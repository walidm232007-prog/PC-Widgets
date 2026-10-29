"""
displays.py
===========
The monitors: which ones are connected, what each is called, and where a card
belongs on one.

A position on its own can't hold a layout together across more than one screen.
Windows measures every monitor in one shared space whose origin is the top-left
of the *main* display, so plugging a monitor in on the left, choosing another
one as main, or dragging the arrangement about in Display settings changes the
coordinates of everything on a screen without anything on it moving. And when
a monitor is unplugged its widgets have nowhere to be - while a layout saved
with them pushed onto another screen would forget where they came from.

So a card is anchored: to a monitor, by the monitor's own identity - its device
path, which names the panel's model and the port it hangs off - and to a spot
on that monitor's work area. Wherever the monitor sits in the arrangement
today, the card is on it. If it isn't connected, the card waits on the main
display, still belonging to it, and goes back when it returns.

Everything here is in physical pixels, the same as the cards (Qt's high-DPI
scaling is off; see main.py).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]

_user32 = ctypes.WinDLL("user32", use_last_error=True)


# --------------------------------------------------------------------------- #
#  What a display is
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Display:
    key: str                # device path; survives reboots, rearranging, resolution
    name: str               # what the monitor calls itself, e.g. "ES-G24F4"
    rect: Rect              # its bounds in the virtual screen
    work: Rect              # less the taskbar
    primary: bool = False
    device: str = ""        # \\.\DISPLAYn - Windows' name for it this session

    @property
    def model(self) -> str:
        return model_of(self.key)

    @property
    def size(self) -> Tuple[int, int]:
        return (self.rect[2] - self.rect[0], self.rect[3] - self.rect[1])

    def info(self) -> Dict[str, Any]:
        """What a layout remembers about a display, so that one which isn't
        connected can still be named, drawn, and recognised on a new port."""
        return {"name": self.name, "rect": list(self.rect),
                "work": list(self.work), "primary": self.primary}


@dataclass(frozen=True)
class Anchor:
    """Where a card belongs: a display, the card's window position on it, and
    that display's work area at the time - so the spot can be carried to
    whatever the work area has become since."""

    display: str
    x: int
    y: int
    area: Rect


def model_of(key: str) -> str:
    """The panel model in a device path - ``XEC2380`` in
    ``\\\\?\\DISPLAY#XEC2380#7&1589beb5&1&UID260#{...}`` - or "" for a key
    that isn't one."""
    parts = key.split("#")
    if len(parts) >= 3 and parts[0].upper().endswith("DISPLAY"):
        return parts[1].upper()
    return ""


def from_info(key: str, info: Dict[str, Any]) -> Optional[Display]:
    """A remembered display, rebuilt from what a layout kept of it."""
    try:
        rect = tuple(int(v) for v in info.get("rect", ()))
        work = tuple(int(v) for v in (info.get("work") or rect))
        if len(rect) != 4 or len(work) != 4:
            return None
        return Display(key, str(info.get("name") or ""), rect, work,
                       bool(info.get("primary")))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  Asking Windows
# --------------------------------------------------------------------------- #
class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32),
                ("DeviceString", wt.WCHAR * 128), ("StateFlags", wt.DWORD),
                ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _PATH_SOURCE(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wt.UINT), ("modeInfoIdx", wt.UINT),
                ("statusFlags", wt.UINT)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wt.UINT), ("Denominator", wt.UINT)]


class _PATH_TARGET(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wt.UINT), ("modeInfoIdx", wt.UINT),
                ("outputTechnology", wt.UINT), ("rotation", wt.UINT),
                ("scaling", wt.UINT), ("refreshRate", _RATIONAL),
                ("scanLineOrdering", wt.UINT), ("targetAvailable", wt.BOOL),
                ("statusFlags", wt.UINT)]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE), ("targetInfo", _PATH_TARGET),
                ("flags", wt.UINT)]


