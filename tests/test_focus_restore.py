#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「互换后把焦点还给原来的窗口」这个勾选框的回归测试。

背景（v1.1.7 的 bug）：GUI 里有这个勾选框，配置里也存着 restore_focus，
但**没有任何代码读它** —— swap_windows.swap() 在末尾无条件
SetForegroundWindow(原前台窗口)。用户取消勾选，焦点照样被抢回去，
而且界面上不会有任何提示。

三段覆盖：
  ① CLI：不带参数时 restore_focus 默认为 True（老命令行行为不变），
     带 --no-restore-focus 才是 False。
  ② 引擎：把 enumerate_monitors / enumerate_windows / move_window / user32
     全换成替身，直接调 swap(args)，数 SetForegroundWindow 到底调没调。
  ③ GUI：真的建一个 App，勾选框一变，_make_args() 就得跟着变、还得落盘。

② 不能只在「关掉」时断言没调用 —— 那样把整段代码删掉也能通过。
所以同时钉住「勾着的时候确实调用了」，两头都锁死。

用法: python tests/test_focus_restore.py
需要带 tkinter 的解释器（swap_gui 顶部 import tkinter）。
"""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ---- 隔离用户配置：必须在 import swap_gui 之前设好 ----
TMP = tempfile.mkdtemp(prefix="swapmon-focus-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False},
              f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G      # noqa: E402
import swap_windows as SW  # noqa: E402  （G 里 import 的是同一个模块对象）

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    print()
    print(f"== {t} ==")


print(f"临时目录: {TMP}")

# ============================================================
section("① CLI：默认还原，--no-restore-focus 才关掉")

_orig_swap = SW.swap
try:
    cap = {}

    def _fake_swap(a):
        cap["args"] = a
        return 0

    SW.swap = _fake_swap          # 只截参数，不真的动窗口

    def parse_via_main(argv):
        """走真正的 main() 拿 args。argparse 不认这个参数时会 SystemExit ——
        必须转成 None，否则整个测试文件在这里就退出，后面的断言一条都跑不到。"""
        cap.clear()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                SW.main(argv)
        except SystemExit:
            return None
        return cap.get("args")

    got = parse_via_main([])      # 老用法：命令行里根本没有这个参数
    check("不带参数时 restore_focus 默认 True（老命令行行为不变）",
          getattr(got, "restore_focus", "<没有这个字段>") is True,
          repr(getattr(got, "restore_focus", "<没有这个字段>")))

    got = parse_via_main(["--no-restore-focus"])
    check("--no-restore-focus 能把开关关掉",
          getattr(got, "restore_focus", "<没有这个字段>") is False,
          repr(getattr(got, "restore_focus", "<没有这个字段>")))
finally:
    SW.swap = _orig_swap

# ============================================================
section("② 引擎：开关关掉后不能再碰前台窗口")


def mk_rect(l, t, r, b):
    rc = SW.RECT()
    rc.left, rc.top, rc.right, rc.bottom = l, t, r, b
    return rc


def mk_mon(hmon, left, primary=False):
    """造一块 1920x1080 的假屏；Monitor 只读 mi 的四个字段。"""
    mi = type("MI", (), {})()
    mi.szDevice = f"\\\\.\\DISPLAY{hmon}"
    mi.rcMonitor = mk_rect(left, 0, left + 1920, 1080)
    mi.rcWork = mk_rect(left, 0, left + 1920, 1040)
    mi.dwFlags = 1 if primary else 0
    return SW.Monitor(hmon, mi)


def mk_win(hwnd, mon, left):
    rc = mk_rect(left, 100, left + 800, 700)
    place = SW.WINDOWPLACEMENT()
    place.showCmd = SW.SW_SHOWNORMAL
    place.rcNormalPosition = mk_rect(left, 100, left + 800, 700)
    return SW.Win(hwnd, f"窗口{hwnd}", "Chrome_WidgetWin_1", 4000 + hwnd,
                  "app.exe", rc, mon, place)


class FakeUser32:
    """只实现 swap() 末尾用到的那三个 API，顺便把调用记下来。"""

    def __init__(self, fg_seq, alive=True):
        self.fg_seq = list(fg_seq)   # 每次 GetForegroundWindow 依次取一个，取完就定住
        self.alive = alive
        self.calls = []

    def GetForegroundWindow(self):
        return self.fg_seq.pop(0) if len(self.fg_seq) > 1 else self.fg_seq[0]

    def IsWindow(self, h):
        return self.alive

    def SetForegroundWindow(self, h):
        self.calls.append(h)
        return True


def mk_args(**kw):
    a = type("A", (), {})()
    a.list = False
    a.dry_run = False
    a.monitors = None
    a.rotate = None
    a.permute = "1>2,2>1"
    a.scale = False
    a.translate_only = False
    a.exclude_title = None
    a.only_title = None
    a.only_proc = None
    a.exclude_pid = ""
    a.skip_minimized = False
    a.verbose = False
    a.quiet = True
    a.json = True
    a.gui = False
    a.restore_focus = True
    for k, v in kw.items():
        setattr(a, k, v)
    return a


MONITORS = [mk_mon(1, 0, True), mk_mon(2, 1920)]
WINS = [mk_win(1001, 0, 100), mk_win(1002, 1, 2020)]
FG, OTHER = 9001, 9002           # 互换期间前台被抢到了 OTHER


def run_swap(args, fake_u32):
    """把引擎边界全部换成替身跑一遍 swap()，返回返回码。"""
    o_mon, o_win = SW.enumerate_monitors, SW.enumerate_windows
    o_move, o_u32 = SW.move_window, SW.user32
    SW.enumerate_monitors = lambda: MONITORS
    SW.enumerate_windows = lambda *a, **k: WINS
    SW.move_window = lambda w, target, dst, verbose=True: (True, "")
    SW.user32 = fake_u32
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return SW.swap(args)
    finally:
        SW.enumerate_monitors, SW.enumerate_windows = o_mon, o_win
        SW.move_window, SW.user32 = o_move, o_u32


# 正向：勾着的时候必须真的把焦点切回去 —— 否则下面的「没调用」毫无意义
u = FakeUser32([FG, OTHER])
rc = run_swap(mk_args(restore_focus=True), u)
check("勾着时前台窗口被切回原来的那个", u.calls == [FG], f"calls={u.calls}")
check("还原焦点不影响返回码", rc == 0, f"rc={rc}")

# 这就是要修的 bug：取消勾选后仍然抢焦点
u = FakeUser32([FG, OTHER])
run_swap(mk_args(restore_focus=False), u)
check("取消勾选后不再抢焦点", u.calls == [], f"calls={u.calls}")

# 向后兼容：第三方脚本自己拼的 args 没有这个字段，行为必须和以前一样
a = mk_args()
del a.restore_focus
u = FakeUser32([FG, OTHER])
run_swap(a, u)
check("args 缺 restore_focus 时按 True 处理（老调用方不受影响）",
      u.calls == [FG], f"calls={u.calls}")

# 原有逻辑不能被破坏：焦点没变 / 原窗口已关闭时都不该多调一次
u = FakeUser32([FG])
run_swap(mk_args(restore_focus=True), u)
check("焦点本来就没变时不调用 SetForegroundWindow", u.calls == [], f"calls={u.calls}")

u = FakeUser32([FG, OTHER], alive=False)
run_swap(mk_args(restore_focus=True), u)
check("原前台窗口已销毁时不调用（IsWindow 为假）", u.calls == [], f"calls={u.calls}")

# ============================================================
section("③ GUI：勾选框接到了 _make_args()，也真的落盘")

import tkinter as tk       # noqa: E402

_orig_cmd = G.AUTOSTART_CMD
G.AUTOSTART_CMD = os.path.join(TMP, "SwapMonitors.cmd")   # 别碰真实的启动文件夹
_root = tk.Tk()
_root.withdraw()
try:
    app = G.App(_root)

    app.focus_var.set(True)
    got = getattr(app._make_args(), "restore_focus", "<缺失>")
    check("勾选时 _make_args().restore_focus 是 True", got is True, repr(got))

    app.focus_var.set(False)
    got = getattr(app._make_args(), "restore_focus", "<缺失>")
    check("取消勾选后 _make_args().restore_focus 是 False", got is False, repr(got))

    app._save()
    with open(TEST_CFG, encoding="utf-8") as f:
        disk = json.load(f)
    check("取消勾选会落盘（重启后还是关着的）",
          disk.get("restore_focus") is False, str(disk.get("restore_focus")))
finally:
    try:
        _root.destroy()
    except Exception:                                        # noqa: BLE001
        pass
    G.AUTOSTART_CMD = _orig_cmd


print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
