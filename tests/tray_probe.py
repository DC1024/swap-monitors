#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专项探针：点 X（WM_CLOSE）后到底发生了什么。子进程输出落盘后再读。

用法: python _tray_probe.py [exe或脚本路径]
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
u.SetProcessDPIAware()
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
u.IsWindowVisible.argtypes = [wintypes.HWND]
u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
k.OpenProcess.restype = wintypes.HANDLE
k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
WM_CLOSE = 0x0010
TITLE = "双屏窗口互换"


def find():
    found = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lp):
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        if TITLE in buf.value:
            found.append((int(hwnd), bool(u.IsWindowVisible(hwnd))))
        return True

    u.EnumWindows(CB(cb), 0)
    return found


def kill_windows():
    for hwnd, _ in find():
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        h = k.OpenProcess(0x0001, False, pid.value)   # PROCESS_TERMINATE
        if h:
            k.TerminateProcess(h, 1)
            print(f"  已强制终止 pid={pid.value}")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "swap_gui.py")
    cmd = [sys.executable, target] if target.endswith(".py") else [target]
    log = os.path.join(HERE, "assets", "_tray_probe.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    lf = open(log, "w", encoding="utf-8")
    print("启动:", cmd)
    p = subprocess.Popen(cmd, cwd=HERE, stdout=lf, stderr=subprocess.STDOUT)

    hwnd = None
    for _ in range(60):
        time.sleep(0.5)
        ws = find()
        if ws:
            hwnd = ws[0][0]
            break
    if not hwnd:
        print("FAIL: 没找到窗口")
        kill_windows()
        return 1
    print(f"窗口 hwnd={hwnd}  启动时可见={find()[0][1]}")
    time.sleep(2.5)

    print("发送 WM_CLOSE（等同点 X）…")
    ok = u.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
    print("  PostMessage 返回:", bool(ok), " 错误码:", ctypes.get_last_error())
    for i in range(12):
        time.sleep(0.5)
        ws = find()
        vis = ws[0][1] if ws else None
        if i in (1, 3, 7, 11) or vis is False:
            print(f"  +{(i + 1) * 0.5:.1f}s 窗口存在={bool(ws)} 可见={vis}")
        if vis is False:
            break
    ws = find()
    print("最终:", f"{len(ws)} 个窗口, 可见={ws[0][1] if ws else None}")
    lf.flush()
    kill_windows()
    lf.close()
    print("\n---- 子进程输出 ----")
    print(open(log, encoding="utf-8").read() or "(空)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
