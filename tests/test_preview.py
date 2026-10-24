#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「预览计划」那一列的回归测试。

背景（v1.1.7 的 bug）：预览时拿 (标题, plan.from.left) 去对表格里的
(label, 当前 rect.left)。可最大化窗口的 plan.from 是 rcNormalPosition
（还原位置），表格里显示的是当前 rect —— 两个数根本不是一回事。于是最常用的
「有窗口最大化 + 双屏」状态下，「互换后」这一列整片空白，用户只会以为预览坏了。

现在两边都用 hwnd 对齐。这个测试把引擎边界 stub 掉，用手工构造的两块屏 +
三个窗口（其中一个是最大化）走完整链路：

    refresh() 填表格  →  _on_swapped(plan, dry=True) 填「互换后」列

并**反向断言**：旧键 (标题, from.left) 在这份 fixture 上确实一条都匹配不上 ——
否则这个用例就是假绿，测不出任何东西。

用法: python tests/test_preview.py
"""
import json
import os
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="swapmon-preview-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False}, f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def rect(left, top, width, height):
    return {"left": left, "top": top, "right": left + width, "bottom": top + height,
            "width": width, "height": height}


def mon(index, left, primary=False):
    return {"index": index, "device": f"\\\\.\\DISPLAY{index}", "primary": primary,
            **rect(left, 0, 2560, 1440)}


# 两块屏：屏1 在左（-2560），屏2 是主屏（0）
MONITORS = [mon(1, -2560), mon(2, 0, primary=True)]

# 三个窗口。第二个是**最大化**：当前 rect 是 (-8,-8) 贴着屏2 的边，
# 而它的「还原位置」是 (-1855, 980) —— 正是 v1.1.7 对不上的那种情况。
LIST_PAYLOAD = {
    "ok": True,
    "monitors": MONITORS,
    "mapping": [{"from": 1, "to": 2}, {"from": 2, "to": 1}],
    "windows": [
        {"hwnd": 1001, "monitor": 1, "state": "普通", "title": "记事本 - 无标题",
         "label": "记事本 - 无标题", "proc": "notepad.exe", "pid": 11,
         **rect(-2568, 0, 1200, 800)},
        {"hwnd": 1002, "monitor": 2, "state": "最大化", "title": "浏览器",
         "label": "浏览器", "proc": "chrome.exe", "pid": 22,
         **rect(-8, -8, 2576, 1456)},
        {"hwnd": 1003, "monitor": 1, "state": "普通", "title": "终端",
         "label": "终端", "proc": "WindowsTerminal.exe", "pid": 33,
         **rect(-2400, 100, 900, 600)},
    ],
}

PLAN_PAYLOAD = {
    "ok": True, "dry_run": True, "mode": "translate",
    "monitors": MONITORS,
    "plan": [
        {"hwnd": 1001, "title": "记事本 - 无标题", "proc": "notepad.exe", "pid": 11,
         "state": "普通", "mode": "translate", "direction": "屏1→屏2",
         "from": rect(-2568, 0, 1200, 800), "to": rect(-8, 0, 1200, 800)},
        # 关键：from 是还原位置 (-1855, 980)，和表格里那行的 (-8,-8) 完全对不上
        {"hwnd": 1002, "title": "浏览器", "proc": "chrome.exe", "pid": 22,
         "state": "最大化", "mode": "translate", "direction": "屏2→屏1",
         "from": rect(-1855, 980, 1200, 800), "to": rect(-1855, 980, 1200, 800)},
        {"hwnd": 1003, "title": "终端", "proc": "WindowsTerminal.exe", "pid": 33,
         "state": "普通", "mode": "translate", "direction": "屏1→屏2",
         "from": rect(-2400, 100, 900, 600), "to": rect(-8, 100, 900, 600)},
    ],
    "moved": 0, "failed": 0, "errors": [],
}


def main():
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)

    # 引擎边界 stub 掉：真实枚举在 CI 的 headless runner 上什么也拿不到
    _orig_run = G.run_with_args
    G.run_with_args = lambda ns: json.loads(json.dumps(LIST_PAYLOAD))
    try:
        app.refresh()
    finally:
        G.run_with_args = _orig_run

    print("== 1) 表格行 id 必须是 hwnd ==")
    items = app.tree.get_children()
    check("三行都在", len(items) == 3, f"实际 {len(items)} 行")
    check("行 id 就是 hwnd（预览才能靠它对齐）",
          set(items) == {"1001", "1002", "1003"}, str(items))

    print("\n== 2) 预览要把「互换后」填满 ==")
    app._on_swapped(json.loads(json.dumps(PLAN_PAYLOAD)), True)
    filled = {it: app.tree.set(it, "to") for it in app.tree.get_children()}
    for hwnd, text in filled.items():
        check(f"hwnd {hwnd} 的「互换后」有内容", bool(text.strip()), repr(text))
    check("最大化窗口那行填的是它的方向（以前这里必定是空的）",
          filled.get("1002", "").startswith("屏2→屏1"), repr(filled.get("1002")))

    print("\n== 3) 反向：旧算法在这份数据上会漏掉哪几行 ==")
    # 旧键 = (plan.title, plan.from.left)；表格里能给的是 (label, 当前 rect.left)
    old_keys = {(p["title"], p["from"]["left"]) for p in PLAN_PAYLOAD["plan"]}
    matched, missed = set(), set()
    for it in app.tree.get_children():
        v = app.tree.item(it, "values")
        try:
            key = (v[4], int(str(v[2]).split(",")[0]))
        except (ValueError, IndexError):
            missed.add(it)
            continue
        (matched if key in old_keys else missed).add(it)
    check("旧算法会漏掉的正好是最大化那一行（其余两行能对上）",
          missed == {"1002"}, f"漏掉 {sorted(missed)}，对上 {sorted(matched)}")
    check("也就是说：只要有一个窗口最大化，它那一列以前就是空的", "1002" in missed)
    _plan_from = next(p["from"]["left"] for p in PLAN_PAYLOAD["plan"] if p["hwnd"] == 1002)
    # 老代码里行 id 是 Tk 自动生成的 I001…，取不到就说明「行 id 用 hwnd」没生效
    _row_left = None
    if "1002" in app.tree.get_children():
        _row_left = int(str(app.tree.item("1002", "values")[2]).split(",")[0])
    check("最大化窗口的 plan.from.left 与表格里的 rect.left 确实不是一个数",
          _plan_from == -1855 and _row_left == -8, f"from={_plan_from} rect={_row_left}")

    root.destroy()

    print()
    if failures:
        print(f"❌ {len(failures)} 项未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("全部通过 ✅ —— 预览列按 hwnd 对齐，最大化窗口不再整列空白")
    return 0


if __name__ == "__main__":
    sys.exit(main())
