#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动 GUI（源码或 exe）→ 置顶截图 → 关闭。用于界面验收。

用法: python verify_gui.py [exe或脚本路径] [截图输出路径] [--hotkey] [--tray] [--min-check]

  --hotkey     额外发一次测试热键并截图（默认 Ctrl+Alt+F9，刻意避开正式版的 Ctrl+Alt+S）
  --tray       额外模拟点 X，验证「窗口隐藏但进程存活」
  --min-check  额外验一遍 --minimized 启动应静默进托盘（开机自启那条路）

安全约定：全程只写独立测试配置（SWAPMONITORS_CONFIG 注入），只清掉本次启动的进程树，
绝不按窗口标题全盘杀进程、绝不改动用户真实配置。
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


# ⚠️ 验收用的热键**刻意不用 ctrl+alt+s**。
# 理由：用户很可能正开着从 Release 下载的正式版，它已经占用了 ctrl+alt+s；
# 这时如果本脚本再发一次 ctrl+alt+s，触发的是**用户自己那个实例**，
# 会真的把他屏幕上的窗口换掉。用一个没人用的组合键才不会误伤。
TEST_HOTKEY = "ctrl+alt+f9"
TEST_HOTKEY_VKS = (0x11, 0x12, 0x78)      # Ctrl, Alt, F9


def send_test_hotkey():
    KEYUP = 0x0002
    for vk in TEST_HOTKEY_VKS:
        u.keybd_event(vk, 0, 0, None)
        time.sleep(0.03)
    for vk in reversed(TEST_HOTKEY_VKS):
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


def kill_tree(pid):
    """只结束「本次启动的进程及其子进程」。

    ⚠️ 以前这里是「按窗口标题全盘搜索再强杀」—— 那非常危险：用户很可能正开着从
    Release 下载的正式版，标题一模一样，会被一起杀掉。现在改成只针对我们自己
    Popen 出来的那棵树（PyInstaller onefile 会有 bootloader 父进程 + 应用子进程，
    所以必须 /T 连子进程一起收）。
    """
    try:
        # 中文 Windows 上 taskkill 的输出是 GBK，用 utf-8 硬解会抛 UnicodeDecodeError
        # （在后台读取线程里抛，主流程只能看到一个莫名其妙的 traceback）。
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        ok = r.returncode == 0
        print(f"（清理进程树 pid={pid}: {'成功' if ok else '已退出/无残留'}）")
        return ok
    except Exception as e:                       # noqa: BLE001
        print(f"（清理进程树失败：{e}）")
        return False


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    hotkey_test = "--hotkey" in sys.argv
    tray_test = "--tray" in sys.argv
    min_check = "--min-check" in sys.argv
    target = argv[0] if argv else os.path.join(ROOT, "swap_gui.py")
    out = argv[1] if len(argv) > 1 else os.path.join(ROOT, "assets", "gui.png")

    # 测试用独立配置：exclude_title=.* 保证任何真实窗口都不会被移动
    # hotkey 必须与 TEST_HOTKEY 一致，否则脚本发的键和被测程序注册的键对不上
    safe = {"hotkey": TEST_HOTKEY, "exclude_title": ".*", "skip_minimized": False,
            "scale": False, "restore_focus": True, "autostart": False,
            "notify_on_swap": True, "permute": None}
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
        hwnd = find_window("多屏窗口互换")
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
            print(f"发送 {TEST_HOTKEY} …")
            u.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            send_test_hotkey()
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
            print(f"托盘状态下再发一次 {TEST_HOTKEY} …")
            send_test_hotkey()
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
        rc = 0
    finally:
        time.sleep(0.3)
        # 必须**先**整棵树一起收：PyInstaller onefile 的 p.terminate() 只杀掉
        # bootloader 父进程，真正的应用子进程会变成孤儿继续活在托盘里。
        killed = kill_tree(p.pid)
        if not killed:
            p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        # 校验：用户真实配置有没有被我们碰过
        now = open(CFG_PATH, encoding="utf-8").read() if os.path.exists(CFG_PATH) else None
        print("用户真实配置未被改动:", now == real_cfg_before)
        for f in (TEST_CFG,):
            if os.path.exists(f):
                os.remove(f)

    # ── 附加阶段：开机自启那条路（带 --minimized）必须静默进托盘 ──────────
    # 对应需求「手动双击要看到界面；只有开机自启才直接待在托盘」。
    # 上面那轮是不带参数的启动（= 手动双击），能拿到窗口就已经证明了前半句；
    # 这里再验后半句：显式带上 --minimized 时，不该有任何可见窗口冒出来。
    if min_check:
        print("\n── 附加检查：--minimized（开机自启路径）应静默进托盘 ──")
        with open(TEST_CFG, "w", encoding="utf-8") as f:
            json.dump(safe, f, ensure_ascii=False)
        p2 = subprocess.Popen(cmd + ["--minimized"], cwd=cwd, env=env)
        try:
            hwnd2 = find_window("多屏窗口互换", timeout=10)
            alive = p2.poll() is None
            if hwnd2 is None and alive:
                print("✅ --minimized 启动：没有可见窗口、进程存活 → 正确定在托盘")
                rc = 0
            elif hwnd2 is not None:
                print("❌ --minimized 启动却把界面显示出来了（应该只待在托盘）")
                rc = 1
            else:
                print("❌ --minimized 启动后进程直接退出了（注册热键/托盘失败？）")
                rc = 1
        finally:
            if not kill_tree(p2.pid):
                p2.terminate()
            try:
                p2.wait(timeout=10)
            except Exception:
                p2.kill()
            if os.path.exists(TEST_CFG):
                os.remove(TEST_CFG)
    return rc


if __name__ == "__main__":
    sys.exit(main())
