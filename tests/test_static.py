#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态自检：专抓「名字没定义」这种只在特定分支才炸的 bug。

为什么值得单独写一个测试文件
----------------------------
v1.0 → v1.1.4，`swap_gui.py` 里有 10 处 `messagebox.xxx(...)`，但整个文件
**从来没有 import 过 messagebox**。这些调用点分散在「提权确认 / 各种警告 /
托盘溢出一次性提示」等分支里，平时跑不到，一跑到就 `NameError` —— 而它是在
Tk 的回调里抛的，被 Tk 自己兜住、只在 stderr 打一行 traceback，于是用户看到
的现象是**「点了没反应」**，完全联想不到是缺一行 import。

后果最重的一处：`_prompt_elevate_for_admin()` 里的 `askyesno` 是勾选
「记住管理员模式」的**必经之路**，一抛异常，「勾选 → 引导提权 → 创建计划任务」
整条链路就断了，勾选框还会悄悄弹回去 —— 也就是那个版本的主打功能对普通用户
**完全不可达**。而当时所有的冒烟测试是全绿的（它们不点那个勾选框）。

这类「引用了但没定义」用 pyflakes 一行就能查出来，但本项目刻意保持零第三方
依赖，所以这里用标准库 `symtable` 自己把作用域表走一遍：
凡是「**被读取** + 是**全局名** + 模块顶层**没绑定** + **不是内建**」的名字，
就是 bug。

用法: python tests/test_static.py
需要带 tkinter 的解释器（第 ③ 节要 import swap_gui）。
"""
import ast
import builtins
import json
import os
import re
import symtable
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 隔离用户配置（import swap_gui 时它会读 SWAPMONITORS_CONFIG 定下 CFG_PATH）
TMP = tempfile.mkdtemp(prefix="swapmon-static-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f10", "exclude_title": ".*", "autostart": False},
              f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

FILES = ["swap_gui.py", "swap_windows.py", "swap_i18n.py"]

# 内建名 + 解释器自动注入的模块级 dunder
ALLOWED = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__",
}

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    print()
    print(f"== {t} ==")


def undefined_globals(path):
    """返回 src 里「被读取但全局作用域里找不到」的名字（已排序）。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    top = symtable.symtable(src, path, "exec")
    # ⚠️ 这里只能取「**真正绑定**过的」顶层名字。
    # 踩过的坑：symtable 的模块表**同时包含**「只在顶层代码里被引用」的名字
    # （比如 `u32.GetCursorPos.argtypes = [ctypes.POINTER(sw.POINT)]` 里那个 sw），
    # 直接拿 get_symbols() 全体当「已定义集合」，反向用例就抓不到东西 ——
    # 当时删掉 `import swap_windows as sw` 却报不出 sw，正是这个原因。
    # is_assigned()：赋值 / for 目标 / with as / except as / 推导式 / def / class
    # is_imported()：import 与 from-import
    defined = {s.get_name() for s in top.get_symbols()
               if s.is_assigned() or s.is_imported()}
    bad = set()

    def walk(tbl):
        for sym in tbl.get_symbols():
            name = sym.get_name()
            # is_global()：这个引用要去「模块全局」里找（不是局部变量、不是闭包变量）。
            # is_referenced()：只算**读**。只有赋值的是定义，不是我们要找的。
            if (sym.is_referenced() and sym.is_global()
                    and name not in defined and name not in ALLOWED):
                bad.add(name)
        # 函数/类/推导式/lambda 都会开出子作用域，层层走下去
        for child in tbl.get_children():
            walk(child)

    walk(top)
    return sorted(bad)


print(f"临时目录: {TMP}")

# ============================================================
section("① 未定义全局名（v1.1.4 的 messagebox 就是栽在这）")
for name in FILES:
    got = undefined_globals(os.path.join(ROOT, name))
    check(f"{name} 没有「引用了但没定义」的全局名",
          not got, f"未定义: {got}")

# ============================================================
section("② 反向验证：检测器真能抓到这类 bug（不然①可能是假绿）")
_src = open(os.path.join(ROOT, "swap_gui.py"), encoding="utf-8").read()
_broken_path = os.path.join(TMP, "broken_gui.py")


