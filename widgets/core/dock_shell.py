"""Windows integration for Glass Dock. No Explorer injection or registry edits.

The native taskbar is only hidden for the lifetime of a pipe to a small guard
process. EOF (including a crash of the board) restores it. Other monitors keep
their native taskbars. This module also runs standalone as the guard, so it
loads nothing heavier than ctypes; Qt is imported only by the functions that
hand back pictures.

Everything here that reads another process - window lists, app identities,
icons, the speakers - is meant for the dock's worker thread. Those calls can
block on a hung app or pump COM messages, and a COM pump inside a paint handler
re-enters paint: that is how the first icon code crashed.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os
import subprocess
import sys
import uuid

u = C.WinDLL("user32", use_last_error=True)
k = C.WinDLL("kernel32", use_last_error=True)
dwm = C.WinDLL("dwmapi")
gdi = C.WinDLL("gdi32")
ole = C.WinDLL("ole32")
_shell = C.WinDLL("shell32")
_ver = C.WinDLL("version")
_ip = C.WinDLL("iphlpapi")
ENUM = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
u.EnumWindows.argtypes = [ENUM, W.LPARAM]
u.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
u.FindWindowW.restype = W.HWND
u.FindWindowExW.argtypes = [W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR]
u.FindWindowExW.restype = W.HWND
u.GetWindow.argtypes = [W.HWND, W.UINT]
u.GetWindow.restype = W.HWND
u.GetForegroundWindow.restype = W.HWND
u.GetWindowLongPtrW.argtypes = [W.HWND, C.c_int]
u.GetWindowLongPtrW.restype = C.c_ssize_t
for name in ("IsWindowVisible", "IsWindow", "IsIconic", "SetForegroundWindow"):
    getattr(u, name).argtypes = [W.HWND]
u.ShowWindow.argtypes = [W.HWND, C.c_int]
u.ShowWindowAsync.argtypes = [W.HWND, C.c_int]
u.SwitchToThisWindow.argtypes = [W.HWND, W.BOOL]
u.SwitchToThisWindow.restype = None
u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
u.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, C.c_size_t]
u.keybd_event.restype = None
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE
k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
k.CloseHandle.argtypes = [W.HANDLE]
k.GetApplicationUserModelId.argtypes = [W.HANDLE, C.POINTER(C.c_uint32), W.LPWSTR]
k.GetApplicationUserModelId.restype = C.c_long
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
gdi.GetObjectW.argtypes = [W.HANDLE, C.c_int, C.c_void_p]
gdi.DeleteObject.argtypes = [W.HGDIOBJ]
ole.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
ole.CoInitializeEx.restype = C.HRESULT
ole.CoUninitialize.restype = None
ole.CoCreateInstance.argtypes = [C.c_void_p, C.c_void_p, W.DWORD, C.c_void_p, C.POINTER(C.c_void_p)]
ole.CoCreateInstance.restype = C.HRESULT
ole.CoTaskMemFree.argtypes = [C.c_void_p]
ole.CoTaskMemFree.restype = None
_shell.SHGetPropertyStoreForWindow.argtypes = [W.HWND, C.c_void_p, C.POINTER(C.c_void_p)]
_shell.SHGetPropertyStoreForWindow.restype = C.HRESULT
_shell.SHGetPropertyStoreFromParsingName.argtypes = [W.LPCWSTR, C.c_void_p, C.c_int, C.c_void_p,
                                                     C.POINTER(C.c_void_p)]
_shell.SHGetPropertyStoreFromParsingName.restype = C.HRESULT
_shell.SHCreateItemFromParsingName.argtypes = [W.LPCWSTR, C.c_void_p, C.c_void_p,
                                               C.POINTER(C.c_void_p)]
_shell.SHCreateItemFromParsingName.restype = C.HRESULT
_ver.GetFileVersionInfoSizeW.argtypes = [W.LPCWSTR, C.POINTER(W.DWORD)]
_ver.GetFileVersionInfoW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p]
_ver.VerQueryValueW.argtypes = [C.c_void_p, W.LPCWSTR, C.POINTER(C.c_void_p), C.POINTER(W.UINT)]
_ip.GetBestInterface.argtypes = [W.DWORD, C.POINTER(W.DWORD)]

SW_HIDE, SW_SHOWNA, SW_MINIMIZE, SW_RESTORE = 0, 8, 6, 9


class APPBARDATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uCallbackMessage", W.UINT),
                ("uEdge", W.UINT), ("rc", W.RECT), ("lParam", W.LPARAM)]


_shell.SHAppBarMessage.argtypes = [W.DWORD, C.POINTER(APPBARDATA)]
_shell.SHAppBarMessage.restype = C.c_size_t
ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS, ABM_GETSTATE = 0, 1, 2, 3, 4
ABE_BOTTOM, ABS_AUTOHIDE = 3, 1


def taskbar() -> int:
    """Explorer's taskbar on the main display, or 0 while Explorer is restarting."""
    return u.FindWindowW("Shell_TrayWnd", None) or 0


