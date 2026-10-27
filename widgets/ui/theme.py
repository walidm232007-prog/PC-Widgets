"""
theme.py
========
The look of the app window and its dialogs.

Solid, and shaped like the widgets. The window is pure black in the dark theme
and pure white in the light one; everything on it - the sidebar, the gallery's
cards, the grouped settings lists - is a panel of flat grey, a few steps off
the window's colour, with the widgets' own corner: the same continuous curve at
the same radius (see ``smooth``). A panel inside another follows its curve
concentrically, its radius shrunk by the gap between them, so nested corners
run parallel instead of pinching. No translucency, no blur, no gradients: on a
flat colour the only things left to read are the shapes and the type, so both
have to be exact.

The greys are neutral rather than bluish, and the label colours are white or
black at descending alpha (see typography.py) rather than separate greys, so
text sits correctly on the window and on every panel alike.

Controls Qt has no good-looking version of are drawn here: buttons, a segmented
control, a switch, colour swatches, keycaps and the coloured icon tiles of a
settings list. Buttons and the other small controls are capsules. Every
interactive one eases between states rather than snapping, which costs little
and is a large part of why native apps feel expensive.

There is one active theme at a time, held here rather than passed to every
control, so a theme change is one call and a repaint.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QElapsedTimer, QPointF, QRectF, QSize, Qt,
    QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QConicalGradient, QFont, QFontMetricsF, QGuiApplication, QImage,
    QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QColorDialog, QHBoxLayout, QLineEdit,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import typography
from ..core.card import RADIUS, squircle
from ..core.palette import Palette, _mix

# Panels share the widgets' corner exactly. INSET is the gap between the
# window's edge and a panel inside it (the sidebar), whose own corner is the
# window's less that gap, so the two curves stay parallel.
CORNER = RADIUS
INSET = 8.0


def rgba(r: int, g: int, b: int, a: float = 1.0) -> str:
    """A colour as ``#AARRGGBB``, which QColor reads directly."""
    return "#%02x%02x%02x%02x" % (round(a * 255), r, g, b)


