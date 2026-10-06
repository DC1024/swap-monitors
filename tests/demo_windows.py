#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在每块屏上摆放几个「假窗口」，用来拍演示图 / 宣传图。

为什么要单独造窗口：真实截图会带上你自己窗口的标题（项目名、文件名、聊天对象…），
不适合放进公开仓库或宣传页。这里全部用通用的示例标题，拍出来的图可以随便公开。

用法:
    python tests/demo_windows.py            # 摆好并保持（Ctrl+C 结束）
    python tests/demo_windows.py --list     # 只打印将要创建的窗口，不真的创建
    python tests/demo_windows.py --maximize # 每屏一个、且都最大化（供最大化专项测试用）

提示：这些窗口是普通 tkinter 窗口，和本工具是两个进程，所以会被正常识别为待互换窗口。

⚠️ 别在调用方进程里造完窗口再同步调 swap_windows.py：调用方会阻塞在 subprocess 上，
Tcl 事件循环停转，Windows 会一直等这个不响应的窗口回消息（SetWindowPos /
SetWindowPlacement 都是跨进程同步调用）→ 互换卡死。窗口必须活在**独立进程**里。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import swap_windows as sw  # noqa: E402

# (标题, 屏序号(1 起), 相对该屏左上角的 x, y, 宽, 高)
#
# 标题一律用通用示例，避免泄露真实信息。
# 所有标题统一带「示例」前缀，这样截图时可以只填 8 个字符的正则 "^(?!示例)"
# 把其它真实窗口全部排除掉，拍出来的图可以直接公开。
MARK = "示例"
LAYOUT = [
    (f"{MARK} · 季度报表 - Excel", 1, 120, 90, 1000, 660),
    (f"{MARK} · 会议纪要 - 编辑器", 1, 1220, 210, 880, 600),
    (f"{MARK} · 产品主页 - 浏览器", 2, 160, 110, 1120, 760),
    (f"{MARK} · Windows PowerShell", 2, 1400, 260, 880, 540),
]
# 最大化版：每屏恰好一个，最大化后各占满一块屏
LAYOUT_MAX = [
    (f"{MARK} · 最大化A", 1, 400, 300, 600, 400),
    (f"{MARK} · 最大化B", 2, 400, 300, 600, 400),
]


def build(specs, maximize=False):
    import tkinter as tk

    roots = []
    for i, (title, mon_no, rx, ry, w, h) in enumerate(specs):
        win = tk.Tk() if i == 0 else tk.Toplevel()
        win.title(title)
        win.configure(bg="#ffffff")
        # Tk 的 geometry 里 "+-2560" 表示「x 绝对定位为 -2560」，负坐标屏也能用
        win.geometry(f"{w}x{h}+{rx}+{ry}")
        tk.Label(win, text=title, bg="#ffffff", fg="#334155",
                 font=("Microsoft YaHei UI", 15)).pack(pady=(36, 6))
        tk.Label(win, text="（演示用窗口，标题为通用示例）", bg="#ffffff", fg="#94a3b8",
                 font=("Microsoft YaHei UI", 10)).pack()
        win.update()
        if maximize:
            win.state("zoomed")      # 先落到目标屏再最大化，就会贴满那一块屏
            win.update()
        roots.append(win)
    return roots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只打印，不创建窗口")
    ap.add_argument("--maximize", action="store_true",
                    help="改成每屏一个窗口并最大化（标题为「示例 · 最大化A/B」）")
    args = ap.parse_args()

    mons = sw.enumerate_monitors()
    if len(mons) < 2:
        print("需要两块显示器；本机只检测到", len(mons), "块")
        return 1

    layout = LAYOUT_MAX if args.maximize else LAYOUT
    specs = []
    for title, mon_no, rx, ry, w, h in layout:
        if mon_no > len(mons):
            continue
        m = mons[mon_no - 1]
        # 夹一下，别超过屏幕右下角
        rx = min(rx, max(0, m.rect.width - w - 20))
        ry = min(ry, max(0, m.rect.height - h - 60))
        specs.append((title, mon_no, m.rect.left + rx, m.rect.top + ry, w, h))

    print(f"检测到 {len(mons)} 块屏：")
    for i, m in enumerate(mons, 1):
        print(f"  屏{i}  {m.label}  {m.rect.width}x{m.rect.height} @ ({m.rect.left},{m.rect.top})"
              f"{'  主屏' if m.primary else ''}")
    print("\n将创建：")
    for title, mon_no, x, y, w, h in specs:
        print(f"  屏{mon_no}  ({x},{y}) {w}x{h}   {title}")

    if args.list:
        return 0

    roots = build(specs, maximize=args.maximize)
    print("\n窗口已就位。截图完成后按 Ctrl+C 结束本进程。")
    try:
        roots[0].mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