def _strip_once(line):
    """删掉某一整行（连同换行），写进临时文件，返回检测结果。"""
    assert _src.count(line) == 1, f"这行在源码里应恰好出现一次: {line!r}"
    with open(_broken_path, "w", encoding="utf-8") as f:
        f.write(_src.replace(line, "", 1))
    return undefined_globals(_broken_path)


# 三个各删一个 import 的用例。删的是「不同层级的名字」：
#   messagebox → 只在函数体里用（正是 v1.1.4 那个 bug 的形状）
#   sw         → 顶层代码里也在用（`sw.POINT`），专门测上面那个「已定义集合取太宽」的坑
#   ttk        → 顶层在建控件时用
check("删掉 `from tkinter import messagebox` 后必须报出 messagebox",
      "messagebox" in _strip_once("from tkinter import messagebox\n"))
check("删掉 `import swap_windows as sw` 后必须报出 sw（顶层也引用过它）",
      "sw" in _strip_once("import swap_windows as sw\n"))
check("删掉 `from tkinter import ttk` 后必须报出 ttk",
      "ttk" in _strip_once("from tkinter import ttk\n"))

check("没动过手脚的源码在这几个反向用例下都不报（说明不是无条件报警）",
      not undefined_globals(os.path.join(ROOT, "swap_gui.py")))

# ============================================================
section("③ messagebox 运行时确实可用")
import tkinter                                     # noqa: E402
import swap_gui as G                               # noqa: E402

check("swap_gui 模块里有 messagebox 这个名字", hasattr(G, "messagebox"))
check("它就是 tkinter.messagebox（不是同名的别的东西）",
      getattr(G, "messagebox", None) is tkinter.messagebox)

_src_gui = open(os.path.join(ROOT, "swap_gui.py"), encoding="utf-8").read()
_calls = sorted(set(re.findall(r"\bmessagebox\.(\w+)", _src_gui)))
_count = len(re.findall(r"\bmessagebox\.", _src_gui))
check(f"源码里用到的 {len(_calls)} 个 messagebox 方法都存在",
      all(hasattr(G.messagebox, m) for m in _calls),
      f"用到: {_calls}")
check("四个常用方法齐全（showinfo / showwarning / askyesno / showerror）",
      all(hasattr(G.messagebox, m) for m in
          ("showinfo", "showwarning", "askyesno", "showerror")))
# v1.1.7 起是 11 处：新增了「排除标题的正则写错了」这条警告。
# 这个数字是**故意写死**的：多一处少一处都该回来看看是不是漏了 import。
check("调用点数量是预期的 11 处（多一处少一处都该回来看看）", _count == 11,
      f"实际 {_count} 处")

# ============================================================
section("④ 「一次性提示」必须先展示成功、再记账（顺序回归）")


class _FakeApp:
    """只带 _overflow_hint 需要的那几个属性的替身，不用起 Tk。"""

    def __init__(self, hidden=False):
        self.cfg = {}
        self.hidden = hidden
        self.hk = None
        self.root = None
        self.saved = 0

    def _save(self):
        self.saved += 1


_real_showinfo = G.messagebox.showinfo
try:
    # (a) 展示这一步抛异常 → 不许记账，且要返回 False 让调用方有机会重试
    def _boom(*a, **kw):
        raise RuntimeError("模拟弹窗失败")

    G.messagebox.showinfo = _boom
    _a = _FakeApp()
    _r = G.App._overflow_hint(_a, "tip")
    check("展示失败时返回 False", _r is False)
    check("展示失败时**不**记账（_overflow_hint_acked 没被写进 cfg）",
          "_overflow_hint_acked" not in _a.cfg)
    check("展示失败时也没落盘", _a.saved == 0)

    # (b) 正常展示 → 记账 + 返回 True
    G.messagebox.showinfo = lambda *a, **kw: "ok"
    _b = _FakeApp()
    _r2 = G.App._overflow_hint(_b, "tip")
    check("展示成功时返回 True", _r2 is True)
    check("展示成功时记账", _b.cfg.get("_overflow_hint_acked") is True)
    check("展示成功时落盘一次", _b.saved == 1)

    # (c) 已记账过就短路，不再弹第二次
    _c = _FakeApp()
    _c.cfg["_overflow_hint_acked"] = True
    check("已提示过时直接返回 False、不再弹",
          G.App._overflow_hint(_c, "tip") is False and _c.saved == 0)
