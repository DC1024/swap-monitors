#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面语言（i18n）机制自检。

管的是「语言怎么切」，不是「翻了哪些句子」—— 后者靠人眼看界面，测不出来。
这里守的是那些**出错了却一声不响**的地方：

  · T() 查表 / 回落 / 非字符串原样返回
  · set_language 把各种写法收敛成 zh / en（脏值不许把界面切成英文）
  · LANGID → 语言。CI 是英文系统，只能喂值来测检测逻辑
  · 英译表本身：打错一个字的键 = 那一句永远不翻，且没有任何报错
  · lang 配置项：默认中文、脏值不崩、App 真的是在建界面之前读的它

不创建窗口、不读用户真实配置（SWAPMONITORS_CONFIG 注入临时文件）。

用法: python tests/test_i18n.py
"""
import json
import os
import re
import string
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 隔离用户配置：必须在 import swap_gui 之前设好，它在 import 时就把 CFG_PATH 定死了
TMP = tempfile.mkdtemp(prefix="swapmon-i18n-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({}, f)          # 故意留空：后面要验证「老配置没有 lang 这一项」
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G       # noqa: E402
import swap_i18n as I      # noqa: E402

GUI_SRC = open(os.path.join(ROOT, "swap_gui.py"), encoding="utf-8").read()
WIN_SRC = open(os.path.join(ROOT, "swap_windows.py"), encoding="utf-8").read()

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    print()
    print(f"== {t} ==")


# ============================================================
section("① T()：查表、回落、不碰非字符串")

# 真实的表现在还是空的（一档扫文案时才会填满），所以这里装一张假表来测机制。
# 用假表而不是等真表，是因为「机制通不通」和「翻了几个词」是两件事：
# 前者坏了是逻辑错，后者大不了界面还是中文。
_REAL_TABLE = I.EN_TEXT
I.EN_TEXT = {"录制": "Record", "应用": "Apply"}
try:
    I.set_language("zh")
    check("中文下原样返回", I.T("录制") == "录制")
    check("中文下压根不查表（表里明明有也不换）", I.T("应用") == "应用")

    I.set_language("en")
    check("英文下查表", I.T("录制") == "Record")
    check("英文下查不到就回落中文原文（不是崩、也不是显示 key 名）",
          I.T("从来没翻过的句子") == "从来没翻过的句子", I.T("从来没翻过的句子"))
    check("空串照样能过", I.T("") == "")
    check("None 原样返回（不会被顺手变成 'None'）", I.T(None) is None, repr(I.T(None)))
    check("非字符串（数字）原样返回", I.T(3) == 3, repr(I.T(3)))
    check("非字符串（对象）原样返回", I.T(I) is I)
finally:
    I.EN_TEXT = _REAL_TABLE
    I.set_language("zh")

check("假表已经撤掉（别污染后面的检查）", I.EN_TEXT is _REAL_TABLE)

# ============================================================
section("①b T() 的模板参数（带数字的句子不许写成 f-string）")
# 为什么必须有模板：`T(f"屏{s}→屏{d}")` 的 key 是渲染后的「屏1→屏2」，
# 每种屏号组合都是一条新词条，表永远填不满。写成 `T("屏{src}→屏{dst}", src=s)`
# 才只有一条。这里验的是**格式化这条路通不通**，真表里的模板由 ④ 的守卫盯着。
I.EN_TEXT = {"屏{src}→屏{dst}": "Screen {src} → Screen {dst}",
             "共 {n} 个窗口": "Total {count} windows"}   # ← 占位符故意写错
try:
    I.set_language("zh")
    check("中文下也要格式化（否则界面上直接看到 `屏{src}`）",
          I.T("屏{src}→屏{dst}", src=1, dst=2) == "屏1→屏2",
          I.T("屏{src}→屏{dst}", src=1, dst=2))
    check("没传参数时一个花括号都不碰",
          I.T("带{花括号}的句子") == "带{花括号}的句子")

    I.set_language("en")
    check("英文下查表 + 格式化",
          I.T("屏{src}→屏{dst}", src=1, dst=2) == "Screen 1 → Screen 2",
          I.T("屏{src}→屏{dst}", src=1, dst=2))
    check("表里没有这条时，用中文模板格式化",
          I.T("送往 {name}", name="屏3") == "送往 屏3",
          I.T("送往 {name}", name="屏3"))
    check("译文占位符对不上 → 退回中文模板，照样给出完整句子",
          I.T("共 {n} 个窗口", n=3) == "共 3 个窗口",
          I.T("共 {n} 个窗口", n=3))
    check("连原文都缺参数 → 原样返回，不抛异常",
          I.T("共 {n} 个窗口", m=3) == "共 {n} 个窗口",
          I.T("共 {n} 个窗口", m=3))
    check("参数值里带花括号不会被二次展开",
          I.T("送往 {name}", name="{怪}") == "送往 {怪}",
          I.T("送往 {name}", name="{怪}"))
    check("非字符串照样原样返回（带着参数也不变）", I.T(None, n=1) is None)
finally:
    I.EN_TEXT = _REAL_TABLE
    I.set_language("zh")

check("①b 的假表也撤掉了", I.EN_TEXT is _REAL_TABLE)

# ============================================================
section("② set_language：各种写法都要收敛对")

for _raw, _want in (("zh", "zh"), ("EN", "en"), ("en", "en"), ("  en  ", "en"),
                    ("En", "en"), ("ZH", "zh"),
                    ("", "zh"), (None, "zh"),
                    ("fr", "zh"), ("english", "zh"), ("e n", "zh")):
    check(f"set_language({_raw!r}) → {_want}", I.set_language(_raw) == _want,
          repr(I.set_language(_raw)))

# auto 跟着机器走，结果取决于跑测试的那台机器 —— 只能断言「不超出 zh/en，
# 且等于它自己算出来的那个值」
_auto = I.set_language("auto")
check("auto 的结果只会是 zh / en", _auto in I.LANGS, _auto)
check("auto 等价于 detect() 或者说中文", _auto == (I.detect() or "zh"), _auto)
check("current() 和 set_language 的返回值一致", I.current() == _auto, I.current())
I.set_language("zh")

check("CHOICES 就是配置项允许的三种写法",
      set(I.CHOICES) == {I.ZH, I.EN, I.AUTO}, str(I.CHOICES))

# ============================================================
section("③ LANGID → 语言（CI 是英文系统，只能喂值测）")

for _id, _want in ((0x0804, "zh"),     # 简体中文
                   (0x0404, "zh"),     # 繁体中文（台湾）
                   (0x0C04, "zh"),     # 繁体中文（香港）
                   (0x1004, "zh"),     # 简体中文（新加坡）
                   (0x0409, "en"),     # 英文（美国）
                   (0x0809, "en"),     # 英文（英国）
                   (0x0411, "en"),     # 日文 —— 不认识的当英文，别当没设置
                   (0x0407, "en")):    # 德文
    check(f"LANGID 0x{_id:04X} → {_want}", I.lang_from_langid(_id) == _want,
          repr(I.lang_from_langid(_id)))

for _bad in (None, 0, -1, "", "中文", "0x0804", [], 0.5):
    check(f"取不到的 LANGID（{_bad!r}）返回 None —— 由调用方决定回落谁",
          I.lang_from_langid(_bad) is None, repr(I.lang_from_langid(_bad)))

# ============================================================
section("④ 英译表守卫（打错字的键，效果是「永远不翻」且不报错）")

# CJK + 全角标点 + 中日韩扩展区的合集。
# 含全角标点是有意的：从中文原文复制过来时，最典型的漏改就是
# 把「（可选）」这类标点整块留下。
CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff"
                 r"\uf900-\ufaff\uff00-\uffef]")


def stray_keys(table, src):
    """表里「源码中根本没出现过」的键 —— 一个字打错就等于这一句白翻。"""
    return sorted(k for k in table if k not in src)


def untranslated(table):
    """译文里还带中文/全角字符，或者跟原文一模一样的词条。"""
    bad = []
    for k, v in table.items():
        v = v if isinstance(v, str) else ""
        if not v.strip():
            bad.append((k, v or "（空）"))
        elif CJK.search(v) or v.strip() == k.strip():
            bad.append((k, v))
    return bad


def fields(s):
    """模板里的占位符名字集合；`{` 没配对（根本写不成模板）时返回 None。"""
    try:
        return {name for _lit, name, _spec, _conv in string.Formatter().parse(s)
                if name is not None}
    except ValueError:
        return None


def bad_placeholders(table):
    """原文与译文的占位符对不上的词条。

    多一个 → 跑到那句就是 KeyError（T() 里兜住了，界面退回中文原文）；
    少一个 → 数字凭空消失，用户看到 `Screen  → Screen `。
    两种都**不会报错**，只能静态比对，所以这条守卫不能省。
    """
    bad = []
    for k, v in table.items():
        if "{" not in k and "{" not in (v if isinstance(v, str) else ""):
            continue
        fk, fv = fields(k), fields(v if isinstance(v, str) else "")
        if fk is None or fv is None or fk != fv:
            bad.append((k, fk, fv))
    return bad


_SRC_ALL = GUI_SRC + WIN_SRC

_stray = stray_keys(I.EN_TEXT, _SRC_ALL)
check(f"表里 {len(I.EN_TEXT)} 个键都能在源码里找到原文", not _stray,
      "找不到: " + ", ".join(_stray[:5]))

_untr = untranslated(I.EN_TEXT)
check("没有「译文还是中文 / 译文是空 / 译文跟原文一样」的词条", not _untr,
      "; ".join(f"{k} → {v}" for k, v in _untr[:5]))

_N_TPL = sum(1 for k in I.EN_TEXT if "{" in k)
_bp = bad_placeholders(I.EN_TEXT)
check(f"模板词条（共 {_N_TPL} 条）两边的占位符一一对应", not _bp,
      "; ".join(f"{k}: 原文 {a} / 译文 {b}" for k, a, b in _bp[:3]))

# 反向验证：表还是空的时候上面两条必然绿，所以必须证明检测器真能抓到问题，
# 不然它就是一条永远绿、永远不管事的假守卫。
_FAKE = {
    "录制": "Record",                 # 正常
    "这段源码里根本没有": "Nope",      # 打错字 / 已经删掉的旧文案
    "应用": "应用（App）",             # 漏翻半句
    "退出": "退出",                    # 压根没翻
    "刷新": "",                        # 填了但空的
}
check("反向：打错字的键被拎出来",
      stray_keys(_FAKE, _SRC_ALL) == ["这段源码里根本没有"],
      str(stray_keys(_FAKE, _SRC_ALL)))
check("反向：漏翻 / 没翻 / 填空的译文被拎出来（3 条）",
      sorted(k for k, _v in untranslated(_FAKE)) == sorted(["应用", "退出", "刷新"]),
      str(sorted(k for k, _v in untranslated(_FAKE))))
check("反向：正常的那条不被误伤", "录制" not in [k for k, _v in untranslated(_FAKE)])

_FAKE_TPL = {
    "共 {n} 个窗口": "Total {count} windows",          # 名字对不上
    "屏{src}→屏{dst}": "Screen {src}",                 # 少一个占位符
    "第 {i} 次": "Attempt {i} of {total}",             # 多一个占位符
    "已用 {n}/{all}": "Used {n} of {all}",             # 正常
    "一共 {0} 个": "Total {0}",                         # 位置参数（能用，但别这么写）
}
_bp_fake = [k for k, _a, _b in bad_placeholders(_FAKE_TPL)]
check("反向：占位符名字不一致 / 少一个 / 多一个都被拎出来（3 条）",
      sorted(_bp_fake) == sorted(["共 {n} 个窗口", "屏{src}→屏{dst}", "第 {i} 次"]),
      str(sorted(_bp_fake)))
check("反向：占位符对得上的模板不被误伤",
      not set(_bp_fake) & {"已用 {n}/{all}", "一共 {0} 个"}, str(_bp_fake))

# 纯英文的译文不该被 CJK 规则碰到（不然这条守卫会把正确的翻译判成错）
check("纯英文译文不触发 CJK 规则",
      not CJK.search("Restore original layout (optional)"))

# ------------------------------------------------------------
# ④b  T() 的第一个参数不许是 f-string
# 这是模板机制真正的入口守卫：`T(f"共 {n} 个窗口")` 语法上合法、中文下看着也对，
# 但 key 是「共 3 个窗口」「共 12 个窗口」……每种取值都是一条新词条，
# 表里永远填不满，而英文界面下这些句子**永远是中文**，还查不出原因。
import ast                                                  # noqa: E402


def fstring_keys(path):
    """返回 [(行号, 中文片段)]：T() 拿 f-string 当 key 的调用点。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = (fn.id if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute) else "")
        if name != "T":
            continue
        first = node.args[0]
        if isinstance(first, ast.JoinedStr):
            flat = "".join(p.value if isinstance(p, ast.Constant) else ""
                           for p in first.values)
            if CJK.search(flat):
                bad.append((node.lineno, flat[:40]))
    return bad


