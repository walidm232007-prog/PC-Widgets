"""
card.py
=======
The base widget card: a frameless, translucent, rounded panel that lives on the
desktop, lines itself up with its neighbours when dragged (the way macOS
desktop widgets do - see magnet.py), and knows its own size class.

Sizing follows the phone convention rather than free resizing - a widget is
small (2x2), medium (4x2) or large (4x4). Fixed sizes are what make a board of
mixed widgets look composed instead of scattered, and they mean every tile can
be designed for three known shapes rather than an infinite number.

Tiles subclass this and implement :meth:`paint_content`. The card handles the
chrome, the dragging, the menu and the persistence; the tile only draws.
"""

from __future__ import annotations

import math
import os

from typing import Dict, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QImage,
    QAction, QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen,
)
from PySide6.QtWidgets import QMenu, QWidget

from . import displays, imagefit, magnet, typography, winapi
from .palette import Palette

# --------------------------------------------------------------------------- #
#  The grid
# --------------------------------------------------------------------------- #
CELL = 78
GAP = 12
STEP = CELL + GAP        # one grid cell and its gap; layout arithmetic elsewhere

# The glide into place when a card is dropped onto an outline: quick for a
# nudge, a little longer for a real pull, never sluggish.
GLIDE_MIN_MS = 170.0
GLIDE_MAX_MS = 300.0
# The drop outline fades in when a card comes close enough to line up, and out
# when it leaves or is let go - never popping on and off under the cursor.
FADE_IN_MS = 140.0
FADE_OUT_MS = 180.0

SIZES: Dict[str, Tuple[int, int]] = {
    "small":  (2, 2),
    "medium": (4, 2),
    "large":  (4, 4),
}

# iOS sets a small widget at 158pt with a 21.5pt corner - about 14% of the short
# side, which is ~24px here. Nudged up slightly by eye; much past this and the
# continuous curve eats the straight edge of a 4x2 card and it reads as a
# lozenge rather than a tile.
RADIUS = 26.0
PAD = 18.0

# Transparent gutter around the card inside its window. This is what stops the
# corners looking cut off: without it the antialiased curve is clipped dead on
# the window boundary, and there is nowhere for a shadow to fall.
MARGIN = 18.0

SHADOW_LAYERS = 9
SHADOW_ALPHA = 34

# Liquid glass is bent again at every step of a drag, on a coarser grid than at
# rest (lens.STEP is 2 px): a quarter of the work, and detail nobody can see
# on a moving card. Where it's let go it's bent again in full. A PC whose last
# bend still took longer than BEND_BUDGET_MS shows frosted glass while the card
# moves instead.
DRAG_STEP = 4
BEND_BUDGET_MS = 12.0

# When the glass behind a card sitting still changes - a new wallpaper, a live
# one moving, the clear copy landing, a Glass setting - the card fades from the
# old glass to the new over this long instead of switching. The tint and the
# content are drawn over it as ever, so only the glass fades.
GLASS_FADE_MS = 400.0


# --------------------------------------------------------------------------- #
#  Quiet mode
# --------------------------------------------------------------------------- #
# Set when a fullscreen app takes the foreground. Painting stops; ticking does
# not, so data stays current and the board is up to date the moment the game is
# minimised.
#
# The board asks about the foreground window once for every card - polling it
# once per widget would be nine times the work for one answer - and then quiets
# the cards on the game's own display (WidgetCard.quiet): a game on one monitor
# hides nothing on another. This flag on the module quiets every card at once.
_quiet = False


def set_quiet(on: bool) -> None:
    global _quiet
    _quiet = bool(on)


def is_quiet() -> bool:
    return _quiet


# --------------------------------------------------------------------------- #
#  Fades
# --------------------------------------------------------------------------- #
class Fader:
    """A 0 → 1 ramp that repaints its widget only while it is running.

    Fades here are brief and rare - a track changing, a minute rolling over - so
    the timer starts when one begins and stops itself at the end. That is the
    whole point of the class: no tile has to raise its tick rate to own an
    animation, and nothing costs anything between fades.

    Eased rather than linear. A straight ramp between two images reads as a
    mechanical wipe; smoothstep spends longer near the ends, which is what makes
    a cross-fade look like one thing becoming another.
    """

    def __init__(self, widget: QWidget, fps: Optional[int] = 30) -> None:
        self._widget = widget
        self._value = 1.0
        self._duration = 1.0
        self._elapsed = 0.0
        self._began = None
        if fps is None:
            # Every frame the main display shows, timed by its refresh (see
            # frameclock) - the dock's choice, where everything else moves so.
            from .frameclock import Ticker
            self._step_ms = 0
            self._timer = Ticker(self._step_display)
            return
        self._step_ms = max(16, int(round(1000.0 / max(1, fps))))
        self._timer = QTimer(widget)
        self._timer.setInterval(self._step_ms)
        self._timer.timeout.connect(self._step)

    @property
    def value(self) -> float:
        """0 at the start of the fade, 1 when it has finished."""
        return self._value

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self, ms: float = 350.0) -> None:
        self._duration = max(1.0, float(ms))
        self._elapsed = 0.0
        self._began = None
        self._value = 0.0
        if not self._timer.isActive():
            self._timer.start()

    def finish(self) -> None:
        """Jump to the end. For a change that should not be animated at all."""
        self._timer.stop()
        self._value = 1.0

    def _step_display(self) -> None:
        from .frameclock import clock
        c = clock()
        if self._began is None:
            self._began = c.frame_time - c.period
        self._elapsed = (c.frame_time - self._began) * 1000.0
        self._advance()

    def _step(self) -> None:
        self._elapsed += self._step_ms
        self._advance()

    def _advance(self) -> None:
        t = min(1.0, self._elapsed / self._duration)
        self._value = t * t * (3.0 - 2.0 * t)          # smoothstep
        self._widget.update()
        if t >= 1.0:
            self._timer.stop()


def card_size(size: str) -> Tuple[int, int]:
    """Size of the visible card."""
    cols, rows = SIZES.get(size, SIZES["small"])
    return (cols * CELL + (cols - 1) * GAP, rows * CELL + (rows - 1) * GAP)


def pixel_size(size: str, extra_bottom: float = 0.0) -> Tuple[int, int]:
    """Window size: the card, its shadow gutter, and any reserved overhang.

    ``extra_bottom`` lets a tile draw *below* its card - the media player's
    transport buttons straddle the bottom edge, and without reserved room the
    window clips them halfway through.
    """
    w, h = card_size(size)
    return (int(w + MARGIN * 2), int(h + MARGIN * 2 + extra_bottom))


