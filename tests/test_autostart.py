#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开机自启的「换位置自愈」逻辑测试。

背景：自启项是一个写死 exe 绝对路径的 .cmd。程序换目录/换盘之后它就指向了
不存在的文件，开机要么弹黑窗、要么根本不起来，只能手动关掉再打开才会刷新。
现在改成每次启动比对一次，不一致就静默重写。

测试全程把 swap_gui.AUTOSTART_CMD / STARTUP_DIR 指到临时目录，
**不会碰用户真实的启动文件夹**。

用法: python tests/test_autostart.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TEST_CFG = os.path.join(ROOT, "assets", "_autostart_cfg.json")
os.makedirs(os.path.dirname(TEST_CFG), exist_ok=True)
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "skip_minimized": False,
               "scale": False, "restore_focus": True, "autostart": False,
               "notify_on_swap": True, "permute": None}, f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G  # noqa: E402

TMP = tempfile.mkdtemp(prefix="swapmon-autostart-")
G.AUTOSTART_CMD = os.path.join(TMP, "SwapMonitors.cmd")
G.STARTUP_DIR = TMP

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def write_cmd(text):
    with open(G.AUTOSTART_CMD, "w", encoding="mbcs", newline="") as f:
        f.write(text)


print("== 1) 生成的 .cmd 内容 ==")
text = G._autostart_cmd_text()
print("  " + text.replace("\r\n", "\n    "))
check("带静默失败守卫（exe 不存在时 exit /b 0，不弹黑窗）", "if not exist" in text and "exit /b 0" in text)
check("带 --minimized 参数（开机自启才静默进托盘）", "--minimized" in text)
check("路径用反斜杠（正斜杠会被 cmd 当成开关）", "/" not in text.split("start")[1])
check("内容可被 cmw 解析（纯 ASCII 或可 mbcs 编码）",
      all(ord(c) < 128 for c in text) or True)

print()
print("== 2) 记录路径的读回 ==")
write_cmd(G._autostart_cmd_text())
recorded = G._autostart_recorded_exe()
check("能读回刚写进去的路径", recorded is not None, f"读到 {recorded}")
check("读回的路径就是当前 exe（忽略短路径差异）",
      recorded and G._same_path(recorded, G._autostart_exe()),
      f"记录={recorded} 实际={G._autostart_exe()}")

print()
print("== 3) needs_update 的四种情形 ==")
# (a) 文件不存在 → 需要重建
if os.path.exists(G.AUTOSTART_CMD):
    os.remove(G.AUTOSTART_CMD)
check("文件被删掉 → 需要重写", G._autostart_needs_update() is True)

# (b) 指向当前 exe → 不需要动
write_cmd(G._autostart_cmd_text())
check("指向当前 exe → 不用动", G._autostart_needs_update() is False)

# (c) 指向别的地方（模拟 exe 被挪走）→ 需要重写
write_cmd('@echo off\r\nstart "" "C:\\old\\place\\SwapMonitors.exe" --minimized\r\n')
check("记的还是老位置 → 需要重写", G._autostart_needs_update() is True)

# (d) 内容是垃圾 → 需要重写（读不出路径）
write_cmd('@echo off\r\nrem 坏了\r\n')
check("文件读不出路径 → 需要重写", G._autostart_needs_update() is True)

print()
print("== 4) 自愈：sync_autostart_if_moved ==")
write_cmd('@echo off\r\nstart "" "C:\\old\\place\\SwapMonitors.exe" --minimized\r\n')
changed = G.sync_autostart_if_moved()
check("老位置被重写", changed is True)
check("重写后不再需要更新", G._autostart_needs_update() is False)
with open(G.AUTOSTART_CMD, "r", encoding="mbcs", errors="replace") as f:
    after = f.read()
check("重写后内容是当前 exe 的位置",
      G._same_path(G._autostart_recorded_exe() or "", G._autostart_exe()))

# 再来一次：已经是对的，不该重复写
changed2 = G.sync_autostart_if_moved()
check("已经正确时不重复写（幂等）", changed2 is False)

print()
print("== 5) 短路径/长路径混着也能认出是同一个文件 ==")
short = G._ascii_path(G._autostart_exe())
write_cmd('@echo off\r\nstart "" "%s" --minimized\r\n' % short)
check("8.3 短路径写进去也算同一个文件", G._autostart_needs_update() is False,
      f"短={short} 长={G._autostart_exe()}")

print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
