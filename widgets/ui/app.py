"""
app.py
======
The PC Widgets window - everything the tray menu does, somewhere you can see it.

Three pages:

- **Gallery**: every widget, previewed live at the size you pick, one click to
  put it on the desktop.
- **On your desktop**: the widgets you have, each with its size, colours and
  background photo, and a way to find or remove one.
- **Settings**: theme, accent, glass, where widgets sit, start with Windows.

The widgets are the point and this window is only how you arrange them, so
closing it hides it: the widgets stay on the desktop, and the tray icon or
launching the app again brings the window straight back.

Nothing here keeps its own copy of the board. Every page reads the cards and
preferences when it draws, and redraws when the board says something changed -
because a change can just as easily come from a card's right-click menu or the
tray as from this window.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import (
    QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt,
    QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..core import appearance, startup, winapi
from ..core.board import REGISTRY, Board
from ..core.palette import ACCENTS
from . import cropper, previews, theme
from .theme import Segmented, Swatch, SwatchRow, Switch

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
    "desktop": "Behind your windows, like icons on the desktop. "
               "Ctrl+Alt+W brings them forward.",
    "normal": "In among your other windows.",
    "top": "Above everything, all the time.",
}
GLASS = (("78", "Clearer"), ("112", "Balanced"), ("158", "Frostier"))
OPACITY = (("50", "50%"), ("65", "65%"), ("80", "80%"), ("90", "90%"),
           ("100", "100%"))


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


def describe(card) -> str:
    """One line about a placed card: its size, background and anything telling."""
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
    return " · ".join(bits)


# --------------------------------------------------------------------------- #
#  Small parts
# --------------------------------------------------------------------------- #
def label(text: str = "", size: float = 10.0, weight=QFont.Normal,
          tone: Optional[str] = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setFont(theme.font(size, weight))
    if tone:
        lab.setProperty("tone", tone)
    if wrap:
        lab.setWordWrap(True)
    return lab


def button(text: str, kind: Optional[str] = None,
           slot: Optional[Callable] = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(theme.font(10, QFont.DemiBold if kind == "primary" else QFont.Medium))
    if kind:
        b.setProperty("kind", kind)
    if slot is not None:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def rule() -> QFrame:
    f = QFrame()
    f.setProperty("role", "rule")
    f.setFixedHeight(1)
    return f


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


def draw_glyph(p: QPainter, kind: str, r: QRectF, colour: str) -> None:
    """The sidebar's icons, drawn - the app ships no image assets for UI."""
    c = QColor(colour)
    p.save()
    if kind == "gallery":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        s = (r.width() - 3.0) / 2.0
        for i in (0, 1):
            for j in (0, 1):
                p.drawRoundedRect(QRectF(r.left() + i * (s + 3), r.top() + j * (s + 3),
                                         s, s), 2.4, 2.4)
    elif kind == "desktop":
        p.setPen(QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        screen = QRectF(r.left() + 0.8, r.top() + 1.6, r.width() - 1.6,
                        r.height() * 0.64)
        p.drawRoundedRect(screen, 2.6, 2.6)
        cx = r.center().x()
        p.drawLine(QPointF(cx, screen.bottom()), QPointF(cx, r.bottom() - 1.6))
        p.drawLine(QPointF(cx - 4.5, r.bottom() - 1.2), QPointF(cx + 4.5, r.bottom() - 1.2))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(screen.right() - 7.0, screen.top() + 2.6, 4.6, 4.6),
                          1.2, 1.2)
    elif kind == "settings":
        bg = QColor(theme.current().sidebar)
        for i, at in enumerate((0.70, 0.30, 0.58)):
            y = r.top() + 3.0 + i * (r.height() - 6.0) / 2.0
            p.setPen(QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(r.left() + 1, y), QPointF(r.right() - 1, y))
            p.setBrush(bg)
            p.drawEllipse(QPointF(r.left() + at * r.width(), y), 2.7, 2.7)
    p.restore()


class Preview(QWidget):
    """A composed picture of a widget, in a rounded well."""

    def __init__(self, radius: float = 12.0, parent=None) -> None:
        super().__init__(parent)
        self.radius = radius
        self._img = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_image(self, img) -> None:
        self._img = img
        self.update()

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        p.setClipPath(clip)
        p.fillRect(self.rect(), QColor(ui.raised))
        if self._img is not None and not self._img.isNull():
            p.drawImage(QPointF((self.width() - self._img.width()) / 2.0,
                                (self.height() - self._img.height()) / 2.0),
                        self._img)
        p.end()