finally:
    G.messagebox.showinfo = _real_showinfo

# hk 存在且界面隐藏（开机自启）时，走气泡通知，也算展示成功
_src_hint = _src_gui[_src_gui.index("def _overflow_hint"):]
_src_hint = _src_hint[:_src_hint.index("def _maybe_hint_overflow")]
check("隐藏状态下走 hk.notify 气泡而不是模态框",
      "self.hidden" in _src_hint and "self.hk.notify" in _src_hint
      and "messagebox.showinfo" in _src_hint)
check("写入 _overflow_hint_acked 的动作在 try 之后（先展示后记账）",
      _src_hint.index("_overflow_hint_acked\"] = True")
      > _src_hint.index("messagebox.showinfo"))

# ============================================================
section("⑤ 语法与字节码")
for name in FILES:
    _p = os.path.join(ROOT, name)
    try:
        with open(_p, encoding="utf-8") as f:
            compile(f.read(), _p, "exec")
        check(f"{name} 能编译通过", True)
    except SyntaxError as e:                            # noqa: BLE001
        check(f"{name} 能编译通过", False, f"{e}")

# ============================================================
section("⑥ 默认跳过名单不误伤正常窗口（任务管理器曾是受害者）")
import swap_i18n as I                                   # noqa: E402
import swap_windows as SW                               # noqa: E402

# v1.0 首版把 `Task Manager|任务管理器` 写进了 DEFAULT_SKIP_TITLE，
# 于是用户开着的任务管理器永远不跟着屏走 —— 而它只是个普通窗口。
for _t in ("任务管理器", "Task Manager", "task manager", "TASK MANAGER"):
    check(f"默认跳过名单不再匹配 {_t!r}",
          not SW.DEFAULT_SKIP_TITLE.match(_t), SW.DEFAULT_SKIP_TITLE.pattern)
for _t in ("Program Manager", "DWM", "Windows 输入体验",
           "Windows Input Experience", "Windows Shell Experience Host",
           "Microsoft Text Input Application"):
    check(f"该跳过的 {_t!r} 仍然在名单里",
          bool(SW.DEFAULT_SKIP_TITLE.match(_t)), SW.DEFAULT_SKIP_TITLE.pattern)

_src_sw = open(os.path.join(ROOT, "swap_windows.py"), encoding="utf-8").read()
check("源码里确实没有把任务管理器列进跳过正则",
      "Task Manager|任务管理器" not in _src_sw)

# 反向验证：真把任务管理器塞回去，上面的断言必须失败（不然就是假绿）
check("反向验证：塞回黑名单后正则会匹配「任务管理器」",
      bool(re.compile(r"^(DWM|Task Manager|任务管理器)$", re.I).match("任务管理器")))

# err=5 是 UIPI 的唯一铁证。旧的「读不到进程名 = 管理员进程」判据靠不住：
# 本程序与对方同为管理员时进程名读得到，窗口照样可能被拒绝。
_mv = _src_sw[_src_sw.index("def move_window"):_src_sw.index("def swap(args)")]
check("move_window 读取 SetWindowPos 的 GetLastError", "get_last_error()" in _mv)
check("err=5 时明确报「拒绝访问」并指向管理员模式",
      "err == 5" in _mv and "拒绝访问" in _mv and "管理员模式" in _mv)
check("失败不再一律归因于「可能权限不足」", "可能权限不足" not in _mv)

# ============================================================
section("⑦ 版本号与文档自洽")
check("APP_VER 形如 x.y.z", bool(re.fullmatch(r"\d+\.\d+\.\d+", G.APP_VER)),
      G.APP_VER)
_md_path = os.path.join(ROOT, "README.md")
_md = open(_md_path, encoding="utf-8").read() if os.path.exists(_md_path) else ""
for _t in ("test_admin_mode.py", "test_static.py", "test_elevate.py",
           "test_hardening.py"):
    check(f"README 的测试清单里列出了 {_t}", _t in _md)

