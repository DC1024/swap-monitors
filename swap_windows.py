#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swap_windows.py —— 一键互换两块显示器上的全部窗口，保持贴附好的相对布局不变。

原理：把每块屏上的窗口矩形按「源屏 → 目标屏」做坐标映射（同尺寸=纯平移，
异尺寸=等比缩放），两块屏的窗口集合互为镜像地整体搬家，所以相对布局天然不乱。
最大化/最小化窗口走 window placement，不丢状态。

用法：
    python swap_windows.py --list            # 看显示器编号 + 会被交换的窗口
    python swap_windows.py --dry-run         # 只打印计划，不动窗口
    python swap_windows.py                   # 真换（默认前两块屏，左右顺序）
    python swap_windows.py --monitors 1,2    # 指定互换哪两块屏（编号见 --list）
    python swap_windows.py --scale           # 强制等比缩放（默认：等尺寸自动平移）

依赖：无。仅 Python 标准库 + Win32 API（ctypes）。
注意：管理员权限运行的窗口（任务管理器、部分杀软等）需要以管理员身份运行本脚本才能搬动。
"""

import argparse
import ctypes
import json
import re
import sys
from ctypes import wintypes

# ---------------------------------------------------------------- Win32 基础

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:
    dwmapi = None


def _set_dpi_awareness():
    """让进程按物理像素取坐标，避免缩放屏上坐标错位。"""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PMv2
            return "PMv2"
    except Exception:
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        if shcore.SetProcessDpiAwareness(2) == 0:  # PER_MONITOR_AWARE
            return "PM"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


DPI_MODE = _set_dpi_awareness()


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    def as_tuple(self):
        return (self.left, self.top, self.right, self.bottom)

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def __repr__(self):
        return f"({self.left},{self.top})-({self.right},{self.bottom})"


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", POINT),
                ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]


MONITORINFOF_PRIMARY = 0x1
MONITOR_DEFAULTTONEAREST = 0x2

user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p,
                                       ctypes.c_void_p, wintypes.LPARAM]
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE

user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_POPUP, WS_VISIBLE, WS_EX_TOOLWINDOW = 0x80000000, 0x10000000, 0x00000080
SW_MAXIMIZE, SW_MINIMIZE, SW_NORMAL = 3, 6, 1
SW_HIDE, SW_SHOWNORMAL, SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED = 0, 1, 2, 3
SW_SHOWNOACTIVATE, SW_RESTORE = 4, 9
SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x0004, 0x0010, 0x0200
DWMWA_CLOAKED = 14

# shell / 输入法 / 覆盖层之类的伪窗口，默认不参与交换
DEFAULT_SKIP_TITLE = re.compile(
    r"^(Program Manager|Windows 输入体验|Windows Input Experience|"
    r"Microsoft Text Input Application|Windows Shell Experience Host|"
    r"DWM|Task Manager|任务管理器)$", re.I)
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
                "MultitaskingViewFrame", "ForegroundStaging"}


def _err(msg=None):
    return ctypes.get_last_error(), msg


def is_cloaked(hwnd):
    """被 DWM 隐藏的窗口（含"其他虚拟桌面"上的窗口）。"""
    if dwmapi is None:
        return False
    val = ctypes.c_int(0)
    try:
        hr = dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED,
                                          ctypes.byref(val), ctypes.sizeof(val))
        return hr == 0 and val.value != 0
    except Exception:
        return False


def proc_name(pid):
    """取进程名；权限不足（管理员进程）时返回 None。"""
    h = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        n = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(n.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value.rsplit("\\", 1)[-1]
        return None
    finally:
        kernel32.CloseHandle(h)


class Monitor:
    __slots__ = ("hmon", "device", "rect", "work", "primary")

    def __init__(self, hmon, mi):
        self.hmon = hmon
        self.device = mi.szDevice
        self.rect = mi.rcMonitor
        self.work = mi.rcWork
        self.primary = bool(mi.dwFlags & MONITORINFOF_PRIMARY)

    @property
    def label(self):
        return f"{self.device}{' (主屏)' if self.primary else ''}"


def enumerate_monitors():
    out = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                            ctypes.POINTER(RECT), wintypes.LPARAM)

    def cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            out.append(Monitor(hmon, mi))
        return True

    user32.EnumDisplayMonitors(None, None, CB(cb), 0)
    out.sort(key=lambda m: (m.rect.left, m.rect.top))  # 左 → 右
    return out


class Win:
    __slots__ = ("hwnd", "title", "cls", "pid", "proc", "rect", "mon", "place")

    def __init__(self, hwnd, title, cls, pid, proc, rect, mon, place):
        self.hwnd, self.title, self.cls = hwnd, title, cls
        self.pid, self.proc = pid, proc
        self.rect, self.mon, self.place = rect, mon, place

    @property
    def state(self):
        return self.place.showCmd

    def state_name(self):
        return {SW_SHOWMINIMIZED: "最小化", SW_SHOWMAXIMIZED: "最大化",
                SW_SHOWNORMAL: "普通"}.get(self.state, str(self.state))


def _mon_index(hmon, monitors):
    for i, m in enumerate(monitors):
        if m.hmon == hmon:
            return i
    return None


def enumerate_windows(monitors, skip_title, only_title=None, only_proc=None,
                      include_minimized=True, exclude_pids=()):
    wins = []
    me = kernel32.GetCurrentProcessId()
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        tbuf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, tbuf, n + 1)
        title = tbuf.value
        cbuf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cbuf, 256)
        cls = cbuf.value

        if cls in SKIP_CLASSES or skip_title.match(title.strip()):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if is_cloaked(hwnd):
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me or pid.value in exclude_pids:
            return True
        pname = proc_name(pid.value)

        if only_title and not only_title.search(title):
            return True
        if only_proc and not (pname and only_proc.search(pname)):
            return True

        r = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        if not include_minimized and r.left <= -30000:
            return True

        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return True

        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        idx = _mon_index(hmon, monitors)
        # 最小化窗口的矩形是 (-32000,-32000)，不能拿它判所属屏 —— 改用"还原位置"的中心点
        if r.left <= -30000 or wp.showCmd == SW_SHOWMINIMIZED:
            hmon = user32.MonitorFromPoint(
                POINT((wp.rcNormalPosition.left + wp.rcNormalPosition.right) // 2,
                      (wp.rcNormalPosition.top + wp.rcNormalPosition.bottom) // 2),
                MONITOR_DEFAULTTONEAREST)
            idx = _mon_index(hmon, monitors)
        if idx is None:
            hmon = user32.MonitorFromPoint(
                POINT((r.left + r.right) // 2, (r.top + r.bottom) // 2),
                MONITOR_DEFAULTTONEAREST)
            idx = _mon_index(hmon, monitors)

        wins.append(Win(hwnd, title, cls, pid.value, pname, r, idx, wp))
        return True

    user32.EnumWindows(CB(cb), 0)
    return wins


def map_rect(r, src, dst, mode="auto"):
    """把矩形从 src 屏映射到 dst 屏。"""
    sw, sh = src.rect.width or 1, src.rect.height or 1
    dw, dh = dst.rect.width or 1, dst.rect.height or 1
    if mode == "translate" or (mode == "auto" and (sw, sh) == (dw, dh)):
        dx, dy = dst.rect.left - src.rect.left, dst.rect.top - src.rect.top
        return RECT(r.left + dx, r.top + dy, r.right + dx, r.bottom + dy)
    sx, sy = dw / sw, dh / sh
    return RECT(round(dst.rect.left + (r.left - src.rect.left) * sx),
                round(dst.rect.top + (r.top - src.rect.top) * sy),
                round(dst.rect.left + (r.right - src.rect.left) * sx),
                round(dst.rect.top + (r.bottom - src.rect.top) * sy))


def clamp_into(r, area):
    """整块塞进目标工作区（异尺寸时才需要）。"""
    w, h = min(r.width, area.width), min(r.height, area.height)
    left = max(area.left, min(r.left, area.right - w))
    top = max(area.top, min(r.top, area.bottom - h))
    return RECT(left, top, left + w, top + h)


def _center_in(r, area):
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    return area.left <= cx < area.right and area.top <= cy < area.bottom


def move_window(win, target, dst, verbose=True):
    """按窗口状态安全落位，返回 (ok, note)。"""
    hwnd, state = win.hwnd, win.state
    flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER

    if state == SW_SHOWMINIMIZED:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return False, "读 placement 失败"
        wp.rcNormalPosition = target
        wp.showCmd = SW_SHOWMINIMIZED  # 保持最小化，只改"将来还原到哪"
        return bool(user32.SetWindowPlacement(hwnd, ctypes.byref(wp))), "已更新还原位置"

    if state == SW_SHOWMAXIMIZED:
        # 1) 就地取消最大化（用 SHOWNOACTIVATE 避免抢焦点）
        # 2) 把"还原位置"挪到目标屏
        # 3) 在新屏重新最大化
        # 直接给最大化窗口改 placement 在 Windows 上经常不生效，必须走这条序列
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
        wp.showCmd = SW_SHOWNOACTIVATE
        user32.SetWindowPlacement(hwnd, ctypes.byref(wp))
        user32.SetWindowPos(hwnd, None, target.left, target.top,
                            target.width, target.height, flags)
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        cur = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(cur))
        if not _center_in(cur, dst.rect):
            # 少数程序第一次不认账，再来一遍
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetWindowPos(hwnd, None, target.left, target.top,
                                target.width, target.height, flags)
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            user32.GetWindowRect(hwnd, ctypes.byref(cur))
            if _center_in(cur, dst.rect):
                return True, "最大化窗口（重试后成功）"
            return False, f"最大化窗口未能移动: {cur!s}"
        return True, "最大化窗口"

    before = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(before))
    ok = bool(user32.SetWindowPos(hwnd, None, target.left, target.top,
                                 target.width, target.height, flags))
    after = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(after))
    if ok and (after.left, after.top) == (before.left, before.top) \
            and (before.left, before.top) != (target.left, target.top):
        return False, "位置未发生变化（可能权限不足）"
    return ok, ""


def swap(args):
    monitors = enumerate_monitors()
    if len(monitors) < 2:
        print("只检测到 1 块显示器，无法互换。", file=sys.stderr)
        return 1

    if args.monitors:
        try:
            idx = [int(x) - 1 for x in args.monitors.split(",")]
            assert len(idx) == 2 and all(0 <= i < len(monitors) for i in idx)
        except Exception:
            print("--monitors 参数应为两块屏的编号，例如 --monitors 1,2", file=sys.stderr)
            return 1
        a, b = monitors[idx[0]], monitors[idx[1]]
    else:
        a, b = monitors[0], monitors[1]

    skip = DEFAULT_SKIP_TITLE
    if args.exclude_title:
        skip = re.compile(f"({DEFAULT_SKIP_TITLE.pattern})|({args.exclude_title})", re.I)
    only_title = re.compile(args.only_title, re.I) if args.only_title else None
    only_proc = re.compile(args.only_proc, re.I) if args.only_proc else None
    excl_pids = {int(x) for x in re.split(r"[,\s]+", args.exclude_pid or "") if x.strip().isdigit()}

    wins = enumerate_windows(monitors, skip, only_title, only_proc,
                             not args.skip_minimized, excl_pids)

    jmode = getattr(args, "json", False)

    def say(*a, **kw):
        if not jmode:
            print(*a, **kw)

    def rect_json(r):
        return {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
                "width": r.width, "height": r.height}

    def mon_json(i, m):
        return {"index": i + 1, "device": m.device, "primary": m.primary,
                **rect_json(m.rect)}

    if args.list:
        if jmode:
            print(json.dumps({
                "ok": True, "dpi_mode": DPI_MODE,
                "monitors": [mon_json(i, m) for i, m in enumerate(monitors)],
                "swap_pair": [monitors.index(a) + 1, monitors.index(b) + 1],
                "windows": [{
                    "monitor": (w.mon + 1) if w.mon is not None else None,
                    "state": w.state_name(), "title": w.title, "proc": w.proc,
                    "pid": w.pid, **rect_json(w.rect),
                } for w in sorted(wins, key=lambda w: (w.mon if w.mon is not None else 9,
                                                       w.rect.left))],
            }, ensure_ascii=False))
            return 0
        print(f"DPI 感知模式: {DPI_MODE}\n")
        print("=== 显示器 ===")
        for i, m in enumerate(monitors):
            mark = "  <== 参与互换" if m in (a, b) else ""
            print(f"  [{i + 1}] {m.label}  区域 {m.rect.as_tuple()}  "
                  f"{m.rect.width}x{m.rect.height}{mark}")
        print(f"\n=== 会被互换的窗口（{len(wins)} 个）===")
        for w in sorted(wins, key=lambda w: (w.mon if w.mon is not None else 9, w.rect.left)):
            tag = f"屏{(w.mon + 1) if w.mon is not None else '?'}"
            print(f"  {tag} {w.state_name():<4s} {w.rect!s:<28s} "
                  f"{(w.proc or '管理员进程?'):<22s} {w.title[:48]!r}")
        return 0

    ia, ib = monitors.index(a), monitors.index(b)
    mode = "scale" if args.scale else ("translate" if args.translate_only else "auto")
    plan = []
    for w in wins:
        if w.mon == ia:
            src, dst = a, b
        elif w.mon == ib:
            src, dst = b, a
        else:
            continue  # 第三块屏/未知屏：不动
        # 最大化/最小化窗口用"还原位置"参与映射，状态才不会丢
        base = w.place.rcNormalPosition if w.state in (SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED) else w.rect
        target = map_rect(base, src, dst, mode)
        if mode == "scale":
            target = clamp_into(target, dst.work)
        plan.append((w, src, dst, target))

    use_scale = args.scale or (mode == "auto" and (a.rect.width, a.rect.height)
                               != (b.rect.width, b.rect.height))
    payload = {
        "ok": True, "dry_run": bool(args.dry_run), "mode": "scale" if use_scale else "translate",
        "monitors": [mon_json(i, m) for i, m in enumerate(monitors)],
        "swap_pair": [monitors.index(a) + 1, monitors.index(b) + 1],
        "plan": [{
            "title": w.title, "proc": w.proc, "pid": w.pid, "state": w.state_name(),
            "direction": "A→B" if src is a else "B→A",
            "from": rect_json(base_rect), "to": rect_json(target),
        } for w, src, dst, target in plan
            for base_rect in [w.place.rcNormalPosition
                              if w.state in (SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED) else w.rect]],
        "moved": 0, "failed": 0, "errors": [],
    }

    if not plan:
        say("没有需要互换的窗口。")
        payload["note"] = "没有需要互换的窗口"
        if jmode:
            print(json.dumps(payload, ensure_ascii=False))
        return 0

    say(f"屏幕 A = {a.label} {a.rect!s}\n屏幕 B = {b.label} {b.rect!s}")
    say(f"（映射方式：{'等比缩放' if use_scale else '纯平移'}）\n")
    for w, src, dst, target in plan:
        say(f"  [{'A→B' if src is a else 'B→A'}] {w.title[:44]!r} "
            f"{w.state_name()} {w.rect!s} → {target!s}")

    if args.dry_run:
        say(f"\n[dry-run] 共 {len(plan)} 个窗口，未做任何改动。")
        if jmode:
            print(json.dumps(payload, ensure_ascii=False))
        return 0

    fg = user32.GetForegroundWindow()  # 互换过程会短暂改变前台窗口，结束后还原
    ok_n = warn = denied = 0
    for w, src, dst, target in plan:
        ok, note = move_window(w, target, dst, not args.quiet)
        if not ok:
            warn += 1
            msg = f"{w.title[:40]!r} {note}"
            payload["errors"].append(msg)
            if w.proc is None:          # 读不到进程名 = 对方是管理员权限进程
                denied += 1
            say(f"  ! 失败: {msg}"
                f"{'（管理员进程？试试用管理员身份运行）' if w.proc is None else ''}")
        else:
            ok_n += 1
            say(f"  ✓ {w.title[:40]!r} → {target!s} {note}") if args.verbose else None
    if fg and user32.IsWindow(fg) and user32.GetForegroundWindow() != fg:
        try:
            user32.SetForegroundWindow(fg)
        except Exception:
            pass

    payload["moved"], payload["failed"] = ok_n, warn
    payload["denied"] = denied          # 因权限（管理员进程）失败的个数
    payload["ok"] = warn == 0
    if jmode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"\n完成：{ok_n}/{len(plan)} 个窗口已互换"
              + (f"，{warn} 个失败。" if warn else "。"))
    return 0 if warn == 0 else 2


def main(argv=None):
    p = argparse.ArgumentParser(
        description="互换两块显示器上的全部窗口，保持相对布局。")
    p.add_argument("--list", action="store_true", help="列出显示器与待交换窗口")
    p.add_argument("--dry-run", action="store_true", help="只打印计划")
    p.add_argument("--monitors", help="要互换的两块屏编号，如 1,2（见 --list）")
    p.add_argument("--scale", action="store_true", help="不同分辨率时按比例缩放")
    p.add_argument("--translate-only", action="store_true", help="强制纯平移")
    p.add_argument("--exclude-title", help="额外排除的窗口标题正则")
    p.add_argument("--only-title", help="只处理标题匹配的窗口（测试用）")
    p.add_argument("--only-proc", help="只处理进程名匹配的窗口")
    p.add_argument("--exclude-pid", help="排除这些进程（逗号分隔），如 GUI 自己")
    p.add_argument("--skip-minimized", action="store_true",
                   help="不处理最小化窗口")
    p.add_argument("-v", "--verbose", action="store_true", help="逐个打印结果")
    p.add_argument("-q", "--quiet", action="store_true", help="静默模式")
    p.add_argument("--json", action="store_true", help="以 JSON 输出（给 GUI 用）")
    p.add_argument("--gui", action="store_true",
                   help="静默运行，仅出错时弹窗提示（给热键快捷方式用）")
    args = p.parse_args(argv)
    if args.scale and args.translate_only:
        print("--scale 与 --translate-only 不能同时用。", file=sys.stderr)
        return 1
    rc = swap(args)
    if args.gui and rc != 0:
        try:
            user32.MessageBoxW(None, f"屏幕互换未完全成功（返回码 {rc}）。\n"
                                     "部分窗口可能属于管理员权限进程，\n"
                                     "请改用管理员身份运行，或先关闭这些窗口。",
                               "屏幕互换", 0x30)  # MB_ICONWARNING
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
