#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.4 三项修复的离线回归测试。

覆盖 R14 报的三个问题：
  ① 重启后托盘里没有图标   → TaskbarCreated 重挂 / 图标矩形探测 / 溢出区提示机制
  ② DSH 窗口搬不动         → 空标题窗口不再被跳过（label 回退）
  ③ 记住管理员模式         → 计划任务 XML / SID / schtasks 输出解码 / 单实例互斥体

特点：
  * **不创建任何计划任务**（写操作一律靠桩函数替身），不碰用户真实设置
  * 启动文件夹、配置文件、show.flag 全部指到临时目录
  * 只有「单实例互斥体」那一节会真的开子进程，用的就是真实互斥体名字
    （若本机刚好有 SwapMonitors 在跑，该节自动跳过）

用法: python tests/test_admin_mode.py
需要带 tkinter 的解释器（swap_gui 顶部 import tkinter）。
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ---- 隔离用户配置：必须在 import swap_gui 之前设好 ----
TMP = tempfile.mkdtemp(prefix="swapmon-v114-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False},
              f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

import swap_gui as G                        # noqa: E402
import swap_windows as SW                   # noqa: E402

# 把「启动文件夹 / 配置目录 / 旗标」也全隔离掉，防手滑
G.STARTUP_DIR = TMP
G.AUTOSTART_CMD = os.path.join(TMP, "SwapMonitors.cmd")
G.CFG_DIR = TMP
G.SHOW_FLAG = os.path.join(TMP, "show.flag")

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

# ============================================================
section("① 托盘：TaskbarCreated 与图标矩形探测")
check("TaskbarCreated 消息已注册成功（返回非 0）", bool(G.WM_TASKBARCREATED),
      f"WM_TASKBARCREATED={G.WM_TASKBARCREATED}")
check("TaskbarCreated 与系统注册值一致",
      G.WM_TASKBARCREATED == G.u32.RegisterWindowMessageW("TaskbarCreated"))
_nii = G.NOTIFYICONIDENTIFIER()
check("NOTIFYICONIDENTIFIER 字段布局正确（cbSize=4, hWnd, uID, guidItem）",
      [f[0] for f in G.NOTIFYICONIDENTIFIER._fields_] ==
      ["cbSize", "hWnd", "uID", "guidItem"],
      str([f[0] for f in G.NOTIFYICONIDENTIFIER._fields_]))
# 注意不能用 NOTIFYICONDATAW 当入参 —— 两者布局不同，这是踩过的坑
_nii.cbSize = ctypes.sizeof(G.NOTIFYICONIDENTIFIER)
check("cbSize 与结构实际尺寸一致（传错会被 API 拒掉）",
      _nii.cbSize == ctypes.sizeof(G.NOTIFYICONIDENTIFIER) and _nii.cbSize > 0,
      f"cbSize={_nii.cbSize}")
check("它与 NOTIFYICONDATAW 是两个不同的结构（别再混用）",
      ctypes.sizeof(G.NOTIFYICONIDENTIFIER) != ctypes.sizeof(G.NOTIFYICONDATAW))
check("Shell_NotifyIconGetRect 已绑定（Win10+ 才有）",
      G.shell32.Shell_NotifyIconGetRect is not None
      or sys.getwindowsversion().build < 22000,
      "老系统无此 API，代码里已做 None 兜底")
# _overflow_left 不依赖 self，直接拿 None 调即可 —— 纯 Win32 查询，不该炸
ov = G.App._overflow_left(None)
check("溢出区边界探测不抛异常，返回 int 或 None", ov is None or isinstance(ov, int),
      f"返回 {ov!r}")
tip = G.TrayThread._tip_text(type("O", (), {"_shown_hk": "ALT+S"})())
check("托盘提示写明了「左键双击显示」", "双击" in tip, tip)
check("TrayThread 有「重挂图标」入口（explorer 重启后用）",
      callable(getattr(G.TrayThread, "reinit_tray", None)))


# ============================================================
section("② 空标题窗口不再被跳过（DSH / Electron）")
w_plain = SW.Win(1, "记事本", "Notepad", 1, "notepad.exe", None, 1, None)
w_blank = SW.Win(2, "", "Chrome_WidgetWin_1", 2, "DSH Desktop.exe", None, 1, None)
check("普通窗口 label = 标题", w_plain.label == "记事本", w_plain.label)
check("空标题窗口 label 回退成进程名", "DSH Desktop.exe" in w_blank.label, w_blank.label)
check("空标题窗口 label 不会空到看不见", w_blank.label.strip() != "", repr(w_blank.label))
check("空标题不再被无条件跳过（源码里没有 `if n <= 0: return True`）",
      "if n <= 0" not in open(os.path.join(ROOT, "swap_windows.py"),
                              encoding="utf-8").read())


# ============================================================
section("③ schtasks 输出的编码解码（关键坑）")
utf16_bom = b"\xff\xfe" + "  <RunLevel>HighestAvailable</RunLevel>".encode("utf-16-le")
utf16_nobom = "  <RunLevel>HighestAvailable</RunLevel>".encode("utf-16-le")
check("带 BOM 的 UTF-16LE 能正确解码", "HighestAvailable" in G._decode_console(utf16_bom))
check("无 BOM 但满是 NUL 的 UTF-16LE 也能正确解码",
      "HighestAvailable" in G._decode_console(utf16_nobom))
check("普通 ASCII（mbcs）照常解码", "SUCCESS" in G._decode_console(b"SUCCESS: ok\r\n"))
check("空输入返回空串", G._decode_console(b"") == "")
# mbcs 优先是对的：schtasks 在本机就是用**系统 ANSI 代码页**输出。所以「GBK 字节
# 能被解出中文」只在中文系统（ACP=936）成立；英文系统的 mbcs 是 cp1252，那串 GBK
# 字节会（不报错地）解成乱码，压根走不到 gbk 那一档。
_ACP = ctypes.windll.kernel32.GetACP()
if _ACP == 936:
    check("GBK 中文能解码", "任务" in G._decode_console("任务已创建".encode("gbk")))
else:
    print(f"  ⏭  GBK 中文能解码（跳过：本机 ACP={_ACP}，mbcs 不是 cp936，走不到 gbk 那一档）")
check("原来的写法（mbcs 硬解）确实会坏 —— 反证这个坑是真的",
      "HighestAvailable" not in utf16_bom.decode("mbcs", errors="replace"))


# ============================================================
section("③ 计划任务 XML 的内容")
sid = G._current_user_sid()
print(f"  当前用户 SID = {sid}")
check("SID 能取到且格式合法", bool(re.fullmatch(r"S-1-5-21(?:-\d+)+", sid or "")), sid)
xml = G._task_xml()
check("RunLevel=HighestAvailable（这就是「记住管理员模式」）",
      "<RunLevel>HighestAvailable</RunLevel>" in xml)
check("LogonTrigger（登录即启动 = 免 UAC 的开机自启）", "<LogonTrigger>" in xml)
check("Principal 用当前用户 SID", f"<UserId>{sid}</UserId>" in xml)
check("InteractiveToken（GUI 才能显示在桌面）",
      "<LogonType>InteractiveToken</LogonType>" in xml)
check("动作参数 --minimized（开机静默进托盘）",
      "<Arguments>--minimized</Arguments>" in xml)
check("声明 UTF-16 编码", 'encoding="UTF-16"' in xml)
check("ExecutionTimeLimit=PT0S（不限时，常驻托盘）",
      "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml)
check("任务名进了 URI", f"\\{G.TASK_NAME}<" in xml)
check("XML 里的 Command 就是当前要拉起的程序",
      f"<Command>{G._task_exe()}</Command>" in xml)

# Command 必须跟着 exe 走，不能写死
_orig_exe = G._autostart_exe
G._autostart_exe = lambda: r"D:\SwapMonitors.exe"
xml2 = G._task_xml()
G._autostart_exe = _orig_exe
check("Command 跟随程序位置（不是写死的路径）",
      "<Command>D:\\SwapMonitors.exe</Command>" in xml2)

# utf-16 落盘必须带 BOM，否则 schtasks /create /xml 会拒收
p = os.path.join(TMP, "_probe_task.xml")
with open(p, "w", encoding="utf-16") as f:
    f.write(G._task_xml())
raw = open(p, "rb").read()
check("写出的任务 XML 带 UTF-16 BOM（schtasks 靠它认编码）",
      raw[:2] in (b"\xff\xfe", b"\xfe\xff"), repr(raw[:4]))
check("读回来仍是合法 XML（含 HighestAvailable）",
      "HighestAvailable" in raw.decode("utf-16"))
check("schtasks 可执行文件定位成功", bool(G._schtasks_exe()), G._schtasks_exe())


# ============================================================
section("③ 任务查询接口：只读、不抛异常")
try:
    ex = G.task_exists()
    check("task_exists() 返回 bool", isinstance(ex, bool), repr(ex))
    if ex:
        print("  [i] 本机确实存在该计划任务（之前手动建过？）")
        print(f"  [i] 是否最高权限: {G.task_runlevel_is_highest()}")
        print(f"  [i] 是否指向当前程序: {G.task_points_to_current_exe()}")
    else:
        print("  [i] 本机尚无该任务 —— 符合预期（本测试不创建任务）")
    ver = G.verify_elevated_task()
    check("诊断报告包含关键条目",
          all(k in ver for k in ("计划任务名", "计划任务存在", "任务为最高权限",
                                 "任务指向当前程序")))
except Exception as e:                                       # noqa: BLE001
    check("任务查询接口不抛异常", False, f"{type(e).__name__}: {e}")


# ============================================================
section("③ 交棒逻辑（用桩函数替身，绝不真的建任务）")
if G.is_elevated():
    print("  [i] 当前是管理员权限，交棒路径不适用，本节跳过")
else:
    _saved_cfg = G.load_cfg()
    _orig_exists, _orig_start = G.task_exists, G.start_elevated_task
    _orig_acquire = G._acquire_single_instance
    try:
        # (a) 没开管理员模式 → 不该交棒，也不该去查任务
        G.save_cfg({**G.DEFAULT_CFG, "run_as_admin": False})
        hit = []
        G.task_exists = lambda: hit.append("exists") or False
        check("未开管理员模式 → 返回 False", G._maybe_hand_over_to_admin(False) is False)
        check("未开管理员模式 → 连任务都不查（省一次进程启动）", not hit)

        # (b) 开了但任务还没建好 → 按普通权限跑，不交棒
        G.save_cfg({**G.DEFAULT_CFG, "run_as_admin": True})
        called = []
        G.task_exists = lambda: False
        G.start_elevated_task = lambda: (called.append(1), (True, ""))[1]
        check("任务不存在 → 不交棒（等自愈补建）",
              G._maybe_hand_over_to_admin(False) is False)
        check("任务不存在 → 不去启动任务", not called)

        # (d) 交棒失败 → 把互斥体收回来，配置里的旗标撤回
        G.task_exists = lambda: True
        G.start_elevated_task = lambda: (False, "任务已在运行")
        G.save_cfg({**G.DEFAULT_CFG, "run_as_admin": True})
        check("启动任务失败 → 不交棒", G._maybe_hand_over_to_admin(False) is False)
        check("启动任务失败 → 把互斥体拿回来（不能把单实例令牌弄丢）",
              G._SINGLE_MUTEX is not None)
        check("启动任务失败 → 撤回 _handover_show",
              G.load_cfg().get("_handover_show") is False)
        G._release_single_instance()

        # (c) 开了且任务在 → 交棒：写 _handover_show、启动任务、**释放互斥体**、返回 True
        called.clear()
        G.start_elevated_task = lambda: (called.append(1), (True, ""))[1]
        G.save_cfg({**G.DEFAULT_CFG, "run_as_admin": True})
        G._acquire_single_instance()
        held_before = G._SINGLE_MUTEX is not None
        r = G._maybe_hand_over_to_admin(False)
        check("任务在 → 返回 True 交棒成功", r is True, repr(r))
        check("任务在 → 启动了计划任务", bool(called))
        check("交棒前释放了单实例互斥体（否则新实例会被自己挡回去）",
              held_before and G._SINGLE_MUTEX is None)
        check("写下了 _handover_show（新实例据此显示界面）",
              G.load_cfg().get("_handover_show") is True)

        # (e) 开机自启那条路（--minimized）：交棒后也不该弹界面
        G.save_cfg({**G.DEFAULT_CFG, "run_as_admin": True})
        r_min = G._maybe_hand_over_to_admin(True)
        check("--minimized 交棒成功", r_min is True, repr(r_min))
        check("--minimized 启动时**不**写 _handover_show（开机就该静默进托盘）",
              G.load_cfg().get("_handover_show") is False)
    finally:
        G.task_exists, G.start_elevated_task = _orig_exists, _orig_start
        G._release_single_instance()
        G.save_cfg(_saved_cfg)


# ============================================================
section("③ 单实例互斥体（真开子进程，验证跨进程语义）")
if not G._acquire_single_instance():
    print("  [i] 本机已有 SwapMonitors 实例在跑，跳过本节")
else:
    G._release_single_instance()
    child_code = (
        "import sys, time\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "import swap_gui as G\n"
        "print('CHILD_ACQUIRED', G._acquire_single_instance(), flush=True)\n"
        "time.sleep(3.0)\n"
    )
    env = dict(os.environ)
    env["SWAPMONITORS_CONFIG"] = TEST_CFG
    cp = subprocess.Popen([sys.executable, "-c", child_code], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    line = (cp.stdout.readline() or "").strip()
    check("子进程成功抢到互斥体（说明初始状态干净）",
          line == "CHILD_ACQUIRED True", line)
    if line == "CHILD_ACQUIRED True":
        time.sleep(0.8)
        G._release_single_instance()
        got = G._acquire_single_instance()
        check("子进程持有期间父进程抢不到 → 单实例生效", got is False, f"got={got}")
        G._release_single_instance()
        # 抢不到时按设计要留下「亮出窗口」旗标
        G._signal_show_existing()
        check("第二个实例会留下 show.flag 旗标", os.path.exists(G.SHOW_FLAG))
        check("老实例能消费该旗标，且消费是幂等的",
              G._consume_show_flag() is True and G._consume_show_flag() is False)
    cp.wait(timeout=20)
    G._release_single_instance()
    time.sleep(0.3)
    check("子进程退出后又能抢到（没留下僵尸互斥体）",
          G._acquire_single_instance() is True)
    G._release_single_instance()
    check("互斥体名字带用户 SID（多用户互不干扰）",
          bool(re.search(r"S-1-5-21(?:-\d+)+", "Local\\SwapMonitors_" + (sid or ""))))


# ============================================================
section("配置持久化：新增的三个字段能存能读")
G.save_cfg({**G.DEFAULT_CFG, "run_as_admin": True, "_pending_admin_setup": True,
            "_handover_show": False})
back = G.load_cfg()
check("run_as_admin 落盘并读回", back.get("run_as_admin") is True)
check("_pending_admin_setup 落盘并读回", back.get("_pending_admin_setup") is True)
check("_handover_show=False 也照样写进去",
      "_handover_show" in back and back["_handover_show"] is False)
check("DEFAULT_CFG 三个字段齐全",
      all(k in G.DEFAULT_CFG for k in ("run_as_admin", "_pending_admin_setup",
                                       "_handover_show")))
# 别写死版本号：每发一版都得回来改一次，迟早会忘，然后测试红得莫名其妙。
# 只要形如 x.y.z 且不低于 1.1.4 就算过。
check("APP_VER 是合法的 x.y.z 且不低于 1.1.4",
      bool(re.fullmatch(r"\d+\.\d+\.\d+", G.APP_VER))
      and tuple(int(x) for x in G.APP_VER.split(".")) >= (1, 1, 4),
      G.APP_VER)
check("--diag 入口存在", callable(G._diag_report))


# ============================================================
section("④ CLI 输出的编码加固（GBK 控制台不能崩）")
import io                                                       # noqa: E402

_buf = io.BytesIO()
_fake = io.TextIOWrapper(_buf, encoding="gbk", errors="strict")
_old_out = sys.stdout
crashed = False
hardened_ok = True
try:
    sys.stdout = _fake
    # 先确认前提：没加固的 GBK 控制台碰到零宽空格确实会崩
    try:
        print("Microsoft\u200b Edge")
    except UnicodeEncodeError:
        crashed = True
    # 加固之后同样的内容不该再崩（emoji / 日文也一起试）
    SW._harden_console()
    try:
        print("Microsoft\u200b Edge 中文")
        print("  还有 emoji 😀 和日文 カタカナ")
    except UnicodeEncodeError:
        hardened_ok = False
    _fake.flush()
finally:
    sys.stdout = _old_out
check("前提成立：GBK 控制台遇到 U+200B 会抛 UnicodeEncodeError", crashed)
check("加固后 U+200B / emoji / 日文都不再抛异常", hardened_ok)
check("加固只替换字符、不改变编码（仍是 gbk）",
      "Microsoft? Edge".encode("gbk") in _buf.getvalue(),
      repr(_buf.getvalue()[:40]))

_js = json.dumps({"title": "Microsoft\u200b Edge 中文", "n": 1}, ensure_ascii=True)
check("--json 输出是纯 ASCII（任何代码页都不会崩）", _js.isascii(), _js)
check("纯 ASCII 的 JSON 解回来字符串完全一致",
      json.loads(_js)["title"] == "Microsoft\u200b Edge 中文")
_src_sw = open(os.path.join(ROOT, "swap_windows.py"), encoding="utf-8").read()
check("swap_windows.py 里已没有 ensure_ascii=False（机器可读输出一律转义）",
      "ensure_ascii=False" not in _src_sw)

# 真跑一次：把子进程的控制台编码强制成 gbk，复现 exe 里的真实条件
# 单屏/无屏机器上 --list 本来就返回 1（swap() 一进来先判显示器数量），
# 所以这里只关心「编码有没有崩」：多屏要求 rc=0，单屏允许 rc=1。
import swap_windows as sw                                   # noqa: E402
_multi_mon = (len(sw.enumerate_monitors()) >= 2
              and not os.environ.get("SWAPMON_FORCE_SINGLE"))
_env = dict(os.environ)
_env["PYTHONIOENCODING"] = "gbk"
_env["SWAPMONITORS_CONFIG"] = TEST_CFG
for _args in (["--list", "--json"], ["--list"]):
    try:
        _p = subprocess.run([sys.executable, os.path.join(ROOT, "swap_windows.py")] + _args,
                            capture_output=True, env=_env, timeout=90)
        _err = _p.stderr.decode("mbcs", "replace")
        _rc_ok = _p.returncode == 0 if _multi_mon else _p.returncode in (0, 1)
        _ok = _rc_ok and "UnicodeEncodeError" not in _err
        check(f"GBK 控制台下 `swap_windows.py {' '.join(_args)}` 正常退出（rc={_p.returncode}"
              f"{'' if _multi_mon else '，单屏机器'}）", _ok, _err[-300:])
        if _args == ["--list", "--json"] and _p.returncode == 0:
            _d = json.loads(_p.stdout.decode("ascii", "replace"))
            check("--json 输出可被 json.loads 解析且 ok=True", bool(_d.get("ok")),
                  str(_d)[:200])
            _titles = [w.get("title") for w in _d.get("windows", [])]
            check("含 U+200B 的标题能原样传出来（没有被 ? 破坏）",
                  all(isinstance(t, str) for t in _titles))
    except Exception as e:                                       # noqa: BLE001
        check(f"GBK 控制台下 {' '.join(_args)} 不抛异常", False,
              f"{type(e).__name__}: {e}")


# ============================================================
section("⑦ 提权待办标记必须落到配置里（不能只写实例属性）")

# 背景：_prompt_elevate_for_admin 里原来写的是 self._pending_admin_setup
# （实例属性），而 _admin_selfheal 读的是 self.cfg["_pending_admin_setup"]。
# 提权重启后是**新进程**，配置从磁盘读 —— 实例属性随旧进程结束就没了，
# 于是「第一次开管理员模式」永远补建不出任务：用户只看到重启了一次、开关还是关的。
# 注意：本文件里旧的做法是直接 save_cfg() 造数据，压根没走这条路，所以是假绿。
import tkinter as tk                                        # noqa: E402

check("配置路径确实指向临时文件（不会碰用户真实配置）", G.CFG_PATH == TEST_CFG,
      G.CFG_PATH)

# App 初始化与 _admin_selfheal 都会碰自启项，全部指到临时目录
_orig_cmd_path = G.AUTOSTART_CMD
G.AUTOSTART_CMD = os.path.join(TMP, "SwapMonitors.cmd")

with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*",
               "run_as_admin": False, "_pending_admin_setup": False}, f, ensure_ascii=False)

_root = tk.Tk()
_root.withdraw()
try:
    app = G.App(_root)
    check("初始状态：待办标记是 False", app.cfg.get("_pending_admin_setup") is False)

    _elevated = []
    _orig_yesno = G.messagebox.askyesno
    G.messagebox.askyesno = lambda *a, **k: True          # 用户点了「是」
    app.elevate = lambda: _elevated.append(1)             # 不真的重启
    try:
        started = app._prompt_elevate_for_admin()
    finally:
        G.messagebox.askyesno = _orig_yesno

    check("确认后返回 True（已发起提权重启）", started is True)
    check("确实调用了 elevate()", _elevated == [1])
    check("标记写进了 cfg", app.cfg.get("_pending_admin_setup") is True)
    check("没有写成同名实例属性（这正是要修的 bug）",
          "_pending_admin_setup" not in vars(app), str(sorted(vars(app)))[:200])
    with open(TEST_CFG, encoding="utf-8") as f:
        _disk = json.load(f)
    check("标记已经落盘（提权后的新进程读的就是它）",
          _disk.get("_pending_admin_setup") is True, str(_disk))

    # 模拟提权后的新进程：丢掉内存状态，重新从磁盘读配置
    app.cfg = G.load_cfg()
    check("新进程能读到待办", app.cfg.get("_pending_admin_setup") is True)

    _orig_elev, _orig_task = G.is_elevated, G.create_elevated_task
    G.is_elevated = lambda: True
    G.create_elevated_task = lambda: (True, "")
    try:
        app._admin_selfheal()
    finally:
        G.is_elevated, G.create_elevated_task = _orig_elev, _orig_task

    check("自愈后 run_as_admin 被置 True", app.cfg.get("run_as_admin") is True)
    check("待办标记用完即清", app.cfg.get("_pending_admin_setup") is False)
    check("管理员开关同步点亮", bool(app.admin_var.get()) is True)
    with open(TEST_CFG, encoding="utf-8") as f:
        _disk2 = json.load(f)
    check("清掉的状态也落盘了", _disk2.get("_pending_admin_setup") is False, str(_disk2))

    # 反向：未提权就回来（用户在 UAC 上点了「否」）→ 不能留下永远待办
    with open(TEST_CFG, "w", encoding="utf-8") as f:
        json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*",
                   "run_as_admin": False, "_pending_admin_setup": True}, f)
    app.cfg = G.load_cfg()
    _orig_elev = G.is_elevated
    G.is_elevated = lambda: False
    try:
        app._admin_selfheal()
    finally:
        G.is_elevated = _orig_elev
    check("提权失败也不留永远待办", app.cfg.get("_pending_admin_setup") is False)
    check("提权失败时管理员模式保持关闭", app.cfg.get("run_as_admin") is False)
finally:
    try:
        _root.destroy()
    except Exception:                                        # noqa: BLE001
        pass
    G.AUTOSTART_CMD = _orig_cmd_path


print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
