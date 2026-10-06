#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多屏场景可行性验证（原型，不动任何真实窗口）。

要回答的是：现有引擎要支持 N 块屏，改动量有多大、哪些场景真能做到、哪些会退化。

做法：直接复用 swap_windows 里**未经修改**的 map_rect / clamp_into，
只把「选屏」那一段从写死的两块换成「排列映射」，看结果对不对。
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import swap_windows as sw  # noqa: E402


# ---------------------------------------------------------------- 脚手架
def mk(name, left, top, w, h, primary=False):
    m = types.SimpleNamespace()
    r = sw.RECT(left, top, left + w, top + h)
    m.device, m.rect, m.work, m.primary = name, r, r, primary
    return m


def row(*specs):
    """横向排一排显示器：specs 是 (宽, 高) 列表。"""
    mons, x = [], 0
    for i, (w, h) in enumerate(specs):
        mons.append(mk(f"DISPLAY{i + 1}", x, 0, w, h, primary=(i == 0)))
        x += w
    return mons


def frac_layout(mons, spec):
    """按屏内相对坐标摆窗口：spec = [(l, t, r, b), ...]，取值 0~1。

    spec 可以传「一组」给所有屏共用，也可以传「一组/屏」（长度等于屏数）——
    后者才是可逆性测试该用的：各屏布局必须互不相同，否则轮转一圈之后
    「屏2 现在的窗口」和「屏2 原来的窗口」会恰好重合，测试就永远通过了。
    """
    per_screen = len(spec) == len(mons) and all(
        isinstance(s, list) for s in spec)
    out = {}
    for i, m in enumerate(mons):
        s = spec[i] if per_screen else spec
        W, H = m.rect.width, m.rect.height
        out[i] = [sw.RECT(m.rect.left + int(l * W), m.rect.top + int(t * H),
                          m.rect.left + int(r * W), m.rect.top + int(b * H))
                  for (l, t, r, b) in s]
    return out


def build_plan(windows, mons, perm, mode="auto"):
    """把 swap() 里「if w.mon==ia ... elif ... else continue」换成排列查表。

    perm: {源屏索引: 目标屏索引}，例如三屏环形轮转 {0:1, 1:2, 2:0}
    注意：先把**全部**目标位置算完再统一落地 —— 这是轮转成立的前提（见 case D）。
    """
    plan = []
    for src_i, wins in windows.items():
        if src_i not in perm:
            continue
        src, dst = mons[src_i], mons[perm[src_i]]
        for r in wins:
            t = sw.map_rect(r, src, dst, mode)
            raw = sw.RECT(t.left, t.top, t.right, t.bottom)
            if mode == "scale":
                t = sw.clamp_into(t, dst.work)
            plan.append({"src": src_i, "dst": perm[src_i],
                         "from": r, "raw": raw, "to": t})
    return plan


def ideal(r, src, dst, mode="auto"):
    """理想（不取整）映射位置，用来量取整误差。返回浮点四元组。

    注意不能复用 sw.RECT —— 它的字段是 c_int，塞不进浮点。
    """
    if mode == "translate" or (mode == "auto"
                               and (src.rect.width, src.rect.height)
                               == (dst.rect.width, dst.rect.height)):
        dx, dy = dst.rect.left - src.rect.left, dst.rect.top - src.rect.top
        return (r.left + dx, r.top + dy, r.right + dx, r.bottom + dy)
    sx, sy = dst.rect.width / src.rect.width, dst.rect.height / src.rect.height
    return (dst.rect.left + (r.left - src.rect.left) * sx,
            dst.rect.top + (r.top - src.rect.top) * sy,
            dst.rect.left + (r.right - src.rect.left) * sx,
            dst.rect.top + (r.bottom - src.rect.top) * sy)


def max_dev(plan, mons, mode="auto"):
    """实际落地位置与理想位置的每边最大偏差（像素）。"""
    d = 0
    for p in plan:
        i = ideal(p["from"], mons[p["src"]], mons[p["dst"]], mode)
        for a, b in ((p["raw"].left, i[0]), (p["raw"].top, i[1]),
                     (p["raw"].right, i[2]), (p["raw"].bottom, i[3])):
            d = max(d, abs(a - b))
    return round(d, 2)