_edges: dict = {}


def taskbar_edge(monitor, bar: int) -> int:
    """Where Explorer's own reservation starts on the main display's bottom edge.

    Its strip stays reserved while the bar is hidden, so a hidden taskbar still
    counts. An auto-hiding one reserves nothing, and Windows 11 only puts the
    bar at the bottom, but a bar somewhere else is not ours to work around.

    Asked every second, so the answer is kept until the bar moves: asking about
    auto-hide is a message to Explorer, and a hung Explorer would hang the dock.
    """
    if not bar:
        return monitor[3]
    r = W.RECT()
    u.GetWindowRect(bar, C.byref(r))
    key = (tuple(monitor), bar, r.left, r.top, r.right, r.bottom)
    if key not in _edges:
        _edges.clear()
        data = APPBARDATA(cbSize=C.sizeof(APPBARDATA))
        autohide = _shell.SHAppBarMessage(ABM_GETSTATE, C.byref(data)) & ABS_AUTOHIDE
        at_bottom = r.bottom >= monitor[3] and monitor[1] < r.top < monitor[3] and \
            r.right - r.left > (monitor[2] - monitor[0]) // 2
        _edges[key] = r.top if at_bottom and not autohide else monitor[3]
    return _edges[key]


class WorkArea:
    """Make maximised windows stop at the dock's top edge.

    Explorer already keeps its own strip at the bottom, hidden or not, so this
    reserves only what is missing above it. It registers a separate hidden
    window as the app bar, so Explorer's placement never moves the visible dock,
    and the shell drops the reservation when that window dies.
    https://learn.microsoft.com/en-us/windows/win32/shell/abm-querypos
    """
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.signature = None

    def reserve(self, monitor, top, bar):
        edge = taskbar_edge(monitor, bar)
        extra = max(0, edge - top)
        signature = (tuple(monitor), top, bar, edge)
        if signature == self.signature:
            return
        self.release()
        if not extra:
            self.signature = signature
            return
        data = APPBARDATA(cbSize=C.sizeof(APPBARDATA), hWnd=self.hwnd, uEdge=ABE_BOTTOM,
                          uCallbackMessage=0x8000 + 73)
        if not _shell.SHAppBarMessage(ABM_NEW, C.byref(data)):
            return
        data.rc = W.RECT(monitor[0], edge - extra, monitor[2], edge)
        _shell.SHAppBarMessage(ABM_QUERYPOS, C.byref(data))
        data.rc.top = data.rc.bottom - extra
        _shell.SHAppBarMessage(ABM_SETPOS, C.byref(data))
        self.signature = signature

    def release(self):
        if self.signature is not None:
            data = APPBARDATA(cbSize=C.sizeof(APPBARDATA), hWnd=self.hwnd)
            _shell.SHAppBarMessage(ABM_REMOVE, C.byref(data))
            self.signature = None


