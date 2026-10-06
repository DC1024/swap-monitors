#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专项验证：最大化窗口到底能不能被搬走？

背景：在 Windows 上你**没法儿直接拖动**一个最大化的窗口，也没法儿用 SetWindowPos
把它挪走 —— 系统会立刻按「贴满本屏」重新布局，把它弹回原来那块屏。想搬它只有一条
路：先取消最大化 → 改「还原位置」到目标屏 → 在新屏重新最大化。
本脚本把这条路径端到端跑一遍，用实测结果回答「最大化窗口能不能移动」。

做法：起一个独立进程摆两个真的最大化窗口（各占一块屏）→ 记下各自在哪块屏 →
跑一次互换 → 再看它们的屏号和最大化状态。

⚠️ 三条踩过的坑，别改回去：
  1. 窗口必须活在**独立进程**里。若在本进程造窗口再同步调 swap_windows.py，本进程会
     阻塞在 subprocess 上、Tcl 事件循环停转，Windows 一直等这个不响应的窗口回消息
     （SetWindowPos / SetWindowPlacement 是跨进程同步调用）→ 互换直接卡死。
  2. 枚举/互换只能走**子进程**：引擎内部会排除自己那个进程的窗口，在本进程里枚举会把
     自己的示例窗口一起排掉，永远看到 0 个。
  3. 只针对「示例 · 最大化」标题，绝不碰任何真实窗口。

用法: python tests/test_maximized.py
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MARK = "示例 · 最大化"
TITLES = (f"{MARK}A", f"{MARK}B")
PY = sys.executable
FAILS = []
MAXIMIZED = "最大化"


def check(label, got, want):
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望 {want!r}\n       实际 {got!r}")
        FAILS.append(label)


def run_engine(*extra):
    r = subprocess.run([PY, os.path.join(ROOT, "swap_windows.py"),
                        "--only-title", f"^{MARK}", *extra],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, timeout=90)
    if r.returncode != 0:
        print(f"  引擎 exit={r.returncode}\n  {r.stdout}\n  {r.stderr}")
        return None
    try:
        return json.loads(r.stdout)
    except Exception as e:                       # noqa: BLE001
        print(f"  解析 JSON 失败：{e}\n  {r.stdout[:300]}")
        return None


def snapshot():
    """{标题: (屏号, 状态字符串)}，只看示例窗口。"""
    d = run_engine("--list", "--json")
    if not d:
        return {}
    return {w["title"]: (w["monitor"], w["state"]) for w in d.get("windows", [])}


def show(tag, snap):
    print(tag)
    for t in TITLES:
        mon, state = snap.get(t, ("?", "?"))
        extra = "" if t in snap else "   ← 没被识别到"
        print(f"  {t}  屏{mon}  状态={state}{extra}")


def kill_tree(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=15)
    except Exception:                            # noqa: BLE001
        pass


def main():
    # 演示窗口交给独立进程去摆，它自己跑 mainloop，消息泵一直活着
    demo = subprocess.Popen(
        [PY, os.path.join(HERE, "demo_windows.py"), "--maximize"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    try:
        # 等两个最大化窗口就位（最多 20 秒）
        before = {}
        for _ in range(40):
            time.sleep(0.5)
            before = snapshot()
            if all(t in before for t in TITLES):
                break
        else:
            print("FAIL: 20 秒内没等到示例窗口")
            print("演示进程输出：", (demo.stdout.read() if demo.stdout else "")[:400])
            return 1

        show("互换前：", before)
        b0, b1 = before[TITLES[0]], before[TITLES[1]]
        check("互换前两个窗口都是最大化", (b0[1], b1[1]), (MAXIMIZED, MAXIMIZED))
        check("互换前两个窗口分处两块屏", b0[0] != b1[0], True)

        print("\n跑一次互换（子进程，只针对示例窗口）…")
        d = run_engine("--json")
        if not d:
            FAILS.append("互换执行失败")
            return 1
        print(f"  ok={d.get('ok')}  moved={d.get('moved')}  failed={d.get('failed')}")
        for p in d.get("plan", []):
            print(f"  [{p['direction']}] {p['title']} {p['state']} "
                  f"({p['from']['left']},{p['from']['top']}) → "
                  f"({p['to']['left']},{p['to']['top']})")

        time.sleep(1.5)
        after = snapshot()
        show("\n互换后：", after)

        print("\n判定：")
        check("两个示例窗口都还在", sorted(after) == sorted(TITLES), True)
        a0, a1 = after.get(TITLES[0], (None, None)), after.get(TITLES[1], (None, None))
        check("互换后仍然都是最大化（状态没丢）", (a0[1], a1[1]), (MAXIMIZED, MAXIMIZED))
        print(f"       屏号变化：A {b0[0]}→{a0[0]}、B {b1[0]}→{a1[0]}")
        check("两个最大化窗口的屏号确实对调了", (a0[0], a1[0]), (b1[0], b0[0]))

        print("\n再换回来…")
        d2 = run_engine("--json")
        if d2:
            time.sleep(1.5)
            back = snapshot()
            r0 = back.get(TITLES[0], (None, None))
            print(f"       屏号：A {b0[0]} → {a0[0]} → {r0[0]}")
            check("再换一次回到原屏（可逆）", r0[0], b0[0])
        else:
            FAILS.append("换回来失败")
    finally:
        kill_tree(demo.pid)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("结论：最大化窗口**可以**被移动 —— 走的是「取消最大化 → 挪还原位置 → 新屏重新最大化」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
