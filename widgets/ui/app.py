"""
app.py
======
The PC Widgets window - everything the tray menu does, somewhere you can see it.

Four pages:

- **Gallery**: every widget, previewed live and at actual size, one click to
  put it on the desktop - on whichever display you pick.
- **On your desktop**: the widgets you have, each with its size, colours,
  background photo and display, and a way to find or remove one.
- **Presets**: your displays as they are arranged, and whole layouts - every
  widget on every display - saved by name, to switch between in a click.
- **Settings**: theme, accent, glass, where widgets sit, start with Windows.

The widgets are the point and this window is only how you arrange them, so
closing it hides it: the widgets stay on the desktop, and the tray icon or
launching the app again brings the window straight back.

It is built from the pieces in theme.py on the frame in chrome.py: a solid
black or white window cut to the widgets' own shape, its sidebar frosted glass
set off from the page by a hairline, grey panels with the widgets' corners, and
controls that ease between states. Nothing here keeps its own copy of the
board. Every page
reads the cards and preferences when it draws, and redraws when the board says
something changed - a change can as easily come from a card's right-click menu
or the tray as from this window.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QHBoxLayout, QLayout, QMenu,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core import appearance, displays, mark, startup, winapi
from ..core.board import REGISTRY, Board, placement_size
from ..core.card import MARGIN, RADIUS
from ..core.palette import ACCENTS
from . import cropper, previews, theme
from .chrome import Chrome
from .theme import (
    Button, Cached, Group, IconTile, SearchField, Segmented, Swatch, SwatchRow,
    Switch, Text, TextField, ease_to, eased, glyph, hints, keycap_width,
    paint_keycap, paint_shadow, paint_surface, rounded, running, smooth,
)

# Gallery names and one-liners. Two tiles are both titled "Calendar" on the
# card itself, which is right there and ambiguous here, hence "Agenda".
CATALOG = {
    "clock": ("Clock", "An analog face, or the time set large."),
    "weather": ("Weather", "Conditions now, and the week ahead at larger sizes."),
    "calendar": ("Calendar", "Today's date, or the whole month at a glance."),
    "outlook": ("Agenda", "What's coming up, from Outlook or your Windows calendar."),
    "notes": ("Notes", "A sticky note you can type in. Saved as you go."),
    "stocks": ("Stocks", "A watchlist with sparklines: shares, indices or crypto."),
    "system": ("System", "Live CPU and memory, with uptime."),
    "disk": ("Storage", "How full your drive is, at a glance."),
    "files": ("Files", "Quick Access and recent files, browsable right in the card."),
    "media": ("Now Playing", "Spotify or Apple Music, with artwork and controls."),
    "workspace": ("Workspace", "A lit bay for each coding agent while it works."),
    "comms": ("Comms", "What your coding agents are saying to each other."),
}
SECTIONS = (
    ("Everyday", ("clock", "weather", "calendar", "outlook", "notes", "stocks")),
    ("Your PC", ("system", "disk", "files", "media")),
    ("Coding agents", ("workspace", "comms")),
)
LAYERS = (("desktop", "On desktop"), ("normal", "Floating"), ("top", "On top"))
LAYER_TEXT = {
    "desktop": "Behind your windows, like icons on the desktop.",
    "normal": "In among your other windows.",
    "top": "Above everything, all the time.",
}
GLASS = (("78", "Clearer"), ("112", "Balanced"), ("158", "Frostier"))
OPACITY = (("50", "50%"), ("65", "65%"), ("80", "80%"), ("90", "90%"),
           ("100", "100%"))
PAGE_MARGIN = 36


def name_of(kind: str) -> str:
    cls = REGISTRY.get(kind)
    return CATALOG.get(kind, (cls.title if cls else kind.title(), ""))[0]


def about(kind: str) -> str:
    return CATALOG.get(kind, ("", ""))[1]


def sections():
    """The gallery's groups, with any tile not listed above gathered at the end
    - a new tile shows up in the gallery without anyone remembering to add it."""
    seen, out = set(), []
    for title, kinds in SECTIONS:
        present = [k for k in kinds if k in REGISTRY]
        seen.update(present)
        if present:
            out.append((title, present))
    rest = sorted((k for k in REGISTRY if k not in seen), key=name_of)
    if rest:
        out.append(("More", rest))
    return out


def spatial(screens) -> list:
    """Displays left to right, top to bottom - the order to offer them in, so a
    choice between "Left" and "Main" reads the way the desk does."""
    return sorted(screens, key=lambda d: (d.rect[0], d.rect[1]))


def describe(card, board: Optional[Board] = None) -> str:
    """One line about a placed card: its size, background, anything telling,
    and - with more than one display - which one it is on."""
    bits = [card.size_class.capitalize()]
    c = card.config
    if c.get("image"):
        bits.append("photo")
    elif card.customizable and appearance.is_solid(c):
        bits.append("solid colour")
    elif card.customizable:
        bits.append("frosted glass")
    if card.kind == "weather" and c.get("place"):
        bits.append(c["place"])
    elif card.kind == "stocks" and c.get("symbols"):
        bits.append(", ".join(c["symbols"][:3]))
    elif card.kind == "outlook":
        bits.append("Outlook" if c.get("ics") else "Windows calendar")
    elif card.kind == "files" and c.get("w"):
        bits.append("custom size")
    if board is not None:
        if card.displaced and card.anchor is not None:
            bits.append(f"waiting for {board.display_name(card.anchor.display)}")
        elif len(board.screens) > 1:
            d = board.display_of(card)
            if d is not None:
                bits.append(f"{displays.labels(board.screens).get(d.key, 'Main')} display")
    return " · ".join(bits)


def label(text: str = "", size: float = 10.0, weight=QFont.Normal,
          tone: Optional[str] = None, wrap: bool = False, elide: bool = False) -> Text:
    return Text(text, size, weight, tone=tone, wrap=wrap, elide=elide)


class _Done(QObject):
    """Carries a background job's result back to the GUI thread.

    Created on the GUI thread, so its own slot runs there however the signal is
    emitted - Qt queues a cross-thread emit to the receiver's thread. Held in
    ``_pending`` until it delivers: nothing else keeps the Python side alive
    once the worker finishes, and a collected receiver drops the result.
    """

    finished = Signal(object)

    def __init__(self, then: Callable) -> None:
        super().__init__()
        self._then = then
        self.finished.connect(self._deliver)

    def _deliver(self, result) -> None:
        _pending.discard(self)
        try:
            self._then(result)
        finally:
            self.deleteLater()


_pending: set = set()


def in_background(work: Callable, then: Callable) -> None:
    """Run ``work`` off the GUI thread and hand its result to ``then`` on it.

    For the shortcut jobs: each one is a PowerShell round trip of about a
    second, and a switch that sticks mid-animation for that long reads as the
    window having hung.
    """
    done = _Done(then)
    _pending.add(done)

    def run():
        try:
            result = work()
        except Exception:
            result = None
        done.finished.emit(result)

    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------- #
#  Layout and motion helpers
# --------------------------------------------------------------------------- #
class TileLayout(QLayout):
    """Equal columns that reflow with the window: as many tiles across as fit
    at ``min_w`` each, stretched to share the width. Hidden tiles - filtered
    out by the search - leave no gap."""

    def __init__(self, parent=None, min_w: int = 380, gap: int = 0) -> None:
        super().__init__(parent)
        self._items: list = []
        self.min_w = min_w
        self.gap = gap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        return QSize(self.min_w, 0)

    def _arrange(self, rect: QRect, apply: bool) -> int:
        shown = [it for it in self._items if not it.isEmpty()]
        cols = max(1, (rect.width() + self.gap) // (self.min_w + self.gap))
        tile_w = (rect.width() - self.gap * (cols - 1)) / cols
        y, row_h = rect.y(), 0
        for i, item in enumerate(shown):
            col = i % cols
            if col == 0 and i:
                y += row_h + self.gap
                row_h = 0
            h = item.sizeHint().height()
            if apply:
                item.setGeometry(QRect(round(rect.x() + col * (tile_w + self.gap)), y,
                                       round(tile_w), h))
            row_h = max(row_h, h)
        return (y + row_h - rect.y()) if shown else 0


class Crossfade(QWidget):
    """The page being left, fading out over the one arriving.

    Grabbed as a picture once, so the fade costs one image draw per frame and
    the new page underneath renders normally - an opacity effect on a whole
    page would re-render every tile on it for every frame of the fade.
    """

    def __init__(self, host: QWidget, picture) -> None:
        super().__init__(host)
        self.picture = picture
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setGeometry(host.rect())
        self._fade = eased(self, 200)
        self._fade.t = 1.0
        self._fade.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        ease_to(self._fade, 0.0)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setOpacity(max(0.0, self._fade.t))
        p.drawPixmap(0, 0, self.picture)
        p.end()


class Toast(QWidget):
    """A short message floating at the foot of the window, with an optional
    action. Rises a few pixels as it fades in, sinks as it fades out."""

    H = 44

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.text = ""
        self.action = ""
        self._fn: Optional[Callable] = None
        self._action_rect = QRectF()
        self._hot = False
        self._show = eased(self, 220)
        self._show.finished.connect(self._faded)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.setMouseTracking(True)
        self.hide()

    def show_message(self, text: str, action: str = "",
                     fn: Optional[Callable] = None, ms: int = 4500) -> None:
        self.text, self.action, self._fn = text, action if fn else "", fn
        f, fa = theme.font(10, QFont.Medium), theme.font(10, QFont.DemiBold)
        w = QFontMetricsF(f).horizontalAdvance(text) + 44
        if self.action:
            w += QFontMetricsF(fa).horizontalAdvance(self.action) + 34
        self.resize(int(w) + 28, self.H + 28)
        self.place()
        self.show()
        self.raise_()
        ease_to(self._show, 1.0)
        self._timer.start(ms)

    def place(self) -> None:
        host = self.parentWidget()
        body = host.body_rect() if hasattr(host, "body_rect") else host.rect()
        side = getattr(host, "SIDE_W", 0)
        x = body.left() + side + (body.width() - side - self.width()) // 2
        self.move(max(body.left() + 8, x), body.bottom() + 1 - self.height() - 14)

    def dismiss(self) -> None:
        ease_to(self._show, 0.0)

    def _faded(self) -> None:
        if self._show.t <= 0.01:
            self.hide()

    def _act(self) -> None:
        fn, self._fn = self._fn, None
        self.dismiss()
        if fn is not None:
            fn()

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        t = self._show.t
        p = hints(QPainter(self))
        p.setOpacity(t)
        r = QRectF(14, 14 + (1 - t) * 10, self.width() - 28, self.H)
        paint_shadow(p, r, r.height() / 2, blur=16, alpha=110, dy=6)
        path = rounded(r, r.height() / 2)
        p.fillPath(path, QColor(ui.hud))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawPath(rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), r.height() / 2))
        glyph(p, "check", QRectF(r.left() + 16, r.center().y() - 7, 14, 14),
              theme.current().good, 1.8)
        f = theme.font(10, QFont.Medium)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        tw = QFontMetricsF(f).horizontalAdvance(self.text)
        p.drawText(QRectF(r.left() + 38, r.top(), tw + 2, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text)
        if self.action:
            fa = theme.font(10, QFont.DemiBold)
            aw = QFontMetricsF(fa).horizontalAdvance(self.action) + 22
            self._action_rect = QRectF(r.right() - aw - 8, r.top() + 7, aw, r.height() - 14)
            if self._hot:
                p.fillPath(rounded(self._action_rect, self._action_rect.height() / 2),
                           QColor(255, 255, 255, 26))
            p.setFont(fa)
            p.setPen(theme.mix(ui.accent, "#ffffff", 0.3))   # the toast is dark in both themes
            p.drawText(self._action_rect, Qt.AlignCenter, self.action)
        p.end()

    def mouseMoveEvent(self, ev) -> None:
        hot = bool(self.action) and self._action_rect.contains(ev.position())
        if hot != self._hot:
            self._hot = hot
            self.setCursor(Qt.PointingHandCursor if hot else Qt.ArrowCursor)
            self.update()

    def mousePressEvent(self, ev) -> None:
        if self.action and self._action_rect.contains(ev.position()):
            self._act()


# --------------------------------------------------------------------------- #
#  Sidebar
# --------------------------------------------------------------------------- #
class NavButton(Cached, QAbstractButton):
    def __init__(self, text: str, glyph_kind: str, parent=None) -> None:
        super().__init__(parent)
        self.glyph_kind = glyph_kind
        self.badge = ""             # a count, right-aligned, as macOS sidebars show
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        self._hover = eased(self, 140)

    def set_badge(self, text: str) -> None:
        if text != self.badge:
            self.badge = text
            self.update()

    def enterEvent(self, _ev) -> None:
        ease_to(self._hover, 1.0)

    def leaveEvent(self, _ev) -> None:
        ease_to(self._hover, 0.0)

    def sizeHint(self) -> QSize:
        return QSize(200, 38)

    def paint_key(self):
        if running(self._hover):
            return None
        return (self.text(), self.glyph_kind, self.badge, self.isChecked(),
                round(self._hover.t, 3))

    def paint(self, p: QPainter) -> None:
        ui = theme.current()
        hints(p)
        r = QRectF(self.rect())
        if not self.isChecked() and self._hover.t > 0:
            fill = QColor(ui.side_hover)
            fill.setAlphaF(fill.alphaF() * self._hover.t)
            p.fillPath(rounded(r, 11), fill)
        on = self.isChecked()
        glyph(p, self.glyph_kind, QRectF(16, (r.height() - 17) / 2.0, 17, 17),
              ui.accent if on else ui.text_dim, 1.6)
        p.setFont(theme.font(10.5, QFont.DemiBold if on else QFont.Medium))
        p.setPen(QColor(ui.text if on else theme.mix(ui.text_dim, ui.text, self._hover.t)))
        p.drawText(QRectF(46, 0, r.width() - 50, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        if self.badge:
            p.setFont(theme.font(9.5, QFont.Medium))
            p.setPen(QColor(ui.text_dim))
            p.drawText(QRectF(0, 0, r.width() - 14, r.height()),
                       Qt.AlignRight | Qt.AlignVCenter, self.badge)


class PeekButton(Cached, QAbstractButton):
    """"Bring to front", with its shortcut drawn as keycaps."""

    KEYS = ("Ctrl", "Alt", "W")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self._hover = eased(self, 140)

    def enterEvent(self, _ev) -> None:
        ease_to(self._hover, 1.0)

    def leaveEvent(self, _ev) -> None:
        ease_to(self._hover, 0.0)

    def paint_key(self):
        if running(self._hover):
            return None
        return (self.text(), self.isEnabled(), round(self._hover.t, 3))

    def paint(self, p: QPainter) -> None:
        ui = theme.current()
        hints(p)
        r = QRectF(self.rect())
        t = self._hover.t if self.isEnabled() else 0.0
        p.fillPath(rounded(r, r.height() / 2),
                   theme.mix(ui.side_control, ui.side_control_hi, t))
        p.setFont(theme.font(10, QFont.Medium))
        p.setPen(QColor(ui.text if self.isEnabled() else ui.text_faint))
        p.drawText(QRectF(18, 0, r.width() - 24, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        if self.isEnabled():
            x = r.right() - 12
            for key in reversed(self.KEYS):
                w = keycap_width(key)
                x -= w
                paint_keycap(p, QRectF(x, r.center().y() - 10, w, 20), key)
                x -= 3


class Sidebar(QWidget):
    """Navigation, on frosted glass that runs to the window's edge, with a
    hairline between it and the page. The window paints the glass and the line
    (chrome.py); this paints what sits on them."""

    PAGES = (("gallery", "Gallery", "gallery"),
             ("desktop", "On your desktop", "desktop"),
             ("presets", "Presets", "presets"),
             ("settings", "Settings", "settings"))
    PAD = 12            # from the window's edge, and from the hairline

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.setFixedWidth(win.SIDE_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self.PAD, 86, self.PAD + 1, self.PAD)
        lay.setSpacing(2)

        self.nav: Dict[str, NavButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, text, g in self.PAGES:
            b = NavButton(text, g)
            b.clicked.connect(lambda _c=False, k=key: win.show_page(k))
            group.addButton(b)
            lay.addWidget(b)
            self.nav[key] = b
        lay.addStretch(1)
        self.peek = PeekButton()
        self.peek.clicked.connect(win.board.toggle_peek)
        lay.addWidget(self.peek)

        # The selection is one pill that slides between items, not a fill that
        # jumps - the same motion the segmented controls use.
        self._pill_y = None
        self._pill = eased(self, 240)
        self._pill_from, self._pill_to = 0.0, 0.0
        self._pill.valueChanged.connect(lambda _v: self.update())
        self.sync()

    def set_page(self, key: str) -> None:
        b = self.nav[key]
        b.setChecked(True)
        target = float(b.geometry().top())
        if self._pill_y is None or not self.isVisible():
            self._pill_y = target
            self._pill_from = self._pill_to = target
            self.update()
            return
        self._pill_from, self._pill_to = self._current_pill(), target
        self._pill.t = 0.0
        ease_to(self._pill, 1.0)

    def _current_pill(self) -> float:
        t = self._pill.t
        return self._pill_from + (self._pill_to - self._pill_from) * t

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        checked = next((b for b in self.nav.values() if b.isChecked()), None)
        if checked is not None:
            self._pill_y = None
            QTimer.singleShot(0, lambda: self.set_page(
                next(k for k, b in self.nav.items() if b is checked)))

    def sync(self) -> None:
        board = self.win.board
        n = len(board.cards)
        self.nav["desktop"].set_badge(str(n) if n else "")
        k = len(board.state.presets)
        self.nav["presets"].set_badge(str(k) if k else "")
        if board.state.layer == "top":
            self.peek.setText("Always on top")
            self.peek.setEnabled(False)
        else:
            self.peek.setEnabled(True)
            self.peek.setText("Send back" if board.peeked else "Bring to front")
        self.update()

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        p = hints(QPainter(self))
        # Centred over the nav items' icons, which sit 16 px into each item.
        x = self.PAD + 10
        # The mark is white, and takes the text colour on the light glass.
        mark.paint(p, QRectF(x, 26, 30, 30), mark.WHITE if ui.dark else ui.text)
        p.setFont(theme.font(12.5, QFont.Bold))
        p.setPen(QColor(ui.text))
        p.drawText(QRectF(x + 40, 23, 160, 20), Qt.AlignLeft | Qt.AlignVCenter, "PC Widgets")
        p.setFont(theme.font(9))
        p.setPen(QColor(ui.text_dim))
        p.drawText(QRectF(x + 40, 43, 160, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   "by Studio Hendrixx")

        if self._pill_y is not None and any(b.isChecked() for b in self.nav.values()):
            y = self._current_pill()
            b = next(iter(self.nav.values()))
            pill = QRectF(b.geometry().left(), y, b.width(), b.height())
            p.fillPath(rounded(pill, 11), QColor(ui.side_pill))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ui.accent))
            p.drawRoundedRect(QRectF(pill.left() + 1, pill.center().y() - 8, 3, 16), 1.5, 1.5)
        p.end()


# --------------------------------------------------------------------------- #
#  Page scaffolding
# --------------------------------------------------------------------------- #
class PageHeader(QWidget):
    """Title, subtitle and an optional control on the right. Fixed above the
    scrolling body; a hairline appears under it once content scrolls beneath,
    the way a macOS toolbar separates only when it needs to."""

    def __init__(self, title: str, subtitle: str = "", right: Optional[QWidget] = None) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAGE_MARGIN, 52, PAGE_MARGIN, 18)
        lay.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.title = label(title, 23, QFont.Bold)
        self.subtitle = label(subtitle, 10.5, tone="dim")
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        lay.addLayout(texts, 1)
        if right is not None:
            lay.addWidget(right, 0, Qt.AlignBottom)
        self._line = eased(self, 180)

    def watch(self, area) -> None:
        bar = area.verticalScrollBar()
        bar.valueChanged.connect(lambda v: ease_to(self._line, 1.0 if v > 2 else 0.0))

    def paintEvent(self, _ev) -> None:
        if self._line.t <= 0.0:
            return
        ui = theme.current()
        p = QPainter(self)
        c = QColor(ui.line)
        c.setAlphaF(c.alphaF() * self._line.t)
        p.setPen(QPen(c, 1.0))
        p.drawLine(QPointF(0, self.height() - 0.5), QPointF(self.width(), self.height() - 0.5))
        p.end()


def page(header: PageHeader, body: QWidget) -> QWidget:
    """A fixed header over a gliding scroll area; the area is ``.area``."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(header)
    w.area = theme.scroll_area(body)
    header.watch(w.area)
    lay.addWidget(w.area, 1)
    return w


