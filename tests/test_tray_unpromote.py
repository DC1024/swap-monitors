#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托盘图标「显示在任务栏上还是收进 ^」的回归测试。

背景（v1.1.8 及以前的 bug）：程序启动时只要发现图标落进了 ^ 隐藏区，就往
HKCU\\Control Panel\\NotifyIconSettings 里写 IsPromoted=1，想「帮」用户把图标
固定到任务栏上。可这个值对**下次登录**是生效的 —— 用户明明只想让它安静待在托盘里，
它偏偏占了任务栏那一格；而且记录是按 exe 路径存的，换过几个目录就会攒下好几条，
条条写着 1，删都不知道从哪删起。

v1.1.9 的做法：
  * 默认**一个字节都不写**（配置里 promote_tray_icon=true 才写，那是用户自己明确要的）；
  * 启动时把老版本写下的那条改回 0，**且只改一次** —— 之后再动就是用户自己的选择
    （右键任务栏 → 任务栏设置 → 其他系统托盘图标 能改），程序不许跟他对着干；
  * 必须在 Shell_NotifyIcon(NIM_ADD) **之前**改，否则会被 explorer 的内存缓存覆盖。

测试用假的 winreg（只实现用到的那几个函数），所以不会碰真实注册表；
用假的 App 替身（只替换窗口几何和提示动作），所以不需要真托盘、真 explorer。

用法: python tests/test_tray_unpromote.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeReg:
    """假的 winreg 模块：只实现 reset_tray_promotion 用到的那几个函数。"""

    HKEY_CURRENT_USER = 0x80000001
    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    REG_DWORD = 4
    ROOT = "ROOT"

    def __init__(self, records, missing=False, fail_write=False):
        # records: {"子键名": {"ExecutablePath": ..., "IsPromoted": 1}}
        self.records = {k: dict(v) for k, v in records.items()}
        self.missing = missing          # True = 模拟 Win10，整条 NotifyIconSettings 没有
        self.fail_write = fail_write    # True = 模拟权限不足
        self.writes = []                # [(子键名, 值名, 新值)]

    def OpenKey(self, root, path, reserved=0, access=0):
        if self.missing:
            raise FileNotFoundError(2, "系统找不到指定的文件。")
        if root == self.HKEY_CURRENT_USER:
            return self.ROOT
        if path not in self.records:
            raise FileNotFoundError(2, "系统找不到指定的文件。")
        return path

    def QueryInfoKey(self, key):
        return (len(self.records), 0, 0)

    def EnumKey(self, key, i):
        return list(self.records)[i]

    def QueryValueEx(self, key, name):
        rec = {} if key == self.ROOT else self.records[key]
        if name not in rec:
            raise FileNotFoundError(2, "系统找不到指定的项。")
        return (rec[name], self.REG_DWORD)

    def SetValueEx(self, key, name, reserved, typ, value):
        if self.fail_write:
            raise PermissionError(5, "拒绝访问。")
        self.records[key][name] = value
        self.writes.append((key, name, value))

    def CloseKey(self, key):
        pass


print("== 1) 老版本把本程序固定在了任务栏上 → 改回收进 ^ ==")
reg = FakeReg({
    "111": {"ExecutablePath": r"D:\SwapMonitors.exe", "IsPromoted": 1},
    "222": {"ExecutablePath": r"C:\Tools\other.exe", "IsPromoted": 1},
})
cfg = {"promote_tray_icon": False, "_tray_unpromote_done": False}
# exe 故意写成小写：注册表里的路径大小写不一定和 sys.executable 一致
changed = G.reset_tray_promotion(cfg, reg=reg, frozen=True, exe=r"d:\swapmonitors.exe")
check("确实改了注册表", changed is True)
check("只写自己那条记录，值是 0", reg.writes == [("111", "IsPromoted", 0)], str(reg.writes))
check("别的程序（other.exe）一个字节都没动",
      reg.records["222"]["IsPromoted"] == 1, str(reg.records["222"]))
check("记下「已经处理过」", cfg.get("_tray_unpromote_done") is True)

