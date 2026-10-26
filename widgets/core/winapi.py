"""
winapi.py
=========
The Win32 bits that make these feel like OS widgets rather than floating apps.

Three jobs:

**Layering.** iPhone and Mac widgets live *on* the desktop, not on top of your
work. Two strategies are offered: pinning to the bottom of the z-order, and
reparenting onto the wallpaper window itself. The second is the real thing -
widgets genuinely become part of the desktop - but it depends on undocumented
shell behaviour, so the first is the reliable fallback.

**A global hotkey**, so the whole board can be summoned over whatever you're
doing and dismissed again.

**Live system readings** for the stats widgets: CPU, memory, disk and battery
straight from the kernel, so no third-party dependency is needed.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass
from typing import List, Optional, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #
GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

SPI_GETWORKAREA = 0x0030
MONITOR_DEFAULTTONEAREST = 2

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
BACKDROP = {"off": 1, "mica": 2, "acrylic": 3, "tabbed": 4}

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

# How a second launch finds the running board and asks it to open its window:
# the board's hidden hotkey window carries this title, and listens for this
# registered message.
CONTROL_TITLE = "PC Widgets control"
SHOW_MESSAGE = "PCWidgets.ShowWindow"

if hasattr(user32, "SetWindowLongPtrW"):
    _get_style, _set_style = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
    _get_style.restype = ctypes.c_longlong
    _set_style.restype = ctypes.c_longlong
    _set_style.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_longlong]
else:  # pragma: no cover
    _get_style, _set_style = user32.GetWindowLongW, user32.SetWindowLongW

user32.GetForegroundWindow.restype = wt.HWND
user32.FindWindowW.restype = wt.HWND
user32.FindWindowExW.restype = wt.HWND
user32.SetParent.restype = wt.HWND
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterWindowMessageW.restype = wt.UINT


# --------------------------------------------------------------------------- #
#  Screen geometry
# --------------------------------------------------------------------------- #
Rect = Tuple[int, int, int, int]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def work_area() -> Rect:
    r = wt.RECT()
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return (0, 0, 1920, 1080)
    return (r.left, r.top, r.right, r.bottom)


def work_area_at(x: int, y: int) -> Rect:
    try:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        hmon = user32.MonitorFromPoint(wt.POINT(int(x), int(y)),
                                       MONITOR_DEFAULTTONEAREST)
        if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            w = mi.rcWork
            return (w.left, w.top, w.right, w.bottom)
    except Exception:
        pass
    return work_area()


SPI_GETDESKWALLPAPER = 0x0073
COLOR_DESKTOP = 1


def wallpaper_path() -> Optional[str]:
    """Current desktop wallpaper file, or None (solid colour, or a slideshow
    mid-swap)."""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if not user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 1024, buf, 0):
            return None
        import os
        return buf.value if buf.value and os.path.exists(buf.value) else None
    except Exception:
        return None


def desktop_colour() -> Tuple[int, int, int]:
    """Fallback when there is no wallpaper image."""
    try:
        v = user32.GetSysColor(COLOR_DESKTOP)
        return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    except Exception:
        return (32, 36, 44)


def monitors() -> List[Rect]:
    """Every monitor's full bounds, for compositing the wallpaper."""
    out: List[Rect] = []
    try:
        proc = ctypes.WINFUNCTYPE(
            wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

        def _cb(hmon, _hdc, lprc, _lp):
            r = lprc.contents
            out.append((r.left, r.top, r.right, r.bottom))
            return True

        user32.EnumDisplayMonitors(None, None, proc(_cb), 0)
    except Exception:
        pass
    return out or [work_area()]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def foreground_is_fullscreen() -> bool:
    """Is the focused window covering a whole monitor?

    Used to stop the board painting behind a game. These cards are layered
    translucent windows, so every repaint makes DWM composite them again even
    when they are completely hidden - the cost does not go away just because
    nobody can see them.

    The shell's own windows cover the screen by definition and must not count,
    or the board would fall silent whenever the desktop had focus.

    Geometry alone is not enough, which took a false positive to discover. A
    *maximised* window is not fullscreen, but Windows gives it a rect eight
    pixels larger than the monitor on every side, and on a display with no
    taskbar the work area is the whole monitor - so an ordinary maximised editor
    looked exactly like a game and would have silenced the board permanently.
    ``IsZoomed`` tells the two apart, and a title bar settles the rest: a game
    running fullscreen has neither.

    The bias is deliberate. Failing to notice a game costs nothing more than the
    board carries today; wrongly deciding one is running stops the widgets
    updating while you work, which looks broken.
    """
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value in ("Progman", "WorkerW", "Shell_TrayWnd",
                          "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"):
            return False

        if user32.IsZoomed(hwnd):
            return False                    # maximised, not fullscreen

        if _get_style(wt.HWND(hwnd), GWL_STYLE) & WS_CAPTION == WS_CAPTION:
            return False                    # it still has a title bar

        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not hmon or not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return False

        m = info.rcMonitor
        return (r.left <= m.left and r.top <= m.top
                and r.right >= m.right and r.bottom >= m.bottom)
    except Exception:
        return False


def virtual_screen() -> Rect:
    SM_X, SM_Y, SM_CX, SM_CY = 76, 77, 78, 79
    x = user32.GetSystemMetrics(SM_X)
    y = user32.GetSystemMetrics(SM_Y)
    return (x, y,
            x + user32.GetSystemMetrics(SM_CX),
            y + user32.GetSystemMetrics(SM_CY))


def dpi_scale(hwnd: int) -> float:
    try:
        if hasattr(user32, "GetDpiForWindow"):
            dpi = user32.GetDpiForWindow(wt.HWND(hwnd))
            if dpi:
                return dpi / 96.0
    except Exception:
        pass
    return 1.0


# --------------------------------------------------------------------------- #
#  Layering
# --------------------------------------------------------------------------- #
def make_desktop_window(hwnd: int, no_activate: bool = True) -> None:
    """Keep a widget out of the taskbar and Alt-Tab.

    ``no_activate`` also stops it taking focus, which is right for every
    display-only card. A card hosting a text field must be activatable or it
    can never receive a keystroke, so it opts out.
    """
    try:
        style = _get_style(wt.HWND(hwnd), GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW
        style &= ~WS_EX_APPWINDOW
        if no_activate:
            style |= WS_EX_NOACTIVATE
        else:
            style &= ~WS_EX_NOACTIVATE
        _set_style(wt.HWND(hwnd), GWL_EXSTYLE, style)
    except Exception:
        pass


def send_to_bottom(hwnd: int) -> None:
    """Push behind every normal window - the reliable 'on the desktop' mode."""
    try:
        user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_BOTTOM), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def bring_to_front(hwnd: int, topmost: bool) -> None:
    try:
        user32.SetWindowPos(
            wt.HWND(hwnd), wt.HWND(HWND_TOPMOST if topmost else HWND_NOTOPMOST),
            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def find_wallpaper_host() -> Optional[int]:
    """The WorkerW that sits between the wallpaper and the desktop icons.

    Explorer only creates it after Progman is poked with the undocumented
    0x052C message. We then want the WorkerW that does *not* own the icon view
    (SHELLDLL_DefView) - that's the one drawn directly over the wallpaper.
    Returns None on any shell layout we don't recognise, and the caller falls
    back to :func:`send_to_bottom`.
    """
    try:
        progman = user32.FindWindowW("Progman", None)
        if not progman:
            return None
        # Ask the shell to spawn the WorkerW layer. Ignore the result; on some
        # builds it is already present.
        result = ctypes.c_ulonglong()
        user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0x0000, 1000,
                                   ctypes.byref(result))

        found: List[int] = []

        def _cb(hwnd, _lp):
            if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
                # Its sibling WorkerW is the wallpaper layer.
                nxt = user32.FindWindowExW(None, hwnd, "WorkerW", None)
                if nxt:
                    found.append(int(nxt))
            return True

        proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb)
        user32.EnumWindows(proc, 0)
        return found[0] if found else None
    except Exception:
        return None