# --------------------------------------------------------------------------- #
#  COM, by vtable slot: ctypes rather than comtypes keeps this dependency-free.
# --------------------------------------------------------------------------- #
class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD),
                ("Data4", C.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


class PROPERTYKEY(C.Structure):
    _fields_ = [("fmtid", GUID), ("pid", W.DWORD)]


class PROPVARIANT(C.Structure):
    _fields_ = [("vt", W.WORD), ("r1", W.WORD), ("r2", W.WORD), ("r3", W.WORD),
                ("value", C.c_void_p), ("extra", C.c_void_p)]


class SIZE(C.Structure):
    _fields_ = [("cx", C.c_long), ("cy", C.c_long)]


ole.PropVariantClear.argtypes = [C.POINTER(PROPVARIANT)]
IID_IPropertyStore = _guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
IID_IShellItem = _guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
IID_IShellItemImageFactory = _guid("bcc18b79-ba16-442f-80c4-8a59c30c463b")
PKEY_AppUserModel_ID = PROPERTYKEY(_guid("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)
CLSID_MMDeviceEnumerator = _guid("bcde0395-e52f-467c-8e3d-c4579291692e")
IID_IMMDeviceEnumerator = _guid("a95664d2-9614-4f35-a746-de8db63617e6")
IID_IAudioEndpointVolume = _guid("5cdf2c82-841e-4546-9722-0cf74078229a")
VT_LPWSTR = 31


def _method(ptr, slot, restype, *argtypes):
    vtbl = C.cast(ptr, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(restype, C.c_void_p, *argtypes)(vtbl[slot])


def _release(ptr) -> None:
    if ptr:
        _method(ptr, 2, C.c_ulong)(ptr)


def com_thread() -> bool:
    """Join a single-threaded apartment, as the shell's objects expect. Pair
    with ``com_done`` when the thread ends."""
    try:
        ole.CoInitializeEx(None, 0x2)       # COINIT_APARTMENTTHREADED
        return True
    except OSError:
        return False


def com_done() -> None:
    ole.CoUninitialize()


def _aumid_from(store) -> str:
    pv = PROPVARIANT()
    try:
        hr = _method(store, 5, C.c_long, C.POINTER(PROPERTYKEY), C.POINTER(PROPVARIANT))(
            store, C.byref(PKEY_AppUserModel_ID), C.byref(pv))
        if hr < 0:
            return ""
        return C.wstring_at(pv.value) if pv.vt == VT_LPWSTR and pv.value else ""
    finally:
        ole.PropVariantClear(C.byref(pv))
        _release(store)


def window_aumid(hwnd: int) -> str:
    """The app ID a window declares for itself (how Chrome's profiles, Store
    apps' frames and Electron apps get their own taskbar buttons), or ""."""
    store = C.c_void_p()
    try:
        _shell.SHGetPropertyStoreForWindow(hwnd, C.byref(IID_IPropertyStore), C.byref(store))
    except OSError:
        return ""
    return _aumid_from(store)


def file_aumid(path: str) -> str:
    """The app ID a shortcut launches under, or ""."""
    store = C.c_void_p()
    try:
        _shell.SHGetPropertyStoreFromParsingName(path, None, 0, C.byref(IID_IPropertyStore),
                                                 C.byref(store))
    except OSError:
        return ""
    return _aumid_from(store)


def process_aumid(pid: int) -> str:
    """A packaged (Store or MSIX) app's ID, from its process, or ""."""
    handle = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = C.c_uint32(0)
        if k.GetApplicationUserModelId(handle, C.byref(size), None) != 122 or not size.value:
            return ""                                # 15703: not a packaged process
        buf = C.create_unicode_buffer(size.value)
        return buf.value if k.GetApplicationUserModelId(handle, C.byref(size), buf) == 0 else ""
    finally:
        k.CloseHandle(handle)


def process_path(pid: int) -> str:
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        buf = C.create_unicode_buffer(32768)
        size = W.DWORD(len(buf))
        return buf.value if k.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)) else ""
    finally:
        k.CloseHandle(handle)


# --------------------------------------------------------------------------- #
#  Windows the taskbar would show
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Task:
    hwnd: int
    title: str
    path: str               # the app's program: a Store app's own, not its frame host's
    aumid: str = ""         # the taskbar's app ID, when the window or package has one
    pid: int = 0
    minimised: bool = False

    @property
    def app(self) -> str:
        """What the taskbar groups by: an app's windows share one button."""
        return app_key(self.aumid, self.path) or f"win:{self.hwnd}"


def app_key(aumid: str = "", path: str = "") -> str:
    if aumid:
        return "id:" + aumid.lower()
    return "exe:" + os.path.normcase(path) if path else ""


# Shell surfaces that look like app windows: Start, search, task view, the
# flyouts, and a Store app's inner window (its frame is the one that counts).
_SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
    "TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow",
    "ForegroundStaging", "MultitaskingViewFrame", "TaskListThumbnailWnd",
    "Shell_InputSwitchTopLevelWindow",
}
_identities: dict = {}       # hwnd -> (pid, aumid, path); windows keep theirs


def tasks(own_aumid: str = "") -> list[Task]:
    """The windows the taskbar would show, front first.

    The taskbar's rules: visible; not a tool window; unowned, or owned with
    WS_EX_APPWINDOW; not WS_EX_NOACTIVATE unless it asks to appear; and not
    cloaked (on another virtual desktop, or a suspended Store app). The PC
    Widgets app window counts - its cards and the dock are tool windows.
    """
    found = []
    seen = set()
    me = os.getpid()

    @ENUM
    def visit(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        ex = u.GetWindowLongPtrW(hwnd, -20)
        app_window = ex & 0x40000
        if ex & 0x80 or (ex & 0x08000000 and not app_window) or \
                (u.GetWindow(hwnd, 4) and not app_window):
            return True
        cloaked = W.DWORD()
        dwm.DwmGetWindowAttribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked))
        if cloaked.value:
            return True
        cls, title = C.create_unicode_buffer(256), C.create_unicode_buffer(1024)
        u.GetClassNameW(hwnd, cls, len(cls))
        u.GetWindowTextW(hwnd, title, len(title))
        if not title.value or cls.value in _SHELL_CLASSES:
            return True
        pid = W.DWORD()
        u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        hwnd = int(hwnd)
        seen.add(hwnd)
        known = _identities.get(hwnd)
        if known is None or known[0] != pid.value:
            known = (pid.value,) + _identify(hwnd, pid.value, cls.value, me, own_aumid)
            _identities[hwnd] = known
        found.append(Task(hwnd, title.value, known[2], known[1], pid.value,
                          bool(u.IsIconic(hwnd))))
        return True

    if not u.EnumWindows(visit, 0):
        raise C.WinError(C.get_last_error())
    for gone in set(_identities) - seen:
        del _identities[gone]
    return found


