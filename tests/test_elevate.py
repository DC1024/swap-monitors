#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提权重启（relaunch_as_admin）的环境清洗回归测试。

对应 v1.1.1 修复的崩溃：PyInstaller 6.22+ 的 onefile 安全校验（#9492/#9520）
会把「继承了上一实例 _PYI_* 环境变量的 UAC 提权进程」当成 onefile 子进程去
校验原始父进程，而原 bootloader 已退出 → 弹
「Security validation failure: invalid originating onefile parent process
(PID not found)」，提权重启直接失败。

修法：拉起前摘掉 _PYI_* 并设 PYINSTALLER_RESET_ENVIRONMENT=1（官方的重启
开关），ShellExecuteW 复制走环境块之后原样还原。本测试验证这对函数的
行为契约；真正的 UAC 弹窗没法自动化，只能人工点按钮验证。

用法: python tests/test_elevate.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TEST_CFG = os.path.join(ROOT, "assets", "_elevate_cfg.json")
os.makedirs(os.path.dirname(TEST_CFG), exist_ok=True)
import json  # noqa: E402

with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "skip_minimized": False,
               "scale": False, "restore_focus": True, "autostart": False,
               "notify_on_swap": True, "permute": None}, f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G  # noqa: E402

failures = []


def check(name, cond, detail=""):
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("== 1) 正常路径：摘掉 _PYI_*，设上官方开关，还原后一切如初 ==")
os.environ["_PYI_ARCHIVE_FILE"] = r"C:\fake\SwapMonitors.exe"
os.environ["_PYI_PARENT_PID"] = "12345"
os.environ["_PYI_APPLICATION_HOME_DIR"] = r"C:\fake\_MEI123"
os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)

saved = G._sanitize_env_for_independent_child()
check("调用期间 _PYI_ARCHIVE_FILE 已摘除", "_PYI_ARCHIVE_FILE" not in os.environ)
check("调用期间 _PYI_PARENT_PID 已摘除", "_PYI_PARENT_PID" not in os.environ)
check("调用期间 _PYI_APPLICATION_HOME_DIR 已摘除", "_PYI_APPLICATION_HOME_DIR" not in os.environ)
check("调用期间 PYINSTALLER_RESET_ENVIRONMENT=1（官方开关）",
      os.environ.get("PYINSTALLER_RESET_ENVIRONMENT") == "1")

G._restore_env_for_child(saved)
check("还原后 _PYI_ARCHIVE_FILE 回到原值",
      os.environ.get("_PYI_ARCHIVE_FILE") == r"C:\fake\SwapMonitors.exe")
check("还原后 _PYI_PARENT_PID 回到原值", os.environ.get("_PYI_PARENT_PID") == "12345")
check("还原后 _PYI_APPLICATION_HOME_DIR 回到原值",
      os.environ.get("_PYI_APPLICATION_HOME_DIR") == r"C:\fake\_MEI123")
check("还原后 PYINSTALLER_RESET_ENVIRONMENT 被移除（原本就不存在）",
      "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ)

print()
print("== 2) 调用方自己本来就设了 PYINSTALLER_RESET_ENVIRONMENT 时，还原成它自己的值 ==")
os.environ.pop("_PYI_ARCHIVE_FILE", None)
os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "0"
saved = G._sanitize_env_for_independent_child()
check("调用期间强制为 1", os.environ.get("PYINSTALLER_RESET_ENVIRONMENT") == "1")
G._restore_env_for_child(saved)
check("还原后回到调用方的 0", os.environ.get("PYINSTALLER_RESET_ENVIRONMENT") == "0")

print()
print("== 3) 快照里不该混进非 _PYI_ 的无关变量 ==")
os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG          # 明确放一个无关变量
saved = G._sanitize_env_for_independent_child()
check("快照只含 _PYI_* 与 PYINSTALLER_RESET_ENVIRONMENT 两个键",
      set(saved) <= {"PYINSTALLER_RESET_ENVIRONMENT"}
      or all(k.startswith("_PYI_") or k == "PYINSTALLER_RESET_ENVIRONMENT" for k in saved),
      f"实际键: {sorted(saved)}")
G._restore_env_for_child(saved)

print()
print("== 4) relaunch_as_admin 本体仍在、签名没变（不真的弹 UAC） ==")
import inspect  # noqa: E402
sig = inspect.signature(G.relaunch_as_admin)
check("relaunch_as_admin 是无参函数", len(sig.parameters) == 0)
check("两个新helper都存在", callable(G._sanitize_env_for_independent_child)
      and callable(G._restore_env_for_child))

print()
os.environ.pop("_PYI_ARCHIVE_FILE", None)
os.environ.pop("_PYI_PARENT_PID", None)
os.environ.pop("_PYI_APPLICATION_HOME_DIR", None)
os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
