#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「部分排列会让窗口叠在一起」的提醒回归测试。

背景（v1.1.7）：排列编辑器允许 {1: 2} 这种写法 —— 屏1 的窗口搬到屏2，
而屏2 **自己没有去向**，它原有的窗口留在原地 → 两堆窗口叠在一起。
_on_perm_change 只拦「两块屏抢同一块目标屏」，不拦这个，界面上一个字都没有。

对一个以「布局不乱」为卖点的工具，这是语义陷阱。但自动补成对换会让
「只搬一块屏」这种正当需求没法表达，所以本次**只加提示、不改语义**：

  * self.perm 原样保留 {1: 2}，引擎照旧只搬屏1；
  * 卡片下方多一条常驻提醒：「屏2 没有设置去向…会叠在一起」；
  * 完整对换 {1:2,2:1}、轮转 {1:2,2:3,3:1}、全部不动 {} 都不提示。

为什么放常驻提示条而不是状态栏：_on_perm_change 末尾是 root.after(60, refresh)，
而 refresh() 收尾一句 set_status("已刷新") 会把状态栏冲掉 —— 提示只闪 60ms，
等于没说。

用法: python tests/test_partial_perm.py
"""
import json
import os
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="swapmon-partial-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False, "permute": {}},
              f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def mon(index, left, primary=False):
    return {"index": index, "device": f"\\\\.\\DISPLAY{index}", "primary": primary,
            "left": left, "top": 0, "right": left + 2560, "bottom": 1440,
            "width": 2560, "height": 1440}


MON2 = [mon(1, -2560), mon(2, 0, primary=True)]
MON3 = [mon(1, -2560), mon(2, 0, primary=True), mon(3, 2560)]


def on_disk_perm():
    try:
        with open(TEST_CFG, encoding="utf-8") as f:
            return json.load(f).get("permute")
    except Exception:                                        # noqa: BLE001
        return None


def main():
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)

    # 真实枚举在 CI 上是 headless（0 块屏），这里直接注入假数据
    app.mons = MON2
    app._perm_n = 2

    print("== 1) _stack_hint 的判定 ==")
    _hint = getattr(app, "_stack_hint", None)
    if not callable(_hint):
        check("App 提供了 _stack_hint() 这个判定入口", False, "没有这个方法")
        print("  （后面几节都依赖它，跳过）")
    else:
        # 前提：{1: 2} 的目标屏 2 确实没有自己的去向 —— 这正是会叠的那种写法
        check("前提：{1:2} 里屏2 没有自己的去向", 2 not in {1: 2})

        h = _hint({1: 2})
        check("{1:2} 会给出提醒", bool(h), repr(h))
        check("提醒点名了屏2", "屏2" in h, repr(h))
        check("提醒说明了「留在原地」", "留在原地" in h, repr(h))

        check("完整对换 {1:2,2:1} 不提醒", _hint({1: 2, 2: 1}) == "",
              repr(_hint({1: 2, 2: 1})))
        check("全部不动 {} 不提醒", _hint({}) == "", repr(_hint({})))
        check("两屏只剩一块动 {2:1} 会提醒屏1", "屏1" in _hint({2: 1}),
              repr(_hint({2: 1})))
        check("越界目标（屏9）不崩、也不乱报", _hint({1: 9}) == "",
              repr(_hint({1: 9})))

        # 三屏：轮转是完整排列，链条是部分排列
        app.mons = MON3
        check("三屏轮转 {1:2,2:3,3:1} 不提醒", _hint({1: 2, 2: 3, 3: 1}) == "",
              repr(_hint({1: 2, 2: 3, 3: 1})))
        check("三屏链条 {1:2,2:3} 提醒屏3", "屏3" in _hint({1: 2, 2: 3}),
              repr(_hint({1: 2, 2: 3})))
        check("两块屏挤到同一块目标屏时提醒的是那个目标",
              "屏2" in _hint({1: 2, 3: 2}), repr(_hint({1: 2, 3: 2})))

    print("\n== 2) 卡片下方的提醒条真的出现/收起 ==")
    app.mons = MON2
    app.perm = {1: 2, 2: 1}
    app._sync_cards()
    _warn = getattr(app, "perm_warn", None)
    if _warn is None:
        check("界面上有 perm_warn 这个提醒控件", False, "老代码里没有")
    else:
        check("对换时提醒条不占位", _warn.winfo_manager() == "",
              repr(_warn.winfo_manager()))
        check("对换时提醒条文字为空", _warn.cget("text") == "",
              repr(_warn.cget("text")))

        app.perm = {1: 2}
        app._sync_cards()
        check("{1:2} 时提醒条出现", _warn.winfo_manager() == "pack",
              repr(_warn.winfo_manager()))
        check("提醒条文字点名屏2", "屏2" in _warn.cget("text"),
              repr(_warn.cget("text")))
        check("提醒条用的是醒目样式 Warn.TLabel",
              str(_warn.cget("style")) == "Warn.TLabel", repr(_warn.cget("style")))

        app.perm = {}
        app._sync_cards()
        check("改回全部不动后提醒条收起", _warn.winfo_manager() == "",
              repr(_warn.winfo_manager()))

    print("\n== 3) 走用户真实路径：在下拉里选「屏2」 ==")
    app.perm = {}
    app.cfg["permute"] = {}
    app._sync_cards()
    app.card_widgets[0]["var"].set("屏2")     # 屏1 → 屏2，屏2 保持「不动」
    app._on_perm_change(0)

    check("perm 仍然是 {1: 2}（只提示，不改语义）", app.perm == {1: 2}, str(app.perm))
    check("改完立刻显示提醒，不用等 60ms 后的重建",
          getattr(app, "perm_warn", None) is not None
          and app.perm_warn.winfo_manager() == "pack")
    check("落盘的 permute 也是 {\"1\": 2}（没有偷偷补成对换）",
          on_disk_perm() == {"1": 2}, str(on_disk_perm()))

    root.destroy()

    print()
    if failures:
        print(f"❌ {len(failures)} 项未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("全部通过 ✅ —— 部分排列会提醒「目标屏的窗口留在原地」，语义没动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
