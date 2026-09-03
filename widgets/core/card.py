"""
card.py
=======
The base widget card: a frameless, translucent, rounded panel that lives on the
desktop, snaps to a grid when dragged, and knows its own size class.

Sizing follows the phone convention rather than free resizing - a widget is
small (2x2), medium (4x2) or large (4x4). Fixed sizes are what make a board of
mixed widgets look composed instead of scattered, and they mean every tile can
be designed for three known shapes rather than an infinite number.

Tiles subclass this and implement :meth:`paint_content`. The card handles the
chrome, the dragging, the menu and the persistence; the tile only draws.
"""

from __future__ import annotations

import os

from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QImage,
    QAction, QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen,
)
from PySide6.QtWidgets import QMenu, QWidget

from . import typography, winapi
from .palette import Palette

# --------------------------------------------------------------------------- #
#  The grid
# --------------------------------------------------------------------------- #
CELL = 78
GAP = 12
STEP = CELL + GAP        # snap increment, only used when snapping is switched on

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


# --------------------------------------------------------------------------- #
#  Quiet mode
# --------------------------------------------------------------------------- #
# Set by the board when a fullscreen app takes the foreground. Painting stops;
# ticking does not, so data stays current and the board is up to date the moment
# the game is minimised.
#
# It lives here, as one flag on the module, because the check is about the
# machine rather than about any one card - polling the foreground window once per
# widget would be nine times the work for one answer.
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

    def __init__(self, widget: QWidget, fps: int = 30) -> None:
        self._widget = widget
        self._step_ms = max(16, int(round(1000.0 / max(1, fps))))
        self._value = 1.0
        self._duration = 1.0
        self._elapsed = 0.0
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
        self._value = 0.0
        if not self._timer.isActive():
            self._timer.start()

    def finish(self) -> None:
        """Jump to the end. For a change that should not be animated at all."""
        self._timer.stop()
        self._value = 1.0

    def _step(self) -> None:
        self._elapsed += self._step_ms
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
        self._img_cache: dict = {}      # (path, w, h) -> cover-fitted QImage
        self.locked = False
        self.snap_to_grid = False        # free placement by default
        self.opacity_ = 1.0
        self.glass = None                # a shared GlassBackdrop, set by Board
        self.glass_tint = 112            # alpha of the tint over the blur
        self._drag_from: Optional[QPoint] = None
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
        """
        if _quiet:
            return
        super().update(*args)

    def force_update(self) -> None:
        """Repaint regardless of quiet mode, to catch up when it lifts."""
        QWidget.update(self)

    # -- for subclasses -------------------------------------------------- #
    def on_start(self) -> None:
        """Called once the native window exists."""

    def on_tick(self) -> None:
        """Refresh data. Called on the widget's own timer."""

    def paint_content(self, p: QPainter, r: QRectF) -> None:
        raise NotImplementedError

    def context_actions(self, menu: QMenu) -> None:
        """Tiles may add their own entries to the right-click menu."""

    def on_focus_click(self, ev) -> None:
        """A focusable card was clicked; hand focus to whatever it hosts."""

    # ------------------------------------------------------------------ #
    #  Appearance
    # ------------------------------------------------------------------ #
    def _paint_image_bg(self, p: QPainter, r: QRectF, path) -> bool:
        """Draw a chosen picture as the card background. Returns whether it did.

        Cover-fitted and centre-cropped so the card is always filled whatever
        the photo's shape, clipped to the squircle so the rounded corners hold,
        then framed with a soft top-and-bottom scrim. The scrim is what keeps
        white text off a bright sky legible without flattening the middle of the
        image - the same trick iOS uses for widgets over a photo. A missing or
        unreadable file falls straight through to the normal background.
        """
        if not self.allow_image:
            return False
        src = self.config.get("image")
        if not src or not os.path.isfile(src):
            return False

        key = (src, int(r.width()), int(r.height()))
        scaled = self._img_cache.get(key)
        if scaled is None:
            img = QImage(src)
            if img.isNull():
                return False
            scaled = img.scaled(int(r.width()), int(r.height()),
                                Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            self._img_cache = {key: scaled}     # only the current size is wanted
        sx = max(0, (scaled.width() - int(r.width())) // 2)
        sy = max(0, (scaled.height() - int(r.height())) // 2)

        path2 = squircle(r, RADIUS)
        p.save()
        p.setClipPath(path2)
        p.drawImage(r, scaled, QRectF(sx, sy, r.width(), r.height()))
        scrim = QLinearGradient(r.left(), r.top(), r.left(), r.bottom())
        scrim.setColorAt(0.0, QColor(0, 0, 0, 105))
        scrim.setColorAt(0.32, QColor(0, 0, 0, 24))
        scrim.setColorAt(0.7, QColor(0, 0, 0, 24))
        scrim.setColorAt(1.0, QColor(0, 0, 0, 120))
        p.setBrush(QBrush(scrim))
        p.setPen(Qt.NoPen)
        p.drawRect(r)
        p.restore()
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
        self.palette_ = self._effective_palette()
        self.on_appearance_changed()
        self.changed.emit()
        self.update()

    def reset_appearance(self) -> None:
        for key in ("bg", "fg", "fill", "image"):
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
        p = QPainter(self)
        self.paint_card(p)
        p.end()

    def paint_card(self, p: QPainter, opacity: Optional[float] = None) -> None:
        """Draw the whole card onto ``p``: chrome, material, then the content.

        Split out of ``paintEvent`` so a tile can render itself into an offscreen
        image and then read those pixels back. That is the only way to blur what
        is *behind* something - a QWidget's own painter cannot be read from - and
        it is what the media player's frosted transport needs.
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
        # Stacked translucent copies, each a little larger and lower. Cheap,
        # and it lifts the card off the wallpaper the way a real widget sits.
        p.setPen(Qt.NoPen)
        for i in range(SHADOW_LAYERS, 0, -1):
            grow = i * 1.7
            alpha = max(2, int(SHADOW_ALPHA / (i * 0.85)))
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawPath(squircle(
                r.adjusted(-grow, -grow + 2.0, grow, grow + 3.0),
                RADIUS + grow))

        # --- the card -------------------------------------------------- #
        path = squircle(r, RADIUS)

        # Frosted material: the blurred desktop showing through, then a tint on
        # top. The blur is clipped to the squircle, which is the whole reason
        # it's done in-process rather than handed to DWM.
        # Three backgrounds, in order of precedence: a chosen picture, a flat
        # colour, or the frosted desktop. A picture and a solid colour both opt
        # out of the glass entirely - that is the whole point of choosing them.
        image = self._paint_image_bg(p, r, path)
        frosted = False
        if not image and not self.solid_bg and self.glass is not None:
            p.save()
            p.setClipPath(path)
            frosted = self.glass.paint_into(
                p, r, self.x() + int(MARGIN), self.y() + int(MARGIN))
            p.restore()

        if not image:
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

        # A soft inner light along the top - the sheen that makes frosted
        # glass read as glass. It belongs only *on* glass: over a flat colour it
        # is the grey glare that gave a solid card a smudged top edge, and over a
        # picture it would fog the image. So it is skipped for both.
        if not self.solid_bg and not image:
            p.save()
            p.setClipPath(path)
            sheen = QLinearGradient(r.left(), r.top(), r.left(), r.top() + 46)
            sheen.setColorAt(0.0, QColor(255, 255, 255, 22 if pal.dark else 90))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(sheen))
            p.drawRect(r)
            p.restore()

        if self._hover and not self.locked:
            ring = QColor(pal.accent)
            ring.setAlpha(110)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.5))
            p.drawPath(path)
            p.setPen(Qt.NoPen)

        inner = QRectF(r.left() + PAD, r.top() + PAD,
                       r.width() - PAD * 2, r.height() - PAD * 2)
        p.setBrush(Qt.NoBrush)
        p.setPen(Qt.NoPen)
        self.paint_content(p, inner)

    # -- painting helpers shared by every tile --------------------------- #
    def font(self, size: float, weight: int = QFont.Normal) -> QFont:
        """Apple-set type: optical size picked by point size, plus SF's
        size-dependent tracking. See :mod:`widgets.core.typography`."""
        return typography.font(size, weight)

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
                self._drag_from = ev.globalPosition().toPoint() - self.pos()
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_from is None:
            return
        self.move(ev.globalPosition().toPoint() - self._drag_from)
        # The frosted slice is sampled by screen position, so it has to be
        # repainted as the card travels or the blur would slide with it.
        self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag_from is None:
            return
        self._drag_from = None
        self.settle()
        self.moved.emit()
        ev.accept()

    def settle(self) -> None:
        """Drop the card where it was let go.

        Free placement is the default - grid snapping fights you when you're
        trying to line something up against a wallpaper feature or another
        window. Snapping is available, but opt-in via the tray menu.

        The only thing always enforced is that the card stays reachable on a
        monitor. Bounds are the *card*, not the window, so its shadow gutter is
        allowed to hang off the screen edge and the card can sit flush.
        """
        m = int(MARGIN)
        cw = self.width() - m * 2
        ch = self.height() - m * 2 - int(self.extra_bottom)
        cx, cy = self.x() + m, self.y() + m
        wl, wt, wr, wb = winapi.work_area_at(cx + cw // 2, cy + ch // 2)

        if self.snap_to_grid:
            cx = wl + round((cx - wl) / STEP) * STEP
            cy = wt + round((cy - wt) / STEP) * STEP

        cx = max(wl, min(cx, wr - cw))
        cy = max(wt, min(cy, wb - ch))
        self.move(int(cx - m), int(cy - m))

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
        if self.customizable:
            from . import appearance
            appearance.add_menu(self, m)
        if not m.actions()[-1].isSeparator():
            m.addSeparator()

        rm = QAction("Remove widget", m)
        rm.triggered.connect(lambda: self.removed.emit(self))
        m.addAction(rm)
        return m
