#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专项探针：点 X（WM_CLOSE）后到底发生了什么。子进程输出落盘后再读。

用法: python tests/tray_probe.py [exe或脚本路径]

安全约定（两条都别改回去）：
  1. 只结束**本次 Popen 出来的那棵进程树**。以前这里是「按标题全盘搜索再
     TerminateProcess」—— 用户很可能正开着从 Release 下载的正式版，标题一模一样，
     会被一起杀掉。
  2. 全程用独立的临时配置（SWAPMONITORS_CONFIG 注入）。否则探针会抢注用户真实的
     全局热键，一旦误发按键就真的会搬动他自己的窗口。

场景覆盖上这个探针和 verify_gui.py --tray 有重叠；保留它是因为它会把子进程的
stdout/stderr 落盘再回读，排查「点了 X 之后程序内部到底怎么想的」更直接。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
u = ctypes.WinDLL("user32", use_last_error=True)
u.SetProcessDPIAware()
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
u.IsWindowVisible.argtypes = [wintypes.HWND]
u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
WM_CLOSE = 0x0010
TITLE = "多屏窗口互换"

TEST_CFG = os.path.join(ROOT, "assets", "_tray_probe_config.json")


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


def kill_tree(pid):
    """只结束本次启动的进程树（PyInstaller onefile 有父/子两层，所以要 /T）。"""
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        print(f"（清理进程树 pid={pid}: {'成功' if r.returncode == 0 else '已退出'}）")
    except Exception as e:                       # noqa: BLE001
        print(f"（清理进程树失败：{e}）")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "swap_gui.py")

    # 独立配置：exclude_title=.* 保证任何真实窗口都不会被搬动
    os.makedirs(os.path.dirname(TEST_CFG), exist_ok=True)
    with open(TEST_CFG, "w", encoding="utf-8") as f:
        json.dump({"hotkey": "ctrl+alt+f10", "exclude_title": ".*",
                   "skip_minimized": False, "scale": False, "restore_focus": True,
                   "autostart": False, "notify_on_swap": True, "permute": None},
                  f, ensure_ascii=False)
    env = dict(os.environ, SWAPMONITORS_CONFIG=TEST_CFG)

    cmd = [sys.executable, target] if target.endswith(".py") else [target]
    cwd = ROOT if target.endswith(".py") else os.path.dirname(os.path.abspath(target))
    log = os.path.join(ROOT, "assets", "_tray_probe.log")
    lf = open(log, "w", encoding="utf-8")
    print("启动:", cmd)
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=lf, stderr=subprocess.STDOUT)

    try:
        hwnd = None
        for _ in range(60):
            time.sleep(0.5)
            ws = find()
            if ws:
                hwnd = ws[0][0]
                break
        if not hwnd:
            print("FAIL: 没找到窗口")
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
        print("\n---- 子进程输出 ----")
        print(open(log, encoding="utf-8").read() or "(空)")
        return 0
    finally:
        lf.close()
        kill_tree(p.pid)
        for f in (TEST_CFG, log):
            if os.path.exists(f):
                os.remove(f)


if __name__ == "__main__":
    sys.exit(main())
