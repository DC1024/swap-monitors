#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""move_window 的调用契约 + 失败路径冒烟测试。

为什么需要它：`swap()` 的核心就是「算好位置 → 逐个 move_window」，但 CI 里
**没有任何测试碰过这个函数** —— 需要真显示器的 test_selftest 不在 CI 跑。
于是像「删掉一个没人用的参数、忘了改调用点」这种改动，本地一跑 `--dry-run`
是绿的（dry-run 在 move_window 之前就 return 了），要等用户按下热键才炸。

这里用**无效 hwnd**（0xDEAD）把三条分支都走一遍：
  * 普通窗口 / 最小化 / 最大化，全部返回 (False, 说明) 而不是抛异常；
  * 顺便钉住签名就是 (win, target, dst) 三个参数 —— 以前有个 verbose，
    函数体里压根没用过，删掉之后调用点必须同步。
无效句柄下所有 Win32 调用都只会失败返回，不会移动任何真实窗口。

用法: python tests/test_move_window.py
"""
import inspect
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("SWAPMONITORS_CONFIG",
                      os.path.join(tempfile.mkdtemp(prefix="swapmon-mw-"), "config.json"))

import swap_windows as sw      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    print()
    print(f"== {t} ==")


def mk_rect(l, t, r, b):
    rc = sw.RECT()
    rc.left, rc.top, rc.right, rc.bottom = l, t, r, b
    return rc


def mk_win(show_cmd):
    rc = mk_rect(100, 100, 900, 700)
    place = sw.WINDOWPLACEMENT()
    place.showCmd = show_cmd
    place.rcNormalPosition = mk_rect(100, 100, 900, 700)
    # 0xDEAD 是个无效句柄：Win32 一律拒绝，绝不会动到真实窗口
    return sw.Win(0xDEAD, "不存在的窗口", "Class", 4242, "nope.exe", rc, 0, place)


class FakeMon:
    def __init__(self, left):
        self.rect = mk_rect(left, 0, left + 1920, 1080)
        self.work = mk_rect(left, 0, left + 1920, 1040)


TARGET = mk_rect(2020, 100, 2820, 700)
DST = FakeMon(1920)

# ============================================================
section("① 签名：只有 (win, target, dst)")

params = list(inspect.signature(sw.move_window).parameters)
check("参数就是 win / target / dst 三个", params == ["win", "target", "dst"], str(params))

# ============================================================
section("② 三条分支都要返回失败说明，而不是抛异常")

for label, show_cmd in (("普通窗口", sw.SW_SHOWNORMAL),
                        ("最小化窗口", sw.SW_SHOWMINIMIZED),
                        ("最大化窗口", sw.SW_SHOWMAXIMIZED)):
    w = mk_win(show_cmd)
    try:
        ok, note = sw.move_window(w, TARGET, DST)
    except Exception as e:                                   # noqa: BLE001
        check(f"{label}：不该抛异常", False, repr(e))
        continue
    check(f"{label}：无效句柄下如实返回失败", ok is False, f"ok={ok} note={note!r}")
    check(f"{label}：失败时给了说明文字", isinstance(note, str) and note != "", repr(note))

# ============================================================
section("③ 按 swap() 的方式调用（位置参数，不多不少）")

w = mk_win(sw.SW_SHOWNORMAL)
try:
    ok, note = sw.move_window(w, TARGET, DST)      # 和 swap() 里的写法完全一致
    check("三个位置参数能直接调通", ok is False, f"ok={ok}")
except TypeError as e:
    check("三个位置参数能直接调通", False, repr(e))

print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
