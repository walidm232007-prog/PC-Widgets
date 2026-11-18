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
grab of it instead, on a thread, every LIVE_EVERY seconds - and only behind the
cards it moved behind (``regions``, ``changed_since``), so each card bends and
fades again only when what is behind it changed.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
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

# A live wallpaper (livewall.py) is looked at this often, on a thread of its
# own, and the glass behind a card built again only where the look finds
# something new - which the card then fades into. While it keeps finding
# something, it looks half as often: a wallpaper that starts moving is caught
# within LIVE_EVERY, and one that never stops costs half as much.
LIVE_EVERY = 3.0
LIVE_FULL_EVERY = 60.0   # s of motion between whole clear copies, for glass behind no card
REGION_PAD = 16          # px round a card its thumbnail takes in
REGION_TOLERANCE = 2.0   # mean change (0..255) in that thumbnail that counts as moved
PATCH_MARGIN = 24        # px round a card rebuilt with it: further than the frost reaches

# Live glass (Settings' "Live glass", on by default) follows a live wallpaper
# as it moves: a frame every LIVE_FRAME seconds while what lies behind the
# cards moves, read back and built only behind the cards in view, with the
# building after each grab kept under LIVE_DUTY of the time (the grab itself is
# mostly waiting on the compositor). Still, it looks every LIVE_STILL seconds
# to notice it move again, and every LIVE_RESTING once it has rested a while.
# Off, the glass updates every LIVE_EVERY seconds and fades, as above.
LIVE_FRAME = 0.1
LIVE_STILL = 0.5
LIVE_RESTING = 1.5
LIVE_DUTY = 0.3
LIVE_WHOLE_EVERY = 3.0   # s of motion between whole looks: the blurred copy, glass behind no card
FROST_MARGIN = DOWNSCALE * (BOX_RADIUS * BOX_PASSES + 2)   # px a frosted slice's blur reaches


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