def css(colour: str) -> str:
    """``#AARRGGBB`` as stylesheet ``rgba()`` - QSS reads alpha only that way."""
    c = QColor(colour)
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def mix(a: str, b: str, t: float) -> QColor:
    """Blend two colours, alpha included."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t),
                  round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t),
                  round(ca.alpha() + (cb.alpha() - ca.alpha()) * t))


@dataclass(frozen=True)
class Ui:
    dark: bool
    accent: str
    base: str            # the window: black, or white
    surface: str         # panels on it: the sidebar, cards, grouped lists
    surface_hi: str      # ... hovered
    edge: str            # hairline round a panel
    frame: str           # hairline round the window itself
    well: str            # behind a widget preview; a step away from the widgets' own fill
    control: str         # button and track fills, on a panel
    control_hi: str
    pill: str            # the selected segment, the selected nav item
    line: str            # separators
    text: str
    text_dim: str
    text_faint: str
    danger: str
    good: str
    hud: str             # floating toolbars and toasts
    shadow: int          # the window's drop shadow, 0-255

    @property
    def accent_soft(self) -> str:
        c = QColor(self.accent)
        return rgba(c.red(), c.green(), c.blue(), 0.16 if self.dark else 0.12)

    @property
    def accent_text(self) -> str:
        """The accent as text: lifted a little on dark so it reads, not glows."""
        return _mix(self.accent, "#ffffff", 0.18) if self.dark else self.accent


def build(palette: Palette) -> Ui:
    # Opaque greys throughout, so a panel is the same colour over the window,
    # over another panel or in a cached picture. The well behind a preview is
    # lighter than the panel in the dark theme and darker in the light one:
    # either way, away from the widgets' own near-black or white.
    if palette.dark:
        return Ui(dark=True, accent=palette.accent,
                  base="#000000", surface="#141416", surface_hi="#1b1b1e",
                  edge=rgba(255, 255, 255, 0.06), frame=rgba(255, 255, 255, 0.13),
                  well="#1e1e21",
                  control="#232327", control_hi="#2d2d32", pill="#3d3d43",
                  line=rgba(255, 255, 255, 0.08),
                  text="#f5f5f7", text_dim=rgba(235, 235, 245, 0.60),
                  text_faint=rgba(235, 235, 245, 0.30),
                  danger="#ff453a", good="#30d158",
                  hud="#1c1c1f", shadow=150)
    return Ui(dark=False, accent=palette.accent,
              base="#ffffff", surface="#f4f4f6", surface_hi="#eeeef1",
              edge=rgba(0, 0, 0, 0.05), frame=rgba(0, 0, 0, 0.11),
              well="#e6e6ea",
              control="#e6e6ea", control_hi="#dcdce1", pill="#ffffff",
              line=rgba(0, 0, 0, 0.08),
              text="#1d1d1f", text_dim=rgba(60, 60, 67, 0.64),
              text_faint=rgba(60, 60, 67, 0.34),
              danger="#ff3b30", good="#34c759",
              hud="#1c1c1f", shadow=80)


_current: Optional[Ui] = None
_generation = 0         # bumped on every theme change; cached paints key on it


def current() -> Ui:
    global _current
    if _current is None:
        from ..core.palette import build as build_palette
        _current = build(build_palette())
    return _current


def set_current(ui: Ui) -> None:
    global _current, _generation
    _current = ui
    _generation += 1


# --------------------------------------------------------------------------- #
#  Type
# --------------------------------------------------------------------------- #
def font(size: float = 10.0, weight=QFont.Normal) -> QFont:
    """The cards' type - SF optical sizes and tracking - so the window and the
    widgets read as one family."""
    return typography.font(size, weight)


def stylesheet(ui: Ui) -> str:
    """For the stock controls that remain. Fonts are set in code, never here: a
    stylesheet font overrides every ``setFont`` below it, headings included."""
    thumb = rgba(255, 255, 255, 0.18) if ui.dark else rgba(0, 0, 0, 0.20)
    thumb_hi = rgba(255, 255, 255, 0.32) if ui.dark else rgba(0, 0, 0, 0.34)
    return f"""
    QLabel {{ color: {ui.text}; background: transparent; }}
    QLabel[tone="dim"] {{ color: {css(ui.text_dim)}; }}
    QLabel[tone="faint"] {{ color: {css(ui.text_faint)}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 3px 4px 3px; }}
    QScrollBar::handle:vertical {{ background: {css(thumb)}; border-radius: 3px;
                                   min-height: 40px; }}
    QScrollBar::handle:vertical:hover {{ background: {css(thumb_hi)}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}
    QSlider {{ background: transparent; min-height: 22px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {css(ui.control_hi)};
                                  border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ui.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #ffffff; width: 18px; height: 18px;
                                  margin: -7px 0; border-radius: 9px;
                                  border: 1px solid rgba(0,0,0,0.20); }}
    QToolTip {{ background: {ui.surface}; color: {ui.text};
                border: 1px solid {css(ui.line)}; padding: 5px 9px; }}
    """


def qpalette(ui: Ui) -> QPalette:
    """A Qt palette in the theme's colours, for whatever fills itself from one.

    Without it those pick up Windows' own palette - dark whenever the system is,
    whatever the app's theme says.
    """
    pal = QPalette()
    for role, colour in ((QPalette.Window, ui.base), (QPalette.Base, ui.surface),
                         (QPalette.AlternateBase, ui.surface),
                         (QPalette.Button, ui.surface),
                         (QPalette.WindowText, ui.text), (QPalette.Text, ui.text),
                         (QPalette.ButtonText, ui.text),
                         (QPalette.PlaceholderText, ui.text_faint),
                         (QPalette.ToolTipBase, ui.surface),
                         (QPalette.ToolTipText, ui.text),
                         (QPalette.Highlight, ui.accent),
                         (QPalette.HighlightedText, "#ffffff")):
        pal.setColor(role, QColor(colour))
    return pal


SCROLLBAR_QSS = """
QScrollBar:vertical { background: transparent; width: 12px; margin: 4px 3px 4px 3px; }
QScrollBar::handle:vertical { background: rgba(140,140,150,0.42); border-radius: 3px;
                              min-height: 40px; }
QScrollBar::handle:vertical:hover { background: rgba(140,140,150,0.70); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class SmoothScrollArea(QScrollArea):
    """A scroll area whose mouse wheel glides instead of jumping.

    Qt moves the content a fixed 60 px the instant a wheel notch lands, which
    reads as stepping rather than scrolling. Here each notch moves a *target*
    and the view eases toward it - time-based, so the glide takes the same time
    at any frame rate - with ticks at the refresh rate of the screen the window
    is on: 5 ms on a 200 Hz panel, 16 ms on a 60 Hz one. Notches arriving
    mid-glide extend it rather than restarting it, a reversal turns round on the
    spot, and dragging the scrollbar or using the keyboard cancels the glide and
    takes over.

    ``scrolling()`` lets the page hold back work that would cost a frame - a
    thumbnail refresh, a hover lift - until the motion has settled, and
    ``settled`` says when that is.
    """

    STEP = 110.0            # pixels per wheel notch
    TAU = 0.070             # seconds; the glide's time constant
    MIN_SPEED = 300.0       # px/s; finishes the tail instead of creeping it

    settled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pos = 0.0
        self._target = 0.0
        self._ours = False
        self._last_wheel = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._step)
        bar = self.verticalScrollBar()
        bar.setStyleSheet(SCROLLBAR_QSS)
        bar.valueChanged.connect(self._external)

    def scrolling(self) -> bool:
        return self._timer.isActive() or time.monotonic() - self._last_wheel < 0.3

    def _tick_ms(self) -> int:
        handle = self.window().windowHandle()
        screen = handle.screen() if handle is not None else QGuiApplication.primaryScreen()
        hz = screen.refreshRate() if screen is not None else 60.0
        return max(4, int(1000.0 / min(max(hz, 30.0), 240.0)))

    def wheelEvent(self, ev) -> None:
        dy = ev.pixelDelta().y() or ev.angleDelta().y() / 120.0 * self.STEP
        if not dy or ev.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            super().wheelEvent(ev)
            return
        bar = self.verticalScrollBar()
        delta = -dy
        if not self._timer.isActive():
            self._pos = self._target = float(bar.value())
        elif (delta > 0) != (self._target > self._pos):
            self._target = self._pos            # turned round: go from here
        self._target = max(float(bar.minimum()), min(float(bar.maximum()),
                                                     self._target + delta))
        self._last_wheel = time.monotonic()
        if not self._timer.isActive() and self._target != self._pos:
            self._clock.start()
            self._timer.start(self._tick_ms())
        ev.accept()

    def _step(self) -> None:
        bar = self.verticalScrollBar()
        dt = min(0.05, self._clock.restart() / 1000.0)
        self._target = min(self._target, float(bar.maximum()))
        gap = self._target - self._pos
        move = gap * (1.0 - math.exp(-dt / self.TAU))
        # A pure exponential spends its last few hundred milliseconds creeping
        # one pixel at a time, which reads as sluggish; below a walking pace
        # the glide just finishes at that pace.
        floor = self.MIN_SPEED * dt
        if abs(move) < floor:
            move = math.copysign(min(floor, abs(gap)), gap)
        self._pos += move
        if abs(self._target - self._pos) < 0.5:
            self._pos = self._target
            self._timer.stop()
        self._ours = True
        bar.setValue(round(self._pos))
        self._ours = False
        if not self._timer.isActive():
            self.settled.emit()

    def _external(self, value: int) -> None:
        if not self._ours and self._timer.isActive():
            self._timer.stop()
            self.settled.emit()
        if not self._ours:
            self._pos = self._target = float(value)


def scroll_area(content: QWidget) -> SmoothScrollArea:
    """A frameless, transparent, vertically scrolling area around ``content``."""
    area = SmoothScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(content)
    # After setWidget, not before: it switches the content's fill back on.
    area.viewport().setAutoFillBackground(False)
    content.setAutoFillBackground(False)
    return area


class Cached:
    """Paint into a pixmap once, and blit it until something visible changes.

    Scrolling repaints every widget in view on every frame, and at 200 Hz a
    frame is 5 ms - not enough to redraw a dozen cards of gradients, paths and
    text from Python each time, however little has changed. A widget mixing
    this in says what it currently looks like (``paint_key``) and how to draw
    itself (``paint``); while the key is unchanged a repaint is one blit. While
    it is animating it returns None and paints directly, since a cache that
    changes on every frame only adds a copy. The key is combined with the
    widget's size and the theme generation, so neither can serve a stale paint.
    """

    def paint_key(self):
        return None

    def paint(self, p: QPainter) -> None:
        raise NotImplementedError

    def paintEvent(self, _ev) -> None:
        key = self.paint_key()
        if key is None:
            p = QPainter(self)
            self.paint(p)
            p.end()
            return
        key = (key, self.width(), self.height(), _generation)
        cache = getattr(self, "_paint_cache", None)
        if cache is None or self._paint_cache_key != key:
            dpr = self.devicePixelRatioF()
            cache = QPixmap(max(1, round(self.width() * dpr)),
                            max(1, round(self.height() * dpr)))
            cache.setDevicePixelRatio(dpr)
            cache.fill(Qt.transparent)
            cp = QPainter(cache)
            self.paint(cp)
            cp.end()
            self._paint_cache, self._paint_cache_key = cache, key
        p = QPainter(self)
        p.drawPixmap(0, 0, cache)
        p.end()


def running(anim: QVariantAnimation) -> bool:
    return anim.state() == QAbstractAnimation.Running


class Text(Cached, QWidget):
    """A line or paragraph of text in the theme's type, painted from a cache.

    Stands in for QLabel inside the window. A QLabel restyles and re-lays-out
    its text on every paint, and inside a scrolling page that is every frame;
    this draws the text once and blits it. ``tone`` is None, "dim", "faint" or
    "danger"; ``wrap`` makes it a paragraph that takes its height from its
    width; ``elide`` lets a long single line shrink with an ellipsis.
    """

    def __init__(self, text: str = "", size: float = 10.0, weight=QFont.Normal,
                 tone: Optional[str] = None, wrap: bool = False,
                 align=Qt.AlignLeft | Qt.AlignVCenter, elide: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._font = font(size, weight)
        self._font_key = self._font.key()       # asked for on every paint; slow to build
        self._tone = tone
        self._wrap = wrap
        self._align = align
        self._elide = elide
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        if wrap:
            policy.setHeightForWidth(True)
        if elide:
            policy.setHorizontalPolicy(QSizePolicy.Ignored)
        self.setSizePolicy(policy)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.updateGeometry()
            self.update()

    def setAlignment(self, align) -> None:
        self._align = align
        self.update()

    def _ink(self) -> QColor:
        ui = current()
        return QColor({"dim": ui.text_dim, "faint": ui.text_faint,
                       "danger": ui.danger}.get(self._tone, ui.text))

    def _margins(self) -> Tuple[int, int]:
        m = self.contentsMargins()
        return m.left() + m.right(), m.top() + m.bottom()

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self._font)
        mw, mh = self._margins()
        if self._wrap:
            w = min(fm.horizontalAdvance(self._text), 420.0)
            return QSize(math.ceil(w) + mw, self.heightForWidth(math.ceil(w) + mw))
        return QSize(math.ceil(fm.horizontalAdvance(self._text)) + 2 + mw,
                     math.ceil(fm.height()) + mh)

    def minimumSizeHint(self) -> QSize:
        if self._wrap or self._elide:
            fm = QFontMetricsF(self._font)
            mw, mh = self._margins()
            return QSize(40 + mw, math.ceil(fm.height()) + mh)
        return self.sizeHint()

    def hasHeightForWidth(self) -> bool:
        return self._wrap

    def heightForWidth(self, width: int) -> int:
        mw, mh = self._margins()
        r = QFontMetricsF(self._font).boundingRect(
            QRectF(0, 0, max(1, width - mw), 100000), int(Qt.TextWordWrap | self._align),
            self._text)
        return math.ceil(r.height()) + mh

    def paint_key(self):
        m = self.contentsMargins()
        return (self._text, self._font_key, self._tone, int(self._align), self._wrap,
                self.isEnabled(), m.left(), m.top(), m.right(), m.bottom())

    def paint(self, p: QPainter) -> None:
        hints(p)
        p.setFont(self._font)
        p.setPen(self._ink() if self.isEnabled() else QColor(current().text_faint))
        r = QRectF(self.contentsRect())
        if self._wrap:
            p.drawText(r, int(self._align | Qt.TextWordWrap), self._text)
        else:
            p.drawText(r, self._align, p.fontMetrics().elidedText(
                self._text, Qt.ElideRight, int(r.width())))


def hints(p: QPainter) -> QPainter:
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    return p


def eased(widget: QWidget, ms: int = 160) -> QVariantAnimation:
    """A 0..1 animation that repaints ``widget`` as it runs. Read ``.t``."""
    anim = QVariantAnimation(widget)
    anim.setDuration(ms)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.t = 0.0

    def step(v):
        anim.t = float(v)
        widget.update()

    anim.valueChanged.connect(step)
    return anim


def ease_to(anim: QVariantAnimation, target: float) -> None:
    if anim.state() != QAbstractAnimation.Running and abs(anim.t - target) < 1e-3:
        return              # already there; an idle animation would only cost paints
    anim.stop()
    anim.setStartValue(anim.t)
    anim.setEndValue(float(target))
    anim.start()


# --------------------------------------------------------------------------- #
#  Shapes, shadows, panels
# --------------------------------------------------------------------------- #
def smooth(rect: QRectF, radius: float = CORNER) -> QPainterPath:
    """A panel's outline: the widgets' continuous corner (card.squircle).

    The window, the sidebar, the cards and the grouped lists are all cut with
    this, so the app reads as made of the same pieces as the widgets it places.
    """
    return squircle(rect, radius)


_shadows: Dict[tuple, Tuple[QImage, int]] = {}


def _blur_alpha(alpha, radius: int, passes: int = 3):
    """Separable box blur of a 2-D numpy array; three passes ~ a Gaussian."""
    import numpy as np
    buf = alpha.astype(np.float32)
    h, w = buf.shape
    k = radius * 2 + 1
    for _ in range(passes):
        pad = np.pad(buf, ((0, 0), (radius, radius)), mode="constant")
        cs = np.cumsum(pad, axis=1)
        cs = np.concatenate([np.zeros((h, 1), np.float32), cs], axis=1)
        buf = (cs[:, k:] - cs[:, :-k]) / k
        pad = np.pad(buf, ((radius, radius), (0, 0)), mode="constant")
        cs = np.cumsum(pad, axis=0)
        cs = np.concatenate([np.zeros((1, w), np.float32), cs], axis=0)
        buf = (cs[k:, :] - cs[:-k, :]) / k
    return buf


def shadow_image(w: float, h: float, radius: float, blur: int,
                 alpha: int) -> Optional[Tuple[QImage, int]]:
    """A soft drop shadow for a ``w`` x ``h`` rounded rectangle: (image, pad).

    A real blur, rendered once per size and kept - the stacked-outline trick the
    cards use is fine at their size, but under a large panel its steps show.
    Draw the image at the shape's position minus ``pad`` on both axes.
    """
    key = (round(w), round(h), round(radius, 1), int(blur), int(alpha))
    hit = _shadows.get(key)
    if hit is not None:
        return hit
    try:
        import numpy as np
    except Exception:
        return None
    pad = int(blur * 2)
    W, H = round(w) + pad * 2, round(h) + pad * 2
    if W <= 0 or H <= 0:
        return None
    shape = QImage(W, H, QImage.Format_Alpha8)
    shape.fill(0)
    p = QPainter(shape)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 255))
    p.drawRoundedRect(QRectF(pad, pad, round(w), round(h)), radius, radius)
    p.end()
    bpl = shape.bytesPerLine()
    raw = np.frombuffer(shape.constBits(), dtype=np.uint8, count=bpl * H)
    a = raw.reshape(H, bpl)[:, :W]
    a = _blur_alpha(a, max(1, blur // 2)) * (alpha / 255.0)
    px = np.zeros((H, W, 4), dtype=np.uint8)
    px[..., 3] = np.clip(a, 0, 255).astype(np.uint8)
    img = QImage(px.data, W, H, W * 4, QImage.Format_RGBA8888_Premultiplied).copy()
    if len(_shadows) > 96:
        _shadows.clear()
    _shadows[key] = (img, pad)
    return _shadows[key]


def paint_shadow(p: QPainter, rect: QRectF, radius: float, blur: int = 18,
                 alpha: int = 90, dy: float = 6.0) -> None:
    hit = shadow_image(rect.width(), rect.height(), radius, blur, alpha)
    if hit is None:
        # Without numpy: a few stacked outlines, the way the cards do it.
        p.save()
        p.setPen(Qt.NoPen)
        for i in range(5, 0, -1):
            p.setBrush(QColor(0, 0, 0, max(2, alpha // (i * 3))))
            g = i * blur / 6.0
            p.drawRoundedRect(rect.adjusted(-g, -g + dy, g, g + dy), radius + g, radius + g)
        p.restore()
        return
    img, pad = hit
    p.drawImage(QPointF(round(rect.x() - pad), round(rect.y() - pad + dy)), img)


def rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_surface(p: QPainter, rect: QRectF, radius: float = CORNER, hover: float = 0.0,
                  fill: Optional[QColor] = None, edge: bool = True) -> None:
    """A panel: flat grey in the widgets' shape, with a hairline round it.

    ``hover`` (0..1) lifts the grey a step toward the text colour and firms up
    the hairline, which is faint enough at rest to vanish against the window
    and still sharpens the curve, which a flat fill alone leaves soft.
    """
    ui = current()
    p.fillPath(smooth(rect, radius),
               fill if fill is not None else mix(ui.surface, ui.surface_hi, hover))
    if edge:
        line = QColor(ui.edge)
        line.setAlphaF(min(1.0, line.alphaF() * (1.0 + 1.5 * hover)))
        p.save()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(line, 1.0))
        p.drawPath(smooth(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5))
        p.restore()


# The window's own shadow falls in a transparent margin this wide round it.
WINDOW_SHADOW = 24
_window_shadows: Dict[tuple, Tuple[QImage, int, int]] = {}


def _window_shadow(radius: float, strength: int) -> Optional[Tuple[QImage, int, int]]:
    """The window's drop shadow as a nine-slice: (image, pad, k).

    A window can be any size, and blurring a full-size mask on every step of a
    resize costs tens of milliseconds a step. But a shadow is the same all the
    way along a straight edge, so it is rendered once, round a small square with
    the window's corners whose sides are long enough for their middles to be a
    true straight-edge shadow, and then stretched: the four corners copied, and
    the middle pixel of each side drawn out along the window's edge.

    Two layers, as a real shadow has: a broad soft one, and a tight one that
    seats the edge. Alpha too faint to see is cut to nothing, which matters more
    than it looks: in a layered window every pixel that isn't fully transparent
    takes the mouse, so an invisible fringe would swallow clicks meant for
    whatever is behind the window.
    """
    key = (round(radius, 1), int(strength))
    hit = _window_shadows.get(key)
    if hit is not None:
        return hit
    try:
        import numpy as np
    except Exception:
        return None
    pad = WINDOW_SHADOW
    span = int(radius * 1.6) + 24           # the curve, then ~3 sigma of straight edge
    side = span * 2 + 1
    n = side + pad * 2

    def layer(dy: float, blur: int, alpha: float):
        shape = QImage(n, n, QImage.Format_Alpha8)
        shape.fill(0)
        sp = QPainter(shape)
        sp.setRenderHint(QPainter.Antialiasing, True)
        sp.setPen(Qt.NoPen)
        sp.setBrush(QColor(0, 0, 0, 255))
        sp.drawPath(squircle(QRectF(pad, pad + dy, side, side), radius))
        sp.end()
        bpl = shape.bytesPerLine()
        raw = np.frombuffer(shape.constBits(), dtype=np.uint8, count=bpl * n)
        return _blur_alpha(raw.reshape(n, bpl)[:, :n], blur) / 255.0 * alpha

    soft = layer(4.0, 7, strength / 255.0)
    tight = layer(1.0, 1, strength / 255.0 * 0.5)
    a = (1.0 - (1.0 - soft) * (1.0 - tight)) * 255.0
    a[a < 3.0] = 0.0
    px = np.zeros((n, n, 4), dtype=np.uint8)
    px[..., 3] = np.clip(a + 0.5, 0, 255).astype(np.uint8)
    img = QImage(px.data, n, n, n * 4, QImage.Format_RGBA8888_Premultiplied).copy()
    _window_shadows[key] = (img, pad, pad + span)
    return _window_shadows[key]


def paint_window_shadow(p: QPainter, rect: QRectF, radius: float, strength: int) -> None:
    """The shadow of a window whose visible body is ``rect``, falling into the
    ``WINDOW_SHADOW`` margin round it. The middle, under the body, is skipped."""
    hit = _window_shadow(radius, strength)
    if hit is None:
        return
    img, pad, k = hit
    x0, y0 = round(rect.left()) - pad, round(rect.top()) - pad
    x1, y1 = round(rect.right()) + pad, round(rect.bottom()) + pad
    w, h = x1 - x0 - 2 * k, y1 - y0 - 2 * k
    if w < 0 or h < 0:
        return
    far = k + 1                          # where the far corners start in the image
    for sx, sy, dx, dy, dw, dh in (
            (0, 0, x0, y0, k, k), (far, 0, x1 - k, y0, k, k),
            (0, far, x0, y1 - k, k, k), (far, far, x1 - k, y1 - k, k, k)):
        p.drawImage(QRectF(dx, dy, dw, dh), img, QRectF(sx, sy, k, k))
    for sx, sy, sw, sh, dx, dy, dw, dh in (
            (k, 0, 1, k, x0 + k, y0, w, k), (k, far, 1, k, x0 + k, y1 - k, w, k),
            (0, k, k, 1, x0, y0 + k, k, h), (far, k, k, 1, x1 - k, y0 + k, k, h)):
        p.drawImage(QRectF(dx, dy, dw, dh), img, QRectF(sx, sy, sw, sh))


# --------------------------------------------------------------------------- #
#  Glyphs
# --------------------------------------------------------------------------- #
def _pen(colour, width: float = 1.6) -> QPen:
    return QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def glyph(p: QPainter, kind: str, r: QRectF, colour, width: float = 1.6) -> None:
    """Small line icons, drawn rather than loaded - the app ships no UI images.

    Drawn in a box ``r`` (any size; designed at 16-18px), in one colour, on a
    shared stroke weight so a row of them reads as a set.
    """
    c = QColor(colour)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(Qt.NoBrush)
    p.setPen(_pen(c, width))
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    cx, cy = r.center().x(), r.center().y()

    def P(fx, fy):
        return QPointF(x + fx * w, y + fy * h)

    if kind == "plus":
        p.drawLine(P(0.5, 0.18), P(0.5, 0.82))
        p.drawLine(P(0.18, 0.5), P(0.82, 0.5))
    elif kind == "check":
        path = QPainterPath(P(0.18, 0.52))
        path.lineTo(P(0.42, 0.75))
        path.lineTo(P(0.84, 0.28))
        p.drawPath(path)
    elif kind == "search":
        p.drawEllipse(QRectF(x + w * 0.12, y + h * 0.12, w * 0.56, h * 0.56))
        p.drawLine(P(0.60, 0.60), P(0.88, 0.88))
    elif kind == "gallery":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        s = (w - 3.0) / 2.0
        for i in (0, 1):
            for j in (0, 1):
                p.drawRoundedRect(QRectF(x + i * (s + 3), y + j * (s + 3), s, s), 2.6, 2.6)
    elif kind == "desktop":
        screen = QRectF(x + 0.8, y + h * 0.10, w - 1.6, h * 0.62)
        p.drawRoundedRect(screen, 2.6, 2.6)
        p.drawLine(QPointF(cx, screen.bottom()), QPointF(cx, y + h * 0.88))
        p.drawLine(QPointF(cx - w * 0.24, y + h * 0.90), QPointF(cx + w * 0.24, y + h * 0.90))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(screen.right() - w * 0.36, screen.top() + h * 0.12,
                                 w * 0.24, h * 0.22), 1.4, 1.4)
    elif kind in ("settings", "sliders"):
        for i, at in enumerate((0.70, 0.30, 0.58)):
            yy = y + h * (0.20 + 0.30 * i)
            p.setPen(_pen(c, width))
            p.drawLine(QPointF(x + 1, yy), QPointF(x + w - 1, yy))
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x + at * w, yy), w * 0.13, w * 0.13)
    elif kind == "moon":
        outer = QPainterPath()
        outer.addEllipse(QRectF(x + w * 0.14, y + h * 0.14, w * 0.72, h * 0.72))
        bite = QPainterPath()
        bite.addEllipse(QRectF(x + w * 0.40, y + h * 0.02, w * 0.62, h * 0.62))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(outer.subtracted(bite))
    elif kind == "drop":
        path = QPainterPath(P(0.5, 0.10))
        path.cubicTo(P(0.5, 0.10), P(0.82, 0.46), P(0.82, 0.62))
        path.cubicTo(P(0.82, 0.82), P(0.66, 0.92), P(0.5, 0.92))
        path.cubicTo(P(0.34, 0.92), P(0.18, 0.82), P(0.18, 0.62))
        path.cubicTo(P(0.18, 0.46), P(0.5, 0.10), P(0.5, 0.10))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(path)
    elif kind == "halfcircle":
        box = QRectF(x + w * 0.14, y + h * 0.14, w * 0.72, h * 0.72)
        p.drawEllipse(box)
        half = QPainterPath()
        half.moveTo(box.center())
        half.arcTo(box, 90, 180)
        half.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(half)
    elif kind == "layers":
        for i, dy in enumerate((0.0, 0.22)):
            path = QPainterPath(P(0.5, 0.18 + dy))
            path.lineTo(P(0.90, 0.38 + dy))
            path.lineTo(P(0.5, 0.58 + dy))
            path.lineTo(P(0.10, 0.38 + dy))
            path.closeSubpath()
            if i == 0:
                p.save()
                p.setBrush(c)
                p.setPen(Qt.NoPen)
                p.drawPath(path)
                p.restore()
            else:
                p.drawPath(path)
    elif kind == "grid":
        s = w * 0.30
        for i in (0, 1):
            for j in (0, 1):
                p.drawRoundedRect(QRectF(x + w * 0.16 + i * (s + w * 0.08),
                                         y + h * 0.16 + j * (s + h * 0.08), s, s), 1.6, 1.6)
    elif kind == "lock":
        body = QRectF(x + w * 0.20, y + h * 0.44, w * 0.60, h * 0.44)
        p.drawArc(QRectF(x + w * 0.32, y + h * 0.12, w * 0.36, h * 0.52), 0, 180 * 16)
        p.drawLine(P(0.32, 0.38), P(0.32, 0.46))
        p.drawLine(P(0.68, 0.38), P(0.68, 0.46))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(body, 2.2, 2.2)
    elif kind == "front":
        p.drawRoundedRect(QRectF(x + w * 0.14, y + h * 0.30, w * 0.56, h * 0.56), 2.2, 2.2)
        p.drawLine(P(0.48, 0.52), P(0.86, 0.14))
        p.drawLine(P(0.60, 0.14), P(0.86, 0.14))
        p.drawLine(P(0.86, 0.14), P(0.86, 0.40))
    elif kind == "power":
        p.drawArc(QRectF(x + w * 0.16, y + h * 0.18, w * 0.68, h * 0.68), 125 * 16, 290 * 16)
        p.drawLine(P(0.5, 0.10), P(0.5, 0.46))
    elif kind == "link":
        p.drawRoundedRect(QRectF(x + w * 0.14, y + h * 0.14, w * 0.72, h * 0.72), 2.4, 2.4)
        p.drawLine(P(0.38, 0.62), P(0.66, 0.34))
        p.drawLine(P(0.44, 0.34), P(0.66, 0.34))
        p.drawLine(P(0.66, 0.34), P(0.66, 0.56))
    elif kind == "save":
        p.drawRoundedRect(QRectF(x + w * 0.16, y + h * 0.16, w * 0.68, h * 0.68), 2.4, 2.4)
        path = QPainterPath(P(0.32, 0.52))
        path.lineTo(P(0.46, 0.66))
        path.lineTo(P(0.70, 0.38))
        p.drawPath(path)
    elif kind == "folder":
        path = QPainterPath(P(0.10, 0.30))
        path.lineTo(P(0.40, 0.30))
        path.lineTo(P(0.48, 0.40))
        path.lineTo(P(0.90, 0.40))
        path.lineTo(P(0.90, 0.82))
        path.lineTo(P(0.10, 0.82))
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(path)
    elif kind == "photo":
        p.drawRoundedRect(QRectF(x + w * 0.10, y + h * 0.18, w * 0.80, h * 0.64), 2.4, 2.4)
        path = QPainterPath(P(0.16, 0.76))
        path.lineTo(P(0.40, 0.50))
        path.lineTo(P(0.56, 0.64))
        path.lineTo(P(0.66, 0.54))
        path.lineTo(P(0.84, 0.74))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(P(0.68, 0.36), w * 0.07, w * 0.07)
    elif kind == "crop":
        p.drawLine(P(0.28, 0.08), P(0.28, 0.72))
        p.drawLine(P(0.28, 0.72), P(0.92, 0.72))
        p.drawLine(P(0.08, 0.28), P(0.72, 0.28))
        p.drawLine(P(0.72, 0.28), P(0.72, 0.92))
    elif kind == "trash":
        p.drawLine(P(0.18, 0.26), P(0.82, 0.26))
        p.drawLine(P(0.40, 0.14), P(0.60, 0.14))
        path = QPainterPath(P(0.26, 0.26))
        path.lineTo(P(0.32, 0.88))
        path.lineTo(P(0.68, 0.88))
        path.lineTo(P(0.74, 0.26))
        p.drawPath(path)
    elif kind == "eye":
        path = QPainterPath(P(0.06, 0.5))
        path.quadTo(P(0.5, 0.06), P(0.94, 0.5))
        path.quadTo(P(0.5, 0.94), P(0.06, 0.5))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy), w * 0.14, w * 0.14)
    elif kind == "more":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        for fx in (0.22, 0.5, 0.78):
            p.drawEllipse(P(fx, 0.5), w * 0.08, w * 0.08)
    elif kind == "reset":
        p.drawArc(QRectF(x + w * 0.16, y + h * 0.16, w * 0.68, h * 0.68), 100 * 16, 280 * 16)
        p.drawLine(P(0.40, 0.10), P(0.54, 0.17))
        p.drawLine(P(0.40, 0.10), P(0.46, 0.26))
    elif kind == "minus":
        p.drawLine(P(0.2, 0.5), P(0.8, 0.5))
    p.restore()