print("\n== 2) 只做一次：之后用户自己改回「固定显示」也忍着不动 ==")
reg.records["111"]["IsPromoted"] = 1      # 用户（或 explorer）又把它固定上了
reg.writes.clear()
changed = G.reset_tray_promotion(cfg, reg=reg, frozen=True, exe=r"d:\swapmonitors.exe")
check("返回 False（这次什么都没做）", changed is False)
check("一次写操作都没有 —— 否则就是每次启动都跟用户对着干",
      reg.writes == [], str(reg.writes))
check("值也确实还是用户设的 1", reg.records["111"]["IsPromoted"] == 1)

print("\n== 3) 用户明确要固定（promote_tray_icon=true）==")
reg = FakeReg({"111": {"ExecutablePath": r"D:\SwapMonitors.exe", "IsPromoted": 1}})
cfg = {"promote_tray_icon": True}
G.reset_tray_promotion(cfg, reg=reg, frozen=True, exe=r"D:\SwapMonitors.exe")
check("不动注册表", reg.writes == [], str(reg.writes))
check("也不记「处理过」—— 将来关掉开关还能补上",
      "_tray_unpromote_done" not in cfg, str(cfg))

print("\n== 4) 没打包（跑源码）→ 图标属于 python.exe，不归我们管 ==")
reg = FakeReg({"111": {"ExecutablePath": r"C:\Python\python.exe", "IsPromoted": 1}})
cfg = {}
G.reset_tray_promotion(cfg, reg=reg, frozen=False, exe=r"C:\Python\python.exe")
check("不动注册表", reg.writes == [], str(reg.writes))
check("不记「处理过」", "_tray_unpromote_done" not in cfg, str(cfg))

print("\n== 5) 已经是「收进 ^」→ 不写，但算处理完 ==")
reg = FakeReg({"111": {"ExecutablePath": r"D:\SwapMonitors.exe", "IsPromoted": 0}})
cfg = {}
changed = G.reset_tray_promotion(cfg, reg=reg, frozen=True, exe=r"D:\SwapMonitors.exe")
check("没有多余的写操作", reg.writes == [], str(reg.writes))
check("返回 False（没改动）", changed is False)
check("仍然记一笔，免得每次启动都白跑", cfg.get("_tray_unpromote_done") is True)

print("\n== 6) 记录里没有 IsPromoted（走系统默认 = 收进 ^）==")
reg = FakeReg({"111": {"ExecutablePath": r"D:\SwapMonitors.exe"}})
cfg = {}
changed = G.reset_tray_promotion(cfg, reg=reg, frozen=True, exe=r"D:\SwapMonitors.exe")
check("不硬塞一个值进去", reg.writes == [], str(reg.writes))
check("返回 False", changed is False)
check("算处理完", cfg.get("_tray_unpromote_done") is True)

print("\n== 7) Win10：整条 NotifyIconSettings 都不存在 ==")
reg = FakeReg({}, missing=True)
cfg = {}
try:
    changed = G.reset_tray_promotion(cfg, reg=reg, frozen=True, exe=r"D:\SwapMonitors.exe")
    raised = ""
except Exception as e:                    # noqa: BLE001
    changed, raised = None, repr(e)
check("不抛异常（Win10 用户照样能用）", raised == "", raised)
check("返回 False", changed is False)
check("算处理完", cfg.get("_tray_unpromote_done") is True)

print("\n== 8) 写不进去（权限不足）→ 静默失败，不许崩 ==")
reg = FakeReg({"111": {"ExecutablePath": r"D:\SwapMonitors.exe", "IsPromoted": 1}},
              fail_write=True)
cfg = {}
try:
    changed = G.reset_tray_promotion(cfg, reg=reg, frozen=True, exe=r"D:\SwapMonitors.exe")
    raised = ""
except Exception as e:                    # noqa: BLE001
    changed, raised = None, repr(e)
check("不抛异常", raised == "", raised)
check("返回 False", changed is False)
check("算处理完（别每次启动都去撞权限）", cfg.get("_tray_unpromote_done") is True)


