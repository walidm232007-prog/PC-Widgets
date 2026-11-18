"""
livewall.py
===========
Live wallpapers - Wallpaper Engine, Lively Wallpaper and the like - and what
they are actually showing.

Such an app never changes the wallpaper Windows has on record: it draws its own
window inside the desktop's wallpaper layer, under the icons, so the glass,
built from the wallpaper files (glass.py), went on showing a picture that
wasn't on screen. The layer is a ``WorkerW`` window: on Windows 11 24H2 and
later a child of ``Progman``, before that a top-level window behind the icons'
own; the app's window sits inside it. With Wallpaper Engine on Windows 11 that
is ``Progman > WorkerW > WPEDesktopDX11Window "WPELiveWallpaper"``.

``PrintWindow`` with ``PW_RENDERFULLCONTENT`` grabs that layer offscreen,
DirectX content and all: the live wallpaper where it draws, and Windows' own
wallpaper on any monitor it leaves alone - never the icons, which live in a
sibling, nor any window, the cards included. Nothing is shown and nothing
moves. A grab of a 3000x1920 desktop takes about 40 ms on a Ryzen 5 5600G,
14 ms of it CPU, the rest waiting on the compositor.

The grab asks the app's window to draw itself, so a hung wallpaper app would
hang the caller: glass.py only grabs from a thread of its own.
"""

from __future__ import annotations

import ctypes as C
import ctypes.wintypes as W
import threading
from typing import Optional, Tuple

from PySide6.QtGui import QImage

u = C.windll.user32
g = C.windll.gdi32

u.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
u.FindWindowW.restype = W.HWND
u.FindWindowExW.argtypes = [W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR]
u.FindWindowExW.restype = W.HWND
u.IsWindow.argtypes = [W.HWND]
u.IsWindowVisible.argtypes = [W.HWND]
u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
u.GetSystemMetrics.argtypes = [C.c_int]
u.GetDC.argtypes = [W.HWND]
u.GetDC.restype = W.HDC
u.ReleaseDC.argtypes = [W.HWND, W.HDC]
u.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
g.CreateCompatibleDC.argtypes = [W.HDC]
g.CreateCompatibleDC.restype = W.HDC
g.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]
g.CreateCompatibleBitmap.restype = W.HBITMAP
g.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
g.SelectObject.restype = W.HGDIOBJ
g.DeleteObject.argtypes = [W.HGDIOBJ]
g.DeleteDC.argtypes = [W.HDC]
g.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
g.BitBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD]
g.CreateDIBSection.argtypes = [W.HDC, C.c_void_p, W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]
g.CreateDIBSection.restype = W.HBITMAP

PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020


class _BITMAPINFOHEADER(C.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
                ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
                ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG),
                ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]


def _children(parent: int, cls: Optional[str] = None):
    child = None
    while True:
        child = u.FindWindowExW(parent, child, cls, None)
        if not child:
            return
        yield child


def _covers_desktop(hwnd: int) -> bool:
    """At least half the virtual screen: the wallpaper layer spans all of it,
    and Explorer's other WorkerW windows are small or hidden."""
    r = W.RECT()
    if not u.GetWindowRect(hwnd, C.byref(r)):
        return False
    area = u.GetSystemMetrics(78) * u.GetSystemMetrics(79)    # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
    return (r.right - r.left) * (r.bottom - r.top) * 2 >= area > 0


def find_layer() -> int:
    """The wallpaper layer a live wallpaper is drawing in, or 0 with none.

    A ``WorkerW`` counts only while it's visible, spans the desktop and holds a
    visible window: the app's. Explorer can leave an empty one behind when the
    app quits, and that shows nothing the wallpaper files don't. One holding
    the icons (``SHELLDLL_DefView``, before 24H2) never counts, whatever else
    is in it - a grab of it would put the icons in the glass. Cheap - a few
    window lookups - so it can be asked every few seconds."""
    progman = u.FindWindowW("Progman", None)
    layers = list(_children(progman, "WorkerW")) if progman else []   # Windows 11 24H2+
    layers += list(_children(None, "WorkerW"))                         # before that
    for layer in layers:
        if (not u.IsWindowVisible(layer) or u.FindWindowExW(layer, None, "SHELLDLL_DefView", None)
                or not _covers_desktop(layer)):
            continue
        if any(u.IsWindowVisible(child) for child in _children(layer)):
            return int(layer)
    return 0


