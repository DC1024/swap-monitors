#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""swap_gui 自己的 user32 实例必须声明 argtypes/restype。

背景（v1.1.7 的隐患，审查报告第 14 条）：`swap_gui.py` 里

    u32 = ctypes.WinDLL("user32", use_last_error=True)

是**另一个** CDLL 实例，拿不到 `swap_windows.user32` 上声明过的类型。
没声明时 ctypes 按默认值处理：**参数按 c_int、返回值按 c_int**。
64 位下 HWND 是 8 字节，于是

    h = u32.FindWindowW(...)      ← 返回值被截成 32 位
    u32.GetWindowRect(h, ...)     ← 再把截断后的值传进去

属于「本机恰好没出事」的潜在崩溃点，不是现网 bug（实测
FindWindowW("Shell_TrayWnd") 返回 852304，截断前后一致，所以一直没暴露）。

这个测试钉三件事：
  ① 三个曾经裸奔的函数现在都有声明；
  ② 为什么非声明不可 —— HWND 8 字节、默认 restype 4 字节，大值会被悄悄截断；
  ③ 声明之后调用路径照常工作（拿真窗口试一次，不抛异常）。

用法: python tests/test_win32_decls.py
"""
import ctypes
import os
import sys
import tempfile
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("SWAPMONITORS_CONFIG",
                      os.path.join(tempfile.mkdtemp(prefix="swapmon-decl-"), "config.json"))

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    print()
    print(f"== {t} ==")


# ============================================================
section("① 为什么非声明不可")

check("HWND 是 8 字节，默认 restype(c_int) 只有 4 字节",
      ctypes.sizeof(wintypes.HWND) > ctypes.sizeof(ctypes.c_int),
      f"HWND={ctypes.sizeof(wintypes.HWND)} c_int={ctypes.sizeof(ctypes.c_int)}")
_trunc = ctypes.c_int(0x1_0000_1234).value
check("高 32 位非零的 HWND 会被 c_int 悄悄截断（不报错）",
      _trunc == 0x1234, hex(_trunc))

# ============================================================
section("② 三个裸奔的函数现在都有声明")

for fn, want_ret in (("FindWindowW", wintypes.HWND),
                     ("GetWindowRect", wintypes.BOOL),
                     ("SetForegroundWindow", wintypes.BOOL)):
    f = getattr(G.u32, fn)
    check(f"{fn}.restype 已声明为 {getattr(want_ret, '__name__', want_ret)}",
          f.restype is want_ret, repr(f.restype))
    check(f"{fn} 有 argtypes", bool(f.argtypes), repr(f.argtypes))

check("FindWindowW 的入参声明成两个宽字符串",
      list(G.u32.FindWindowW.argtypes or []) == [wintypes.LPCWSTR, wintypes.LPCWSTR],
      str(G.u32.FindWindowW.argtypes))
check("GetWindowRect 的 hwnd 参数按 HWND 传（不再被截断）",
      (G.u32.GetWindowRect.argtypes or [None])[0] is wintypes.HWND,
      str(G.u32.GetWindowRect.argtypes))
check("SetForegroundWindow 的 hwnd 参数按 HWND 传",
      (G.u32.SetForegroundWindow.argtypes or [None])[0] is wintypes.HWND,
      str(G.u32.SetForegroundWindow.argtypes))

# ============================================================
section("③ 声明之后调用路径照常工作")

# _overflow_left 走的就是 FindWindowW + GetWindowRect 这条链。
# 不依赖 self，直接拿 None 调；Win10 上没有溢出区窗口，返回 None 是对的。
try:
    left = G.App._overflow_left(None)
    check("_overflow_left() 不抛异常", True)
    check("返回值要么是 None（没有溢出区），要么是个整数",
          left is None or isinstance(left, int), repr(left))
except Exception as e:                                       # noqa: BLE001
    check("_overflow_left() 不抛异常", False, repr(e))

# 真拿一个一定存在的顶层窗口走一遍 GetWindowRect（任务栏）。
# CI 的 runner 可能没有交互式桌面会话，拿不到就显式跳过，别当失败。
try:
    h = G.u32.FindWindowW("Shell_TrayWnd", None)
    if not h:
        print("  ⏭ 拿不到任务栏窗口（没有交互式桌面会话），跳过矩形校验")
    else:
        r = wintypes.RECT()
        ok = G.u32.GetWindowRect(h, ctypes.byref(r))
        check("GetWindowRect 拿得到任务栏矩形", bool(ok), f"ok={ok}")
        check("矩形宽度是正数（说明 hwnd 没被截断成别的窗口）",
              r.right - r.left > 0, str((r.left, r.top, r.right, r.bottom)))
except Exception as e:                                       # noqa: BLE001
    check("任务栏窗口查询不抛异常", False, repr(e))

print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
