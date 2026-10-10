#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swap_gui.py —— 多屏窗口互换（图形界面 + 全局热键）

界面里能看：两块屏信息、当前会被互换的窗口清单、互换预览。
点一下就能换，也能绑一个全局热键（默认 Ctrl+Alt+S）随时换。
可选：跳过最小化窗口、异分辨率等比缩放、排除指定标题、开机自启。

无第三方依赖（tkinter + ctypes）。打包成 exe 后同样可用。
命令行模式仍保留：带任何参数运行时直接透传给 swap_windows.py。
"""

import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import font as tkfont
from tkinter import ttk

import swap_windows as sw

APP_TITLE = "多屏窗口互换"
APP_VER = "1.1.4"
# 配置目录名沿用 "SwapMonitors"，刻意不跟着显示名走 ——
# 这样从 v1.0 升级上来时，热键、排除标题等已有设置不会丢。
CFG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SwapMonitors")
# 允许用环境变量指向别的配置文件（自动化测试必须用它，避免覆盖用户真实设置）
CFG_PATH = os.environ.get("SWAPMONITORS_CONFIG") or os.path.join(CFG_DIR, "config.json")
STARTUP_DIR = os.path.join(os.environ.get("APPDATA") or "", "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup")
AUTOSTART_CMD = os.path.join(STARTUP_DIR, "SwapMonitors.cmd")
# 第二个实例发现程序已在运行时，留下这个旗标；老实例的轮询会消费它并把界面亮出来。
# 用文件而不是窗口消息：跨权限（普通 → 管理员）也能通信，且不需要 Tk 支持自定义消息。
SHOW_FLAG = os.path.join(CFG_DIR, "show.flag")

DEFAULT_CFG = {
    "hotkey": "ctrl+alt+s",
    "exclude_title": "",
    "skip_minimized": False,
    "scale": False,
    "restore_focus": True,
    "autostart": False,
    "notify_on_swap": True,     # 互换完成后是否弹托盘气泡
    "permute": None,            # {"1": 2, "2": 1} 这样的「哪块屏送往哪块屏」；None = 默认行为
    "run_as_admin": False,      # 记住管理员模式：由「最高权限」计划任务拉起，免 UAC
    # 内部标记：勾选管理员模式后发起了提权重启，等提权后的实例回来补建任务。
    # 带下划线前缀，界面上不出现，但必须落盘（跨进程传递）。
    "_pending_admin_setup": False,
    # 内部标记：这次是「普通实例交棒给计划任务」，新实例要把界面亮出来。
    # 任务的动作里写死了 --minimized（供开机静默用），靠这个标记把手动双击区分开。
    "_handover_show": False,
    # 内部标记：「托盘图标被 Win11 收进 ^ 隐藏区」的说明只弹一次，别反复打扰
    "_overflow_hint_acked": False,
}
# 注意：这里没有 start_minimized。
# 「手动双击也要收进托盘」是个坑 —— 用户会以为程序没启动。现在只有开机自启
# 那一项（启动项里带 --minimized）才会静默进托盘，手动启动一律把界面显示出来。

# ---- 配色：浅色 + 蓝色科技感 ----
C_BG, C_CARD, C_LINE = "#f4f7fb", "#ffffff", "#d8e2f0"
C_TEXT, C_DIM = "#16283f", "#6b7f99"
C_ACCENT, C_ACCENT_D = "#1f6feb", "#1552b8"
C_OK, C_WARN = "#1a7f4b", "#b4530a"
FONT = "Microsoft YaHei UI"


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ============================================================ 全局热键

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000
WM_HOTKEY, WM_DESTROY = 0x0312, 0x0002
VK_F1 = 0x70
HWND_MESSAGE = -3

MB_ICONINFORMATION, MB_ICONWARNING, MB_TOPMOST = 0x40, 0x30, 0x40000

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", ctypes.c_long * 2)]


u32.DefWindowProcW.restype = LRESULT
u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
u32.RegisterClassExW.restype = wintypes.WORD
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                wintypes.HINSTANCE, wintypes.LPVOID]
u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
u32.RegisterHotKey.restype = wintypes.BOOL
u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
u32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT,
                             wintypes.UINT, wintypes.UINT]
u32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
u32.DestroyWindow.argtypes = [wintypes.HWND]

# ---------------------------------------------------------------- 托盘图标

shell32 = ctypes.WinDLL("shell32", use_last_error=True)

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
SM_CXSMICON = 49
WM_TRAYICON = 0x8000 + 1               # WM_APP + 1
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
# 诊断弹窗用；argtypes 一定要声明，否则 64 位下 LPWSTR 会被截成 32 位
u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                            wintypes.LPCWSTR, wintypes.UINT]
u32.MessageBoxW.restype = ctypes.c_int


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    """Shell_NotifyIconGetRect 的入参。注意它和 NOTIFYICONDATAW 布局不同，
    不能混用（中间少了 uFlags/uCallbackMessage/hIcon/szTip 这几段）。"""
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID)]


shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
try:
    shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                               ctypes.POINTER(wintypes.RECT)]
    shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
except AttributeError:      # 极老的系统没有这个导出，退化为"无法自检"
    shell32.Shell_NotifyIconGetRect = None
u32.CreatePopupMenu.restype = wintypes.HMENU
u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                            wintypes.LPCWSTR]
u32.DestroyMenu.argtypes = [wintypes.HMENU]
u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.HWND,
                               ctypes.c_void_p]
u32.TrackPopupMenu.restype = ctypes.c_int
u32.GetCursorPos.argtypes = [ctypes.POINTER(sw.POINT)]
u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
u32.LoadImageW.restype = wintypes.HANDLE
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
u32.LoadIconW.restype = wintypes.HANDLE
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM]
u32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
u32.RegisterWindowMessageW.restype = wintypes.UINT

# explorer（任务栏）重启或首次就绪时会广播这条消息。收到它必须重新 NIM_ADD，
# 否则托盘图标会永久消失（开机自启最常撞到的就是这一条）。
WM_TASKBARCREATED = u32.RegisterWindowMessageW("TaskbarCreated")

# Win11 会把「未固定」的图标默认收进「隐藏的图标 ^」溢出区。
# Shell_NotifyIconGetRect 拿到的 rect 若落在 ^ 按钮位置，就说明没固定上。
NIIF_NONE = 0x0
S_OK = 0

HK_CLS = "SwapMonitorsTraySink"

_SINK_HANDLERS = {}          # hwnd -> 处理函数；返回 True 表示已处理


def _hk_wndproc(hwnd, msg, wp, lp):
    h = _SINK_HANDLERS.get(int(hwnd or 0))
    if h is not None:
        try:
            if h(hwnd, msg, wp, lp):
                return 0
        except Exception:
            pass
    return u32.DefWindowProcW(hwnd, msg, wp, lp)


# 必须持模块级引用：ctypes 回调被 GC 回收后，窗口类里存的函数指针就悬空了 → 收消息即崩
_HK_WNDPROC_REF = WNDPROC(_hk_wndproc)


# ---------------------------------------------------------------- 权限（UAC）

def is_elevated():
    """当前进程是否已是管理员权限（读令牌的 TokenElevation，比 IsUserAnAdmin 可靠）。

    argtypes 必须显式给全：GetCurrentProcess() 返回的是 -1 伪句柄，经
    c_void_p 转成 Python 里的 2**64-1；若让 ctypes 按默认的 c_int 传参，
    OpenProcessToken 会抛 OverflowError，被下面的 except 吞掉后这里就
    永远返回 False —— v1.1.1 及之前正是这个坑：明明已经提权成功，
    界面却一直显示「普通用户」，而且提权后再点提权还会再来一遍。
    """
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TOKEN_QUERY, TokenElevation = 0x0008, 20
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 wintypes.LPVOID, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        h = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY,
                                         ctypes.byref(h)):
            return False
        try:
            val, n = wintypes.DWORD(), wintypes.DWORD()
            if not advapi32.GetTokenInformation(h, TokenElevation, ctypes.byref(val),
                                                ctypes.sizeof(val), ctypes.byref(n)):
                return False
            return bool(val.value)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


def _sanitize_env_for_independent_child():
    """拉起「独立新实例」前，把 PyInstaller 的内部环境变量摘干净。

    为什么需要这一步（v1.1.1 修复的提权崩溃）：
    - PyInstaller 6.9 起，用同一个 exe 再拉起的进程默认被当成「worker 子进程」，
      会复用父进程的解包目录；官方文档要求重启场景必须设
      PYINSTALLER_RESET_ENVIRONMENT=1 才会被当作独立实例。
    - 6.22.1 起又加了安全校验（#9492/#9520）：UAC 提权的 onefile 进程若继承了
      上一实例的 _PYI_* 内部变量，会被当成 onefile 子进程去校验「原始父进程」，
      而原 bootloader 在旧实例退出时就没了 —— 于是弹
      「Security validation failure: invalid originating onefile parent process
      (PID not found)」，提权重启直接失败、新旧两个都没了。
    ShellExecuteW 的子进程继承的是调用方**此刻**的环境块，所以这里先摘掉
    _PYI_*、设上官方开关，等 ShellExecuteW 把环境块复制走之后再原样还原。
    返回还原用的快照（交给 _restore_env_for_child）。
    """
    saved = {k: os.environ[k] for k in list(os.environ) if k.startswith("_PYI_")}
    for k in saved:
        del os.environ[k]
    saved["PYINSTALLER_RESET_ENVIRONMENT"] = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return saved


def _restore_env_for_child(saved):
    """把 _sanitize_env_for_independent_child 改动的环境变量原样还原。"""
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def relaunch_as_admin():
    """弹 UAC 以管理员身份重启自己。返回 True 表示已成功拉起（调用方应自行退出）。"""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    if getattr(sys, "frozen", False):
        exe, params, cwd = sys.executable, "", os.path.dirname(sys.executable)
    else:
        exe = sys.executable
        params = f'"{os.path.abspath(__file__)}"'
        cwd = os.path.dirname(os.path.abspath(__file__))
    saved = _sanitize_env_for_independent_child()
    try:
        ret = shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)  # SW_SHOWNORMAL
    finally:
        _restore_env_for_child(saved)
    return int(ret or 0) > 32          # >32 表示成功；5 = 用户在 UAC 上点了「否」


class Toggle(tk.Frame):
    """自绘开关项 —— clam 主题自带的勾选框记号太含糊，自己画一个清楚的。"""

    def __init__(self, parent, text, var, command=None):
        super().__init__(parent, bg=C_BG)
        self.var, self.command = var, command
        self.mark = tk.Label(self, text="", bg=C_BG, font=(FONT, 10, "bold"),
                             width=2, cursor="hand2")
        self.mark.pack(side="left")
        self.label = tk.Label(self, text=text, bg=C_BG, fg=C_TEXT, font=(FONT, 9),
                              cursor="hand2")
        self.label.pack(side="left")
        for w in (self, self.mark, self.label):
            w.bind("<Button-1>", self._toggle)
        self._refresh()

    def _toggle(self, _=None):
        self.var.set(not self.var.get())
        self._refresh()
        if self.command:
            self.command()

    def _refresh(self):
        on = bool(self.var.get())
        self.mark.configure(text="✓" if on else "○",
                            fg=C_ACCENT if on else "#b7c6d9")


def parse_hotkey(spec):
    """'ctrl+alt+s' / 'ctrl+shift+f9' → (mods, vk, 显示名)"""
    import re
    mods, vk, names = 0, None, []
    for part in re.split(r"[+\-\s]+", (spec or "").strip().lower()):
        if not part:
            continue
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
            names.append("Ctrl")
        elif part == "alt":
            mods |= MOD_ALT
            names.append("Alt")
        elif part == "shift":
            mods |= MOD_SHIFT
            names.append("Shift")
        elif part in ("win", "super", "cmd"):
            mods |= MOD_WIN
            names.append("Win")
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk = VK_F1 + int(part[1:]) - 1
            names.append(part.upper())
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
            names.append(part.upper())
    if vk is None:
        raise ValueError("热键里至少要有一个主键，例如 ctrl+alt+s")
    return mods, vk, "+".join(names)


def vk_to_spec(mods, vk):
    """录制用：把 Tk 的 event.state + keysym 转成 'ctrl+alt+s'"""
    parts = []
    if mods & 0x4:
        parts.append("ctrl")
    if mods & 0x20000:
        parts.append("alt")
    if mods & 0x1:
        parts.append("shift")
    return parts, vk


class TrayThread(threading.Thread):
    """独立线程持有隐藏窗口，负责：全局热键 + 托盘图标 + 托盘右键菜单。
    所有 Win32 消息都在这个线程自己的循环里处理，不干扰 Tk 的消息循环。"""

    MENU_SWAP, MENU_PREVIEW, MENU_SHOW, MENU_EXIT = 101, 102, 103, 104

    def __init__(self, results, icon_path=None):
        super().__init__(daemon=True)
        self.results = results          # 回传事件给 UI
        self.cmds = queue.Queue()
        self.hwnd = None
        self.icon_path = icon_path
        self.hicon = None
        self.nid = None
        self._spec = None
        self._shown_hk = ""
        self._stop = False

    # ---- UI 侧调用 ----
    def set_hotkey(self, spec):
        self.cmds.put(("set", spec))

    def notify(self, title, text, warning=False):
        self.cmds.put(("notify", (title, text, warning)))

    def reinit_tray(self):
        """explorer 重启后重新挂托盘图标（由 UI 线程转发）。"""
        self.cmds.put(("reinit", None))

    # ---- 供 UI 线程查询（只读，无副作用）----
    def icon_rect(self):
        try:
            return self._icon_rect()
        except Exception:
            return None

    def promote_icon(self):
        try:
            return self._promote_icon()
        except Exception:
            return False

    def stop(self):
        self._stop = True
        self.cmds.put(("quit", None))

    # ---- 线程内部 ----
    def _create_window(self):
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = _HK_WNDPROC_REF
        wc.lpszClassName = HK_CLS
        if not u32.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
            return None
        # 必须是"真实但不显示"的顶层窗口：message-only 窗口收不到托盘回调
        hwnd = u32.CreateWindowExW(0, HK_CLS, HK_CLS, 0, 0, 0, 0, 0,
                                   None, None, None, None)
        if hwnd:
            _SINK_HANDLERS[int(hwnd)] = self._on_message
        return hwnd

    def _load_icon(self):
        size = u32.GetSystemMetrics(SM_CXSMICON) or 16
        if self.icon_path and os.path.exists(self.icon_path):
            h = u32.LoadImageW(None, self.icon_path, IMAGE_ICON, size, size,
                               LR_LOADFROMFILE)
            if h:
                return h
        return u32.LoadIconW(None, ctypes.cast(32512, wintypes.LPCWSTR))  # IDI_APPLICATION

    def _add_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self._tip_text()
        self.nid = nid
        return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _icon_rect(self):
        """图标在屏幕上的矩形；拿不到返回 None。"""
        if shell32.Shell_NotifyIconGetRect is None:
            return None
        ident = NOTIFYICONIDENTIFIER()
        ident.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
        ident.hWnd = self.hwnd
        ident.uID = 1
        r = wintypes.RECT()
        return r if shell32.Shell_NotifyIconGetRect(ctypes.byref(ident),
                                                   ctypes.byref(r)) == S_OK else None

    def _add_icon_with_retry(self, tries=6, delay=0.4):
        """托盘就绪需要时间：explorer 刚起来时 NIM_ADD 会失败或落不到位，重试几轮。"""
        for i in range(tries):
            if self._add_icon():
                if self._icon_rect() is not None:
                    return True
                # 加进去了但查不到矩形：可能还没画出来，再等等
            else:
                # 典型失败：Shell_NotifyIcon 拿不到 explorer 的窗口（开机太早）
                self.nid = None
            if i < tries - 1:
                time.sleep(delay)
        return bool(self.nid is not None and self._icon_rect() is not None)

    def _promote_icon(self):
        """Win11：把图标标记为「固定显示」，否则它只会躺在「^ 隐藏的图标」里。
        写 HKCU 下 explorer 自己维护的记录；失败就只提示不加戏。"""
        try:
            import winreg
            exe = os.path.abspath(sys.executable)
            if not getattr(sys, "frozen", False):
                return False
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Control Panel\NotifyIconSettings", 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            n = winreg.QueryInfoKey(key)[0]
            hit = False
            for i in range(n):
                sub = winreg.EnumKey(key, i)
                try:
                    sk = winreg.OpenKey(key, sub, 0, winreg.KEY_READ | winreg.KEY_WRITE)
                    path = str(winreg.QueryValueEx(sk, "ExecutablePath")[0])
                except OSError:
                    continue
                if path.lower() == exe.lower():
                    winreg.SetValueEx(sk, "IsPromoted", 0, winreg.REG_DWORD, 1)
                    hit = True
            return hit
        except Exception:
            return False

    def _ensure_icon(self):
        """注册托盘图标；成功后再判断它有没有被 Win11 收进溢出区。"""
        if self.hwnd is None:
            return
        if self._add_icon_with_retry():
            self.results.put(("tray_ok", True))
        else:
            self.results.put(("tray_ok", False))

    def _tip_text(self):
        hk = self._shown_hk or "未设热键"
        return f"{APP_TITLE}　·　热键 {hk}　·　左键双击显示、右键菜单"

    def _refresh_tip(self):
        if not self.nid:
            return
        self.nid.uFlags = NIF_TIP
        self.nid.szTip = self._tip_text()
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def _balloon(self, title, text, warning=False):
        if not self.nid:
            return
        self.nid.uFlags = NIF_INFO | NIF_ICON | NIF_TIP
        self.nid.szInfoTitle = (title or APP_TITLE)[:63]
        self.nid.szInfo = (text or "")[:255]
        self.nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def _popup_menu(self):
        hmenu = u32.CreatePopupMenu()
        u32.AppendMenuW(hmenu, MF_STRING, self.MENU_SWAP, "立即互换窗口")
        u32.AppendMenuW(hmenu, MF_STRING, self.MENU_PREVIEW, "预览互换计划")
        u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(hmenu, MF_STRING, self.MENU_SHOW, "显示主界面")
        u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(hmenu, MF_STRING, self.MENU_EXIT, "退出")
        pt = sw.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        # TrackPopupMenu 要求先把自己置前台，否则点到菜单外面菜单不消失
        u32.SetForegroundWindow(self.hwnd)
        cmd = u32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                 pt.x, pt.y, 0, self.hwnd, None)
        u32.PostMessageW(self.hwnd, 0, 0, 0)      # WM_NULL，收尾
        u32.DestroyMenu(hmenu)
        mapping = {self.MENU_SWAP: "tray_swap", self.MENU_PREVIEW: "tray_preview",
                   self.MENU_SHOW: "tray_show", self.MENU_EXIT: "tray_exit"}
        if cmd in mapping:
            self.results.put((mapping[cmd], None))

    def _on_message(self, hwnd, msg, wp, lp):
        if msg == WM_HOTKEY:
            self.results.put(("hotkey", None))
            return True
        if msg == WM_TASKBARCREATED and WM_TASKBARCREATED:
            # explorer 重启 / 任务栏刚就绪：之前注册的图标已经没了，必须重加
            self.nid = None
            self.results.put(("tray_reinit", None))
            return True
        if msg == WM_TRAYICON:
            ev = int(lp) & 0xFFFF
            if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.results.put(("tray_show", None))
            elif ev == WM_RBUTTONUP:
                self._popup_menu()
            return True
        return False

    def run(self):
        self.hwnd = self._create_window()
        if not self.hwnd:
            self.results.put(("hk_fail", "无法创建托盘/热键窗口"))
            return
        self.hicon = self._load_icon()
        self._ensure_icon()
        msg = MSG()
        while not self._stop:
            while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
            try:
                while True:
                    op, val = self.cmds.get_nowait()
                    if op == "set":
                        self._apply(val)
                    elif op == "reinit":
                        self._ensure_icon()
                    elif op == "notify":
                        self._balloon(*val)
                    elif op == "quit":
                        self._stop = True
            except queue.Empty:
                pass
            time.sleep(0.03)
        # 退出：先摘托盘图标，再卸热键，最后销毁窗口
        if self.nid:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            except Exception:
                pass
        _SINK_HANDLERS.pop(int(self.hwnd or 0), None)
        u32.UnregisterHotKey(self.hwnd, 1)
        u32.DestroyWindow(self.hwnd)

    def _apply(self, spec):
        u32.UnregisterHotKey(self.hwnd, 1)
        try:
            mods, vk, shown = parse_hotkey(spec)
        except ValueError as e:
            self.results.put(("hk_fail", str(e)))
            return
        if u32.RegisterHotKey(self.hwnd, 1, mods | MOD_NOREPEAT, vk):
            self._spec = spec
            self._shown_hk = shown
            self._refresh_tip()
            self.results.put(("hk_ok", shown))
        else:
            self._shown_hk = ""
            self._refresh_tip()
            self.results.put(("hk_fail", f"{shown} 已被其他程序占用（Win32 错误 "
                                        f"{ctypes.get_last_error()}），换一个组合试试"))


# ============================================================ 主界面


def _ascii_path(path):
    """尽量返回纯 ASCII 的 8.3 短路径 —— .cmd 里带中文路径极易因编码而失效。"""
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k.GetShortPathNameW.restype = wintypes.DWORD
        n = k.GetShortPathNameW(path, None, 0)
        if n:
            buf = ctypes.create_unicode_buffer(n)
            if k.GetShortPathNameW(path, buf, n) and all(ord(c) < 128 for c in buf.value):
                return buf.value.replace("/", "\\")
    except Exception:
        pass
    return path.replace("/", "\\")   # cmd.exe 里必须用反斜杠，正斜杠会被当开关


def _write_ascii_file(path, text):
    """按 cmd.exe 能正确解析的编码写文件：全 ASCII 用 ascii，否则用系统 ANSI(mbcs)。"""
    enc = "ascii" if all(ord(c) < 128 for c in text) else "mbcs"
    with open(path, "w", encoding=enc, errors="strict", newline="") as f:
        f.write(text)
    return enc


def _long_path(p):
    """把 8.3 短路径还原成长路径，用于路径比对（.cmd 里写的是短路径）。"""
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k.GetLongPathNameW.restype = wintypes.DWORD
        n = k.GetLongPathNameW(p, None, 0)
        if n:
            buf = ctypes.create_unicode_buffer(n)
            if k.GetLongPathNameW(p, buf, n):
                return buf.value
    except Exception:
        pass
    return p


def _same_path(a, b):
    """忽略大小写、正斜杠和 8.3 短路径差异地比较两个路径。"""
    try:
        return (os.path.normcase(os.path.abspath(_long_path(a)))
                == os.path.normcase(os.path.abspath(_long_path(b))))
    except Exception:
        return a == b


def _autostart_exe():
    """自启项真正要拉起的那个 exe（frozen 是自己，源码方式是 pythonw）。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pyw if os.path.exists(pyw) else sys.executable