# --------------------------------------------------------------------------- #
#  Gallery
# --------------------------------------------------------------------------- #
class GalleryTile(Cached, QWidget):
    """One widget in the gallery: a panel in the widgets' own shape, its preview
    at actual size in a well whose corners follow the panel's, and size + Add
    underneath."""

    PAD_X, PAD_TOP, PAD_BOTTOM = 10, 10, 10      # half the gap between tiles
    WELL_INSET = 10
    WELL_H = 220
    HEIGHT = 372

    def __init__(self, win: "MainWindow", cls) -> None:
        super().__init__()
        self.win = win
        self.kind = cls.kind
        self.count = 0
        self._preview = None
        sizes = list(cls.supported_sizes)
        self.size_key = "medium" if "medium" in sizes else sizes[0]
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._hover = eased(self, 160)

        self.sizes = Segmented([(s, s.capitalize()) for s in sizes], self.size_key,
                               self, height=30)
        self.sizes.changed.connect(self._size_changed)
        self.add = Button("Add", "primary", "plus", self, height=32,
                          slot=self._add, tip=f"Put {name_of(self.kind)} on your desktop")
        # Wide enough to become "Added" without changing size under the cursor.
        fm = QFontMetricsF(theme.font(10, QFont.DemiBold))
        self.add.setFixedSize(int(fm.horizontalAdvance("Added") + 14 + 6 + 34), 32)

    def sizeHint(self) -> QSize:
        return QSize(380, self.HEIGHT)

    # -- geometry -------------------------------------------------------- #
    def card(self) -> QRectF:
        return QRectF(self.PAD_X, self.PAD_TOP, self.width() - self.PAD_X * 2,
                      self.height() - self.PAD_TOP - self.PAD_BOTTOM)

    def well(self) -> QRectF:
        c, g = self.card(), self.WELL_INSET
        return QRectF(c.left() + g, c.top() + g, c.width() - 2 * g, self.WELL_H)

    def _place(self) -> None:
        c = self.card()
        y = round(c.bottom() - 16 - 32)
        self.add.move(round(c.right() - 16 - self.add.width()), y)
        avail = int(c.width() - 32 - self.add.width() - 12)
        w = max(self.sizes.minimumSizeHint().width(),
                min(self.sizes.sizeHint().width(), avail))
        self.sizes.setGeometry(round(c.left() + 16), y + 1, w, 30)
        self.update()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._place()

    def enterEvent(self, _ev) -> None:
        # Content gliding under a still pointer would light every card it
        # passes, each one an animation competing with the scroll for the
        # same frames - so the hover waits until the page settles (sync_hover).
        if not self.win.gallery.area.scrolling():
            ease_to(self._hover, 1.0)

    def leaveEvent(self, _ev) -> None:
        ease_to(self._hover, 0.0)

    def sync_hover(self) -> None:
        ease_to(self._hover, 1.0 if self.underMouse() else 0.0)

    def paint_key(self):
        if running(self._hover):
            return None
        return (self._preview.cacheKey() if self._preview is not None else 0, self.count,
                round(self._hover.t, 3))

    # -- content --------------------------------------------------------- #
    def _size_changed(self, key: str) -> None:
        self.size_key = key
        self.render_preview()

    def render_preview(self) -> None:
        well = self.well()
        w, h = int(well.width()), int(well.height())
        if w < 40 or h < 40:
            return
        b = self.win.board
        self._preview = previews.gallery_image(
            self.kind, self.size_key, b.palette, b.glass if b.state.glass else None,
            b.state.opacity, b.state.glass_tint, w, h)
        self.update()

    def set_count(self, n: int) -> None:
        self.count = n
        self.update()

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        return not q or q in name_of(self.kind).lower() or q in about(self.kind).lower()

    def _add(self) -> None:
        board = self.win.board
        where = self.win.gallery.target()
        card = board.add(self.kind, self.size_key, display=where)
        name = name_of(self.kind)
        if card is None:
            self.win.toast(f"Couldn't add {name}.")
            return
        board.spotlight(card)
        self.add.flash("Added")
        on = "your desktop"
        if where is not None:
            on = f"your {displays.labels(board.screens).get(where, 'Main')} display"
        self.win.toast(f"{name} is on {on}.", "Show",
                       lambda c=card: board.spotlight(c))

    def paint(self, p: QPainter) -> None:
        ui = theme.current()
        hints(p)
        c = self.card()
        paint_surface(p, c, hover=self._hover.t)

        well = self.well()
        path = smooth(well, theme.CORNER - self.WELL_INSET)
        p.save()
        p.setClipPath(path)
        p.fillRect(well, QColor(ui.well))
        if self._preview is not None and not self._preview.isNull():
            p.drawImage(QPointF(well.left() + (well.width() - self._preview.width()) / 2.0,
                                well.top() + (well.height() - self._preview.height()) / 2.0),
                        self._preview)
        p.restore()

        if self.count:
            text = "On desktop" if self.count == 1 else f"{self.count} on desktop"
            f = theme.font(8.75, QFont.DemiBold)
            tw = QFontMetricsF(f).horizontalAdvance(text)
            chip = QRectF(well.left() + 12, well.top() + 12, tw + 34, 24)
            p.fillPath(rounded(chip, 12), QColor(ui.accent))
            glyph(p, "check", QRectF(chip.left() + 9, chip.top() + 6, 12, 12), "#ffffff", 1.8)
            p.setFont(f)
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(chip.left() + 25, chip.top(), tw + 4, chip.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, text)

        x = c.left() + 18
        p.setFont(theme.font(12.5, QFont.DemiBold))
        p.setPen(QColor(ui.text))
        p.drawText(QRectF(x, well.bottom() + 12, c.width() - 36, 22),
                   Qt.AlignLeft | Qt.AlignVCenter, name_of(self.kind))
        p.setFont(theme.font(9.75))
        p.setPen(QColor(ui.text_dim))
        p.drawText(QRectF(x, well.bottom() + 35, c.width() - 36, 20),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(about(self.kind), Qt.ElideRight,
                                              int(c.width() - 36)))


class GalleryPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.tiles: List[GalleryTile] = []
        self.groups: List[tuple] = []
        self.search = SearchField("Search widgets")
        self.search.setFixedWidth(250)
        self.search.textChanged.connect(self._filter)
        # With more than one display, which one Add puts a widget on. It starts
        # on the display this window is on - where the person adding is looking.
        self.where = Segmented([("", "Main")], height=30)
        self.where_label = label("Add to", 10, QFont.Medium, tone="dim")
        tools = QWidget()
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(10)
        tl.addWidget(self.where_label, 0, Qt.AlignVCenter)
        tl.addWidget(self.where, 0, Qt.AlignVCenter)
        tl.addSpacing(10)
        tl.addWidget(self.search, 0, Qt.AlignVCenter)
        header = PageHeader("Gallery", "Pick a widget and a size, and it goes "
                                       "straight onto your desktop.", tools)

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(PAGE_MARGIN - GalleryTile.PAD_X, 8,
                               PAGE_MARGIN - GalleryTile.PAD_X, 28)
        col.setSpacing(0)
        for title, kinds in sections():
            head = QWidget()
            hl = QHBoxLayout(head)
            hl.setContentsMargins(GalleryTile.PAD_X, 10, GalleryTile.PAD_X, 8)
            hl.addWidget(label(title, 13, QFont.DemiBold))
            hl.addStretch(1)
            col.addWidget(head)
            grid = QWidget()
            tiles = TileLayout(grid, min_w=380, gap=0)
            members = []
            for kind in kinds:
                tile = GalleryTile(win, REGISTRY[kind])
                tiles.addWidget(tile)
                self.tiles.append(tile)
                members.append(tile)
            col.addWidget(grid)
            col.addSpacing(14)
            self.groups.append((head, grid, members))
        self.none = label("No widgets match that search.", 11, tone="dim")
        self.none.setAlignment(Qt.AlignCenter)
        self.none.setContentsMargins(0, 60, 0, 0)
        self.none.hide()
        col.addWidget(self.none)
        col.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        pg = page(header, body)
        self.area = pg.area
        self.area.settled.connect(self._settled)
        outer.addWidget(pg)

        # Previews are painted a few at a time, between events, so opening the
        # window never stalls on twelve renders at once.
        self._queue: List[GalleryTile] = []
        self._pump = QTimer(self)
        self._pump.setInterval(0)
        self._pump.timeout.connect(self._render_next)
        self._resized = QTimer(self)
        self._resized.setSingleShot(True)
        self._resized.timeout.connect(self.refresh_previews)
        self._painted = False
        self.sync_displays()

    def sync_displays(self, here: Optional[str] = None) -> None:
        """Offer the displays there are, in the order they sit on the desk."""
        screens = self.win.board.screens
        names = displays.labels(screens)
        keys = [d.key for d in screens]
        pick = here if here in keys else (self.where.current if self.where.current in keys
                                          else self.win.display_here().key)
        self.where.set_options([(d.key, names[d.key]) for d in spatial(screens)], pick)
        many = len(screens) > 1
        self.where.setVisible(many)
        self.where_label.setVisible(many)

    def target(self) -> Optional[str]:
        """The display Add puts a widget on; None when there is only one."""
        return self.where.current if len(self.win.board.screens) > 1 else None

    def _filter(self, text: str) -> None:
        any_shown = False
        for head, grid, members in self.groups:
            shown = [t for t in members if t.matches(text)]
            for t in members:
                t.setVisible(t in shown)
            head.setVisible(bool(shown))
            grid.setVisible(bool(shown))
            any_shown |= bool(shown)
            grid.layout().invalidate()
        self.none.setVisible(not any_shown)

    def _settled(self) -> None:
        for t in self.tiles:
            if t.isVisible():
                t.sync_hover()

    def on_show(self) -> None:
        self.refresh_counts()
        self.sync_displays(here=self.win.display_here().key)
        if not self._painted:
            self._painted = True
            QTimer.singleShot(0, self.refresh_previews)

    def refresh_previews(self) -> None:
        self._queue = list(self.tiles)
        self._pump.start()

    def _render_next(self) -> None:
        if not self._queue:
            self._pump.stop()
            return
        self._queue.pop(0).render_preview()

    def refresh_counts(self) -> None:
        counts = Counter(c.kind for c in self.win.board.cards)
        for t in self.tiles:
            t.set_count(counts.get(t.kind, 0))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self._painted:
            self._resized.start(140)


