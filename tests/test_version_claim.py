#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README / build_exe.bat / CI 三处说的 Python 版本必须是同一个区间。

背景：README 写「Python 3.8+」，build_exe.bat 写「CPython 3.10-3.13」，CI 只跑
3.12 —— 三句话各说各的。而 3.8 那个下限从来没有任何 CI 背书：33 个 .py 在 3.8
语法下确实能解析（`ast.parse(feature_version=(3, 8))` 全通过，也没用
`removeprefix` 这类 3.9 才有的 API），但没人跑过测试。写一个没人验证过的下限，
等于给用户一个假的保证。

于是把口径统一到 3.10–3.13（PyInstaller 的支持范围），并写这个测试盯住三件事：

  1. README（运行 / 打包两节）、build_exe.bat、两个 workflow 说的是**同一个区间**；
  2. CI 实际跑的那个版本，落在声明的区间**里面**；
  3. 所有 .py 在**声明的下限**语法下仍能解析 —— 有人手滑用了新语法（比如 3.12 的
     `def f[T]()`），这条立刻变红，而不是等用户装 3.10 才发现跑不起来。

第 3 条才是这个测试的真正价值：前两条只是防「文档互相打架」，第 3 条防「文档说的
话已经不成立」。

⚠️ 这个测试**只管语法，不管标准库**。`ast.parse(feature_version=...)` 能拦住
`match`、`except*`、PEP 695 这类语法级差异（已实测：match 在 3.8 被拒 / 3.10 通过，
except* 与 `def f[T]()` 在 3.12 才通过），但拦不住「语法一直是老的、API 是 3.11 才
有的」——比如 `typing.Self`、`asyncio.TaskGroup`。要覆盖那种情况只能真的在 3.10 上
跑一遍测试，也就是往 CI 矩阵里加版本。别把这里的绿灯当成「3.10 一定能跑」。

用法: python tests/test_version_claim.py
"""
import ast
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def normalize(text):
    """把各种破折号统一成 ASCII 连字符，免得 3.10–3.13 / 3.10-3.13 被当成两个东西。"""
    for ch in "\u2013\u2014\u2212":        # en dash / em dash / minus sign
        text = text.replace(ch, "-")
    return text


RANGE_RE = re.compile(r"3\.(\d+)\s*-\s*3\.(\d+)")


def first_range(text, label):
    """取第一处 a.b-c.d 形式的版本区间，返回 (下限, 上限) 或 None。"""
    m = RANGE_RE.search(normalize(text))
    if not m:
        print(f"  ⚠️  {label} 里没找到 a.b-c.d 形式的版本区间")
        return None
    return int(m.group(1)), int(m.group(2))


print("== 三处声明的版本区间 ==")

readme = read("README.md")
bat = read("build_exe.bat")
ci_yml = read(".github", "workflows", "ci.yml")
rel_yml = read(".github", "workflows", "release.yml")

# README 有两节各自声明，分开看，别让「从源码运行」那节替「自己打包 exe」背书
i_run = readme.find("## 从源码运行")
i_pack = readme.find("## 自己打包 exe")
run_sec = readme[i_run:i_pack] if -1 not in (i_run, i_pack) else ""
pack_sec = readme[i_pack:i_pack + 1200] if i_pack != -1 else ""

run_rng = first_range(run_sec, "README「从源码运行」")
pack_rng = first_range(pack_sec, "README「自己打包 exe」")
bat_rng = first_range(bat, "build_exe.bat")

check("README「从源码运行」写了版本区间", run_rng is not None)
check("README「自己打包 exe」写了版本区间", pack_rng is not None)
check("build_exe.bat 写了版本区间", bat_rng is not None)

if run_rng and bat_rng:
    check("README 与 build_exe.bat 是同一个区间",
          run_rng == bat_rng, f"README={run_rng} bat={bat_rng}")
if pack_rng and run_rng:
    check("README 的打包节与运行节是同一个区间",
          pack_rng == run_rng, f"运行={run_rng} 打包={pack_rng}")

lo, hi = run_rng or (0, 0)
if lo:
    check(f"下限不是已 EOL 的版本（当前 3.{lo}）", lo >= 10,
          f"3.{lo} 已过 EOL，若要重新支持请先往 CI 矩阵里加它")

print("\n== CI 实际跑的版本必须落在区间里 ==")
if not lo:
    # 拿不到区间就别硬编断言：拿 0 去填只会产出「3.0-3.0」这种误导性结论
    print("  ⏭️  跳过（README 没有可用的版本区间，上面已经报过错了）")
else:
    for label, text in (("ci.yml", ci_yml), ("release.yml", rel_yml)):
        m = re.search(r'python-version:\s*"3\.(\d+)"', text)
        if not m:
            check(f"{label} 里能找到 python-version", False)
            continue
        minor = int(m.group(1))
        check(f"{label} 的 3.{minor} 在 3.{lo}-3.{hi} 之内",
              lo <= minor <= hi, f"区间是 3.{lo}-3.{hi}")

print("\n== 代码在声明的下限语法下必须还能解析 ==")
if not lo:
    print("  ⏭️  跳过（同上）")
else:
    files = sorted(glob.glob(os.path.join(ROOT, "*.py"))
                   + glob.glob(os.path.join(ROOT, "tests", "*.py")))
    bad = []
    for path in files:
        src = open(path, encoding="utf-8", errors="replace").read()
        try:
            ast.parse(src, filename=path, feature_version=(3, lo))
        except SyntaxError as exc:
            bad.append(f"{os.path.relpath(path, ROOT)}:{exc.lineno} {exc.msg}")

    check(f"{len(files)} 个 .py 在 3.{lo} 语法下全部可解析",
          not bad, "; ".join(bad[:4]))

print()
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
if not lo:
    # 没有区间就没法给「统一在哪个范围」下结论，别印一句好听的空话
    sys.exit(1)
print(f"全部通过 ✅ —— 版本口径统一在 3.{lo}-3.{hi}，代码也确实跑得起来")