# --------------------------------------------------------------------------- #
#  Buttons
# --------------------------------------------------------------------------- #
class Button(Cached, QAbstractButton):
    """A capsule button: a flat fill, eased hover, a pressed state, and an
    optional glyph. With a glyph and no text it is a circle.

    ``kind``: "primary" (accent), "secondary" (quiet fill), "ghost" (no fill
    until hovered, accent text), "danger" (red text), "plain" (dim text) and
    "hud" (light text, for controls floating over a photo in either theme).
    ``pill=False`` gives softer rounded-rectangle corners instead.
    """

    def __init__(self, text: str = "", kind: str = "secondary",
                 glyph_kind: Optional[str] = None, parent=None, height: int = 32,
                 pill: Optional[bool] = None, slot: Optional[Callable] = None,
                 tip: str = "") -> None:
        super().__init__(parent)
        self.setText(text)
        self.kind = kind
        self.glyph_kind = glyph_kind
        self._h = height
        self.pill = True if pill is None else pill
        self._hover = eased(self, 140)
        self._flash: Optional[Tuple[str, str, str]] = None
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if slot is not None:
            self.clicked.connect(slot)
        if tip:
            self.setToolTip(tip)

    def _font(self) -> QFont:
        return font(10, QFont.DemiBold if self.kind == "primary" else QFont.Medium)

    def _label(self) -> Tuple[str, Optional[str]]:
        if self._flash is not None:
            return self._flash[0], self._flash[1]
        return self.text(), self.glyph_kind

    def sizeHint(self) -> QSize:
        text, g = self._label()
        fm = QFontMetricsF(self._font())
        w = fm.horizontalAdvance(self.text()) if self.text() else 0
        if self._flash is not None:
            w = max(w, fm.horizontalAdvance(self._flash[0]))
        gw = 14 if (g or self.glyph_kind) else 0
        gap = 6 if (gw and (self.text() or self._flash)) else 0
        side = 16 if self.pill else 13
        if not self.text() and gw:
            return QSize(self._h, self._h)
        return QSize(math.ceil(w + gw + gap + side * 2), self._h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def setText(self, text: str) -> None:
        super().setText(text)
        self.updateGeometry()

    def flash(self, text: str, glyph_kind: str = "check", colour: Optional[str] = None,
              ms: int = 1600) -> None:
        """Briefly become a confirmation - "Added", with a check - then revert."""
        self._flash = (text, glyph_kind, colour or current().good)
        self.updateGeometry()
        self.update()
        QTimer.singleShot(ms, self._unflash)

    def _unflash(self) -> None:
        self._flash = None
        self.updateGeometry()
        self.update()

    def enterEvent(self, _ev) -> None:
        ease_to(self._hover, 1.0)

    def leaveEvent(self, _ev) -> None:
        ease_to(self._hover, 0.0)

    def paint_key(self):
        if running(self._hover):
            return None
        return (self.text(), self.glyph_kind, self.kind, self.pill, self._flash,
                self.isEnabled(), self.isDown(), self.hasFocus(), round(self._hover.t, 3))

    def paint(self, p: QPainter) -> None:
        ui = current()
        hints(p)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2.0 if self.pill else min(9.0, r.height() / 2.0)
        t = self._hover.t if self.isEnabled() else 0.0
        down = self.isDown()
        path = rounded(r, rad)
        text, g = self._label()

        if self.kind == "primary" or self._flash is not None:
            base = QColor(self._flash[2] if self._flash else ui.accent)
            if not self.isEnabled():
                p.fillPath(path, QColor(ui.control))
                ink = QColor(ui.text_faint)
            else:
                fill = base.darker(112) if down else mix(base.name(), base.lighter(112).name(), t)
                p.fillPath(path, fill)
                ink = QColor("#ffffff")
        else:
            if self.kind == "secondary":
                p.fillPath(path, mix(ui.control, ui.control_hi, t))
                ink = QColor(ui.text if self.isEnabled() else ui.text_faint)
            else:
                hov = QColor(255, 255, 255, 30) if self.kind == "hud" else QColor(ui.control_hi)
                hov.setAlphaF(hov.alphaF() * t)
                p.fillPath(path, hov)
                if self.kind == "danger":
                    ink = QColor(ui.danger)
                elif self.kind == "plain":
                    ink = mix(ui.text_dim, ui.text, t)
                elif self.kind == "hud":
                    ink = mix(rgba(255, 255, 255, 0.78), "#ffffff", t)
                else:
                    ink = QColor(ui.accent_text)
                if not self.isEnabled():
                    ink = QColor(ui.text_faint)
            if down:
                p.fillPath(path, QColor(0, 0, 0, 40 if ui.dark else 18))

        f = self._font()
        p.setFont(f)
        fm = QFontMetricsF(f)
        tw = fm.horizontalAdvance(text) if text else 0.0
        gw = 14.0 if g else 0.0
        gap = 6.0 if (gw and text) else 0.0
        left = r.center().x() - (tw + gw + gap) / 2.0
        if g:
            glyph(p, g, QRectF(left, r.center().y() - 7.0, 14.0, 14.0), ink, 1.7)
        if text:
            p.setPen(ink)
            p.drawText(QRectF(left + gw + gap, r.top(), tw + 2, r.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, text)
        if self.hasFocus():
            ring = QColor(ui.accent)
            ring.setAlpha(170)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.5))
            p.drawPath(rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), rad))