def squircle(rect: QRectF, radius: float) -> QPainterPath:
    """A rounded rectangle with Apple-style *continuous* corners.

    A plain rounded rect meets its straight edge at a sudden change of
    curvature, which is exactly what reads as a hard or "sharp" corner. A
    continuous corner starts curving earlier - about 1.5x the radius along each
    edge - and eases in, so the transition is invisible.
    """
    r = min(radius, min(rect.width(), rect.height()) / 2.0)
    a = min(r * 1.5286, min(rect.width(), rect.height()) / 2.0)
    c = a * 0.45          # control-point pull; tuned by eye against iOS widgets

    x1, y1 = rect.left(), rect.top()
    x2, y2 = rect.right(), rect.bottom()

    p = QPainterPath()
    p.moveTo(x1 + a, y1)
    p.lineTo(x2 - a, y1)
    p.cubicTo(x2 - c, y1, x2, y1 + c, x2, y1 + a)
    p.lineTo(x2, y2 - a)
    p.cubicTo(x2, y2 - c, x2 - c, y2, x2 - a, y2)
    p.lineTo(x1 + a, y2)
    p.cubicTo(x1 + c, y2, x1, y2 - c, x1, y2 - a)
    p.lineTo(x1, y1 + a)
    p.cubicTo(x1, y1 + c, x1 + c, y1, x1 + a, y1)
    p.closeSubpath()
    return p


class DropOutline(QWidget):
    """The rounded placeholder showing where a dragged card will settle.

    Its own little window, the size of a card's window, kept directly under
    the card being dragged. It never takes the mouse or focus - the drag
    carries on straight through it. One is shared by every card; only one
    card is ever dragged at a time.
    """

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus
                            | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowTitle("PC Widget - drop outline")
        self._card = QRectF()
        self._dark = True
        self._native = False
        # Faded rather than popped: 0 gone, 1 fully drawn.
        self._alpha = 0.0
        self._fade = None
        self._fading_out = False

    def show_for(self, card: "WidgetCard", x: int, y: int) -> None:
        if self.size() != card.size():
            self.resize(card.size())
        self._card = card.card_rect()
        self._dark = card.palette_.dark
        self.move(x, y)
        if not self.isVisible():
            self._alpha = 0.0
            self.show()
            if not self._native:
                winapi.make_desktop_window(int(self.winId()), no_activate=True)
                self._native = True
        # Re-asserted every time: the card can be restacked while it's dragged.
        winapi.place_below(int(self.winId()), int(card.winId()))
        if self._fading_out or (self._fade is None and self._alpha < 1.0):
            self._fade_to(1.0, FADE_IN_MS)
        self.update()

    def fade_out(self, ms: float = None) -> None:
        """Fade away, then hide. Safe to call when already hidden."""
        if not self.isVisible() or self._fading_out:
            return
        self._fade_to(0.0, FADE_OUT_MS if ms is None else ms)

    def hide_now(self) -> None:
        self._stop_fade()
        self._alpha = 0.0
        self.hide()

    def _stop_fade(self) -> None:
        if self._fade is not None:
            self._fade.stop()
            self._fade.deleteLater()
            self._fade = None
        self._fading_out = False

    def _fade_to(self, target: float, ms: float) -> None:
        self._stop_fade()
        start = self._alpha
        if not winapi.animations_enabled() or abs(target - start) < 0.01:
            self._alpha = target
            if target <= 0.0:
                self.hide()
            self.update()
            return
        from .frameclock import Tween
        t = Tween(self)
        t.setStartValue(start)
        t.setEndValue(target)
        # Scaled by how far there is to go, so a reversal mid-fade isn't slow.
        t.setDuration(max(40.0, ms * abs(target - start)))
        t.setEasingCurve(QEasingCurve.OutCubic if target > start else QEasingCurve.InOutQuad)

        def step(v) -> None:
            self._alpha = float(v)
            self.update()

        def done() -> None:
            self._stop_fade()
            self._alpha = target
            if target <= 0.0:
                self.hide()

        t.valueChanged.connect(step)
        t.finished.connect(done)
        self._fade = t
        self._fading_out = target <= 0.0
        t.start()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # The Mac's placeholder is only a line: a crisp, light grey hairline in
        # the widget's own shape, with the desktop showing straight through -
        # no wash, no blur, no shadow. Under it, one pixel out, a faint dark
        # line so it still reads over a white wallpaper.
        a = max(0.0, min(1.0, self._alpha))
        if a <= 0.0:
            p.end()
            return
        r = self._card.adjusted(1.0, 1.0, -1.0, -1.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, int(40 * a)), 3.6))
        p.drawPath(squircle(r, RADIUS - 1.0))
        p.setPen(QPen(QColor(232, 232, 236, int(235 * a)), 2.0))
        p.drawPath(squircle(r, RADIUS - 1.0))
        p.end()


_outline: Optional[DropOutline] = None
# The card being dragged or gliding into place, if any. While there is one,
# every card on the board wears the light rim, as the Mac's widgets all do the
# moment one of them is picked up.
_moving: Optional["WidgetCard"] = None


def drop_outline() -> DropOutline:
    global _outline
    if _outline is None:
        _outline = DropOutline()
    return _outline