for _p in ("swap_gui.py", "swap_windows.py"):
    _bad = fstring_keys(os.path.join(ROOT, _p))
    check(f"{_p} 里没有 T(f\"…\") 这种写法（要用模板参数）", not _bad, str(_bad))

# 反向验证：换成一个真这么写的临时文件，必须点名报出来
_fpath = os.path.join(TMP, "bad_fstring_key.py")
with open(_fpath, "w", encoding="utf-8") as f:
    f.write("def g(n):\n"
            "    return T(f\"共 {n} 个窗口\")\n")
check("反向：T(f\"共 {n} 个窗口\") 会被点名",
      [ln for ln, _t in fstring_keys(_fpath)] == [2], str(fstring_keys(_fpath)))

# 顺带把「正常写法不被误伤」也钉住（条件表达式当参数是合法用法）
_okpath = os.path.join(TMP, "ok_key.py")
with open(_okpath, "w", encoding="utf-8") as f:
    f.write("def g(flag):\n"
            "    return T(\"恢复原状（再换一次）\" if flag else \"再轮转一次\")\n")
check("反向：条件表达式当参数不被误伤", fstring_keys(_okpath) == [],
      str(fstring_keys(_okpath)))

# ============================================================
section("⑤ lang 配置项：默认中文、脏值不崩")

