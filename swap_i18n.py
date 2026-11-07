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
#
# 翻译口径（改文案时照着这个来，别一句一个风格）：
#   · 用**动词开头的短句**当按钮/菜单项：Record / Apply / Swap now / Exit
#   · 整句提示用陈述语气，不加句号，首字母大写
#   · 不翻译品牌名（多屏窗口互换 = Swap Monitors，与 exe 名一致）
#
# 带数字的句子用**命名占位符**当模板：`"屏{src}→屏{dst}"`。
# 两边的占位符名字和个数必须一一对应 —— 多一个就是 KeyError（界面退回中文），
# 少一个就是数字凭空消失，两种都不报错，所以 tests/test_i18n.py 静态比对。
EN_TEXT = {
    # ---- 窗口标题与页头 ----
    "多屏窗口互换": "Swap Monitors",
    "  把屏幕上的窗口整体互换，相对布局原样保留":
        "  Swap every window between screens, keeping the relative layout",

    # ---- 页头按钮 ----
    "以管理员身份重启": "Restart as admin",

    # ---- 排列区 ----
    "每块屏的窗口送往：": "Send each screen's windows to:",
    "环形轮转": "Rotate",
    "两两互换": "Pair up",
    "全部不动": "Keep all",
    # 模板词条（占位符由调用方传）：见 T() 的说明
    "屏{src}→屏{dst}": "Screen {src} → Screen {dst}",
    "全部不动：按热键不会有任何窗口被移动":
        "Keep all: the hotkey moves nothing",

    # ---- 窗口清单 ----
    "参与互换的窗口": "WINDOWS TO SWAP",
    "刷新": "Refresh",
    "屏幕": "Screen",
    "状态": "State",
    "位置": "Rect",
    "进程": "Process",
    "窗口标题": "Window title",
    "互换后": "Goes to",
    # 列表行与计数（模板）
    "屏{n}": "Screen {n}",
    "（管理员进程）": "(admin process)",
    "共 {count} 个窗口　·　{desc}": "{count} windows · {desc}",
    # 映射方式说明（_map_desc 的三条 + 空排列那条）
    "没有任何屏参与（全部不动）": "No screen involved (keep all)",
    "{n} 对全为纯平移": "{n} pairs, all move as-is",
    "{n} 对全为等比缩放": "{n} pairs, all scaled",
    "{n} 对中 {n_scale} 对缩放、{n_rest} 对平移":
        "{n_scale} of {n} pairs scaled, {n_rest} move as-is",

    # ---- 热键 ----
    "全局热键": "GLOBAL HOTKEY",
    "录制": "Record",
    "应用": "Apply",
    "正在注册…": "Registering…",

    # ---- 排除标题 ----
    "排除的窗口标题（正则，可留空）":
        "Excluded window titles (regex, leave empty for none)",

    # ---- 选项 ----
    "跳过最小化窗口": "Skip minimized windows",
    "分辨率不同的屏之间等比缩放（否则纯平移）":
        "Scale between screens with different resolutions (otherwise move as-is)",
    "互换后把焦点还给原来的窗口":
        "Give focus back to the original window after swapping",
    "互换完成后弹出提示（托盘气泡）":
        "Show a tray balloon when the swap finishes",
    "开机自动启动（开机后静默待在托盘）":
        "Start with Windows (stays quietly in the tray)",
    "记住管理员模式（管理员窗口也能搬，启动不再弹 UAC）":
        "Remember admin mode (move elevated windows too, no UAC prompt)",

    # ---- 主操作按钮 ----
    "立即互换": "Swap now",
    "预览计划（不改动）": "Preview (no changes)",
    "退出": "Exit",
    "恢复原状（再换一次）": "Restore (swap again)",
    "再轮转一次": "Rotate again",

    # ---- 状态栏的初始文字 ----
    "就绪": "Ready",

    # ---- 语言开关 ----
    "界面语言": "Language",
    "跟随系统": "Auto",

    # ---- 托盘 ----
    "立即互换窗口": "Swap windows now",
    "预览互换计划": "Preview swap plan",
    "显示主界面": "Show main window",
    "未设热键": "no hotkey",
    "热键": "hotkey",
    "左键双击显示、右键菜单": "double-click to show, right-click for menu",

    # ---- 标点（当分隔符用，所以也得能翻）----
    "、": ", ",
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


def T(text, **kw):
    """把界面上的中文原文换成当前语言；查不到就原样返回。

    名字就叫 T（translate 的首字母），因为一档扫下来会有上百个调用点，
    每处多写几个字符就是几百个字符的噪音。这不是缩写癖，是调用密度决定的。

    非字符串原样返回：这个函数会被顺手用在 `T(某个参数)` 上，
    随手把 None 变成 "None" 是最难查的一类 bug。

    **kw 是模板参数 —— 带数字/名字的句子要写成模板，不要用 f-string：

        T("屏{src}→屏{dst}", src=s, dst=d)      # 对
        T(f"屏{s}→屏{dst}")                     # 错：key 里已经是渲染后的值

    第二种写法看着更短，但 key 会变成「屏1→屏2」「屏2→屏3」……每种组合
    都是一条新词条，表里永远填不完。模板写法则只有一条 key。
    中文下也要走 format（否则界面上直接看到 `屏{src}`），所以别省这两个字符。
    """
    if not isinstance(text, str):
        return text
    out = text if _ACTIVE == ZH else EN_TEXT.get(text, text)
    if not kw:
        return out
    try:
        return out.format(**kw)
    except (KeyError, IndexError, ValueError):
        # 译文里的占位符和原文对不上（多写、少写、拼错）。守卫测试能静态抓到
        # 这种错，但运行时也得有个台阶：退回中文原文再格式化一次，
        # 让用户看到一句完整的中文，而不是 `Screen {src} → Screen `。
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
