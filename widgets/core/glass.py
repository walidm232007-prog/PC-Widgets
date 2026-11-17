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

A live wallpaper - Wallpaper Engine and the like - covers the wallpaper files
without Windows ever hearing of it (livewall.py), and the glass went on showing
a picture that wasn't on screen. While one runs, both copies are built from a
grab of it instead: on a thread, every LIVE_EVERY seconds, and only when the
grab shows a new frame.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter

from . import displays, livewall, winapi

DOWNSCALE = 14           # bigger = blurrier and cheaper
BOX_PASSES = 3
BOX_RADIUS = 2

CLEAR_SCALE = 2          # the clear copy is kept at 1/2 size...
# ...and frosted at that size by box passes, (radius, passes) for each of
# Settings' Blur choices. Light, three of radius 1, comes to about Glisse's own
# 3px blur and is the default: with none, a busy wallpaper's fine detail ran
# straight through the cards' small grey labels. More is about 6px.
FROSTS = {"none": (0, 0), "light": (1, 3), "more": (2, 4)}

# A live wallpaper (livewall.py) is grabbed this often, on a thread of its own,
# and the glass built again only when the grab shows a new frame. It trails an
# animated wallpaper by up to this much, which a blur of it hides well enough;
# more often would only cost more.
LIVE_EVERY = 15.0