def _overlap(a, b) -> bool:
    """Whether two (x, y, w, h) rects overlap."""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _aligned(rect, virtual, reach: int) -> Tuple[int, int, int, int]:
    """``rect`` with ``reach`` px round it, its edges moved out to even pixels
    of the virtual screen, so its half-size planes line up with the clear
    copy's."""
    x, y, w, h = rect
    vl, vt, s = virtual[0], virtual[1], CLEAR_SCALE
    x0, y0 = vl + (x - vl - reach) // s * s, vt + (y - vt - reach) // s * s
    x1, y1 = vl - (-(x - vl + w + reach) // s) * s, vt - (-(y - vt + h + reach) // s) * s
    return (x0, y0, x1 - x0, y1 - y0)


def _hidden(info) -> set:
    """The regions in ``info`` (region -> (its window, radius)) that a window
    above theirs covers entirely, so live glass needn't build what nobody can
    see. Only an ordinary window counts - visible, uncloaked, captioned or
    maximised, not layered, click-through or a tool window - since an
    invisible overlay over the whole screen (a game launcher's, a graphics
    driver's) would otherwise freeze every card. One a window and a half
    cover still counts as in view: that only costs a frame."""
    wanted = {int(hwnd): rect for rect, (hwnd, _r) in info.items() if hwnd}
    if not wanted:
        return set()
    try:
        import ctypes
        import ctypes.wintypes as wt
        user, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    except Exception:
        return set()
    above, hidden, left = [], set(), set(wanted)
    box, cloak = wt.RECT(), wt.DWORD()
    cls = ctypes.create_unicode_buffer(64)
    style_ex_skip = 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000   # LAYERED, TRANSPARENT, TOOLWINDOW, NOACTIVATE

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        h = int(hwnd or 0)
        if h in wanted:
            x, y, w, hh = wanted[h]
            if any(l <= x and t <= y and r >= x + w and b >= y + hh for l, t, r, b in above):
                hidden.add(wanted[h])
            left.discard(h)
            return bool(left)                  # stop once the lowest card is reached
        if not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
            return True
        if user.GetWindowLongW(hwnd, -20) & style_ex_skip:
            return True
        if user.GetWindowLongW(hwnd, -16) & 0x00C00000 != 0x00C00000 and not user.IsZoomed(hwnd):
            return True                        # no caption and not maximised
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloak), ctypes.sizeof(cloak))
        if cloak.value:
            return True
        user.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return True
        if user.GetWindowRect(hwnd, ctypes.byref(box)):
            above.append((box.left, box.top, box.right, box.bottom))
        return True

    try:
        user.EnumWindows(visit, 0)
    except Exception:
        return set()
    return hidden


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
        # A new wallpaper's blurred copy, waiting to land with its clear copy
        # (refresh): (image, origin, tint), or None.
        self._pending = None
        # (generation, regions it changed - or None, everywhere) for the last
        # few changes, so a card can tell whether one reached it.
        self._history = deque(maxlen=32)
        # Where glass shows, (x, y, w, h) on the virtual screen: each card, and
        # the dock. Set by the board; a live wallpaper is judged there.
        self.regions: Tuple[Tuple[int, int, int, int], ...] = ()
        # For each region, (its window, the corner radius of a card's pane or
        # None): live glass skips one hidden behind a window, and bends a
        # card's pane ahead.
        self.region_info: Dict[Tuple, Tuple[int, Optional[float]]] = {}
        self.live_glass = True        # Settings' Live glass
        # Live glass's work, done ahead for the cards: bent panes, by (x, y,
        # w, h, radius), and frosted slices, by region; and the panes last bent
        # on the coarse grid, to be bent in full once the wallpaper rests.
        self._ahead: Dict[Tuple, QImage] = {}
        self._frost_ahead: Dict[Tuple, QImage] = {}
        self._live_coarse: set = set()
        self._live_work = 0.0         # s the last frame spent building, the grab aside
        self._live_moving = False     # whether the last look found something new
        self._live_look_at = 0.0      # when the last whole look was
        # A live wallpaper being followed: its layer, or 0. The glass shows it
        # from its first good grab (showing_live); until then, and once it
        # quits, the wallpaper files.
        self.live = 0
        self.showing_live = False
        self._live_job = 0
        self._live_wake: Optional[threading.Event] = None
        self._live_mark: Optional[bytes] = None    # the last frame looked at; None: build it whole
        self._live_virtual = None                  # the virtual screen it was placed on
        self._live_whole_at = 0.0                  # when the clear copy was last built whole
        self._region_marks: Dict[Tuple, bytes] = {}   # each region's thumbnail, as built

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
            self._last = (pics, mode, virtual, QColor(r, g, b))
            # With liquid glass a new desktop lands whole, blurred and clear
            # copies together once the clear one is built: the cards keep the
            # old glass meanwhile and fade from it, where they went frosted
            # for a moment first. Only a first desktop shows straight away.
            now = self.image is None or not self.liquid
            if now:
                self.tint = QColor(r, g, b)
                self.image, self.origin = image, origin
                self._bump()
        if self.liquid:
            self._start_clear(*self._last, keep=True,
                              blurred=None if now else (image, origin, QColor(r, g, b)))
        else:
            self._drop_clear()
        return True

    def _bump(self, regions=None, fade: bool = True) -> None:
        """A new generation, under the lock: what changed is ``regions``,
        (x, y, w, h) each, or None for everywhere; ``fade``, whether glass
        should fade into it (not a live glass frame). Work done ahead for the
        glass it touched is dropped."""
        self.generation += 1
        self._history.append((self.generation, None if regions is None else tuple(regions), fade))
        if regions is None:
            self._ahead.clear()
            self._frost_ahead.clear()
            return
        for kept in (self._ahead, self._frost_ahead):
            for key in [k for k in kept if any(_overlap(k[:4], r) for r in regions)]:
                del kept[key]

    def changed_since(self, generation, rect) -> bool:
        """Whether the glass behind ``rect`` (x, y, w, h on the virtual screen)
        can have changed since ``generation``. A card asks before it bends its
        glass again or fades: a live wallpaper moving behind one card changes
        the glass behind that one, and the others keep theirs."""
        return self._since(generation, rect, False)

    def fade_since(self, generation, rect) -> bool:
        """Whether a change since ``generation`` behind ``rect`` should be faded
        into: anything but live glass's frames, which follow each other too
        quickly for a fade."""
        return self._since(generation, rect, True)

    def _since(self, generation, rect, fading: bool) -> bool:
        if not isinstance(generation, int):
            return True
        with self._lock:
            if generation >= self.generation:
                return False
            later = [(regions, fade) for gen, regions, fade in self._history if gen > generation]
            if len(later) < self.generation - generation:
                return True            # further back than is remembered
        for regions, fade in later:
            if fading and not fade:
                continue
            if regions is None or any(_overlap(rect, r) for r in regions):
                return True
        return False

    def set_live_glass(self, on: bool) -> None:
        """Settings' Live glass: follow a live wallpaper as it moves, or update
        every LIVE_EVERY seconds and fade."""
        self.live_glass = bool(on)
        with self._lock:
            self._live_coarse.clear()
        self._wake_live()

    def frost_ahead(self, sx: int, sy: int, w: int, h: int) -> Optional[QImage]:
        """Live glass's frosted slice for the card whose glass is at ``sx, sy``
        and ``w`` by ``h``, built from its latest frame, or None."""
        with self._lock:
            return self._frost_ahead.get((int(sx), int(sy), int(w), int(h)))

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
        landed = False
        with self._lock:
            self._job += 1             # and any build still running is ignored
            self.clear = None
            if self._pending is not None:
                # A new wallpaper was waiting on the clear copy: show it now.
                self.image, self.origin, self.tint = self._pending
                self._pending = None
                self._bump()
                landed = True
            self._built.set()          # nothing coming: don't keep anyone waiting
        if landed:
            self.notifier.ready.emit()

    def _start_clear(self, pictures, mode: int, virtual, colour: QColor,
                     keep: bool = False, blurred=None) -> None:
        """Build the clear copy on a thread. ``blurred``, (image, origin,
        tint), is a new wallpaper's blurred copy, landing with it."""
        frost = FROSTS.get(self.frost, FROSTS["light"])
        with self._lock:
            if self.showing_live:
                return                 # the live wallpaper's thread builds it
            self._job += 1
            job = self._job
            if blurred is not None:
                self._pending = blurred
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
                landed = planes is not None or self._pending is not None
                if self._pending is not None:
                    self.image, self.origin, self.tint = self._pending
                    self._pending = None
                if planes is not None:
                    self.clear, self.clear_origin = planes, origin
                if landed:
                    self._bump()
                self._built.set()
            if landed:
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
    def check_live(self, now: bool = False) -> bool:
        """Start following a live wallpaper when one starts, and stop when it
        quits. Cheap: the board asks every few seconds. True when the glass
        went back to the wallpaper files and everything glass should repaint;
        a live frame lands through ``notifier.ready`` instead.

        ``now``, as the board starts: the first frame is built here and then,
        in about 0.3 s, so the cards' first paint shows the live wallpaper -
        not the files' picture for a moment, then a switch."""
        try:
            layer = livewall.find_layer()
        except Exception:
            layer = 0
        if layer == self.live:
            return False
        if layer:
            job, wake = self._prepare_live(layer)
            if now:
                try:
                    self._live_once(layer, job)
                except Exception:
                    pass
                with self._lock:
                    if job == self._live_job:
                        self._built.set()
            threading.Thread(target=self._follow,
                             args=(layer, job, wake, now and self.showing_live),
                             name="glass-live", daemon=True).start()
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
            self._region_marks = {}
            self._built.set()

    def _prepare_live(self, layer: int):
        """Ready to follow ``layer``: (its job, the event that wakes its thread)."""
        wake = threading.Event()
        with self._lock:
            self._live_job += 1
            if self._live_wake is not None:
                self._live_wake.set()          # an old layer's thread quits
            self.live, self._live_wake, self._live_mark = layer, wake, None
            self._region_marks = {}
            self._built.clear()
            return self._live_job, wake

    def _wake_live(self) -> None:
        """A grab now, built into glass even if the frame hasn't changed."""
        with self._lock:
            self._live_mark = None
            if self._live_wake is not None:
                self._live_wake.set()

    def _follow(self, layer: int, job: int, wake: threading.Event,
                rested: bool = False) -> None:
        """The live wallpaper's thread: a look every LIVE_EVERY seconds, twice
        that while each look finds something new, and sooner when woken. None
        at all while a full-screen window has the focus - a game covers the
        desktop, glass and all. ``rested``: the first frame is already built,
        so the first look waits too."""
        wait = (LIVE_FRAME if self.live_glass else LIVE_EVERY) if rested else 0.0
        still = 0
        try:
            while True:
                if wait:
                    wake.wait(wait)
                wake.clear()
                if job != self._live_job or not livewall.alive(layer):
                    return
                live = self.live_glass
                built = again = False
                started = time.perf_counter()
                try:
                    if winapi.fullscreen_monitor() is not None:
                        pass
                    elif not live or self._whole_look_due(still):
                        built, again = self._live_once(layer, job, keep=True)
                        self._live_look_at = time.monotonic()
                    else:
                        built, again = self._live_frame(layer, job)
                except Exception:
                    pass
                with self._lock:
                    if job == self._live_job:
                        self._built.set()      # tried: wait_clear needn't wait on
                took = time.perf_counter() - started
                still = 0 if built else still + 1
                self._live_moving = built
                if again:
                    wait = 0.0
                elif not live:
                    wait = LIVE_EVERY * (2 if built else 1)
                elif built:
                    # Paced as the dock's capture is: a frame every LIVE_FRAME,
                    # but resting at least as long as LIVE_DUTY asks after the
                    # building - the grab before it is mostly waiting.
                    work = self._live_work
                    wait = max(LIVE_FRAME - took, work * (1 - LIVE_DUTY) / LIVE_DUTY)
                else:
                    wait = LIVE_STILL if still < 10 else LIVE_RESTING
        finally:
            livewall.release()                 # the bitmaps its grabs kept

    def _whole_look_due(self, still: int) -> bool:
        """Whether live glass's next look should be a whole one: the first, one
        after a setting changed, and every LIVE_WHOLE_EVERY seconds while it
        moves, for the blurred copy and glass behind no card."""
        if self._live_mark is None or not self.showing_live:
            return True
        return self._live_moving and time.monotonic() - self._live_look_at >= LIVE_WHOLE_EVERY

    def _live_once(self, layer: int, job: int, keep: bool = False) -> Tuple[bool, bool]:
        """One look at the live wallpaper. Returns (whether glass was built
        from it, whether to look again straight away - Glass style or Blur
        changed while it built).

        The first look builds both copies whole. After that, a 16x16 thumbnail
        of what lies behind each of ``regions`` says which moved, and only the
        glass behind those is built again: the blurred copy whole, since that
        takes 20 ms, but the clear copy only round them - a millisecond or two
        each, where the whole desk takes a fifth of a second. While it keeps
        moving, the clear copy is built whole every LIVE_FULL_EVERY seconds
        too, for glass that is behind no card: the dock's menus, a card that
        has just been moved. ``keep``: grab into the bitmap kept from the
        last look (livewall.grab), as the thread does."""
        got = livewall.grab(layer, keep=keep)
        if got is None:
            return False, False
        img, at = got
        mark = livewall.fingerprint(img)
        if livewall.blank(mark):
            return False, False                # it drew nothing: keep what's shown
        with self._lock:
            liquid, frost, last = self.liquid, self.frost, self._live_mark
            regions, marks = tuple(self.regions), dict(self._region_marks)
            clear, showing, placed = self.clear, self.showing_live, self._live_virtual
        if last is not None and mark == last:
            return False, False                # the same frame: paused, or still
        r, g, b = winapi.desktop_colour()
        colour = QColor(r, g, b)
        virtual = winapi.virtual_screen()
        canvas = self._place(img, at, virtual, colour)
        vl, vt, vr, vb = virtual
        if not regions:
            regions = ((vl, vt, vr - vl, vb - vt),)    # no board: the desk as one
        whole = last is None or not showing or placed != virtual
        moved = []
        if not whole:
            for rect in regions:
                seen = self._region_mark(canvas, virtual, rect)
                if seen is not None and livewall.moved(marks.get(rect), seen, REGION_TOLERANCE):
                    moved.append(rect)
                    marks[rect] = seen
            if not moved:
                with self._lock:
                    if job == self._live_job:
                        self._live_mark = mark
                if self.live_glass:
                    # Live glass's frames keep the glass behind the cards
                    # current, so this finds nothing new there - but the
                    # blurred copy is redone for glass that isn't a card's,
                    # the app's sidebar: no new generation, nothing to bend.
                    canvas = canvas.convertToFormat(QImage.Format_RGB32)
                    image, origin = self._blurred(canvas, virtual)
                    with self._lock:
                        if job == self._live_job:
                            self.image, self.origin = image, origin
                    self.notifier.ready.emit()
                return False, False            # moving, but behind no glass
            whole = time.monotonic() - self._live_whole_at >= LIVE_FULL_EVERY
        if whole:
            marks = {rect: self._region_mark(canvas, virtual, rect) for rect in regions}
        marks = {rect: marks[rect] for rect in regions if marks.get(rect) is not None}
        # Built from a copy in the format the scaling works in: once here,
        # rather than at every look, and a kept grab is overwritten by the next.
        canvas = canvas.convertToFormat(QImage.Format_RGB32)
        image, origin = self._blurred(canvas, virtual)
        planes = None
        if liquid:
            frosting = FROSTS.get(frost, FROSTS["light"])
            try:
                fits_whole = (clear is not None and clear.shape[1] == canvas.height() // CLEAR_SCALE
                              and clear.shape[2] == canvas.width() // CLEAR_SCALE)
                if whole or not fits_whole:
                    planes, whole = self._planes(canvas, virtual, frosting)[0], True
                else:
                    planes = self._patch(clear, canvas, virtual, moved, frosting)
            except Exception:
                planes = None                  # no numpy: the cards stay frosted
        with self._lock:
            if job != self._live_job:
                return False, False            # stopped while it built
            fits = not self.liquid or (self.liquid, self.frost) == (liquid, frost)
            self.image, self.origin, self.tint = image, origin, colour
            if fits:
                self.clear = planes if self.liquid else None
                self.clear_origin = origin
            self._live_mark = mark if fits else None
            self._region_marks = marks if fits else {}
            self._live_virtual = virtual
            self.showing_live = True
            self._pending = None
            self._job += 1                     # a build from the files still running is dropped
            # Live glass fades only into a first frame or a setting's change;
            # its looks along the way are frames like the rest.
            self._bump(None if whole else moved,
                       fade=not self.live_glass or last is None or not showing)
            if whole and fits:
                self._live_whole_at = time.monotonic()
        self.notifier.ready.emit()
        return True, not fits

    def _live_frame(self, layer: int, job: int) -> Tuple[bool, bool]:
        """One frame of live glass, returning as _live_once does. The layer is
        grabbed, but read back only behind the glass in view - ``regions`` not
        hidden behind a window - and built again only where that moved. Liquid
        glass gets its clear copy patched there and each card's pane bent
        ahead, here, on the grid a card uses while dragged, so a card's repaint
        is only drawing; frosted glass gets each card's slice blurred. Nothing
        fades: the frames follow each other too quickly. Once it rests, the
        coarse bends are bent again in full, and those fade in."""
        with self._lock:
            liquid, frost = self.liquid, self.frost
            regions, info = tuple(self.regions), dict(self.region_info)
            marks, clear, origin = dict(self._region_marks), self.clear, self.clear_origin
            coarse, placed = set(self._live_coarse), self._live_virtual
        virtual = winapi.virtual_screen()
        if placed != virtual or (liquid and clear is None) or not regions:
            return self._live_once(layer, job, keep=True)
        hidden = _hidden(info)
        view = [r for r in regions if r not in hidden]
        if not view:
            self._live_work = 0.0
            return False, False
        asks = [_aligned(r, virtual, PATCH_MARGIN if liquid else FROST_MARGIN) for r in view]
        got = livewall.grab_parts(layer, asks, keep=True)
        if got is None:
            return False, False
        started = time.perf_counter()
        moved, pieces = [], []
        for rect, part in zip(view, got[0]):
            if part is None:
                continue
            seen = self._part_mark(part[1], part[0], rect)
            if seen is not None and livewall.moved(marks.get(rect), seen, REGION_TOLERANCE):
                moved.append(rect)
                marks[rect] = seen
                pieces.append((rect, part[0], part[1]))
        frosting = FROSTS.get(frost, FROSTS["light"])
        if not moved:
            sharp = {}
            if liquid and coarse and clear is not None:
                sharp = self._bend_panes(clear, origin, coarse, info, None)
            if sharp:
                with self._lock:
                    if job != self._live_job or (self.liquid, self.frost) != (liquid, frost):
                        return False, False
                    self._bump([k[:4] for k in sharp])
                    self._ahead.update(sharp)
                    self._live_coarse.clear()
                self.notifier.ready.emit()
            self._live_work = time.perf_counter() - started
            return False, False
        planes, ahead, slices = None, {}, {}
        try:
            if liquid:
                planes = clear.copy()
                for _rect, prect, img in pieces:
                    self._patch_part(planes, img.convertToFormat(QImage.Format_RGB32), prect,
                                     virtual, frosting)
                from .card import DRAG_STEP
                ahead = self._bend_panes(planes, origin, moved, info, DRAG_STEP)
            else:
                for rect, prect, img in pieces:
                    slices[rect] = self._frost_slice(img, prect, rect)
        except Exception:
            return False, False                # no numpy: the cards stay as they are
        with self._lock:
            if job != self._live_job:
                return False, False
            if (self.liquid, self.frost) != (liquid, frost):
                self._live_mark = None         # a setting changed meanwhile: a whole look next
                return True, True
            if planes is not None:
                self.clear = planes
            self._region_marks.update({r: marks[r] for r in moved})
            self._bump(moved, fade=False)
            self._ahead.update(ahead)
            self._frost_ahead.update(slices)
            self._live_coarse.update(ahead)
        self.notifier.ready.emit()
        self._live_work = time.perf_counter() - started
        return True, False

    @staticmethod
    def _part_mark(img: QImage, prect: Tuple[int, int, int, int],
                   rect: Tuple[int, int, int, int]) -> Optional[bytes]:
        """_region_mark's thumbnail of ``rect``, from a patch read back round
        it at ``prect``: the same pixels, so the two compare."""
        x, y, w, h = rect
        area = QRect(x - prect[0] - REGION_PAD, y - prect[1] - REGION_PAD,
                     w + 2 * REGION_PAD, h + 2 * REGION_PAD).intersected(img.rect())
        if area.width() < 2 or area.height() < 2:
            return None
        small = img.copy(area).scaled(16, 16, Qt.IgnoreAspectRatio,
                                      Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB888)
        return bytes(small.constBits())[: 16 * 16 * 3]

    @staticmethod
    def _patch_part(planes, img: QImage, prect: Tuple[int, int, int, int],
                    virtual: Tuple[int, int, int, int], frost: Tuple[int, int]) -> None:
        """Write a patch, read back at ``prect`` (from an even pixel of the
        virtual screen, PATCH_MARGIN round its card), into the clear copy
        ``planes``, in place."""
        part, _ = GlassBackdrop._planes(img, (0, 0, prect[2], prect[3]), frost)
        py, px = (prect[1] - virtual[1]) // CLEAR_SCALE, (prect[0] - virtual[0]) // CLEAR_SCALE
        ph, pw = min(part.shape[1], planes.shape[1] - py), min(part.shape[2], planes.shape[2] - px)
        if ph > 0 and pw > 0:
            planes[:, py:py + ph, px:px + pw] = part[:, :ph, :pw]

    @staticmethod
    def _frost_slice(img: QImage, prect: Tuple[int, int, int, int],
                     rect: Tuple[int, int, int, int]) -> QImage:
        """A card's frosted slice, blurred as the whole desk is, from a patch
        read back FROST_MARGIN round it."""
        pw, ph = img.width(), img.height()
        small = img.scaled(max(1, pw // DOWNSCALE), max(1, ph // DOWNSCALE),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        small = _box_blur(small, BOX_RADIUS, BOX_PASSES)
        big = small.scaled(pw, ph, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return big.copy(rect[0] - prect[0], rect[1] - prect[1], rect[2], rect[3])

    @staticmethod
    def _bend_panes(planes, origin, rects, info, step) -> Dict[Tuple, QImage]:
        """Each card's pane among ``rects`` bent from ``planes``, keyed as
        bend() looks for it, on a ``step`` px grid (lens.STEP if None). The
        dock bends its own."""
        from . import lens
        out = {}
        for rect in rects:
            x, y, w, h = rect[:4]
            radius = info.get((x, y, w, h), (0, None))[1]
            if radius is not None:
                out[(x, y, w, h, float(radius))] = lens.bend(planes, CLEAR_SCALE, origin, x, y, w, h,
                                                             radius, int(step or lens.STEP))
        return out

    @staticmethod
    def _region_mark(canvas: QImage, virtual: Tuple[int, int, int, int],
                     rect: Tuple[int, int, int, int]) -> Optional[bytes]:
        """A 16x16 thumbnail of what lies behind ``rect`` (x, y, w, h on the
        virtual screen) and REGION_PAD round it - averaged, not sampled, so a
        scatter of sparkles doesn't count as the wallpaper moving. None off
        the desk."""
        x, y, w, h = rect
        area = QRect(x - virtual[0] - REGION_PAD, y - virtual[1] - REGION_PAD,
                     w + 2 * REGION_PAD, h + 2 * REGION_PAD).intersected(canvas.rect())
        if area.width() < 2 or area.height() < 2:
            return None
        part = canvas if area == canvas.rect() else canvas.copy(area)
        small = part.scaled(16, 16, Qt.IgnoreAspectRatio,
                            Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB888)
        return bytes(small.constBits())[: 16 * 16 * 3]

    @staticmethod
    def _patch(planes, canvas: QImage, virtual: Tuple[int, int, int, int], regions,
               frost: Tuple[int, int]):
        """The clear copy ``planes`` with the glass behind each of ``regions``
        built again from ``canvas``, a grab as big as the virtual screen. On a
        copy: the old planes are left alone, since a card may be bending them.

        Each is built with PATCH_MARGIN px round it, further than the frost
        reaches, so inside it comes out as a whole-desk build would, and from
        an even pixel, so its halves line up with the whole copy's."""
        out = planes.copy()
        s = CLEAR_SCALE
        vl, vt = virtual[0], virtual[1]
        cw, ch = canvas.width() // s * s, canvas.height() // s * s
        for x, y, w, h in regions:
            x0 = max(0, (x - vl - PATCH_MARGIN) // s * s)
            y0 = max(0, (y - vt - PATCH_MARGIN) // s * s)
            x1 = min(cw, -(-(x - vl + w + PATCH_MARGIN) // s) * s)
            y1 = min(ch, -(-(y - vt + h + PATCH_MARGIN) // s) * s)
            if x1 - x0 < 4 * s or y1 - y0 < 4 * s:
                continue
            part, _ = GlassBackdrop._planes(canvas.copy(x0, y0, x1 - x0, y1 - y0),
                                            (0, 0, x1 - x0, y1 - y0), frost)
            py, px = y0 // s, x0 // s
            ph, pw = min(part.shape[1], out.shape[1] - py), min(part.shape[2], out.shape[2] - px)
            out[:, py:py + ph, px:px + pw] = part[:, :ph, :pw]
        return out

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
        w, h = int(round(w)), int(round(h))
        with self._lock:
            planes, origin = self.clear, self.clear_origin
            ahead = self._ahead.get((int(sx), int(sy), w, h, float(radius))) if step is None else None
        if ahead is not None:
            return ahead                       # bent already, by live glass's thread
        if planes is None:
            return None
        from . import lens
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
        live = self.frost_ahead(screen_x, screen_y, int(dest.width()), int(dest.height()))
        if live is not None:
            p.drawImage(dest, live)            # live glass's slice, from its latest frame
            return True
        if self.image is None:
            return False
        ox, oy = self.origin
        src = QRect(int(screen_x - ox), int(screen_y - oy),
                    int(dest.width()), int(dest.height()))
        if not src.intersects(self.image.rect()):
            return False
        p.drawImage(dest, self.image, src)
        return True
