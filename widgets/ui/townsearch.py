"""
townsearch.py
=============
Choosing a town for the Weather widget: a field, and the places that match what
has been typed so far, each with its region.

It replaces a plain input box that took the first match without saying which
one - "Springfield" is thirty places - and that left a card reading "Couldn't
find" when the spelling was a letter off. Here the matches update as you type,
and the two ways it can fail are told apart: no place by that name, and no
answer from the service.

Only the typed text leaves the PC, to Open-Meteo's geocoding service, through
the app's HTTPS-only fetch (core/net.py). Nothing looks up where the PC is.

The search itself - debounced, threaded, stale answers dropped - is
``TownQuery``, shared with the first-run setup's location step; ``TownSearch``
is the popup a card opens round it.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import QLineEdit, QWidget

from ..core.palette import Palette
from . import theme

# How the body reads in each state. Kept together so the wording can be seen
# whole: what to do next, never an error code.
HINT = "Type the name of a town or city."
SEARCHING = "Searching…"
NO_MATCH = "No places match “{q}”. Check the spelling, or try a town nearby."
OFFLINE = "Couldn't reach Open-Meteo. Check your connection, then try again."
PRIVACY = "Only what you type is sent, to Open-Meteo."


class TownQuery(QObject):
    """What has been typed and what matched: the search behind both the
    popup a card opens and the first-run setup's location step.

    A search goes out once typing pauses (DEBOUNCE_MS), never for fewer than
    two characters, and on a worker thread; an answer that comes back after a
    newer search has gone out is dropped, so a slow reply can't overwrite the
    list for what is now in the field. ``changed`` fires whenever ``state`` or
    ``towns`` do.
    """

    changed = Signal()
    found = Signal(int, object)     # (query number, towns or the exception raised)

    DEBOUNCE_MS = 300
    MAX = 6

    def __init__(self, parent=None, finder: Optional[Callable] = None,
                 threaded: bool = True) -> None:
        super().__init__(parent)
        if finder is None:
            from ..tiles.weather import find_towns
            finder = find_towns
        self.finder = finder
        self.threaded = threaded
        self.towns: List = []
        self.state = "idle"             # idle | searching | results | none | offline
        self.query = ""
        self._seq = 0
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self.search_now)
        self.found.connect(self._landed)

    def type(self, text: str) -> None:
        self.query = text
        if len(text.strip()) < 2:
            self._debounce.stop()
            self._seq += 1              # anything still on its way is now stale
            self.state, self.towns = "idle", []
            self.changed.emit()
            return
        self._debounce.start()

    def pending(self) -> bool:
        return self._debounce.isActive()

    def search_now(self) -> None:
        self._debounce.stop()
        q = self.query.strip()
        if len(q) < 2:
            return
        self._seq += 1
        seq = self._seq
        self.state = "searching"
        self.changed.emit()
        if not self.threaded:
            self._landed(seq, self._run(q))
            return

        def work() -> None:
            got = self._run(q)
            try:
                self.found.emit(seq, got)
            except RuntimeError:
                pass                    # closed before the answer came back

        threading.Thread(target=work, daemon=True).start()

    def _run(self, q: str):
        try:
            return self.finder(q)
        except Exception as exc:        # the network, a timeout, a bad reply: all "couldn't reach"
            return exc

    def _landed(self, seq: int, got) -> None:
        if seq != self._seq:
            return                      # an older query, overtaken by typing
        if isinstance(got, Exception):
            self.state, self.towns = "offline", []
        elif not got:
            self.state, self.towns = "none", []
        else:
            self.state, self.towns = "results", list(got)[:self.MAX]
        self.changed.emit()

    def message(self) -> str:
        """What to say in place of a list, for every state but results."""
        return {"idle": HINT, "searching": SEARCHING,
                "none": NO_MATCH.format(q=self.query.strip()), "offline": OFFLINE,
                "results": ""}[self.state]


class _Field(QLineEdit):
    """The search box, with its magnifier drawn in like the app's own."""

    def __init__(self, ui, dim: str, parent=None) -> None:
        super().__init__(parent)
        self._ui, self._dim = ui, dim

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = theme.hints(QPainter(self))
        theme.glyph(p, "search", QRectF(14, (self.height() - 14) / 2.0, 14, 14),
                    self._dim, 1.6)
        p.end()