# --------------------------------------------------------------------------- #
#  Segmented control
# --------------------------------------------------------------------------- #
class Segmented(Cached, QWidget):
    """A segmented control: one choice of a few in a capsule track, the
    selected pill sliding to it."""

    changed = Signal(str)

    def __init__(self, options: Iterable[Tuple[str, str]],
                 current_key: Optional[str] = None, parent=None,
                 height: int = 30) -> None:
        super().__init__(parent)
        self.options: List[Tuple[str, str]] = [(str(k), str(v)) for k, v in options]
        keys = [k for k, _ in self.options]
        self._index = keys.index(current_key) if current_key in keys else 0
        self._pill = float(self._index)
        self._hover = -1
        self._h = height
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    @property
    def current(self) -> str:
        return self.options[self._index][0]

    def set_options(self, options: Iterable[Tuple[str, str]],
                    current_key: Optional[str] = None) -> None:
        """Replace the choices - one inspector serves cards with different sizes."""
        self.options = [(str(k), str(v)) for k, v in options]
        keys = [k for k, _ in self.options]
        self._index = keys.index(current_key) if current_key in keys else 0
        self._anim.stop()
        self._pill = float(self._index)
        self._hover = -1
        self.updateGeometry()
        self.update()

    def set_current(self, key: str, emit: bool = False) -> None:
        keys = [k for k, _ in self.options]
        if key not in keys:
            return
        i = keys.index(key)
        if i != self._index:
            self._index = i
            if self.isVisible():
                self._anim.stop()
                self._anim.setStartValue(self._pill)
                self._anim.setEndValue(float(i))
                self._anim.start()
            else:
                self._pill = float(i)
                self.update()
        if emit:
            self.changed.emit(key)

    def _step(self, v) -> None:
        self._pill = float(v)
        self.update()

    def _font(self, selected: bool) -> QFont:
        return font(9.75, QFont.DemiBold if selected else QFont.Medium)

    def _width(self, padding: float) -> int:
        fm = QFontMetricsF(self._font(True))
        seg = max(fm.horizontalAdvance(v) for _, v in self.options) + padding
        return int(seg * len(self.options)) + 4

    def sizeHint(self) -> QSize:
        return QSize(self._width(28), self._h)

    def minimumSizeHint(self) -> QSize:
        # Allowed to tighten up rather than push a neighbour out of a narrow row.
        return QSize(self._width(14), self._h)

    def _hit(self, x: float) -> int:
        n = len(self.options)
        seg = (self.width() - 4) / n
        return max(0, min(n - 1, int((x - 2) // seg)))

    def paint_key(self):
        if running(self._anim):
            return None
        return (tuple(self.options), self._index, round(self._pill, 3), self._hover,
                self.hasFocus(), self.isEnabled())

    def paint(self, p: QPainter) -> None:
        ui = current()
        hints(p)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.fillPath(rounded(r, rad), QColor(ui.control))

        n = len(self.options)
        seg = (r.width() - 4) / n
        pill = QRectF(2 + self._pill * seg, 2, seg, r.height() - 4)
        if not ui.dark:
            paint_shadow(p, pill, rad - 2, blur=4, alpha=40, dy=1)
        p.fillPath(rounded(pill, rad - 2), QColor(ui.pill))

        for i, (_k, label) in enumerate(self.options):
            cell = QRectF(2 + i * seg, 0, seg, r.height())
            sel = i == self._index
            p.setFont(self._font(sel))
            p.setPen(QColor(ui.text if (sel or i == self._hover) else ui.text_dim))
            p.drawText(cell, Qt.AlignCenter, label)

        if self.hasFocus():
            ring = QColor(ui.accent)
            ring.setAlpha(150)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.5))
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), rad, rad)

    def mouseMoveEvent(self, ev) -> None:
        i = self._hit(ev.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _ev) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.set_current(self.options[self._hit(ev.position().x())][0],
                             emit=True)

    def keyPressEvent(self, ev) -> None:
        step = {Qt.Key_Left: -1, Qt.Key_Right: 1}.get(ev.key())
        if step is None:
            return super().keyPressEvent(ev)
        i = max(0, min(len(self.options) - 1, self._index + step))
        self.set_current(self.options[i][0], emit=True)


