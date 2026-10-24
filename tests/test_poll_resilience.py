#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面心跳 _poll() 的抗异常能力回归测试。

背景（v1.1.7 的 bug）：_poll 是 Tk 的 after 回调，80ms 一次，负责消费后台
线程的消息（热键触发、托盘菜单、交换结果）。它长这样：

    def _poll(self):
        if _consume_show_flag(): ...        # ← 任何一行都可能抛
        ...
        try:
            while True: ...                 # 只兜住了 queue.Empty
        except queue.Empty:
            pass
        self.root.after(80, self._poll)     # ← 抛了就永远执行不到

after 回调抛异常时，Tk 只把 traceback 打到 stderr —— 打包成 --windowed 的
exe 根本没有 stderr。于是心跳**永久停摆**：热键还响（RegisterHotKey 在
TrayThread 自己的消息循环里），但托盘菜单点不动、状态栏冻住、双击图标
叫不出窗口，用户只能重启，而且一点线索都没有。

现在的约定：整体兜住 + 重排放进 finally，出错只写一行限流的日志。

用法: python tests/test_poll_resilience.py
需要带 tkinter 的解释器（swap_gui 顶部 import tkinter）。
"""
import json
import os
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ---- 隔离用户配置：必须在 import swap_gui 之前设好 ----
TMP = tempfile.mkdtemp(prefix="swapmon-poll-")
TEST_CFG = os.path.join(TMP, "config.json")
with open(TEST_CFG, "w", encoding="utf-8") as f:
    json.dump({"hotkey": "ctrl+alt+f9", "exclude_title": ".*", "autostart": False,
               "run_as_admin": False, "_pending_admin_setup": False},
              f, ensure_ascii=False)
os.environ["SWAPMONITORS_CONFIG"] = TEST_CFG

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

# 日志与旗标全部指到临时目录，绝不碰用户真实文件
LOG = os.path.join(TMP, "error.log")
G.sw.LOG_PATH = LOG
G.SHOW_FLAG = os.path.join(TMP, "show.flag")
G.AUTOSTART_CMD = os.path.join(TMP, "SwapMonitors.cmd")


def log_text():
    try:
        with open(LOG, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def boom():
    raise RuntimeError("模拟心跳里的意外错误")


# ============================================================
section("① 心跳里抛异常：不许逃出去，而且必须重排下一次")

root = tk.Tk()
root.withdraw()
app = G.App(root)
try:
    scheduled = []
    app.root.after = lambda ms, fn=None, *a: scheduled.append((ms, fn))

    def call_poll():
        """调一次心跳；有异常逃出来就返回它（正常返回 None）。

        必须自己接住 —— 否则旧代码第一下就抛，整个文件当场退出，
        后面那些断言一条都跑不到，反向验证看不出全貌。"""
        try:
            app._poll()
            return None
        except BaseException as e:       # noqa: BLE001 —— 故意的，要抓住一切
            return e

    _orig_flag = G._consume_show_flag
    G._consume_show_flag = boom          # 心跳第一行就炸
    try:
        raised = call_poll()
    finally:
        G._consume_show_flag = _orig_flag

    check("心跳里的异常不会逃出去（逃出去就再也没人排下一次）",
          raised is None, repr(raised))
    check("出错后仍然排了下一次心跳（80ms）",
          len(scheduled) == 1 and scheduled[0][0] == 80, str(scheduled))
    check("出错写了日志（否则 exe 里完全没线索）",
          "界面心跳出错" in log_text() and "RuntimeError" in log_text(),
          log_text()[:200])
    check("状态栏提示了一次", "界面刷新" in app.status.cget("text"),
          app.status.cget("text"))

    # 恢复正常：计数归零，不再刷日志
    scheduled.clear()
    open(LOG, "w", encoding="utf-8").close()
    app._poll()
    check("恢复正常后不再写日志", log_text() == "", log_text()[:200])
    check("恢复正常后照样排下一次心跳", len(scheduled) == 1, str(scheduled))
    check("连续出错计数已归零",
          getattr(app, "_poll_errors", "<缺失>") == 0,
          repr(getattr(app, "_poll_errors", "<缺失>")))

    # 限流：80ms 一次，不限流一分钟能刷 750 条，error.log 会被自己冲爆
    open(LOG, "w", encoding="utf-8").close()
    G._consume_show_flag = boom
    escaped = 0
    try:
        for _ in range(6):
            if call_poll() is not None:
                escaped += 1
    finally:
        G._consume_show_flag = _orig_flag
    check("连续 6 次出错全部被吞掉（一次都没逃出去）", escaped == 0,
          f"{escaped} 次逃出去了")
    n = log_text().count("界面心跳出错")
    check("连续出错时限流（6 次只记前 3 条）", n == 3, f"实际 {n} 条")
    check("但错误确实被吞掉了 6 次（计数在累加，不是只处理一次）",
          getattr(app, "_poll_errors", "<缺失>") == 6,
          repr(getattr(app, "_poll_errors", "<缺失>")))

    # ============================================================
    section("② 窗口销毁后重排会抛 TclError：退出流程不许崩")

    def dead_after(ms, fn=None, *a):
        raise tk.TclError("application has been destroyed")

    app.root.after = dead_after
    raised = call_poll()
    check("after 抛 TclError 时 _poll 也不许崩", raised is None, repr(raised))

    # ============================================================
    section("③ 出过错之后，队列照常消费（心跳真的还活着）")

    app.root.after = lambda ms, fn=None, *a: None
    app._poll_errors = 0
    while True:                          # 先把初始化阶段残留的事件清空
        try:
            app.q.get_nowait()
        except Exception:                # noqa: BLE001
            break
    app.q.put(("hk_fail", "Ctrl+Alt+F12 已被其他程序占用"))
    app._poll()
    check("出错之后队列里的消息仍然被处理",
          "F12" in app.hk_state.cget("text"), app.hk_state.cget("text"))
finally:
    try:
        root.destroy()
    except Exception:                                        # noqa: BLE001
        pass


print()
if failures:
    print(f"共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("全部通过 ✅")