def _box_blur(img: QImage, radius: int, passes: int) -> QImage:
    """Separable box blur with the edges held. Repeated passes approach a
    Gaussian.

    Each pass adds up the 2 * radius + 1 shifted copies of the padded image.
    For the small radii used here that is well under half the time a running
    sum took - 125 ms instead of 308 for the clear copy of a 3000x1920 desk -
    and the same picture to within a level, from rounding. It counts: a live
    wallpaper's clear copy is frosted again with every new frame."""
    try:
        import numpy as np
    except Exception:
        return img       # the downscale/upscale alone still reads as blurred

    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w < 3 or h < 3 or radius < 1:
        return img

    arr = np.frombuffer(img.constBits(), dtype=np.uint8,
                        count=img.bytesPerLine() * h).reshape(h, img.bytesPerLine() // 4, 4)
    buf = arr[:, :w].astype(np.float32)

    k = radius * 2 + 1
    for _ in range(max(1, passes)):
        pad = np.pad(buf, ((0, 0), (radius, radius), (0, 0)), mode="edge")
        buf = pad[:, :w].copy()
        for i in range(1, k):
            buf += pad[:, i:i + w]
        buf *= 1.0 / k

        pad = np.pad(buf, ((radius, radius), (0, 0), (0, 0)), mode="edge")
        buf = pad[:h].copy()
        for i in range(1, k):
            buf += pad[i:i + h]
        buf *= 1.0 / k

    out = np.ascontiguousarray(np.clip(buf, 0, 255).astype(np.uint8))
    # Keep a reference to the buffer on the QImage or it is freed underneath it.
    return QImage(out.data, w, h, QImage.Format_RGBA8888).copy()


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
        # Bumped whenever what liquid glass bends changes - a new desktop, a new
        # clear copy - so a card can tell its kept bend is for an old one.
        self.generation = 0
        self.liquid = True            # Settings' Glass style; False for frosted
        self.frost = "light"          # Settings' Blur, a key of FROSTS
        self.notifier = _Notifier()
        self.last_bend_ms = 0.0       # what the last bend cost, its setup aside
        self._lock = threading.Lock()
        self._job = 0
        self._built = threading.Event()
        self._last = None             # what the last refresh composed, to rebuild from
        # A live wallpaper being followed: its layer, or 0. The glass shows it
        # from its first good grab (showing_live); until then, and once it
        # quits, the wallpaper files.
        self.live = 0
        self.showing_live = False
        self._live_job = 0
        self._live_wake: Optional[threading.Event] = None
        self._live_mark: Optional[bytes] = None    # the last frame's fingerprint
        self._live_virtual = None                  # the virtual screen it was placed on

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
        """Rebuild if a wallpaper or the display layout changed.

        While a live wallpaper is on the glass, its thread keeps the glass
        current: a forced refresh, or a new display layout, only asks it for a
        fresh grab now."""
        if self.showing_live:
            if force or winapi.virtual_screen() != self._live_virtual:
                self._wake_live()
            return False
        sig, mode, pics = self._read()
        if not force and sig == self._signature and self.image is not None:
            return False
        r, g, b = winapi.desktop_colour()
        virtual = winapi.virtual_screen()
        image, origin = self.compose(pics, mode, virtual, QColor(r, g, b))
        with self._lock:
            if self.showing_live:
                return False           # a live wallpaper's frame landed meanwhile
            self._signature = sig
            self.tint = QColor(r, g, b)
            self.image, self.origin = image, origin
            self.generation += 1
            self._last = (pics, mode, virtual, QColor(r, g, b))
        if self.liquid:
            self._start_clear(*self._last)
        else:
            self._drop_clear()
        return True

    # ------------------------------------------------------------------ #
    #  The clear copy, for liquid glass
    # ------------------------------------------------------------------ #
    @property
    def ready(self) -> bool:
        """Whether cards can bend: liquid glass chosen, and the clear copy
        built for this desktop."""
        return self.liquid and self.clear is not None

    def set_liquid(self, on: bool) -> None:
        """Settings' Glass style. Frosted drops the clear copy (4 MB, and the
        build after every wallpaper change); liquid builds it again, frosted
        until it lands."""
        on = bool(on)
        if on == self.liquid:
            return
        self.liquid = on
        if self.showing_live:
            if not on:
                with self._lock:
                    self.clear = None
        elif on and self._last is not None:
            self._start_clear(*self._last)
        elif not on:
            self._drop_clear()
        if self.live:
            self._wake_live()          # its next frame comes with or without the copy

    def set_frost(self, level: str) -> None:
        """Settings' Blur. The clear copy is built again with it, and the old
        one stays in use until the new one lands, so the cards never flash
        frosted while the setting changes."""
        level = level if level in FROSTS else "light"
        if level == self.frost:
            return
        self.frost = level
        if self.liquid and self._last is not None and not self.showing_live:
            self._start_clear(*self._last, keep=True)
        if self.live:
            self._wake_live()

    def _drop_clear(self) -> None:
        with self._lock:
            self._job += 1             # and any build still running is ignored
            self.clear = None
            self._built.set()          # nothing coming: don't keep anyone waiting

    def _start_clear(self, pictures, mode: int, virtual, colour: QColor,
                     keep: bool = False) -> None:
        frost = FROSTS.get(self.frost, FROSTS["light"])
        with self._lock:
            if self.showing_live:
                return                 # the live wallpaper's thread builds it
            self._job += 1
            job = self._job
            if not keep:
                self.clear = None      # the old one shows the old wallpaper
            self._built.clear()

        def build() -> None:
            try:
                planes, origin = self.compose_clear(pictures, mode, virtual, colour, frost)
            except Exception:
                planes = None          # no numpy, or a picture that won't load
            with self._lock:
                if job != self._job or self.showing_live:
                    return             # the desktop changed again meanwhile
                if planes is not None:
                    self.clear, self.clear_origin = planes, origin
                    self.generation += 1
                self._built.set()
            if planes is not None:
                self.notifier.ready.emit()

        threading.Thread(target=build, name="glass-clear", daemon=True).start()

    def wait_clear(self, timeout: float = 15.0) -> bool:
        """Block until the clear copy is built (or failed to be). For the self
        test and offscreen renders; the board itself never waits."""
        self._built.wait(timeout)
        return self.ready

    # ------------------------------------------------------------------ #
    #  Live wallpapers (livewall.py)
    # ------------------------------------------------------------------ #
    def check_live(self) -> bool:
        """Start following a live wallpaper when one starts, and stop when it
        quits. Cheap: the board asks every few seconds. True when the glass
        went back to the wallpaper files and everything glass should repaint;
        a live frame lands through ``notifier.ready`` instead."""
        try:
            layer = livewall.find_layer()
        except Exception:
            layer = 0
        if layer == self.live:
            return False
        if layer:
            self._start_live(layer)
            return False
        showing = self.showing_live
        self.stop_live()
        return self.refresh(force=True) if showing else False

    def stop_live(self) -> None:
        """Stop following the live wallpaper. The glass keeps its last frame
        until refresh() builds it from the files again."""
        with self._lock:
            self._live_job += 1
            if self._live_wake is not None:
                self._live_wake.set()          # its thread wakes and quits
            self.live, self.showing_live, self._live_wake = 0, False, None
            self._built.set()

    def _start_live(self, layer: int) -> None:
        wake = threading.Event()
        with self._lock:
            self._live_job += 1
            job = self._live_job
            if self._live_wake is not None:
                self._live_wake.set()          # an old layer's thread quits
            self.live, self._live_wake, self._live_mark = layer, wake, None
            self._built.clear()
        threading.Thread(target=self._follow, args=(layer, job, wake),
                         name="glass-live", daemon=True).start()

    def _wake_live(self) -> None:
        """A grab now, built into glass even if the frame hasn't changed."""
        with self._lock:
            self._live_mark = None
            if self._live_wake is not None:
                self._live_wake.set()

    def _follow(self, layer: int, job: int, wake: threading.Event) -> None:
        """The live wallpaper's thread: a grab now, then every LIVE_EVERY
        seconds or when woken. None at all while a full-screen window has the
        focus - a game covers the desktop, glass and all."""
        while job == self._live_job and livewall.alive(layer):
            again = False
            try:
                if winapi.fullscreen_monitor() is None:
                    again = self._live_once(layer, job)
            except Exception:
                pass
            with self._lock:
                if job == self._live_job:
                    self._built.set()          # tried: wait_clear needn't wait on
            if not again:
                wake.wait(LIVE_EVERY)
            wake.clear()

    def _live_once(self, layer: int, job: int) -> bool:
        """One grab, built into both copies when it shows a new frame. True
        to go again straight away: Glass style or Blur changed while it built."""
        got = livewall.grab(layer)
        if got is None:
            return False
        img, at = got
        mark = livewall.fingerprint(img)
        if livewall.blank(mark):
            return False                       # it drew nothing: keep what's shown
        with self._lock:
            liquid, frost, last = self.liquid, self.frost, self._live_mark
        if not livewall.differs(mark, last):
            return False                       # a still wallpaper, or a paused one
        r, g, b = winapi.desktop_colour()
        colour = QColor(r, g, b)
        virtual = winapi.virtual_screen()
        canvas = self._place(img, at, virtual, colour)
        image, origin = self._blurred(canvas, virtual)
        planes = None
        if liquid:
            try:
                planes = self._planes(canvas, virtual, FROSTS.get(frost, FROSTS["light"]))[0]
            except Exception:
                planes = None                  # no numpy: the cards stay frosted
        with self._lock:
            if job != self._live_job:
                return False                   # stopped while it built
            fits = not self.liquid or (self.liquid, self.frost) == (liquid, frost)
            self.image, self.origin, self.tint = image, origin, colour
            if fits:
                self.clear = planes if self.liquid else None
                self.clear_origin = origin
            self._live_mark = mark if fits else None
            self._live_virtual = virtual
            self.showing_live = True
            self._job += 1                     # a build from the files still running is dropped
            self.generation += 1
        self.notifier.ready.emit()
        return not fits

    @staticmethod
    def _place(img: QImage, at: Tuple[int, int], virtual: Tuple[int, int, int, int],
               colour: QColor) -> QImage:
        """A grab where it sits on the virtual screen, on the desktop colour
        wherever it doesn't reach. The wallpaper layer spans the whole virtual
        screen, so that is normally the grab itself."""
        vl, vt, vr, vb = virtual
        vw, vh = max(1, vr - vl), max(1, vb - vt)
        if (at[0], at[1]) == (vl, vt) and (img.width(), img.height()) == (vw, vh):
            return img
        canvas = QImage(vw, vh, QImage.Format_RGB32)
        canvas.fill(colour)
        p = QPainter(canvas)
        p.drawImage(at[0] - vl, at[1] - vt, img)
        p.end()
        return canvas

    @staticmethod
    def compose_clear(pictures: Sequence[Tuple[displays.Display, Optional[str]]], mode: int,
                      virtual: Tuple[int, int, int, int], colour: QColor,
                      frost: Tuple[int, int] = FROSTS["light"]):
        """The desktop for liquid glass: fitted like the blurred one, then
        halved and frosted by ``frost``, (box radius, passes) - by default
        about as much as Glisse's 3px blur frosts the web. Returns ((3, H, W)
        uint8 planes, origin)."""
        return GlassBackdrop._planes(GlassBackdrop._canvas(pictures, mode, virtual, colour),
                                     virtual, frost)

    @staticmethod
    def _planes(canvas: QImage, virtual: Tuple[int, int, int, int],
                frost: Tuple[int, int]):
        """The desktop's ``canvas``, as big as the virtual screen, made into
        liquid glass's clear copy: halved, frosted, and split into red, green
        and blue planes. Returns (planes, origin)."""
        import numpy as np

        small = canvas.scaled(max(2, canvas.width() // CLEAR_SCALE),
                              max(2, canvas.height() // CLEAR_SCALE),
                              Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        radius, passes = frost
        if radius and passes:
            small = _box_blur(small, radius, passes)
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
        return GlassBackdrop._blurred(GlassBackdrop._canvas(pictures, mode, virtual, colour),
                                      virtual)

    @staticmethod
    def _blurred(canvas: QImage, virtual: Tuple[int, int, int, int]
                 ) -> Tuple[QImage, Tuple[int, int]]:
        """The desktop's ``canvas``, as big as the virtual screen, blurred.
        Returns (image, origin)."""
        vl, vt, vr, vb = virtual
        vw, vh = max(1, vr - vl), max(1, vb - vt)
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