# --------------------------------------------------------------------------- #
#  Switch
# --------------------------------------------------------------------------- #
class Switch(Cached, QAbstractButton):
    """An iOS switch. Checkable; ``toggled`` is the signal to use."""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self.toggled.connect(self._slide)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setFixedSize(46, 28)

    def sizeHint(self) -> QSize:
        return QSize(46, 28)

    def set_quietly(self, on: bool) -> None:
        """Reflect a value changed elsewhere without emitting ``toggled``."""
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def _slide(self, on: bool) -> None:
        if not self.isVisible():
            self._pos = 1.0 if on else 0.0
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _step(self, v) -> None:
        self._pos = float(v)
        self.update()

    def paint_key(self):
        if running(self._anim):
            return None
        return (self.isChecked(), round(self._pos, 3), self.hasFocus(), self.isEnabled())

    def paint(self, p: QPainter) -> None:
        ui = current()
        hints(p)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        t = self._pos
        track = mix(rgba(120, 120, 128, 0.32) if ui.dark else rgba(120, 120, 128, 0.20),
                    ui.accent, t)
        p.fillPath(rounded(r, r.height() / 2), track)
        d = r.height() - 4
        x = r.left() + 2 + t * (r.width() - d - 4)
        knob = QRectF(x, r.top() + 2, d, d)
        paint_shadow(p, knob, d / 2, blur=4, alpha=80, dy=1.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(knob)
        if self.hasFocus():
            ring = QColor(ui.accent)
            ring.setAlpha(150)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.5))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                              self.height() / 2, self.height() / 2)