class WidgetCard(QWidget):
    """One widget on the board."""

    kind = "base"
    title = "Widget"
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 1000

    # Most cards are display-only and must never take focus away from your
    # work. A card that hosts a real input (the notes editor) has to, or there
    # is no way for keystrokes to reach it.
    wants_focus = False

    # Extra window height reserved *below* the card, for a tile that
    # deliberately draws outside it. The card itself stays the standard size.
    extra_bottom = 0.0

    # Whether every timer tick should also repaint. True suits a tile whose
    # reading changes each tick anyway. A tile that ticks fast for animation but
    # is expensive to draw should set this False and call update() when something
    # has actually changed - the media player's frosted glass costs 4ms a frame,
    # which is a tenth of a core if it repaints 25 times a second for nothing.
    repaint_on_tick = True
    customizable = False        # opt-in to per-card colour customization
    allow_image = False         # opt-in to a picture background

    # Set by the board. Where the card belongs (a displays.Anchor), whether its
    # own display is missing so it is waiting on another, and whether a game
    # covers the display it is on.
    anchor = None
    displaced = False
    quiet = False
    # The other cards on the board, for lining up against. Set by the board
    # to a callable; None means a card on its own (the gallery's previews).
    peers = None

    moved = Signal()
    changed = Signal()          # size or config changed; board should persist
    removed = Signal(object)

    def __init__(self, palette: Palette, size: str = "small",
                 config: Optional[dict] = None) -> None:
        super().__init__(None)
        self.size_class = size if size in self.supported_sizes else self.supported_sizes[0]
        self.config = dict(config or {})
        # The theme palette is kept apart from the one the tile actually paints
        # with: a customised card overrides colours on a derived copy, and the
        # base is what a theme change updates. See widgets/core/appearance.py.
        self._base_palette = palette
        self.palette_ = self._effective_palette()
        self._img_cache: dict = {}      # see _image_layer
        self.spotlit = False             # ringed while the app points it out
        self.locked = False
        self.snap_to_grid = False        # align to neighbours while dragging (magnet.py)
        self.opacity_ = 1.0
        self.glass = None                # a shared GlassBackdrop, set by Board
        self.glass_tint = 112            # alpha of the tint over the blur
        self._bent = None                # (key, image): the liquid glass, kept
        # The glass as last drawn - (what it shows, image, source rect or None
        # for all of it) - where, at which generation of the glass, and the
        # pane a change is fading from.
        self._pane = None
        self._pane_at: Optional[Tuple[int, int, int, int]] = None
        self._pane_gen = None
        self._pane_from = None
        self._frost_gen = None           # frosted: the generation last looked at...
        self._frost_what = None          # ...and the one the slice last changed at
        self._glass_fade = Fader(self)
        self._glass_fade.finish()        # nothing to fade from on the first paint
        self._shadow_kept = None         # (size and where, image): the shadow, drawn once
        # What's drawn over the glass, kept as one image for live glass's
        # frames (glass_update): (what it was drawn for, image), whether
        # something has asked for a repaint since, whether the repaint asked
        # for is for new glass alone, and whether a picture covers the glass.
        self._over_kept = None
        self._over_stale = True
        self._glass_only = False
        self._covered = False
        self._drag_ms = 0.0              # what bending it at a step of a drag costs
        self._drag_from: Optional[QPoint] = None
        self._target: Optional[Tuple[int, int]] = None   # window pos the outline shows
        self._glide = None               # the Tween easing a dropped card into place
        self._gliding = False
        self._hover = False

        flags = Qt.FramelessWindowHint | Qt.Tool
        if not self.wants_focus:
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle(f"PC Widget - {self.title}")
        self.setMouseTracking(True)
        self.resize(*pixel_size(self.size_class, self.extra_bottom))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        # Windows may place a window itself when it is first shown, overriding
        # the move() that ran before. That is how a board once cascaded into
        # the top-left corner - and because the layout is persisted on startup,
        # the OS's choice then got saved and restored on every later launch.
        # Re-assert the intended position once the native window exists.
        wanted = self.pos()
        self.show()
        if self.pos() != wanted:
            self.move(wanted)
        winapi.make_desktop_window(int(self.winId()),
                                   no_activate=not self.wants_focus)
        self.on_start()
        self._tick()
        if self.refresh_ms:
            self._timer.start(self.refresh_ms)

    def _tick(self) -> None:
        try:
            self.on_tick()
        except Exception:
            pass          # a flaky data source must never kill the board
        if self.repaint_on_tick:
            self.update()

    # ------------------------------------------------------------------ #
    def update(self, *args) -> None:
        """Repaint - unless a fullscreen app is in front.

        Overridden here rather than guarded at each call site so that *every*
        route to a repaint is covered: timers, data arriving on a signal, hover,
        a fade stepping. One of those getting missed is how a "pauses while
        gaming" feature quietly does nothing.

        Any of them may change what's drawn over the glass, so the layer of it
        kept for glass_update() is drawn again at the next paint - noted even
        while quiet, for the repaint that catches up.
        """
        self._over_stale = True
        if _quiet or self.quiet:
            return
        super().update(*args)

    def glass_update(self) -> None:
        """Repaint for new glass behind the card and nothing else: live
        glass's frames, up to sixty a second. What's drawn over the glass -
        the tint, the lit rim, the content - is kept from the last paint as one
        image and laid on the new glass rather than drawn again (paint_card's
        ``over``). Drawing it was most of a repaint: a stocks card's took
        1.7 ms, and live glass repaints every card behind a moving wallpaper
        at its own pace. Quiet, like update()."""
        if _quiet or self.quiet:
            return
        self._glass_only = True
        QWidget.update(self)

    def live_update(self) -> None:
        """Repaint for what paint_live draws and nothing else - a part that
        moves over a card that doesn't, the timer's ruler gliding at the
        display's rate. The kept layer is laid back on, as for glass_update(),
        so a frame costs the moving part alone. Quiet, like update()."""
        if _quiet or self.quiet:
            return
        self._glass_only = True
        QWidget.update(self)

    def force_update(self) -> None:
        """Repaint regardless of quiet mode, to catch up when it lifts."""
        self._over_stale = True
        QWidget.update(self)

    # -- for subclasses -------------------------------------------------- #
    def on_start(self) -> None:
        """Called once the native window exists."""

    def on_tick(self) -> None:
        """Refresh data. Called on the widget's own timer."""

    def paint_content(self, p: QPainter, r: QRectF) -> None:
        raise NotImplementedError

    def paint_live(self, p: QPainter, r: QRectF) -> None:
        """What a tile draws over its kept layer at every paint: a part that
        moves while the rest stands still, repainted with live_update(). It
        stays out of the kept layer, so moving it costs only itself. Nothing
        by default."""

    def context_actions(self, menu: QMenu) -> None:
        """Tiles may add their own entries to the right-click menu."""

    @classmethod
    def new_config(cls) -> dict:
        """Settings a brand-new card of this kind starts with. A card restored
        from a saved layout keeps exactly what it had."""
        return {}

    def on_focus_click(self, ev) -> None:
        """A focusable card was clicked; hand focus to whatever it hosts."""

    # ------------------------------------------------------------------ #
    #  Appearance
    # ------------------------------------------------------------------ #
    def _say_image(self, msg: str) -> None:
        """Report a background-image outcome once per card, per message."""
        seen = getattr(self, "_img_said", None)
        if seen is None:
            seen = self._img_said = set()
        if msg in seen:
            return
        seen.add(msg)
        try:
            from .store import report
            report(f"{self.kind}: background image - {msg}")
        except Exception:
            pass

    def _image_layer(self, p: QPainter, r: QRectF):
        """The background picture cut and scaled for this card, or None.

        Returns ``(piece, covers)``: the visible part of the picture placed by
        the card's saved crop (see :mod:`widgets.core.imagefit`), and whether it
        fills the whole card. A missing or unreadable file is None, so the card
        falls straight through to its normal background.

        Cached on everything that changes the result: the file, the card's size,
        the crop, and how many device pixels each card pixel covers. That last
        one is not 1 when the crop editor draws the card enlarged - keying on it
        gives the editor a sharp picture without costing the desktop anything -
        and a few entries are kept so the editor and the desktop drawing the same
        card in turn don't evict each other on every frame.
        """
        if not self.allow_image:
            return None
        src = self.config.get("image")
        if not src:
            return None
        if not os.path.isfile(src):
            self._say_image(f"file is gone: {src}")
            return None

        crop = imagefit.from_config(self.config)
        t = p.worldTransform()
        dev = math.hypot(t.m11(), t.m12()) or 1.0
        key = (src, round(r.width(), 2), round(r.height(), 2), crop.key(),
               round(dev, 3))
        hit = self._img_cache.pop(key, None)
        if hit is None:
            img = imagefit.load(src)
            if img is None:
                self._say_image(f"could not decode {src}")
                return None
            self._say_image(f"loaded {img.width()}x{img.height()} from {src}")
            piece = imagefit.render(img, r.width(), r.height(), crop, dev)
            if piece is None:
                return None
            hit = (piece, imagefit.covers(img.width(), img.height(),
                                          r.width(), r.height(), crop))
        self._img_cache[key] = hit                  # most recent last
        while len(self._img_cache) > 4:
            self._img_cache.pop(next(iter(self._img_cache)))
        return hit

    def _draw_image_layer(self, p: QPainter, r: QRectF, piece) -> None:
        """Paint a cut picture, clipped to the squircle, under a soft scrim.

        The scrim - darker at the top and bottom - is what keeps white text off
        a bright sky legible without flattening the middle of the image, the same
        trick iOS uses for widgets over a photo. It covers only the picture, so a
        photo zoomed out to fit doesn't put dark bands across the card's colour.

        The picture is snapped to whole device pixels. It was scaled to exactly
        the size it is drawn at, and a fractional offset would make the painter
        resample it - a half-pixel blur across the whole photo.
        """
        tx, ty, tw, th = piece.target
        vx, vy, vw, vh = piece.visible
        p.save()
        p.setClipPath(squircle(r, RADIUS))
        t = p.worldTransform()
        inv, ok = t.inverted()
        at = QPointF(r.left() + tx, r.top() + ty)
        if ok:
            dp = t.map(at)
            at = inv.map(QPointF(round(dp.x()), round(dp.y())))
        dev = math.hypot(t.m11(), t.m12()) or 1.0
        p.drawImage(QRectF(at.x(), at.y(), piece.image.width() / dev,
                           piece.image.height() / dev), piece.image)

        vis = QRectF(r.left() + vx, r.top() + vy, vw, vh)
        scrim = QLinearGradient(vis.left(), vis.top(), vis.left(), vis.bottom())
        scrim.setColorAt(0.0, QColor(0, 0, 0, 105))
        scrim.setColorAt(0.32, QColor(0, 0, 0, 24))
        scrim.setColorAt(0.7, QColor(0, 0, 0, 24))
        scrim.setColorAt(1.0, QColor(0, 0, 0, 120))
        p.setBrush(QBrush(scrim))
        p.setPen(Qt.NoPen)
        p.drawRect(vis)
        p.restore()

    def _paint_image_bg(self, p: QPainter, r: QRectF, path=None) -> bool:
        """Draw the chosen picture as the card background. Returns whether it did."""
        layer = self._image_layer(p, r)
        if layer is None:
            return False
        self._draw_image_layer(p, r, layer[0])
        return True

    def _effective_palette(self) -> Palette:
        if not self.customizable:
            return self._base_palette
        from . import appearance
        return appearance.effective(self._base_palette, self.config)

    @property
    def solid_bg(self) -> bool:
        """True when the card is a flat fill rather than frosted glass."""
        if not self.customizable:
            return False
        from . import appearance
        return appearance.is_solid(self.config)

    def set_palette_(self, palette: Palette) -> None:
        # A theme change updates the base; the tile keeps its own overrides.
        self._base_palette = palette
        self.palette_ = self._effective_palette()
        self.update()

    def set_appearance(self, key: str, value) -> None:
        """Write one appearance key, re-derive the palette, persist, repaint."""
        if value is None:
            self.config.pop(key, None)
        else:
            self.config[key] = value
        if key == "image":
            # A crop belongs to the picture it was made for.
            self.config.pop("crop", None)
        self.palette_ = self._effective_palette()
        self.on_appearance_changed()
        self.changed.emit()
        self.update()

    def set_image(self, path: Optional[str], crop: Optional[dict] = None) -> None:
        """Set or clear the background picture and where it sits, as one change.

        One write rather than two ``set_appearance`` calls, so the layout is never
        saved holding a new picture with the old picture's crop.
        """
        for key, value in (("image", path), ("crop", crop if path else None)):
            if value is None:
                self.config.pop(key, None)
            else:
                self.config[key] = value
        self.palette_ = self._effective_palette()
        self.on_appearance_changed()
        self.changed.emit()
        self.update()

    def reset_appearance(self) -> None:
        for key in ("bg", "fg", "fill", "image", "crop"):
            self.config.pop(key, None)
        self.palette_ = self._effective_palette()
        self.on_appearance_changed()
        self.changed.emit()
        self.update()

    def on_appearance_changed(self) -> None:
        """Hook for tiles that hold colour outside the palette (Notes' editor)."""
        pass

    def set_size_class(self, size: str) -> None:
        if size not in self.supported_sizes or size == self.size_class:
            return
        self.size_class = size
        self.resize(*pixel_size(size, self.extra_bottom))
        self.changed.emit()
        self.update()

    def card_rect(self) -> QRectF:
        return QRectF(MARGIN, MARGIN,
                      self.width() - MARGIN * 2,
                      self.height() - MARGIN * 2 - self.extra_bottom)

    def paintEvent(self, _ev) -> None:
        # New glass alone (glass_update) lays the kept layer on it; any other
        # repaint draws it all. Read before painting: a repaint asked for while
        # this one paints is the next one's.
        reuse = self._glass_only and not self._over_stale
        self._glass_only = self._over_stale = False
        p = QPainter(self)
        self.paint_card(p, over="reuse" if reuse else "keep")
        p.end()

    def paint_card(self, p: QPainter, opacity: Optional[float] = None,
                   shadow: bool = True, over: Optional[str] = None) -> None:
        """Draw the whole card onto ``p``: chrome, material, then the content.

        Split out of ``paintEvent`` so a tile can render itself into an offscreen
        image and then read those pixels back. That is the only way to blur what
        is *behind* something - a QWidget's own painter cannot be read from - and
        it is what the media player's frosted transport needs. The app window and
        the crop editor draw cards through it too; the editor leaves the shadow
        off, since there the card sits inside a picture rather than on a desktop.

        ``over``, from paintEvent: what's drawn over the glass is drawn into an
        image and kept ("keep"), or the kept one is laid on the new glass
        ("reuse") - if it was drawn for the same card, size and glass. Others
        draw it all straight onto ``p``.
        """
        # Antialiasing alone only smooths *shapes*. Glyphs need TextAntialiasing
        # as well, and without it type is rendered with hard pixel edges - which
        # is what made the digits look chiselled rather than smooth.
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.LosslessImageRendering, True)
        p.setOpacity(self.opacity_ if opacity is None else opacity)

        pal = self.palette_
        r = self.card_rect()

        # --- ambient shadow -------------------------------------------- #
        # Stacked translucent copies, each a little larger and lower: it lifts
        # the card off the wallpaper the way a real widget sits. Drawn once a
        # size and kept, since its nine curves were half a plain card's
        # repaint, and live glass repaints a card many times a second.
        p.setPen(Qt.NoPen)
        if shadow:
            p.drawImage(QPointF(0, 0), self._shadow(r, p.device().devicePixelRatioF()))

        # --- the card -------------------------------------------------- #
        path = squircle(r, RADIUS)

        # Three backgrounds, in order of precedence: a chosen picture, a flat
        # colour, or the frosted desktop. A picture that fills the card opts out
        # of the other two entirely - that is the whole point of choosing one -
        # while a picture zoomed out to fit shows the card's own background
        # around it.
        layer = self._image_layer(p, r)
        image = layer is not None
        frosted = False
        liquid = False
        self._covered = bool(image and layer[1])
        if not self._covered and not self.solid_bg and self.glass is not None:
            # Glass: the desktop showing through, then a tint on top. Liquid
            # glass (lens.py) when it can be bent, frosted glass otherwise -
            # both clipped to the squircle, which is the whole reason it's done
            # in-process rather than handed to DWM.
            p.save()
            p.setClipPath(path)
            liquid, frosted = self._paint_glass(p, r)
            p.restore()

        # Everything over the glass. From paintEvent it's drawn into an image
        # and kept, and a repaint for new glass alone lays that on the new
        # glass. Only at full opacity: a fading card's layers each fade, which
        # one faded image wouldn't match.
        if over is not None and p.opacity() == 1.0:
            dpr = p.device().devicePixelRatioF()
            key = (self.width(), self.height(), r.x(), r.y(), r.width(), r.height(), dpr,
                   liquid, frosted, self._covered, id(pal), self._hover and not self.locked,
                   _moving is not None, _moving is self, self.spotlit)
            kept = self._over_kept
            if over != "reuse" or kept is None or kept[0] != key:
                # Drawn on a straight-alpha image, as the dock is, then
                # converted once: on a premultiplied one Qt renders type with
                # ClearType whatever the font asks, and over the glass every
                # letter had red and blue edges. Greyscale, as Apple draws it.
                img = QImage(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)),
                             QImage.Format_ARGB32)
                img.setDevicePixelRatio(dpr)
                img.fill(Qt.transparent)
                q = QPainter(img)
                q.setRenderHints(p.renderHints())
                q.setFont(p.font())
                self._paint_over(q, r, path, layer, liquid, frosted)
                q.end()
                img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
                img.setDevicePixelRatio(dpr)
                kept = self._over_kept = (key, img)
            p.drawImage(QPointF(0, 0), kept[1])
        else:
            self._paint_over(p, r, path, layer, liquid, frosted)
        p.setBrush(Qt.NoBrush)
        p.setPen(Qt.NoPen)
        self.paint_live(p, QRectF(r.left() + PAD, r.top() + PAD,
                                  r.width() - PAD * 2, r.height() - PAD * 2))

    def _paint_over(self, p: QPainter, r: QRectF, path: QPainterPath, layer,
                    liquid: bool, frosted: bool) -> None:
        """What paint_card draws over the card's glass: the tint, a picture,
        the lit rim or sheen, the rings, and the content."""
        pal = self.palette_
        image = layer is not None
        p.setPen(Qt.NoPen)
        if not (image and layer[1]):
            fill = QColor(pal.card)
            if self.solid_bg:
                fill.setAlpha(255)
            elif frosted:
                # Low alpha so the wallpaper genuinely reads through, the way an
                # Apple widget does. Opaque enough to keep text legible over a
                # busy picture, which is what the tint is for.
                fill.setAlpha(self.glass_tint)
            else:
                fill.setAlpha(224 if pal.dark else 232)
            p.setBrush(fill)
            p.drawPath(path)
            self.paint_tint(p, path, r)

        if image:
            self._draw_image_layer(p, r, layer[0])

        if liquid:
            # Liquid glass's light is a thin lit rim, not a sheen: the edge
            # catching a light from the top left, over a hairline.
            p.drawImage(QPointF(r.left(), r.top()),
                        self.glass.rim(r.width(), r.height(), RADIUS, pal.dark))
        elif not self.solid_bg and not image:
            # A soft inner light along the top - the sheen that makes frosted
            # glass read as glass. It belongs only *on* glass: over a flat colour
            # it is the grey glare that gave a solid card a smudged top edge, and
            # over a picture it would fog the image. So it is skipped for both.
            p.save()
            p.setClipPath(path)
            sheen = QLinearGradient(r.left(), r.top(), r.left(), r.top() + 46)
            sheen.setColorAt(0.0, QColor(255, 255, 255, 22 if pal.dark else 90))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(sheen))
            p.drawRect(r)
            p.restore()

        if _moving is not None:
            # Moving: the same light hairline as the drop outline, the way the
            # Mac rims its widgets while one is moved - brightest on the one in
            # hand, so card and outline read as one shape and its landing place.
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(232, 232, 236, 200 if _moving is self else 120), 1.6))
            p.drawPath(squircle(r.adjusted(0.8, 0.8, -0.8, -0.8), RADIUS - 0.8))
            p.setPen(Qt.NoPen)
        elif self._hover and not self.locked:
            ring = QColor(pal.accent)
            ring.setAlpha(110)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.5))
            p.drawPath(path)
            p.setPen(Qt.NoPen)

        # The app pointing a card out - just added, or "Show on desktop". Drawn
        # whether or not positions are locked: it is not a drag affordance.
        if self.spotlit:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(pal.accent), 3.0))
            p.drawPath(squircle(r.adjusted(1.5, 1.5, -1.5, -1.5), RADIUS - 1.5))
            p.setPen(Qt.NoPen)

        inner = QRectF(r.left() + PAD, r.top() + PAD,
                       r.width() - PAD * 2, r.height() - PAD * 2)
        p.setBrush(Qt.NoBrush)
        p.setPen(Qt.NoPen)
        self.paint_content(p, inner)

    def _shadow(self, r: QRectF, dpr: float) -> QImage:
        """The ambient shadow for a card at ``r``, as an image the size of
        the window, drawn at ``dpr`` and kept until the size changes."""
        key = (self.width(), self.height(), r.x(), r.y(), r.width(), r.height(), dpr)
        kept = self._shadow_kept
        if kept is None or kept[0] != key:
            img = QImage(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)),
                         QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.Antialiasing, True)
            q.setPen(Qt.NoPen)
            for i in range(SHADOW_LAYERS, 0, -1):
                grow = i * 1.7
                q.setBrush(QColor(0, 0, 0, max(2, int(SHADOW_ALPHA / (i * 0.85)))))
                q.drawPath(squircle(r.adjusted(-grow, -grow + 2.0, grow, grow + 3.0), RADIUS + grow))
            q.end()
            kept = self._shadow_kept = (key, img)
        return kept[1]

    def paint_tint(self, p: QPainter, path: QPainterPath, r: QRectF) -> None:
        """Colour laid over the card's glass, under liquid glass's lit rim and
        under the content. Nothing by default; the weather faces use it for
        Apple's sky-coloured background. Skipped when a picture fills the card."""

    def _paint_glass(self, p: QPainter, r: QRectF) -> Tuple[bool, bool]:
        """Paint the card's slice of desktop: through liquid glass when it can
        be bent, frosted otherwise. Returns (liquid, drawn); not drawn, the
        card is off the desktop and gets a solid fill instead.

        When that slice changes under a card sitting still, the card fades
        from the old one to the new over GLASS_FADE_MS rather than switching.
        A card being moved or resized switches: there's no old slice of the
        same place to fade from."""
        g = self.glass
        sx, sy = self.x() + int(MARGIN), self.y() + int(MARGIN)
        w, h = int(round(r.width())), int(round(r.height()))
        moving = self._drag_from is not None or self._gliding
        pane = self._liquid_pane(g, sx, sy, w, h, moving)
        liquid = pane is not None
        if pane is None:
            pane = self._frosted_pane(g, sx, sy, w, h)
        if pane is None:
            self._pane = self._pane_from = None
            return False, False
        at = (sx, sy, w, h)
        last = self._pane
        if last is not None and last[0] != pane[0]:
            # Live glass's frames switch: they follow each other too quickly
            # to fade between. Anything else - a new wallpaper, a setting -
            # fades, if the card sits where it was.
            fade_since = getattr(g, "fade_since", None)
            live = (fade_since is not None and g.generation != self._pane_gen
                    and not fade_since(self._pane_gen, at))
            if self._pane_at == at and not moving and not live:
                self._pane_from = last
                self._glass_fade.start(GLASS_FADE_MS)
            else:
                self._pane_from = None
                self._glass_fade.finish()
        self._pane, self._pane_at, self._pane_gen = pane, at, g.generation
        # Bent glass is worked out on a coarser grid than the screen's
        # (lens.STEP) and drawn scaled up, smoothly - paint_card sets
        # SmoothPixmapTransform. Frosted glass is a slice of the blurred desk.
        target = QRectF(r.left(), r.top(), w, h)
        if self._pane_from is not None and self._glass_fade.value < 1.0:
            self._draw_pane(p, target, self._pane_from)
            opacity = p.opacity()
            p.setOpacity(opacity * self._glass_fade.value)
            self._draw_pane(p, target, pane)
            p.setOpacity(opacity)
        else:
            self._pane_from = None
            self._draw_pane(p, target, pane)
        return liquid, True

    def _paint_liquid(self, p: QPainter, r: QRectF) -> bool:
        """The liquid glass alone, without a fade: True if it could be bent.
        The self-test's way in to the bending."""
        sx, sy = self.x() + int(MARGIN), self.y() + int(MARGIN)
        w, h = int(round(r.width())), int(round(r.height()))
        moving = self._drag_from is not None or self._gliding
        pane = self._liquid_pane(self.glass, sx, sy, w, h, moving)
        if pane is None:
            return False
        self._draw_pane(p, QRectF(r.left(), r.top(), w, h), pane)
        return True

    @staticmethod
    def _draw_pane(p: QPainter, target: QRectF, pane) -> None:
        if pane[2] is None:
            p.drawImage(target, pane[1])
        else:
            p.drawImage(target, pane[1], pane[2])

    def _liquid_pane(self, g, sx: int, sy: int, w: int, h: int, moving: bool):
        """The card's slice of desktop through liquid glass (lens.py), as a
        pane for _paint_glass, or None for frosted glass: while the clear copy
        of the desktop is still being built (it's done on a thread), without
        numpy, or on a PC too slow to bend at every step of a drag.

        The bend is kept until the card moves, changes size or the glass
        behind it changes, so a card sitting still costs nothing to repaint -
        the clock ticking redraws the kept image. A change somewhere else on
        the desk (a live wallpaper moving behind another card) keeps it too."""
        if not getattr(g, "ready", False):
            return None
        key = (id(g), g.generation, sx, sy, w, h, moving)
        kept = self._bent
        if kept is not None and kept[0] != key and kept[0][0] == key[0] and kept[0][2:] == key[2:]:
            changed = getattr(g, "changed_since", None)
            if changed is not None and not changed(kept[0][1], (sx, sy, w, h)):
                kept = self._bent = (key, kept[1])
        if kept is None or kept[0] != key:
            if moving and self._drag_ms > BEND_BUDGET_MS:
                return None
            img = g.bend(sx, sy, w, h, RADIUS, step=DRAG_STEP if moving else None)
            if img is None:
                return None
            # What a step of a drag costs: measured on one, or - from a bend at
            # rest, on a grid with four times the points - about a quarter.
            self._drag_ms = g.last_bend_ms if moving else g.last_bend_ms / 4.0
            kept = self._bent = (key, img)
        return (("liquid", id(kept[1])), kept[1], None)

    def _frosted_pane(self, g, sx: int, sy: int, w: int, h: int):
        """The card's slice of the blurred desk, as a pane for _paint_glass,
        or None off the desk. It counts as changed only when the glass behind
        the card did, so a live wallpaper moving elsewhere fades nothing."""
        ahead = getattr(g, "frost_ahead", None)
        live = ahead(sx, sy, w, h) if ahead is not None else None
        if live is not None:
            return (("frosted", id(g), "live", id(live)), live, None)   # live glass's slice
        image = g.image
        if image is None:
            return None
        ox, oy = g.origin
        src = QRect(int(sx - ox), int(sy - oy), int(w), int(h))
        if not src.intersects(image.rect()):
            return None
        gen = g.generation
        if self._frost_gen != gen:
            changed = getattr(g, "changed_since", None)
            if self._frost_gen is None or changed is None or changed(self._frost_gen, (sx, sy, w, h)):
                self._frost_what = gen
            self._frost_gen = gen
        return (("frosted", id(g), self._frost_what), image, src)

    def glass_stale(self) -> bool:
        """Whether the glass behind this card changed since it was drawn - so
        the board repaints only the cards a live wallpaper moved behind."""
        g = self.glass
        if g is None or self.solid_bg or self._covered:
            return False                 # no glass shows: a picture covers it, say
        if self._pane is None or self._pane_at is None:
            return True
        if bool(getattr(g, "ready", False)) != (self._pane[0][0] == "liquid"):
            return True
        changed = getattr(g, "changed_since", None)
        return changed is None or changed(self._pane_gen, self._pane_at)

    # -- painting helpers shared by every tile --------------------------- #
    def font(self, size: float, weight: int = QFont.Normal,
             italic: bool = False, stretch: int = 0) -> QFont:
        """Apple-set type in Inter: optical size picked by point size, plus
        Apple's size-dependent tracking. See :mod:`widgets.core.typography`.

        ``italic`` and ``stretch`` pass through for the clock's time styling.
        A font picked from the card's Font menu (``config["font"]``) replaces
        Inter for everything the tile draws."""
        return typography.font(size, weight, italic=italic, stretch=stretch,
                               choice=self.config.get("font"))

    def set_font_choice(self, key: Optional[str]) -> None:
        """Set the card in one of typography.FONT_CHOICES, or back to the
        default with None."""
        if key is not None and key not in typography.FONT_CHOICES:
            return
        if key == self.config.get("font"):
            return
        if key is None:
            self.config.pop("font", None)
        else:
            self.config["font"] = key
        self.on_appearance_changed()
        self.changed.emit()
        self.update()

    def _font_menu(self, menu: QMenu) -> None:
        """Font: the default, then every bundled face by group, each name
        shown in its own typeface so the menu is its own preview."""
        from PySide6.QtGui import QActionGroup
        sub = menu.addMenu("Font")
        group = QActionGroup(sub)
        current = self.config.get("font")
        default = QAction("Default (Inter)", sub, checkable=True)
        default.setChecked(current not in typography.FONT_CHOICES)
        default.triggered.connect(lambda _c=False: self.set_font_choice(None))
        group.addAction(default)
        sub.addAction(default)
        sub.addSeparator()
        # A submenu per group: with every face in one list the menu ran taller
        # than a laptop screen. The group holding the current font is ticked.
        for heading in typography.FONT_GROUPS:
            keys = [k for k, v in typography.FONT_CHOICES.items()
                    if v[2] == heading and typography.choice_available(k)]
            if not keys:
                continue
            gm = sub.addMenu(heading)
            if current in keys:
                gm.setTitle(f"{heading}  \u2713")
            for key in keys:
                label, family = typography.FONT_CHOICES[key][:2]
                a = QAction(label, gm, checkable=True)
                preview = QFont(family)
                preview.setPointSizeF(12.0 if key == "caveat" else 10.5)
                if typography.FONT_CHOICES[key][3]:
                    # A variable file's default can be its thinnest weight.
                    preview.setVariableAxis(QFont.Tag("wght"), 400.0)
                a.setFont(preview)
                a.setChecked(key == current)
                a.triggered.connect(lambda _c=False, k=key: self.set_font_choice(k))
                group.addAction(a)
                gm.addAction(a)

    def text(self, p: QPainter, rect: QRectF, s: str, size: int,
             color: str, weight: int = QFont.Normal,
             align=Qt.AlignLeft | Qt.AlignVCenter) -> None:
        p.setFont(self.font(size, weight))
        p.setPen(QColor(color))
        p.drawText(rect, align, s)

    def label(self, p: QPainter, rect: QRectF, s: str) -> None:
        """The header every tile uses.

        Sentence case, semibold, secondary colour - the way Apple sets widget
        headers. The previous letter-spaced uppercase treatment is a Windows
        convention and was the single most un-Apple thing on the card.
        """
        p.setFont(self.font(10.5, QFont.DemiBold))
        p.setPen(QColor(self.palette_.text_dim))
        # Tiles pass a 12px band sized for the old 8pt caption; give the larger
        # type room rather than editing every offset in every tile.
        p.drawText(QRectF(rect.left(), rect.top(), rect.width(),
                          max(rect.height(), 16.0)),
                   Qt.AlignLeft | Qt.AlignVCenter, s)

    def bar(self, p: QPainter, rect: QRectF, frac: float, color: str) -> None:
        frac = max(0.0, min(1.0, frac))
        track = QColor(self.palette_.text_faint)
        track.setAlpha(52)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if frac > 0.001:
            p.setBrush(QColor(color))
            p.drawRoundedRect(
                QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * frac),
                       rect.height()),
                rect.height() / 2, rect.height() / 2)

    # ------------------------------------------------------------------ #
    #  Interaction
    # ------------------------------------------------------------------ #
    def enterEvent(self, _ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _ev) -> None:
        self._hover = False
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.RightButton:
            self._menu().exec(ev.globalPosition().toPoint())
            return
        if ev.button() == Qt.LeftButton:
            if self.wants_focus:
                self.raise_()
                self.activateWindow()
                self.on_focus_click(ev)
            if not self.locked:
                # Caught mid-glide: stop where it is and pick it up from there.
                self._stop_glide()
                self._drag_from = ev.globalPosition().toPoint() - self.pos()
                self._target = None
                self._set_moving(True)      # rim the board at once
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_from is None:
            return
        # The card always follows the mouse exactly - nothing snaps under the
        # cursor. Only the outline jumps to where it will land.
        self.move(ev.globalPosition().toPoint() - self._drag_from)
        self._update_target(bool(ev.modifiers() & Qt.AltModifier))
        # The frosted slice is sampled by screen position, so it has to be
        # repainted as the card travels or the blur would slide with it.
        self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag_from is None:
            return
        self._drag_from = None
        if ev.modifiers() & Qt.AltModifier:
            self._target = None                 # Alt held: exactly where let go
        target = self._target
        self._target = None
        if _outline is not None:
            _outline.fade_out()         # fades as the card glides into it
        x, y = target if target is not None else (self.x(), self.y())
        self._glide_to(*self._clamped(x, y))
        ev.accept()

    # -- lining up ------------------------------------------------------- #
    def _screen_card(self, x: Optional[int] = None,
                     y: Optional[int] = None) -> Tuple[int, int, int, int]:
        """The visible card as (x, y, w, h) on screen - the window minus its
        shadow gutter - with the window at (x, y), or where it is now."""
        m = int(MARGIN)
        r = self.card_rect()
        return ((self.x() if x is None else x) + m,
                (self.y() if y is None else y) + m,
                int(round(r.width())), int(round(r.height())))

    def _update_target(self, free: bool) -> None:
        """Work out where the card would settle and move the outline there.

        Holding Alt places freely, as Command does on a Mac."""
        target = None
        if self.snap_to_grid and not free:
            cx, cy, cw, ch = self._screen_card()
            work = displays.work_area_at(cx + cw // 2, cy + ch // 2)
            others = [o._screen_card() for o in (self.peers() if self.peers else ())
                      if o is not self and o.isVisible()]
            m = int(MARGIN)
            held = None
            if self._target is not None:
                held = (self._target[0] + m, self._target[1] + m)
            hit = magnet.suggest((cx, cy, cw, ch), others, work, GAP, held=held)
            if hit is not None:
                target = self._clamped(hit[0] - m, hit[1] - m)
                # Already sitting on it: an outline exactly under the card
                # tells you nothing.
                if target == (self.x(), self.y()) and self._target is None:
                    target = None
        self._target = target
        if target is None:
            if _outline is not None:
                _outline.fade_out()
        else:
            drop_outline().show_for(self, *target)

    def _clamped(self, x: int, y: int) -> Tuple[int, int]:
        """A window position for this card at (x, y), pulled back so the card
        stays on the monitor it's over. Bounds are the *card*, not the window,
        so the shadow gutter may hang off the edge and the card sit flush."""
        m = int(MARGIN)
        cx, cy, cw, ch = self._screen_card(x, y)
        wl, wt, wr, wb = displays.work_area_at(cx + cw // 2, cy + ch // 2)
        cx = max(wl, min(cx, wr - cw))
        cy = max(wt, min(cy, wb - ch))
        return (int(cx - m), int(cy - m))

    # -- the glide into place -------------------------------------------- #
    def _glide_to(self, x: int, y: int) -> None:
        """Ease the card from where it was let go to (x, y), then report the
        move. A cut instead when there's nowhere to go or Windows' animation
        effects are off."""
        sx, sy = self.x(), self.y()
        dist = math.hypot(x - sx, y - sy)
        if dist < 1.0 or not winapi.animations_enabled():
            self.move(x, y)
            self._landed()
            return
        from .frameclock import Tween
        t = Tween(self)
        t.setStartValue(0.0)
        t.setEndValue(1.0)
        # Ease-out: fast off the mark, then settling softly into the outline,
        # which is what makes it feel pulled in rather than teleported.
        t.setDuration(max(GLIDE_MIN_MS, min(GLIDE_MAX_MS, 150.0 + dist * 1.2)))
        t.setEasingCurve(QEasingCurve.OutCubic)
        t.valueChanged.connect(lambda v: (
            self.move(int(round(sx + (x - sx) * v)), int(round(sy + (y - sy) * v))),
            self.update()))
        t.finished.connect(self._landed)
        self._glide = t
        self._gliding = True
        t.start()

    def _stop_glide(self) -> None:
        if self._glide is not None:
            self._glide.stop()
            self._glide.deleteLater()
            self._glide = None
        self._gliding = False

    def closeEvent(self, ev) -> None:
        # A card removed mid-glide must not leave the frame clock stepping it.
        self._stop_glide()
        if self._drag_from is not None and _outline is not None:
            _outline.hide_now()
        self._drag_from = None
        self._set_moving(False)
        super().closeEvent(ev)

    def _set_moving(self, on: bool) -> None:
        global _moving
        if on:
            _moving = self
        elif _moving is self:
            _moving = None
        else:
            return
        for c in [self] + [o for o in (self.peers() if self.peers else ())
                           if o is not self]:
            c.update()

    def _landed(self) -> None:
        self._stop_glide()
        self._set_moving(False)
        self.moved.emit()
        # Where it landed, liquid glass is bent in full for the exact spot: the
        # drag bent it on a coarser grid, or not at all on a slow PC.
        self.update()

    def settle(self) -> None:
        """Keep the card reachable on a monitor - the one it's over.

        For a card put somewhere by the app rather than dragged: restored from
        a saved layout, just added, or grown past the screen edge. Lining up
        with neighbours happens only while dragging, where you can see it - a
        card shifting by itself on launch would just look like a bug.
        """
        self.move(*self._clamped(self.x(), self.y()))

    # Kept so older callers and saved layouts keep working.
    snap = settle

    def _menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction(self.title).setEnabled(False)
        m.addSeparator()

        if len(self.supported_sizes) > 1:
            for s in self.supported_sizes:
                a = QAction(s.capitalize(), m, checkable=True)
                a.setChecked(s == self.size_class)
                a.triggered.connect(lambda _c, sz=s: self.set_size_class(sz))
                m.addAction(a)
            m.addSeparator()

        self.context_actions(m)
        self._font_menu(m)
        if self.customizable:
            from . import appearance
            appearance.add_menu(self, m)
        if not m.actions()[-1].isSeparator():
            m.addSeparator()

        rm = QAction("Remove widget", m)
        rm.triggered.connect(lambda: self.removed.emit(self))
        m.addAction(rm)
        return m
