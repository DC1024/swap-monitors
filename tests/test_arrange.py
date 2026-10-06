#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排列编辑器（GUI 里「每块屏的窗口送往」那套控件）的逻辑测试。

不需要真的插三块屏 —— 把 app.mons 换成伪造的显示器列表就能验多屏分支。
只跑逻辑，不移动任何窗口（配置里 exclude_title=.* 兜底）。

用法: python tests/test_arrange.py
"""
import json
import os
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TEST_CFG = os.path.join(ROOT, "assets", "_arrange_cfg.json")
os.makedirs(os.path.dirname(TEST_CFG), exist_ok=True)

BASE_CFG = {"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "skip_minimized": False,
            "scale": False, "restore_focus": True, "autostart": False,
            "notify_on_swap": True, "permute": None}


def write_cfg(**over):
    cfg = dict(BASE_CFG)
    cfg.update(over)
    with open(TEST_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return cfg


write_cfg()
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G      # noqa: E402
import swap_windows as sw  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        print(f"       期望 {want!r}\n       实际 {got!r}")
        FAILS.append(label)
    return ok


def fake_mon(i, left, primary=False, w=2560, h=1440):
    """伪造一块显示器，字段与引擎 --list 的输出保持一致。"""
    return {"device": f"\\\\.\\DISPLAY{i + 1}", "left": left, "top": 0,
            "width": w, "height": h, "right": left + w, "bottom": h,
            "primary": primary, "work": [left, 0, left + w, h]}


def main():
    root = tk.Tk()
    root.withdraw()                       # 测试不开窗
    app = G.App(root)

    real_n = len(app.mons)
    print(f"（本机实际 {real_n} 块屏；多屏分支用伪造数据验证）\n")

    # ── 1. 老配置里没有 permute 时，默认 = 互换最左两块 ─────────────────
    print("=== 1. 默认排列 ===")
    app.cfg["permute"] = None
    app._perm_n = None
    app._ensure_perm()
    app._sync_cards()
    check("默认 perm", app.perm, {1: 2, 2: 1})
    check("自逆（可以对换回来）", app._is_involution(), True)
    check("映射说明", app._map_desc(), "2 对全为纯平移")
    check("按钮文案", app.redo_btn.cget("text"), "恢复原状（再换一次）")
    check("传给引擎的 permute 串", app._make_args().permute, "1>2,2>1")

    # ── 2. 引擎能不能正确解读这个串（GUI 与 CLI 的契约）────────────────
    print("\n=== 2. GUI→引擎 的串往返 ===")
    spec = app._make_args().permute
    check("parse_permute 解析结果", sw.parse_permute(spec, 2), {0: 1, 1: 0})
    check("perm_to_spec 反写", sw.perm_to_spec({0: 1, 1: 0}), "1>2,2>1")

    # ── 3. 三个预设按钮 ────────────────────────────────────────────
    print("\n=== 3. 预设按钮（两屏）===")
    app.preset_rotate()
    check("环形轮转（两屏时等价于对换）", app.perm, {1: 2, 2: 1})
    app.preset_pair()
    check("两两互换", app.perm, {1: 2, 2: 1})
    app.preset_none()
    check("全部不动", app.perm, {})
    check("全不动时的说明", app._map_desc(), "没有任何屏参与（全部不动）")
    check("全不动时传给引擎的是空串", app._make_args().permute, "")
    check("空串在引擎侧合法且表示都不动", sw.parse_permute("", 2), {})
    check("全不动时不说「恢复原状」", app._is_involution(), False)

    # ── 4. 伪造三块屏：轮转要真的转起来 ─────────────────────────────
    print("\n=== 4. 三屏轮转（伪造显示器）===")
    app.mons = [fake_mon(0, -2560), fake_mon(1, 0, primary=True), fake_mon(2, 5120)]
    app._perm_n = None                    # 强制重新校验排列
    app._ensure_perm()
    app._sync_cards()
    check("三屏卡片数", len(app.card_widgets), 3)
    app.preset_rotate()
    check("轮转排列", app.perm, {1: 2, 2: 3, 3: 1})
    check("轮转不是自逆（要转满 3 次）", app._is_involution(), False)
    check("按钮文案改成「再轮转一次」", app.redo_btn.cget("text"), "再轮转一次")
    check("映射说明", app._map_desc(), "3 对全为纯平移")
    check("传给引擎的串", app._make_args().permute, "1>2,2>3,3>1")
    check("引擎解析三屏轮转", sw.parse_permute("1>2,2>3,3>1", 3), {0: 1, 1: 2, 2: 0})
    # 轮转三次必须回到原位：perm^3 == identity
    p = app.perm
    for _ in range(2):
        p = {s: app.perm[d] for s, d in p.items()}
    check("轮转三次后回到原状", p, {1: 1, 2: 2, 3: 3})

    # 三屏下换了分辨率就该被识别为缩放对
    # 注意：排列是 1→2、2→3、3→1，把屏3改成 3840 会同时让 2→3 和 3→1 两对变成缩放
    app.mons[2]["width"], app.mons[2]["right"] = 3840, 5120 + 3840
    check("混排时按对统计缩放", app._map_desc(), "3 对中 2 对缩放、1 对平移")
    app.mons[2].update(width=2560, right=5120 + 2560)

    # 两两互换在三屏下只动前两块
    app.preset_pair()
    check("三屏下的「两两互换」只动前两块", app.perm, {1: 2, 2: 1})

    # ── 5. 下拉撞车：两块屏不能选同一块目标屏 ──────────────────────
    print("\n=== 5. 下拉撞车保护 ===")
    # preset_* 内部会 refresh()，而 refresh() 会把 mons 换成真实显示器 ——
    # 所以这里必须把伪造的三屏再装回去，否则卡片只剩两张，测的就不是三屏分支了。
    app.mons = [fake_mon(0, -2560), fake_mon(1, 0, primary=True), fake_mon(2, 5120)]
    app._perm_n = None
    app._ensure_perm()
    app.preset_none()
    app.mons = [fake_mon(0, -2560), fake_mon(1, 0, primary=True), fake_mon(2, 5120)]
    app._perm_n = None
    app._ensure_perm()
    app._sync_cards()
    check("三张卡都就位", len(app.card_widgets), 3)
    for cw in app.card_widgets:           # 手改三张卡都指向「屏1」
        cw["var"].set("屏1")
    app._on_perm_change(2)                # 刚被改的是第 3 张卡
    check("第 3 张卡抢到了屏1", app.perm.get(3), 1)
    check("撞车的卡被让开（没有两块屏共用一个目标）",
          len(set(app.perm.values())), len(app.perm))

    # 自环（送往自己）不能原样流到引擎 —— parse_permute 会拒收
    app._sync_cards()
    cw0 = app.card_widgets[0]
    cw0["var"].set("屏1")                 # 第 1 张卡选「屏1」= 自己
    app._on_perm_change(0)
    check("自环被丢弃（等于不动）", 1 in app.perm, False)
    try:
        sw.parse_permute(app._make_args().permute, 3)
        engine_ok = True
    except ValueError as e:               # 引擎拒收就说明脏数据漏出去了
        engine_ok = False
        print(f"       引擎报错：{e}")
    check("剩下的排列仍能被引擎接受", engine_ok, True)

    # ── 6. 坏配置要整体退回默认，不能半截生效 ──────────────────────
    print("\n=== 6. 非法配置的兜底 ===")
    for label, raw in (("目标屏重复", {"1": 2, "2": 2}),
                       ("屏号越界", {"1": 5, "5": 1}),
                       ("送往自己", {"1": 1}),
                       ("不是字典", [1, 2]),
                       ("值不是数字", {"1": "二"})):
        app.cfg["permute"] = raw
        check(f"{label} → 退回默认", app._load_perm(2), {1: 2, 2: 1})
    app.cfg["permute"] = {"1": 2, "2": 3, "3": 1}
    check("合法三屏配置被原样接受", app._load_perm(3), {1: 2, 2: 3, 3: 1})

    # ── 7. 窗口高度必须装得下内容 ──────────────────────────────────
    # 回归守卫：曾经尺寸是在 refresh() 更新计数文字、start_hotkey() 写状态栏
    # **之前**量的，窗口比内容矮 100px 上下，底部那排开关直接被裁掉看不见。
    # 这里用「三块屏 → 卡片排成两行」的最坏情况来量。
    print("\n=== 7. 窗口尺寸装得下内容（防裁切回归）===")
    root.deiconify()
    app._fit()
    root.update_idletasks()
    need, got = root.winfo_reqheight(), root.winfo_height()
    print(f"       内容需要 {need}px，窗口实际 {got}px")
    check("窗口高度 >= 内容所需高度", got >= need, True)
    mw, mh = root.minsize()
    check("minsize 也不小于内容高度", mh >= need, True)
    root.withdraw()

    root.destroy()
    os.remove(TEST_CFG)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("全部 PASS —— 排列编辑器逻辑正确（两屏 / 三屏 / 坏配置兜底都覆盖到了）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
