"""
theme.py
========
The look of the app window and its dialogs.

The widgets paint every pixel themselves; this is for the parts that are
ordinary Qt controls - buttons, sliders, scroll areas - plus the few controls Qt
has no good-looking version of: a segmented control, a switch and colour
swatches, drawn the way macOS and iOS draw them.

The greys are Apple's neutral system greys rather than tinted ones, for the same
reason the cards use pure white and black labels (see typography.py): bluish
greys are most of what makes a dark mode look like a Windows theme. The accent
is the board's, so changing it in Settings recolours the app with the widgets.

There is one active theme at a time, held here rather than passed to every
control, so a theme change is one call and a repaint - not a walk over every
widget in the window handing each a new palette.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QConicalGradient, QFont, QFontMetricsF, QPainter, QPalette, QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QColorDialog, QHBoxLayout, QScrollArea,
    QSizePolicy, QWidget,
)

from ..core import typography
from ..core.palette import Palette, _mix


@dataclass(frozen=True)
class Ui:
    dark: bool
    accent: str
    window: str          # page background
    sidebar: str
    panel: str           # grouped sections and gallery tiles
    raised: str          # control fill
    raised_hi: str       # control fill, hovered
    line: str            # hairlines
    text: str
    text_dim: str
    text_faint: str
    pill: str            # the selected segment
    danger: str

    @property
    def accent_soft(self) -> str:
        return _mix(self.accent, self.panel, 0.80)

    @property
    def accent_hover(self) -> str:
        return _mix(self.accent, "#ffffff", 0.12)


def build(palette: Palette) -> Ui:
    if palette.dark:
        return Ui(dark=True, accent=palette.accent,
                  window="#131316", sidebar="#1a1a1e", panel="#1e1e23",
                  raised="#2a2a30", raised_hi="#35353c", line="#2b2b31",
                  text="#f5f5f7", text_dim="#a1a1a6", text_faint="#6e6e73",
                  pill="#5a5a62", danger="#ff453a")
    return Ui(dark=False, accent=palette.accent,
              window="#f5f5f7", sidebar="#ebebef", panel="#ffffff",
              raised="#e6e6eb", raised_hi="#dadadf", line="#e0e0e5",
              text="#1d1d1f", text_dim="#6e6e73", text_faint="#a1a1a6",
              pill="#ffffff", danger="#ff3b30")


_current: Optional[Ui] = None


def current() -> Ui:
    global _current
    if _current is None:
        from ..core.palette import build as build_palette
        _current = build(build_palette())
    return _current


def set_current(ui: Ui) -> None:
    global _current
    _current = ui


# --------------------------------------------------------------------------- #
#  Type
# --------------------------------------------------------------------------- #
def font(size: float = 10.0, weight=QFont.Normal) -> QFont:
    """The cards' type, so the window reads as the same family as the widgets."""
    return typography.font(size, weight)