def alive(layer: int) -> bool:
    return bool(layer) and bool(u.IsWindow(layer)) and bool(u.IsWindowVisible(layer))


_kept = threading.local()        # a grabbing thread's bitmap and image, between grabs


def grab(hwnd: int, keep: bool = False) -> Optional[Tuple[QImage, Tuple[int, int]]]:
    """The window as it draws itself, offscreen: (image, its top-left on the
    virtual screen), or None if it wouldn't draw. The image is Format_BGR888,
    as read_bitmap leaves it.

    ``keep``, for a thread that grabs over and over: its bitmap and image are
    kept for its next grab of the same size rather than made afresh, which
    took a 3000x1920 grab from 52 ms and 23 ms of CPU to about 39 and 14. That
    next grab then overwrites the image, so copy what must outlast it, and
    call release() when done grabbing."""
    r = W.RECT()
    if not u.GetWindowRect(hwnd, C.byref(r)):
        return None
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    parts = getattr(_kept, "parts", None) if keep else None
    if parts is not None and parts[0] != (w, h):
        release()
        parts = None
    if parts is None:
        screen = u.GetDC(None)
        try:
            dc = g.CreateCompatibleDC(screen)
            bmp = g.CreateCompatibleBitmap(screen, w, h)
        finally:
            u.ReleaseDC(None, screen)
        parts = ((w, h), dc, bmp, QImage(w, h, QImage.Format_BGR888))
        if keep:
            _kept.parts = parts
    _size, dc, bmp, img = parts
    try:
        old = g.SelectObject(dc, bmp)
        drawn = u.PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT)
        g.SelectObject(dc, old)            # GetDIBits reads a bitmap no DC holds
        if not drawn or read_bitmap(dc, bmp, w, h, into=img) is None:
            return None
        return img, (r.left, r.top)
    finally:
        if not keep:
            g.DeleteObject(bmp)
            g.DeleteDC(dc)


def grab_parts(hwnd: int, rects, keep: bool = False):
    """The window as it draws itself, offscreen, read back only where each of
    ``rects`` (x, y, w, h on the virtual screen) lies: ([(rect, image), ...],
    the window's top-left), each rect clipped to the window and each image
    Format_BGR888; or None if it wouldn't draw.

    The compositor still draws all of it, so the grab costs what a whole one
    does, but four card-sized patches read back in 0.6 ms, where the whole
    3000x1920 took 4 ms and its conversion 5 more. ``keep`` as for grab(),
    and the two share the kept bitmap."""
    r = W.RECT()
    if not u.GetWindowRect(hwnd, C.byref(r)):
        return None
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    parts = getattr(_kept, "parts", None) if keep else None
    if parts is not None and parts[0] != (w, h):
        release()
        parts = None
    if parts is None:
        screen = u.GetDC(None)
        try:
            dc = g.CreateCompatibleDC(screen)
            bmp = g.CreateCompatibleBitmap(screen, w, h)
        finally:
            u.ReleaseDC(None, screen)
        parts = ((w, h), dc, bmp, QImage(w, h, QImage.Format_BGR888))
        if keep:
            _kept.parts = parts
    _size, dc, bmp, _img = parts
    old = g.SelectObject(dc, bmp)
    try:
        if not u.PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT):
            return None
        return read_parts(dc, (r.left, r.top, w, h), rects, keep), (r.left, r.top)
    finally:
        g.SelectObject(dc, old)
        if not keep:
            g.DeleteObject(bmp)
            g.DeleteDC(dc)


