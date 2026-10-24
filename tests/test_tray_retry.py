#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托盘图标重试逻辑的回归测试。

背景（v1.1.7 的 bug）：_add_icon_with_retry 每一轮都调 _add_icon()，而
Shell_NotifyIconW(NIM_ADD) 对同一个 (hWnd, uID) **只能成功一次** —— 第二次
会返回 False（图标已经在了，实测确认）。

于是「加进去了但还没画出来（查不到矩形）」这条路径上：

    第 1 轮：NIM_ADD 成功，查不到矩形 → 继续
    第 2 轮：NIM_ADD 返回 False → 当成失败 → self.nid = None
    第 3-6 轮：同样

最终 _add_icon_with_retry 返回 False → 界面报「托盘图标创建失败（热键仍可用）」，
可图标其实好好地在那儿；而且 self.nid 被清成 None，之后 _refresh_tip /
_balloon / 退出时摘图标全部静默失效。

测试用假托盘线程（只替换 _add_icon / _icon_rect 两个最底层动作），
所以不需要真的 explorer，也不会在 CI 上碰到托盘相关的不确定性。

用法: python tests/test_tray_retry.py
"""
import os
import queue
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeTray(G.TrayThread):
    """只替换「加图标」和「查矩形」两个动作，其余逻辑全用真的。"""

    def __init__(self, add_results, rect_results):
        self.hwnd = 1
        self.results = queue.Queue()
        self.nid = None
        self._spec = None
        self._shown_hk = ""
        self.add_calls = 0
        self.rect_calls = 0
        self._add_results = list(add_results)
        self._rect_results = list(rect_results)

    def _add_icon(self):
        self.add_calls += 1
        # 真实实现是先 self.nid = nid 再调 shell，成功与否都会先设上
        self.nid = object()
        return self._add_results.pop(0) if self._add_results else False

    def _icon_rect(self):
        self.rect_calls += 1
        return self._rect_results.pop(0) if self._rect_results else None


print("== 1) 加成功、但矩形要等两轮才查得到 ==")
t = FakeTray([True], [None, None, (0, 0, 10, 10)])
ok = t._add_icon_with_retry(tries=6, delay=0)
check("最终判定为成功", ok is True)
check("NIM_ADD 只调了一次（老代码这里会调 3 次，第 2 次起全是假失败）",
      t.add_calls == 1, f"实际 {t.add_calls} 次")
check("矩形是轮询出来的（没有靠重复 add）", t.rect_calls == 3, f"实际 {t.rect_calls} 次")

print("\n== 2) 一开始 add 失败，后面成功 ==")
t = FakeTray([False, False, True], [None, None, None, (0, 0, 10, 10)])
ok = t._add_icon_with_retry(tries=6, delay=0)
check("最终判定为成功", ok is True)
check("失败后确实重试了 add", t.add_calls == 3, f"实际 {t.add_calls} 次")

print("\n== 3) add 一直失败 ==")
t = FakeTray([False] * 6, [None] * 6)
ok = t._add_icon_with_retry(tries=3, delay=0)
check("判定为失败", ok is False)
check("试满 tries 次", t.add_calls == 3, f"实际 {t.add_calls} 次")
check("失败时 nid 被清掉（避免拿脏句柄去 NIM_MODIFY）", t.nid is None, str(t.nid))

print("\n== 4) 加成功但始终查不到矩形（Win11 收进溢出区）==")
t = FakeTray([True], [None] * 6)
ok = t._add_icon_with_retry(tries=3, delay=0)
check("不报失败（图标确实在，只是看不见）", ok is True)
check("add 仍然只调一次", t.add_calls == 1, f"实际 {t.add_calls} 次")
check("nid 必须留着，否则之后刷新提示/弹气泡/退出摘图标全部失效",
      t.nid is not None, str(t.nid))

print("\n== 5) 第一次就成功且矩形立刻可查 ==")
t = FakeTray([True], [(0, 0, 10, 10)])
ok = t._add_icon_with_retry(tries=6, delay=0)
check("判定为成功", ok is True)
check("不浪费轮询", t.add_calls == 1 and t.rect_calls == 1,
      f"add={t.add_calls} rect={t.rect_calls}")

print()
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("全部通过 ✅ —— 托盘重试不会重复 NIM_ADD，也不会误报失败")
