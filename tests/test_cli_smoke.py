#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 冒烟：真跑一次 --list / --dry-run，确认输出能被机器消费。

为什么单独一个文件：这两条命令在**单屏 / 无屏机器上本来就返回 1**
（swap() 一进来先判显示器数量，<2 直接返回），而 GitHub 的 runner 只有一块
虚拟屏、甚至是 0 块。所以「压根没有 JSON 可验」必须显式跳过，不能让它把
CI 搞红，也不能让它伪装成通过。

要验的其实是三件事：
  ① 不崩（没有 traceback）
  ② 退出码是 0（多屏机器上）
  ③ 输出是**纯 ASCII 的合法 JSON** —— 机器可读输出一律转义，任何代码页都不崩

用法: python tests/test_cli_smoke.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import swap_windows as sw      # noqa: E402

FAILS = []
SKIPS = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def skip(name, why):
    print(f"  ⏭  {name}（跳过：{why}）")
    SKIPS.append(name)


CFG = os.path.join(tempfile.mkdtemp(prefix="swapmon-cli-"), "config.json")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", SWAPMONITORS_CONFIG=CFG)


def run(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "swap_windows.py"), *args],
                          capture_output=True, env=ENV, timeout=120)


n_mon = len(sw.enumerate_monitors())
multi = n_mon >= 2
print(f"（本机 {n_mon} 块屏 → {'多屏，按完整流程验' if multi else '单屏/无屏，只验不崩'}\n")

seen = {}          # key -> 解析后的 payload，最后用来对两份输出做交叉检查

for args, keys in ((["--list", "--json"], ("monitors", "windows")),
                   (["--dry-run", "--json"], ("plan",))):
    label = " ".join(args)
    print(f"== {label} ==")
    r = run(*args)
    err = r.stderr.decode("utf-8", "replace")

    check(f"{label} 不崩（无 traceback）", b"Traceback" not in r.stderr, err[-300:])

    if r.returncode != 0:
        if not multi and "只检测到" in err:
            skip(f"{label} 的 JSON 内容", f"本机 {n_mon} 块屏，swap() 提前返回 {r.returncode}")
        else:
            check(f"{label} 退出码为 0", False, f"rc={r.returncode} {err[-300:]}")
        print()
        continue

    check(f"{label} 退出码为 0", True)
    check(f"{label} 输出是纯 ASCII（任何代码页都不会崩）", r.stdout.isascii(),
          r.stdout[:160].decode("utf-8", "replace"))
    try:
        d = json.loads(r.stdout.decode("ascii"))
    except Exception as e:                                    # noqa: BLE001
        check(f"{label} 输出可被 json.loads 解析", False, f"{type(e).__name__}: {e}")
        print()
        continue
    check(f"{label} 输出可被 json.loads 解析", True)
    check(f"{label} 带 ok=True", d.get("ok") is True, str(d)[:200])
    for k in keys:
        check(f"{label} 含 {k} 列表", isinstance(d.get(k), list), str(d)[:200])
    seen[label] = d
    print()

# 两份输出必须能用 hwnd 对上：GUI 的「预览计划」就是拿 windows[].hwnd 当表格行 id，
# 再用 plan[].hwnd 把「互换后」填回去。少了这个字段，预览列只能是空白。
if len(seen) == 2:
    print("== windows ↔ plan 的 hwnd 契约 ==")
    wins = seen["--list --json"]["windows"]
    plan = seen["--dry-run --json"]["plan"]
    check("windows[].hwnd 都是整数", all(isinstance(w.get("hwnd"), int) for w in wins),
          str([w.get("hwnd") for w in wins])[:200])
    check("plan[].hwnd 都是整数", all(isinstance(x.get("hwnd"), int) for x in plan),
          str([x.get("hwnd") for x in plan])[:200])
    check("plan 里的每个 hwnd 都能在 windows 里找到",
          {x["hwnd"] for x in plan} <= {w["hwnd"] for w in wins},
          str({x["hwnd"] for x in plan} - {w["hwnd"] for w in wins}))
    print()

print()
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print(f"   - {f}")
    sys.exit(1)
if SKIPS:
    print(f"全部通过 ✅（{len(SKIPS)} 项因环境跳过）")
else:
    print("全部通过 ✅")
