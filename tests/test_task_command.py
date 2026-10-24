#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计划任务 XML 里的 `&amp;` 必须反转义的回归测试。

背景（v1.1.7 的 bug）：`_task_xml()` 写入任务时用 saxutils.escape 转义过
（v1.1.7 修的另一个问题：路径里有裸 `&` 会让 XML 直接失效），
但读回来的路径没还原就开始比对：

    task_points_to_current_exe():
        m = re.search(r"<Command>(.*?)</Command>", _task_xml_text(), re.S)
        return _same_path(m.group(1).strip(), _task_exe())     ← 拿 &amp; 比 &

于是装在 `C:\\Tools\\R&D\\` 这类目录下的用户，每次启动都判定「exe 换位置了」，
去重建一遍计划任务（`sync_task_if_moved`）：
  * 普通权限下重建必然失败 —— 白跑一趟；
  * 管理员权限下每次都白写一遍注册表里的任务，还多一次 UAC 交互；
  * 而且提示文案是「路径变了」，跟真实原因（XML 转义）毫无关系。

用法: python tests/test_task_command.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="swapmon-task-")
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


print(f"临时目录: {TMP}")

EXE = r"C:\Tools\R&D\SwapMonitors.exe"          # 目录名里带 & —— 就是会出事的那种
XML = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals>
    <Principal id="Author">
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Actions Context="Author">
    <Exec>
      <Command>{G._esc(EXE)}</Command>
      <Arguments>--minimized</Arguments>
    </Exec>
  </Actions>
</Task>
"""

# ============================================================
section("① 前置：这份 XML 里确实是转义形态（否则后面全是假绿）")

check("XML 里的 Command 是 &amp; 形态", "R&amp;D" in XML, XML.split("<Command>")[-1][:60])
check("裸 & 确实比不上转义形态（这就是 bug 的根）",
      G._same_path(r"C:\Tools\R&amp;D\SwapMonitors.exe", EXE) is False)

# ============================================================
section("② 反转义后能正确认出来")

_orig_text, _orig_exe = G._task_xml_text, G._task_exe
G._task_xml_text = lambda: XML
G._task_exe = lambda: EXE
try:
    # 旧代码里没有这个函数，用 getattr 兜一下，别让整个文件在这里就退出
    _get_cmd = getattr(G, "_task_command", None)
    check("提供了 _task_command() 这个统一入口（两处取 Command 的地方共用）",
          callable(_get_cmd))
    cmd = _get_cmd() if callable(_get_cmd) else None
    check("_task_command() 把 &amp; 还原成 &", cmd == EXE, repr(cmd))
    check("task_points_to_current_exe() 认得出这就是当前 exe",
          G.task_points_to_current_exe() is True)

    # 反向：路径真的不一样时仍要判 False，别为了变绿一律返回 True
    G._task_exe = lambda: r"D:\Other\SwapMonitors.exe"
    check("路径真的变了仍然判 False",
          G.task_points_to_current_exe() is False)
    G._task_exe = lambda: EXE

    # 没有任务时不能崩
    G._task_xml_text = lambda: ""
    check("没有任务时 _task_command() 返回 None",
          (cmd is None) if not callable(_get_cmd) else (_get_cmd() is None),
          repr(_get_cmd() if callable(_get_cmd) else None))
    check("没有任务时 task_points_to_current_exe() 返回 False",
          G.task_points_to_current_exe() is False)
    G._task_xml_text = lambda: XML

    # ============================================================
    section("③ sync_task_if_moved 不该白重建一遍任务")

    rebuilt = []
    _orig_elev, _orig_create = G.is_elevated, G.create_elevated_task
    G.is_elevated = lambda: True
    G.create_elevated_task = lambda: (rebuilt.append(1), (True, ""))[1]
    try:
        moved = G.sync_task_if_moved()
    finally:
        G.is_elevated, G.create_elevated_task = _orig_elev, _orig_create
    check("任务已经指向当前 exe 时不重建", rebuilt == [], f"重建了 {len(rebuilt)} 次")
    check("sync_task_if_moved() 返回 False（无需处理）", moved is False, repr(moved))
finally:
    G._task_xml_text, G._task_exe = _orig_text, _orig_exe


print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
