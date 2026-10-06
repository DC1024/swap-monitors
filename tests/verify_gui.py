#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动 GUI（源码或 exe）→ 置顶截图 → 关闭。用于界面验收。

用法: python verify_gui.py [exe或脚本路径] [截图输出路径]
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
u = ctypes.WinDLL("user32", use_last_error=True)
u.SetProcessDPIAware()
u.FindWindowW.restype = wintypes.HWND
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.IsWindowVisible.argtypes = [wintypes.HWND]
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
u.SetForegroundWindow.argtypes = [wintypes.HWND]
u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
u.IsWindow.argtypes = [wintypes.HWND]
u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
WM_CLOSE = 0x0010

CFG_DIR = os.path.join(os.environ.get("APPDATA") or "", "SwapMonitors")
CFG_PATH = os.path.join(CFG_DIR, "config.json")
# 测试一律用独立的临时配置（靠环境变量 SWAPMONITORS_CONFIG 注入），绝不碰用户真实设置
TEST_CFG = os.path.join(ROOT, "assets", "_test_config.json")


def send_hotkey_ctrl_alt_s():
    VK_CONTROL, VK_MENU, VK_S = 0x11, 0x12, 0x53
    KEYUP = 0x0002
    for vk in (VK_CONTROL, VK_MENU, VK_S):
        u.keybd_event(vk, 0, 0, None)
        time.sleep(0.03)
    for vk in (VK_S, VK_MENU, VK_CONTROL):
        u.keybd_event(vk, 0, KEYUP, None)
        time.sleep(0.03)


def find_window(substr, timeout=40):
    deadline = time.time() + timeout
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def cb(hwnd, lp):
        if not u.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        if substr in buf.value:
            found.append(hwnd)
            return False
        return True

    while time.time() < deadline:
        found.clear()
        u.EnumWindows(CB(cb), 0)
        if found:
            return found[0]
        time.sleep(0.5)
    return None


def hard_kill_leftovers(substr="双屏窗口互换"):
    """p.terminate() 在某些环境下不生效；按窗口标题找到残留进程后强杀，避免污染后续测试。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    PROCESS_TERMINATE = 0x0001
    pids = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lp):
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        if substr in buf.value:
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value not in pids:
                pids.append(pid.value)
        return True

    u.EnumWindows(CB(cb), 0)
    for pid in pids:
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            kernel32.TerminateProcess(h, 1)
            kernel32.CloseHandle(h)
    if pids:
        print(f"（清理残留进程: {pids}）")


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    hotkey_test = "--hotkey" in sys.argv
    tray_test = "--tray" in sys.argv
    target = argv[0] if argv else os.path.join(ROOT, "swap_gui.py")
    out = argv[1] if len(argv) > 1 else os.path.join(ROOT, "assets", "gui.png")

    # 测试用独立配置：exclude_title=.* 保证任何真实窗口都不会被移动
    safe = {"hotkey": "ctrl+alt+s", "exclude_title": ".*", "skip_minimized": False,
            "scale": False, "restore_focus": True, "autostart": False,
            "start_minimized": False}
    os.makedirs(os.path.dirname(TEST_CFG), exist_ok=True)
    with open(TEST_CFG, "w", encoding="utf-8") as f:
        json.dump(safe, f, ensure_ascii=False)
    real_cfg_before = open(CFG_PATH, encoding="utf-8").read() if os.path.exists(CFG_PATH) else None
    print(f"已写入独立测试配置: {TEST_CFG}")
    print(f"（用户真实配置 {CFG_PATH} 只会做只读校验，不改动）")

    env = dict(os.environ, SWAPMONITORS_CONFIG=TEST_CFG)
    cmd = [sys.executable, target] if target.endswith(".py") else [target]
    # exe 用成品自己所在目录做 cwd —— 顺便证明它不依赖源码目录
    cwd = ROOT if target.endswith(".py") else os.path.dirname(os.path.abspath(target))
    print("启动:", cmd, "\ncwd:", cwd)
    p = subprocess.Popen(cmd, cwd=cwd, env=env)
    try:
        hwnd = find_window("双屏窗口互换")
        if not hwnd:
            print("FAIL: 40 秒内没找到窗口")
            return 1
        print("找到窗口 hwnd =", hwnd)
        u.ShowWindow(hwnd, 9)          # SW_RESTORE
        u.SetWindowPos(hwnd, wintypes.HWND(-1), 40, 40, 0, 0, 0x0001 | 0x0040)
        u.SetForegroundWindow(hwnd)
        time.sleep(3.0)                # 等界面把状态刷出来
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        print(f"窗口矩形: ({r.left},{r.top})-({r.right},{r.bottom}) "
              f"{r.right - r.left}x{r.bottom - r.top}")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
        img.save(out)
        print("截图已保存:", out, img.size)

        if hotkey_test:
            print("发送 Ctrl+Alt+S …")
            u.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            send_hotkey_ctrl_alt_s()
            time.sleep(3.5)
            r2 = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r2))
            out2 = out.replace(".png", "_after_hotkey.png")
            ImageGrab.grab(bbox=(r2.left, r2.top, r2.right, r2.bottom)).save(out2)
            print("热键后截图已保存:", out2)

        if tray_test:
            # 点 X（发 WM_CLOSE）→ 应该收进托盘：窗口不可见但进程仍在
            print("发送 WM_CLOSE（模拟点 X）…")
            u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            time.sleep(2.0)
            print(f"  IsWindow(hwnd)     = {bool(u.IsWindow(hwnd))}")
            print(f"  IsWindowVisible    = {bool(u.IsWindowVisible(hwnd))}")
            print(f"  进程还在跑         = {p.poll() is None}  (None 表示仍在运行)")
            gone = not (u.IsWindow(hwnd) and u.IsWindowVisible(hwnd))
            alive = p.poll() is None
            print("  " + ("✅ 已收进托盘（窗口隐藏、进程存活）" if gone and alive
                          else "❌ 未按预期收进托盘"))
            # 收进托盘后再按一次热键，验证后台仍然工作
            print("托盘状态下再发一次 Ctrl+Alt+S …")
            send_hotkey_ctrl_alt_s()
            time.sleep(3.5)
            alive2 = p.poll() is None
            print(f"  进程仍在跑         = {alive2}")
            full = os.path.join(os.path.dirname(out),
                                os.path.basename(out).replace(".png", "_tray_fullscreen.png"))
            ImageGrab.grab().save(full)      # 整屏（含托盘气泡，若有）
            print("整屏截图已保存:", full)
            results = {"hidden_ok": gone, "alive_after_close": alive,
                       "alive_after_hotkey": alive2}
            print("托盘自检结果:", results)
        return 0
    finally:
        time.sleep(0.3)
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        hard_kill_leftovers()
        # 校验：用户真实配置有没有被我们碰过
        now = open(CFG_PATH, encoding="utf-8").read() if os.path.exists(CFG_PATH) else None
        print("用户真实配置未被改动:", now == real_cfg_before)
        for f in (TEST_CFG,):
            if os.path.exists(f):
                os.remove(f)


if __name__ == "__main__":
    sys.exit(main())