def _identify(hwnd: int, pid: int, cls: str, me: int, own_aumid: str):
    """(app ID, program) for a window, the way the taskbar decides."""
    if pid == me:
        return own_aumid, process_path(pid)
    path, aumid = process_path(pid), window_aumid(hwnd)
    if cls == "ApplicationFrameWindow":
        # A Store app runs in its own process inside a frame owned by
        # ApplicationFrameHost; the app is the inner window's process.
        inner = u.FindWindowExW(hwnd, None, "Windows.UI.Core.CoreWindow", None)
        if inner:
            ipid = W.DWORD()
            u.GetWindowThreadProcessId(inner, C.byref(ipid))
            path = process_path(ipid.value) or path
            aumid = aumid or process_aumid(ipid.value)
        if not aumid and not inner:
            path = ""                    # unknown: never group every Store app as one
    return aumid or process_aumid(pid), path


# --------------------------------------------------------------------------- #
#  Pictures and names
# --------------------------------------------------------------------------- #
class BITMAP(C.Structure):
    _fields_ = [("bmType", C.c_long), ("bmWidth", C.c_long), ("bmHeight", C.c_long),
                ("bmWidthBytes", C.c_long), ("bmPlanes", W.WORD), ("bmBitsPixel", W.WORD),
                ("bmBits", C.c_void_p)]


class BITMAPINFOHEADER(C.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", C.c_long), ("biHeight", C.c_long),
                ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
                ("biSizeImage", W.DWORD), ("biXPelsPerMeter", C.c_long),
                ("biYPelsPerMeter", C.c_long), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]


class DIBSECTION(C.Structure):
    _fields_ = [("dsBm", BITMAP), ("dsBmih", BITMAPINFOHEADER), ("dsBitfields", W.DWORD * 3),
                ("dshSection", W.HANDLE), ("dsOffset", W.DWORD)]


def _dib_image(hbm):
    """A 32-bit DIB section as a QImage.

    Not QImage.fromHBITMAP: it drops the alpha channel (an icon on a black
    square) and turned the shell's bottom-up bitmaps upside down - the folder
    tab and Epic's wordmark came out inverted. The shell hands back straight,
    not premultiplied, alpha.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    ds = DIBSECTION()
    if not gdi.GetObjectW(hbm, C.sizeof(DIBSECTION), C.byref(ds)) or not ds.dsBm.bmBits \
            or ds.dsBm.bmBitsPixel != 32:
        return None
    w, h, stride = ds.dsBm.bmWidth, abs(ds.dsBm.bmHeight), ds.dsBm.bmWidthBytes
    img = QImage(C.string_at(ds.dsBm.bmBits, stride * h), w, h, stride,
                 QImage.Format_ARGB32).copy()
    if ds.dsBmih.biHeight > 0:                      # bottom-up rows
        img = img.flipped(Qt.Vertical)
    return img.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def shell_image(name: str, px: int):
    """The shell's own picture of an app or file at ``px`` pixels, as a QImage.

    ``name`` is a path (an .exe, a shortcut) or ``shell:AppsFolder\\<app ID>``
    for anything in Start, Store apps included - the same artwork Start and the
    taskbar use, rendered at the size asked for rather than scaled from 32 px.
    """
    factory = C.c_void_p()
    try:
        _shell.SHCreateItemFromParsingName(name, None, C.byref(IID_IShellItemImageFactory),
                                           C.byref(factory))
    except OSError:
        return None
    try:
        hbm = W.HBITMAP()
        get = _method(factory, 3, C.c_long, SIZE, C.c_int, C.POINTER(W.HBITMAP))
        if get(factory, SIZE(px, px), 0x4, C.byref(hbm)) < 0 or not hbm:   # SIIGBF_ICONONLY
            return None
        try:
            return _dib_image(hbm)
        finally:
            gdi.DeleteObject(hbm)
    finally:
        _release(factory)


def window_icon(hwnd: int):
    """The window's own icon as a QImage, or None.

    Never waits on an unresponsive window for long. Only call this from a
    worker thread: a window of this very process would answer on the GUI
    thread, and the GUI thread may be the one waiting.
    """
    from PySide6.QtGui import QImage
    result = C.c_size_t()
    handle = None
    for size in (1, 2, 0):  # ICON_BIG, ICON_SMALL2, ICON_SMALL
        if u.SendMessageTimeoutW(hwnd, 0x7F, size, 0, 0x2, 100, C.byref(result)) and result.value:
            handle = u.CopyIcon(result.value)       # SMTO_ABORTIFHUNG
            break
    if not handle:
        for slot in (-14, -34):  # GCLP_HICON, GCLP_HICONSM
            icon = u.GetClassLongPtrW(hwnd, slot)
            if icon:
                handle = u.CopyIcon(icon)
                break
    if not handle:
        return None
    try:
        img = QImage.fromHICON(handle)
        return None if img.isNull() else img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    finally:
        u.DestroyIcon(handle)


_shell.SHGetNameFromIDList.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_void_p)]
_shell.SHGetNameFromIDList.restype = C.HRESULT
APPS_FOLDER = "::{4234D49B-0245-4DF3-B780-3893943456E1}\\"
_pinned = (None, [])


def taskbar_pins() -> list[str]:
    """The taskbar's own pins, in its order, as dock pin targets: shortcut
    paths, or ``shell:AppsFolder\\<app ID>`` for Store apps.

    Read from Explorer's list (Taskband\\Favorites) rather than the User
    Pinned folder: the folder has shortcuts only, in no order, and a Store app
    pinned to the taskbar has no shortcut at all. The value is a run of
    records - a tag byte, a 32-bit length, then an ordinary shell item ID list
    - ended by 0xFF; the shell turns each list back into a name. Read again
    only when the value changes.
    """
    global _pinned
    import struct
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband") as key:
            data, _ = winreg.QueryValueEx(key, "Favorites")
    except OSError:
        return []
    if not isinstance(data, bytes):
        return []
    if data == _pinned[0]:
        return list(_pinned[1])
    targets, pos = [], 0
    while pos + 5 <= len(data) and data[pos] != 0xFF:
        size = struct.unpack_from("<I", data, pos + 1)[0]
        if size <= 0 or pos + 5 + size > len(data):
            break                                   # not the layout this expects: stop
        ids = C.create_string_buffer(data[pos + 5:pos + 5 + size] + b"\0\0")
        pos += 5 + size
        text = C.c_void_p()
        try:
            _shell.SHGetNameFromIDList(ids, 0x80028000, C.byref(text))  # desktop parsing name
        except OSError:
            continue
        try:
            name = C.wstring_at(text)
        finally:
            ole.CoTaskMemFree(text)
        if name.upper().startswith(APPS_FOLDER.upper()):
            name = name[len(APPS_FOLDER):]
        if not (name[1:3] == ":\\" or name.startswith("\\\\")):
            name = "shell:AppsFolder\\" + name     # a Store app's ID, not a file
        targets.append(name)
    _pinned = (data, targets)
    return list(targets)


def overflow_window() -> int:
    """The notification area's hidden-icons flyout while it is open, else 0."""
    for cls in ("TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow"):
        hwnd = u.FindWindowW(cls, None)
        if hwnd and u.IsWindowVisible(hwnd):
            cloaked = W.DWORD()
            dwm.DwmGetWindowAttribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked))
            if not cloaked.value:
                return hwnd
    return 0