# --------------------------------------------------------------------------- #
#  Colour swatches
# --------------------------------------------------------------------------- #
class Swatch(Cached, QAbstractButton):
    """A colour dot. ``mode`` is "colour", "default" (no override) or "custom"."""

    def __init__(self, colour: Optional[str], tip: str, mode: str = "colour",
                 parent=None, size: int = 30) -> None:
        super().__init__(parent)
        self.colour = colour
        self.mode = mode
        self.setCheckable(True)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)
        self._hover = eased(self, 120)

    def enterEvent(self, _ev) -> None:
        ease_to(self._hover, 1.0)

    def leaveEvent(self, _ev) -> None:
        ease_to(self._hover, 0.0)

    def paint_key(self):
        if running(self._hover):
            return None
        return (self.colour, self.mode, self.isChecked(), round(self._hover.t, 3))

    def paint(self, p: QPainter) -> None:
        ui = current()
        hints(p)
        s = self.width()
        grow = 1.2 * self._hover.t
        dot = QRectF(6 - grow, 6 - grow, s - 12 + grow * 2, s - 12 + grow * 2)
        p.setPen(Qt.NoPen)
        if self.mode == "default":
            p.setBrush(QColor(ui.control_hi))
            p.drawEllipse(dot)
            p.setPen(_pen(ui.text_dim, 1.5))
            p.drawLine(QPointF(dot.left() + dot.width() * 0.28, dot.bottom() - dot.height() * 0.28),
                       QPointF(dot.right() - dot.width() * 0.28, dot.top() + dot.height() * 0.28))
        elif self.mode == "custom":
            grad = QConicalGradient(dot.center(), 90)
            for i, hue in enumerate((0, 60, 120, 180, 240, 300, 360)):
                grad.setColorAt(i / 6.0, QColor.fromHsv(hue % 360, 190, 245))
            p.setBrush(grad)
            p.drawEllipse(dot)
            if self.isChecked() and self.colour:
                p.setBrush(QColor(self.colour))
                p.setPen(QPen(QColor("#ffffff"), 1.5))
                p.drawEllipse(dot.adjusted(4.5, 4.5, -4.5, -4.5))
        else:
            p.setBrush(QColor(self.colour or "#000000"))
            p.drawEllipse(dot)
            # A soft highlight on the top and a hairline keep the dot from
            # reading as a hole when it matches the panel.
            shine = QLinearGradient(dot.topLeft(), dot.bottomLeft())
            shine.setColorAt(0.0, QColor(255, 255, 255, 46))
            shine.setColorAt(0.55, QColor(255, 255, 255, 0))
            p.setBrush(shine)
            p.drawEllipse(dot)
            edge = QColor(ui.text)
            edge.setAlpha(46)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(edge, 1.0))
            p.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))
        if self.isChecked():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(ui.accent), 2.0))
            p.drawEllipse(QRectF(1.5, 1.5, s - 3, s - 3))


