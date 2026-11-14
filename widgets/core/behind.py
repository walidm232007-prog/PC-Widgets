"""
behind.py
=========
The dock's glass, seen through to what is really behind it.

glass.py composites the wallpaper, and that is all the cards ever need: they
live on the desktop layer, so the wallpaper *is* what is behind them. The dock
floats over everything, and with a window open under it the wallpaper was the
wrong picture: a white document behind a dock showing a blurred beach.

Windows' own materials can't stand in, for the reasons glass.py gives (they
paint the whole window, shadow gutter and all, with Windows' corners, and they
can't be bent for liquid glass). So the dock captures the strip of screen it
sits on and blurs or bends that, with the same code that does the wallpaper.

Capturing the screen under a window would normally capture the window too.
``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`` (Windows 10 2004 and
later) leaves it out of every capture, so the strip shows what is under the
dock. That has a price: the dock doesn't appear in screenshots, recordings or
screen sharing either. Turned off, the dock is put back into them.

What it costs. A GDI grab of a 1100x64 strip is about 5 ms on a Ryzen 5 5600G
at 1080p - far too slow for a 200 Hz frame, so it never runs per frame:

- Only while a window actually covers the strip. With just the desktop behind
  the dock, the wallpaper glass is the same picture for nothing.
- At most ten times a second, on a thread of its own, which also blurs (and,
  for liquid glass, halves and frosts) what it grabbed.
- The dock is told only when the strip changed - an idle document behind it
  costs the grab and nothing else.
- Not at all while the dock is down below the screen, so a full-screen game
  pays nothing.

The dock's slide and its growing row keep using the last capture, exactly as
they kept the last wallpaper bend, so nothing new happens per frame.

``LiveGlass`` stands in for the board's GlassBackdrop wherever the dock paints:
it answers from the capture where it covers the asked-for rect, and from the
wallpaper everywhere else.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QImage

from . import glass as _glass

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11

INTERVAL = 0.1           # s between grabs: ten a second at most
MARGIN = 24              # px grabbed round the strip, so the blur has context
CHANGED = 1.5            # mean difference, in 0..255 levels, that counts as a change

_IGNORE = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

try:
    _user = ctypes.WinDLL("user32", use_last_error=True)
    _gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    _dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    _ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    _user.GetDC.restype = wt.HDC
    _user.GetDC.argtypes = [wt.HWND]
    _user.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    _gdi.CreateCompatibleDC.restype = wt.HDC
    _gdi.CreateCompatibleDC.argtypes = [wt.HDC]
    _gdi.CreateDIBSection.restype = wt.HBITMAP
    _gdi.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
    _gdi.SelectObject.restype = wt.HGDIOBJ
    _gdi.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    _gdi.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
    _gdi.DeleteObject.argtypes = [wt.HGDIOBJ]
    _gdi.DeleteDC.argtypes = [wt.HDC]
    _user.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
    _user.EnumWindows.argtypes = [_ENUM, wt.LPARAM]
except (AttributeError, OSError):          # not Windows
    _user = _gdi = _dwm = None


def exclude(hwnd: int, on: bool = True) -> bool:
    """Leave a window out of screen captures (on), or put it back."""
    if _user is None or not hwnd:
        return False
    return bool(_user.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE))


def covered(rect: Tuple[int, int, int, int], skip: Sequence[int] = ()) -> bool:
    """Whether a visible window other than ``skip`` overlaps ``rect`` (left,
    top, right, bottom): anything that would make the strip differ from the
    wallpaper. False positives - an invisible overlay over the whole screen -
    only cost a grab; they never make the glass wrong."""
    if _user is None:
        return False
    l, t, r, b = rect
    skip = {int(h) for h in skip if h}
    found = [False]
    box = wt.RECT()
    cls = ctypes.create_unicode_buffer(64)
    cloak = wt.DWORD()

    @_ENUM
    def visit(hwnd, _):
        h = int(hwnd or 0)
        if h in skip or not _user.IsWindowVisible(hwnd) or _user.IsIconic(hwnd):
            return True
        if not _user.GetWindowRect(hwnd, ctypes.byref(box)):
            return True
        if box.right <= l or box.left >= r or box.bottom <= t or box.top >= b:
            return True
        _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloak), ctypes.sizeof(cloak))
        if cloak.value:
            return True
        _user.GetClassNameW(hwnd, cls, len(cls))
        if cls.value in _IGNORE:
            return True
        found[0] = True
        return False                       # one is enough

    _user.EnumWindows(visit, 0)
    return found[0]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(rect: Tuple[int, int, int, int]):
    """The screen in ``rect`` (left, top, right, bottom, virtual-screen px) as
    an (h, w, 4) uint8 array, blue-green-red-x, or None. Windows excluded from
    capture are not in it."""
    if _user is None:
        return None
    import numpy as np
    l, t, r, b = rect
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    screen = _user.GetDC(None)
    if not screen:
        return None
    mem = _gdi.CreateCompatibleDC(screen)
    info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = _gdi.CreateDIBSection(screen, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    try:
        if not mem or not bmp or not bits.value:
            return None
        old = _gdi.SelectObject(mem, bmp)
        ok = _gdi.BitBlt(mem, 0, 0, w, h, screen, l, t, 0x00CC0020)     # SRCCOPY
        _gdi.SelectObject(mem, old)
        if not ok:
            return None
        buf = (ctypes.c_uint8 * (w * h * 4)).from_address(bits.value)
        return np.frombuffer(buf, np.uint8).reshape(h, w, 4).copy()
    finally:
        if bmp:
            _gdi.DeleteObject(bmp)
        if mem:
            _gdi.DeleteDC(mem)
        _user.ReleaseDC(None, screen)


class Patch:
    """One capture, ready to paint: the strip blurred as glass.py blurs the
    wallpaper, and for liquid glass the clear half-size planes lens.py bends."""

    def __init__(self, rect, image: QImage, planes, generation: int):
        self.rect = QRect(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
        self.image = image
        self.planes = planes
        self.generation = generation

    def holds(self, x: int, y: int, w: int, h: int) -> bool:
        return self.rect.contains(QRect(int(x), int(y), max(1, int(w)), max(1, int(h))))


def build(pixels, rect, liquid: bool, frost: Tuple[int, int], generation: int) -> Patch:
    """For frosted glass, blur a grabbed strip as the wallpaper is blurred;
    for liquid glass, halve and frost it into planes to bend. Only the one in
    use is built - the other is half the cost again."""
    import numpy as np
    h, w = pixels.shape[:2]
    rgba = np.ascontiguousarray(pixels[..., [2, 1, 0, 3]])
    rgba[..., 3] = 255
    canvas = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
    image = planes = None
    if not liquid:
        small = canvas.scaled(max(1, w // _glass.DOWNSCALE), max(1, h // _glass.DOWNSCALE),
                              Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        small = _glass._box_blur(small, _glass.BOX_RADIUS, _glass.BOX_PASSES)
        image = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    else:
        half = canvas.scaled(max(2, w // _glass.CLEAR_SCALE), max(2, h // _glass.CLEAR_SCALE),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        radius, passes = frost
        if radius and passes:
            half = _glass._box_blur(half, radius, passes)
        half = half.convertToFormat(QImage.Format_RGBA8888)
        hw, hh = half.width(), half.height()
        arr = np.frombuffer(half.constBits(), np.uint8,
                            count=half.bytesPerLine() * hh).reshape(hh, half.bytesPerLine() // 4, 4)
        planes = np.ascontiguousarray(arr[:, :hw, :3].transpose(2, 0, 1))
    return Patch(rect, image, planes, generation)


class Behind(QObject):
    """Watches the strip of screen under the dock and keeps a Patch of it
    while a window covers it. ``changed`` fires, on the GUI thread, when the
    patch is new or gone."""
    changed = Signal()

    def __init__(self, base) -> None:
        super().__init__()
        self.base = base                   # the board's GlassBackdrop
        self.patch: Optional[Patch] = None
        self.grab_ms = 0.0                 # what the last grab and build cost
        self._rect: Optional[Tuple[int, int, int, int]] = None
        self._skip: List[int] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None     # set and cleared under _lock
        self._count = 0
        self._style = None                 # (liquid, frost) the patch was built for

    def watch(self, rects: Sequence[QRect], skip: Sequence[int] = ()) -> None:
        """Capture the box round ``rects`` (virtual-screen px), skipping the
        windows in ``skip`` when looking for cover. No rects: stop."""
        rects = [r for r in rects if r is not None and not r.isEmpty()]
        if not rects:
            self.stop()
            return
        l = min(r.left() for r in rects) - MARGIN
        t = min(r.top() for r in rects) - MARGIN
        r_ = max(r.left() + r.width() for r in rects) + MARGIN
        b = max(r.top() + r.height() for r in rects) + MARGIN
        try:
            from . import winapi
            vl, vt, vr, vb = winapi.virtual_screen()
            l, t, r_, b = max(l, vl), max(t, vt), min(r_, vr), min(b, vb)
        except Exception:
            pass
        with self._lock:
            self._rect = (l, t, r_, b)
            self._skip = [int(h) for h in skip if h]
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="dock-behind",
                                                daemon=True)
                self._thread.start()
        self._wake.set()

    def stop(self, keep: bool = False) -> None:
        """Stop grabbing. ``keep`` holds on to the last capture - the dock
        sliding down keeps it for the way back up."""
        with self._lock:
            self._rect = None
        self._wake.set()
        if not keep:
            self._drop()

    def poke(self) -> None:
        """Grab soon: a window moved, the style changed."""
        self._wake.set()

    def _drop(self) -> None:
        with self._lock:
            had = self.patch is not None
            self.patch = None
        if had:
            self.changed.emit()

    def _run(self) -> None:
        last = None                        # the previous grab, to tell a change
        while True:
            with self._lock:
                rect, skip = self._rect, list(self._skip)
                if rect is None:
                    self._thread = None    # watch() starts another
                    return
            try:
                if not covered(rect, skip):
                    last = None
                    self._drop()
                else:
                    started = time.perf_counter()
                    pixels = grab(rect)
                    style = (bool(self.base.liquid),
                             _glass.FROSTS.get(self.base.frost, _glass.FROSTS["light"]))
                    if pixels is not None and (last is None or last.shape != pixels.shape or
                                               style != self._style or
                                               _differs(last, pixels)):
                        last = pixels
                        self._count += 1
                        patch = build(pixels, rect, style[0], style[1], self._count)
                        self.grab_ms = (time.perf_counter() - started) * 1000.0
                        with self._lock:
                            if self._rect == rect:
                                self.patch, self._style = patch, style
                        self.changed.emit()
            except Exception:
                last = None                # a failed grab: try again next time
            self._wake.wait(INTERVAL)
            self._wake.clear()
            time.sleep(0.02)               # a burst of pokes still caps the rate


def _differs(a, b) -> bool:
    """Whether two grabs differ enough to rebuild: every fourth pixel each
    way, a sixteenth of the work, is plenty for glass this blurred."""
    import numpy as np
    sa = a[::4, ::4, :3].astype(np.int16)
    sb = b[::4, ::4, :3].astype(np.int16)
    return float(np.abs(sa - sb).mean()) > CHANGED


class LiveGlass:
    """GlassBackdrop's face for the dock: the capture where it covers what is
    asked for, the wallpaper everywhere else."""

    def __init__(self, behind: Behind) -> None:
        self.behind = behind
        self.base = behind.base

    def _patch(self, x, y, w, h) -> Optional[Patch]:
        patch = self.behind.patch
        return patch if patch is not None and patch.holds(x, y, w, h) else None

    @property
    def tint(self):
        return self.base.tint

    @property
    def liquid(self) -> bool:
        return self.base.liquid

    @property
    def ready(self) -> bool:
        patch = self.behind.patch
        if patch is not None and self.base.liquid and patch.planes is not None:
            return True
        return self.base.ready

    @property
    def generation(self):
        patch = self.behind.patch
        return self.base.generation if patch is None else ("live", self.base.generation,
                                                           patch.generation)

    def bend(self, sx, sy, w, h, radius, step=None):
        patch = self._patch(sx, sy, w, h)
        if patch is None or patch.planes is None:
            return self.base.bend(sx, sy, w, h, radius, step)
        from . import lens
        w, h = int(round(w)), int(round(h))
        step = int(step or lens.STEP)
        return lens.bend(patch.planes, _glass.CLEAR_SCALE, (patch.rect.x(), patch.rect.y()),
                         int(sx), int(sy), w, h, radius, step)

    def rim(self, w, h, radius, dark):
        return self.base.rim(w, h, radius, dark)

    def paint_into(self, p, dest, screen_x, screen_y) -> bool:
        patch = self._patch(screen_x, screen_y, dest.width(), dest.height())
        if patch is None or patch.image is None:
            return self.base.paint_into(p, dest, screen_x, screen_y)
        src = QRect(int(screen_x - patch.rect.x()), int(screen_y - patch.rect.y()),
                    int(dest.width()), int(dest.height()))
        p.drawImage(dest, patch.image, src)
        return True