def stylesheet(ui: Ui) -> str:
    """For the stock controls. Fonts are set in code, not here: a stylesheet
    font would override every ``setFont`` below it, headings included."""
    scroll = "rgba(255,255,255,0.16)" if ui.dark else "rgba(0,0,0,0.18)"
    scroll_hi = "rgba(255,255,255,0.28)" if ui.dark else "rgba(0,0,0,0.30)"
    return f"""
    #AppRoot, #Dialog {{ background: {ui.window}; }}
    #Sidebar {{ background: {ui.sidebar}; border-right: 1px solid {ui.line}; }}
    #Inspector {{ background: {ui.panel}; border: 1px solid {ui.line};
                  border-radius: 16px; }}
    QLabel {{ color: {ui.text}; background: transparent; }}
    QLabel[tone="dim"] {{ color: {ui.text_dim}; }}
    QLabel[tone="faint"] {{ color: {ui.text_faint}; }}
    QLabel[role="badge"] {{ color: {ui.accent}; background: {ui.accent_soft};
                            border-radius: 9px; padding: 1px 8px; }}
    QFrame[role="panel"] {{ background: {ui.panel}; border: 1px solid {ui.line};
                            border-radius: 14px; }}
    QFrame[role="rule"] {{ background: {ui.line}; border: none;
                           min-height: 1px; max-height: 1px; }}
    QPushButton {{ background: {ui.raised}; color: {ui.text}; border: none;
                   border-radius: 8px; padding: 6px 14px; }}
    QPushButton:hover {{ background: {ui.raised_hi}; }}
    QPushButton:pressed {{ background: {ui.line}; }}
    QPushButton:disabled {{ color: {ui.text_faint}; background: {ui.raised}; }}
    QPushButton[kind="primary"] {{ background: {ui.accent}; color: #ffffff; }}
    QPushButton[kind="primary"]:hover {{ background: {ui.accent_hover}; }}
    QPushButton[kind="primary"]:disabled {{ background: {ui.raised};
                                            color: {ui.text_faint}; }}
    QPushButton[kind="danger"] {{ color: {ui.danger}; }}
    QPushButton[kind="flat"] {{ background: transparent; color: {ui.accent};
                                padding: 6px 8px; }}
    QPushButton[kind="flat"]:hover {{ background: {ui.raised}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {scroll}; border-radius: 4px;
                                   min-height: 36px; }}
    QScrollBar::handle:vertical:hover {{ background: {scroll_hi}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}
    QSlider {{ background: transparent; min-height: 22px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {ui.raised_hi};
                                  border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ui.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #ffffff; width: 18px; height: 18px;
                                  margin: -7px 0; border-radius: 9px;
                                  border: 1px solid rgba(0,0,0,0.18); }}
    QToolTip {{ background: {ui.panel}; color: {ui.text};
                border: 1px solid {ui.line}; padding: 4px 8px; }}
    #Toast {{ background: {"#3a3a41" if ui.dark else "#1d1d1f"};
              border-radius: 12px; }}
    #Toast QLabel {{ color: #ffffff; }}
    #Toast QPushButton {{ background: transparent; padding: 4px 10px;
                          color: {_mix(ui.accent, "#ffffff", 0.35)}; }}
    #Toast QPushButton:hover {{ background: rgba(255,255,255,0.10); }}
    """


def qpalette(ui: Ui) -> QPalette:
    """A Qt palette in the theme's colours.

    The stylesheet covers the controls this app styles; this covers everything
    else that fills itself from the palette. Without it those pick up Windows'
    own palette - dark whenever the system is - whatever the app's theme says.
    """
    pal = QPalette()
    for role, colour in ((QPalette.Window, ui.window), (QPalette.Base, ui.panel),
                         (QPalette.AlternateBase, ui.raised),
                         (QPalette.Button, ui.raised),
                         (QPalette.WindowText, ui.text), (QPalette.Text, ui.text),
                         (QPalette.ButtonText, ui.text),
                         (QPalette.PlaceholderText, ui.text_faint),
                         (QPalette.ToolTipBase, ui.panel),
                         (QPalette.ToolTipText, ui.text),
                         (QPalette.Highlight, ui.accent),
                         (QPalette.HighlightedText, "#ffffff")):
        pal.setColor(role, QColor(colour))
    return pal


