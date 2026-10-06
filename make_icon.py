#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 app_icon.ico：圆角蓝底 + 两块屏幕 + 双向箭头（纯代码绘制，无版权问题）。"""
import os
import sys

from PIL import Image, ImageDraw

S = 1024          # 4 倍超采样画布
R = 4             # 超采样倍数
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
PREVIEW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon_preview.png")


def lerp(c1, c2, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底 + 竖向渐变
    radius = int(S * 0.21)
    grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    top, bot = (58, 142, 245), (18, 74, 186)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bot, y / (S - 1)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # 左上角一点高光，显得不那么平
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-S * 0.35, -S * 0.5, S * 0.75, S * 0.35],
                                 fill=(255, 255, 255, 26))
    img = Image.alpha_composite(img, Image.composite(glow, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
    d = ImageDraw.Draw(img)

    W = (255, 255, 255, 255)
    W2 = (255, 255, 255, 210)

    # 两块屏幕
    def screen(x0, y0, x1, y1):
        pad = int(S * 0.028)
        d.rounded_rectangle([x0, y0, x1, y1], radius=int(S * 0.035),
                            outline=W, width=int(S * 0.030))
        # 屏幕里的两条"内容线"
        lx = x0 + pad * 2
        for k in range(2):
            ly = y0 + pad * 3 + k * int(S * 0.055)
            d.rounded_rectangle([lx, ly, lx + (x1 - x0) * (0.62 - 0.2 * k),
                                 ly + int(S * 0.022)], radius=int(S * 0.011), fill=W2)
        # 支架
        cx = (x0 + x1) / 2
        d.rectangle([cx - int(S * 0.055), y1, cx + int(S * 0.055),
                     y1 + int(S * 0.05)], fill=W)
        d.rounded_rectangle([cx - int(S * 0.10), y1 + int(S * 0.05) - int(S * 0.008),
                             cx + int(S * 0.10), y1 + int(S * 0.075)],
                            radius=int(S * 0.012), fill=W)

    bw, bh = int(S * 0.30), int(S * 0.335)
    top_y = int(S * 0.215)
    screen(int(S * 0.075), top_y, int(S * 0.075) + bw, top_y + bh)
    screen(int(S * 0.925) - bw, top_y, int(S * 0.925), top_y + bh)

    # 中间双向箭头
    cy = top_y + bh // 2
    ax0, ax1 = int(S * 0.075) + bw + int(S * 0.02), int(S * 0.925) - bw - int(S * 0.02)
    bar = int(S * 0.017)
    head_w, head_h = int(S * 0.045), int(S * 0.052)
    d.rectangle([ax0, cy - bar, ax1, cy + bar], fill=W)
    d.polygon([(ax0, cy), (ax0 + head_w, cy - head_h), (ax0 + head_w, cy + head_h)], fill=W)
    d.polygon([(ax1, cy), (ax1 - head_w, cy - head_h), (ax1 - head_w, cy + head_h)], fill=W)

    img = img.resize((256, 256), Image.LANCZOS)
    os.makedirs(os.path.dirname(PREVIEW), exist_ok=True)
    img.save(PREVIEW)
    img.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                       (64, 64), (128, 128), (256, 256)])
    print("已生成:", OUT, os.path.getsize(OUT), "字节")
    print("预览:", PREVIEW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