check("DEFAULT_CFG 里有 lang", "lang" in G.DEFAULT_CFG)
check("默认是 zh 而不是 auto（升级不该悄悄换界面语言）",
      G.DEFAULT_CFG.get("lang") == "zh", repr(G.DEFAULT_CFG.get("lang")))
check("默认值在允许的取值里", G.DEFAULT_CFG.get("lang") in I.CHOICES)

# 老配置里没有 lang 这一项 —— 这是所有老用户的真实情况
G.save_cfg({})
check("老配置（没有 lang 这一项）读出来就是 zh",
      G.load_cfg().get("lang") == "zh", repr(G.load_cfg().get("lang")))

G.save_cfg({**G.DEFAULT_CFG, "lang": "en"})
check("存了 en 就读得回 en", G.load_cfg().get("lang") == "en")
check("apply_language 真的把它翻成 en", G.apply_language(G.load_cfg()) == "en")

for _raw in ("fr", "", None, 123, ["en"], {"a": 1}, True):
    _got = G.apply_language({"lang": _raw})
    check(f"lang={_raw!r} 回落中文而不是崩", _got == "zh", repr(_got))

check("连 lang 都没有（空配置）也是中文", G.apply_language({}) == "zh")

# 落盘 / 读回这一圈：ensure_ascii=False + UTF-8，别让中文变成 \uXXXX
G.save_cfg({**G.DEFAULT_CFG, "lang": "zh", "exclude_title": "^微信$"})
check("中文正则原样存取（没有被转义成 \\uXXXX）",
      G.load_cfg().get("exclude_title") == "^微信$")