class SwatchRow(QWidget):
    """"Theme default", a row of colours, and a custom picker. Emits the hex
    chosen, or None for the theme default."""

    picked = Signal(object)

    def __init__(self, swatches, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        self.default = Swatch(None, "Theme default", "default")
        self.default.clicked.connect(lambda: self.picked.emit(None))
        self._add(lay, self.default)
        self.dots: List[Swatch] = []
        for name, hex_colour in swatches:
            s = Swatch(hex_colour, name)
            s.clicked.connect(lambda _c=False, h=hex_colour: self.picked.emit(h))
            self._add(lay, s)
            self.dots.append(s)
        self.custom = Swatch(None, "Custom colour…", "custom")
        self.custom.clicked.connect(self._pick_custom)
        self._add(lay, self.custom)
        lay.addStretch(1)
        self._current: Optional[str] = None

    def _add(self, lay, s: Swatch) -> None:
        self.group.addButton(s)
        lay.addWidget(s)

    def set_current(self, hex_colour: Optional[str]) -> None:
        self._current = hex_colour
        if not hex_colour:
            self.default.setChecked(True)
            self.custom.colour = None
        else:
            match = next((s for s in self.dots
                          if s.colour.lower() == hex_colour.lower()), None)
            if match is not None:
                match.setChecked(True)
                self.custom.colour = None
            else:
                self.custom.colour = hex_colour
                self.custom.setChecked(True)
        for s in [self.default, self.custom, *self.dots]:
            s.update()

    def _pick_custom(self) -> None:
        # Parentless on purpose: a child of the app window would inherit its
        # stylesheet and come out half-themed.
        col = QColorDialog.getColor(QColor(self._current or "#808080"), None,
                                    self.title)
        if col.isValid():
            self.picked.emit(col.name())
        else:
            self.set_current(self._current)       # undo the radio check


# --------------------------------------------------------------------------- #
#  Keycaps, icon tiles, search, grouped lists
# --------------------------------------------------------------------------- #
def paint_keycap(p: QPainter, rect: QRectF, text: str) -> None:
    """A small key - the way a shortcut reads at a glance."""
    ui = current()
    path = rounded(rect, 5.0)
    p.fillPath(path, QColor(ui.control_hi))
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(ui.line), 1.0))
    p.drawPath(rounded(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4.5))
    under = QColor(0, 0, 0, 70 if ui.dark else 30)
    p.setPen(QPen(under, 1.0))
    p.drawLine(QPointF(rect.left() + 4, rect.bottom() - 0.5),
               QPointF(rect.right() - 4, rect.bottom() - 0.5))
    p.setFont(font(7.75, QFont.DemiBold))
    p.setPen(QColor(ui.text_dim))
    p.drawText(rect.adjusted(0, -1, 0, -1), Qt.AlignCenter, text)
    p.restore()