class _MODE_INFO(ctypes.Structure):
    # Only counted and passed through; the 48-byte union is never read.
    _fields_ = [("infoType", wt.UINT), ("id", wt.UINT), ("adapterId", _LUID),
                ("info", ctypes.c_byte * 48)]


class _INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wt.UINT), ("size", wt.UINT), ("adapterId", _LUID),
                ("id", wt.UINT)]


class _TARGET_NAME(ctypes.Structure):
    _fields_ = [("header", _INFO_HEADER), ("flags", wt.UINT),
                ("outputTechnology", wt.UINT), ("edidManufactureId", wt.USHORT),
                ("edidProductCodeId", wt.USHORT), ("connectorInstance", wt.UINT),
                ("monitorFriendlyDeviceName", wt.WCHAR * 64),
                ("monitorDevicePath", wt.WCHAR * 128)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", _INFO_HEADER), ("viewGdiDeviceName", wt.WCHAR * 32)]


MONITORINFOF_PRIMARY = 1
EDD_GET_DEVICE_INTERFACE_NAME = 1
QDC_ONLY_ACTIVE_PATHS = 2
_GET_SOURCE_NAME, _GET_TARGET_NAME = 1, 2

_user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
_user32.EnumDisplayDevicesW.argtypes = [wt.LPCWSTR, wt.DWORD,
                                        ctypes.POINTER(_DISPLAY_DEVICEW), wt.DWORD]
_MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                                      ctypes.POINTER(wt.RECT), wt.LPARAM)


def _enum() -> List[Tuple[str, Rect, Rect, bool]]:
    """(\\\\.\\DISPLAYn, bounds, work area, main?) for every monitor - cheap,
    so it can be asked on every save."""
    out: List[Tuple[str, Rect, Rect, bool]] = []
    try:
        def _cb(hmon, _hdc, _rc, _lp):
            mi = _MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(mi)
            if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                m, w = mi.rcMonitor, mi.rcWork
                out.append((mi.szDevice, (m.left, m.top, m.right, m.bottom),
                            (w.left, w.top, w.right, w.bottom),
                            bool(mi.dwFlags & MONITORINFOF_PRIMARY)))
            return True

        _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    except Exception:
        pass
    return out


def _names() -> Dict[str, Tuple[str, str]]:
    """\\\\.\\DISPLAYn -> (device path, the monitor's own name), for every
    monitor in use.

    From QueryDisplayConfig, the API behind Windows' own Display settings and
    the only one that names a monitor the way it names itself: EnumDisplayDevices
    calls nearly every one "Generic PnP Monitor".
    """
    out: Dict[str, Tuple[str, str]] = {}
    try:
        n_paths, n_modes = wt.UINT(), wt.UINT()
        if _user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS,
                                               ctypes.byref(n_paths),
                                               ctypes.byref(n_modes)) != 0:
            return out
        paths = (_PATH_INFO * max(1, n_paths.value))()
        modes = (_MODE_INFO * max(1, n_modes.value))()
        if _user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths),
                                      paths, ctypes.byref(n_modes), modes,
                                      None) != 0:
            return out
        for i in range(n_paths.value):
            path = paths[i]
            src = _SOURCE_NAME()
            src.header.type, src.header.size = _GET_SOURCE_NAME, ctypes.sizeof(src)
            src.header.adapterId, src.header.id = (path.sourceInfo.adapterId,
                                                   path.sourceInfo.id)
            tgt = _TARGET_NAME()
            tgt.header.type, tgt.header.size = _GET_TARGET_NAME, ctypes.sizeof(tgt)
            tgt.header.adapterId, tgt.header.id = (path.targetInfo.adapterId,
                                                   path.targetInfo.id)
            if (_user32.DisplayConfigGetDeviceInfo(ctypes.byref(src)) == 0
                    and _user32.DisplayConfigGetDeviceInfo(ctypes.byref(tgt)) == 0):
                # A cloned display is two monitors on one source; the first
                # one stands for both.
                out.setdefault(src.viewGdiDeviceName, (
                    tgt.monitorDevicePath, tgt.monitorFriendlyDeviceName))
    except Exception:
        pass
    return out


