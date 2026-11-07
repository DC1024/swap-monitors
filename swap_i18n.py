#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""swap_i18n.py —— 界面语言（中文 / English）的开关和词条表。

这一层刻意做得很薄：**只认字符串**，不碰控件、不碰布局、不读配置文件。
配置从哪来、什么时候切，由调用方（swap_gui）决定。这样做的原因很实际 ——
布局相关的判断（按钮宽度、窗口高度）全在 swap_gui 里，把语言和布局搅在
一起，改文案就会动到尺寸。

三条设计约定
------------
1. **中文原文就是 key。** `T("录制")` 在中文下原样返回，在英文下查表。
   查不到也原样返回 —— 漏翻的后果是「这一句还是中文」，不是崩溃，
   更不是把 `录制` 这种 key 名怼到界面上。
2. **默认中文。** 老版本的配置里没有 lang 这一项，读出来就是 DEFAULT_CFG
   里的 "zh"，所以从旧版升上来的界面一个字都不会变。想要英文得自己选，
   或者显式写 "auto" 交给系统判断。**不默认 auto** 的另一个原因：CI 跑在
   英文系统上，测试断言的是中文文案 —— 默认跟随系统会让「测试绿不绿」
   取决于 runner 的区域设置，那是最难查的一类红。
3. **认不出来的值一律回落中文**，不当成 auto。一个打错字的配置项不该
   把界面悄悄切成英文 —— 悄悄变的东西最难排查。
"""

import ctypes
import sys

ZH = "zh"
EN = "en"
AUTO = "auto"

LANGS = (ZH, EN)          # 真正能停留下来的两种
CHOICES = (ZH, EN, AUTO)  # 配置项允许写的三种

# ============================================================ 英译表
# 键 = 源码里那个中文原文（**一个字都不能差**），值 = 英文。
#
# 这张表必须在源码里找得到对应的原文：打错一个字，那一句就永远不会被翻译，
# 而且没有任何报错 —— 只是界面上一直是中文。所以
#   · tests/test_i18n.py 会把「源码里找不到的键」和「译文里还带中文的值」
#     一起拎出来；
#   · 加词条时直接把源码里那串复制过来，别手打。
EN_TEXT = {
    # 例： "录制": "Record",
}


# ============================================================ 系统语言检测
# GetUserDefaultUILanguage() 返回一个 LANGID（WORD）：低 10 位是主语言，
# 高位是子语言。0x04 = LANG_CHINESE，于是 0x0804 简中 / 0x0404 繁中 /
# 0x0C04 港 / 0x1004 新加坡 全部落在同一格里，一次就能判完。
LANG_CHINESE = 0x04


def lang_from_langid(langid):
    """LANGID → "zh" / "en"；取不到（None / 非数字 / 0）时返回 None。

    特意抽成不碰系统的纯函数：CI 跑在英文系统上，没法真把系统切成中文，
    但可以直接把它喂 0x0804 看是不是认成中文。检测逻辑一旦没法测，
    「auto 在某些机器上不生效」就只能靠用户报了才知道。
    """
    try:
        n = int(langid)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return ZH if (n & 0x3FF) == LANG_CHINESE else EN


def detect():
    """问 Windows「界面语言是什么」，取不到返回 None。

    只认**界面语言**（GetUserDefaultUILanguage），不认区域格式：一个在英文
    系统上把区域设成中国的人，看到英文界面是合理的 —— 他读英文没问题，
    只是喜欢那个日期格式。
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        return lang_from_langid(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:                       # noqa: BLE001 —— 取不到就回落，不闹
        return None


# ============================================================ 当前语言
_ACTIVE = ZH


def current():
    """当前生效的语言，永远是 "zh" 或 "en"。"""
    return _ACTIVE


def set_language(lang):
    """定下当前语言，返回真正生效的那个（"zh" / "en"）。

    "auto" 会去问系统；问不出来、或者值根本认不出来，都回落中文。
    可以反复调用（启动时一次、用户切换时再一次），没有副作用。
    """
    global _ACTIVE
    want = str(lang or "").strip().lower()
    if want == AUTO:
        want = detect() or ZH
    if want not in LANGS:
        want = ZH
    _ACTIVE = want
    return _ACTIVE


def T(text):
    """把界面上的中文原文换成当前语言；查不到就原样返回。

    名字就叫 T（translate 的首字母），因为一档扫下来会有上百个调用点，
    每处多写几个字符就是几百个字符的噪音。这不是缩写癖，是调用密度决定的。

    非字符串原样返回：这个函数会被顺手用在 `T(某个参数)` 上，
    随手把 None 变成 "None" 是最难查的一类 bug。
    """
    if not isinstance(text, str) or _ACTIVE == ZH:
        return text
    return EN_TEXT.get(text, text)
