#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「同一时刻只能有一个 swap() 在跑」的回归测试。

背景（v1.1.7 的 bug）：run_with_args 靠 contextlib.redirect_stdout 抓 swap()
打印的 JSON，而 redirect_stdout 换掉的是**进程级**的 sys.stdout。
do_swap 把 swap() 丢到后台线程跑（要等窗口真的动完，几百毫秒），
主线程这边「刷新」按钮 / 改下拉框触发的 refresh() 也会调 run_with_args。
两个线程一重叠，就变成互相写对方的缓冲区：

    A: 装 bufA      B: 装 bufB（把 bufA 存起来当"原来的 stdout"）
    A 打印          → 进了 bufB
    B 打印          → 也进了 bufB
    A 拿到空缓冲 → "输出解析失败"；B 拿到两行 JSON

用户看到的是「互换失败：输出解析失败」，可窗口其实已经动过了 —— 最坏的一种
错误提示：它让人以为什么都没发生。

现在的约定：
  * run_with_args 用一把全局锁把 swap() 圈起来（串行，几十到几百毫秒）；
  * refresh() 在 busy 时直接返回，既不读正在变的位置，也不会去抢那把锁
    把界面卡住。

用法: python tests/test_swap_serialized.py
需要带 tkinter 的解释器（swap_gui 顶部 import tkinter）。
"""
import json
import os
import sys
import tempfile
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ---- 隔离用户配置：必须在 import swap_gui 之前设好 ----
TMP = tempfile.mkdtemp(prefix="swapmon-serial-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False},
              f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G      # noqa: E402

# 这个测试会（故意）让 redirect_stdout 打架，旧代码下 sys.stdout 会被换成
# 一个再也回不来的 StringIO —— 那样本测试自己的断言输出就全被吞了，
# 反向验证时只能看到一片空白。所以所有输出都直接写这个句柄。
_REAL_OUT = sys.stdout

failures = []


def say(*a):
    print(*a, file=_REAL_OUT)


def check(name, cond, detail=""):
    say(f"  {'✅' if cond else '❌'} {name}"
        + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    say()
    say(f"== {t} ==")


say(f"临时目录: {TMP}")

# ============================================================
section("① 两个线程同时跑 swap()：时间段不许重叠，各拿各的 JSON")

_orig_swap = G.sw.swap
_orig_run = G.run_with_args
intervals = []
_guard = threading.Lock()


def fake_swap(ns):
    """冒充 swap()：睡一会儿（保证两个线程一定重叠），打印自己的 JSON。"""
    t0 = time.monotonic()
    time.sleep(0.2)
    print(json.dumps({"ok": True, "who": ns.who}))
    t1 = time.monotonic()
    with _guard:
        intervals.append((ns.who, t0, t1))
    return 0


results = {}


def run_one(who):
    ns = type("N", (), {})()
    ns.who = who
    results[who] = G.run_with_args(ns)


try:
    G.sw.swap = fake_swap
    ta = threading.Thread(target=run_one, args=("A",), daemon=True)
    tb = threading.Thread(target=run_one, args=("B",), daemon=True)
    ta.start()
    time.sleep(0.02)          # 让 A 先进入 swap()
    tb.start()
    ta.join(10)
    tb.join(10)
finally:
    G.sw.swap = _orig_swap
    sys.stdout = _REAL_OUT        # 旧代码下这里可能已经被换成死掉的 StringIO

check("两个线程都跑完了", len(results) == 2, str(sorted(results)))
_iv = sorted(intervals, key=lambda x: x[1])
_overlap = len(_iv) == 2 and _iv[1][1] < _iv[0][2]      # 后一段开始得比前一段结束还早
check("两段执行时间不重叠（真串行，不是各跑各的）",
      len(_iv) == 2 and not _overlap,
      str([(w, round(t0, 3), round(t1, 3)) for w, t0, t1 in _iv]))
for who in ("A", "B"):
    got = results.get(who) or {}
    check(f"{who} 拿到了自己的 JSON（不是别人的，也不是空的）",
          got.get("who") == who and got.get("ok") is True, str(got)[:200])

# ============================================================
section("② 互换进行中：refresh() 直接返回，不抢那把锁")

G.AUTOSTART_CMD = os.path.join(TMP, "SwapMonitors.cmd")
root = tk.Tk()
root.withdraw()
app = G.App(root)
try:
    called = []
    G.run_with_args = lambda ns: (called.append(ns),
                                  {"ok": True, "monitors": [], "windows": []})[1]

    app.busy = True
    out = app.refresh()
    check("忙的时候不去读窗口信息（读到的位置正在变）", called == [], str(len(called)))
    check("忙的时候 refresh 明确返回 None", out is None, repr(out))
    check("状态栏说明上一次操作还在进行", "进行中" in app.status.cget("text"),
          app.status.cget("text"))

    app.busy = False
    called.clear()
    out = app.refresh()
    check("不忙的时候照常刷新", len(called) == 1, str(len(called)))
    check("刷新成功返回数据", isinstance(out, dict) and out.get("ok") is True, repr(out))
finally:
    G.run_with_args = _orig_run
    try:
        root.destroy()
    except Exception:                                        # noqa: BLE001
        pass


say()
if failures:
    say(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
say("全部通过 ✅")