u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]


def place_above(hwnd: int, cx: int, top: int, screen) -> None:
    """Move another app's popup so it sits centred above a point, on screen.
    Explorer leaves its hidden-icons flyout wherever it is put."""
    r = W.RECT()
    u.GetWindowRect(hwnd, C.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    x = max(screen[0] + 8, min(cx - w // 2, screen[2] - w - 8))
    u.SetWindowPos(hwnd, None, x, top - h, 0, 0, 0x0001 | 0x0004 | 0x0010)  # no size/z/activate


def close_overflow() -> None:
    """Dismiss the hidden-icons flyout the way a person would, with Escape -
    only while it has the keyboard, so the key never lands anywhere else."""
    flyout = overflow_window()
    if flyout and foreground() == flyout:
        key(0x1B)


_oleaut = C.WinDLL("oleaut32")
_oleaut.SysFreeString.argtypes = [C.c_void_p]
CLSID_CUIAutomation = _guid("ff48dba4-60ef-4201-aa87-54103eef594e")
IID_IUIAutomation = _guid("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")


def focused_control():
    """(name, automation ID, class) of whatever has keyboard focus, read
    through UI Automation, or None. COM thread only."""
    uia, el = C.c_void_p(), C.c_void_p()
    try:
        ole.CoCreateInstance(C.byref(CLSID_CUIAutomation), None, 1, C.byref(IID_IUIAutomation),
                             C.byref(uia))
        if _method(uia, 8, C.c_long, C.POINTER(C.c_void_p))(uia, C.byref(el)) < 0 or not el:
            return None                             # IUIAutomation::GetFocusedElement

        def text(slot):
            out = C.c_void_p()
            if _method(el, slot, C.c_long, C.POINTER(C.c_void_p))(el, C.byref(out)) < 0 or \
                    not out:
                return ""
            try:
                return C.wstring_at(out)
            finally:
                _oleaut.SysFreeString(out)
        return text(23), text(29), text(30)         # CurrentName, AutomationId, ClassName
    except OSError:
        return None
    finally:
        _release(el)
        _release(uia)


def display_name(target: str) -> str:
    """What Start calls an app: its Start entry, a shortcut's own name, or an
    exe's description ("Google Chrome", not "chrome")."""
    if target.lower().startswith("shell:appsfolder\\"):
        item = C.c_void_p()
        try:
            _shell.SHCreateItemFromParsingName(target, None, C.byref(IID_IShellItem),
                                               C.byref(item))
        except OSError:
            return ""
        try:
            text = C.c_void_p()
            if _method(item, 5, C.c_long, C.c_int, C.POINTER(C.c_void_p))(
                    item, 0, C.byref(text)) < 0 or not text:     # SIGDN_NORMALDISPLAY
                return ""
            try:
                return C.wstring_at(text)
            finally:
                ole.CoTaskMemFree(text)
        finally:
            _release(item)
    base, ext = os.path.splitext(os.path.basename(target))
    if ext.lower() == ".exe":
        return _file_description(target) or base
    return base


def _file_description(path: str) -> str:
    try:
        size = _ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        data = C.create_string_buffer(size)
        if not _ver.GetFileVersionInfoW(path, 0, size, data):
            return ""
        ptr, n = C.c_void_p(), W.UINT()
        if not _ver.VerQueryValueW(data, "\\VarFileInfo\\Translation", C.byref(ptr), C.byref(n)) \
                or n.value < 4:
            return ""
        lang, page = C.cast(ptr, C.POINTER(W.WORD))[0], C.cast(ptr, C.POINTER(W.WORD))[1]
        key = f"\\StringFileInfo\\{lang:04x}{page:04x}\\FileDescription"
        if not _ver.VerQueryValueW(data, key, C.byref(ptr), C.byref(n)) or not n.value:
            return ""
        return C.wstring_at(ptr, n.value).rstrip("\0").strip()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
#  Network and speakers, for the quick-settings button
# --------------------------------------------------------------------------- #
class MIB_IFROW(C.Structure):
    _fields_ = [("wszName", W.WCHAR * 256), ("dwIndex", W.DWORD), ("dwType", W.DWORD),
                ("dwMtu", W.DWORD), ("dwSpeed", W.DWORD), ("dwPhysAddrLen", W.DWORD),
                ("bPhysAddr", C.c_ubyte * 8), ("dwAdminStatus", W.DWORD),
                ("dwOperStatus", W.DWORD), ("dwLastChange", W.DWORD)] + \
               [(f"dw{n}", W.DWORD) for n in (
                   "InOctets", "InUcastPkts", "InNUcastPkts", "InDiscards", "InErrors",
                   "InUnknownProtos", "OutOctets", "OutUcastPkts", "OutNUcastPkts",
                   "OutDiscards", "OutErrors", "OutQLen", "DescrLen")] + \
               [("bDescr", C.c_ubyte * 256)]


_ip.GetIfEntry.argtypes = [C.POINTER(MIB_IFROW)]


def network() -> str:
    """What the route to the internet runs over: "ethernet", "wifi",
    "cellular" or "offline". A routing-table lookup; nothing is sent."""
    index = W.DWORD()
    if _ip.GetBestInterface(0x01010101, C.byref(index)) != 0:      # 1.1.1.1
        return "offline"
    row = MIB_IFROW(dwIndex=index.value)
    if _ip.GetIfEntry(C.byref(row)) != 0:
        return "ethernet"
    return {71: "wifi", 243: "cellular", 244: "cellular"}.get(row.dwType, "ethernet")


class Speakers:
    """The default output's volume and mute, through IAudioEndpointVolume.

    Asks for the default device each time, so switching to headphones is
    picked up. Create and use it on one COM thread.
    """
    def __init__(self):
        self._enum = C.c_void_p()
        try:
            ole.CoCreateInstance(C.byref(CLSID_MMDeviceEnumerator), None, 0x17,
                                 C.byref(IID_IMMDeviceEnumerator), C.byref(self._enum))
        except OSError:
            self._enum = None

    def read(self):
        """(level 0..1, muted), or None with no speakers."""
        if not self._enum:
            return None
        device, volume = C.c_void_p(), C.c_void_p()
        try:
            if _method(self._enum, 4, C.c_long, C.c_int, C.c_int, C.POINTER(C.c_void_p))(
                    self._enum, 0, 0, C.byref(device)) < 0:      # eRender, eConsole
                return None
            if _method(device, 3, C.c_long, C.c_void_p, W.DWORD, C.c_void_p,
                       C.POINTER(C.c_void_p))(device, C.byref(IID_IAudioEndpointVolume), 0x17,
                                              None, C.byref(volume)) < 0:
                return None
            level, muted = C.c_float(), W.BOOL()
            if _method(volume, 9, C.c_long, C.POINTER(C.c_float))(volume, C.byref(level)) < 0:
                return None
            if _method(volume, 15, C.c_long, C.POINTER(W.BOOL))(volume, C.byref(muted)) < 0:
                return None
            return max(0.0, min(1.0, level.value)), bool(muted.value)
        finally:
            _release(volume)
            _release(device)

    def close(self):
        _release(self._enum)
        self._enum = None


# --------------------------------------------------------------------------- #
#  Acting on windows
# --------------------------------------------------------------------------- #
def activate(hwnd: int) -> None:
    """Bring a window forward, restoring it if minimised.

    Windows lets this process take the foreground because the click that
    asked for it was its input; SwitchToThisWindow covers the case where the
    foreground lock still says no.
    """
    if not u.IsWindow(hwnd):
        return
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    if not u.SetForegroundWindow(hwnd):
        u.SwitchToThisWindow(hwnd, True)


def minimise(hwnd: int) -> None:
    if u.IsWindow(hwnd):
        u.ShowWindow(hwnd, SW_MINIMIZE)


def close(hwnd: int) -> None:
    """Ask a window to close, as the taskbar's Close window does."""
    if u.IsWindow(hwnd):
        u.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE


class DWM_THUMBNAIL_PROPERTIES(C.Structure):
    _fields_ = [("dwFlags", W.DWORD), ("rcDestination", W.RECT), ("rcSource", W.RECT),
                ("opacity", C.c_ubyte), ("fVisible", W.BOOL), ("fSourceClientAreaOnly", W.BOOL)]


dwm.DwmRegisterThumbnail.argtypes = [W.HWND, W.HWND, C.POINTER(W.HANDLE)]
dwm.DwmRegisterThumbnail.restype = C.HRESULT
dwm.DwmUnregisterThumbnail.argtypes = [W.HANDLE]
dwm.DwmUnregisterThumbnail.restype = C.HRESULT
dwm.DwmQueryThumbnailSourceSize.argtypes = [W.HANDLE, C.POINTER(SIZE)]
dwm.DwmQueryThumbnailSourceSize.restype = C.HRESULT
dwm.DwmUpdateThumbnailProperties.argtypes = [W.HANDLE, C.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwm.DwmUpdateThumbnailProperties.restype = C.HRESULT
dwm.DwmSetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]


class Thumbnail:
    """A live picture of another window, drawn by the compositor into one of
    ours - what the taskbar's previews are. The destination window must not
    be layered: Qt makes a see-through frameless window layered, and the
    compositor never draws a thumbnail into one, though it registers."""

    def __init__(self, dest: int, source: int):
        self.handle = W.HANDLE()
        hr = dwm.DwmRegisterThumbnail(dest, source, C.byref(self.handle))
        if hr:
            self.handle = None
            raise OSError(f"DwmRegisterThumbnail failed ({hr & 0xFFFFFFFF:#x})")

    def size(self) -> tuple[int, int]:
        s = SIZE()
        if not self.handle or dwm.DwmQueryThumbnailSourceSize(self.handle, C.byref(s)):
            return 0, 0
        return s.cx, s.cy

    def place(self, left: int, top: int, right: int, bottom: int, opacity: int = 255) -> None:
        if self.handle:
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = 0x1 | 0x4 | 0x8 | 0x10    # destination, opacity, visible, whole window
            props.rcDestination = W.RECT(left, top, right, bottom)
            props.opacity = max(0, min(255, opacity))
            props.fVisible, props.fSourceClientAreaOnly = opacity > 0, False
            dwm.DwmUpdateThumbnailProperties(self.handle, C.byref(props))

    def close(self) -> None:
        if self.handle:
            dwm.DwmUnregisterThumbnail(self.handle)
            self.handle = None


def round_corners(hwnd: int) -> None:
    """Windows 11's own rounded corners, and the border and shadow that go
    with them, on a popup of ours. Earlier Windows ignores it."""
    value = C.c_int(2)                              # DWMWCP_ROUND
    dwm.DwmSetWindowAttribute(hwnd, 33, C.byref(value), C.sizeof(value))


def window_border(hwnd: int, shown: bool) -> None:
    """Windows 11's hairline border round a popup of ours, or none - a neon
    tube takes its place."""
    value = C.c_uint(0xFFFFFFFF if shown else 0xFFFFFFFE)   # DWMWA_COLOR_DEFAULT / _NONE
    dwm.DwmSetWindowAttribute(hwnd, 34, C.byref(value), C.sizeof(value))


def window_title(hwnd: int) -> str:
    buf = C.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def foreground() -> int:
    return u.GetForegroundWindow() or 0


u.IsZoomed.argtypes = [W.HWND]


_START_HOSTS = {"startmenuexperiencehost.exe", "searchhost.exe"}
_start_pids: dict = {}       # pid -> whether it is Start's (or search's) host


def is_start(hwnd: int) -> bool:
    """Start, or the search it turns into as you type: each lives in its own
    host process, whatever its window's class or title."""
    if not hwnd:
        return False
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, C.byref(pid))
    known = _start_pids.get(pid.value)
    if known is None:
        if len(_start_pids) > 64:
            _start_pids.clear()
        known = _start_pids[pid.value] = \
            os.path.basename(process_path(pid.value)).lower() in _START_HOSTS
    return known


def maximised_on(hwnds, monitor) -> bool:
    """Whether any of these windows is maximised on that monitor. Plain
    window-state reads: nothing here waits on the windows' apps."""
    for hwnd in hwnds:
        if u.IsWindow(hwnd) and u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd) \
                and u.IsZoomed(hwnd):
            r = W.RECT()
            u.GetWindowRect(hwnd, C.byref(r))
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            if monitor[0] <= cx < monitor[2] and monitor[1] <= cy < monitor[3]:
                return True
    return False


class CURSORINFO(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hCursor", W.HANDLE),
                ("ptScreenPos", W.POINT)]


u.GetCursorInfo.argtypes = [C.POINTER(CURSORINFO)]


def cursor():
    """(x, y, shown) for the mouse pointer. A game that has captured the mouse
    hides the pointer, which is how the dock knows not to pop up over it."""
    info = CURSORINFO(cbSize=C.sizeof(CURSORINFO))
    if not u.GetCursorInfo(C.byref(info)):
        return 0, 0, False
    return info.ptScreenPos.x, info.ptScreenPos.y, bool(info.flags & 0x1)   # CURSOR_SHOWING


_shell.GetCurrentProcessExplicitAppUserModelID.argtypes = [C.POINTER(C.c_void_p)]
_shell.GetCurrentProcessExplicitAppUserModelID.restype = C.HRESULT


def own_aumid() -> str:
    """The app ID this process gave itself (main.py sets one before any window),
    so the board's own window gets its own button rather than Python's."""
    text = C.c_void_p()
    try:
        _shell.GetCurrentProcessExplicitAppUserModelID(C.byref(text))
    except OSError:
        return ""
    try:
        return C.wstring_at(text) if text else ""
    finally:
        ole.CoTaskMemFree(text)


WINEVENTPROC = C.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, C.c_long, C.c_long,
                             W.DWORD, W.DWORD)