# --------------------------------------------------------------------------- #
#  On your desktop
# --------------------------------------------------------------------------- #
class DeskRow(Cached, QAbstractButton):
    THUMB = (112, 64)

    def __init__(self, card, parent=None) -> None:
        super().__init__(parent)
        self.card = card
        self.where = None               # the display heading it is listed under
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(84)
        self._thumb = None
        self._hover = eased(self, 140)

    def refresh(self) -> None:
        try:
            self._thumb = previews.card_image(self.card, *self.THUMB, pad=4)
        except RuntimeError:
            self._thumb = None            # the card went away mid-refresh
        self.update()

    def enterEvent(self, _ev) -> None:
        ease_to(self._hover, 1.0)

    def leaveEvent(self, _ev) -> None:
        ease_to(self._hover, 0.0)

    def paint_key(self):
        if running(self._hover):
            return None
        try:
            detail = describe(self.card)
        except RuntimeError:
            detail = ""
        return (self.isChecked(), round(self._hover.t, 3), detail,
                self._thumb.cacheKey() if self._thumb is not None else 0)

    def paint(self, p: QPainter) -> None:
        ui = theme.current()
        hints(p)
        r = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        if self.isChecked():
            paint_surface(p, r, 20.0)
        elif self._hover.t > 0:
            p.save()
            p.setOpacity(self._hover.t * 0.6)
            p.fillPath(smooth(r, 20.0), QColor(ui.surface))
            p.restore()
        # The thumbnail sits 10px in, so its corners follow the row's.
        tw, th = self.THUMB
        box = QRectF(r.left() + 10, r.center().y() - th / 2.0, tw, th)
        p.save()
        p.setClipPath(smooth(box, 10.0))
        p.fillRect(box, QColor(ui.well))
        if self._thumb is not None:
            p.drawImage(box.topLeft(), self._thumb)
        p.restore()

        x = box.right() + 14
        text_w = r.right() - x - 12
        try:
            title, detail = name_of(self.card.kind), describe(self.card)
        except RuntimeError:
            title, detail = "", ""
        p.setPen(QColor(ui.text))
        p.setFont(theme.font(11, QFont.DemiBold))
        p.drawText(QRectF(x, r.center().y() - 20, text_w, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, title)
        p.setPen(QColor(ui.text_dim))
        p.setFont(theme.font(9.25))
        detail = p.fontMetrics().elidedText(detail, Qt.ElideRight, int(text_w))
        p.drawText(QRectF(x, r.center().y() + 1, text_w, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, detail)


class Stage(Cached, QWidget):
    """The selected widget, large, in a well with the widgets' corners."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(236)
        self._img = None

    def set_image(self, img) -> None:
        self._img = img
        self.update()

    def well(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0, 0, 0, -6)

    def paint_key(self):
        return (self._img.cacheKey() if self._img is not None else 0,)

    def paint(self, p: QPainter) -> None:
        ui = theme.current()
        hints(p)
        well = self.well()
        p.save()
        p.setClipPath(smooth(well))
        p.fillRect(well, QColor(ui.well))
        if self._img is not None and not self._img.isNull():
            p.drawImage(QPointF(well.left() + (well.width() - self._img.width()) / 2.0,
                                well.top() + (well.height() - self._img.height()) / 2.0),
                        self._img)
        p.restore()


def form_row(title: str, control: QWidget, stacked: bool = False) -> QWidget:
    """A row of a grouped list: label on the left and control on the right, or
    - for wide controls like a swatch row - the control under its label."""
    row = QWidget()
    if stacked:
        lay = QVBoxLayout(row)
        lay.setContentsMargins(16, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(label(title, 10.25, QFont.Medium))
        lay.addWidget(control)
    else:
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 9, 14, 9)
        lay.setSpacing(12)
        lay.addWidget(label(title, 10.25, QFont.Medium))
        lay.addStretch(1)
        lay.addWidget(control, 0, Qt.AlignVCenter)
    return row


def section_title(text: str) -> QLabel:
    lab = label(text, 9.75, QFont.DemiBold, tone="dim")
    lab.setContentsMargins(6, 18, 0, 8)
    return lab


class Inspector(QWidget):
    """Everything about one placed card, as grouped lists under a large preview."""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.card = None
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 6, 24)
        lay.setSpacing(0)

        self.stage = Stage()
        lay.addWidget(self.stage)
        lay.addSpacing(14)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 0, 0)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.name = label("", 17, QFont.Bold)
        self.detail = label("", 9.75, tone="dim")
        names.addWidget(self.name)
        names.addWidget(self.detail)
        top.addLayout(names, 1)
        top.addWidget(Button("Show on desktop", "ghost", "eye", slot=self._show,
                             tip="Lift it above your windows for a moment"),
                      0, Qt.AlignVCenter)
        lay.addLayout(top)

        self.size_title = section_title("Layout")
        lay.addWidget(self.size_title)
        self.size_group = Group()
        self.sizes = Segmented([("small", "Small")], "small")
        self.sizes.changed.connect(self._size)
        self.size_row = self.size_group.add(form_row("Size", self.sizes))
        self.where = Segmented([("", "Main")])
        self.where.changed.connect(self._move_to)
        self.where_row = self.size_group.add(form_row("Display", self.where))
        note = QWidget()
        nl = QVBoxLayout(note)
        nl.setContentsMargins(16, 10, 16, 12)
        self.where_note = label("", 9.25, tone="dim", wrap=True)
        nl.addWidget(self.where_note)
        self.where_note_row = self.size_group.add(note)
        lay.addWidget(self.size_group)

        self.bg_title = section_title("Background")
        lay.addWidget(self.bg_title)
        self.bg_group = Group()
        self.fill = Segmented([("glass", "Frosted glass"), ("solid", "Solid colour")])
        self.fill.changed.connect(lambda k: self._set("fill", k))
        self.bg_group.add(form_row("Style", self.fill))
        self.bg = SwatchRow(appearance.BG_SWATCHES, "Background colour")
        self.bg.picked.connect(lambda h: self._set("bg", h))
        self.bg_group.add(form_row("Colour", self.bg, stacked=True))
        lay.addWidget(self.bg_group)

        self.fg_title = section_title("Text")
        lay.addWidget(self.fg_title)
        self.fg_group = Group()
        self.fg = SwatchRow(appearance.FG_SWATCHES, "Text colour")
        self.fg.picked.connect(lambda h: self._set("fg", h))
        fg_row = form_row("Colour", self.fg, stacked=True)
        self.fg_note = label("Typed notes stay readable on any background.", 9, tone="faint")
        fg_row.layout().addWidget(self.fg_note)
        self.fg_group.add(fg_row)
        lay.addWidget(self.fg_group)

        self.photo_title = section_title("Photo")
        lay.addWidget(self.photo_title)
        self.photo_group = Group()
        prow = QWidget()
        pl = QHBoxLayout(prow)
        pl.setContentsMargins(16, 10, 14, 10)
        pl.setSpacing(8)
        self.photo_name = label("", 9.75, tone="dim", elide=True)
        self.choose = Button("Choose…", "secondary", "photo", slot=self._choose, height=30)
        self.adjust = Button("Adjust…", "secondary", "crop", slot=self._adjust, height=30,
                             tip="Move and zoom the photo inside the widget")
        self.clear = Button("", "plain", "trash", slot=self._clear, height=30,
                            tip="Remove the photo")
        pl.addWidget(self.photo_name, 1)
        pl.addWidget(self.choose)
        pl.addWidget(self.adjust)
        pl.addWidget(self.clear)
        self.photo_group.add(prow)
        lay.addWidget(self.photo_group)

        self.plain = label("Now Playing keeps its own look - it takes its colours "
                           "from the album art.", 9.75, tone="dim", wrap=True)
        self.plain.setContentsMargins(6, 16, 0, 0)
        lay.addWidget(self.plain)

        lay.addSpacing(22)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.more = Button("More settings…", "secondary", "more", slot=self._more,
                           tip="Everything in the widget's right-click menu")
        self.reset = Button("Reset look", "plain", "reset", slot=self._reset)
        bottom.addWidget(self.more)
        bottom.addWidget(self.reset)
        bottom.addStretch(1)
        bottom.addWidget(Button("Remove widget", "danger", "trash", slot=self._remove))
        lay.addLayout(bottom)
        lay.addStretch(1)

    # ------------------------------------------------------------------ #
    def set_card(self, card) -> None:
        self.card = card
        if card is None:
            return
        self.name.setText(name_of(card.kind))
        self.sizes.set_options([(s, s.capitalize()) for s in card.supported_sizes],
                               card.size_class)
        self.size_row.setVisible(len(card.supported_sizes) > 1)
        self.sync_where()
        custom = card.customizable
        for w in (self.bg_title, self.bg_group, self.fg_title, self.fg_group, self.reset):
            w.setVisible(custom)
        self.fg_note.setVisible(card.kind == "notes")
        photo = bool(getattr(card, "allow_image", False))
        self.photo_title.setVisible(photo)
        self.photo_group.setVisible(photo)
        self.plain.setVisible(not custom)
        self.refresh()

    def refresh(self) -> None:
        c = self.card
        if c is None:
            return
        try:
            self.detail.setText(describe(c, self.win.board))
        except RuntimeError:
            return
        self.sizes.set_current(c.size_class)
        self.sync_where()
        if c.customizable:
            self.fill.set_current("solid" if appearance.is_solid(c.config) else "glass")
            self.bg.set_current(c.config.get("bg"))
            self.fg.set_current(c.config.get("fg"))
            self.reset.setEnabled(any(c.config.get(k)
                                      for k in ("bg", "fg", "fill", "image")))
        img = c.config.get("image")
        self.photo_name.setText(os.path.basename(img) if img else "No photo")
        self.choose.setText("Change…" if img else "Choose…")
        self.adjust.setVisible(bool(img))
        self.clear.setVisible(bool(img))
        self.render_preview()

    def sync_where(self) -> None:
        """The display the card is on, out of the ones there are - offered only
        when there is a choice, or when the card is waiting for its own."""
        c = self.card
        if c is None:
            return
        board = self.win.board
        names = displays.labels(board.screens)
        try:
            r = c.card_rect()
            here = displays.at(c.x() + r.center().x(), c.y() + r.center().y(),
                               board.screens)
        except RuntimeError:
            return
        options = [(d.key, names[d.key]) for d in spatial(board.screens)]
        if options != self.where.options:
            self.where.set_options(options, here.key)
        else:
            self.where.set_current(here.key)
        self.where_row.setVisible(len(board.screens) > 1 or c.displaced)
        self.where_note_row.setVisible(bool(c.displaced))
        if c.displaced and c.anchor is not None:
            self.where_note.setText(
                f"{board.display_name(c.anchor.display)} isn't connected, so this "
                f"widget is waiting on your {names.get(here.key, 'Main')} display. "
                "It goes back when that monitor does - or choose a display to "
                "keep it on.")
        shown = not (self.size_row.isHidden() and self.where_row.isHidden())
        self.size_title.setVisible(shown)
        self.size_group.setVisible(shown)

    def render_preview(self) -> None:
        if self.card is None:
            return
        well = self.stage.well()
        if well.width() < 40:
            return
        try:
            self.stage.set_image(previews.card_image(self.card, int(well.width()),
                                                     int(well.height()), pad=20))
        except RuntimeError:
            pass

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.render_preview()

    # -- actions --------------------------------------------------------- #
    def _size(self, key: str) -> None:
        if self.card is not None:
            self.card.set_size_class(key)
            self.card.settle()          # a bigger card must stay on the screen
            self.win.board.persist()

    def _move_to(self, key: str) -> None:
        if self.card is not None and self.win.board.move_to_display(self.card, key):
            self.win.board.spotlight(self.card)

    def _set(self, key: str, value) -> None:
        if self.card is not None:
            self.card.set_appearance(key, value)

    def _choose(self) -> None:
        if self.card is not None:
            cropper.choose(self.card, self.win)

    def _adjust(self) -> None:
        if self.card is not None:
            cropper.edit(self.card, self.win)

    def _clear(self) -> None:
        if self.card is not None:
            self.card.set_appearance("image", None)

    def _reset(self) -> None:
        if self.card is not None:
            self.card.reset_appearance()

    def _more(self) -> None:
        if self.card is not None:
            menu = self.card._menu()
            menu.exec(self.more.mapToGlobal(QPoint(0, self.more.height() + 4)))

    def _remove(self) -> None:
        if self.card is not None:
            self.win.remove_card(self.card)

    def _show(self) -> None:
        if self.card is not None:
            self.win.board.spotlight(self.card)


class DesktopPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.rows: List[DeskRow] = []
        self.heads: List[QWidget] = []
        self.selected = None
        self.header = PageHeader("On your desktop", "")

        self.body = QWidget()
        body = QHBoxLayout(self.body)
        body.setContentsMargins(PAGE_MARGIN - 6, 4, PAGE_MARGIN - 12, 0)
        body.setSpacing(22)
        self.list = QWidget()
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(0, 0, 8, 16)
        self.list_lay.setSpacing(2)
        self.list_lay.addStretch(1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.list_area = theme.scroll_area(self.list)
        self.list_area.setFixedWidth(356)
        body.addWidget(self.list_area)
        self.inspector = Inspector(win)
        self.insp_area = theme.scroll_area(self.inspector)
        body.addWidget(self.insp_area, 1)
        self.header.watch(self.list_area)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        for w in (label("Nothing on your desktop yet", 15, QFont.DemiBold),
                  label("Add widgets from the gallery and they'll show up here.",
                        10.5, tone="dim")):
            w.setAlignment(Qt.AlignCenter)
            el.addWidget(w)
        el.addSpacing(16)
        go = Button("Open the gallery", "primary", "gallery", height=36,
                    slot=lambda: win.show_page("gallery"))
        el.addWidget(go, 0, Qt.AlignCenter)
        el.addStretch(2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.header)
        outer.addWidget(self.body, 1)
        outer.addWidget(self.empty, 1)

        # Live thumbnails are redrawn one per tick rather than all at once -
        # nine cards rendered back to back is a tenth of a second the window
        # can't draw a frame in - and not at all while anything is scrolling.
        self._thumbs: list = []
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(40)
        self._thumb_timer.timeout.connect(self._thumb_next)
        self._seen = False
        self.rebuild()

    def _where(self, card):
        """The heading a card belongs under: its display, or the one it waits for."""
        if card.displaced and card.anchor is not None:
            return ("waiting", card.anchor.display)
        d = self.win.board.display_of(card)
        return ("on", d.key if d is not None else "")

    def _groups(self, cards) -> list:
        """The cards under a heading per display, in the order the displays
        sit on the desk - or no headings at all, with only one display."""
        board = self.win.board
        if len(board.screens) < 2 and not any(c.displaced for c in cards):
            return [("", cards)]
        names = displays.labels(board.screens)
        out = []
        for d in spatial(board.screens):
            members = [c for c in cards if self._where(c) == ("on", d.key)]
            if members:
                out.append((f"{names[d.key]} display", members))
        waiting: Dict[str, list] = {}
        for c in cards:
            kind, key = self._where(c)
            if kind == "waiting":
                waiting.setdefault(key, []).append(c)
        for key, members in waiting.items():
            out.append((f"Waiting for {board.display_name(key)}", members))
        listed = {c for _t, members in out for c in members}
        rest = [c for c in cards if c not in listed]
        if rest:
            out.append(("", rest))
        return out

    def rebuild(self) -> None:
        cards = list(self.win.board.cards)
        for w in self.rows + self.heads:
            # Out of the layout now, not whenever the deferred delete runs - a
            # rebuild must never show rows for cards that have gone.
            if isinstance(w, DeskRow):
                self.group.removeButton(w)
            self.list_lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        self.rows, self.heads = [], []
        for i, (title, members) in enumerate(self._groups(cards)):
            if title:
                head = label(title, 9.75, QFont.DemiBold, tone="dim")
                head.setContentsMargins(12, 4 if i == 0 else 16, 0, 6)
                self.list_lay.insertWidget(self.list_lay.count() - 1, head)
                self.heads.append(head)
            for card in members:
                row = DeskRow(card)
                row.where = self._where(card)
                row.clicked.connect(lambda _c=False, c=card: self.select(c))
                self.group.addButton(row)
                self.list_lay.insertWidget(self.list_lay.count() - 1, row)
                self.rows.append(row)
        n = len(cards)
        self.header.subtitle.setText(
            f"{n} widget{'s' if n != 1 else ''}. Pick one to change its size, colours "
            "or background photo." if n else "Widgets you add will appear here.")
        self.body.setVisible(bool(n))
        self.empty.setVisible(not n)
        if self.selected not in cards:
            self.selected = cards[0] if cards else None
        self.select(self.selected)
        if self.isVisible():
            self.refresh_thumbs(now=True)

    def select(self, card) -> None:
        self.selected = card
        for row in self.rows:
            row.setChecked(row.card is card)
        self.inspector.set_card(card)

    def card_changed(self, card) -> None:
        for row in self.rows:
            if row.card is card:
                if row.where != self._where(card):
                    self.rebuild()          # it moved to another display's heading
                    return
                row.refresh()
        if card is self.selected:
            self.inspector.refresh()

    def displays_changed(self) -> None:
        """A monitor came or went: cards may belong under other headings now,
        and one that moved shows a different slice of the glass - which the
        rebuilt rows draw afresh."""
        self.rebuild()

    def refresh_thumbs(self, now: bool = False) -> None:
        if now:
            self._thumb_timer.stop()
            for row in self.rows:
                row.refresh()
            self.inspector.render_preview()
            return
        self._thumbs = [self.inspector, *self.rows]
        self._thumb_timer.start()

    def _thumb_next(self) -> None:
        if self.list_area.scrolling() or self.insp_area.scrolling():
            return
        if not self._thumbs:
            self._thumb_timer.stop()
            return
        item = self._thumbs.pop(0)
        if item is self.inspector:
            item.render_preview()
        elif item in self.rows:
            item.refresh()

    def on_show(self) -> None:
        # The first time, every row needs a picture straight away; after that
        # they only need freshening, which can be spread out.
        self.refresh_thumbs(now=not self._seen)
        self._seen = True


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #
def setting_row(glyph_kind: str, colour: str, title: str, desc: str,
                control: QWidget) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(16, 11, 16, 11)
    h.setSpacing(12)
    h.addWidget(IconTile(glyph_kind, colour), 0, Qt.AlignVCenter)
    text = QVBoxLayout()
    text.setSpacing(1)
    text.addWidget(label(title, 10.5, QFont.Medium))
    d = label(desc, 9.25, tone="dim", wrap=True)
    text.addWidget(d)
    row.desc = d
    h.addLayout(text, 1)
    h.addWidget(control, 0, Qt.AlignVCenter)
    return row


class SettingsPage(QWidget):
    INSET = 56          # separators start under the text, past the icon tile

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        board = win.board
        header = PageHeader("Settings", "How the widgets look, and where they sit.")
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(PAGE_MARGIN, 0, PAGE_MARGIN, 32)
        col.setSpacing(0)

        look = self._group(col, "Look")
        self.theme_seg = Segmented([("dark", "Dark"), ("light", "Light")])
        self.theme_seg.changed.connect(board.set_theme)
        look.add(setting_row("moon", "#5e5ce6", "Theme",
                             "For the widgets and this window.", self.theme_seg), self.INSET)
        self.accents = QWidget()
        al = QHBoxLayout(self.accents)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(0)
        group = QButtonGroup(self.accents)
        group.setExclusive(True)
        self.accent_dots: Dict[str, Swatch] = {}
        for key, hex_colour in ACCENTS.items():
            dot = Swatch(hex_colour, key.capitalize())
            dot.clicked.connect(lambda _c=False, k=key: board.set_accent(k))
            group.addButton(dot)
            al.addWidget(dot)
            self.accent_dots[key] = dot
        look.add(setting_row("", "rainbow", "Accent",
                             "Highlights, today's date, the second hand.", self.accents),
                 self.INSET)
        self.glass = Switch()
        self.glass.toggled.connect(board.set_glass)
        look.add(setting_row("drop", "#0a84ff", "Frosted glass",
                             "The blurred wallpaper showing through each widget.",
                             self.glass), self.INSET)
        self.tint = Segmented(GLASS)
        self.tint.changed.connect(lambda v: board.set_glass_tint(int(v)))
        look.add(setting_row("sliders", "#30b0c7", "Glass strength",
                             "How much tint sits over the blur.", self.tint), self.INSET)
        self.opacity = Segmented(OPACITY)
        self.opacity.changed.connect(lambda v: board.set_opacity(int(v) / 100.0))
        look.add(setting_row("halfcircle", "#8e8e93", "Opacity",
                             "The whole widget, content and all.", self.opacity), self.INSET)

        desk = self._group(col, "On the desktop")
        self.layer = Segmented(LAYERS)
        self.layer.changed.connect(board.set_layer)
        self.layer_row = desk.add(setting_row("layers", "#bf5af2", "Where widgets sit", "",
                                              self.layer), self.INSET)
        self.snap = Switch()
        self.snap.toggled.connect(board.set_snap)
        desk.add(setting_row("grid", "#ff9f0a", "Snap to grid",
                             "Line widgets up on a grid as you drop them.", self.snap),
                 self.INSET)
        self.lock = Switch()
        self.lock.toggled.connect(board.set_locked)
        desk.add(setting_row("lock", "#636366", "Lock positions",
                             "Stop widgets moving when you click or drag them.", self.lock),
                 self.INSET)
        self.peek = Button("Bring to front", "secondary", slot=board.toggle_peek)
        desk.add(setting_row("front", "#0a84ff", "Bring to front",
                             "Show every widget over your windows - or press Ctrl+Alt+W.",
                             self.peek), self.INSET)

        app = self._group(col, "App")
        self.boot = Switch()
        self.boot.toggled.connect(self._set_startup)
        app.add(setting_row("power", "#30d158", "Start with Windows",
                            "Widgets come back when your PC does. This window stays closed.",
                            self.boot), self.INSET)
        app.add(setting_row("link", "#0a84ff", "Desktop shortcut",
                            "A PC Widgets icon on your desktop.",
                            Button("Create", "secondary", slot=self._shortcut)), self.INSET)
        app.add(setting_row("save", "#34c759", "Save layout now",
                            "It saves as you go - this is for peace of mind.",
                            Button("Save", "secondary", slot=self._save)), self.INSET)
        app.add(setting_row("folder", "#64d2ff", "Widgets folder",
                            "Where PC Widgets lives on this PC.",
                            Button("Open", "secondary", slot=self._folder)), self.INSET)
        app.add(setting_row("power", "#ff453a", "Quit PC Widgets",
                            "Takes the widgets off your desktop until you open the app again.",
                            Button("Quit", "danger", slot=self._quit)), self.INSET)
        col.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page(header, body))
        self.sync()

    def _group(self, col: QVBoxLayout, title: str) -> Group:
        col.addWidget(section_title(title))
        g = Group()
        col.addWidget(g)
        return g

    def on_show(self) -> None:
        self.sync()     # the Startup shortcut can change outside the app

    def sync(self) -> None:
        """Show the board's current settings, without re-applying any of them."""
        s = self.win.board.state
        self.theme_seg.set_current(s.theme)
        dot = self.accent_dots.get(s.accent)
        if dot is not None:
            dot.setChecked(True)
        self.glass.set_quietly(s.glass)
        self.tint.set_current(min(GLASS, key=lambda g: abs(int(g[0]) - s.glass_tint))[0])
        self.opacity.set_current(min(OPACITY, key=lambda o: abs(int(o[0]) - s.opacity * 100))[0])
        self.layer.set_current(s.layer)
        self.layer_row.desc.setText(LAYER_TEXT.get(s.layer, ""))
        self.snap.set_quietly(s.snap)
        self.lock.set_quietly(s.locked)
        self.boot.set_quietly(startup.is_enabled())
        if s.layer == "top":
            self.peek.setText("Always on top")
            self.peek.setEnabled(False)
        else:
            self.peek.setEnabled(True)
            self.peek.setText("Send back" if self.win.board.peeked else "Bring to front")

    # -- actions --------------------------------------------------------- #
    def _set_startup(self, on: bool) -> None:
        self.boot.setEnabled(False)

        def done(ok) -> None:
            self.boot.setEnabled(True)
            if not ok:
                self.boot.set_quietly(startup.is_enabled())
                self.win.toast("Couldn't update the Startup folder.")
            else:
                self.win.toast("PC Widgets will start with Windows." if on
                               else "PC Widgets won't start with Windows.")
            if self.win.tray is not None:
                self.win.tray.rebuild_menu()

        in_background(lambda: startup.set_enabled(on), done)

    def _shortcut(self) -> None:
        in_background(startup.create_desktop_shortcut,
                      lambda path: self.win.toast("Shortcut added to your desktop."
                                                  if path else
                                                  "Couldn't create the shortcut."))

    def _save(self) -> None:
        from ..core import store
        ok = self.win.board.persist(force=True)
        n = len(self.win.board.cards)
        self.win.toast(f"Layout saved - {n} widget{'s' if n != 1 else ''}."
                       if ok else f"Couldn't save. See {store.log_path()}")

    def _folder(self) -> None:
        os.startfile(os.path.dirname(startup.app_link()))

    def _quit(self) -> None:
        if self.win.tray is not None:
            self.win.tray._quit()


# --------------------------------------------------------------------------- #
#  Presets
# --------------------------------------------------------------------------- #
def when(ts: float) -> str:
    """When something was saved, the way you'd say it."""
    if not ts:
        return ""
    t, now = time.localtime(ts), time.localtime()
    clock = time.strftime("%H:%M", t)
    if t[:3] == now[:3]:
        return f"today at {clock}"
    if t[:3] == time.localtime(time.time() - 86400)[:3]:
        return f"yesterday at {clock}"
    if t.tm_year == now.tm_year:
        return f"{t.tm_mday} {time.strftime('%b', t)}"
    return f"{t.tm_mday} {time.strftime('%b %Y', t)}"


def paint_map(p: QPainter, box: QRectF, screens, cards, names=None,
              small: bool = False) -> None:
    """The desk from above: every display in its place and to scale, and a
    block for each widget where it sits on it.

    ``screens`` is ``[(key, rect, connected)]`` and ``cards`` ``[(rect, away)]``,
    both in virtual-screen pixels; ``away`` marks a widget waiting for a display
    that isn't connected. ``names`` puts a label under each display.
    """
    ui = theme.current()
    if not screens:
        return
    l = min(s[1][0] for s in screens)
    t = min(s[1][1] for s in screens)
    r = max(s[1][2] for s in screens)
    b = max(s[1][3] for s in screens)
    w, h = max(1, r - l), max(1, b - t)
    below = 0.0 if (small or not names) else 24.0
    pad = 2.0 if small else 8.0
    scale = min((box.width() - 2 * pad) / w, (box.height() - 2 * pad - below) / h)
    ox = box.left() + (box.width() - w * scale) / 2.0
    oy = box.top() + pad + (box.height() - 2 * pad - below - h * scale) / 2.0

    def to(rc) -> QRectF:
        return QRectF(ox + (rc[0] - l) * scale, oy + (rc[1] - t) * scale,
                      (rc[2] - rc[0]) * scale, (rc[3] - rc[1]) * scale)

    # A gap between neighbours, so two monitors side by side read as two.
    gap, rad = (1.5, 3.5) if small else (3.0, 9.0)
    for key, rc, connected in screens:
        sr = to(rc).adjusted(gap, gap, -gap, -gap)
        if connected:
            p.fillPath(smooth(sr, rad), QColor(ui.control_hi))
        else:
            p.save()
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(ui.text_faint), 1.0, Qt.DashLine))
            p.drawPath(smooth(sr.adjusted(0.5, 0.5, -0.5, -0.5), rad))
            p.restore()
        if names and key in names and not small:
            p.setFont(theme.font(9.25, QFont.DemiBold))
            p.setPen(QColor(ui.text if connected else ui.text_dim))
            p.drawText(QRectF(sr.left() - 30, sr.bottom() + 6, sr.width() + 60, 16),
                       Qt.AlignHCenter | Qt.AlignTop, names[key])
    ink = QColor(ui.text)
    ink.setAlphaF(0.80)
    for rc, away in cards:
        cr = to(rc)
        cr.setWidth(max(cr.width(), 2.0))
        cr.setHeight(max(cr.height(), 2.0))
        p.fillPath(smooth(cr, max(1.0, RADIUS * scale)),
                   QColor(ui.accent) if away else ink)


