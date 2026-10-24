#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.7 四项加固的回归测试（纯离线，任何机器可跑）

覆盖的是那份外部评审里被点名的 P0：

  ① 热键必须带 Ctrl/Alt/Win，主键限 ASCII
     —— 之前 `s` 能注册成全局热键，全系统打字就废了；`中` 会被 isalnum()
        放过，然后 RegisterHotKey 失败却报「已被占用」。
  ② 计划任务 XML 转义 exe 路径
     —— 装在 `C:\\Tools\\R&D\\` 这类目录时 XML 直接失效。
  ③ EnumWindows 回调 try/except + 落日志
     —— ctypes 回调里抛异常无法向外传播，windowed exe 没 stderr，
        一个窗口就能静默截断整份列表。
  ④ re.error 兜底
     —— 非法正则不能再抛未捕获异常崩掉。

每一节都带**反向验证**：先证明「不作弊时检测器会报警」，否则该项是假绿。
"""

import io
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 隔离用户配置
TMP = tempfile.mkdtemp(prefix="swapmon-v117-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    f.write('{"hotkey": "ctrl+alt+f10", "exclude_title": "", "autostart": false}')
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G          # noqa: E402
import swap_windows as sw     # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(t):
    print()
    print(f"== {t} ==")


def skip(name, why):
    """环境不满足，这条用例跑不了 —— 明确打出来，不假装通过。"""
    print(f"  ⏭  {name}（跳过：{why}）")


def expect_reject(spec):
    try:
        got = G.parse_hotkey(spec)
    except ValueError:
        return True
    return f"竟然接受了，返回 {got}"


def expect_accept(spec, mods=None, vk=None):
    try:
        got = G.parse_hotkey(spec)
    except ValueError as e:
        return f"不该拒绝：{e}"
    if mods is not None and got[0] != mods:
        return f"mods={got[0]}，期望 {mods}"
    if vk is not None and got[1] != vk:
        return f"vk={got[1]}，期望 {vk}"
    return True


# ============================================================
section("① 热键：必须有修饰键，主键限 ASCII")

r = expect_reject("s")
check("裸 `s` 必须被拒（否则全系统打字报废）", r is True, str(r))
r = expect_reject("a")
check("裸 `a` 必须被拒", r is True, str(r))
r = expect_reject("shift+s")
check("`shift+s` 必须被拒（Shift 单独不算修饰键）", r is True, str(r))
r = expect_reject("f5")
check("裸 `f5` 必须被拒", r is True, str(r))

for spec in ("中", "あ", "é", "ctrl+中", "ctrl+空格"):
    r = expect_reject(spec)
    check(f"非 ASCII 主键 {spec!r} 必须被拒（ord() 出来是无效虚键码）",
          r is True, str(r))

r = expect_accept("ctrl+s", mods=G.MOD_CONTROL, vk=ord("S"))
check("`ctrl+s` 接受", r is True, str(r))
r = expect_accept("alt+s", mods=G.MOD_ALT, vk=ord("S"))
check("`alt+s` 接受（用户当前就在用这个）", r is True, str(r))
r = expect_accept("win+s", mods=G.MOD_WIN, vk=ord("S"))
check("`win+s` 接受", r is True, str(r))
r = expect_accept("ctrl+alt+s", mods=G.MOD_CONTROL | G.MOD_ALT, vk=ord("S"))
check("`ctrl+alt+s` 接受且 mods 正确", r is True, str(r))
r = expect_accept("ctrl+shift+f9", mods=G.MOD_CONTROL | G.MOD_SHIFT,
                  vk=G.VK_F1 + 8)
check("`ctrl+shift+f9` 接受且 F9 映射正确", r is True, str(r))
r = expect_accept("ctrl+1", vk=ord("1"))
check("数字主键 `ctrl+1` 接受", r is True, str(r))
r = expect_accept("CTRL + ALT + S")          # 大小写 + 空格
check("大小写与空格不敏感", r is True, str(r))

# 反向验证：证明上面那些"必须被拒"不是空转 —— 旧写法确实会放过它们
check("反向：旧判据 `isalnum()` 确实会放过 '中'（所以这条约束有意义）",
      "中".isalnum() and len("中") == 1)
check("反向：MAIN_KEYS 里不能有非 ASCII",
      all(c in "0123456789abcdefghijklmnopqrstuvwxyz" for c in G.MAIN_KEYS))
# 用 AST 精确判断「代码里有没有调用 .isalnum()」。
# 用字符串匹配会被注释和 docstring 误伤 —— 那些正是解释「为什么不用它」的说明。
import ast  # noqa: E402

_src_gui = open(os.path.join(ROOT, "swap_gui.py"), encoding="utf-8").read()
_calls = [n for n in ast.walk(ast.parse(_src_gui))
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "isalnum"]
check("源码里已不再调用 .isalnum()（注释/docstring 里的说明不算）",
      not _calls, f"仍有 {len(_calls)} 处")
check("改用 MAIN_KEYS 这张明确的表来判主键",
      "part in MAIN_KEYS" in _src_gui)

# ============================================================
section("② 计划任务 XML：exe 路径必须转义")

_orig_exe = G._task_exe
NASTY = r"C:\Tools\R&D\Swap <v2>\SwapMonitors.exe"
G._task_exe = lambda: NASTY
try:
    xml_text = G._task_xml()
finally:
    G._task_exe = _orig_exe

try:
    root = ET.fromstring(xml_text)
    parsed_ok = True
    err = ""
except ET.ParseError as e:
    root, parsed_ok, err = None, False, str(e)
check(f"含 & < > 的路径也能产出合法 XML（{NASTY}）", parsed_ok, err)

if parsed_ok:
    ns = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
    cmd = root.find(f".//{ns}Command")
    check("XML 里有 <Command> 节点", cmd is not None)
    if cmd is not None:
        check("解析回来的路径与原文一致（转义没有破坏内容）",
              cmd.text == NASTY, f"取回 {cmd.text!r}")

check("转义后出现 &amp; / &lt; / &gt;",
      "&amp;" in xml_text and "&lt;" in xml_text and "&gt;" in xml_text)

# 反向验证：不转义的话 XML 一定解析失败 —— 证明这个测试不是摆设
broken = xml_text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
try:
    ET.fromstring(broken)
    check("反向：不转义的 XML 应该解析失败（结果它成功了 → 用例无效）", False)
except ET.ParseError:
    check("反向：不转义的 XML 确实解析失败（说明这条防护有用）", True)

# 普通路径不能被改坏
G._task_exe = lambda: r"D:\SwapMonitors.exe"
try:
    plain = G._task_xml()
finally:
    G._task_exe = _orig_exe
check("普通路径不被改写", r"D:\SwapMonitors.exe" in plain)
check("_esc 对空值是安全的", G._esc(None) == "" and G._esc("") == "")

# ============================================================
section("③ 枚举回调：异常不能跑出去，且要落日志")

src_sw = open(os.path.join(ROOT, "swap_windows.py"), encoding="utf-8").read()
check("源码里 EnumWindows 回调有 try/except 外壳",
      "def cb(hwnd, lparam):" in src_sw and "return _cb(hwnd, lparam)" in src_sw)
check("回调出错后是 return True（继续枚举，而不是截断列表）",
      re.search(r"except Exception as e:.*?return True", src_sw, re.S) is not None)

# 实测：让 is_cloaked 对每个窗口都抛异常，枚举必须照样跑完且不抛
_log_dir = tempfile.mkdtemp(prefix="swapmon-log-")
_log_file = os.path.join(_log_dir, "error.log")
_orig_log, sw.LOG_PATH = sw.LOG_PATH, _log_file
_orig_cloaked = sw.is_cloaked


def boom(hwnd):
    raise RuntimeError("故意炸，看回调能不能兜住")


sw.is_cloaked = boom
try:
    mons = sw.enumerate_monitors()
    wins = sw.enumerate_windows(mons, re.compile(r"(?!x)x"))
    did_not_raise = True
except Exception as e:                       # noqa: BLE001
    did_not_raise = False
    wins = None
    print(f"     枚举竟然抛出了：{type(e).__name__}: {e}")
finally:
    sw.is_cloaked = _orig_cloaked
    sw.LOG_PATH = _orig_log

check("回调里抛异常时 enumerate_windows 不向外抛", did_not_raise)
check("异常被吞掉后列表为空（该窗口被跳过，不是被截断）",
      did_not_raise and wins == [], f"返回 {wins!r}")
check("错误写进了日志文件", os.path.exists(_log_file))
if os.path.exists(_log_file):
    body = open(_log_file, encoding="utf-8").read()
    check("日志内容含「枚举窗口」与异常类型",
          "枚举窗口" in body and "RuntimeError" in body, body[:120])
    check("日志带时间戳", re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", body)
          is not None)

# 反向验证：日志路径不可写时也不能把程序带崩
sw.LOG_PATH = os.path.join("Z:\\不存在的盘", "x", "error.log")
try:
    sw.log_error("写不进去也不许崩")
    check("反向：日志写不进去时静默吞掉、不抛异常", True)
except Exception as e:                       # noqa: BLE001
    check("反向：日志写不进去时静默吞掉、不抛异常", False, f"{type(e).__name__}: {e}")
sw.LOG_PATH = _orig_log

# 真正要测的是 log_error 会自己把目录建出来。原来的写法断言的是
# %APPDATA%\SwapMonitors 存在 —— 本机跑过程序所以它当然在，干净的 runner 上
# 就是假红（那目录根本没被写过）。改成指到一个多级都不存在的路径再写。
_nested_root = tempfile.mkdtemp(prefix="swapmon-nested-")
_nested_log = os.path.join(_nested_root, "a", "b", "error.log")
_orig_log2, sw.LOG_PATH = sw.LOG_PATH, _nested_log
try:
    sw.log_error("建目录")
    check("log_error 会自己建目录", os.path.isdir(os.path.dirname(_nested_log)))
    check("建完目录后日志真的写进去了", os.path.exists(_nested_log))
finally:
    sw.LOG_PATH = _orig_log2

# ============================================================
section("④ 非法正则：给干净提示，不许抛 re.error 崩掉")

PY = sys.executable
SCRIPT = os.path.join(ROOT, "swap_windows.py")


def run_cli(args, env=None):
    e = dict(os.environ)
    e["SWAPMONITORS_CONFIG"] = TEST_CFG
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    return subprocess.run([PY, SCRIPT] + args, capture_output=True,
                          timeout=120, env=e)


# swap() 一进来就先判显示器数量（<2 直接返回 1），走不到正则校验那一步。
# GitHub 的 runner 只有一块虚拟屏，所以「提示是人话」「合法正则照常工作」
# 这两类断言在单屏机器上必然假红 —— 显式跳过，别拿假绿掩盖。
_multi = (len(sw.enumerate_monitors()) >= 2
          and not os.environ.get("SWAPMON_FORCE_SINGLE"))

for flag in ("--exclude-title", "--only-title", "--only-proc"):
    r = run_cli(["--list", flag, "("])
    check(f"`{flag} (` 不崩溃（有返回码，无 traceback）",
          r.returncode in (0, 1, 2) and b"Traceback" not in r.stderr,
          f"rc={r.returncode} stderr={r.stderr[:160]!r}")
    if r.returncode != 0:
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        if _multi:
            check(f"`{flag} (` 的提示是人话（提到「正则写错了」）",
                  "正则写错了" in out, out[:160])
        else:
            skip(f"`{flag} (` 的提示是人话", "单屏机器走不到正则校验")

if _multi:
    r = run_cli(["--list", "--exclude-title", "示例"])
    check("合法正则照常工作", r.returncode == 0, f"rc={r.returncode}")
else:
    skip("合法正则照常工作", "单屏机器 --list 本来就返回 1")

# 反向验证：不兜住的话 re.error 确实会往外抛
try:
    re.compile("(")
    check("反向：`re.compile('(')` 应当抛 re.error", False)
except re.error:
    check("反向：`re.compile('(')` 确实抛 re.error（所以必须兜）", True)

check("源码里 re.compile 用户输入前包了 try",
      "except re.error as e:" in src_sw)

# ============================================================
section("⑤ 顺带：静态自检仍然干净")
import symtable  # noqa: E402

builtins_ = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
ALLOWED = set(builtins_) | {"__name__", "__file__", "__doc__", "__package__",
                            "__spec__", "__loader__", "__builtins__", "__debug__"}


def undefined_globals(path):
    src = open(path, encoding="utf-8").read()
    top = symtable.symtable(src, path, "exec")
    defined = {s.get_name() for s in top.get_symbols()
               if s.is_assigned() or s.is_imported()}
    bad = set()

    def walk(t):
        for s in t.get_symbols():
            n = s.get_name()
            if (s.is_referenced() and s.is_global()
                    and n not in defined and n not in ALLOWED):
                bad.add(n)
        for c in t.get_children():
            walk(c)

    walk(top)
    return sorted(bad)


for name in ("swap_gui.py", "swap_windows.py"):
    p = os.path.join(ROOT, name)
    check(f"{name} 没有「引用了却没定义」的全局名",
          not undefined_globals(p), str(undefined_globals(p)))

check("版本号已升到 1.1.7", G.APP_VER == "1.1.7", G.APP_VER)

# ============================================================
print()
if failures:
    print(f"❌ {len(failures)} 项失败：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("🎉 全部通过")