def _interface(device: str) -> str:
    """A monitor's device path by the older route, for when the display-config
    API has nothing to say."""
    try:
        dd = _DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if _user32.EnumDisplayDevicesW(device, 0, ctypes.byref(dd),
                                       EDD_GET_DEVICE_INTERFACE_NAME):
            return dd.DeviceID
    except Exception:
        pass
    return ""


def _work_area() -> Rect:
    r = wt.RECT()
    if not _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
        return (0, 0, 1920, 1080)
    return (r.left, r.top, r.right, r.bottom)


def _build(found: List[Tuple[str, Rect, Rect, bool]]) -> List[Display]:
    if not found:
        # No monitor at all (every one asleep, say). Something has to stand in
        # for one, or there would be nowhere to put anything.
        area = _work_area()
        return [Display("primary", "Display", area, area, True)]
    names = _names()
    out: List[Display] = []
    seen = set()
    for device, rect, work, primary in found:
        path, name = names.get(device, ("", ""))
        key = path or _interface(device) or device
        if key in seen:                         # two keys must never collide
            key = f"{key}|{device}"
        seen.add(key)
        if not name:
            number = device.rstrip("\\").split("DISPLAY")[-1]
            name = f"Display {number}" if number.isdigit() else "Display"
        out.append(Display(key, name, rect, work, primary, device))
    # The main display first, then left to right: the order a person reads them.
    out.sort(key=lambda d: (not d.primary, d.rect[0], d.rect[1]))
    return out


def signature() -> tuple:
    """Every monitor's bounds and work area - enough to notice that something
    changed, without asking the display driver for names."""
    return tuple(_enum())


_cache: Tuple[tuple, List[Display]] = ((), [])


def scan() -> List[Display]:
    """The displays connected right now, asked afresh."""
    global _cache
    sig = signature()
    _cache = (sig, _build(list(sig)))
    return list(_cache[1])


def current(force: bool = False) -> List[Display]:
    """The displays connected now, main first. Never empty.

    Names and device paths are only fetched again when the monitors' bounds have
    changed - so this is cheap enough to ask on every drop and every save - or
    when ``force`` says a monitor may have been swapped for another one of the
    same size, which bounds alone can't show.
    """
    if force or not _cache[1] or signature() != _cache[0]:
        return scan()
    return list(_cache[1])


# --------------------------------------------------------------------------- #
#  Questions about a set of displays
# --------------------------------------------------------------------------- #
def at(x: float, y: float, among: Optional[Sequence[Display]] = None) -> Display:
    """The display a point is on, or the one nearest it - which is what
    MonitorFromPoint answers with MONITOR_DEFAULTTONEAREST."""
    screens = list(among) if among else current()
    best, gap = screens[0], None
    for d in screens:
        l, t, r, b = d.rect
        dx = max(l - x, 0.0, x - (r - 1))
        dy = max(t - y, 0.0, y - (b - 1))
        g = dx * dx + dy * dy
        if g == 0:
            return d
        if gap is None or g < gap:
            best, gap = d, g
    return best


def work_area_at(x: float, y: float) -> Rect:
    return at(x, y).work


def primary(among: Sequence[Display]) -> Display:
    return next((d for d in among if d.primary), among[0])


def find(key: str, among: Sequence[Display]) -> Optional[Display]:
    return next((d for d in among if d.key == key), None)