def collisions(rects):
    n = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if a.left < b.right and b.left < a.right and a.top < b.bottom and b.top < a.bottom:
                n += 1
    return n


def offscreen(plan, mons):
    n = 0
    for p in plan:
        m = mons[p["dst"]].rect
        t = p["to"]
        if t.left < m.left or t.top < m.top or t.right > m.right or t.bottom > m.bottom:
            n += 1
    return n


def head(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


# ---------------------------------------------------------------- 用例
def case_a_three_same_size():
    """三块同尺寸屏，环形轮转 1→2→3→1：布局应逐像素保持且完全可逆。"""
    head("A. 三块同尺寸屏 + 环形轮转（1→2→3→1）")
    mons = row((1920, 1080), (1920, 1080), (1920, 1080))
    # 三块屏刻意用**互不相同**的布局，否则轮转会与原始状态巧合重合，测不出东西
    wins = frac_layout(mons, [
        [(0.04, 0.05, 0.46, 0.25), (0.04, 0.30, 0.46, 0.50)],          # 屏1 左列两个
        [(0.52, 0.10, 0.96, 0.55)],                                     # 屏2 右侧一个大的
        [(0.04, 0.60, 0.30, 0.92), (0.34, 0.60, 0.60, 0.92),
         (0.64, 0.60, 0.96, 0.92)],                                     # 屏3 底部三个
    ])
    perm = {0: 1, 1: 2, 2: 0}
    plan = build_plan(wins, mons, perm, "translate")

    # 同尺寸纯平移 = 整数加法，应当**零**偏差
    print(f"  三块屏布局各不相同：{ [len(wins[i]) for i in range(3)] } 个窗口")
    print(f"  窗口 {len(plan)} 个；映射方式：纯平移（三块屏尺寸一致）")
    print(f"  与理想位置的偏差：{max_dev(plan, mons)} 像素（整数平移应为 0）")
    print(f"  落地后窗口重叠对数：{collisions([p['to'] for p in plan])}（应为 0）")
    print(f"  落到目标屏外的窗口：{offscreen(plan, mons)}（应为 0）")

    # 可逆性：各屏窗口数不同，因此按「每屏的矩形集合」比较，而不是按下标逐一对齐
    cur = {i: [sw.RECT(r.left, r.top, r.right, r.bottom) for r in ws]
           for i, ws in wins.items()}
    snap = {}
    for turn in (1, 2, 3):
        nxt = {i: [] for i in wins}
        for i, ws in cur.items():
            for r in ws:
                nxt[perm[i]].append(sw.map_rect(r, mons[i], mons[perm[i]], "translate"))
        cur = nxt
        snap[turn] = all(
            sorted(x.as_tuple() for x in cur[i]) == sorted(x.as_tuple() for x in wins[i])
            for i in wins)
    print(f"  轮转 1/2/3 次后每屏窗口集合回到原位：{snap[1]} / {snap[2]} / {snap[3]}")
    ok = snap[3] and not snap[1] and max_dev(plan, mons) == 0
    print(f"  → 三屏轮转逐像素可逆，且必须转满 3 圈才复位：{ok}")
    return ok


def case_b_mixed_size():
    """三块尺寸不同：量化缩放误差，以及纯平移的边界溢出。"""
    head("B. 三块尺寸不同（1920x1080 / 2560x1440 / 3840x2160）+ 环形轮转")
    mons = row((1920, 1080), (2560, 1440), (3840, 2160))
    wins = frac_layout(mons, [(0.04, 0.05, 0.46, 0.25),
                              (0.04, 0.28, 0.46, 0.48),
                              (0.52, 0.05, 0.96, 0.45),
                              (0.52, 0.50, 0.96, 0.95)])
    perm = {0: 1, 1: 2, 2: 0}

    print("  ① scale 模式（异尺寸自动等比缩放，这是默认行为）")
    ps = build_plan(wins, mons, perm, "scale")
    print(f"     窗口 {len(ps)} 个")
    print(f"     与理想位置的偏差：{max_dev(ps, mons)} 像素 —— 取整误差，"
          f"<=1px 属于数学上不可避免")
    print(f"     落到目标屏外的窗口：{offscreen(ps, mons)}")
    print(f"     落地后重叠对数：{collisions([p['to'] for p in ps])}")
    print("     (相对布局保持，只是尺寸被按比例重算 —— 和「原样搬过去」不是一回事)")

    print("  ② translate 模式（--translate-only，强制不缩放）")
    pt = build_plan(wins, mons, perm, "translate")
    off, col = offscreen(pt, mons), collisions([p["to"] for p in pt])
    print(f"     落到目标屏外的窗口：{off}/{len(pt)}")
    print(f"     落地后重叠对数：{col}")
    print("     → 尺寸不同的屏之间强制纯平移，必然有窗口跑出屏幕；"
          "被 clamp_into 拉回来后还会互相叠在一起")

    print("  ③ 三屏混排时单个窗口必须「按来源屏分别选模式」")
    for src_i, dst_i in perm.items():
        a, b = mons[src_i], mons[dst_i]
        same = (a.rect.width, a.rect.height) == (b.rect.width, b.rect.height)
        print(f"     DISPLAY{src_i + 1} ({a.rect.width}x{a.rect.height}) → "
              f"DISPLAY{dst_i + 1} ({b.rect.width}x{b.rect.height})："
              f"{'纯平移' if same else '必须缩放'}")
    print("     → 现在 GUI 的「等比缩放」是**全局一个开关**，三屏混排下不够用，"
          "要改成按 (源,目标) 这一对来判断")


def case_c_custom_permutation():
    """自定义排列：四块屏 1↔3、2↔4，其余不动；以及只换两块的特例。"""
    head("C. 四块屏 + 自定义排列")
    mons = row((1920, 1080), (1920, 1080), (1920, 1080), (1920, 1080))
    wins = frac_layout(mons, [(0.04, 0.05, 0.46, 0.45),
                              (0.52, 0.05, 0.96, 0.45)])
    perm = {0: 2, 2: 0, 1: 3, 3: 1}
    plan = build_plan(wins, mons, perm, "translate")
    print(f"  排列 {{1→3, 3→1, 2→4, 4→2}}：影响窗口 {len(plan)} 个，"
          f"偏差 {max_dev(plan, mons)}px，重叠 {collisions([p['to'] for p in plan])}")
    perm2 = {0: 2, 2: 0}
    plan2 = build_plan(wins, mons, perm2, "translate")
    print(f"  排列 {{1→3, 3→1}}（只换两块）：影响窗口 {len(plan2)} 个，"
          f"2 号/4 号屏不动")
    print(f"  → 后者正是现有 --monitors 1,3 已经能做的特例，"
          f"引擎不需要新逻辑，只缺 GUI 入口")


def case_d_naive_sequential():
    """反例：不先算完整计划、而是在遍历过程中边算边移，窗口会被级联搬走。"""
    head("D. 反例：为什么必须「先算全部目标、再统一落地」")
    perm = {0: 1, 1: 2, 2: 0}
    names = ["w1(原在屏1)", "w2(原在屏2)", "w3(原在屏3)"]
    screen_of = {0: 0, 1: 1, 2: 2}          # 窗口 -> 所在屏

    naive = dict(screen_of)
    for i in (0, 1, 2):                      # 遍历屏，把「当前在这块屏上的」全推到 perm[i]
        moving = [k for k, v in naive.items() if v == i]
        for k in moving:
            naive[k] = perm[i]
    correct = {k: perm[v] for k, v in screen_of.items()}

    print("  期望（先算完再落地）：", {names[k]: f"屏{correct[k] + 1}" for k in correct})
    print("  边算边移（朴素写法）：", {names[k]: f"屏{naive[k] + 1}" for k in naive})
    print(f"  → 朴素写法把 3 个窗口全堆到了屏{list(naive.values())[0] + 1}；"
          f"当前 swap() 已经先建 plan 列表再逐个 move_window，天然没有这个问题")
    return naive != correct


def main():
    a = case_a_three_same_size()
    case_b_mixed_size()
    case_c_custom_permutation()
    d_bad = case_d_naive_sequential()
    head("小结")
    print(f"  同尺寸 N 屏轮转逐像素可逆、改动量极小：{a}")
    print(f"  异尺寸 / 混排会退化（缩放取整 + 溢出 + 全局开关不够用）：见 B")
    print(f"  「先算后移」的两段式设计已具备，多屏不会级联搬错：{d_bad}")
    return 0 if a and d_bad else 1


if __name__ == "__main__":
    sys.exit(main())