def attach_to_wallpaper(hwnd: int) -> bool:
    """Reparent onto the wallpaper layer. True if the shell allowed it."""
    host = find_wallpaper_host()
    if not host:
        return False
    try:
        return bool(user32.SetParent(wt.HWND(hwnd), wt.HWND(host)))
    except Exception:
        return False


def detach_from_wallpaper(hwnd: int) -> None:
    try:
        user32.SetParent(wt.HWND(hwnd), None)
    except Exception:
        pass


def _colorref(hex_colour: str) -> int:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16) | (int(h[2:4], 16) << 8) | (int(h[4:6], 16) << 16)


def style_titlebar(hwnd: int, dark: bool, caption: Optional[str] = None,
                   text: Optional[str] = None) -> None:
    """A title bar that matches the window under it.

    Dark mode works from Windows 10 20H1; the caption and text colours are
    Windows 11 only, and on anything older the call simply fails and the bar
    stays the system's - which is why every result is ignored.
    """
    try:
        val = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
                                     ctypes.byref(val), ctypes.sizeof(val))
        for attr, colour in ((DWMWA_CAPTION_COLOR, caption),
                             (DWMWA_TEXT_COLOR, text)):
            if colour:
                ref = wt.DWORD(_colorref(colour))
                dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), attr,
                                             ctypes.byref(ref), ctypes.sizeof(ref))
    except Exception:
        pass