u.SetWinEventHook.argtypes = [W.DWORD, W.DWORD, W.HMODULE, WINEVENTPROC, W.DWORD, W.DWORD,
                              W.DWORD]
u.SetWinEventHook.restype = W.HANDLE
u.UnhookWinEvent.argtypes = [W.HANDLE]
EVENT_FOREGROUND = 0x0003
EVENT_SHOW = 0x8002


class WindowEvents:
    """Foreground changes, and windows appearing or going, as they happen.

    Out-of-context WinEvent hooks: Windows queues each event to the thread that
    set the hook and hands it over from that thread's message loop, so the
    callback runs on the GUI thread between messages, never inside one.
    Events from this process are skipped.
    """
    RANGES = ((0x0003, 0x0003),     # foreground
              (0x0016, 0x0017),     # minimise start / end
              (0x8001, 0x8003),     # destroy, show, hide
              (0x8017, 0x8018))     # cloaked, uncloaked (virtual desktops)

    def __init__(self, callback):
        self._callback = callback
        self._proc = WINEVENTPROC(self._on)       # kept alive as long as the hooks
        self._hooks = [h for h in (u.SetWinEventHook(lo, hi, None, self._proc, 0, 0, 0x2)
                                   for lo, hi in self.RANGES) if h]
        if not self._hooks:
            raise C.WinError(C.get_last_error())

    def _on(self, hook, event, hwnd, obj, child, thread, when):
        if hwnd and obj == 0 and child == 0:      # OBJID_WINDOW, CHILDID_SELF
            try:
                self._callback(event, int(hwnd))
            except Exception:
                pass

    def close(self) -> None:
        for hook in self._hooks:
            u.UnhookWinEvent(hook)
        self._hooks = []


