#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「排除的窗口标题」输入框的回归测试。

背景（v1.1.7 的 bug）：输入框的 write trace 直接调 _save()，而 _save() 里
会做正则校验 —— 写错就弹模态框、并且把输入框的值 set() 回上一个能用的值。
于是**每敲一个字符**都走一遍这个流程：

    敲 "("  → 正则非法 → 弹框 → 值被改回 ""
    敲 "S"  → 变成 "S" …

用户的实际感受是「输入框吞字符」：像 (Steam|Epic).* 这种正常正则根本打不进去，
而且每敲一个非法字符就弹一次框。这个 bug 只在真的逐字符输入时才复现，
所以测试也必须逐字符敲。

现在的约定：
  * _save() 只做**无副作用**的合法性判断（_re_ok），不弹框、不回写；
    非法值不落盘，配置里继续保留上一个能用的值。
  * 弹框只发生在 <FocusOut>（用户离开输入框时），且不回写、不改用户输入。

用法: python tests/test_regex_input.py
"""
import json
import os
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="swapmon-regex-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False}, f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def on_disk_exclude():
    try:
        with open(TEST_CFG, encoding="utf-8") as f:
            return json.load(f).get("exclude_title")
    except Exception:                                        # noqa: BLE001
        return None


print("== 1) _re_ok 本身 ==")
_ok = getattr(G, "_re_ok", None)
_is_ok = _ok if callable(_ok) else (lambda p: True)   # 老版本没有它，别把测试带崩
check("_re_ok 存在（供 _save 用的无副作用判断）", callable(_ok))
if callable(_ok):
    check("空串算合法（表示不过滤）", _ok("") is True)
    check("普通正则合法", _ok(r"(Steam|Epic).*") is True)
    check("半个括号非法", _ok("(") is False)
    check("非法量词非法", _ok("*abc") is False)
    check("不抛异常", all(_ok(p) in (True, False)
                          for p in ("", "(", "*", "a{2,1}", r"\p{L}")))
else:
    print("  ⏭  _re_ok 的单元断言（跳过：程序里没有这个函数）")

root = tk.Tk()
root.withdraw()
app = G.App(root)

_warnings = []
_orig_warn = G.messagebox.showwarning
G.messagebox.showwarning = lambda *a, **k: _warnings.append(a)

TEXT = "(Steam|Epic).*"
print("\n== 2) 逐字符输入正常正则（旧代码在这里会吞字符 + 狂弹框）==")
app.ex_var.set("")                     # 清空，模拟用户全选删掉重打
_typed = []
for ch in TEXT:
    app.ex_var.set(app.ex_var.get() + ch)   # 与真人敲键走同一条 trace
    _typed.append(on_disk_exclude())

check("输入框里是完整的正则（没有被改写）", app.ex_var.get() == TEXT, repr(app.ex_var.get()))
check("输入过程中一次模态框都没弹", _warnings == [], f"弹了 {len(_warnings)} 次")
check("落盘的 exclude_title 每一步都是合法正则",
      all(_is_ok(v) for v in _typed), str(_typed))
check("最后落盘的就是刚输入的这段", on_disk_exclude() == TEXT, repr(on_disk_exclude()))

print("\n== 3) 输入非法正则：不弹框、不落盘、也不改用户输入 ==")
_warnings.clear()
app.ex_var.set("")                     # 清空（合法）
_before = on_disk_exclude()
app.ex_var.set("(")                    # 非法
check("非法值没有落盘（配置里还是上一个能用的值）",
      on_disk_exclude() == _before, f"{_before!r} -> {on_disk_exclude()!r}")
check("非法值也没被写进 cfg", app.cfg.get("exclude_title") == _before,
      repr(app.cfg.get("exclude_title")))
check("打字过程中不弹框", _warnings == [], f"弹了 {len(_warnings)} 次")
check("输入框里仍然是用户敲的那个字符（没被改回去）",
      app.ex_var.get() == "(", repr(app.ex_var.get()))

print("\n== 4) 光标离开（FocusOut）时才提示一次 ==")
_warnings.clear()
app._check_exclude_re()                # <FocusOut> 绑的就是它
check("非法正则离开输入框时提示一次", len(_warnings) == 1, f"弹了 {len(_warnings)} 次")
check("提示文案说清了「保留上一次能用的值」",
      _warnings and "保留上一次" in str(_warnings[0]), str(_warnings[:1]))
check("提示之后输入框内容仍未被改写", app.ex_var.get() == "(", repr(app.ex_var.get()))
check("提示之后配置仍未被污染", on_disk_exclude() == _before, repr(on_disk_exclude()))

_warnings.clear()
app.ex_var.set(TEXT)                   # 改回合法
app._check_exclude_re()
check("合法正则离开输入框时不提示", _warnings == [], f"弹了 {len(_warnings)} 次")
check("合法值正常落盘", on_disk_exclude() == TEXT, repr(on_disk_exclude()))

G.messagebox.showwarning = _orig_warn
root.destroy()

print()
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    return_code = 1
else:
    print("全部通过 ✅ —— 输入框不再吞字符，校验只在离开时提示")
    return_code = 0
sys.exit(return_code)
