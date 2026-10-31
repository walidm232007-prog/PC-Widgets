"""Windows integration for Glass Dock. No Explorer injection or registry edits.

The native taskbar is only hidden for the lifetime of a pipe to a small guard
process. EOF (including a crash of the board) restores it. Other monitors keep
their native taskbars. This module also runs standalone as the guard.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os
import subprocess
import sys

u = C.WinDLL("user32", use_last_error=True)
k = C.WinDLL("kernel32", use_last_error=True)
dwm = C.WinDLL("dwmapi")
ENUM = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
u.EnumWindows.argtypes = [ENUM, W.LPARAM]
u.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
u.FindWindowW.restype = W.HWND
u.GetWindow.argtypes = [W.HWND, W.UINT]
u.GetWindow.restype = W.HWND
u.GetForegroundWindow.restype = W.HWND
u.GetWindowLongPtrW.argtypes = [W.HWND, C.c_int]
u.GetWindowLongPtrW.restype = C.c_ssize_t
for name in ("IsWindowVisible", "IsWindow", "IsIconic", "SetForegroundWindow"):
    getattr(u, name).argtypes = [W.HWND]
u.ShowWindow.argtypes = [W.HWND, C.c_int]
u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE
k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
k.CloseHandle.argtypes = [W.HANDLE]
dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
u.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM,
                                 W.UINT, W.UINT, C.POINTER(C.c_size_t)]
u.SendMessageTimeoutW.restype = C.c_ssize_t
u.GetClassLongPtrW.argtypes = [W.HWND, C.c_int]
u.GetClassLongPtrW.restype = C.c_size_t
u.CopyIcon.argtypes = [W.HICON]
u.CopyIcon.restype = W.HICON
u.DestroyIcon.argtypes = [W.HICON]


class APPBARDATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uCallbackMessage", W.UINT),
                ("uEdge", W.UINT), ("rc", W.RECT), ("lParam", W.LPARAM)]


_shell = C.WinDLL("shell32")
_shell.SHAppBarMessage.argtypes = [W.DWORD, C.POINTER(APPBARDATA)]
_shell.SHAppBarMessage.restype = C.c_size_t


class WorkArea:
    """Reserve only the extra height beyond Explorer's existing bottom strip.

    Uses a separate hidden window so Explorer's appbar placement never moves
    the visible rounded dock. The shell releases this when that window dies.
    https://learn.microsoft.com/en-us/windows/win32/shell/abm-querypos
    """
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.signature = None

    def reserve(self, rect, height, taskbar):
        data = APPBARDATA(cbSize=C.sizeof(APPBARDATA), hWnd=self.hwnd, uEdge=3)
        bar = W.RECT()
        u.GetWindowRect(taskbar, C.byref(bar))
        auto = bool(_shell.SHAppBarMessage(4, C.byref(data)) & 1)
        existing = 0 if auto else max(0, rect[3] - bar.top)
        extra = max(0, height - existing)
        signature = (rect, height, taskbar, auto, existing)
        if signature == self.signature:
            return
        self.release()
        if not extra:
            return
        data.uCallbackMessage = 0x8000 + 73
        if not _shell.SHAppBarMessage(0, C.byref(data)):
            return
        data.rc = W.RECT(rect[0], rect[3] - extra, rect[2], rect[3])
        _shell.SHAppBarMessage(2, C.byref(data))
        data.rc.top = data.rc.bottom - extra
        _shell.SHAppBarMessage(3, C.byref(data))
        self.signature = signature

    def release(self):
        if self.signature is not None:
            data = APPBARDATA(cbSize=C.sizeof(APPBARDATA), hWnd=self.hwnd)
            _shell.SHAppBarMessage(1, C.byref(data))
            self.signature = None


@dataclass(frozen=True)
class Task:
    hwnd: int
    title: str
    path: str


def tasks() -> list[Task]:
    """Visible, unowned application windows, excluding cloaked virtual desktops."""
    found = []
    paths = {}

    @ENUM
    def visit(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        style = u.GetWindowLongPtrW(hwnd, -20)
        if style & 0x80 or (u.GetWindow(hwnd, 4) and not style & 0x40000):
            return True
        pid = W.DWORD()
        u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        # Do not exclude our whole process: the PC Widgets app is a normal
        # taskbar window too. Its cards/dock are already excluded as tool windows.
        cloaked = W.DWORD()
        dwm.DwmGetWindowAttribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked))
        if cloaked.value:
            return True
        cls, title = C.create_unicode_buffer(256), C.create_unicode_buffer(1024)
        u.GetClassNameW(hwnd, cls, len(cls))
        u.GetWindowTextW(hwnd, title, len(title))
        if not title.value or cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return True
        if pid.value not in paths:
            path = C.create_unicode_buffer(32768)
            size = W.DWORD(len(path))
            process = k.OpenProcess(0x1000, False, pid.value)
            try:
                paths[pid.value] = (path.value if process and
                    k.QueryFullProcessImageNameW(process, 0, path, C.byref(size)) else "")
            finally:
                if process:
                    k.CloseHandle(process)
        found.append(Task(int(hwnd), title.value, paths[pid.value]))
        return True

    if not u.EnumWindows(visit, 0):
        raise C.WinError(C.get_last_error())
    return found


def window_icon(hwnd: int):
    """An owned copy of the window's actual icon, not its host executable's.

    Hosted/Store apps often have a generic executable icon. Never wait on an
    unresponsive window without a timeout. The caller must DestroyIcon the copy.
    """
    result = C.c_size_t()
    for size in (1, 2, 0):  # ICON_BIG, SMALL2, SMALL
        # SMTO_BLOCK prevents incoming messages re-entering Qt while waiting;
        # a reentrant paint can otherwise call shell COM in a sent-message
        # handler (RPC_E_CANTCALLOUT_ININPUTSYNCCALL).
        if u.SendMessageTimeoutW(hwnd, 0x7F, size, 0, 3, 40, C.byref(result)) and result.value:
            return u.CopyIcon(result.value)
    for slot in (-14, -34):  # GCLP_HICON, GCLP_HICONSM
        icon = u.GetClassLongPtrW(hwnd, slot)
        if icon:
            return u.CopyIcon(icon)
    return None


def activate(hwnd: int) -> None:
    if not u.IsWindow(hwnd):
        return
    if u.GetForegroundWindow() == hwnd:
        u.ShowWindow(hwnd, 6)  # minimise
    else:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)
        u.SetForegroundWindow(hwnd)


def shortcut(key: int | None = None) -> None:
    """Open the real Windows Start / quick settings / notification UI."""
    u.keybd_event(0x5B, 0, 0, 0)
    if key is not None:
        u.keybd_event(key, 0, 0, 0)
        u.keybd_event(key, 0, 2, 0)
    u.keybd_event(0x5B, 0, 2, 0)


class TaskbarLease:
    def __init__(self):
        self.guard = None
        self.hwnd = None

    def hide(self) -> bool:
        hwnd = u.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return False
        if self.guard is not None:
            if self.guard.poll() is not None:
                self.restore()
                return False
            if hwnd != self.hwnd:  # Explorer restarted: acquire a fresh lease.
                self.restore()
                return self.hide()
        elif u.IsWindowVisible(hwnd):
            # The guard starts before hiding. If process creation fails, leave
            # Explorer alone. All pipe handles close when the parent dies.
            try:
                executable = sys.executable
                if os.path.basename(executable).lower() == "pythonw.exe":
                    executable = os.path.join(os.path.dirname(executable), "python.exe")
                self.guard = subprocess.Popen(
                    [executable, os.path.abspath(__file__), "--guard", str(hwnd)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                self.hwnd = hwnd
            except OSError:
                self.guard = None
                return False
        else:
            return False  # do not take ownership of somebody else's hidden bar
        u.ShowWindow(hwnd, 0)
        return True

    def reveal(self) -> None:
        if self.hwnd and u.IsWindow(self.hwnd):
            u.ShowWindow(self.hwnd, 5)

    def restore(self) -> None:
        self.reveal()
        guard, self.guard = self.guard, None
        self.hwnd = None
        if guard is not None:
            guard.stdin.close()
            # Wait for restoration before another lease can hide the taskbar.
            try:
                guard.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


def _guard(hwnd: int) -> None:
    try:
        sys.stdin.buffer.read()
    finally:
        if u.IsWindow(hwnd):
            u.ShowWindow(hwnd, 5)


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--guard":
    _guard(int(sys.argv[2]))