def shortcut(key: int | None = None) -> None:
    """Open the real Windows Start / quick settings / notification UI."""
    u.keybd_event(0x5B, 0, 0, 0)
    if key is not None:
        u.keybd_event(key, 0, 0, 0)
        u.keybd_event(key, 0, 2, 0)
    u.keybd_event(0x5B, 0, 2, 0)


def key(vk: int) -> None:
    """Tap a key such as volume up (0xAF) or down (0xAE)."""
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)


class TaskbarLease:
    def __init__(self):
        self.guard = None
        self.hwnd = None

    def hide(self) -> bool:
        hwnd = taskbar()
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
        if u.IsWindowVisible(hwnd):
            u.ShowWindow(hwnd, SW_HIDE)
        return True

    def rehide(self) -> None:
        """Hide the leased bar again after Explorer showed it by itself. Async:
        this runs from a window-event callback, and a busy Explorer must not
        stall the dock's thread."""
        if self.guard is not None and self.guard.poll() is None and self.hwnd \
                and u.IsWindowVisible(self.hwnd):
            u.ShowWindowAsync(self.hwnd, SW_HIDE)

    def reveal(self) -> None:
        # SW_SHOWNA, never SW_SHOW: showing the bar must not activate it. An
        # activated taskbar takes the focus from whatever had it - a game, say.
        if self.hwnd and u.IsWindow(self.hwnd) and not u.IsWindowVisible(self.hwnd):
            u.ShowWindow(self.hwnd, SW_SHOWNA)

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
            u.ShowWindow(hwnd, SW_SHOWNA)


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--guard":
    _guard(int(sys.argv[2]))