# ---------------------------------------------------------------- UI 侧
class FakeHk:
    """只提供 _maybe_hint_overflow 用到的那两样东西。"""

    def __init__(self, rect_left):
        self.rect_left = rect_left
        self.promoted = 0

    def icon_rect(self):
        r = type("Rect", (), {})()
        r.left = self.rect_left
        return r

    def promote_icon(self):
        self.promoted += 1
        return True


class FakeApp(G.App):
    """App 的替身：不建窗口、不碰注册表，只替换几何和提示。"""

    def __init__(self, cfg, icon_left=2000, overflow_left=1000):
        self.cfg = cfg
        self._overflow_hinted = False
        self.hk = FakeHk(icon_left)
        self._overflow_left_value = overflow_left
        self.status = []
        self.hint = ""
        self.root = type("Root", (), {"after": lambda *a, **k: None})()

    def _overflow_left(self):
        return self._overflow_left_value

    def _overflow_hint(self, tip):
        self.hint = tip
        return True

    def set_status(self, text):
        self.status.append(text)


print("\n== 9) 默认路径：图标收进 ^ 时**不许**再写「固定显示」==")
app = FakeApp({})
app._maybe_hint_overflow()
check("promote_icon 一次都没被调用（老代码这里必调一次）",
      app.hk.promoted == 0, f"实际 {app.hk.promoted} 次")
check("提示里说清了图标收在 ^ 里", "隐藏的图标" in app.hint, app.hint[:60])
check("状态栏也说清了", bool(app.status) and "隐藏区" in app.status[0], str(app.status))

print("\n== 10) 只有明确开了 promote_tray_icon 才写 ==")
app = FakeApp({"promote_tray_icon": True})
app._maybe_hint_overflow()
check("这种配置下才调 promote_icon", app.hk.promoted == 1, f"实际 {app.hk.promoted} 次")

print("\n== 11) 图标本来就在可见区（用户自己固定的）→ 什么都不做 ==")
app = FakeApp({}, icon_left=500, overflow_left=1000)
app._maybe_hint_overflow()
check("不提升", app.hk.promoted == 0)
check("不弹提示", app.hint == "", app.hint)

print("\n== 12) start_hotkey：先修注册表 → 落盘 → 才挂图标（顺序不能反）==")


class FakeTrayThread:
    """顶替 TrayThread：只记下「我被建起来了没有、参数对不对」。"""

    def __init__(self, results, icon_path=None):
        calls.append("tray")              # 类比 NIM_ADD 的那一刻
        self.results = results
        self.icon_path = icon_path
        self.started = False
        self.spec = None

    def start(self):
        self.started = True

    def set_hotkey(self, spec):
        self.spec = spec


class FakeAppHotkey(G.App):
    """只给 start_hotkey 用：不建窗口、不碰真注册表。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.q = "q"
        self.hk = None


def fake_fix(cfg, **kw):
    calls.append("fix")
    cfg["_tray_unpromote_done"] = True
    return True


def fake_save(cfg):
    calls.append("save")
    return True


calls = []
_real = (G.reset_tray_promotion, G.save_cfg, G.TrayThread)
G.reset_tray_promotion, G.save_cfg, G.TrayThread = fake_fix, fake_save, FakeTrayThread
try:
    app = FakeAppHotkey({"hotkey": "ctrl+alt+s"})
    app.start_hotkey()
    check("三步齐全且顺序正确（先修记录、再落盘、最后才挂图标）",
          calls == ["fix", "save", "tray"], str(calls))
    check("托盘线程真的起来了", bool(app.hk) and app.hk.started is True)
    check("热键按配置下发", getattr(app.hk, "spec", None) == "ctrl+alt+s",
          str(getattr(app.hk, "spec", None)))
    # 已经有 hk 了（比如提权失败后重试调 start_hotkey）——不该再修一遍、再落一次盘
    calls.clear()
    app.start_hotkey()
    check("重复调用不再碰注册表", calls == [], str(calls))
finally:
    G.reset_tray_promotion, G.save_cfg, G.TrayThread = _real

print()
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("全部通过 ✅ —— 图标默认收进 ^，只修一次老记录，且不跟用户对着干")