def read_parts(dc: int, where: Tuple[int, int, int, int], rects, keep: bool = False):
    """Patches of the bitmap selected into ``dc`` - the window at ``where``,
    (x, y, w, h) on the virtual screen - one for each of ``rects``, in order:
    (the rect clipped to the window, a Format_BGR888 image), or None for one
    off it. Each is copied through a 24-bit DIB section of its size, kept for
    the next call with ``keep``."""
    left, top, w, h = where
    out = []
    sections = getattr(_kept, "sections", None) if keep else None
    if sections is None:
        sections = {}
        if keep:
            _kept.sections = sections
    screen = u.GetDC(None)
    sdc = g.CreateCompatibleDC(screen)
    try:
        for x, y, rw, rh in rects:
            x0, y0 = max(x, left), max(y, top)
            x1, y1 = min(x + rw, left + w), min(y + rh, top + h)
            if x1 - x0 < 1 or y1 - y0 < 1:
                out.append(None)
                continue
            pw, ph = x1 - x0, y1 - y0
            if (pw, ph) not in sections:
                info = _BITMAPINFOHEADER(C.sizeof(_BITMAPINFOHEADER), pw, -ph, 1, 24, 0, 0, 0, 0, 0, 0)
                bits = C.c_void_p()
                section = g.CreateDIBSection(screen, C.byref(info), 0, C.byref(bits), None, 0)
                if not section:
                    out.append(None)
                    continue
                sections[(pw, ph)] = (section, bits)
            section, bits = sections[(pw, ph)]
            held = g.SelectObject(sdc, section)
            g.BitBlt(sdc, 0, 0, pw, ph, dc, x0 - left, y0 - top, SRCCOPY)
            g.SelectObject(sdc, held)
            g.GdiFlush()
            img = QImage(pw, ph, QImage.Format_BGR888)
            size = img.bytesPerLine() * ph
            C.memmove((C.c_ubyte * size).from_buffer(img.bits()), bits, size)
            out.append(((x0, y0, pw, ph), img))
    finally:
        g.DeleteDC(sdc)
        u.ReleaseDC(None, screen)
        if not keep:
            for section, _bits in sections.values():
                g.DeleteObject(section)
    return out


def release() -> None:
    """Free the bitmaps the calling thread kept with keep=True."""
    parts = getattr(_kept, "parts", None)
    if parts is not None:
        _kept.parts = None
        g.DeleteObject(parts[2])
        g.DeleteDC(parts[1])
    sections = getattr(_kept, "sections", None)
    if sections:
        _kept.sections = None
        for section, _bits in sections.values():
            g.DeleteObject(section)


def read_bitmap(dc: int, bmp: int, w: int, h: int,
                into: Optional[QImage] = None) -> Optional[QImage]:
    """A GDI bitmap's pixels as an opaque QImage, Format_BGR888: read back as
    24-bit, top-down - blue, green, red, rows padded to 4 bytes - which is that
    format's layout exactly. 32-bit would leave the alpha byte at 0, and the
    glass built from that would come out clear. Read into ``into``, a BGR888
    image of the size, when given."""
    img = into if into is not None else QImage(w, h, QImage.Format_BGR888)
    info = _BITMAPINFOHEADER(C.sizeof(_BITMAPINFOHEADER), w, -h, 1, 24, 0, 0, 0, 0, 0, 0)
    buf = (C.c_ubyte * (img.bytesPerLine() * h)).from_buffer(img.bits())
    lines = g.GetDIBits(dc, bmp, 0, h, buf, C.byref(info), 0)
    del buf
    return img if lines == h else None


def fingerprint(img: QImage) -> bytes:
    """A 32x18 thumbnail's pixels: enough to tell a new frame from the same one."""
    small = img.scaled(32, 18).convertToFormat(QImage.Format_RGB888)
    return bytes(small.constBits())[: 32 * 18 * 3]


def differs(a: Optional[bytes], b: Optional[bytes], tolerance: float = 2.0) -> bool:
    """Whether two fingerprints show different frames: their mean difference,
    0-255 a channel, above ``tolerance``. A paused wallpaper grabs the same."""
    if a is None or b is None or len(a) != len(b):
        return True
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) > tolerance


def moved(a: Optional[bytes], b: Optional[bytes], tolerance: float = 2.0,
          spot: int = 12) -> bool:
    """Whether a card's thumbnail moved: differs() on the whole, or any one
    cell more than ``spot`` levels - a light drifting across one corner moves
    the mean of 256 cells by next to nothing, a scatter of sparkles moves no
    cell that far."""
    if differs(a, b, tolerance):
        return True
    return max(abs(x - y) for x, y in zip(a, b)) > spot


def blank(mark: bytes) -> bool:
    """Whether a grab, by its fingerprint, came back one flat colour - black,
    as a rule. That's a failed one: the app drew nothing into it, so the glass
    keeps what it showed."""
    return not mark or all(max(mark[c::3]) - min(mark[c::3]) < 8 for c in range(3))
