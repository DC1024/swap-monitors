#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热键里「只能有一个主键」的回归测试。

背景（v1.1.7 的 bug）：parse_hotkey 遇到第二个主键时直接覆盖：

    for part in ...:
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk = VK_F1 + int(part[1:]) - 1        ← 覆盖
        elif len(part) == 1 and part in MAIN_KEYS:
            vk = ord(part.upper())                ← 又覆盖
        ...
        names.append(part.upper())                ← 但显示名两个都留着

于是 `ctrl+s+d` 解析成 vk=D、显示名 `Ctrl+S+D`：界面写着按 Ctrl+S，
实际注册的是 Ctrl+D。用户按 S 没反应，回设置里看又"明明写着 S" ——
最难自查的一类不一致（而且没有任何报错）。

现在的约定：第二个主键直接报错，并说清"多出来的那个会被悄悄丢掉"。

用法: python tests/test_hotkey_main_key.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="swapmon-mainkey-")
os.environ["SWAPMONITORS_CONFIG"] = os.path.join(TMP, "config.json")

import swap_gui as G      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    print()
    print(f"== {t} ==")


def rejected(spec):
    """返回 None 表示按预期被拒；否则返回说明。"""
    try:
        got = G.parse_hotkey(spec)
    except ValueError as e:
        return None, str(e)
    return f"竟然接受了，返回 {got}", ""


print(f"临时目录: {TMP}")

# ============================================================
section("① 两个主键必须被拒（不能悄悄丢掉一个）")

for spec in ("ctrl+s+d", "ctrl+alt+f9+f10", "ctrl+s+1", "alt+shift+a+b"):
    bad, msg = rejected(spec)
    check(f"{spec!r} 被拒", bad is None, str(bad))
    check(f"{spec!r} 的提示说清了原因", "主键只能有一个" in msg, msg)

# 提示里要能看出是哪两个键，否则用户不知道该删哪个
_, msg = rejected("ctrl+s+d")
check("提示里点名了两个主键", "S" in msg and "D" in msg, msg)

# ============================================================
section("② 正常写法一个都不能误伤")

OK = {
    "ctrl+s": ord("S"),
    "alt+s": ord("S"),
    "win+s": ord("S"),
    "ctrl+alt+s": ord("S"),
    "ctrl+shift+f9": G.VK_F1 + 8,
    "ctrl+1": ord("1"),
    "CTRL + ALT + S": ord("S"),
    "ctrl-alt-f12": G.VK_F1 + 11,
}
for spec, want_vk in OK.items():
    try:
        mods, vk, shown = G.parse_hotkey(spec)
    except ValueError as e:
        check(f"{spec!r} 不该被拒", False, str(e))
        continue
    check(f"{spec!r} 解析正确", vk == want_vk and "+" not in shown.split("+")[-1],
          f"vk={vk} 期望 {want_vk}，显示 {shown}")

# ============================================================
section("③ 不变式：任何被接受的热键，显示名里只有一个主键")

# 这一条比逐个用例更能说明问题：旧代码下 ctrl+s+d 会被接受，
# 而显示名里有 S 和 D 两个主键 —— 就是 bug 本身。
MODS = ("Ctrl", "Alt", "Shift", "Win")
probe = ["ctrl+s", "ctrl+s+d", "ctrl+f1+f2", "alt+1+2", "win+shift+a+b+c",
         "ctrl+alt+shift+win+f24", "ctrl+shift+f9"]
for spec in probe:
    try:
        _, _, shown = G.parse_hotkey(spec)
    except ValueError:
        continue                       # 被拒的不用检查
    mains = [p for p in shown.split("+") if p not in MODS]
    check(f"{spec!r} 被接受时显示名只有 1 个主键", len(mains) == 1, f"显示 {shown}")

print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