def labels(screens: Iterable[Display]) -> Dict[str, str]:
    """A short name for each display, by where it sits: "Main" for the main
    one and "Left", "Right", "Above" or "Below" for the rest, numbered outward
    when two share a side ("Left", "Left 2").

    Places rather than numbers. Windows' own numbers are per session and often
    differ from the ones Display settings shows, and nobody remembers which
    monitor is 2 - but everyone knows which one is on their left.
    """
    screens = list(screens)
    if not screens:
        return {}
    main = primary(screens)
    out = {main.key: "Main"}
    mx, my = (main.rect[0] + main.rect[2]) / 2.0, (main.rect[1] + main.rect[3]) / 2.0
    sides: Dict[str, List[Tuple[float, Display]]] = {}
    for d in screens:
        if d.key == main.key:
            continue
        dx = (d.rect[0] + d.rect[2]) / 2.0 - mx
        dy = (d.rect[1] + d.rect[3]) / 2.0 - my
        if abs(dx) >= abs(dy):
            side = "Left" if dx < 0 else "Right"
        else:
            side = "Above" if dy < 0 else "Below"
        sides.setdefault(side, []).append((math.hypot(dx, dy), d))
    for side, items in sides.items():
        items.sort(key=lambda item: item[0])
        for i, (_dist, d) in enumerate(items):
            out[d.key] = side if i == 0 else f"{side} {i + 1}"
    return out


def carry(x: int, y: int, w: int, h: int, then: Rect, now: Rect) -> Tuple[int, int]:
    """Where a ``w`` x ``h`` card at (x, y) on the work area ``then`` belongs on
    the work area ``now``.

    When the two are the same size - the monitor was moved about in the
    arrangement, or plugged into another port - the card just keeps its place on
    its screen. When the size changed, each axis keeps the card's distance from
    the *nearer* edge: a card 24 px in from the right edge at one resolution is
    24 px in from the right edge at the next. That keeps a column down one side
    lined up, where spreading positions out in proportion would nudge every card
    by a different amount and the layout would come apart. Either way the card
    ends up on the screen.
    """
    tl, tt, tr, tb = then
    nl, nt, nr, nb = now
    nx = x + ((nl - tl) if x + w / 2.0 <= (tl + tr) / 2.0 else (nr - tr))
    ny = y + ((nt - tt) if y + h / 2.0 <= (tt + tb) / 2.0 else (nb - tb))
    nx = max(nl, min(nx, nr - w))
    ny = max(nt, min(ny, nb - h))
    return int(nx), int(ny)


def resolve(saved: Dict[str, Dict[str, Any]], screens: Sequence[Display],
            known: Iterable[str] = ()) -> Dict[str, Display]:
    """Which connected display each saved one is now, for those that can be told.

    In order of certainty: the same device path - the same monitor on the same
    port; then, for a monitor moved to another port or back under a new path
    after a driver update, the one newly seen display of the same model; then a
    newly seen display sitting exactly where the saved one sat, which is all
    but certainly its replacement.

    "Newly seen" matters. Two identical monitors share a model, and with one of
    them unplugged the other must not be taken for it. ``known`` is every key
    the caller has seen connected; a display among those is only ever itself.
    """
    by_key = {d.key: d for d in screens}
    out: Dict[str, Display] = {k: by_key[k] for k in saved if k in by_key}
    seen = set(known) | set(saved)
    fresh = [d for d in screens if d.key not in seen]

    missing = [k for k in saved if k not in out]
    models = [model_of(k) for k in missing]
    for k, m in zip(missing, models):
        # Only when it can be one monitor: two missing of a model, or two new
        # ones, and the model says nothing about which is which.
        same = [d for d in fresh if m and d.model == m]
        if m and models.count(m) == 1 and len(same) == 1:
            out[k] = same[0]
            fresh.remove(same[0])

    for k in missing:
        if k in out:
            continue
        rect = tuple(saved[k].get("rect") or ()) if isinstance(saved[k], dict) else ()
        hit = next((d for d in fresh if d.rect == rect), None)
        if hit is not None:
            out[k] = hit
            fresh.remove(hit)
    return out
