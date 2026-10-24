#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局热键「改失败要能退回原样」的回归测试。

背景（v1.1.7 的 bug）：改热键是「先落盘、再注册」：

    apply_hotkey:  cfg["hotkey"] = spec; _save()      ← 先写死
    _apply:        UnregisterHotKey(老热键)            ← 老的先放掉
                   RegisterHotKey(新组合) 失败 → 只报错，老的没装回去

于是用户换一个被占用的组合，结果是从「有个能用的热键」变成「一个都没有」，
而且配置里记的就是那个用不了的组合 —— 下次启动照样失败，还找不到原因。

现在的约定：
  * apply_hotkey 只把组合交给托盘线程，**不写配置**；
  * 只有真的注册成功（hk_ok 事件）才落盘；
  * 注册失败时把老热键重新注册回去，并在提示里说明它还在生效。

测试分两段：
  A) 不碰 Win32，验证「注册成功前绝不落盘」；
  B) 真调 RegisterHotKey，故意把目标组合占住让它必然失败，再验证老热键
     确实被装回去了（用「再注册同一个组合会失败」来证明它还在）。

用法: python tests/test_hotkey_rollback.py
"""
import json
import os
import queue
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="swapmon-hotkey-")
TEST_CFG = os.path.join(TMP, "config.json")
OLD = "ctrl+alt+f9"
NEW = "ctrl+alt+f10"
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": OLD, "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False}, f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def skip(name, why):
    print(f"  ⏭  {name}（跳过：{why}）")


def on_disk_hotkey():
    try:
        with open(TEST_CFG, encoding="utf-8") as f:
            return json.load(f).get("hotkey")
    except Exception:                                        # noqa: BLE001
        return None


root = tk.Tk()
root.withdraw()
app = G.App(root)

# ============================================================
print("== A) 注册成功之前不许落盘 ==")


class FakeHk:
    """只记录被要求注册什么，不真注册。"""

    def __init__(self):
        self.specs = []

    def set_hotkey(self, spec):
        self.specs.append(spec)

    def stop(self):
        pass

    def notify(self, *a, **k):
        pass


app.hk = FakeHk()
app.hk_var.set(NEW)
app.apply_hotkey()
check("把新组合交给了托盘线程", app.hk.specs == [NEW], str(app.hk.specs))
check("还没注册成功，cfg 里不能先写新值", app.cfg["hotkey"] == OLD, str(app.cfg.get("hotkey")))
check("磁盘上也不能先写新值", on_disk_hotkey() == OLD, str(on_disk_hotkey()))

app.q.put(("hk_ok", ("Ctrl+Alt+F10", NEW)))       # 模拟托盘线程注册成功
app._poll()
check("注册成功后才落盘", on_disk_hotkey() == NEW, str(on_disk_hotkey()))
check("cfg 同步更新", app.cfg["hotkey"] == NEW, str(app.cfg.get("hotkey")))

# ============================================================
print("\n== B) 注册失败要把原热键装回去（真调 RegisterHotKey）==")

u32 = G.u32
hwnd = root.winfo_id()


class FakeTray(G.TrayThread):
    """绕开托盘/隐藏窗口，只测 _apply 的热键逻辑。"""

    def __init__(self, hwnd_, results):
        self.hwnd = hwnd_
        self.results = results
        self.nid = None          # _refresh_tip 见到 None 会直接返回
        self._spec = None
        self._shown_hk = ""


q = queue.Queue()
t = FakeTray(hwnd, q)
ID_APPLY, ID_OCCUPY, ID_PROBE = 1, 2, 3

try:
    t._apply(OLD)
    ev, val = q.get_nowait()
    if ev != "hk_ok":
        skip("热键回滚验证", f"本机注册 {OLD} 就失败了（{val}），没法验")
    else:
        check("第一个热键注册成功", ev == "hk_ok")
        check("生效组合被记住", t._spec == OLD, str(t._spec))

        # 把 NEW 占住，让下面那次注册必然失败（这是唯一能稳定制造失败的办法）
        m2, v2, shown2 = G.parse_hotkey(NEW)
        occupied = u32.RegisterHotKey(hwnd, ID_OCCUPY, m2 | G.MOD_NOREPEAT, v2)
        check("测试前提：目标组合已被占住", bool(occupied), f"RegisterHotKey({shown2}) 没成功")

        if occupied:
            t._apply(NEW)
            ev2, val2 = q.get_nowait()
            check("新组合注册失败（预期）", ev2 == "hk_fail", f"{ev2} {val2}")
            check("提示里说明老热键仍在生效", "仍在生效" in str(val2), str(val2))
            check("生效组合没有被改掉", t._spec == OLD, str(t._spec))

            # 真验证：老组合现在仍被 id=1 占着 → 换 id=3 再注册应当失败
            m1, v1, _ = G.parse_hotkey(OLD)
            again = u32.RegisterHotKey(hwnd, ID_PROBE, m1 | G.MOD_NOREPEAT, v1)
            check("老热键确实被重新注册回去了（同组合再注册会失败）",
                  not again, "竟然还能注册成功 —— 说明老的没装回去")
            if again:
                u32.UnregisterHotKey(hwnd, ID_PROBE)
finally:
    for _id in (ID_APPLY, ID_OCCUPY, ID_PROBE):
        u32.UnregisterHotKey(hwnd, _id)

root.destroy()

print()
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("全部通过 ✅ —— 热键改失败会退回原样，配置也只在成功后更新")