def _autostart_cmd_text():
    """自启 .cmd 的内容。

    多了一行 `if not exist ... exit /b 0`：万一 exe 被挪走或删掉，
    开机时静默跳过，而不是弹一个黑窗报错。
    """
    exe = _ascii_path(_autostart_exe())
    if getattr(sys, "frozen", False):
        target = f'"{exe}"'
    else:
        target = f'"{exe}" "{_ascii_path(os.path.abspath(__file__))}"'
    return ("@echo off\r\n"
            f'if not exist "{exe}" exit /b 0\r\n'
            f'start "" {target} --minimized\r\n')


def _autostart_recorded_exe():
    """读现有 .cmd 里记录的 exe 路径；文件不存在或读不出来就返回 None。"""
    try:
        with open(AUTOSTART_CMD, "r", encoding="mbcs", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    m = re.search(r'start\s+""\s+"([^"]+)"', text)
    return m.group(1) if m else None


def _autostart_needs_update():
    """自启项是否需要重写：文件丢了，或里面记的路径已经不是当前的 exe。"""
    recorded = _autostart_recorded_exe()
    if recorded is None:
        return True                      # 文件不存在/读不出来 → 按配置重建
    return not _same_path(recorded, _autostart_exe())


def sync_autostart_if_moved():
    """exe 换了位置就静默重写自启项，不用用户手动关再开一次。

    以前自启项里写死 exe 的绝对路径，程序换个目录/换个盘之后就指向了不存在的文件：
    开机时要么弹黑窗、要么干脆不起来，必须手动把「开机自动启动」关掉再打开才会刷新。
    现在每次启动都比对一次，不一致就重写 —— 换位置后只要正常跑一次就自动跟上。
    返回 True 表示这次真的重写了。
    """
    try:
        if not _autostart_needs_update():
            return False
        os.makedirs(STARTUP_DIR, exist_ok=True)
        _write_ascii_file(AUTOSTART_CMD, _autostart_cmd_text())
        return True
    except Exception:
        return False


# ============================================================ 单实例守卫

# 一个进程只能有一份：否则用户"双击 exe"会在托盘里堆出第二个图标，
# 而且管理员模式交棒时新旧两个实例会互相打架。
# 用「本地命名互斥体」而不是 Global\：Global\ 需要 SeCreateGlobalPrivilege，
# 普通用户拿不到，会直接失败；两个实例都在同一个交互会话里，Local\ 足够。
_SINGLE_MUTEX = None            # 句柄必须一直握着，句柄一关就相当于释放了
_SINGLE_MUTEX_NAME = ""         # 记下实际用的名字，--diag 里能看见
_SINGLE_MUTEX_NOTE = "（尚未尝试）"   # 失败原因。**绝不能静默吞掉** ——
# 「单实例悄悄失效」的表现是"双击后冒出第二个托盘图标"，肉眼很难联想到互斥体，
# 所以这里把出错信息留在全局里，由 --diag 打印出来。


def single_mutex_name():
    """互斥体名。单独抽出来是为了 --diag 能显示它、也方便测试断言。"""
    return "Local\\SwapMonitors_" + (_current_user_sid() or "default")


def _acquire_single_instance():
    """True = 本进程是唯一实例；False = 已经有一个在跑。"""
    global _SINGLE_MUTEX, _SINGLE_MUTEX_NAME, _SINGLE_MUTEX_NOTE
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateMutexW.restype = wintypes.HANDLE
        name = single_mutex_name()
        _SINGLE_MUTEX_NAME = name
        h = k.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if not h:
            _SINGLE_MUTEX_NOTE = f"CreateMutexW 失败，GetLastError={err}"
            return True          # 互斥体都建不出来（权限/内核对象耗尽）就别挡着程序跑
        _SINGLE_MUTEX = h        # 故意泄漏句柄，进程退出时由系统回收
        _SINGLE_MUTEX_NOTE = (f"已创建，last_error={err}"
                              + ("（该名字已存在 → 不是唯一实例）" if err == 183 else ""))
        return err != 183        # ERROR_ALREADY_EXISTS
    except Exception as e:                                   # noqa: BLE001
        _SINGLE_MUTEX_NOTE = f"{type(e).__name__}: {e}"
        return True


def _release_single_instance():
    """交棒前主动让位：任务拉起的新实例得能拿到互斥体，否则它一启动就自杀。"""
    global _SINGLE_MUTEX
    if _SINGLE_MUTEX:
        try:
            ctypes.WinDLL("kernel32").CloseHandle(_SINGLE_MUTEX)
        except Exception:
            pass
        _SINGLE_MUTEX = None


def _signal_show_existing():
    """叫已经在跑的那一份把界面亮出来。"""
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(SHOW_FLAG, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return False


def _consume_show_flag():
    """消费「亮出界面」旗标；返回是否消费到了。"""
    try:
        if os.path.exists(SHOW_FLAG):
            os.remove(SHOW_FLAG)
            return True
    except OSError:
        pass
    return False


def _clear_show_flag():
    try:
        if os.path.exists(SHOW_FLAG):
            os.remove(SHOW_FLAG)
    except OSError:
        pass


# ============================================================ 管理员模式（计划任务）

# 「记住管理员模式」不是靠兼容性标记（那个每次启动都要弹 UAC，开机自启没人点就白搭），
# 而是注册一个「以最高权限运行」的计划任务：由任务计划服务代为启动，全程不弹 UAC。
TASK_NAME = "SwapMonitors-Elevated"


def _task_exe():
    """计划任务要拉起的 exe。frozen 是自己；源码方式用 pythonw。"""
    return _autostart_exe()


def _task_xml():
    """生成任务 XML。

    - LogonTrigger：登录即启动，等价于开机自启（且免 UAC）
    - RunLevel=HighestAvailable：以最高权限运行，这就是「记住管理员模式」
    - 抓当前用户的 SID 作 Principal，任务才允许在该用户会话里跑 GUI
    """
    exe = _task_exe()
    sid = _current_user_sid()
    # 用 --minimized 让自启时静默进托盘；任务触发器不会带上这个参数，
    # 所以写进 Arguments。
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>多屏窗口互换：以管理员权限启动，避免每次弹 UAC。</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{sid}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <Arguments>--minimized</Arguments>
    </Exec>
  </Actions>
</Task>
"""
    return xml


def _current_user_sid():
    """取当前用户 SID（形如 S-1-5-21-...），任务 Principal 里要用它。"""
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetCurrentProcess.argtypes = []
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 wintypes.LPVOID, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID,
                                                    ctypes.POINTER(wintypes.LPWSTR)]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        tok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(k.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):
            return ""
        try:
            n = wintypes.DWORD()
            advapi32.GetTokenInformation(tok, 1, None, 0, ctypes.byref(n))  # TokenUser
            buf = ctypes.create_string_buffer(n.value)
            if not advapi32.GetTokenInformation(tok, 1, buf, n.value, ctypes.byref(n)):
                return ""
            sid_ptr = ctypes.cast(buf, ctypes.POINTER(wintypes.LPVOID))[0]
            s = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(s)):
                return ""
            try:
                return s.value or ""
            finally:
                ctypes.WinDLL("kernel32").LocalFree(s)
        finally:
            k.CloseHandle(tok)
    except Exception:
        return ""


def _schtasks_exe():
    """优先用绝对路径：极少数环境下 PATH 被改过，裸 "schtasks" 会找不到。"""
    p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                     "System32", "schtasks.exe")
    return p if os.path.exists(p) else "schtasks"


def _decode_console(b):
    """控制台输出解码。

    坑：`schtasks /query /xml` 是把 XML **直接以 UTF-16LE 写 stdout** 的，
    用 mbcs 解出来是一堆带 \\x00 的乱码 —— 于是 "HighestAvailable" 永远搜不到，
    「管理员模式」会被误判为没生效。所以先嗅探 BOM / 大量 NUL 再决定解码方式。
    """
    if not b:
        return ""
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return b[2:].decode("utf-16-le", errors="replace") if b[:2] == b"\xff\xfe" \
                else b[2:].decode("utf-16-be", errors="replace")
        except Exception:
            pass
    elif len(b) > 8 and b.count(b"\x00") > len(b) // 4:
        try:
            return b.decode("utf-16-le", errors="replace").replace("\ufeff", "")
        except Exception:
            pass
    for enc in ("mbcs", "utf-8", "gbk"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", errors="replace")


def _run_schtasks(args):
    """调 schtasks 并回传 (返回码, 输出)。用 CREATE_NO_WINDOW 免得闪黑窗。"""
    import subprocess
    try:
        p = subprocess.run([_schtasks_exe()] + args, capture_output=True,
                           timeout=30, creationflags=0x08000000)
        return p.returncode, _decode_console((p.stdout or b"") + (p.stderr or b""))
    except Exception as e:
        return -1, str(e)


def _task_xml_text():
    """查任务的 XML 原文；查不到返回空串。"""
    rc, out = _run_schtasks(["/query", "/tn", TASK_NAME, "/xml"])
    return out if rc == 0 else ""


def task_exists():
    rc, _ = _run_schtasks(["/query", "/tn", TASK_NAME])
    return rc == 0


def task_runlevel_is_highest():
    """已存在的任务是不是「最高权限」；查不到就当不是。"""
    return "HighestAvailable" in _task_xml_text()


def task_points_to_current_exe():
    m = re.search(r"<Command>(.*?)</Command>", _task_xml_text(), re.S)
    if not m:
        return False
    return _same_path(m.group(1).strip(), _task_exe())


def create_elevated_task():
    """创建/更新「最高权限」计划任务。必须已在管理员权限下调用，否则 schtasks 会失败。"""
    import tempfile
    xml = _task_xml()
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="swapmon_task_")
    try:
        # 必须 utf-16（带 BOM）：XML 头里写的是 UTF-16，schtasks 认 BOM 决定编码。
        # 写成 UTF-8 会被 `schtasks /create /xml` 拒掉（中文描述直接变乱码/报错）。
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(xml)
        rc, out = _run_schtasks(["/create", "/tn", TASK_NAME, "/xml", path, "/f"])
        if rc != 0:
            return False, out
        # 再查一遍，确认系统真的把 RunLevel 落进去了（个别策略会静默忽略）
        if not task_runlevel_is_highest():
            return False, ("任务已写入，但查回来的不是「最高权限」。\n"
                           "多半是组策略限制了任务权限，或系统任务是别人建的。\n"
                           + out.strip()[-300:])
        return True, out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _diag_report():
    """--diag 的输出：把诊断行印出来，方便用户复制回来。

    打包成 --windowed 的 exe 后**没有控制台**（此时 sys.stdout 是 None）；
    但只要是从 cmd/PowerShell 里调用，stdout 就是有效管道，能直接打印。
    所以判据是「有没有可用的 stdout」，而不是「是不是打包过」——
    否则从命令行跑也会弹个窗，脚本里根本没法用。
    """
    txt = verify_elevated_task()
    printed = False
    if sys.stdout is not None:
        try:
            sys.stdout.write(txt + "\n")
            sys.stdout.flush()
            printed = True
        except Exception:
            pass
    if not printed:
        # 双击运行、没有控制台：只能弹窗，否则用户什么都看不到
        try:
            u32.MessageBoxW(None, txt, f"{APP_TITLE} 诊断信息",
                            MB_ICONINFORMATION | MB_TOPMOST)
        except Exception:
            pass
    return 0


def delete_elevated_task():
    rc, out = _run_schtasks(["/delete", "/tn", TASK_NAME, "/f"])
    return rc == 0, out


def start_elevated_task():
    """立即通过任务拉起一个最高权限实例（不弹 UAC）。返回是否成功。"""
    rc, out = _run_schtasks(["/run", "/tn", TASK_NAME])
    return rc == 0, out


def sync_task_if_moved():
    """exe 换位置后任务里的 Command 就失效了，能改就改。

    更新任务需要管理员权限；没有权限就什么都不做（留给下次以管理员运行时修）。
    """
    try:
        xml = _task_xml_text()
        if not xml:
            return False
        m = re.search(r"<Command>(.*?)</Command>", xml, re.S)
        if m and _same_path(m.group(1).strip(), _task_exe()) and "HighestAvailable" in xml:
            return False                 # 已经是我们要的样子，不用动
        if not is_elevated():
            return False
        ok, _ = create_elevated_task()
        return ok
    except Exception:
        return False


def verify_elevated_task():
    """把「管理员模式」的当前实况拼成一行行文字，便于自检 / 排错。"""
    # 真的抢一次互斥体：这一步能顺手告诉我们「现在是否已经有实例在跑」，
    # 也是排查「双击冒出第二个托盘图标」这类问题的唯一线索。
    try:
        uniq = _acquire_single_instance()
        lock_msg = ("抢到（当前没有别的实例在运行）" if uniq
                    else "没抢到（已经有实例在运行）")
        _release_single_instance()
    except Exception as e:                                   # noqa: BLE001
        lock_msg = f"抢锁异常 {type(e).__name__}: {e}"

    lines = []
    try:
        lines.append(f"程序版本: {APP_VER}")
        lines.append(f"可执行文件: {sys.executable}")
        lines.append(f"frozen(打包运行): {bool(getattr(sys, 'frozen', False))}")
        lines.append(f"配置文件: {CFG_PATH}")
        lines.append(f"当前是否管理员: {is_elevated()}")
        lines.append(f"计划任务名: {TASK_NAME}")
        lines.append(f"计划任务存在: {task_exists()}")
        lines.append(f"任务为最高权限: {task_runlevel_is_highest()}")
        lines.append(f"任务指向当前程序: {task_points_to_current_exe()}")
        lines.append(f"任务要拉起的程序: {_task_exe()}")
        lines.append(f"开机自启 .cmd 存在: {os.path.exists(AUTOSTART_CMD)}")
        lines.append(f"单实例互斥体名: {single_mutex_name()}")
        lines.append(f"单实例本次抢锁: {lock_msg}")
        lines.append(f"单实例状态: {_SINGLE_MUTEX_NOTE}")
        lines.append(f"用户 SID: {_current_user_sid() or '（取不到！）'}")
        cfg = load_cfg()
        lines.append(f"配置 run_as_admin: {cfg.get('run_as_admin')}")
        lines.append(f"配置 autostart: {cfg.get('autostart')}")
    except Exception as e:                                  # noqa: BLE001
        lines.append(f"诊断过程出错: {e}")
    return "\n".join(lines)


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_cfg()
        self.q = queue.Queue()
        self.busy = False
        self.mons = []
        self.wins = []
        self.hk = None
        self.hk_shown = self.cfg["hotkey"].upper()
        self.hk_count = 0          # 热键触发次数，既是有用反馈也方便自检
        self.hidden = False        # 是否已收进托盘
        self.tray_hint_shown = False
        self.tray_ok = False       # Shell_NotifyIcon(NIM_ADD) 是否成功
        self._overflow_hinted = False   # 「图标被收进 ^ 溢出区」是否已提示过
        self._tray_beat = 0        # 托盘心跳计数：定期确认图标还在
        self._tray_reinit_left = 10   # 心跳重挂的次数上限，防止无意义地一直重试
        self._hiding = False       # 防止 <Unmap> 递归
        self.perm = {}             # {源屏(1起): 目标屏(1起)} 哪块屏送往哪块屏
        self._perm_n = None        # 上次初始化排列时的屏数，屏数变了要重新校验

        root.title(f"{APP_TITLE} v{APP_VER}")
        root.configure(bg=C_BG)
        self._set_win_icon()
        self._style()

        self._build()
        self.update_perm_label()
        self.root.after(80, self._poll)
        self.refresh()
        # 尺寸校正一定要放在 refresh() 之后：refresh() 会改「共 N 个窗口」那行文字，
        # 行高/换行一变，底部选项的位置就跟着变。顺序反了窗口就矮一截。
        self._fit()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        # 自启自愈：配置里开着但 .cmd 丢了、或者记的还是 exe 的老位置，就在这里
        # 静默重写一遍。程序换目录/换盘之后正常跑一次就自动跟上，不用手动关再开。
        if self.cfg.get("autostart"):
            self.root.after(200, self._autostart_selfheal)
        # 管理员模式自愈：补建/更新/清理计划任务，让「记住管理员」真的记住
        if self.cfg.get("run_as_admin") or self.cfg.get("_pending_admin_setup"):
            self.root.after(300, self._admin_selfheal)

    def _autostart_selfheal(self):
        try:
            if sync_autostart_if_moved():
                self.set_status(f"开机自启已按新位置更新：{_ascii_path(_autostart_exe())}")
        except Exception:
            pass

    # ---------- 外观 ----------
    def _fit(self, recenter=True):
        """按实际内容把窗口调到刚好的大小，并给出最小值。

        为什么要单独抽成方法、还允许重复调用：
          1. 内容高度是**长出来的**。refresh() 会改「共 N 个窗口」那行、start_hotkey()
             会往状态栏写「✓ 已注册…」，这些都会把底部那排选项往下推。在它们之前量
             一次，窗口就会矮一截 —— 实测差 100px 上下，底部的开关直接被裁掉。
          2. 窗口处于 withdraw 状态时几何请求会被忽略（geometry() 形同虚设），
             所以从托盘恢复显示之后必须再校正一次，否则弹出来是个小方块。
        """
        self.root.update_idletasks()
        w = max(980, self.root.winfo_reqwidth() + 4)
        h = self.root.winfo_reqheight() + 4
        self.root.minsize(w, h)
        if recenter:
            self._center(w, h)
        else:                       # 只改尺寸，别把用户挪过的位置又拉回屏幕正中
            self.root.geometry(f"{w}x{h}")
        return w, h

    def _center(self, w, h):
        sw_, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw_ - w) // 2}+{max(0, (sh - h) // 2 - 20)}")

    def _set_win_icon(self):
        ico = os.path.join(sw_resource_dir(), "app_icon.ico")
        try:
            if os.path.exists(ico):
                self.root.iconbitmap(ico)
        except Exception:
            pass

    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=C_BG, foreground=C_TEXT, font=(FONT, 9))
        st.configure("Card.TFrame", background=C_CARD, relief="flat")
        st.configure("TFrame", background=C_BG)
        st.configure("TLabel", background=C_BG, foreground=C_TEXT)
        st.configure("Card.TLabel", background=C_CARD, foreground=C_TEXT)
        st.configure("Dim.TLabel", background=C_CARD, foreground=C_DIM)
        st.configure("Title.TLabel", background=C_BG, foreground=C_TEXT,
                     font=(FONT, 15, "bold"))
        st.configure("Sub.TLabel", background=C_BG, foreground=C_DIM, font=(FONT, 9))
        st.configure("Sect.TLabel", background=C_BG, foreground=C_DIM,
                     font=(FONT, 9, "bold"))
        st.configure("Accent.TButton", font=(FONT, 11, "bold"), padding=(22, 10),
                     background=C_ACCENT, foreground="#ffffff", borderwidth=0)
        st.map("Accent.TButton", background=[("active", C_ACCENT_D), ("disabled", "#9db9e6")])
        st.configure("TButton", font=(FONT, 9), padding=(12, 6), background="#e8eef8",
                     foreground=C_TEXT, borderwidth=0)
        st.map("TButton", background=[("active", "#d5e2f5")])
        st.configure("TCheckbutton", background=C_BG, foreground=C_TEXT)
        st.map("TCheckbutton", background=[("active", C_BG)])
        try:  # 让勾选状态更清楚（clam 默认那个记号偏难认）
            st.configure("TCheckbutton", indicatorcolor=C_CARD, focuscolor=C_BG,
                         indicatormargin=4)
            st.map("TCheckbutton", indicatorcolor=[("selected", C_ACCENT),
                                                   ("active", "#dbe7fa")])
        except Exception:
            pass
        st.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                     foreground=C_TEXT, rowheight=26, borderwidth=0, font=(FONT, 9))
        st.configure("Treeview.Heading", font=(FONT, 9, "bold"),
                     background="#e8eef8", foreground=C_DIM, relief="flat")
        st.map("Treeview", background=[("selected", "#d7e6ff")],
               foreground=[("selected", C_TEXT)])

    # ---------- 布局 ----------
    def _build(self):
        pad = {"padx": 16, "pady": (0, 0)}
        # 顶部标题
        head = ttk.Frame(self.root)
        head.pack(fill="x", padx=18, pady=(16, 10))
        ttk.Label(head, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="  把屏幕上的窗口整体互换，相对布局原样保留",
                  style="Sub.TLabel").pack(side="left", pady=(6, 0))
        # 权限状态 + 提权按钮（放最显眼的位置）
        self.elev_btn = ttk.Button(head, text="以管理员身份重启", command=self.elevate)
        self.elev_btn.pack(side="right")
        self.perm_lbl = ttk.Label(head, text="", style="Sub.TLabel")
        self.perm_lbl.pack(side="right", padx=(0, 10), pady=(6, 0))

        # 显示器卡片（每块屏一个「送往 →」下拉，屏数不限）
        self.cards = ttk.Frame(self.root)
        self.cards.pack(fill="x", padx=18)
        self.card_widgets = []

        pre = ttk.Frame(self.root)
        pre.pack(fill="x", padx=18, pady=(8, 0))
        ttk.Label(pre, text="每块屏的窗口送往：", style="Sub.TLabel").pack(side="left")
        for text, fn in (("环形轮转", self.preset_rotate),
                         ("两两互换", self.preset_pair),
                         ("全部不动", self.preset_none)):
            ttk.Button(pre, text=text, width=9, command=fn).pack(side="left", padx=(8, 0))
        self.perm_hint = ttk.Label(pre, text="", style="Sub.TLabel")
        self.perm_hint.pack(side="right")

        # 中部：窗口清单
        mid = ttk.Frame(self.root)
        mid.pack(fill="both", expand=True, padx=18, pady=(14, 0))
        bar = ttk.Frame(mid)
        bar.pack(fill="x")
        ttk.Label(bar, text="参与互换的窗口", style="Sect.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(bar, text="", style="Sub.TLabel")
        self.count_lbl.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="刷新", command=self.refresh).pack(side="right")

        wrap = ttk.Frame(mid, style="Card.TFrame", padding=1)
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("mon", "state", "rect", "proc", "title", "to")
        heads = ("屏幕", "状态", "位置", "进程", "窗口标题", "互换后")
        widths = (62, 66, 200, 150, 260, 210)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=7)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "title"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("odd", background="#f8fbff")

        # 底部：设置 + 操作
        bot = ttk.Frame(self.root)
        bot.pack(fill="x", padx=18, pady=(14, 16))

        left = ttk.Frame(bot)
        left.pack(side="left", fill="both", expand=True)

        hk = ttk.Frame(left)
        hk.pack(fill="x")
        ttk.Label(hk, text="全局热键", style="Sect.TLabel").pack(anchor="w")
        row = ttk.Frame(hk)
        row.pack(fill="x", pady=(6, 0))
        self.hk_var = tk.StringVar(value=self.cfg["hotkey"])
        self.hk_entry = ttk.Entry(row, textvariable=self.hk_var, width=20,
                                  font=(FONT, 10), justify="center")
        self.hk_entry.pack(side="left", ipady=3)
        ttk.Button(row, text="录制", width=6, command=self.record_hotkey).pack(
            side="left", padx=6)
        ttk.Button(row, text="应用", width=6, command=self.apply_hotkey).pack(side="left")
        self.hk_state = ttk.Label(hk, text="正在注册…", style="Sub.TLabel")
        self.hk_state.pack(anchor="w", pady=(6, 0))

        ex = ttk.Frame(left)
        ex.pack(fill="x", pady=(12, 0))
        ttk.Label(ex, text="排除的窗口标题（正则，可留空）",
                  style="Sect.TLabel").pack(anchor="w")
        self.ex_var = tk.StringVar(value=self.cfg["exclude_title"])
        ttk.Entry(ex, textvariable=self.ex_var, font=(FONT, 9)).pack(
            fill="x", pady=(6, 0), ipady=3)

        opts = ttk.Frame(left)
        opts.pack(fill="x", pady=(12, 0))
        self.skipmin_var = tk.BooleanVar(value=self.cfg["skip_minimized"])
        self.scale_var = tk.BooleanVar(value=self.cfg["scale"])
        self.focus_var = tk.BooleanVar(value=self.cfg["restore_focus"])
        self.notify_var = tk.BooleanVar(value=self.cfg["notify_on_swap"])
        self.auto_var = tk.BooleanVar(value=self.cfg["autostart"])
        self.admin_var = tk.BooleanVar(value=self.cfg["run_as_admin"])
        for var, text, cmd in (
                (self.skipmin_var, "跳过最小化窗口", self._save),
                (self.scale_var, "分辨率不同的屏之间等比缩放（否则纯平移）", self._save),
                (self.focus_var, "互换后把焦点还给原来的窗口", self._save),
                (self.notify_var, "互换完成后弹出提示（托盘气泡）", self._save),
                (self.auto_var, "开机自动启动（开机后静默待在托盘）", self.set_autostart),
                (self.admin_var, "记住管理员模式（管理员窗口也能搬，启动不再弹 UAC）",
                 self.set_run_as_admin)):
            Toggle(opts, text, var, cmd).pack(anchor="w", pady=1)

        right = ttk.Frame(bot)
        right.pack(side="right", fill="y", padx=(20, 0))
        ttk.Button(right, text="立即互换", style="Accent.TButton",
                   command=lambda: self.do_swap()).pack(fill="x")
        ttk.Button(right, text="预览计划（不改动）",
                   command=lambda: self.do_swap(dry=True)).pack(fill="x", pady=(8, 0))
        self.redo_btn = ttk.Button(right, text="恢复原状（再换一次）",
                                   command=lambda: self.do_swap())
        self.redo_btn.pack(fill="x", pady=(8, 0))
        ttk.Button(right, text="退出", command=self.quit_app).pack(fill="x", pady=(8, 0))

        self.status = ttk.Label(self.root, text="就绪", style="Sub.TLabel")
        self.status.pack(fill="x", padx=18, pady=(0, 10))

        # 放到最后再挂：_save() 要用到上面所有变量，早挂会有初始化顺序问题
        self.ex_var.trace_add("write", lambda *_: self._save())

    # ---------- 热键 / 托盘 ----------
    def start_hotkey(self):
        if self.hk is None:
            self.hk = TrayThread(self.q, icon_path=os.path.join(sw_resource_dir(),
                                                                "app_icon.ico"))
            self.hk.start()
            self.hk.set_hotkey(self.cfg["hotkey"])

    def record_hotkey(self):
        top = tk.Toplevel(self.root)
        top.title("录制热键")
        top.configure(bg=C_BG)
        top.resizable(False, False)
        w, h = 360, 150
        top.geometry(f"{w}x{h}+{self.root.winfo_x() + 260}+{self.root.winfo_y() + 260}")
        top.transient(self.root)
        ttk.Label(top, text="请按下想用的组合键", style="Title.TLabel",
                  font=(FONT, 12, "bold")).pack(pady=(24, 6))
        lb = ttk.Label(top, text="（Esc 取消，需含 Ctrl / Alt / Shift）", style="Sub.TLabel")
        lb.pack()

        modmap = {0x4: MOD_CONTROL, 0x20000: MOD_ALT, 0x1: MOD_SHIFT, 0x40000: MOD_WIN}
        names = {MOD_CONTROL: "ctrl", MOD_ALT: "alt", MOD_SHIFT: "shift", MOD_WIN: "win"}

        def on_key(e):
            mods = 0
            for mask, m in modmap.items():
                if e.state & mask:
                    mods |= m
            if mods == 0:
                lb.configure(text="必须按住 Ctrl / Alt / Shift / Win 之一")
                return
            key = (e.keysym or "").lower()
            if key in ("control_l", "control_r", "alt_l", "alt_r", "shift_l",
                       "shift_r", "super_l", "super_r", "escape"):
                if key == "escape":
                    top.destroy()
                return
            if len(key) == 1:
                vk = ord(key.upper())
            elif key.startswith("f") and key[1:].isdigit():
                vk = VK_F1 + int(key[1:]) - 1
            else:
                lb.configure(text=f"不支持这个键：{e.keysym}")
                return
            parts = [n for m, n in names.items() if mods & m]
            spec = "+".join(parts + [key if len(key) == 1 else key.upper()])
            self.hk_var.set(spec)
            top.destroy()
            self.apply_hotkey()

        top.bind("<KeyPress>", on_key)
        top.after(60, top.focus_force)
        top.grab_set()

    def apply_hotkey(self):
        spec = self.hk_var.get().strip()
        try:
            parse_hotkey(spec)
        except ValueError as e:
            self.hk_state.configure(text=f"✗ {e}", foreground=C_WARN)
            return
        self.cfg["hotkey"] = spec
        self._save()
        if self.hk is None:
            self.start_hotkey()
        else:
            self.hk.set_hotkey(spec)
        self.hk_state.configure(text="正在注册…", foreground=C_DIM)

    # ---------- 权限（UAC 提权） ----------
    def update_perm_label(self):
        if is_elevated():
            self.perm_lbl.configure(text="权限：管理员 ✓", foreground=C_OK)
            try:
                self.elev_btn.configure(state="disabled")
            except Exception:
                pass
        elif self.cfg.get("run_as_admin"):
            self.perm_lbl.configure(text="权限：普通用户（已设管理员模式，重启后生效）",
                                    foreground=C_WARN)
        else:
            self.perm_lbl.configure(text="权限：普通用户（管理员窗口搬不动）",
                                    foreground=C_WARN)

    def elevate(self):
        if is_elevated():
            messagebox.showinfo(APP_TITLE, "当前已经是管理员权限。", parent=self.root)
            return
        self._save()
        # 先释放热键再拉起新实例，否则新实例会因热键被自己占着而注册失败
        if self.hk:
            self.hk.stop()
            self.hk = None
            time.sleep(0.25)
        # 把单实例互斥体让出去：提权后的新实例和本进程是两份，不让位它会被守卫挡回去。
        _release_single_instance()
        if relaunch_as_admin():
            self.set_status("已请求提权，正在以管理员身份重启…")
            self.root.after(250, self._exit_now)
        else:
            _acquire_single_instance()   # 提权没成功，互斥体得收回来
            messagebox.showwarning(
                APP_TITLE,
                "提权没有成功（多数情况是你在 UAC 弹窗上点了「否」）。\n\n"
                "也可以直接右键程序 →「以管理员身份运行」。",
                parent=self.root)
            self.start_hotkey()        # 提权失败，把热键注册回来

    def _exit_now(self):
        self._save()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------- 自启 ----------
    def set_autostart(self):
        on = self.auto_var.get()
        self.cfg["autostart"] = on
        self._save()
        try:
            if on:
                os.makedirs(STARTUP_DIR, exist_ok=True)
                # 幂等：不管之前是什么内容，一律按当前 exe 的真实位置重写一次
                _write_ascii_file(AUTOSTART_CMD, _autostart_cmd_text())
                self.set_status(f"已设置开机自启：{AUTOSTART_CMD}")
            else:
                if os.path.exists(AUTOSTART_CMD):
                    os.remove(AUTOSTART_CMD)
                self.set_status("已取消开机自启")
        except Exception as e:  # noqa: BLE001
            messagebox.showwarning(APP_TITLE, f"修改开机自启失败：{e}", parent=self.root)

    # ---------- 记住管理员模式 ----------
    def set_run_as_admin(self):
        """勾选/取消「记住管理员模式」。

        开启时要建一个「最高权限」计划任务 —— 这一步本身需要管理员权限，
        所以未提权时就先引导用户以管理员身份重启一次，重启后自动完成创建。
        """
        on = self.admin_var.get()
        if on:
            if not is_elevated():
                self.admin_var.set(False)
                self._save()
                if self._prompt_elevate_for_admin():
                    return
                return
            ok, out = create_elevated_task()
            if ok:
                self.cfg["run_as_admin"] = True
                self._save()
                # 任务已经在「登录时」自动拉起，旧的 Startup\*.cmd 就成了重复入口，
                # 留着会拉起两个实例（一个普通一个管理员），必须清掉。
                self._remove_startup_cmd()
                self.auto_var.set(False)
                self.cfg["autostart"] = False
                self._save()
                self.set_status("已记住管理员模式：开机与双击都将是管理员（不再弹 UAC）")
                self.update_perm_label()
            else:
                self.admin_var.set(False)
                self._save()
                messagebox.showwarning(
                    APP_TITLE,
                    "创建「最高权限」计划任务失败。\n\n"
                    "常见原因：当前不是管理员，或系统策略禁止创建任务。\n\n"
                    f"系统返回：{out.strip()[:300]}",
                    parent=self.root)
            return
        # 关闭：删掉任务，并同步关掉「开机自启」（那是任务在管的事）
        self.cfg["run_as_admin"] = False
        self._save()
        if task_exists():
            ok, out = delete_elevated_task()
            if not ok and is_elevated():
                messagebox.showwarning(APP_TITLE,
                                       f"删除计划任务失败：{out.strip()[:200]}",
                                       parent=self.root)
                return
        self.set_status("已关闭管理员模式，下次启动恢复为普通权限")

    def _remove_startup_cmd(self):
        try:
            if os.path.exists(AUTOSTART_CMD):
                os.remove(AUTOSTART_CMD)
        except Exception:
            pass

    def _prompt_elevate_for_admin(self):
        """未提权时询问是否顺手提权重启以创建任务；返回 True 表示已发起重启。"""
        if not messagebox.askyesno(
                APP_TITLE,
                "「记住管理员模式」需要创建一条「以最高权限运行」的计划任务，"
                "这一步本身需要管理员权限。\n\n"
                "现在以管理员身份重启一次来完成设置吗？\n"
                "（这次会弹一次 UAC，之后启动就再也不弹了）",
                parent=self.root):
            self.set_status("已取消：未开启管理员模式")
            return False
        # 标记"本次提权是为了落实管理员模式"，提权后的实例凭它自动补建任务
        self._pending_admin_setup = True
        self._save()
        self.elevate()
        return True

    def _admin_selfheal(self):
        """启动时的管理员模式自愈，四种情况：

        1. 上次发起了「为管理员模式提权重启」，且这次真的拿到了管理员 → 补建任务
        2. 配置开着、任务却不在（被删/换机器）→ 以管理员身份补建
        3. 配置开着、任务在，但 exe 换了位置 → 按新位置更新任务
        4. 配置关了、任务还残留 → 删掉，免得下次登录又冒出个管理员实例

        注意 1 和 2 的区别：**pending 阶段 run_as_admin 还是 False**
        （`set_run_as_admin` 只在任务真的建成之后才把它置 True），
        所以这两件事必须分开判断，否则提权重启回来什么也不会发生。
        """
        want = bool(self.cfg.get("run_as_admin"))
        pending = bool(self.cfg.get("_pending_admin_setup"))

        if pending:
            # 待办一律就地清掉：不管这次提权成没成，都不能留个"永远待办"。
            self.cfg["_pending_admin_setup"] = False
            self._save()
            if not is_elevated():
                # 用户在 UAC 上点了「否」，或者提权被策略拦了 —— 保持关闭状态
                self.admin_var.set(False)
                self.cfg["run_as_admin"] = False
                self._save()
                self.set_status("未取得管理员权限，管理员模式未开启")
                return
            ok, out = create_elevated_task()
            if ok:
                self.cfg["run_as_admin"] = True
                self.admin_var.set(True)
                # 任务已在「登录时」自动拉起，旧的 Startup\*.cmd 就成了重复入口，
                # 留着会拉起两个实例（一个普通一个管理员），必须清掉。
                self._remove_startup_cmd()
                self.auto_var.set(False)
                self.cfg["autostart"] = False
                self._save()
                self.update_perm_label()
                self.set_status("管理员模式已生效：开机与双击都将是管理员（不再弹 UAC）")
            else:
                self.admin_var.set(False)
                self.cfg["run_as_admin"] = False
                self._save()
                self.set_status("管理员模式设置失败（已保持关闭）")
                messagebox.showwarning(
                    APP_TITLE,
                    "创建「最高权限」计划任务失败，管理员模式没有开启。\n\n"
                    "常见原因：系统策略禁止创建计划任务，或杀软拦截了 schtasks。\n\n"
                    f"系统返回：{str(out).strip()[:300]}",
                    parent=self.root)
            return

        if want:
            if not task_exists():
                if is_elevated():
                    ok, _ = create_elevated_task()
                    if ok:
                        self._remove_startup_cmd()
                        self.auto_var.set(False)
                        self.cfg["autostart"] = False
                        self._save()
                        self.set_status("已重建管理员模式计划任务（之前缺失或已失效）")
            elif sync_task_if_moved():
                self.set_status("计划任务已按程序新位置更新")
        else:
            if task_exists() and is_elevated():
                delete_elevated_task()

    # ---------- 数据刷新 ----------
    def refresh(self):
        ns = self._make_args()
        ns.list = True
        data = run_with_args(ns)
        if not data or not data.get("ok"):
            self.set_status("读取屏幕/窗口信息失败")
            return
        self.mons = data.get("monitors", [])
        self.wins = data.get("windows", [])
        self._ensure_perm()          # 屏数变了要重新校验老的映射
        self._sync_cards()
        self.tree.delete(*self.tree.get_children())
        for i, w in enumerate(sorted(self.wins, key=lambda x: (x["monitor"] or 9, x["left"]))):
            self.tree.insert("", "end", values=(
                f"屏{w['monitor']}", w["state"],
                f"{w['left']},{w['top']}  {w['width']}×{w['height']}",
                w["proc"] or "（管理员进程）", w.get("label") or w["title"], ""),
                tags=("odd",) if i % 2 else ())
        self.count_lbl.configure(text=f"共 {len(self.wins)} 个窗口　·　{self._map_desc()}")
        self.set_status("已刷新")
        return data

    # ---------- 排列编辑器 ----------
    CARD_COLS = 2
    MARKS = "①②③④⑤⑥⑦⑧⑨⑩"

    def _map_desc(self):
        """映射方式说明。按对判断 —— 多屏混排时不同屏对的方式可能不一样。"""
        if not self.perm:
            return "没有任何屏参与（全部不动）"
        forced = bool(self.scale_var.get())
        n = n_scale = 0
        for s, d in self.perm.items():
            if not (1 <= s <= len(self.mons) and 1 <= d <= len(self.mons)):
                continue
            a, b = self.mons[s - 1], self.mons[d - 1]
            n += 1
            if forced or (a["width"], a["height"]) != (b["width"], b["height"]):
                n_scale += 1
        if not n:
            return "没有任何屏参与（全部不动）"
        if n_scale == 0:
            return f"{n} 对全为纯平移"
        if n_scale == n:
            return f"{n} 对全为等比缩放"
        return f"{n} 对中 {n_scale} 对缩放、{n - n_scale} 对平移"

    def _is_involution(self):
        """排列是不是「自逆」的 —— 即作用两次回到原状（对换就是这样）。

        只有这种情况才可以说「再按一次换回」。三屏轮转 {1:2,2:3,3:1} 要转满 3 次才复位。
        """
        if not self.perm:
            return False
        return all(self.perm.get(d) == s for s, d in self.perm.items())

    def _ensure_perm(self):
        if self._perm_n == len(self.mons):
            return
        self._perm_n = len(self.mons)
        self.perm = self._load_perm(len(self.mons))
        self.cfg["permute"] = {str(k): v for k, v in sorted(self.perm.items())}

    def _load_perm(self, n):
        """读配置里的排列；任何一项不合法（屏号越界、目标重复）就整体退回默认。"""
        default = {1: 2, 2: 1} if n >= 2 else {}
        raw = self.cfg.get("permute")
        if not isinstance(raw, dict):
            return dict(default)
        perm = {}
        for k, v in raw.items():
            try:
                s, d = int(k), int(v)
            except (TypeError, ValueError):
                return dict(default)
            if not (1 <= s <= n and 1 <= d <= n) or s == d:
                return dict(default)
            perm[s] = d
        if len(set(perm.values())) != len(perm):     # 多块屏挤到同一块目标屏
            return dict(default)
        return perm

    def _sync_cards(self):
        """按屏数建/更新卡片。屏数变了才重建控件，否则只刷新文字和下拉项。"""
        n = len(self.mons)
        if n != len(self.card_widgets):
            for w in self.cards.winfo_children():
                w.destroy()
            self.card_widgets = []
            cols = min(self.CARD_COLS, max(1, n))
            for i in range(n):
                f = ttk.Frame(self.cards, style="Card.TFrame", padding=12)
                f.grid(row=i // cols, column=i % cols, sticky="nsew",
                       padx=(0 if i % cols == 0 else 10, 0), pady=(0, 8))
                tag = ttk.Label(f, text="—", style="Card.TLabel", font=(FONT, 11, "bold"))
                tag.pack(anchor="w")
                info = ttk.Label(f, text="", style="Dim.TLabel", font=(FONT, 9))
                info.pack(anchor="w", pady=(3, 0))
                r = ttk.Frame(f, style="Card.TFrame")
                r.pack(fill="x", pady=(9, 0))
                ttk.Label(r, text="送往", style="Dim.TLabel").pack(side="left")
                var = tk.StringVar()
                cb = ttk.Combobox(r, textvariable=var, state="readonly", width=10,
                                  font=(FONT, 9))
                cb.pack(side="left", padx=(7, 0), fill="x", expand=True)
                cb.bind("<<ComboboxSelected>>", lambda e, i=i: self._on_perm_change(i))
                self.card_widgets.append({"frame": f, "tag": tag, "info": info,
                                          "var": var, "combo": cb})
            for c in range(cols):
                self.cards.columnconfigure(c, weight=1)

        for i, cw in enumerate(self.card_widgets):
            m = self.mons[i]
            mark = self.MARKS[i] if i < len(self.MARKS) else f"({i + 1})"
            star = "　★主屏" if m["primary"] else ""
            dev = m["device"].split("\\")[-1]
            dest = self.perm.get(i + 1)
            cw["tag"].configure(text=f"{mark}　{dev}{star}")
            cw["info"].configure(
                text=f"{m['width']}×{m['height']}　　左 {m['left']}, 上 {m['top']}"
                     + (f"　→　送往 屏{dest}" if dest else "　→　不动（不参与）"))
            cw["combo"].configure(values=["不动"] + [f"屏{j + 1}" for j in range(n) if j != i])
            cw["var"].set(f"屏{dest}" if dest else "不动")

        self.perm_hint.configure(
            text="全部不动：按热键不会有任何窗口被移动" if not self.perm
            else "、".join(f"屏{s}→屏{d}" for s, d in sorted(self.perm.items())))
        # 「恢复原状」只在对换时成立；轮转要说「再转一次」
        self.redo_btn.configure(
            text="恢复原状（再换一次）" if self._is_involution() else "再轮转一次")

    def _on_perm_change(self, idx):
        """用户改了某块屏的去向。同一块目标屏不能被两块源屏选走，撞车就把先来的让开。"""
        just = idx + 1
        chosen = {}
        for i, cw in enumerate(self.card_widgets):
            v = cw["var"].get()
            if v.startswith("屏"):
                try:
                    chosen[i + 1] = int(v[1:])
                except ValueError:
                    pass
        final, note = {}, None
        for s in sorted(chosen, key=lambda k: (k != just, k)):
            d = chosen[s]
            if d == s:               # 送往自己 = 不动。下拉里不会给出这种选项，
                continue             # 但配置可能被手改坏，兜一下免得把自环送给引擎
            owner = next((k for k, v in final.items() if v == d), None)
            if owner is not None:
                note = f"屏{d} 已被屏{owner} 选为目标，屏{s} 改为不动"
                continue
            final[s] = d
        self.perm = final
        self.cfg["permute"] = {str(k): v for k, v in sorted(final.items())}
        self._save()
        if note:
            self.set_status(note)
        self.root.after(60, self.refresh)     # 等下拉收起再重建，避免自己销毁自己

    def _apply_perm(self, perm, msg):
        n = len(self.mons)
        clean, seen = {}, set()
        for s in sorted(perm):
            d = perm[s]
            if not (1 <= s <= n and 1 <= d <= n) or s == d or d in seen:
                continue
            clean[s] = d
            seen.add(d)
        self.perm = clean
        self.cfg["permute"] = {str(k): v for k, v in sorted(clean.items())}
        self._save()
        self.set_status(msg)
        self.refresh()

    def preset_rotate(self):
        n = len(self.mons)
        if n < 2:
            self.set_status("只有一块屏，无法轮转")
            return
        self._apply_perm({i + 1: (i + 1) % n + 1 for i in range(n)},
                         "已设为环形轮转：" + "→".join(f"屏{i + 1}" for i in range(n)) + "→屏1")

    def preset_pair(self):
        if len(self.mons) < 2:
            self.set_status("只有一块屏，无法互换")
            return
        self._apply_perm({1: 2, 2: 1}, "已设为互换最左两块，其余不动")

    def preset_none(self):
        self._apply_perm({}, "已设为全部不动")

    def _make_args(self, dry=False):
        class A:
            pass
        a = A()
        a.list = False
        a.dry_run = dry
        a.monitors = None
        a.rotate = None
        # "" 是合法的排列写法，表示「全部不动」；None 才是「没指定、用默认」
        a.permute = ",".join(f"{s}>{d}" for s, d in sorted(self.perm.items()))
        a.scale = bool(self.scale_var.get())
        a.translate_only = False
        a.exclude_title = self.ex_var.get().strip() or None
        a.only_title = None
        a.only_proc = None
        a.exclude_pid = str(os.getpid())      # 别把本窗口自己换走
        a.skip_minimized = bool(self.skipmin_var.get())
        a.verbose = False
        a.quiet = True
        a.json = True
        a.gui = False
        return a

    # ---------- 执行 ----------
    def do_swap(self, dry=False):
        if self.busy:
            self.set_status("上一次操作还在进行中…")
            return
        self.busy = True
        self.set_status("预览中…" if dry else "正在互换窗口…")

        def work():
            try:
                data = run_with_args(self._make_args(dry))
            except Exception as e:  # noqa: BLE001
                data = {"ok": False, "errors": [str(e)]}
            self.q.put(("swapped", (data, dry)))

        threading.Thread(target=work, daemon=True).start()

    def _on_swapped(self, data, dry):
        self.busy = False
        data = data or {}
        if not data.get("ok"):
            errs = data.get("errors") or ["未知错误"]
            self.set_status(f"失败：{errs[0]}")
            denied = data.get("denied", 0)
            if dry:
                return
            if self.hidden:      # 界面收在托盘时不能用模态框（会弹在看不见的地方）
                if self.hk:
                    self.hk.notify(f"{APP_TITLE}：互换未完成",
                                   "\n".join(errs[:4]), warning=True)
                return
            if denied:
                if messagebox.askyesno(
                        APP_TITLE,
                        f"有 {denied} 个窗口属于管理员权限运行的程序，普通权限搬不动它们。\n\n"
                        "是否以管理员身份重启本程序？\n"
                        "（会弹一次 UAC，界面会关闭后自动重开，热键会重新注册）",
                        parent=self.root):
                    self.elevate()
                return
            messagebox.showwarning(APP_TITLE, "部分窗口未能互换：\n\n"
                                   + "\n".join(errs[:6]), parent=self.root)
            return

        if dry:
            plan = data.get("plan", [])
            for item in self.tree.get_children():
                self.tree.set(item, "to", "")
            info = {}
            for p in plan:
                info[(p["title"], p["from"]["left"])] = p
            for item in self.tree.get_children():
                v = self.tree.item(item, "values")
                try:
                    key = (v[4], int(v[2].split(",")[0]))
                except (ValueError, IndexError):
                    continue
                p = info.get(key)
                if p:
                    r = p["to"]
                    self.tree.set(item, "to", f"{p['direction']} → {r['left']},{r['top']}")
            self.set_status(f"预览完成：{len(plan)} 个窗口将被互换（未做任何改动）"
                            if plan else "没有需要互换的窗口（当前都在同一块屏上？）")
            return

        moved = data.get("moved", 0)
        self.refresh()          # 注意顺序：refresh 会写状态栏，完成提示要放在它后面
        msg = f"完成：{moved} 个窗口已互换" + ("（本次没有匹配的窗口）" if not moved else "")
        # 只有「对换」（排列是自逆的，作用两次回到原状）才谈得上「再按一次换回」；
        # 三屏轮转要按满一圈才复位，说「再按一次换回」是错的
        tail = ("再按一次热键即可换回" if self._is_involution()
                else "这是多屏轮转，继续按热键会一圈一圈地换下去")
        self.set_status(f"{msg}　·　{tail}")
        if self.hidden and self.hk and self.notify_var.get():
            self.hk.notify(APP_TITLE, f"{msg}\n{tail}")

    # ---------- 托盘溢出区（Win11）----------
    def _overflow_left(self):
        """「隐藏的图标」面板左边界；拿不到返回 None。用它判断图标是不是被收进去了。"""
        try:
            h = u32.FindWindowW("TopLevelWindowForOverflowXamlIsland", None)
            if not h:
                return None
            r = wintypes.RECT()
            if not u32.GetWindowRect(h, ctypes.byref(r)):
                return None
            return r.left
        except Exception:
            return None

    def _overflow_hint(self, tip):
        """提示用户把图标从 ^ 里拖出来。

        实测结论（2026-10-11，Win11 + 自动隐藏任务栏）：
          * `IsPromoted=1` 写进 HKCU\\Control Panel\\NotifyIconSettings 后**不会立刻生效**
            —— explorer 在图标创建时读的是自己内存里的缓存，而且在图标被删掉时
            还会把缓存里的状态写回注册表，把我们写的值覆盖掉。
          * 所以唯一**确实管用**的办法是用户手动把图标从 ^ 拖到任务栏上，
            拖一次之后由 explorer 自己记住。别再承诺「已自动置顶」了。
        """
        if self.cfg.get("_overflow_hint_acked"):
            return
        self.cfg["_overflow_hint_acked"] = True
        self._save()
        if self.hidden:
            # 开机自启、界面收在托盘里：只能发气泡，别弹窗打扰
            if self.hk:
                self.hk.notify(APP_TITLE, tip, warning=True)
        else:
            messagebox.showinfo(APP_TITLE, tip + "\n\n（这条提示只出现一次）",
                                parent=self.root)

    def _maybe_hint_overflow(self, attempt=0):
        """图标虽然注册成功，但 Win11 默认会把它塞进 ^ 溢出区。

        两个坑：
        - `TopLevelWindowForOverflowXamlIsland` 是**懒创建**的，程序刚起来时
          它还不存在，只查一次必然查不到 —— 所以要隔一会儿重试几轮。
        - 开机自启的实例界面是收进托盘的，状态栏文字用户根本看不见 ——
          所以要弹一次说明（隐藏状态下退化成气泡通知）。
        """
        if self._overflow_hinted:
            return
        rect = self.hk.icon_rect() if self.hk else None
        left = self._overflow_left()
        if rect is None or left is None:
            if attempt < 8:
                self.root.after(1500, lambda: self._maybe_hint_overflow(attempt + 1))
            return
        if rect.left < left:
            return                      # 已在可见区，不用管
        self._overflow_hinted = True
        # 顺手把「固定显示」标记写上（对下次 explorer 重启/重新登录有用），
        # 但不能指望它当场生效，所以提示文案不依赖它的返回值
        if self.hk:
            self.hk.promote_icon()
        self.set_status("托盘图标已就绪（在 ^ 隐藏区里，拖出来一次即可固定）")
        self._overflow_hint(
            "托盘图标已经注册好了，但被 Win11 放进了任务栏的「^ 隐藏的图标」里，"
            "所以你在托盘上看不到它。\n\n"
            "解决办法（一次就够）：\n"
            "  1. 点任务栏右下角的  ^\n"
            "  2. 在弹出的面板里找到「多屏窗口互换」\n"
            "  3. 把它拖到任务栏上（或右键选「固定到任务栏」）\n\n"
            "之后它就会一直显示在那里；热键不受影响，随时可用。")

    # ---------- 事件循环 ----------
    def _poll(self):
        # 第二个实例被单实例守卫挡下时会留下旗标，代表"用户又点了一次图标"，
        # 这里消费掉并把界面亮出来 —— 双击 exe 就等于把已运行的窗口叫到前台。
        if _consume_show_flag():
            self.show_window()
        # 托盘心跳：每 ~20 秒确认一次图标还在。开机自启那一份偶尔会赶上
        # explorer 还没就绪，图标要么没挂上、要么后面被系统悄悄清掉 ——
        # 这里发现「已经注册成功但现在查不到矩形」就重挂一次，用户不用手动重启程序。
        self._tray_beat += 1
        if self._tray_beat >= 250:                  # 250 × 80ms ≈ 20s
            self._tray_beat = 0
            if self.tray_ok and self.hk and self.hk.icon_rect() is None:
                if self._tray_reinit_left > 0:
                    self._tray_reinit_left -= 1
                    self.hk.reinit_tray()
                    if not self._tray_reinit_left:
                        self.set_status("托盘图标反复挂不上，热键照常可用（可试着重启一下资源管理器）")
        try:
            while True:
                ev, val = self.q.get_nowait()
                if ev == "hotkey":
                    self.hk_count += 1
                    self._hk_label(ok=True)
                    self.set_status(f"热键触发第 {self.hk_count} 次，正在互换…")
                    self.do_swap()
                elif ev == "hk_ok":
                    self.hk_shown = val
                    self._hk_label(ok=True)
                    self.set_status(f"热键 {val} 已生效，随时按下即可互换；点 X 会收进托盘")
                elif ev == "tray_ok":
                    self.tray_ok = bool(val)
                    self._hk_label(ok=True)
                    if not val:
                        self.set_status("托盘图标创建失败（热键仍可用）")
                    else:
                        self._maybe_hint_overflow()
                elif ev == "tray_reinit":
                    # 收到的是 TrayThread 的回传，转成命令让它自己重挂
                    if self.hk:
                        self.hk.reinit_tray()
                elif ev == "hk_fail":
                    self.hk_state.configure(text=f"✗ {val}", foreground=C_WARN)
                elif ev == "tray_show":
                    self.show_window()
                elif ev == "tray_swap":
                    self.do_swap()
                elif ev == "tray_preview":
                    self.show_window()
                    self.do_swap(dry=True)
                elif ev == "tray_exit":
                    self.quit_app()
                elif ev == "swapped":
                    self._on_swapped(*val)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _hk_label(self, ok=True):
        extra = ""
        if self.hk_count:
            extra += f"　·　已触发 {self.hk_count} 次"
        extra += "　·　托盘已就绪" if self.tray_ok else "　·　托盘未就绪"
        self.hk_state.configure(text=f"{'✓' if ok else '✗'} 已注册：{self.hk_shown}{extra}",
                                foreground=C_OK if ok else C_WARN)

    def set_status(self, text):
        self.status.configure(text=text)

    def _save(self, *_):
        self.cfg.update({
            "hotkey": self.hk_var.get().strip(),
            "exclude_title": self.ex_var.get().strip(),
            "skip_minimized": bool(self.skipmin_var.get()),
            "scale": bool(self.scale_var.get()),
            "restore_focus": bool(self.focus_var.get()),
            "autostart": bool(self.auto_var.get()),
            "notify_on_swap": bool(self.notify_var.get()),
            "permute": {str(k): v for k, v in sorted(self.perm.items())},
            "run_as_admin": bool(self.admin_var.get()),
        })
        # 只落已知的键 —— 顺便把老版本留下的 start_minimized 之类陈旧配置清掉，
        # 否则它会一直躺在那儿让人误以为是有效的设置
        save_cfg({k: self.cfg[k] for k in DEFAULT_CFG if k in self.cfg})

    def on_close(self):
        """点 X：收进托盘（不占任务栏），热键继续有效。"""
        self.hide_to_tray()

    def _on_unmap(self, event):
        """点最小化（—）也收进托盘，而不是缩到任务栏。"""
        if event.widget is not self.root or self._hiding:
            return
        try:
            if self.root.state() == "iconic":
                self._hiding = True
                self.root.after(10, self._minimize_to_tray)
        except Exception:
            pass

    def _minimize_to_tray(self):
        try:
            self.hide_to_tray()
        finally:
            self._hiding = False

    def hide_to_tray(self):
        if self.hidden:
            return
        self._save()
        self.root.withdraw()
        self.hidden = True
        self.set_status("已收进托盘")
        if not self.tray_hint_shown:
            self.tray_hint_shown = True
            if self.hk:
                self.hk.notify(APP_TITLE,
                               "已收进托盘，热键仍然有效。\n"
                               "左键点托盘图标显示界面，右键可互换 / 显示 / 退出。")

    def show_window(self):
        """从托盘恢复主界面。"""
        try:
            self.root.deiconify()
            self.root.state("normal")
            # 隐藏期间 geometry 请求是被忽略的（开机自启那条路就是这样进来的），
            # 所以亮出来之后补一次尺寸校正，免得弹出来是个小方块。
            self._fit(recenter=False)
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass
        self.hidden = False

    def quit_app(self):
        self._save()
        if self.hk:
            self.hk.stop()
            time.sleep(0.15)          # 等托盘图标摘掉再退
        try:
            self.root.destroy()
        except Exception:
            pass


# ============================================================ 运行交换（同进程，但走函数调用）

def sw_resource_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def run_with_args(ns):
    """用命名空间直接跑 swap()（argparse 是按 argv 解析的，传对象更省事），回收 JSON。"""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sw.swap(ns)
    text = buf.getvalue().strip()
    try:
        data = json.loads(text.splitlines()[-1]) if text else {}
    except Exception:
        data = {"ok": False, "errors": [f"输出解析失败: {text[:200]}"]}
    data["_rc"] = rc
    return data


# ============================================================ 入口


def _maybe_hand_over_to_admin(startup_min):
    """「记住管理员模式」的启动接管。

    配置里开着 run_as_admin，但当前实例是普通权限（比如用户直接双击了 exe，
    而不是走计划任务），就通过计划任务把真正的工作实例以管理员拉起，然后自己退出。
    这样"双击 exe"和"开机自启"两条路都能稳稳定在管理员模式。

    返回 True 表示已经交棒，调用方应当立刻结束本进程。
    """
    cfg = load_cfg()
    if not cfg.get("run_as_admin"):
        return False
    if is_elevated():
        return False                 # 已经是管理员（多半就是任务拉起来的），正常跑
    if not task_exists():
        return False                 # 任务还没建好，先按普通权限跑，等自愈补建
    # 任务的动作固定带 --minimized（开机静默用）。手动双击时得让新实例
    # 知道"这次要显示界面"，所以先把旗标写进配置，交棒后再清掉。
    if not startup_min:
        cfg["_handover_show"] = True
        save_cfg({k: cfg[k] for k in DEFAULT_CFG if k in cfg})
    # 让位给即将被任务拉起的实例：不释放互斥体的话，它一启动就撞上单实例守卫，
    # 会以为"已经有一个在跑"然后自杀，结果谁也起不来。
    _release_single_instance()
    ok, _ = start_elevated_task()
    if not ok:
        cfg["_handover_show"] = False
        save_cfg({k: cfg[k] for k in DEFAULT_CFG if k in cfg})
        _acquire_single_instance()   # 交棒失败，把互斥体拿回来自己用
        return False
    # 给自己一点时间让新实例起来，避免托盘/热键出现空档
    time.sleep(0.6)
    return True


def main():
    # 窗口标题里有 GBK 编不出来的字符时，print 会把程序崩掉 —— 先给控制台打上补丁。
    # （GUI 本身不 print，但 --list/--diag 这些命令行路径会。）
    try:
        sw._harden_console()
    except Exception:
        pass
    argv = sys.argv[1:]
    if "--diag" in argv:
        return _diag_report()
    # --minimized 只由「开机自启」那个启动项传进来。
    # 手动双击**不带**这个参数，所以一定会看到界面 —— 不会出现「点了没反应、
    # 以为没启动」的情况。用户点 X 之后才收进托盘。
    startup_min = "--minimized" in argv
    argv = [a for a in argv if a != "--minimized"]
    if argv:                       # 带实际参数 → 命令行模式，透传给核心脚本
        return sw.main(argv)

    # 单实例守卫必须放在交棒**之前**：
    # 若「任务拉起的实例」已经在跑，交棒会扑空（任务策略是 IgnoreNew），
    # 结果是双击 exe 什么都不发生。先抢互斥体失败 → 直接把老实例叫到前台。
    if not _acquire_single_instance():
        _signal_show_existing()
        return 0
    _consume_show_flag()           # 清掉可能残留的旧旗标，免得开机时莫名冒出窗口

    # 「记住管理员模式」：非管理员时交棒给计划任务拉起的实例
    if _maybe_hand_over_to_admin(startup_min):
        return 0

    root = tk.Tk()
    # 先藏起来再建界面。
    # 因为 App.__init__ 里为了量高度会调 update_idletasks()，那一下会把窗口映射出来，
    # 于是开机自启时会闪过一个窗口才收进托盘。先 withdraw 就彻底不闪。
    root.withdraw()
    app = App(root)
    app.start_hotkey()
    # 交棒过来的实例：任务动作带的是 --minimized，但这个旗标说明该显示界面
    if app.cfg.get("_handover_show"):
        app.cfg["_handover_show"] = False
        app._save()
        startup_min = False
    if startup_min:
        app.hidden = True            # 开机自启：静默待在托盘里
        app.tray_hint_shown = True
    else:
        root.deiconify()             # 手动启动：一定把界面亮出来
    # start_hotkey() 会往状态栏写「✓ 已注册…」，内容高度又变了，
    # 所以显示出来之后必须再校正一次尺寸（否则底部选项被裁）。
    app._fit()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
