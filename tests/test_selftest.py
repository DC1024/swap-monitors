#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：造两个真实窗口(分置两屏，带消息泵) → 子进程执行互换 → 校验位置/最大化/最小化状态。"""
import ctypes
import os
import subprocess
import sys
import threading
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import swap_windows as sw  # noqa: E402

u = sw.user32
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", sw.POINT)]


u.DefWindowProcW.restype = LRESULT
u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
u.RegisterClassExW.restype = wintypes.WORD
u.CreateWindowExW.restype = wintypes.HWND
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                              wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.HWND,
                              wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
u.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
u.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
u.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.DestroyWindow.argtypes = [wintypes.HWND]
sw.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WS_OVERLAPPEDWINDOW, WS_VISIBLE = 0x00CF0000, 0x10000000
WM_QUIT = 0x0012
CLS = "SwapTestWnd"


def _proc(hwnd, msg, wp, lp):
    return u.DefWindowProcW(hwnd, msg, wp, lp)


_PROC = WNDPROC(_proc)
_ready = threading.Event()
_ctx = {"hwnd": [], "tid": 0}


def _window_thread(targets):
    """在独立线程里创建窗口并常驻消息泵 —— 否则跨进程 SetWindowPos 会死锁。"""
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _PROC
    wc.lpszClassName = CLS
    wc.hbrBackground = ctypes.cast(6, wintypes.HBRUSH)  # COLOR_WINDOW+1
    if not u.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
        _ctx["err"] = f"RegisterClassExW 失败 {ctypes.get_last_error()}"
        _ready.set()
        return
    for title, x, y, w, h in targets:
        hwnd = u.CreateWindowExW(0, CLS, title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                 x, y, w, h, None, None, None, None)
        if not hwnd:
            _ctx["err"] = f"CreateWindowExW 失败 {ctypes.get_last_error()}"
            _ready.set()
            return
        _ctx["hwnd"].append(hwnd)
    _ctx["tid"] = sw.kernel32.GetCurrentThreadId()
    _ready.set()

    msg = MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))


def rect_of(hwnd):
    r = sw.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def placement_of(hwnd):
    wp = sw.WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(sw.WINDOWPLACEMENT)
    u.GetWindowPlacement(hwnd, ctypes.byref(wp))
    return wp


def mon_of(hwnd, monitors):
    h = u.MonitorFromWindow(hwnd, sw.MONITOR_DEFAULTTONEAREST)
    for m in monitors:
        if m.hmon == h:
            return m
    return None


def run_swap(label):
    r = subprocess.run([sys.executable, "-u", os.path.join(ROOT, "swap_windows.py"),
                        "--only-title", "SWAPTEST", "--quiet"],
                       capture_output=True, text=True, timeout=120)
    print(f"---- swap[{label}] rc={r.returncode} ----")
    print(r.stdout.strip() or "(无输出)")
    if r.stderr.strip():
        print("stderr:", r.stderr.strip())
    return r.returncode


def main():
    mons = sw.enumerate_monitors()
    if len(mons) < 2:
        print("需要两块显示器才能自测")
        return 1
    A, B = mons[0], mons[1]
    print(f"A = {A.label} {A.rect}   B = {B.label} {B.rect}\n")

    ax, ay = A.rect.left + 100, A.rect.top + 100
    bx, by = B.rect.left + 250, B.rect.top + 180
    th = threading.Thread(target=_window_thread, args=(
        [("SWAPTEST-A", ax, ay, 420, 300), ("SWAPTEST-B", bx, by, 520, 340)],),
        daemon=True)
    th.start()
    _ready.wait(10)
    if not _ctx["hwnd"]:
        print("建窗失败:", _ctx.get("err"))
        return 1
    h1, h2 = _ctx["hwnd"]
    print(f"初始  win1 {rect_of(h1)} (期望 A 屏 {ax},{ay})   "
          f"win2 {rect_of(h2)} (期望 B 屏 {bx},{by})")

    fails = []
    if (rect_of(h1).left, rect_of(h1).top) != (ax, ay):
        fails.append("初始 win1 未落在 A 屏预期位置")
    if (rect_of(h2).left, rect_of(h2).top) != (bx, by):
        fails.append("初始 win2 未落在 B 屏预期位置")

    # --- 用例1：普通窗口互换（纯平移，布局不变）---
    run_swap("1")
    r1, r2 = rect_of(h1), rect_of(h2)
    print(f"-> win1 {r1}   win2 {r2}")
    if (r1.left, r1.top) != (B.rect.left + 100, B.rect.top + 100):
        fails.append(f"win1 应到 B 屏 ({B.rect.left + 100},{B.rect.top + 100})，实为 {r1}")
    if (r1.width, r1.height) != (420, 300):
        fails.append(f"win1 尺寸被改动: {r1.width}x{r1.height}")
    if (r2.left, r2.top) != (A.rect.left + 250, A.rect.top + 180):
        fails.append(f"win2 应到 A 屏 ({A.rect.left + 250},{A.rect.top + 180})，实为 {r2}")
    if (r2.width, r2.height) != (520, 340):
        fails.append(f"win2 尺寸被改动: {r2.width}x{r2.height}")
    if mon_of(h1, mons) is not B or mon_of(h2, mons) is not A:
        fails.append("win1/win2 未真正落到对方屏幕")
    print("用例1 普通窗口互换 " + ("PASS" if not fails else "FAIL"))

    # --- 用例2：最大化 / 最小化 状态保持 ---
    n0 = len(fails)
    u.ShowWindow(h1, sw.SW_MAXIMIZE)
    u.ShowWindow(h2, sw.SW_MINIMIZE)
    run_swap("2")
    p1, p2 = placement_of(h1), placement_of(h2)
    mr = rect_of(h1)
    print(f"-> win1 showCmd={p1.showCmd} rect={mr} normal={p1.rcNormalPosition}")
    print(f"-> win2 showCmd={p2.showCmd} normal={p2.rcNormalPosition}")
    if p1.showCmd != sw.SW_SHOWMAXIMIZED:
        fails.append(f"win1 丢了最大化状态 (showCmd={p1.showCmd})")
    if mon_of(h1, mons) is not A:
        fails.append("win1 没有在 A 屏上最大化")
    if not sw._center_in(mr, A.rect):
        fails.append(f"win1 最大化后中心不在 A 屏: {mr}")
    if abs(mr.width - A.rect.width) > 32 or abs(mr.height - A.rect.height) > 32:
        fails.append(f"win1 最大化后未铺满 A 屏: {mr.width}x{mr.height} vs "
                     f"{A.rect.width}x{A.rect.height}")
    if p2.showCmd != sw.SW_SHOWMINIMIZED:
        fails.append(f"win2 丢了最小化状态 (showCmd={p2.showCmd})")
    if (p2.rcNormalPosition.left, p2.rcNormalPosition.top) != (bx, by):
        fails.append(f"win2 还原位置应为 ({bx},{by})，实为 "
                     f"({p2.rcNormalPosition.left},{p2.rcNormalPosition.top})")
    print("用例2 最大化/最小化状态保持 " + ("PASS" if len(fails) == n0 else "FAIL"))

    for h in (h1, h2):
        u.DestroyWindow(h)
    u.PostThreadMessageW(_ctx["tid"], WM_QUIT, 0, 0)

    print()
    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("全部 PASS —— 两次互换后布局回到原位（映射可逆）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