def waiting_note(board: Board) -> str:
    """What saving does with widgets that are waiting for their display - kept
    on it, so a preset saved while a monitor sleeps still puts them back."""
    keys = {c.anchor.display for c in board.cards
            if c.displaced and c.anchor is not None}
    if not keys:
        return ""
    name = board.display_name(next(iter(keys))) if len(keys) == 1 else "a display"
    return f"Widgets waiting for {name} are kept on it."


def preset_model(preset, board: Board):
    """A preset's displays and widgets for paint_map: drawn as it was saved,
    with each display marked by whether it is connected now."""
    found = displays.resolve(preset.displays, board.screens)
    screens = []
    for key, info in preset.displays.items():
        d = displays.from_info(key, info)
        if d is not None:
            screens.append((key, d.rect, key in found))
    m = int(MARGIN)
    cards = []
    for pl in preset.placements:
        w, h = placement_size(pl)
        cards.append(((pl.x + m, pl.y + m, pl.x + m + w, pl.y + m + h), False))
    return screens, cards


class DesktopMap(Cached, QWidget):
    """Your displays as they are arranged, with the widgets on each where they
    are right now, and a line about each display beside the picture."""

    H = 250

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.setFixedHeight(self.H)

    def _model(self):
        board = self.win.board
        names = displays.labels(board.screens)
        screens = [(d.key, d.rect, True) for d in board.screens]
        cards, counts, waiting = [], Counter(), Counter()
        for c in board.cards:
            try:
                r = c.card_rect()
                x, y = c.x() + r.left(), c.y() + r.top()
            except RuntimeError:
                continue                    # went away mid-paint
            cards.append(((int(x), int(y), int(x + r.width()), int(y + r.height())),
                          bool(c.displaced)))
            d = board.display_of(c)
            if c.displaced and c.anchor is not None:
                waiting[c.anchor.display] += 1
            elif d is not None:
                counts[d.key] += 1
        return names, screens, cards, counts, waiting

    def paint_key(self):
        names, screens, cards, counts, waiting = self._model()
        return (tuple(sorted(names.items())), tuple(screens), tuple(cards),
                tuple(sorted(counts.items())), tuple(sorted(waiting.items())))

    def paint(self, p: QPainter) -> None:
        ui = theme.current()
        hints(p)
        board = self.win.board
        names, screens, cards, counts, waiting = self._model()
        r = QRectF(self.rect())
        paint_surface(p, r)
        split = r.width() * 0.56
        paint_map(p, QRectF(r.left() + 18, r.top() + 16, split - 30, r.height() - 32),
                  screens, cards, names)

        x = r.left() + split + 4
        width = max(40.0, r.right() - x - 22)
        y = r.top() + 30
        for d in spatial(board.screens):
            n = counts.get(d.key, 0)
            p.setFont(theme.font(11, QFont.DemiBold))
            p.setPen(QColor(ui.text))
            p.drawText(QRectF(x, y, width, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       names.get(d.key, d.name))
            line = (f"{d.name} · {d.size[0]} × {d.size[1]} · "
                    + (f"{n} widget{'s' if n != 1 else ''}" if n else "no widgets"))
            p.setFont(theme.font(9.25))
            p.setPen(QColor(ui.text_dim))
            p.drawText(QRectF(x, y + 20, width, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(line, Qt.ElideRight, int(width)))
            y += 52
        for key, n in sorted(waiting.items()):
            p.setFont(theme.font(9.25, QFont.Medium))
            p.setPen(QColor(ui.accent_text))
            p.drawText(QRectF(x, y, width, 40), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                       f"{n} widget{'s' if n != 1 else ''} waiting for "
                       f"{board.display_name(key)} - on your Main display until "
                       "it's back.")
            y += 44


class PresetRow(Cached, QWidget):
    """One saved preset: the desk as it has it, its name and what is in it, and
    Apply - or, when the desktop is laid out exactly like it, "In use"."""

    H = 96
    THUMB = (136, 76)

    def __init__(self, page: "PresetsPage", preset) -> None:
        super().__init__()
        self.page = page
        self.win = page.win
        self.preset = preset
        self.in_use = False
        self._editing = False
        self.setFixedHeight(self.H)
        self.apply = Button("Apply", "secondary", parent=self, height=30,
                            slot=lambda: self.page.apply(self.preset),
                            tip="Lay your desktop out like this")
        self.more = Button("", "plain", "more", self, height=30, slot=self._menu,
                           tip="Update, rename or delete")
        # Outside a layout a widget keeps Qt's default size, not its own.
        for b in (self.apply, self.more):
            b.resize(b.sizeHint())
        self.field = TextField("Name this preset", self, height=30)
        self.field.hide()
        self.field.returnPressed.connect(self._commit)
        self.field.editingFinished.connect(self._commit)
        self.field.cancelled.connect(self._cancel)

    def text_left(self) -> float:
        return 16 + self.THUMB[0] + 16

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        y = (self.height() - 30) // 2
        self.more.move(self.width() - 14 - self.more.width(), y)
        self.apply.move(self.more.x() - 8 - self.apply.width(), y)
        x = int(self.text_left()) - 13
        self.field.setGeometry(x, self.height() // 2 - 28,
                               max(140, min(380, self.apply.x() - 16 - x)), 30)

    def set_in_use(self, on: bool) -> None:
        if on != self.in_use:
            self.in_use = on
            self.apply.setVisible(not on)
            self.update()

    def detail(self) -> str:
        pre, board = self.preset, self.win.board
        n = len(pre.placements)
        keys = {pl.display for pl in pre.placements}
        bits = [f"{n} widget{'s' if n != 1 else ''}"]
        if len(keys) > 1:
            bits.append(f"{len(keys)} displays")
        found = displays.resolve(pre.displays, board.screens)
        gone = sorted(k for k in keys if k not in found)
        if gone:
            name = pre.displays.get(gone[0], {}).get("name") or board.display_name(gone[0])
            bits.append(f"{name} not connected")
        if pre.saved:
            bits.append(f"saved {when(pre.saved)}")
        return " · ".join(bits)

    def paint_key(self):
        pre = self.preset
        screens, cards = preset_model(pre, self.win.board)
        return (pre.name, self.in_use, self._editing, self.detail(), tuple(screens),
                tuple(cards), self.apply.x())

    def paint(self, p: QPainter) -> None:
        ui = theme.current()
        hints(p)
        tw, th = self.THUMB
        box = QRectF(16, (self.height() - th) / 2.0, tw, th)
        p.save()
        p.setClipPath(smooth(box, 12.0))
        p.fillRect(box, QColor(ui.well))
        p.restore()
        screens, cards = preset_model(self.preset, self.win.board)
        paint_map(p, box.adjusted(7, 7, -7, -7), screens, cards, small=True)

        chip = None
        if self.in_use:
            f = theme.font(9.25, QFont.DemiBold)
            cw = QFontMetricsF(f).horizontalAdvance("In use") + 36
            chip = QRectF(self.more.x() - 8 - cw, self.height() / 2.0 - 13, cw, 26)
        x = self.text_left()
        w = (chip.left() if chip is not None else self.apply.x()) - 14 - x
        mid = self.height() / 2.0
        if not self._editing:
            p.setFont(theme.font(11.5, QFont.DemiBold))
            p.setPen(QColor(ui.text))
            p.drawText(QRectF(x, mid - 24, w, 22), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.preset.name, Qt.ElideRight, int(w)))
        p.setFont(theme.font(9.25))
        p.setPen(QColor(ui.text_dim))
        p.drawText(QRectF(x, mid + 3, w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.detail(), Qt.ElideRight, int(w)))
        if chip is not None:
            p.fillPath(rounded(chip, 13), QColor(ui.accent_soft))
            glyph(p, "check", QRectF(chip.left() + 11, chip.top() + 7, 12, 12),
                  ui.accent_text, 1.8)
            p.setFont(theme.font(9.25, QFont.DemiBold))
            p.setPen(QColor(ui.accent_text))
            p.drawText(QRectF(chip.left() + 27, chip.top(), chip.width() - 30, chip.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, "In use")

    # -- naming ---------------------------------------------------------- #
    def rename(self) -> None:
        self._editing = True
        self.field.restyle()
        self.field.setText(self.preset.name)
        self.field.show()
        self.field.setFocus(Qt.OtherFocusReason)
        self.field.selectAll()
        self.update()

    def _commit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        name = self.field.text().strip()
        self.field.hide()
        self.update()
        if name and name != self.preset.name \
                and not self.win.board.rename_preset(self.preset, name):
            self.win.toast(f"There's already a preset called “{name}”.")

    def _cancel(self) -> None:
        self._editing = False
        self.field.hide()
        self.update()

    def _menu(self) -> None:
        m = QMenu(self)
        update = QAction("Update with this layout", m)
        update.setEnabled(bool(self.win.board.cards))
        update.triggered.connect(lambda: self.page.update_preset(self.preset))
        rename = QAction("Rename", m)
        rename.triggered.connect(self.rename)
        delete = QAction("Delete", m)
        delete.triggered.connect(lambda: self.page.delete(self.preset))
        m.addAction(update)
        m.addAction(rename)
        m.addSeparator()
        m.addAction(delete)
        m.exec(self.more.mapToGlobal(QPoint(0, self.more.height() + 4)))


class PresetsPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.rows: List[PresetRow] = []
        self.save = Button("Save this layout", "primary", "plus", height=34,
                           slot=self._save_new,
                           tip="Every widget on every display, as a new preset")
        header = PageHeader("Presets", "Save every widget on every display as a "
                                       "preset, and switch between them in a click.",
                            self.save)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(PAGE_MARGIN, 0, PAGE_MARGIN, 32)
        col.setSpacing(0)
        self.map_title = section_title("Your displays")
        col.addWidget(self.map_title)
        self.map = DesktopMap(win)
        col.addWidget(self.map)
        col.addWidget(section_title("Saved presets"))
        # Separators start under the text, past the picture of the desk.
        self.group = Group(inset=int(16 + PresetRow.THUMB[0] + 16))
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(18, 16, 18, 18)
        el.setSpacing(3)
        el.addWidget(label("No presets yet", 11, QFont.DemiBold))
        el.addWidget(label("Save the layout you have now - every widget, on every "
                           "display - and switch back to it whenever you like. "
                           "Presets are in the tray menu too.", 9.75, tone="dim",
                           wrap=True))
        self.group.add(self.empty)
        col.addWidget(self.group)
        col.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        pg = page(header, body)
        self.area = pg.area
        outer.addWidget(pg)
        self.rebuild()

    def rebuild(self) -> None:
        for row in self.rows:
            self.group.lay.removeWidget(row)
            self.group.rows.remove(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        for preset in self.win.board.state.presets:
            row = PresetRow(self, preset)
            self.group.add(row)
            self.rows.append(row)
        self.empty.setVisible(not self.rows)
        self.refresh()

    def refresh(self) -> None:
        """Follow the board: which preset is in use, the map, the Save button."""
        board = self.win.board
        for row in self.rows:
            row.set_in_use(board.preset_in_use(row.preset))
            row.update()
        self.save.setEnabled(bool(board.cards))
        self.map_title.setText("Your displays" if len(board.screens) > 1
                               else "Your display")
        self.map.update()
        self.group.update()

    def on_show(self) -> None:
        self.refresh()

    # -- actions --------------------------------------------------------- #
    def _save_new(self) -> None:
        board = self.win.board
        preset = board.save_preset()
        if preset is None:
            self.win.toast("Add some widgets first - a preset is a layout of them.")
            return
        waiting = waiting_note(board)
        self.win.toast(f"Saved “{preset.name}”. "
                       + (waiting or "Type a name for it, or keep this one."))
        row = next((r for r in self.rows if r.preset is preset), None)
        if row is not None:
            row.rename()
            QTimer.singleShot(0, lambda r=row: self.area.ensureWidgetVisible(r))

    def apply(self, preset) -> None:
        board = self.win.board
        before = board.apply_preset(preset)
        if before is None:
            self.win.toast(f"“{preset.name}” has no widgets in it.")
            return
        self.win.toast(f"“{preset.name}” is on your desktop.", "Undo",
                       lambda: board.apply_layout(before.placements, before.displays))

    def update_preset(self, preset) -> None:
        board = self.win.board
        if board.update_preset(preset) is not None:
            self.win.toast(f"Updated “{preset.name}” to this layout.", "Undo",
                           lambda: board.put_preset(preset))

    def delete(self, preset) -> None:
        board = self.win.board
        i = board.remove_preset(preset)
        if i >= 0:
            self.win.toast(f"Deleted “{preset.name}”.", "Undo",
                           lambda: board.put_preset(preset, i))


# --------------------------------------------------------------------------- #
#  The window
# --------------------------------------------------------------------------- #
class MainWindow(Chrome, QWidget):
    SIDE_W = 244

    def __init__(self, board: Board, tray=None) -> None:
        super().__init__(None)
        self.board = board
        self.tray = tray
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("PC Widgets")
        ico = startup.icon_path()
        if ico:
            self.setWindowIcon(QIcon(ico))
        self._fit_to_screen()
        self._look = None
        self._apply_look()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = Sidebar(self)
        root.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.gallery = GalleryPage(self)
        self.desktop = DesktopPage(self)
        self.presets = PresetsPage(self)
        self.settings = SettingsPage(self)
        self.pages = {"gallery": self.gallery, "desktop": self.desktop,
                      "presets": self.presets, "settings": self.settings}
        for pg in self.pages.values():
            self.stack.addWidget(pg)
        self.toast_box = Toast(self)

        board.cards_changed.connect(self._cards_changed)
        board.card_changed.connect(self._card_changed)
        board.prefs_changed.connect(self._prefs_changed)
        board.presets_changed.connect(self._presets_changed)
        board.displays_changed.connect(self._displays_changed)
        # The sidebar is frosted with the board's blurred wallpaper; a new
        # wallpaper is a new picture behind it.
        board.glass_changed.connect(lambda: self.update(self.side_rect()))

        # Thumbnails of live cards go stale - a clock moves on - so they are
        # redrawn now and then, but only while anyone can see them.
        self._live = QTimer(self)
        self._live.timeout.connect(self._tick)
        self._live.start(4000)
        self.init_chrome(side=lambda: self.SIDE_W, glass=lambda: self.board.glass)
        self.show_page("gallery")

    def _fit_to_screen(self) -> None:
        # Sizes are of the visible window; the shadow's margin goes round them.
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1040)
        m = theme.WINDOW_SHADOW
        w, h = min(1200, area.width() - 60), min(860, area.height() - 60)
        self.resize(w + 2 * m, h + 2 * m)
        self.setMinimumSize(min(1060, w) + 2 * m, min(640, h) + 2 * m)
        self.move(area.center() - QPoint(w // 2 + m, h // 2 + m))

    def _apply_look(self) -> None:
        # No stylesheet on the window: one there routes every widget inside it
        # through Qt's stylesheet style, which a scrolling page pays for on
        # every frame. The few stock controls left carry their own.
        ui = theme.build(self.board.palette)
        theme.set_current(ui)
        self.setPalette(theme.qpalette(ui))
        self.setFont(theme.font(10))
        for field in self.findChildren(SearchField) + self.findChildren(TextField):
            field.restyle()
        if self.testAttribute(Qt.WA_WState_Created):
            winapi.set_dark_frame(int(self.winId()), ui.dark)
        s = self.board.state
        self._look = (s.theme, s.accent, s.glass, s.glass_tint, s.opacity)

    # ------------------------------------------------------------------ #
    def show_page(self, key: str) -> None:
        pg = self.pages.get(key, self.gallery)
        old = self.stack.currentWidget()
        if self.isVisible() and old is not None and old is not pg:
            Crossfade(self.stack, old.grab())
        self.stack.setCurrentWidget(pg)
        self.sidebar.set_page(key if key in self.pages else "gallery")
        if self.isVisible():
            pg.on_show()

    def bring_up(self, page: Optional[str] = None) -> None:
        if page:
            self.show_page(page)
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        # Windows applies a launching shortcut's "Run: Minimised" to the first
        # ordinary window the process shows - which is this one, even hours
        # after a minimised launch at boot. Undo it once it has happened.
        QTimer.singleShot(0, self._unminimise)
        QTimer.singleShot(200, self._unminimise)

    def _unminimise(self) -> None:
        if self.isVisible() and (self.isMinimized()
                                 or winapi.is_minimised(int(self.winId()))):
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def toast(self, text: str, action: str = "", fn: Optional[Callable] = None) -> None:
        self.toast_box.show_message(text, action, fn)

    def display_here(self) -> displays.Display:
        """The display this window is on: where someone using it is looking."""
        g = self.frameGeometry()
        return displays.at(g.center().x(), g.center().y(), self.board.screens)

    def remove_card(self, card) -> None:
        pl = self.board.remove(card)
        if pl is not None:
            self.toast(f"Removed {name_of(pl.kind)}.", "Undo",
                       lambda: self.board.restore(pl))

    # -- board events ---------------------------------------------------- #
    def _cards_changed(self) -> None:
        self.desktop.rebuild()
        self.gallery.refresh_counts()
        self.presets.refresh()
        self.sidebar.sync()

    def _card_changed(self, card) -> None:
        self.desktop.card_changed(card)
        self.presets.refresh()

    def _presets_changed(self) -> None:
        self.presets.rebuild()
        self.sidebar.sync()

    def _displays_changed(self) -> None:
        self.gallery.sync_displays()
        self.desktop.displays_changed()
        self.presets.refresh()

    def _prefs_changed(self) -> None:
        s = self.board.state
        look = (s.theme, s.accent, s.glass, s.glass_tint, s.opacity)
        if look != self._look:
            self._apply_look()
            for w in self.findChildren(QWidget):
                w.update()
            self.update()
            self.gallery.refresh_previews()
            self.desktop.refresh_thumbs()
        self.settings.sync()
        self.sidebar.sync()

    def _tick(self) -> None:
        if self.isVisible() and not self.isMinimized() \
                and self.stack.currentWidget() is self.desktop:
            self.desktop.refresh_thumbs()

    # -- window events --------------------------------------------------- #
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        self.paint_backdrop(p, ev.rect())
        p.end()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.stack.currentWidget().on_show()
        self.sidebar.sync()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self.toast_box.isVisible():
            self.toast_box.place()

    def closeEvent(self, ev) -> None:
        """Hide rather than quit: the widgets are the app, not this window."""
        ev.ignore()
        self.hide()
        state = self.board.state
        if self.tray is not None and not state.close_hint_shown:
            state.close_hint_shown = True
            self.board.persist()
            self.tray.showMessage(
                "PC Widgets is still running",
                "Your widgets stay on the desktop. Click the tray icon to open "
                "this window again.", self.tray.icon(), 6000)
