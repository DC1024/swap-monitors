#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exe 冒烟：把打包出来的 SwapMonitors.exe 真跑一次，确认它起得来。

为什么需要这个文件：release.yml 里的「校验产物」只看 MZ / `PE\\0\\0` 头和文件大小。
那两项能挡住「打出来的根本不是 exe」，挡不住「是 exe 但一起动就崩」—— 少打一个
hidden import、tkinter 的 tcl 目录没进包、`--add-data` 的图标路径写错，PE 头全都
照样正常，用户双击才看到报错框。

怎么验才不算自欺欺人：跑 `--help`，要求退出码 0。
  · swap_gui.py **顶层** import tkinter，所以这一步顺带证明了 tkinter 在包里；
  · main() 里 `if argv: return sw.main(argv)` —— 带参数直接走 CLI，不建窗口、
    不抢全局热键、不碰单实例互斥体，headless 的 runner 上也能跑；
  · windowed 构建里 sys.stdout 是 None，但 argparse 的 _print_message 把
    AttributeError 吞掉了，仍然 exit(0)，不会因为「没控制台」而假红。

再加一条负对照：`--bogus` 必须返回 2。少了它，一个「不管传什么都 exit(0)」的坏包
也能通过上面那条 —— 退出码得先证明自己是个真信号。

超时是必须的：windowed 构建启动失败时 runw.exe 会弹一个 MessageBox 然后**一直
等人点**。没有 timeout 的话 CI 不会红，会卡到 job 超时（25 分钟），而且日志里
什么都看不到。

用法:
  python tests/test_exe_smoke.py                # 默认 dist/SwapMonitors.exe
  python tests/test_exe_smoke.py <exe 路径>     # 指定产物
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_EXE = os.path.join(ROOT, "dist", "SwapMonitors.exe")

# 一次 12MB onefile 的启动在冷机器上是几秒级；180 秒足够区分「慢」和「卡死」，
# 又不至于把 25 分钟的 job 预算吃掉多少。
TIMEOUT = 180

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


def run(exe, *args):
    """跑一次 exe。返回 (退出码, 说明)；起不来或超时则退出码为 None。

    起不来要单独接住：文件不是 PE 时 Windows 直接抛 OSError(WinError 193)，
    让它冒出去就变成 traceback，看着像脚本自己坏了，而不是产物坏了。
    """
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=TIMEOUT)
        return r.returncode, ""
    except subprocess.TimeoutExpired:
        return None, f"{TIMEOUT} 秒没退出 —— 多半是启动就崩，runw.exe 弹了错误框在等人点"
    except OSError as e:
        return None, f"{type(e).__name__}: {e}"


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXE

    if os.name != "nt":
        skip("整个文件", "产物是 Windows exe，非 Windows 上没法跑")
        print("\n全部跳过 ⏭")
        return 0

    if not os.path.isfile(exe):
        # 「没打包」不是「打包坏了」，不该判红 —— 本地跑全套自测的人大多没建过 exe。
        # 发版流程里这条不会走到：前面「校验产物」那步已经是硬门槛，文件不在就红了。
        skip("整个文件", f"没找到 {exe}，先跑 build_exe.bat")
        print("\n全部跳过 ⏭")
        return 0

    print(f"产物：{exe}（{os.path.getsize(exe):,} 字节）\n")

    print("== --help ==")
    rc, why = run(exe, "--help")
    check("--help 能退出（不卡在错误框上）", rc is not None, why)
    if rc is None:
        # 已经卡死了，后面的负对照没有意义，直接收尾
        print()
        return report()
    check("--help 退出码为 0（能启动；tkinter 与 swap_windows 都在包里）",
          rc == 0, f"rc={rc}")
    print()

    print("== --bogus（负对照）==")
    rc2, why2 = run(exe, "--bogus")
    check("--bogus 能退出", rc2 is not None, why2)
    if rc2 is not None:
        # argparse 对未知参数固定 exit(2)。这条同时证明「退出码不是恒 0」——
        # 否则上一条的 0 分不清是「跑通了」还是「什么都没做就退出了」。
        check("--bogus 退出码为 2（说明退出码是真信号）", rc2 == 2, f"rc={rc2}")
    print()
    return report()


def report():
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        print("\n这个 exe 不要发出去。")
        return 1
    if SKIPS:
        print(f"全部通过 ✅（{len(SKIPS)} 项因环境跳过）")
    else:
        print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    # runner 是英文系统，cp1252 编不出 ✅ —— 只把编不出的字符替换掉，
    # 不改编码（控制台是 UTF-8 时照样原样输出）。
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:                                        # noqa: BLE001
        pass
    sys.exit(main())