# ============================================================
section("⑥ App / main 真的是在建界面之前读的 lang")

# 为什么要管顺序：标题、卡片、按钮的文字在**建的那一刻**就定下来了。
# 晚一步设语言，界面上会先画出旧语言的一帧，然后才被覆盖 —— 闪一下很怪，
# 而且截图 / 自检脚本量到的都是那一帧。
#
# 为什么用 AST 而不是搜字符串：搜字符串的版本被自己骗过一次 ——
# 把 `apply_language(self.cfg)` 注释掉（`# apply_language(self.cfg)`）
# 它还是一条不落地算通过。这里要的是「真的会执行的调用」，只有语法树分得清
# 注释和代码。
import ast                                          # noqa: E402

_TREE = ast.parse(GUI_SRC, "swap_gui.py")


def _find_func(tree, cls, name):
    """取出 App.__init__ 的语法树节点；找不到返回 None。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == name:
                    return sub
    return None


def _find_main(tree):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    return None


def _line_of(func, name=None, attr=None):
    """func 里第一次出现 `name(...)` 调用 / `xx.attr(...)` 调用的行号。"""
    for sub in ast.walk(func):
        if isinstance(sub, ast.Call):
            f = sub.func
            if name and isinstance(f, ast.Name) and f.id == name:
                return sub.lineno
            if attr and isinstance(f, ast.Attribute) and f.attr == attr:
                return sub.lineno
    return None


_INIT_NODE = _find_func(_TREE, "App", "__init__")
check("找得到 App.__init__ 的语法树", _INIT_NODE is not None)

_apply_at = _line_of(_INIT_NODE, name="apply_language") if _INIT_NODE else None
_title_at = _line_of(_INIT_NODE, attr="title") if _INIT_NODE else None
_build_at = _line_of(_INIT_NODE, attr="_build") if _INIT_NODE else None

check("App.__init__ 里真的**调用**了 apply_language（注释掉不算）",
      _apply_at is not None, "没找到调用")
check("apply_language 在 root.title() 之前",
      _apply_at is not None and _title_at is not None and _apply_at < _title_at,
      f"apply@{_apply_at} title@{_title_at}")
check("apply_language 在 self._build() 之前（控件文字是在 _build 里定的）",
      _apply_at is not None and _build_at is not None and _apply_at < _build_at,
      f"apply@{_apply_at} build@{_build_at}")

_MAIN_NODE = _find_main(_TREE)
check("找得到 main 的语法树", _MAIN_NODE is not None)

_main_apply = _line_of(_MAIN_NODE, name="apply_language") if _MAIN_NODE else None
# --diag 那一行是 `if "--diag" in argv:`，用字符串常量找它最稳
_diag_at = None
if _MAIN_NODE:
    for _sub in ast.walk(_MAIN_NODE):
        if (isinstance(_sub, ast.Constant) and _sub.value == "--diag"):
            _diag_at = _sub.lineno
            break
check("main() 里也定了一次（--diag 和透传引擎那两条路不建 App）",
      _main_apply is not None, "没找到调用")
check("main() 里这一步在 --diag 之前",
      _main_apply is not None and _diag_at is not None and _main_apply < _diag_at,
      f"apply@{_main_apply} diag@{_diag_at}")

# 反向验证：证明上面这几个检测器不是永真。两次都是「把源码改坏 → 必须报出来」。
_BAD_SRC = GUI_SRC.replace("        apply_language(self.cfg)\n",
                           "        # apply_language(self.cfg)\n", 1)
_BAD_INIT = _find_func(ast.parse(_BAD_SRC), "App", "__init__")
check("反向：把 App 里那行注释掉，检测器必须认为它不存在",
      _BAD_INIT is not None and _line_of(_BAD_INIT, name="apply_language") is None)

_MOVED = GUI_SRC.replace("        apply_language(self.cfg)\n", "", 1)
_MOVED = _MOVED.replace("        self._build()\n",
                        "        self._build()\n        apply_language(self.cfg)\n", 1)
_MOVED_INIT = _find_func(ast.parse(_MOVED), "App", "__init__")
check("反向：把调用挪到 _build() 之后，顺序检查必须失败",
      _MOVED_INIT is not None
      and _line_of(_MOVED_INIT, name="apply_language")
      > _line_of(_MOVED_INIT, attr="_build"))

# ============================================================
section("⑦ 界面上真的没有漏翻的中文（中英各建一次窗）")

# 这一节要真建窗口 —— 它验的是「扫文案有没有漏」，这个只有把控件建出来才看得见：
# 词条表写得再整齐，只要某个 ttk.Label 忘了包 T()，界面上就是一句中文。
import tkinter as tk                              # noqa: E402

# 这些控件的文字是**运行时才定**的（窗口数、注册状态、当前排列说明……），
# 一档只扫静态文案，它们留给下一批。名单里每减一项，都是那批的工作量。
DYNAMIC = ("count_lbl", "perm_lbl", "perm_hint", "perm_warn", "hk_state", "status")


def build_app(lang):
    with open(TEST_CFG, "w", encoding="utf-8") as f:
        json.dump({"lang": lang}, f)
    root = tk.Tk()
    root.withdraw()
    return root, G.App(root)


def walk(w, out=None):
    out = [] if out is None else out
    out.append(w)
    for c in w.winfo_children():
        walk(c, out)
    return out


def subtree_ids(w):
    return {id(x) for x in walk(w)}


def cjk_texts(widgets):
    """控件 text 里还带中文的（没有 -text 选项的控件直接跳过）。"""
    bad = []
    for w in widgets:
        try:
            t = w.cget("text")
        except tk.TclError:            # Frame / Combobox / Treeview 没有 -text
            continue
        if isinstance(t, str) and CJK.search(t):
            bad.append(f"{w.winfo_class()} {t!r}")
    return bad


def audit(lang):
    """建一次窗，返回（跳过动态控件后仍带中文的控件, 全部控件数）。"""
    root, app = build_app(lang)
    try:
        skip = {id(getattr(app, n)) for n in DYNAMIC if hasattr(app, n)}
        skip |= subtree_ids(app.cards)          # 卡片里的「屏1 / 送往」也是动态的
        skip |= subtree_ids(app.perm_warn_box)  # 提醒条同理
        widgets = [w for w in walk(root) if id(w) not in skip]
        bad = cjk_texts(widgets)
        heads = [app.tree.heading(c, "text")
                 for c in ("mon", "state", "rect", "proc", "title", "to")]
        bad += [f"表头 {h!r}" for h in heads if CJK.search(h or "")]
        return bad, len(widgets), heads
    finally:
        root.destroy()


# 反向验证先跑：中文模式下同一个 walker 必须**抓得到**一堆中文。
# 不然「英文模式下没有中文」可能只是因为 walker 什么都没扫到（假绿）。
_bad_zh, _n_zh, _head_zh = audit("zh")
check(f"中文模式下扫到 {_n_zh} 个控件，其中带中文的不少于 15 个（说明 walker 真在扫）",
      _n_zh > 20 and len(_bad_zh) >= 15, f"{len(_bad_zh)} 个带中文")

_bad_en, _n_en, _head_en = audit("en")
check(f"英文模式下扫了 {_n_en} 个控件", _n_en > 20, str(_n_en))
check("英文模式下没有任何一个控件残留中文", not _bad_en,
      "; ".join(_bad_en[:6]))
check("英文模式下六个表头也全翻好了",
      _head_en == ["Screen", "State", "Rect", "Process", "Window title", "Goes to"],
      str(_head_en))
check("中文模式下六个表头是中文",
      _head_zh == ["屏幕", "状态", "位置", "进程", "窗口标题", "互换后"], str(_head_zh))

# 窗口标题也要跟着语言走
for _lang, _want in (("zh", "多屏窗口互换"), ("en", "Swap Monitors")):
    _r, _a = build_app(_lang)
    _t = _r.title()
    _r.destroy()
    check(f"lang={_lang} 时窗口标题是 {_want!r}", _t.startswith(_want), _t)

I.set_language("zh")

# ============================================================
section("⑧ 语言下拉框：存进配置、明说要重启、不当场改")

_root, _app = build_app("zh")
check("下拉框给出的选项是中文/English/跟随系统",
      [lb for lb, _v in _app._lang_choices] == ["中文", "English", "跟随系统"],
      str([lb for lb, _v in _app._lang_choices]))
check("当前配置是 zh 时，下拉框显示「中文」", _app.lang_var.get() == "中文",
      _app.lang_var.get())

_app.lang_var.set("English")
_app._on_lang_change()
check("选 English 之后配置落盘成 en", G.load_cfg().get("lang") == "en",
      repr(G.load_cfg().get("lang")))
check("状态栏用英文提示重启（这句话按**刚选的**语言说）",
      "restart" in _app.status.cget("text"), _app.status.cget("text"))
# 这条是「重启生效」这个决定的守门人：一旦有人改成热切换，这里立刻红
check("这次切换没有当场改变生效中的语言（说明确实是「重启后生效」）",
      I.current() == "zh", I.current())

_app.lang_var.set("中文")
_app._on_lang_change()
check("选回中文也能存回去", G.load_cfg().get("lang") == "zh")
check("选回中文时中文提示重启", "重启" in _app.status.cget("text"),
      _app.status.cget("text"))

# 选了个跟当前一样的，应该直接返回：不写盘、也不刷一句没用的提示
G.save_cfg({**G.DEFAULT_CFG, "lang": "auto"})
_app.cfg["lang"] = "auto"
_before = _app.status.cget("text")
_app.lang_var.set("跟随系统")
_app._on_lang_change()
check("选了个没变的选项不刷状态栏（早退，不做无用功）",
      _app.status.cget("text") == _before, _app.status.cget("text"))
_root.destroy()

# 配置里写着 auto 时，下拉框要停在 auto 那一项。
# ⚠️ 这里**不能**断言字面是「跟随系统」：CI 是英文系统，auto 会解析成 en，
# 那一项的标签就变成 "Auto" 了 —— 本文件第一版正是这么写的，CI 直接红在
# 这条上。断言「选中的那一项对应的配置值等于 auto」才跟语言无关。
_root2, _app2 = build_app("auto")
check("配置是 auto 时下拉框停在 auto 那一项（标签跟着界面语言变，不查字面）",
      [v for lb, v in _app2._lang_choices if lb == _app2.lang_var.get()] == ["auto"],
      _app2.lang_var.get())
_root2.destroy()

I.set_language("zh")

# ============================================================
os.remove(TEST_CFG)

print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