def keycap_width(text: str) -> float:
    return max(20.0, QFontMetricsF(font(7.75, QFont.DemiBold)).horizontalAdvance(text) + 10)


class IconTile(Cached, QWidget):
    """The coloured rounded square in front of a settings row, as on macOS.

    A colour, lighter at the top, with a white glyph; "rainbow" draws the
    accent picker's wheel instead.
    """

    def __init__(self, glyph_kind: str, colour: str, parent=None, size: int = 28) -> None:
        super().__init__(parent)
        self.glyph_kind = glyph_kind
        self.colour = colour
        self.setFixedSize(size, size)

    def paint_key(self):
        return (self.glyph_kind, self.colour)

    def paint(self, p: QPainter) -> None:
        hints(p)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.width() * 0.26
        if self.colour == "rainbow":
            grad = QConicalGradient(r.center(), 90)
            for i, hue in enumerate((0, 60, 120, 180, 240, 300, 360)):
                grad.setColorAt(i / 6.0, QColor.fromHsv(hue % 360, 200, 250))
            p.fillPath(rounded(r, rad), grad)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(r.center(), r.width() * 0.17, r.width() * 0.17)
        else:
            base = QColor(self.colour)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0.0, base.lighter(122))
            grad.setColorAt(1.0, base.darker(108))
            p.fillPath(rounded(r, rad), grad)
            inner = r.adjusted(r.width() * 0.22, r.height() * 0.22,
                               -r.width() * 0.22, -r.height() * 0.22)
            glyph(p, self.glyph_kind, inner, "#ffffff", 1.6)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 40), 1.0))
        p.drawPath(rounded(r, rad))


class SearchField(QLineEdit):
    """A rounded search box with its magnifier drawn in.

    Styled on the field itself rather than from a window-wide stylesheet: any
    stylesheet on a window routes every widget inside it through Qt's slower
    stylesheet style, and inside a scrolling page that is paid on every frame.
    """

    def __init__(self, placeholder: str = "Search", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Search")
        self.setPlaceholderText(placeholder)
        self.setFont(font(10))
        self.setClearButtonEnabled(True)
        self.setFixedHeight(34)
        self.restyle()

    def restyle(self) -> None:
        ui = current()
        self.setStyleSheet(
            f"QLineEdit {{ background: {css(ui.surface)}; color: {ui.text};"
            f" border: 1px solid {css(ui.edge)}; border-radius: 17px;"
            f" padding: 7px 14px 7px 34px; selection-background-color: {ui.accent};"
            f" selection-color: #ffffff; }}"
            f"QLineEdit:focus {{ border: 1px solid {ui.accent}; }}")
        pal = self.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(ui.text_faint))
        self.setPalette(pal)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = hints(QPainter(self))
        glyph(p, "search", QRectF(13, (self.height() - 14) / 2.0, 14, 14),
              current().text_dim, 1.6)
        p.end()


class Group(Cached, QWidget):
    """An inset-grouped list section: a panel in the widgets' shape whose rows
    are separated by hairlines that start where the row's text does, as in iOS
    Settings."""

    def __init__(self, parent=None, inset: int = 16) -> None:
        super().__init__(parent)
        self.inset = inset
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.rows: List[QWidget] = []

    def add(self, row: QWidget, inset: Optional[int] = None) -> QWidget:
        row._sep_inset = self.inset if inset is None else inset
        self.lay.addWidget(row)
        self.rows.append(row)
        return row

    def paint_key(self):
        return tuple((w.geometry().getRect(), w.isHidden(), w._sep_inset)
                     for w in self.rows)

    def paint(self, p: QPainter) -> None:
        ui = current()
        hints(p)
        paint_surface(p, QRectF(self.rect()))
        p.setPen(QPen(QColor(ui.line), 1.0))
        shown = [w for w in self.rows if not w.isHidden()]
        for w in shown[:-1]:
            y = w.geometry().bottom() + 0.5
            p.drawLine(QPointF(w.geometry().left() + w._sep_inset, y),
                       QPointF(w.geometry().right(), y))