class TileLayout(QLayout):
    """Equal columns that reflow with the window: as many tiles across as fit
    at ``min_w`` each, then stretched to share the width evenly."""

    def __init__(self, parent=None, min_w: int = 268, gap: int = 16) -> None:
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
        cols = max(1, (rect.width() + self.gap) // (self.min_w + self.gap))
        tile_w = (rect.width() - self.gap * (cols - 1)) / cols
        y, row_h = rect.y(), 0
        for i, item in enumerate(self._items):
            col = i % cols
            if col == 0 and i:
                y += row_h + self.gap
                row_h = 0
            h = item.sizeHint().height()
            if apply:
                item.setGeometry(QRect(round(rect.x() + col * (tile_w + self.gap)), y,
                                       round(tile_w), h))
            row_h = max(row_h, h)
        return (y + row_h - rect.y()) if self._items else 0


class Toast(QFrame):
    """A short message at the foot of the window, with an optional action."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 8, 8)
        lay.setSpacing(10)
        self.text = label("", 10, QFont.Medium)
        self.action = QPushButton()
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.setFont(theme.font(10, QFont.DemiBold))
        self.action.clicked.connect(self._act)
        lay.addWidget(self.text)
        lay.addWidget(self.action)
        self._fn: Optional[Callable] = None
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.finished.connect(self._faded)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.hide()

    def show_message(self, text: str, action: str = "",
                     fn: Optional[Callable] = None, ms: int = 4500) -> None:
        self.text.setText(text)
        self._fn = fn
        self.action.setText(action)
        self.action.setVisible(bool(action and fn))
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def place(self) -> None:
        host = self.parentWidget()
        side = getattr(host, "sidebar", None)
        left = side.width() if side is not None else 0
        x = left + (host.width() - left - self.width()) // 2
        self.move(max(8, x), host.height() - self.height() - 22)

    def dismiss(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _faded(self) -> None:
        if self._fx.opacity() <= 0.01:
            self.hide()

    def _act(self) -> None:
        fn, self._fn = self._fn, None
        self.dismiss()
        if fn is not None:
            fn()


# --------------------------------------------------------------------------- #
#  Sidebar
# --------------------------------------------------------------------------- #
class NavButton(QAbstractButton):
    def __init__(self, text: str, glyph: str, parent=None) -> None:
        super().__init__(parent)
        self.glyph = glyph
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        self._hover = False

    def enterEvent(self, _ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _ev) -> None:
        self._hover = False
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(180, 38)

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = QRectF(self.rect())
        if self.isChecked() or self._hover:
            fill = QColor(ui.raised)
            if not self.isChecked():
                fill.setAlpha(140)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 9, 9)
        draw_glyph(p, self.glyph, QRectF(12, (r.height() - 18) / 2.0, 18, 18),
                   ui.accent if self.isChecked() else ui.text_dim)
        p.setFont(theme.font(10.5, QFont.DemiBold if self.isChecked() else QFont.Medium))
        p.setPen(QColor(ui.text))
        p.drawText(QRectF(42, 0, r.width() - 46, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()


class Sidebar(QFrame):
    PAGES = (("gallery", "Gallery", "gallery"),
             ("desktop", "On your desktop", "desktop"),
             ("settings", "Settings", "settings"))

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.setObjectName("Sidebar")
        self.setFixedWidth(232)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 22, 16, 18)
        lay.setSpacing(0)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel()
        ico = startup.icon_path()
        if ico:
            mark.setPixmap(QIcon(ico).pixmap(32, 32))
        brand.addWidget(mark)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("PC Widgets", 13, QFont.Bold))
        self.count = label("", 9, tone="dim")
        names.addWidget(self.count)
        brand.addLayout(names, 1)
        lay.addLayout(brand)
        lay.addSpacing(26)

        self.nav: Dict[str, NavButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, text, glyph in self.PAGES:
            b = NavButton(text, glyph)
            b.clicked.connect(lambda _c=False, k=key: win.show_page(k))
            group.addButton(b)
            lay.addWidget(b)
            lay.addSpacing(2)
            self.nav[key] = b
        lay.addStretch(1)

        self.peek = button("Bring to front", slot=win.board.toggle_peek,
                           tip="Show every widget over your windows")
        lay.addWidget(self.peek)
        lay.addSpacing(6)
        hint = label("or press Ctrl+Alt+W", 8.75, tone="faint")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)
        self.sync()

    def set_page(self, key: str) -> None:
        self.nav[key].setChecked(True)

    def sync(self) -> None:
        n = len(self.win.board.cards)
        self.count.setText(f"{n} widget{'s' if n != 1 else ''} on your desktop"
                           if n else "Nothing on your desktop yet")
        board = self.win.board
        if board.state.layer == "top":
            self.peek.setText("Always on top")
            self.peek.setEnabled(False)
        else:
            self.peek.setEnabled(True)
            self.peek.setText("Send back" if board.peeked else "Bring to front")


# --------------------------------------------------------------------------- #
#  Gallery
# --------------------------------------------------------------------------- #
class GalleryTile(QFrame):
    PREVIEW_H = 172

    def __init__(self, win: "MainWindow", cls) -> None:
        super().__init__()
        self.win = win
        self.kind = cls.kind
        self.setProperty("role", "panel")
        sizes = list(cls.supported_sizes)
        self.size_key = "medium" if "medium" in sizes else sizes[0]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 14)
        lay.setSpacing(0)
        self.preview = Preview(10.0)
        self.preview.setFixedHeight(self.PREVIEW_H)
        lay.addWidget(self.preview)
        lay.addSpacing(12)

        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 2, 0)
        head.setSpacing(8)
        head.addWidget(label(name_of(self.kind), 12, QFont.DemiBold))
        head.addStretch(1)
        self.badge = label("", 8.5, QFont.DemiBold)
        self.badge.setProperty("role", "badge")
        self.badge.hide()
        head.addWidget(self.badge)
        lay.addLayout(head)
        lay.addSpacing(3)

        desc = label(about(self.kind), 9.5, tone="dim", wrap=True)
        desc.setContentsMargins(4, 0, 4, 0)
        desc.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        desc.setFixedHeight(36)
        lay.addWidget(desc)
        lay.addSpacing(10)

        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(8)
        self.sizes = Segmented([(s, s.capitalize()) for s in sizes], self.size_key,
                               height=28)
        self.sizes.changed.connect(self._size_changed)
        row.addWidget(self.sizes)
        row.addStretch(1)
        self.add = button("Add", "primary", self._add,
                          tip=f"Put a {name_of(self.kind)} widget on your desktop")
        self.add.setMinimumWidth(68)
        row.addWidget(self.add)
        lay.addLayout(row)

    def _size_changed(self, key: str) -> None:
        self.size_key = key
        self.render_preview()

    def render_preview(self) -> None:
        w, h = self.preview.width(), self.preview.height()
        if w < 40 or h < 40:
            return
        b = self.win.board
        self.preview.set_image(previews.gallery_image(
            self.kind, self.size_key, b.palette,
            b.glass if b.state.glass else None, b.state.opacity,
            b.state.glass_tint, w, h))

    def set_count(self, n: int) -> None:
        self.badge.setText("On desktop" if n == 1 else f"{n} on desktop")
        self.badge.setVisible(n > 0)

    def _add(self) -> None:
        board = self.win.board
        card = board.add(self.kind, self.size_key)
        name = name_of(self.kind)
        if card is None:
            self.win.toast(f"Couldn't add {name}.")
            return
        board.spotlight(card)
        self.win.toast(f"{name} added to your desktop.", "Show",
                       lambda c=card: board.spotlight(c))


class GalleryPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.tiles: List[GalleryTile] = []
        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(32, 28, 32, 32)
        col.setSpacing(0)
        col.addWidget(label("Gallery", 22, QFont.Bold))
        col.addSpacing(4)
        col.addWidget(label("Pick a widget and a size, and it goes straight onto "
                            "your desktop.", 10.5, tone="dim"))
        for title, kinds in sections():
            col.addSpacing(26)
            col.addWidget(label(title, 12.5, QFont.DemiBold))
            col.addSpacing(12)
            grid = QWidget()
            tiles = TileLayout(grid)
            for kind in kinds:
                tile = GalleryTile(win, REGISTRY[kind])
                tiles.addWidget(tile)
                self.tiles.append(tile)
            col.addWidget(grid)
        col.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(theme.scroll_area(content))

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

    def on_show(self) -> None:
        self.refresh_counts()
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
class DeskRow(QAbstractButton):
    THUMB = (116, 64)

    def __init__(self, card, parent=None) -> None:
        super().__init__(parent)
        self.card = card
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(80)
        self._thumb = None
        self._hover = False

    def refresh(self) -> None:
        try:
            self._thumb = previews.card_image(self.card, *self.THUMB, pad=4)
        except RuntimeError:
            self._thumb = None            # the card went away mid-refresh
        self.update()

    def enterEvent(self, _ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = QRectF(self.rect())
        if self.isChecked() or self._hover:
            fill = QColor(ui.raised)
            if not self.isChecked():
                fill.setAlpha(120)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 12, 12)
        tw, th = self.THUMB
        box = QRectF(10, (r.height() - th) / 2.0, tw, th)
        clip = QPainterPath()
        clip.addRoundedRect(box, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.fillRect(box, QColor(ui.sidebar))
        if self._thumb is not None:
            p.drawImage(box.topLeft(), self._thumb)
        p.restore()

        x = box.right() + 14
        text_w = r.width() - x - 12
        try:
            title, detail = name_of(self.card.kind), describe(self.card)
        except RuntimeError:
            title, detail = "", ""
        p.setPen(QColor(ui.text))
        p.setFont(theme.font(11, QFont.DemiBold))
        p.drawText(QRectF(x, r.height() / 2 - 20, text_w, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, title)
        p.setPen(QColor(ui.text_dim))
        f = theme.font(9.25)
        p.setFont(f)
        detail = p.fontMetrics().elidedText(detail, Qt.ElideRight, int(text_w))
        p.drawText(QRectF(x, r.height() / 2 + 1, text_w, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, detail)
        p.end()


class Inspector(QFrame):
    """Everything about one placed card."""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.card = None
        self.setObjectName("Inspector")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(0)

        self.preview = Preview(12.0)
        self.preview.setFixedHeight(200)
        lay.addWidget(self.preview)
        lay.addSpacing(16)

        top = QHBoxLayout()
        self.name = label("", 15, QFont.Bold)
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(button("Show on desktop", "flat", self._show,
                             tip="Lift it above your windows for a moment"))
        lay.addLayout(top)
        self.detail = label("", 9.75, tone="dim")
        lay.addWidget(self.detail)
        lay.addSpacing(16)
        lay.addWidget(rule())

        self.size_box, size_lay = self._section(lay, "Size")
        self.sizes = Segmented([("small", "Small")], "small")
        self.sizes.changed.connect(self._size)
        row = QHBoxLayout()
        row.addWidget(self.sizes)
        row.addStretch(1)
        size_lay.addLayout(row)

        self.bg_box, bg_lay = self._section(lay, "Background")
        self.fill = Segmented([("glass", "Frosted glass"), ("solid", "Solid colour")])
        self.fill.changed.connect(lambda k: self._set("fill", k))
        row = QHBoxLayout()
        row.addWidget(self.fill)
        row.addStretch(1)
        bg_lay.addLayout(row)
        bg_lay.addSpacing(10)
        self.bg = SwatchRow(appearance.BG_SWATCHES, "Background colour")
        self.bg.picked.connect(lambda h: self._set("bg", h))
        bg_lay.addWidget(self.bg)

        self.fg_box, fg_lay = self._section(lay, "Text colour")
        self.fg = SwatchRow(appearance.FG_SWATCHES, "Text colour")
        self.fg.picked.connect(lambda h: self._set("fg", h))
        fg_lay.addWidget(self.fg)
        self.fg_note = label("Typed notes stay readable on any background.",
                             9, tone="faint")
        fg_lay.addSpacing(4)
        fg_lay.addWidget(self.fg_note)

        self.photo_box, photo_lay = self._section(lay, "Photo")
        self.photo_name = label("", 9.5, tone="dim")
        photo_lay.addWidget(self.photo_name)
        photo_lay.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.choose = button("Choose photo…", slot=self._choose)
        self.adjust = button("Adjust…", slot=self._adjust,
                             tip="Move and zoom the photo inside the widget")
        self.clear = button("Remove", slot=self._clear)
        for b in (self.choose, self.adjust, self.clear):
            row.addWidget(b)
        row.addStretch(1)
        photo_lay.addLayout(row)

        self.plain = label("Now Playing keeps its own look - it takes its colours "
                           "from the album art.", 9.75, tone="dim", wrap=True)
        lay.addSpacing(14)
        lay.addWidget(self.plain)

        lay.addSpacing(18)
        lay.addWidget(rule())
        lay.addSpacing(14)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.more = button("More settings…", slot=self._more,
                           tip="Everything in the widget's right-click menu")
        self.reset = button("Reset look", slot=self._reset)
        bottom.addWidget(self.more)
        bottom.addWidget(self.reset)
        bottom.addStretch(1)
        bottom.addWidget(button("Remove widget", "danger", self._remove))
        lay.addLayout(bottom)
        lay.addStretch(1)

    def _section(self, lay: QVBoxLayout, title: str):
        box = QWidget()
        inner = QVBoxLayout(box)
        inner.setContentsMargins(0, 16, 0, 0)
        inner.setSpacing(0)
        inner.addWidget(label(title, 9.75, QFont.DemiBold, tone="dim"))
        inner.addSpacing(8)
        lay.addWidget(box)
        return box, inner

    # ------------------------------------------------------------------ #
    def set_card(self, card) -> None:
        self.card = card
        if card is None:
            return
        self.name.setText(name_of(card.kind))
        self.sizes.set_options([(s, s.capitalize()) for s in card.supported_sizes],
                               card.size_class)
        self.size_box.setVisible(len(card.supported_sizes) > 1)
        custom = card.customizable
        for w in (self.bg_box, self.fg_box, self.reset):
            w.setVisible(custom)
        self.fg_note.setVisible(card.kind == "notes")
        self.photo_box.setVisible(bool(getattr(card, "allow_image", False)))
        self.plain.setVisible(not custom)
        self.refresh()

    def refresh(self) -> None:
        c = self.card
        if c is None:
            return
        try:
            self.detail.setText(describe(c))
        except RuntimeError:
            return
        self.sizes.set_current(c.size_class)
        if c.customizable:
            self.fill.set_current("solid" if appearance.is_solid(c.config) else "glass")
            self.bg.set_current(c.config.get("bg"))
            self.fg.set_current(c.config.get("fg"))
            self.reset.setEnabled(any(c.config.get(k)
                                      for k in ("bg", "fg", "fill", "image")))
        img = c.config.get("image")
        self.photo_name.setText(os.path.basename(img) if img else "No photo")
        self.choose.setText("Change photo…" if img else "Choose photo…")
        self.adjust.setEnabled(bool(img))
        self.clear.setEnabled(bool(img))
        self.render_preview()

    def render_preview(self) -> None:
        if self.card is None:
            return
        w, h = self.preview.width(), self.preview.height()
        if w < 40:
            return
        try:
            self.preview.set_image(previews.card_image(self.card, w, h, pad=18))
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
        self.selected = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 24)
        outer.setSpacing(0)
        outer.addWidget(label("On your desktop", 22, QFont.Bold))
        outer.addSpacing(4)
        self.sub = label("", 10.5, tone="dim")
        outer.addWidget(self.sub)
        outer.addSpacing(20)

        self.body = QWidget()
        body = QHBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        self.list = QWidget()
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(4)
        self.list_lay.addStretch(1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        area = theme.scroll_area(self.list)
        area.setFixedWidth(360)
        body.addWidget(area)
        self.inspector = Inspector(win)
        insp = QWidget()
        il = QVBoxLayout(insp)
        il.setContentsMargins(0, 0, 4, 0)
        il.addWidget(self.inspector)
        body.addWidget(theme.scroll_area(insp), 1)
        outer.addWidget(self.body, 1)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        for w in (label("Nothing on your desktop yet", 15, QFont.DemiBold),
                  label("Add widgets from the gallery and they'll show up here.",
                        10.5, tone="dim")):
            w.setAlignment(Qt.AlignCenter)
            el.addWidget(w)
        el.addSpacing(14)
        go = button("Open the gallery", "primary", lambda: win.show_page("gallery"))
        el.addWidget(go, 0, Qt.AlignCenter)
        el.addStretch(2)
        outer.addWidget(self.empty, 1)
        self.rebuild()

    def rebuild(self) -> None:
        cards = list(self.win.board.cards)
        for row in self.rows:
            # Out of the layout now, not whenever the deferred delete runs - a
            # rebuild must never show rows for cards that have gone.
            self.group.removeButton(row)
            self.list_lay.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        for card in cards:
            row = DeskRow(card)
            row.clicked.connect(lambda _c=False, c=card: self.select(c))
            self.group.addButton(row)
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)
            self.rows.append(row)
        n = len(cards)
        self.sub.setText(f"{n} widget{'s' if n != 1 else ''}. Pick one to change "
                         "its size, colours or background photo."
                         if n else "Widgets you add will appear here.")
        self.body.setVisible(bool(n))
        self.empty.setVisible(not n)
        if self.selected not in cards:
            self.selected = cards[0] if cards else None
        self.select(self.selected)
        if self.isVisible():
            self.refresh_thumbs()

    def select(self, card) -> None:
        self.selected = card
        for row in self.rows:
            row.setChecked(row.card is card)
        self.inspector.set_card(card)

    def card_changed(self, card) -> None:
        for row in self.rows:
            if row.card is card:
                row.refresh()
        if card is self.selected:
            self.inspector.refresh()

    def refresh_thumbs(self) -> None:
        for row in self.rows:
            row.refresh()
        self.inspector.render_preview()

    def on_show(self) -> None:
        self.refresh_thumbs()


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #
class SettingsPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        board = win.board
        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(32, 28, 32, 32)
        col.setSpacing(0)
        col.addWidget(label("Settings", 22, QFont.Bold))
        col.addSpacing(4)
        col.addWidget(label("How the widgets look, and where they sit.", 10.5,
                            tone="dim"))

        look = self._group(col, "Look")
        self.theme_seg = Segmented([("dark", "Dark"), ("light", "Light")])
        self.theme_seg.changed.connect(board.set_theme)
        self._row(look, "Theme", "For the widgets and this window.", self.theme_seg)
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
        self._row(look, "Accent", "Highlights, today's date, the second hand.",
                  self.accents)
        self.glass = Switch()
        self.glass.toggled.connect(board.set_glass)
        self._row(look, "Frosted glass",
                  "The blurred wallpaper showing through each widget.", self.glass)
        self.tint = Segmented(GLASS)
        self.tint.changed.connect(lambda v: board.set_glass_tint(int(v)))
        self._row(look, "Glass strength", "How much tint sits over the blur.",
                  self.tint)
        self.opacity = Segmented(OPACITY)
        self.opacity.changed.connect(lambda v: board.set_opacity(int(v) / 100.0))
        self._row(look, "Opacity", "The whole widget, content and all.",
                  self.opacity, last=True)

        desk = self._group(col, "On the desktop")
        self.layer = Segmented(LAYERS)
        self.layer.changed.connect(board.set_layer)
        self.layer_text = self._row(desk, "Where widgets sit", "", self.layer)
        self.snap = Switch()
        self.snap.toggled.connect(board.set_snap)
        self._row(desk, "Snap to grid",
                  "Line widgets up on a grid as you drop them.", self.snap)
        self.lock = Switch()
        self.lock.toggled.connect(board.set_locked)
        self._row(desk, "Lock positions",
                  "Stop widgets moving when you click or drag them.", self.lock)
        self.peek = button("Bring to front", slot=board.toggle_peek)
        self._row(desk, "Bring to front",
                  "Show every widget over your windows until you send them back.",
                  self.peek, last=True)

        app = self._group(col, "App")
        self.boot = Switch()
        self.boot.toggled.connect(self._set_startup)
        self._row(app, "Start with Windows",
                  "Widgets come back when your PC does. This window stays closed.",
                  self.boot)
        self._row(app, "Desktop shortcut", "A PC Widgets icon on your desktop.",
                  button("Create", slot=self._shortcut))
        self._row(app, "Save layout now",
                  "It saves as you go - this is for peace of mind.",
                  button("Save", slot=self._save))
        self._row(app, "Widgets folder", "Where PC Widgets lives on this PC.",
                  button("Open", slot=self._folder))
        self._row(app, "Quit PC Widgets",
                  "Takes the widgets off your desktop until you open the app again.",
                  button("Quit", "danger", self._quit), last=True)
        col.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(theme.scroll_area(content))
        self.sync()

    def _group(self, col: QVBoxLayout, title: str) -> QVBoxLayout:
        col.addSpacing(26)
        col.addWidget(label(title, 12.5, QFont.DemiBold))
        col.addSpacing(10)
        panel = QFrame()
        panel.setProperty("role", "panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(0)
        col.addWidget(panel)
        return lay

    def _row(self, group: QVBoxLayout, title: str, desc: str, control: QWidget,
             last: bool = False) -> QLabel:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(18, 13, 18, 13)
        h.setSpacing(20)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, 10.5, QFont.Medium))
        d = label(desc, 9.25, tone="dim", wrap=True)
        text.addWidget(d)
        h.addLayout(text, 1)
        h.addWidget(control, 0, Qt.AlignVCenter)
        group.addWidget(row)
        if not last:
            line = QWidget()
            ll = QHBoxLayout(line)
            ll.setContentsMargins(18, 0, 0, 0)
            ll.addWidget(rule())
            group.addWidget(line)
        return d

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
        self.layer_text.setText(LAYER_TEXT.get(s.layer, ""))
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
#  The window
# --------------------------------------------------------------------------- #
class MainWindow(QWidget):
    def __init__(self, board: Board, tray=None) -> None:
        super().__init__(None)
        self.board = board
        self.tray = tray
        self.setObjectName("AppRoot")
        # A plain QWidget ignores a stylesheet background unless told to use
        # one, and what shows through instead is Windows' own palette - dark
        # whenever the system is, which left a light-themed window half dark.
        self.setAttribute(Qt.WA_StyledBackground, True)
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
        self.settings = SettingsPage(self)
        self.pages = {"gallery": self.gallery, "desktop": self.desktop,
                      "settings": self.settings}
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.toast_box = Toast(self)

        board.cards_changed.connect(self._cards_changed)
        board.card_changed.connect(self._card_changed)
        board.prefs_changed.connect(self._prefs_changed)

        # Thumbnails of live cards go stale - a clock moves on - so they are
        # redrawn now and then, but only while anyone can see them.
        self._live = QTimer(self)
        self._live.timeout.connect(self._tick)
        self._live.start(4000)
        self.show_page("gallery")

    def _fit_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1040)
        w, h = min(1180, area.width() - 60), min(860, area.height() - 60)
        self.resize(w, h)
        self.setMinimumSize(min(1000, w), min(620, h))
        self.move(area.center() - QPoint(w // 2, h // 2))

    def _apply_look(self) -> None:
        ui = theme.build(self.board.palette)
        theme.set_current(ui)
        self.setPalette(theme.qpalette(ui))
        self.setStyleSheet(theme.stylesheet(ui))
        self.setFont(theme.font(10))
        if self.testAttribute(Qt.WA_WState_Created):
            winapi.style_titlebar(int(self.winId()), ui.dark, ui.window, ui.text)
        s = self.board.state
        self._look = (s.theme, s.accent, s.glass, s.glass_tint, s.opacity)

    # ------------------------------------------------------------------ #
    def show_page(self, key: str) -> None:
        page = self.pages.get(key, self.gallery)
        self.stack.setCurrentWidget(page)
        self.sidebar.set_page(key if key in self.pages else "gallery")
        if self.isVisible():
            page.on_show()

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

    def remove_card(self, card) -> None:
        pl = self.board.remove(card)
        if pl is not None:
            self.toast(f"Removed {name_of(pl.kind)}.", "Undo",
                       lambda: self.board.restore(pl))

    # -- board events ---------------------------------------------------- #
    def _cards_changed(self) -> None:
        self.desktop.rebuild()
        self.gallery.refresh_counts()
        self.sidebar.sync()

    def _card_changed(self, card) -> None:
        self.desktop.card_changed(card)

    def _prefs_changed(self) -> None:
        s = self.board.state
        look = (s.theme, s.accent, s.glass, s.glass_tint, s.opacity)
        if look != self._look:
            themed = look[:2] != self._look[:2]
            self._apply_look()
            if themed:
                for w in self.findChildren(QWidget):
                    w.update()
            self.gallery.refresh_previews()
            self.desktop.refresh_thumbs()
        self.settings.sync()
        self.sidebar.sync()

    def _tick(self) -> None:
        if self.isVisible() and not self.isMinimized() \
                and self.stack.currentWidget() is self.desktop:
            self.desktop.refresh_thumbs()

    # -- window events --------------------------------------------------- #
    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        ui = theme.current()
        winapi.style_titlebar(int(self.winId()), ui.dark, ui.window, ui.text)
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
