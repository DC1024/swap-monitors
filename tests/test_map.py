#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「映射逻辑与分辨率无关」：用合成显示器矩形跑各种分辨率/排布组合。"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import swap_windows as sw  # noqa: E402


def mk(name, left, top, w, h, primary=False):
    m = types.SimpleNamespace()
    r = sw.RECT(left, top, left + w, top + h)
    m.device, m.rect, m.work, m.primary = name, r, r, primary
    return m


def show(title, a, b, win_rel, mode="auto", clamp=False):
    """win_rel: 相对 A 屏的 (left, top, w, h)"""
    A, B = mk("A", *a), mk("B", *b)
    wr = sw.RECT(win_rel[0], win_rel[1], win_rel[0] + win_rel[2], win_rel[1] + win_rel[3])
    out = sw.map_rect(wr, A, B, mode)
    if clamp:
        out = sw.clamp_into(out, B.work)
    # 归一化：在目标屏里的相对位置（千分比），用来判断"布局是否原样搬过去"
    rel_x = (wr.left - A.rect.left) / A.rect.width
    rel_y = (wr.top - A.rect.top) / A.rect.height
    got_x = (out.left - B.rect.left) / B.rect.width
    got_y = (out.top - B.rect.top) / B.rect.height
    ok = abs(rel_x - got_x) < 0.001 and abs(rel_y - got_y) < 0.001
    print(f"{title}")
    print(f"   A={A.rect.width}x{A.rect.height}{'  @'+str(A.rect.left) if A.rect.left else ''}"
          f"   B={B.rect.width}x{B.rect.height}{'  @'+str(B.rect.left) if B.rect.left else ''}"
          f"   模式={mode}")
    print(f"   窗口在 A 的相对位置 (左{rel_x:.1%}, 上{rel_y:.1%}) "
          f"→ 落在 B 的 (左{got_x:.1%}, 上{got_y:.1%})   {'✅ 相对布局保持' if ok else '❌ 偏移'}")
    return ok


def main():
    print("=== 1. 同分辨率（你现在的配置：两块 2560x1440，左右并排）===")
    ok1 = show("1920x1080 也照样成立", (0, 0, 1920, 1080), (-1920, 0, 1920, 1080),
               (300, 100, 900, 700))
    ok2 = show("1440p / 4K / 带鱼屏 都不例外（只换数字）",
               (0, 0, 3840, 2160), (-3840, 0, 3840, 2160), (500, 200, 1600, 1200))
    ok3 = show("竖排（上下）也一样", (0, 0, 2560, 1440), (0, 1440, 2560, 1440),
               (100, 200, 800, 600))

    print()
    print("=== 2. 两屏分辨率不同（自动切等比缩放 + 夹进工作区）===")
    A = mk("A", 0, 0, 1920, 1080)
    B = mk("B", 1920, 0, 3840, 2160)
    wr = sw.RECT(960, 0, 1920, 1080)          # A 屏右半边
    got = sw.map_rect(wr, A, B, "scale")
    exp = sw.RECT(1920 + 1920, 0, 1920 + 3840, 2160)
    print(f"   A=1920x1080 的右半边 {wr!s}\n"
          f"   → B=3840x2160 的右半边 {got!s}\n"
          f"   期望 {exp!s}  {'✅ 一致' if got.as_tuple() == exp.as_tuple() else '❌'}")
    print(f"   尺寸同步放大：{wr.width}x{wr.height} → {got.width}x{got.height} "
          f"（×{got.width / wr.width:.1f}）")
    ok4 = got.as_tuple() == exp.as_tuple()

    print()
    print("=== 3. 纯平移 vs 缩放的自动判定 ===")
    a, b = mk("A", 0, 0, 2560, 1440), mk("B", -2560, 0, 2560, 1440)
    print(f"   等尺寸 → map_rect 默认 auto 走平移: "
          f"{sw.map_rect(sw.RECT(10, 20, 100, 120), a, b, 'auto')!s}")
    c = mk("C", 0, 0, 1920, 1080)
    print(f"   异尺寸 → auto 走缩放:            "
          f"{sw.map_rect(sw.RECT(10, 20, 100, 120), a, c, 'auto')!s}")

    print()
    print("=== 4. 可逆性：换过去再换回来 ===")
    back = sw.map_rect(got, B, A, "scale")
    print(f"   {wr!s} → 换到 B → 换回 A = {back!s}  "
          f"{'✅ 逐像素还原' if back.as_tuple() == wr.as_tuple() else '❌ 有误差'}")
    ok5 = back.as_tuple() == wr.as_tuple()

    allok = all([ok1, ok2, ok3, ok4, ok5])
    print()
    print("结论：" + ("全部通过 —— 映射逻辑与分辨率、尺寸是否相同、屏幕怎么摆都无关"
                  if allok else "有失败项"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