def register_message(name: str) -> int:
    """A message number unique to ``name``, the same in every process."""
    try:
        return int(user32.RegisterWindowMessageW(name))
    except Exception:
        return 0


def signal_window(title: str, message: int) -> bool:
    """Post ``message`` to another process's window, found by its title.

    Also hands that process the right to come to the foreground. Windows only
    lets the process the user just interacted with take focus - here that is
    the second launch, not the running board - so without passing it on, the
    board's window would only flash in the taskbar instead of appearing.
    """
    try:
        hwnd = user32.FindWindowW(None, title)
        if not hwnd or not message:
            return False
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            user32.AllowSetForegroundWindow(pid.value)
        return bool(user32.PostMessageW(hwnd, message, 0, 0))
    except Exception:
        return False


def is_minimised(hwnd: int) -> bool:
    try:
        return bool(user32.IsIconic(wt.HWND(hwnd)))
    except Exception:
        return False


def set_app_id(app_id: str) -> None:
    """Give the process its own taskbar identity.

    Without one the app window is grouped under pythonw.exe and shows Python's
    icon on the taskbar, whatever icon the window itself sets.
    """
    try:
        ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(app_id))
    except Exception:
        pass


def apply_backdrop(hwnd: int, mode: str, dark: bool) -> bool:
    """Windows 11 Mica/Acrylic behind the widget. Silently no-ops elsewhere."""
    try:
        val = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
                                     ctypes.byref(val), ctypes.sizeof(val))
        back = ctypes.c_int(BACKDROP.get(mode, 1))
        res = dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
                                           ctypes.byref(back), ctypes.sizeof(back))
        return res == 0 and mode != "off"
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Global hotkey
# --------------------------------------------------------------------------- #
def register_hotkey(hwnd: int, hotkey_id: int, mods: int, vk: int) -> bool:
    try:
        return bool(user32.RegisterHotKey(wt.HWND(hwnd), hotkey_id,
                                          mods | MOD_NOREPEAT, vk))
    except Exception:
        return False


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    try:
        user32.UnregisterHotKey(wt.HWND(hwnd), hotkey_id)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  System readings (so the stats widgets need no third-party package)
# --------------------------------------------------------------------------- #
class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", wt.BYTE), ("BatteryFlag", wt.BYTE),
                ("BatteryLifePercent", wt.BYTE), ("SystemStatusFlag", wt.BYTE),
                ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]


@dataclass
class Memory:
    percent: float
    used_gb: float
    total_gb: float


def memory() -> Memory:
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    total = m.ullTotalPhys / (1024 ** 3)
    used = (m.ullTotalPhys - m.ullAvailPhys) / (1024 ** 3)
    return Memory(float(m.dwMemoryLoad), used, total)


_last_cpu: Optional[Tuple[int, int]] = None


def cpu_percent() -> float:
    """Busy fraction since the previous call.

    GetSystemTimes gives cumulative counters, so the first call has nothing to
    compare against and reports 0 - callers should poll on a timer.
    """
    global _last_cpu
    idle, kern, usr = wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                                   ctypes.byref(usr)):
        return 0.0

    def _v(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    idle_t, total_t = _v(idle), _v(kern) + _v(usr)
    if _last_cpu is None:
        _last_cpu = (idle_t, total_t)
        return 0.0
    d_idle, d_total = idle_t - _last_cpu[0], total_t - _last_cpu[1]
    _last_cpu = (idle_t, total_t)
    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))


@dataclass
class Disk:
    letter: str
    percent: float
    used_gb: float
    total_gb: float


def disk(path: str = "C:\\") -> Optional[Disk]:
    try:
        free, total = ctypes.c_ulonglong(), ctypes.c_ulonglong()
        if not kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(path), None,
            ctypes.byref(total), ctypes.byref(free)
        ):
            return None
        t = total.value / (1024 ** 3)
        u = (total.value - free.value) / (1024 ** 3)
        return Disk(path[0], (u / t * 100.0) if t else 0.0, u, t)
    except Exception:
        return None


@dataclass
class Battery:
    present: bool
    percent: int
    charging: bool
    minutes_left: Optional[int]


def battery() -> Battery:
    s = SYSTEM_POWER_STATUS()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return Battery(False, 0, False, None)
    pct = int(s.BatteryLifePercent)
    # 255 means "unknown"; flag bit 128 means "no system battery".
    present = pct != 255 and not (int(s.BatteryFlag) & 128)
    secs = int(s.BatteryLifeTime)
    return Battery(
        present=present,
        percent=pct if present else 0,
        charging=int(s.ACLineStatus) == 1,
        minutes_left=(secs // 60) if (present and secs not in (0xFFFFFFFF, 0)) else None,
    )


def uptime_seconds() -> float:
    try:
        return kernel32.GetTickCount64() / 1000.0
    except Exception:
        return 0.0