class TownSearch(QWidget):
    """A popup: type, see matches, pick one with the mouse, the arrow keys and
    Enter, or close it with Esc or a click elsewhere."""

    M = 18              # room round the panel for its shadow
    W = 380
    PAD = 14
    FIELD = 38
    ROW = 46
    MAX_ROWS = TownQuery.MAX

    def __init__(self, palette: Palette, on_pick: Callable, finder: Optional[Callable] = None,
                 threaded: bool = True) -> None:
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Set your town")
        self.ui = theme.build(palette)
        # Secondary text a step darker than the app's light grey, which is 3.8:1
        # on white; this is 5.9:1 on the panel. The dark grey already passes.
        self.dim = theme.rgba(255, 255, 255, 0.62) if self.ui.dark else "#5d5d63"
        self.on_pick = on_pick
        self.q = TownQuery(self, finder, threaded)
        self.q.changed.connect(self._changed)
        self.current = 0
        self._anchor: Optional[QRect] = None
        self._above = False

        self.field = _Field(self.ui, self.dim, self)
        self.field.setPlaceholderText("Town or city")
        self.field.setFont(theme.font(10.5, QFont.Medium))
        self.field.setFixedHeight(self.FIELD)
        self.field.setClearButtonEnabled(True)
        self._style_field()
        self.field.textEdited.connect(self.q.type)
        self.field.installEventFilter(self)
        self.setMouseTracking(True)
        self._relayout()

    @property
    def state(self) -> str:
        return self.q.state

    @property
    def towns(self) -> List:
        return self.q.towns

    # ------------------------------------------------------------------ #
    def _style_field(self) -> None:
        ui = self.ui
        field = ui.control if ui.dark else "#ffffff"
        self.field.setStyleSheet(
            f"QLineEdit {{ background: {field}; color: {ui.text};"
            f" border: 1px solid {theme.css(ui.edge)}; border-radius: {self.FIELD // 2}px;"
            f" padding: 6px 14px 6px 36px; selection-background-color: {ui.accent};"
            f" selection-color: #ffffff; }}"
            f"QLineEdit:focus {{ border: 2px solid {ui.accent}; padding: 5px 13px 5px 35px; }}")
        pal = self.field.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(self.dim))
        self.field.setPalette(pal)

    def panel(self) -> QRectF:
        return QRectF(self.rect()).adjusted(self.M, self.M, -self.M, -self.M)

    def _body_height(self) -> int:
        if self.state == "results":
            return self.ROW * min(len(self.towns), self.MAX_ROWS)
        return 44 if self.state in ("none", "offline") else 30

    def _relayout(self) -> None:
        h = self.PAD + self.FIELD + 8 + self._body_height() + 10 + 18 + self.PAD
        self.resize(self.W + 2 * self.M, h + 2 * self.M)
        self.field.setGeometry(self.M + self.PAD, self.M + self.PAD,
                               self.W - 2 * self.PAD, self.FIELD)
        if self._anchor is not None:
            self._place()
        self.update()

    def _rows_top(self) -> float:
        return self.M + self.PAD + self.FIELD + 8

    def row_rect(self, i: int) -> QRectF:
        return QRectF(self.M + 8, self._rows_top() + i * self.ROW, self.W - 16, self.ROW)

    # -- placing it ------------------------------------------------------ #
    def open_near(self, anchor: QRect) -> None:
        """Show it under ``anchor`` (a card, in screen pixels), or above it when
        there's no room below, kept on that card's screen."""
        self._anchor = QRect(anchor)
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()
        self.field.setFocus(Qt.PopupFocusReason)

    def _place(self) -> None:
        a = self._anchor
        screen = QGuiApplication.screenAt(a.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1040)
        pw, ph = self.width() - 2 * self.M, self.height() - 2 * self.M
        below = a.bottom() + 10
        above = a.top() - 10 - ph
        if not self._above and below + ph > area.bottom() and above >= area.top():
            self._above = True      # decided once, so a growing list doesn't jump sides
        y = above if self._above else below
        x = max(area.left() + 8, min(a.left(), area.right() - 8 - pw))
        y = max(area.top() + 8, min(y, area.bottom() - 8 - ph))
        self.move(QPoint(int(x - self.M), int(y - self.M)))

    # -- searching ------------------------------------------------------- #
    def _changed(self) -> None:
        if self.q.state == "results":
            self.current = 0
        self._relayout()

    # -- choosing -------------------------------------------------------- #
    def pick(self, i: int) -> None:
        if self.state != "results" or not 0 <= i < len(self.towns):
            return
        town = self.towns[i]
        self.close()
        self.on_pick(town)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.field and ev.type() == QEvent.KeyPress:
            key = ev.key()
            if key == Qt.Key_Escape:
                self.close()
                return True
            if key in (Qt.Key_Down, Qt.Key_Up) and self.state == "results":
                step = 1 if key == Qt.Key_Down else -1
                self.current = (self.current + step) % len(self.towns)
                self.update()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if self.q.pending():
                    self.q.search_now()     # Enter before the pause: search, then pick
                if self.state == "results":
                    self.pick(self.current)
                return True
        return super().eventFilter(obj, ev)

    def _row_at(self, pos) -> int:
        if self.state != "results":
            return -1
        for i in range(len(self.towns)):
            if self.row_rect(i).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, ev) -> None:
        i = self._row_at(ev.position())
        if i >= 0 and i != self.current:
            self.current = i
            self.update()
        self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)

    def mousePressEvent(self, ev) -> None:
        if not self.panel().contains(ev.position()):
            self.close()            # the shadow margin counts as "elsewhere"
            return
        i = self._row_at(ev.position())
        if ev.button() == Qt.LeftButton and i >= 0:
            self.pick(i)

    # -- drawing --------------------------------------------------------- #
    def paintEvent(self, _ev) -> None:
        ui = self.ui
        p = theme.hints(QPainter(self))
        panel = self.panel()
        theme.paint_shadow(p, panel, 16, blur=22, alpha=80 if ui.dark else 60, dy=8)
        path = theme.rounded(panel, 16)
        p.fillPath(path, QColor(ui.surface))
        p.setPen(QColor(ui.edge))
        p.drawPath(theme.rounded(panel.adjusted(0.5, 0.5, -0.5, -0.5), 15.5))

        top = self._rows_top()
        if self.state == "results":
            name_f = theme.font(10.5, QFont.DemiBold)
            region_f = theme.font(9.5)
            for i, town in enumerate(self.towns):
                r = self.row_rect(i)
                if i == self.current:
                    p.fillPath(theme.rounded(r.adjusted(0, 2, 0, -2), 10),
                               QColor(ui.accent_soft))
                x = r.left() + 14
                p.setFont(name_f)
                p.setPen(QColor(ui.text))
                nw = QFontMetricsF(name_f).horizontalAdvance(town.name)
                p.drawText(QRectF(x, r.top(), nw + 2, r.height()),
                           Qt.AlignLeft | Qt.AlignVCenter, town.name)
                if town.region:
                    p.setFont(region_f)
                    p.setPen(QColor(self.dim))
                    room = r.right() - 14 - (x + nw + 10)
                    text = QFontMetricsF(region_f).elidedText(town.region, Qt.ElideRight, room)
                    p.drawText(QRectF(x + nw + 10, r.top(), room, r.height()),
                               Qt.AlignLeft | Qt.AlignVCenter, text)
        else:
            words = self.q.message()
            p.setFont(theme.font(10))
            p.setPen(QColor(self.dim if self.state != "offline" else ui.text))
            p.drawText(QRectF(self.M + self.PAD + 4, top, self.W - 2 * self.PAD - 8,
                              self._body_height()),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, words)

        p.setFont(theme.font(9))
        p.setPen(QColor(self.dim))
        p.drawText(QRectF(self.M + self.PAD + 4, panel.bottom() - self.PAD - 18,
                          self.W - 2 * self.PAD - 8, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, PRIVACY)
        p.end()
