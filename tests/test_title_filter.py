#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标题过滤（--exclude-title / --only-title）的匹配语义回归测试。

背景（v1.1.7）：两条过滤路径用的不是同一种匹配方式 ——
  * 排除：`skip_title.match(title)`  → 只从**标题开头**匹配
  * 只处理：`only_title.search(title)` → 从**任意位置**匹配
于是 `--exclude-title Steam` 只排掉「Steam 开头的窗口」，标题叫
「打开 Steam 商店」的照样被搬走；README 只好教用户写成 `.*Steam.*` 绕过去。
界面上那个「排除的窗口标题」输入框也是这条路径，写 Steam 一样不管用。

现在的约定：两条路径都走 `_title_hits()`，统一 search（含即可），并且统一
strip。默认的 DEFAULT_SKIP_TITLE 自带 `^...$`，锚定语义不受影响。

特别保留的一条：排除路径的 `title and` 守卫**不能删** —— 用户把排除正则写成
`.*` 时，无标题窗口（Electron 主窗）不该被一起排掉，否则它们会从列表里消失。

用法: python tests/test_title_filter.py
"""
import inspect
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 隔离用户配置（import swap_gui 时会读它）
TMP = tempfile.mkdtemp(prefix="swapmon-title-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False}, f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_windows as SW      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def combined(user_pat):
    """按 swap() 里的写法把「默认排除 + 用户正则」拼成一个模式。"""
    return re.compile(f"({SW.DEFAULT_SKIP_TITLE.pattern})|({user_pat})", re.I)


def main():
    print("== 1) 前提：旧写法 match() 确实漏掉中间命中 ==")
    check("re.match('Steam') 匹配不到「打开 Steam 商店」",
          re.compile("Steam").match("打开 Steam 商店") is None)
    check("re.search('Steam') 能匹配到", re.compile("Steam").search("打开 Steam 商店"))

    print("\n== 2) _title_hits 的语义 ==")
    hits = getattr(SW, "_title_hits", None)
    if not callable(hits):
        check("swap_windows 提供了 _title_hits() 这个统一入口", False, "没有这个方法")
        print("  （后面几节都依赖它，跳过）")
    else:
        steam = combined("Steam")
        check("标题中间命中：打开 Steam 商店", hits(steam, "打开 Steam 商店") is True)
        check("标题开头命中：Steam 商店", hits(steam, "Steam 商店") is True)
        check("大小写不敏感：STEAM", hits(steam, "STEAM 商店") is True)
        check("不相关标题不命中：记事本", hits(steam, "记事本 - 无标题") is False)
        check("前导空格不影响（两条路径都 strip）",
              hits(steam, "   打开 Steam 商店") is True)

        # 默认那一条自带 ^...$，用 search 之后仍然只匹配整串
        check("默认项仍然匹配整串：Program Manager",
              hits(SW.DEFAULT_SKIP_TITLE, "Program Manager") is True)
        check("默认项不会变成「含即可」：Program Manager 2",
              hits(SW.DEFAULT_SKIP_TITLE, "Program Manager 2") is False)
        check("默认项不会变成「含即可」：我的 Program Manager",
              hits(SW.DEFAULT_SKIP_TITLE, "我的 Program Manager") is False)
        check("默认项在组合模式里也保持锚定",
              hits(combined("Steam"), "我的 Program Manager") is False)

        # 用户自己写 ^ 时仍然锚定开头
        check("用户写 ^Steam 仍然只匹配开头",
              hits(combined("^Steam"), "打开 Steam 商店") is False)
        check("用户写 ^Steam 能匹配开头",
              hits(combined("^Steam"), "Steam 商店") is True)

        # --only-title 是同一个判定：写 Chrome 能匹配「Google Chrome」
        check("--only-title 写 Chrome 能匹配「Google Chrome」",
              hits(re.compile("Chrome", re.I), "Google Chrome") is True)

        # 空标题：helper 自己不拦（拦在排除路径的调用点上），见第 3 节
        check("空标题配 .* 仍然命中（保持只处理路径的老行为）",
              hits(re.compile(".*"), "") is True)

    print("\n== 3) 枚举里两条路径都走这个入口 ==")
    try:
        src = inspect.getsource(SW.enumerate_windows)
    except Exception as e:                                   # noqa: BLE001
        src = ""
        check("能读到 enumerate_windows 的源码", False, str(e))
    if src:
        check("排除路径走 _title_hits", "_title_hits(skip_title" in src)
        check("只处理路径走 _title_hits", "_title_hits(only_title" in src)
        check("不再有 skip_title.match(", "skip_title.match" not in src)
        check("排除路径保留了「标题非空」守卫（否则 .* 会排掉无标题窗口）",
              "title and _title_hits(skip_title" in src)

    print()
    if failures:
        print(f"❌ {len(failures)} 项未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("全部通过 ✅ —— 排除和只处理都是「标题里含即命中」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
