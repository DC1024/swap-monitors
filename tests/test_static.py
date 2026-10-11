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

FILES = ["swap_gui.py", "swap_windows.py"]

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
check("调用点数量是预期的 10 处（多一处少一处都该回来看看）", _count == 10,
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
section("⑥ 版本号与文档自洽")
check("APP_VER 形如 x.y.z", bool(re.fullmatch(r"\d+\.\d+\.\d+", G.APP_VER)),
      G.APP_VER)
_md_path = os.path.join(ROOT, "README.md")
_md = open(_md_path, encoding="utf-8").read() if os.path.exists(_md_path) else ""
for _t in ("test_admin_mode.py", "test_static.py", "test_elevate.py"):
    check(f"README 的测试清单里列出了 {_t}", _t in _md)

print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
