"""
glass.py
========
Apple-style frosted material: a blurred copy of what is behind the card, tinted
and clipped to the card's shape.

Windows offers Mica and Acrylic through DWM, and neither works here. Both are
applied to the whole *window*, and the window is deliberately larger than the
card so the continuous corners have room to antialias and the shadow has
somewhere to fall - so a DWM material paints the transparent gutter too and
every card ends up sitting in a grey block. DWM can round the window itself,
but only at its own ~8px radius, nowhere near the 28px the cards use.

So the blur is done here instead. That is possible because of where these
widgets live: parked on the desktop layer, the thing behind them *is* the
wallpaper. We composite the wallpaper across the virtual screen exactly as the
shell does, blur it once, and each card then paints the slice sitting behind it
- clipped to its own squircle, with any radius, perfectly antialiased.

"Exactly as the shell does" means monitor by monitor. Each one can show its own
picture, fitted its own way, and the single wallpaper path Windows reports for
the whole desktop names only one of them - which put one monitor's picture,
blurred, behind every card on the others.

The blur is a heavy downscale, a couple of box passes at that small size, and a
smooth upscale. At 1/14 scale a 3840x2160 desktop is a ~275x155 image, so the
whole thing costs a few milliseconds and is done once per wallpaper change.

The cards' liquid glass (lens.py) needs the desktop clear rather than blurred,
so a second copy is kept beside it: half size and only lightly frosted, about
4 MB for a 3000x1920 desk. Building it takes a few hundred milliseconds, so it
happens on a thread, and until it is there a card shows the frosted glass
instead. numpy does the bending; without it (a checkout with only PySide6) the
copy is never built and every card stays frosted.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter

from . import displays, winapi

DOWNSCALE = 14           # bigger = blurrier and cheaper
BOX_PASSES = 3
BOX_RADIUS = 2

CLEAR_SCALE = 2          # the clear copy is kept at 1/2 size...
# ...and frosted by this many box passes of radius 1 at that size: three come to
# about Glisse's own 3px blur. With none, a busy wallpaper's fine detail ran
# straight through the cards' small grey labels.
CLEAR_FROST = 3


def _box_blur(img: QImage, radius: int, passes: int) -> QImage:
    """Separable box blur. Repeated passes approach a Gaussian."""
    try:
        import numpy as np
    except Exception:
        return img       # the downscale/upscale alone still reads as blurred

    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w < 3 or h < 3:
        return img

    ptr = img.bits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=w * h * 4).reshape(h, w, 4)
    buf = arr.astype(np.float32)

    k = radius * 2 + 1
    for _ in range(max(1, passes)):
        pad = np.pad(buf, ((0, 0), (radius, radius), (0, 0)), mode="edge")
        cs = np.cumsum(pad, axis=1)
        cs = np.concatenate([np.zeros((h, 1, 4), np.float32), cs], axis=1)
        buf = (cs[:, k:, :] - cs[:, :-k, :]) / k

        pad = np.pad(buf, ((radius, radius), (0, 0), (0, 0)), mode="edge")
        cs = np.cumsum(pad, axis=0)
        cs = np.concatenate([np.zeros((1, w, 4), np.float32), cs], axis=0)
        buf = (cs[k:, :, :] - cs[:-k, :, :]) / k

    out = np.ascontiguousarray(np.clip(buf, 0, 255).astype(np.uint8))
    # Keep a reference to the buffer on the QImage or it is freed underneath it.
    res = QImage(out.data, w, h, QImage.Format_RGBA8888).copy()
    return res


def _fit(p: QPainter, img: QImage, dest: QRect, mode: int) -> None:
    """Draw a wallpaper into one monitor's rect the way the shell would."""
    w, h = dest.width(), dest.height()
    if w <= 0 or h <= 0:
        return
    if mode == winapi.WALL_STRETCH:
        p.drawImage(dest, img)
    elif mode == winapi.WALL_FIT:
        scaled = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage(dest.left() + (w - scaled.width()) // 2,
                    dest.top() + (h - scaled.height()) // 2, scaled)
    elif mode == winapi.WALL_CENTER:
        p.save()
        p.setClipRect(dest)
        p.drawImage(dest.left() + (w - img.width()) // 2,
                    dest.top() + (h - img.height()) // 2, img)
        p.restore()
    elif mode == winapi.WALL_TILE:
        p.save()
        p.setClipRect(dest)
        for y in range(dest.top(), dest.bottom() + 1, max(1, img.height())):
            for x in range(dest.left(), dest.right() + 1, max(1, img.width())):
                p.drawImage(x, y, img)
        p.restore()
    else:
        # Fill: scaled to cover, the overflow cut off evenly on both sides.
        scaled = img.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        sx = max(0, (scaled.width() - w) // 2)
        sy = max(0, (scaled.height() - h) // 2)
        p.drawImage(dest, scaled, QRect(sx, sy, w, h))


class _Notifier(QObject):
    """Tells the GUI thread the clear copy is ready. A signal emitted from the
    builder thread is delivered queued, on the thread that made this."""
    ready = Signal()


class GlassBackdrop:
    """The blurred desktop, in virtual-screen coordinates - and, for liquid
    glass, a clear one."""

    def __init__(self) -> None:
        self.image: Optional[QImage] = None
        self.origin: Tuple[int, int] = (0, 0)
        self.tint: QColor = QColor(30, 34, 42)
        self._signature: Optional[Tuple] = None
        # The clear copy: (3, H, W) uint8 red, green and blue planes at
        # 1/CLEAR_SCALE size, or None until it's built (or with no numpy).
        self.clear = None
        self.clear_origin: Tuple[int, int] = (0, 0)
        # Bumped whenever the desktop picture changes, so a card can tell its
        # kept bend is for an old one.
        self.generation = 0
        self.notifier = _Notifier()
        self.last_bend_ms = 0.0       # what the last bend cost, its setup aside
        self._lock = threading.Lock()
        self._job = 0
        self._built = threading.Event()

    # ------------------------------------------------------------------ #
    @staticmethod
    def pictures(screens: Sequence[displays.Display]
                 ) -> Tuple[int, List[Tuple[displays.Display, Optional[str]]]]:
        """How the shell fits wallpaper, and the picture on each display.

        Matched by device path, then by bounds. A monitor's own picture can be a
        file that has since been moved or deleted - the shell keeps showing its
        transcoded copy - and then the desktop-wide path is the best there is.
        """
        fallback = winapi.wallpaper_path()
        mode, walls = winapi.wallpapers()
        by_key = {k.lower(): p for k, _r, p in walls}
        by_rect = {r: p for _k, r, p in walls}
        out = []
        for d in screens:
            path = by_key.get(d.key.lower()) or by_rect.get(d.rect)
            out.append((d, path if path and os.path.isfile(path) else fallback))
        return mode, out

    def _read(self):
        """(signature, mode, pictures) for the desktop as it is now."""
        mode, pics = self.pictures(displays.current())
        each = []
        for d, path in pics:
            try:
                stamp = os.path.getmtime(path) if path else 0.0
            except OSError:
                stamp = 0.0
            each.append((d.key, d.rect, path, stamp))
        return (mode, tuple(each), winapi.virtual_screen()), mode, pics

    def _current_signature(self) -> Tuple:
        return self._read()[0]

    def refresh(self, force: bool = False) -> bool:
        """Rebuild if a wallpaper or the display layout changed."""
        sig, mode, pics = self._read()
        if not force and sig == self._signature and self.image is not None:
            return False
        self._signature = sig
        r, g, b = winapi.desktop_colour()
        self.tint = QColor(r, g, b)
        virtual = winapi.virtual_screen()
        self.image, self.origin = self.compose(pics, mode, virtual, QColor(r, g, b))
        self.generation += 1
        self._start_clear(pics, mode, virtual, QColor(r, g, b))
        return True

    # ------------------------------------------------------------------ #
    #  The clear copy, for liquid glass
    # ------------------------------------------------------------------ #
    @property
    def ready(self) -> bool:
        """Whether cards can bend: the clear copy is built for this desktop."""
        return self.clear is not None

    def _start_clear(self, pictures, mode: int, virtual, colour: QColor) -> None:
        with self._lock:
            self._job += 1
            job = self._job
            self.clear = None          # the old one shows the old wallpaper
            self._built.clear()

        def build() -> None:
            try:
                planes, origin = self.compose_clear(pictures, mode, virtual, colour)
            except Exception:
                planes = None          # no numpy, or a picture that won't load
            with self._lock:
                if job != self._job:
                    return             # the desktop changed again meanwhile
                if planes is not None:
                    self.clear, self.clear_origin = planes, origin
                self._built.set()
            if planes is not None:
                self.notifier.ready.emit()

        threading.Thread(target=build, name="glass-clear", daemon=True).start()

    def wait_clear(self, timeout: float = 15.0) -> bool:
        """Block until the clear copy is built (or failed to be). For the self
        test and offscreen renders; the board itself never waits."""
        self._built.wait(timeout)
        return self.ready

    @staticmethod
    def compose_clear(pictures: Sequence[Tuple[displays.Display, Optional[str]]], mode: int,
                      virtual: Tuple[int, int, int, int], colour: QColor):
        """The desktop for liquid glass: fitted like the blurred one, then
        halved and frosted about as much as Glisse's 3px blur frosts the web.
        Returns ((3, H, W) uint8 planes, origin)."""
        import numpy as np

        canvas = GlassBackdrop._canvas(pictures, mode, virtual, colour)
        small = canvas.scaled(max(2, canvas.width() // CLEAR_SCALE),
                              max(2, canvas.height() // CLEAR_SCALE),
                              Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if CLEAR_FROST:
            small = _box_blur(small, 1, CLEAR_FROST)
        small = small.convertToFormat(QImage.Format_RGBA8888)
        w, h = small.width(), small.height()
        rgba = np.frombuffer(small.constBits(), dtype=np.uint8,
                             count=small.bytesPerLine() * h).reshape(h, small.bytesPerLine() // 4, 4)
        planes = np.ascontiguousarray(rgba[:, :w, :3].transpose(2, 0, 1))
        return planes, (virtual[0], virtual[1])

    def bend(self, sx: int, sy: int, w: int, h: int, radius: float,
             step: Optional[int] = None) -> Optional[QImage]:
        """The pane at ``sx, sy`` on the virtual screen seen through liquid
        glass (see lens.py), worked out on a ``step`` px grid (lens.STEP if
        None), or None while there is no clear copy to bend.

        ``last_bend_ms`` is left holding what the bend itself cost. Working out
        a new size's field, once, is kept out of it: counted in, the first bend
        of a new card made a quick PC look slow and its drag went frosted."""
        with self._lock:
            planes, origin = self.clear, self.clear_origin
        if planes is None:
            return None
        from . import lens
        w, h = int(round(w)), int(round(h))
        step = int(step or lens.STEP)
        lens.field(w, h, radius, step)
        started = time.perf_counter()
        img = lens.bend(planes, CLEAR_SCALE, origin, int(sx), int(sy), w, h, radius, step)
        self.last_bend_ms = (time.perf_counter() - started) * 1000.0
        return img

    @staticmethod
    def rim(w: float, h: float, radius: float, dark: bool) -> QImage:
        """The lit rim to lay over a liquid pane (see lens.py)."""
        from . import lens
        return lens.rim(int(round(w)), int(round(h)), float(radius), bool(dark))

    @staticmethod
    def _canvas(pictures: Sequence[Tuple[displays.Display, Optional[str]]], mode: int,
                virtual: Tuple[int, int, int, int], colour: QColor) -> QImage:
        """The desktop as the shell draws it: each display's picture fitted to
        it, across the virtual screen."""
        vl, vt, vr, vb = virtual
        vw, vh = max(1, vr - vl), max(1, vb - vt)
        canvas = QImage(vw, vh, QImage.Format_RGBA8888)
        canvas.fill(colour)

        loaded: Dict[str, QImage] = {}

        def picture(path: Optional[str]) -> QImage:
            if not path:
                return QImage()
            if path not in loaded:
                loaded[path] = QImage(path)
            return loaded[path]

        p = QPainter(canvas)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if mode == winapi.WALL_SPAN and pictures:
            # One picture across every monitor, as if they were one screen.
            img = picture(next((path for _d, path in pictures if path), None))
            l = min(d.rect[0] for d, _ in pictures)
            t = min(d.rect[1] for d, _ in pictures)
            r = max(d.rect[2] for d, _ in pictures)
            b = max(d.rect[3] for d, _ in pictures)
            if not img.isNull():
                _fit(p, img, QRect(l - vl, t - vt, r - l, b - t), winapi.WALL_FILL)
        else:
            for d, path in pictures:
                img = picture(path)
                if not img.isNull():
                    ml, mt, mr, mb = d.rect
                    _fit(p, img, QRect(ml - vl, mt - vt, mr - ml, mb - mt), mode)
        p.end()
        return canvas

    @staticmethod
    def compose(pictures: Sequence[Tuple[displays.Display, Optional[str]]], mode: int,
                virtual: Tuple[int, int, int, int], colour: QColor
                ) -> Tuple[QImage, Tuple[int, int]]:
        """The blurred desktop: each display's picture fitted to it the way the
        shell fits it, then blurred as one image. Returns (image, origin)."""
        vl, vt, vr, vb = virtual
        vw, vh = max(1, vr - vl), max(1, vb - vt)
        canvas = GlassBackdrop._canvas(pictures, mode, virtual, colour)
        small = canvas.scaled(max(1, vw // DOWNSCALE), max(1, vh // DOWNSCALE),
                              Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        small = _box_blur(small, BOX_RADIUS, BOX_PASSES)
        return (small.scaled(vw, vh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation),
                (vl, vt))

    # ------------------------------------------------------------------ #
    def paint_into(self, p: QPainter, dest, screen_x: int, screen_y: int) -> bool:
        """Paint the slice of blurred desktop sitting behind this card.

        ``dest`` is in widget coordinates; ``screen_x/y`` is where that rect
        lives on the virtual screen. Returns False if there is nothing to draw
        and the caller should fall back to a flat fill.
        """
        if self.image is None:
            return False
        ox, oy = self.origin
        src = QRect(int(screen_x - ox), int(screen_y - oy),
                    int(dest.width()), int(dest.height()))
        if not src.intersects(self.image.rect()):
            return False
        p.drawImage(dest, self.image, src)
        return True