# ============================================================
section("⑧ 控件的显示文字不许当语义用（B1 回归）")
# 踩过的坑：排列卡片的下拉框原来是 `v.startswith("屏")` + `int(v[1:])`，
# 从**显示文字**里抠屏号。文案一变（界面切成英文后是 "Screen 2"）判定就为假 →
# 不报错、不弹框、不写配置，用户看到的是「改了下拉没反应」。
# 现在显示文字和屏号分开存（`nums` + `combo.current()`），文字随便翻。
# 判定走 ast 而不是 grep：`_sync_cards` 的注释里就写着 `v.startswith("屏")`
# 这句反例原文，字符串搜索会把注释也算上（test_i18n 已经在这上面栽过一次）。
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


def text_parsing_calls(path):
    """返回 [(行号, 说明)]：拿中文字面量做前缀/后缀判定的调用。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("startswith", "endswith")):
            continue
        for a in node.args:
            if (isinstance(a, ast.Constant) and isinstance(a.value, str)
                    and _CJK.search(a.value)):
                bad.append((node.lineno, f".{fn.attr}({a.value!r})"))
    return bad


for _name in FILES:
    _got = text_parsing_calls(os.path.join(ROOT, _name))
    check(f"{_name} 没有「拿中文显示文字做判定」的 startswith/endswith",
          not _got, str(_got))

# 反向验证：把老写法塞进一个临时文件，检测器必须点名报出来（不然 ⑧ 上面的绿是假的）
_bad_path = os.path.join(TMP, "bad_text_parse.py")
with open(_bad_path, "w", encoding="utf-8") as f:
    f.write('def f(v):\n'
            '    if v.startswith("屏"):\n'
            '        return int(v[1:])\n'
            '    return 0\n')
check("反向验证：`v.startswith(\"屏\")` 会被点名",
      [ln for ln, _why in text_parsing_calls(_bad_path)] == [2],
      str(text_parsing_calls(_bad_path)))

# 取值那一步必须真的走下标（注释里说得好听、代码没改也照样能绿，所以直接看源码）
import inspect                                             # noqa: E402
_src_pick = inspect.getsource(G.App._card_choice)
check("_card_choice 用 combo.current() 取「第几项」", "current()" in _src_pick)
check("_card_choice 不再从显示文字里抠数字",
      "startswith" not in _src_pick and "int(v[1:])" not in _src_pick)
check("同一份代码里 values 与 nums 是两个分开的来源",
      'cw["nums"] = [num for _label, num in opts]' in _src_gui
      and "values=[label for label, _num in opts]" in _src_gui)

# ============================================================
section("⑨ 「权限不足」的判定不许拿显示文字做字符串匹配（B6 回归）")
# 踩过的坑：swap() 原来写 `if "拒绝访问" in note or w.proc is None`，
# 用 move_window 返回的那句**给人看的说明**判断「是不是权限不够」。
# 界面一切成英文，note 就不是那四个字了 —— 而 `in` 判断**不报错**，
# 只是恒为假：用户照样看到「部分窗口未能互换」，却再也看不到
# 「要不要以管理员身份重启」那一步，等于把一个功能悄悄关掉。
# 这和 ⑧ 的下拉框是同一类错：拿显示文字当语义用。
#
# 修法是把「为什么失败」和「怎么告诉用户」拆开：why 是机器可读的常量
# （WHY_DENIED 等，永不翻译），note 只负责好看。下面的守卫锁住这件事。
_src_sw_full = open(os.path.join(ROOT, "swap_windows.py"), encoding="utf-8").read()
_swap_body = _src_sw_full[_src_sw_full.index("def swap(args):")
                          :_src_sw_full.index("def _harden_console")]

check("swap() 用 move_window_detail 拿 why",
      "move_window_detail(" in _swap_body, _swap_body[:200])
check("「权限不足」判定走 why == WHY_DENIED，不再匹配 note 里的字",
      "why == WHY_DENIED" in _swap_body, _swap_body[:400])
check("swap() 里没有 `\"拒绝访问\" in note` 这种判定",
      '"拒绝访问" in note' not in _swap_body
      and "拒绝访问' in note" not in _swap_body)

# WHY_* 这一组必须是**常量**、且值里不能带中文 —— 否则它自己又变成了
# 一份要翻译的文案，等于绕了一圈回到原处。
for _k in ("WHY_DENIED", "WHY_PLACEMENT", "WHY_SETPOS", "WHY_NOCHANGE",
           "WHY_MAXIMIZED"):
    check(f"{_k} 定义了", f"{_k} = " in _src_sw_full)
_why_vals = re.findall(r'^WHY_\w+ = "([^"]*)"', _src_sw_full, re.M)
check(f"{len(_why_vals)} 个 WHY_* 的值里都没有中文（机器可读，永不翻译）",
      _why_vals and not any(_CJK.search(v) for v in _why_vals), str(_why_vals))

# 反向验证：把老写法塞回去，检测器必须变红。
# 不这么做的话，上面那三条只是「恰好现在没写」—— 哪天有人图省事改回去，
# 它们照样绿（因为 `"拒绝访问" in note` 只出现在新加的注释里也会被判为假阴性）。
_bad_swap = os.path.join(TMP, "bad_swap_denied.py")
with open(_bad_swap, "w", encoding="utf-8") as f:
    f.write('def swap(args):\n'
            '    note = "x"\n'
            '    if "拒绝访问" in note or w.proc is None:\n'
            '        denied += 1\n'
            '    return 0\n'
            'def _harden_console():\n'
            '    pass\n')
_bad_body = open(_bad_swap, encoding="utf-8").read()
_bad_body = _bad_body[:_bad_body.index("def _harden_console")]
check("反向验证：老写法 `\"拒绝访问\" in note` 会被点名",
      '"拒绝访问" in note' in _bad_body and "why == WHY_DENIED" not in _bad_body)

# move_window 必须是 move_window_detail 的薄壳（保留二元组返回值给老调用方），
# 而 detail 才是真正干活的那个。
_mw = inspect.getsource(SW.move_window)
check("move_window 仍然是 (ok, note) 二元组（老调用方不受影响）",
      "return ok, note" in _mw, _mw)
check("move_window 内部转调 move_window_detail",
      "move_window_detail(" in _mw, _mw)

# detail 的三元组返回值在三种状态下都得是 (bool, str, why|None)
def _mk_rect(l, t, r, b):
    rc = SW.RECT()
    rc.left, rc.top, rc.right, rc.bottom = l, t, r, b
    return rc


_rc = _mk_rect(100, 100, 900, 700)
_pl = SW.WINDOWPLACEMENT()
_pl.showCmd = SW.SW_SHOWNORMAL
_pl.rcNormalPosition = _mk_rect(100, 100, 900, 700)
_w = SW.Win(0xDEAD, "t", "C", 1, "nope.exe", _rc, 0, _pl)


class _Mon:
    def __init__(self, left):
        self.rect = _mk_rect(left, 0, left + 1920, 1080)
        self.work = _mk_rect(left, 0, left + 1920, 1040)


_TARGET = _mk_rect(2020, 100, 2820, 700)

for _label, _cmd in (("普通", SW.SW_SHOWNORMAL), ("最小化", SW.SW_SHOWMINIMIZED),
                     ("最大化", SW.SW_SHOWMAXIMIZED)):
    _pl.showCmd = _cmd
    _r = SW.move_window_detail(_w, _TARGET, _Mon(1920))
    check(f"{_label}窗口：move_window_detail 返回三元组 (bool, str, why)",
          isinstance(_r, tuple) and len(_r) == 3
          and isinstance(_r[0], bool) and isinstance(_r[1], str)
          and (_r[2] is None or isinstance(_r[2], str)), str(_r))

# 最核心的一条：why 与界面语言**无关**。这条红就是真 bug —— 意味着切语言
# 会改变程序行为。
for _lang in ("zh", "en"):
    I.set_language(_lang)
    _pl.showCmd = SW.SW_SHOWNORMAL
    _why = SW.move_window_detail(_w, _TARGET, _Mon(1920))[2]
    check(f"lang={_lang} 时 why 是 {SW.WHY_SETPOS!r}（不随语言变）",
          _why == SW.WHY_SETPOS, repr(_why))
I.set_language("zh")

print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