def scroll_area(content: QWidget) -> QScrollArea:
    """A frameless, transparent, vertically scrolling area around ``content``."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(content)
    # After setWidget, not before: it switches the content's fill back on.
    area.viewport().setAutoFillBackground(False)
    content.setAutoFillBackground(False)
    return area


# --------------------------------------------------------------------------- #
#  Segmented control
# --------------------------------------------------------------------------- #
class Segmented(QWidget):
    """macOS's segmented control: one choice of a few, the pill slides to it."""

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
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    @property
    def current(self) -> str:
        return self.options[self._index][0]

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
        return QSize(self._width(26), self._h)

    def minimumSizeHint(self) -> QSize:
        # Allowed to tighten up rather than push a neighbour out of a narrow row.
        return QSize(self._width(12), self._h)

    def _hit(self, x: float) -> int:
        n = len(self.options)
        seg = (self.width() - 4) / n
        return max(0, min(n - 1, int((x - 2) // seg)))

    def paintEvent(self, _ev) -> None:
        ui = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = QRectF(self.rect())
        rad = min(9.0, r.height() / 2 - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ui.raised))
        p.drawRoundedRect(r, rad, rad)

        n = len(self.options)
        seg = (r.width() - 4) / n
        pill = QRectF(2 + self._pill * seg, 2, seg, r.height() - 4)
        if not ui.dark:
            p.setBrush(QColor(0, 0, 0, 26))
            p.drawRoundedRect(pill.translated(0, 0.8), rad - 2, rad - 2)
        p.setBrush(QColor(ui.pill))
        p.drawRoundedRect(pill, rad - 2, rad - 2)

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
        p.end()

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
class Switch(QAbstractButton):
    """An iOS switch. Checkable; ``toggled`` is the signal to use."""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self.toggled.connect(self._slide)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setFixedSize(44, 26)

    def sizeHint(self) -> QSize:
        return QSize(44, 26)

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

    def paintEvent(self, _ev) -> None:
        ui = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off = QColor(ui.raised_hi)
        on = QColor(ui.accent)
        t = self._pos
        track = QColor(round(off.red() + (on.red() - off.red()) * t),
                       round(off.green() + (on.green() - off.green()) * t),
                       round(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.left() + 2 + t * (r.width() - d - 4)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, r.top() + 2.6, d, d))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))
        if self.hasFocus():
            ring = QColor(ui.accent)
            ring.setAlpha(150)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 1.5))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                              self.height() / 2, self.height() / 2)
        p.end()


# --------------------------------------------------------------------------- #
#  Colour swatches
# --------------------------------------------------------------------------- #
class Swatch(QAbstractButton):
    """A colour dot. ``mode`` is "colour", "default" (no override) or "custom"."""

    def __init__(self, colour: Optional[str], tip: str, mode: str = "colour",
                 parent=None) -> None:
        super().__init__(parent)
        self.colour = colour
        self.mode = mode
        self.setCheckable(True)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(30, 30)
        self._hover = False

    def enterEvent(self, _ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _ev) -> None:
        ui = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        dot = QRectF(5, 5, 20, 20)
        p.setPen(Qt.NoPen)
        if self.mode == "default":
            p.setBrush(QColor(ui.raised_hi))
            p.drawEllipse(dot)
            p.setPen(QPen(QColor(ui.text_dim), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(dot.left() + 5, dot.bottom() - 5),
                       QPointF(dot.right() - 5, dot.top() + 5))
        elif self.mode == "custom":
            grad = QConicalGradient(dot.center(), 90)
            for i, hue in enumerate((0, 60, 120, 180, 240, 300, 360)):
                grad.setColorAt(i / 6.0, QColor.fromHsv(hue % 360, 200, 245))
            p.setBrush(grad)
            p.drawEllipse(dot)
            if self.isChecked() and self.colour:
                p.setBrush(QColor(self.colour))
                p.setPen(QPen(QColor("#ffffff"), 1.5))
                p.drawEllipse(dot.adjusted(5, 5, -5, -5))
        else:
            p.setBrush(QColor(self.colour or "#000000"))
            p.drawEllipse(dot)
            # A hairline keeps a swatch that matches the panel from vanishing.
            edge = QColor(ui.text)
            edge.setAlpha(40)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(edge, 1.0))
            p.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))
        if self.isChecked() or self._hover:
            ring = QColor(ui.accent if self.isChecked() else ui.text_faint)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 2.0))
            p.drawEllipse(QRectF(1.5, 1.5, 27, 27))
        p.end()


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
