"""
setup_steps.py
==============
The first-run setup after Start Setup: where you are, which widgets, the Glass
Dock, then a last screen - all on the main screen's veil (welcome.py).
FIRST-RUN.md has the design and the reasons for it.

The pages are laid out on a 1920 x 1080 design fitted to the screen
(``Frame``), with Skip, Back and Continue kept at the screen's own corners. The
widgets and the dock they show are the real thing, painted offscreen the way
the gallery paints its samples - Weather with the forecast for your town - so
the setup can't promise something the desktop won't look like.

Nothing changes until the end. Done applies every choice. Skip applies the
ones made so far and keeps the starter board's for the rest, so it never
undoes something picked a moment ago; on a second run from Settings the same
button is Cancel and applies nothing. ``apply`` is where choices become the
board: the theme, the widgets down the right-hand edge the way the starter
board lays them out, the town and units on Weather, and the dock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath,
                           QPalette, QPen, QRegion)
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QLineEdit, QWidget

from ..core import store, winapi
from ..core.card import MARGIN, card_size
from ..core.palette import build as build_palette, button_fill
from . import theme
from .townsearch import OFFLINE as OFFLINE_SAY, TownQuery
from .welcome import FILL_MS, _Fade, _mix

W0, H0 = 1920.0, 1080.0         # the design's screen
STARTER = ("clock", "weather", "system", "disk", "notes")      # store.default_board's, in order
LEFT_OUT = ("Coding agents",)   # gallery sections the slideshow leaves to the gallery
PAGE_MS = 400                   # one step giving way to the next
SLIDE_MS = 320                  # the slideshow moving one widget
CHOICE_MS = 200                 # a segmented control's pill sliding
REST_MS = 350                   # a highlighted town resting this long fetches its forecast
INK = QColor("#1d1d1f")
WHITE = QColor("#ffffff")

LOCATION_TEXT = ("Your town sets the weather. PC Widgets doesn't look up where you are; "
                 "only what you type is sent, to Open-Meteo.")
WIDGETS_TEXT = "Scroll sideways to see them all. Pick a size, then add the ones you want."
DOCK_TEXT = ("A floating dock for your apps, music and Windows controls. It takes the "
             "taskbar's place, and the taskbar comes back whenever the dock is off.")
SLIDE_HINT = "← → or the scroll wheel move one widget at a time"

# Where Weather's three sizes sit on the location step, picture boxes with the
# card's shadow gutter included: bottoms aligned, 24 px between the cards.
WEATHER_BOXES = (("small", (486, 834, 204, 204)), ("medium", (678, 834, 384, 204)),
                 ("large", (1050, 654, 384, 384)))

# The slideshow's slots by distance from the middle: x, y, width, height and
# opacity on the design, right of centre (the left side mirrors them). A
# widget between two slots is drawn between them, so it glides.
SLOTS = ((700, 400, 520, 440, 1.0), (1260, 430, 420, 380, 0.6),
         (1740, 470, 380, 320, 0.32), (2180, 500, 340, 290, 0.0))
SLOT_PITCH = 510                # centre to the next slot's centre, for a drag
PICTURE = (68, 34, 384, 204)    # the widget's picture in the middle slot


def _white(a: float) -> QColor:
    c = QColor(255, 255, 255)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _alpha_of(w: Optional[QWidget]) -> float:
    """A control's opacity: its own fade times its page's and the steps'."""
    a = 1.0
    while w is not None:
        v = getattr(w, "alpha", None)
        if isinstance(v, float):
            a *= v
        w = w.parentWidget()
    return a


class Frame:
    """The design's 1920 x 1080 screen fitted inside a real one: ``k`` scales
    it and ``x0``/``y0`` centre it, so a 16:10 or ultrawide screen gets the
    same page with room round it. Controls that belong to the screen's edges
    use ``w``/``h`` with ``s`` margins instead."""

    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = int(w), int(h)
        self.k = min(self.w / W0, self.h / H0)
        self.x0 = (self.w - W0 * self.k) / 2.0
        self.y0 = (self.h - H0 * self.k) / 2.0

    def x(self, v: float) -> float:
        return self.x0 + v * self.k

    def y(self, v: float) -> float:
        return self.y0 + v * self.k

    def s(self, v: float) -> float:
        return v * self.k

    def rect(self, x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(self.x(x), self.y(y), self.s(w), self.s(h))

    def font(self, px: float, weight=QFont.Normal) -> QFont:
        """Type at ``px`` design pixels: the app's Inter, its optical size and
        tracking chosen for the design size, set in pixels so a render and the
        screen agree whatever the DPI."""
        f = theme.font(px * 0.75, weight)
        f.setPixelSize(max(1, round(px * self.k)))
        return f


# --------------------------------------------------------------------------- #
#  What the setup chooses
# --------------------------------------------------------------------------- #
def setup_kinds() -> List[str]:
    """The widgets the slideshow offers, in the gallery's order: all but the
    coding agents' two, which are for the people who'll go looking."""
    from ..core.board import REGISTRY
    from .app import SECTIONS
    out: List[str] = []
    for title, kinds in SECTIONS:
        if title not in LEFT_OUT:
            out += [k for k in kinds if k in REGISTRY and k not in out]
    return out


def default_size(kind: str) -> str:
    from ..core.board import REGISTRY
    sizes = tuple(getattr(REGISTRY.get(kind), "supported_sizes", ("small",)))
    return "small" if "small" in sizes else sizes[0]


def sizes_of(kind: str) -> Tuple[str, ...]:
    from ..core.board import REGISTRY
    return tuple(getattr(REGISTRY.get(kind), "supported_sizes", ("small",)))


def town_key(town) -> Optional[tuple]:
    if town is None:
        return None
    return (round(float(town.lat), 4), round(float(town.lon), 4))


def in_unit(forecast, unit: str):
    """A forecast fetched in Celsius, in ``unit``: one fetch serves both."""
    if unit != "F" or forecast is None:
        return forecast
    from ..tiles.weather import Day

    def f(v):
        return None if v is None else v * 9.0 / 5.0 + 32.0
    return replace(forecast, temp=f(forecast.temp), feels=f(forecast.feels),
                   days=[Day(d.label, d.code, f(d.hi), f(d.lo)) for d in forecast.days])


@dataclass
class Choices:
    """Everything the setup can change, as it stands."""

    town: Optional[object] = None           # weather.Town, or None for no town yet
    unit: str = "C"
    theme: str = "dark"
    picked: List[str] = field(default_factory=list)     # kinds, in the slideshow's order
    sizes: Dict[str, str] = field(default_factory=dict)
    dock: bool = False
    dock_style: str = "glass"
    neon: bool = False

    def copy(self) -> "Choices":
        return replace(self, picked=list(self.picked), sizes=dict(self.sizes))

    @classmethod
    def from_board(cls, board) -> "Choices":
        """What the setup opens with: the starter board on a fresh install,
        whose board is still empty, or the board as it is on a second run."""
        from ..tiles.weather import Town
        st = board.state
        cards = list(board.cards)
        kinds = setup_kinds()
        sizes: Dict[str, str] = {}
        if cards:
            have = {c.kind for c in cards}
            picked = [k for k in kinds if k in have]
            for c in cards:
                sizes.setdefault(c.kind, c.size_class)
        else:
            starter = store.default_board((0, 0, 1920, 1080)).placements
            picked = [k for k in kinds if any(pl.kind == k for pl in starter)]
            for pl in starter:
                sizes.setdefault(pl.kind, pl.size)
        for k in kinds:
            sizes.setdefault(k, default_size(k))
        weather = next((c for c in cards if c.kind == "weather"), None)
        cfg = weather.config if weather is not None else {}
        unit = cfg.get("unit") or winapi.temperature_unit()
        town = None
        if cfg.get("lat") is not None and cfg.get("lon") is not None:
            try:
                town = Town(str(cfg.get("place") or "Your town"), "", "",
                            float(cfg["lat"]), float(cfg["lon"]))
            except (TypeError, ValueError):
                town = None
        return cls(town=town, unit=unit if unit in ("C", "F") else "C",
                   theme=st.theme if st.theme in ("light", "dark") else "dark",
                   picked=picked, sizes=sizes, dock=bool(st.dock_enabled),
                   dock_style=st.dock_style if st.dock_style in ("glass", "solid") else "glass",
                   neon=bool(st.dock_neon))

    def order(self) -> List[str]:
        """The picked widgets in the order they go down the edge: the starter
        board's own first, so a setup left alone lays out exactly like it."""
        return ([k for k in STARTER if k in self.picked]
                + [k for k in self.picked if k not in STARTER])


def pack(items: Sequence[Tuple[str, str]], work, taken: Sequence[QRectF] = ()
         ) -> List[Tuple[str, str, int, int]]:
    """Window positions for new widgets down the right-hand edge, laid out as
    the starter board is (store.default_board): a column a medium card wide,
    small cards in pairs, filled from the top and never back up, so the five
    starter widgets land exactly where they always have. A column that runs
    out of height carries on to its left. ``taken`` holds the cards already on
    the board, as visible rectangles, and nothing is put over them."""
    wl, wt, wr, wb = work
    m = MARGIN
    blocked = [QRectF(r).adjusted(-6, -6, 6, 6) for r in taken]
    used: Dict[Tuple[int, int], set] = {}
    out: List[Tuple[str, str, int, int]] = []
    col = row = 0
    for kind, size in items:
        cw, ch = card_size(size)
        rows = 1 if ch <= 168 else 2
        spot = None
        while spot is None:
            right = wr - 24 - col * 360
            if right - 348 < wl:
                break
            y = wt + 24 + row * 180
            if y + m + ch > wb - 6:
                col, row = col + 1, 0
                continue
            halves = used.setdefault((col, row), set())
            if cw <= 168:
                for half, x in (("right", right - 168), ("left", right - 348)):
                    if half in halves:
                        continue
                    r = QRectF(x + m, y + m, cw, ch)
                    if not any(r.intersects(b) for b in blocked):
                        halves.add(half)
                        spot = (x, y)
                        break
                if spot is None:
                    row += 1
            else:
                free = all(not used.get((col, row + i)) for i in range(rows))
                r = QRectF(right - 348 + m, y + m, cw, ch)
                if free and not any(r.intersects(b) for b in blocked):
                    for i in range(rows):
                        used.setdefault((col, row + i), set()).update(("left", "right"))
                    spot = (right - 348, y)
                    row += rows
                else:
                    row += 1
        if spot is None:
            spot = (wl + 40, wt + 40)       # no room left on the right: overlap, never vanish
        x, y = spot
        blocked.append(QRectF(x + m, y + m, cw, ch).adjusted(-6, -6, 6, 6))
        out.append((kind, size, int(x), int(y)))
    return out


def apply(board, ch: Choices, start: Choices) -> None:
    """Make the board what the setup chose.

    ``start`` is what the setup opened with. On a fresh install that is the
    starter board, and every widget is new. On a second run it is the board
    as it was, and a widget already there is only touched where its choice
    changed: a size picked anew, the town or units changed on the Weather card
    that showed them. Widgets the slideshow doesn't offer are never touched.
    """
    from ..core import displays
    from ..core.board import REGISTRY
    from ..core.store import Placement

    with board.batch():
        if ch.theme != board.state.theme:
            board.set_theme(ch.theme)

        kinds = setup_kinds()
        have: Dict[str, list] = {}
        for c in list(board.cards):
            have.setdefault(c.kind, []).append(c)
        for kind in kinds:
            if kind not in ch.picked:
                for c in have.pop(kind, []):
                    board.remove(c)
        for kind, cards in have.items():
            if kind in kinds and ch.sizes.get(kind) != start.sizes.get(kind):
                cards[0].set_size_class(ch.sizes[kind])

        town_moved = town_key(ch.town) != town_key(start.town)
        for c in have.get("weather", []):
            lat, lon = c.config.get("lat"), c.config.get("lon")
            try:
                theirs = None if lat is None or lon is None else (round(float(lat), 4),
                                                                  round(float(lon), 4))
            except (TypeError, ValueError):
                theirs = None
            if theirs is not None and theirs != town_key(start.town):
                continue        # a card showing another town, set on purpose
            if town_moved and ch.town is not None:
                c.set_town(ch.town)
            if ch.unit != start.unit:
                c.set_unit(ch.unit)

        d = displays.primary(board.screens)
        taken = [c.card_rect().translated(c.x(), c.y()) for c in board.cards]
        fresh = [(k, ch.sizes.get(k) or default_size(k)) for k in ch.order() if k not in have]
        for kind, size, x, y in pack(fresh, d.work, taken):
            cls = REGISTRY.get(kind)
            config = cls.new_config() if cls is not None else {}
            if kind == "weather":
                config["unit"] = ch.unit
                if ch.town is not None:
                    config.update(lat=ch.town.lat, lon=ch.town.lon, place=ch.town.label)
            board.restore(Placement(kind, size, x, y, config, d.key, list(d.work)))

        # The look first and switching it on last, so the dock comes up in
        # the look that was picked rather than changing in front of you.
        if ch.dock_style != board.state.dock_style:
            board.set_dock("style", ch.dock_style)
        if ch.neon != board.state.dock_neon:
            board.set_dock("neon", ch.neon)
        if ch.dock != board.state.dock_enabled:
            board.set_dock("enabled", ch.dock)
        board.state.setup = "done"

    if not board.cards:
        # Choosing no widgets is a choice: write the empty board on purpose,
        # past the guard that stops an empty board erasing a saved one.
        board.state.placements = []
        store.save(board.state, allow_empty=True)


# --------------------------------------------------------------------------- #
#  Fetching
# --------------------------------------------------------------------------- #
class Forecasts(QObject):
    """Forecasts for the towns being looked at, each fetched once, in
    Celsius (``in_unit`` gives Fahrenheit from the same reading)."""

    landed = Signal()
    _got = Signal(object, object)

    def __init__(self, parent=None, threaded: bool = True) -> None:
        super().__init__(parent)
        self.threaded = threaded
        self.cache: Dict[tuple, object] = {}
        self.pending: set = set()
        self._got.connect(self._store)

    def get(self, town):
        """The forecast, the exception that stopped it, or None while it's
        on its way (or not asked for)."""
        return self.cache.get(town_key(town)) if town is not None else None

    def want(self, town) -> None:
        key = town_key(town)
        if key is None or key in self.cache or key in self.pending:
            return
        self.pending.add(key)
        lat, lon, label = float(town.lat), float(town.lon), town.label

        def work() -> None:
            from ..tiles import weather
            try:
                got = weather.fetch(lat, lon, label, "C")
            except Exception as exc:         # offline, a timeout, a bad reply
                got = exc
            try:
                self._got.emit(key, got)
            except RuntimeError:
                pass                         # the setup closed first

        if self.threaded:
            threading.Thread(target=work, daemon=True, name="setup-forecast").start()
        else:
            work()

    def _store(self, key, got) -> None:
        self.pending.discard(key)
        self.cache[key] = got
        self.landed.emit()


def read_desktop(watcher):
    """The taskbar's pins and the open apps, for the dock preview. Native
    calls that can block, so it runs on a worker thread with its own COM."""
    from ..core import dock_shell as shell
    com = shell.com_thread()
    try:
        snap = watcher.read()
    finally:
        if com:
            shell.com_done()
    snap.source = None
    return snap


class _DockBoard(QObject):
    """What the preview dock reads from a board: the real board's glass and
    pins, with the setup's choices in place of its own, so the preview can
    change with every choice while the real dock stays as it is."""

    prefs_changed = Signal()
    glass_changed = Signal()
    displays_changed = Signal()

    def __init__(self, board) -> None:
        super().__init__()
        self.real = board
        self.state = replace(board.state)
        self.palette = board.palette
        self.glass = board.glass

    def set_dock(self, _key, _value) -> None:
        pass                    # a preview changes nothing


# --------------------------------------------------------------------------- #
#  Controls on the veil
# --------------------------------------------------------------------------- #
def _check_path(r: QRectF) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 5 / 24, r.top() + r.height() * 12.5 / 24)
    path.lineTo(r.left() + r.width() * 9.5 / 24, r.top() + r.height() * 17 / 24)
    path.lineTo(r.left() + r.width() * 19 / 24, r.top() + r.height() * 7.5 / 24)
    return path


def _paint_mark(p: QPainter, kind: str, r: QRectF, colour: QColor, width: float) -> None:
    p.save()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(colour, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if kind == "check":
        p.drawPath(_check_path(r))
    else:
        c = r.center()
        h = r.width() * 0.32
        p.drawLine(QPointF(c.x() - h, c.y()), QPointF(c.x() + h, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - h), QPointF(c.x(), c.y() + h))
    p.restore()


def paint_pill(p: QPainter, r: QRectF, frame: Frame, kind: str, text: str, mark: Optional[str],
               lit: float, down: bool, font: QFont, accent: str) -> None:
    """A capsule button, shared by the real buttons and the slideshow's
    pictures of them. ``primary`` and ``on``: white, dark type. ``quiet`` and
    ``off``: a veil of white, white type."""
    rad = r.height() / 2.0
    path = theme.rounded(r, rad)
    if kind in ("primary", "on"):
        if kind == "primary":
            theme.paint_shadow(p, r, rad, blur=max(4, round(frame.s(24))), alpha=58,
                               dy=frame.s(8))
        fill = QColor("#dcdce2") if down else _mix(WHITE, QColor("#e9e9ee"), lit * 0.6)
        ink = INK
    else:
        fill = _white(0.16 + 0.10 * lit + (0.06 if down else 0.0))
        ink = WHITE
    p.fillPath(path, fill)
    fm = QFontMetricsF(font)
    tw = fm.horizontalAdvance(text)
    ms = frame.s(15)
    gap = frame.s(6) if mark else 0.0
    x = r.center().x() - (tw + (ms + gap if mark else 0.0)) / 2.0
    if mark:
        tint = QColor(button_fill(accent)) if kind in ("primary", "on") else WHITE
        _paint_mark(p, mark, QRectF(x, r.center().y() - ms / 2.0, ms, ms), tint,
                    max(1.5, frame.s(3)))
        x += ms + gap
    p.setFont(font)
    p.setPen(ink)
    p.drawText(QRectF(x, r.top(), tw + 2, r.height()), Qt.AlignLeft | Qt.AlignVCenter, text)


def choice_widths(frame: Frame, labels: Sequence[str], px: float, pad: float,
                  seg_w: Optional[float]) -> List[float]:
    if seg_w is not None:
        return [frame.s(seg_w)] * len(labels)
    fm = QFontMetricsF(frame.font(px, QFont.DemiBold))
    return [fm.horizontalAdvance(t) + 2 * frame.s(pad) for t in labels]


def paint_choice(p: QPainter, r: QRectF, frame: Frame, look: str, labels: Sequence[str],
                 widths: Sequence[float], pos: float, index: int, px: float, inset: float,
                 hover: int = -1) -> None:
    """A segmented control, shared by the real ones and the slideshow's
    pictures of them. ``look``: ``veil`` (white on the dimmed screen), or
    ``light`` / ``dark`` (on the dock's card)."""
    rad = r.height() / 2.0
    if look == "veil":
        track, pill, on, off = _white(0.16), WHITE, INK, WHITE
    elif look == "light":
        track, pill, on, off = QColor("#e6e6ea"), WHITE, INK, INK
    else:
        track, pill, on, off = _white(0.10), _white(0.24), QColor("#f5f5f7"), QColor("#f5f5f7")
    p.fillPath(theme.rounded(r, rad), track)
    ins = frame.s(inset)
    xs = [r.left() + ins]
    for w in widths:
        xs.append(xs[-1] + w)
    cells = [QRectF(xs[i], r.top() + ins, widths[i], r.height() - 2 * ins)
             for i in range(len(widths))]
    if not cells:
        return
    if 0 <= hover < len(cells) and hover != index:
        p.fillPath(theme.rounded(cells[hover], cells[hover].height() / 2.0),
                   _white(0.08) if look != "light" else QColor(0, 0, 0, 10))
    lo = max(0, min(len(cells) - 1, int(pos)))
    hi = min(len(cells) - 1, lo + 1)
    t = max(0.0, min(1.0, pos - lo))
    a, b = cells[lo], cells[hi]
    sel = QRectF(a.left() + (b.left() - a.left()) * t, a.top(),
                 a.width() + (b.width() - a.width()) * t, a.height())
    if look == "light":
        theme.paint_shadow(p, sel, sel.height() / 2.0, blur=4, alpha=36, dy=1)
    p.fillPath(theme.rounded(sel, sel.height() / 2.0), pill)
    for i, (cell, label) in enumerate(zip(cells, labels)):
        chosen = i == index
        p.setFont(frame.font(px, QFont.DemiBold if chosen else QFont.Medium))
        p.setPen(on if chosen else off)
        p.drawText(cell, Qt.AlignCenter, label)


class _Control(QAbstractButton):
    """A button drawn on the veil: scaled with the page, easing between rest
    and hover with the welcome buttons' fade, ringed in white while it has the
    keyboard, and as faded as its page while pages change. ``room`` is space
    round the visible control for that ring and any shadow; only the visible
    part answers the pointer."""

    def __init__(self, frame: Frame, motion: bool, parent=None, room: float = 6.0) -> None:
        super().__init__(parent)
        self.frame = frame
        self.room = max(2, round(frame.s(room)))
        self.hover = False
        self.lit = _Fade(self, FILL_MS, motion)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def visual(self) -> QRectF:
        m = self.room
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def visual_size(self) -> Tuple[float, float]:
        return (self.width() - 2 * self.room, self.height() - 2 * self.room)

    def place(self, x: float, y: float) -> None:
        """Put the visible edge, not the margin round it, at x, y."""
        self.move(round(x - self.room), round(y - self.room))

    def hitButton(self, pos) -> bool:
        return self.visual().contains(QPointF(pos))

    def opacity(self) -> float:
        return _alpha_of(self)

    def _settle(self) -> None:
        if getattr(self, "lit", None) is None:
            return              # still being built: Qt can send a change event that early
        on = self.isEnabled() and (self.hover or self.hasFocus() or self.isDown())
        self.lit.to(1.0 if on else 0.0)

    def mouseMoveEvent(self, ev) -> None:
        over = self.hitButton(ev.position().toPoint())
        if over != self.hover:
            self.hover = over
            if over:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
            self._settle()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:
        if self.hover:
            self.hover = False
            self.unsetCursor()
            self._settle()
        super().leaveEvent(ev)

    def focusInEvent(self, ev) -> None:
        super().focusInEvent(ev)
        self._settle()
        self.update()

    def focusOutEvent(self, ev) -> None:
        super().focusOutEvent(ev)
        self._settle()
        self.update()

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        self._settle()

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if not ev.isAutoRepeat():
                self.click()
            return
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            ev.ignore()         # arrows move the slideshow, not the focus
            return
        super().keyPressEvent(ev)

    def paint_ring(self, p: QPainter, r: QRectF, radius: float, colour: Optional[QColor] = None) -> None:
        if not self.hasFocus():
            return
        g = self.frame.s(3.0)
        p.save()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(colour or _white(0.95), max(1.5, self.frame.s(2.0))))
        p.drawRoundedRect(r.adjusted(-g, -g, g, g), radius + g, radius + g)
        p.restore()


class _Pill(_Control):
    """Continue, Done, Back, and the slideshow's Add."""

    def __init__(self, frame: Frame, motion: bool, text: str, kind: str = "primary",
                 px: float = 16.0, height: float = 44.0, pad: float = 26.0,
                 accent: str = "#3573da", parent=None) -> None:
        super().__init__(frame, motion, parent, room=34.0 if kind == "primary" else 6.0)
        self.kind, self.px, self.h, self.pad, self.accent = kind, px, height, pad, accent
        self.mark: Optional[str] = None
        self.set_label(text)

    def font_(self) -> QFont:
        return self.frame.font(self.px, QFont.Medium if self.kind in ("quiet",) else QFont.DemiBold)

    def set_label(self, text: str, mark: Optional[str] = None, kind: Optional[str] = None) -> None:
        if kind is not None:
            self.kind = kind
        self.setText(text)
        self.setAccessibleName(text)
        self.mark = mark
        f = self.frame
        w = QFontMetricsF(self.font_()).horizontalAdvance(text) + 2 * f.s(self.pad)
        if mark:
            w += f.s(21)
        self.setFixedSize(round(w + 2 * self.room), round(f.s(self.h) + 2 * self.room))
        self.update()

    def paintEvent(self, _ev) -> None:
        a = self.opacity()
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(a * (1.0 if self.isEnabled() else 0.4))
        r = self.visual()
        paint_pill(p, r, self.frame, self.kind, self.text(), self.mark, self.lit.t,
                   self.isDown(), self.font_(), self.accent)
        self.paint_ring(p, r, r.height() / 2.0)
        p.end()


class _Link(_Control):
    """Skip, or Cancel on a second run: quiet type that brightens."""

    def __init__(self, frame: Frame, motion: bool, text: str, parent=None) -> None:
        super().__init__(frame, motion, parent)
        self.setText(text)
        self.setAccessibleName(text)
        w = QFontMetricsF(self.font_()).horizontalAdvance(text) + frame.s(32)
        self.setFixedSize(round(w + 2 * self.room), round(frame.s(36) + 2 * self.room))

    def font_(self) -> QFont:
        return self.frame.font(15, QFont.Medium)

    def paintEvent(self, _ev) -> None:
        a = self.opacity()
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(a)
        r = self.visual()
        t = self.lit.t
        if t > 0.001:
            p.fillPath(theme.rounded(r, r.height() / 2.0), _white(0.12 * t))
        p.setFont(self.font_())
        p.setPen(_white(0.80 + 0.20 * t))
        p.drawText(r, Qt.AlignCenter, self.text())
        self.paint_ring(p, r, r.height() / 2.0)
        p.end()


class _Round(_Control):
    """The slideshow's previous and next buttons."""

    def __init__(self, frame: Frame, motion: bool, direction: int, parent=None) -> None:
        super().__init__(frame, motion, parent)
        self.direction = direction
        name = "Next widget" if direction > 0 else "Previous widget"
        self.setText(name)
        self.setAccessibleName(name)
        d = round(frame.s(60))
        self.setFixedSize(d + 2 * self.room, d + 2 * self.room)

    def paintEvent(self, _ev) -> None:
        a = self.opacity()
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(a * (1.0 if self.isEnabled() else 0.35))
        r = self.visual()
        p.setPen(Qt.NoPen)
        p.setBrush(_white(0.16 + 0.10 * self.lit.t))
        p.drawEllipse(r)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_white(0.22), 1.0))
        p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
        c = r.center()
        u = r.width() / 60.0
        p.setPen(QPen(WHITE, max(1.5, 2.4 * u * 22 / 24), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        dx = 3.5 * u * 22 / 24 * self.direction
        h = 7.0 * u * 22 / 24
        path = QPainterPath()
        path.moveTo(c.x() - dx, c.y() - h)
        path.lineTo(c.x() + dx, c.y())
        path.lineTo(c.x() - dx, c.y() + h)
        p.drawPath(path)
        self.paint_ring(p, r, r.height() / 2.0)
        p.end()


class _Switch(_Control):
    """An on/off switch on the dock's card. The off track is a grey that
    holds 3:1 against the card in light and dark, which the app's lighter
    one doesn't."""

    def __init__(self, frame: Frame, motion: bool, checked: bool, name: str, parent=None) -> None:
        super().__init__(frame, motion, parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setText(name)
        self.setAccessibleName(name)
        self.dark = False
        self.accent = "#3573da"
        self.pos_ = 1.0 if checked else 0.0
        self.motion = motion
        self._tween = None
        self.setFixedSize(round(frame.s(50)) + 2 * self.room, round(frame.s(30)) + 2 * self.room)
        self.toggled.connect(self._slide)

    def set_quietly(self, on: bool) -> None:
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self.pos_ = 1.0 if on else 0.0
        self.update()

    def _slide(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        if not self.motion or not self.isVisible():
            self.pos_ = target
            self.update()
            return
        from ..core.frameclock import Tween
        if self._tween is None:
            self._tween = Tween(self)
            self._tween.setEasingCurve(QEasingCurve.OutCubic)
            self._tween.valueChanged.connect(self._step)
        self._tween.stop()
        self._tween.setStartValue(self.pos_)
        self._tween.setEndValue(target)
        self._tween.setDuration(CHOICE_MS)
        self._tween.start()

    def _step(self, v: float) -> None:
        self.pos_ = float(v)
        self.update()

    def paintEvent(self, _ev) -> None:
        a = self.opacity()
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(a * (1.0 if self.isEnabled() else 0.4))
        r = self.visual()
        off = QColor("#75757b" if self.dark else "#7c7c80")
        p.fillPath(theme.rounded(r, r.height() / 2.0), _mix(off, QColor(self.accent), self.pos_))
        d = r.height() - 2 * self.frame.s(2)
        x = r.left() + self.frame.s(2) + self.pos_ * (r.width() - d - 2 * self.frame.s(2))
        knob = QRectF(x, r.top() + self.frame.s(2), d, d)
        theme.paint_shadow(p, knob, d / 2.0, blur=4, alpha=70, dy=1)
        p.setPen(Qt.NoPen)
        p.setBrush(WHITE)
        p.drawEllipse(knob)
        self.paint_ring(p, r, r.height() / 2.0, QColor(self.accent))
        p.end()


class _Choice(QWidget):
    """A segmented control: units, Light/Dark, a widget's size, the dock's
    look. The pill slides to the choice; the arrow keys move it while the
    control has the keyboard."""

    changed = Signal(str)

    def __init__(self, frame: Frame, motion: bool, options, current: str, height: float = 40.0,
                 px: float = 15.0, pad: float = 18.0, inset: float = 3.0,
                 seg_w: Optional[float] = None, look: str = "veil", name: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.frame, self.motion = frame, motion
        self.h, self.px, self.pad, self.inset, self.seg_w, self.look = height, px, pad, inset, seg_w, look
        self.room = max(2, round(frame.s(6)))
        self.accent = "#3573da"
        self.options: List[Tuple[str, str]] = []
        self.index = 0
        self.pos_ = 0.0
        self.hover_i = -1
        self._tween = None
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.set_options(options, current)

    @property
    def current(self) -> str:
        return self.options[self.index][0] if self.options else ""

    def labels(self) -> List[str]:
        return [v for _, v in self.options]

    def widths(self) -> List[float]:
        return choice_widths(self.frame, self.labels(), self.px, self.pad, self.seg_w)

    def visual_size(self) -> Tuple[float, float]:
        return (sum(self.widths()) + 2 * self.frame.s(self.inset), self.frame.s(self.h))

    def set_options(self, options, current: str) -> None:
        self.options = [(str(k), str(v)) for k, v in options]
        keys = [k for k, _ in self.options]
        self.index = keys.index(current) if current in keys else 0
        self.pos_ = float(self.index)
        if self._tween is not None:
            self._tween.stop()
        w, h = self.visual_size()
        self.setFixedSize(round(w) + 2 * self.room, round(h) + 2 * self.room)
        self.update()

    def set_current(self, key: str, emit: bool = False) -> None:
        keys = [k for k, _ in self.options]
        if key not in keys:
            return
        i = keys.index(key)
        if i != self.index:
            self.index = i
            if self.motion and self.isVisible():
                from ..core.frameclock import Tween
                if self._tween is None:
                    self._tween = Tween(self)
                    self._tween.setEasingCurve(QEasingCurve.OutCubic)
                    self._tween.valueChanged.connect(self._step)
                self._tween.stop()
                self._tween.setStartValue(self.pos_)
                self._tween.setEndValue(float(i))
                self._tween.setDuration(CHOICE_MS)
                self._tween.start()
            else:
                self.pos_ = float(i)
            self.update()
        if emit:
            self.changed.emit(key)

    def _step(self, v: float) -> None:
        self.pos_ = float(v)
        self.update()

    def place(self, x: float, y: float) -> None:
        self.move(round(x - self.room), round(y - self.room))

    def visual(self) -> QRectF:
        m = self.room
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hit(self, x: float) -> int:
        r = self.visual()
        left = r.left() + self.frame.s(self.inset)
        for i, w in enumerate(self.widths()):
            if left <= x < left + w:
                return i
            left += w
        return -1

    def mouseMoveEvent(self, ev) -> None:
        i = self._hit(ev.position().x()) if self.visual().contains(ev.position()) else -1
        if i != self.hover_i:
            self.hover_i = i
            self.update()

    def leaveEvent(self, _ev) -> None:
        self.hover_i = -1
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton or not self.visual().contains(ev.position()):
            ev.ignore()
            return
        i = self._hit(ev.position().x())
        if i >= 0:
            self.set_current(self.options[i][0], emit=True)

    def keyPressEvent(self, ev) -> None:
        step = {Qt.Key_Left: -1, Qt.Key_Right: 1}.get(ev.key())
        if step is None:
            ev.ignore()
            return
        i = max(0, min(len(self.options) - 1, self.index + step))
        self.set_current(self.options[i][0], emit=True)

    def focusInEvent(self, ev) -> None:
        super().focusInEvent(ev)
        self.update()

    def focusOutEvent(self, ev) -> None:
        super().focusOutEvent(ev)
        self.update()

    def paintEvent(self, _ev) -> None:
        a = _alpha_of(self)
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(a * (1.0 if self.isEnabled() else 0.4))
        r = self.visual()
        paint_choice(p, r, self.frame, self.look, self.labels(), self.widths(), self.pos_,
                     self.index, self.px, self.inset, self.hover_i)
        if self.hasFocus():
            g = self.frame.s(3.0)
            ring = _white(0.95) if self.look == "veil" else QColor(self.accent)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, max(1.5, self.frame.s(2.0))))
            p.drawRoundedRect(r.adjusted(-g, -g, g, g), r.height() / 2 + g, r.height() / 2 + g)
        p.end()


# --------------------------------------------------------------------------- #
#  Pages
# --------------------------------------------------------------------------- #
class _Page(QWidget):
    """One screen of the setup, the size of the stage. It paints its own words
    at ``alpha``, and its controls read the same ``alpha``, so a page fades
    as one."""

    def __init__(self, steps: "Steps") -> None:
        super().__init__(steps)
        self.steps = steps
        self.frame = steps.frame
        self.motion = steps.motion
        self.alpha = 0.0
        self.setGeometry(steps.rect())
        self.hide()

    # -- the page's life ---------------------------------------------------- #
    def prepare(self) -> None:
        """Just before the page comes in: get its pictures ready."""

    def entered(self) -> None:
        """The page is the current one: give it the keyboard."""
        self.steps.setFocus(Qt.OtherFocusReason)

    def left(self) -> None:
        """The page has gone."""

    def set_alpha(self, a: float) -> None:
        self.alpha = float(a)
        self.update()

    def arrow(self, _key) -> bool:
        return False

    def restyle(self) -> None:
        """The setup's light or dark changed."""
        self.update()

    # -- type ---------------------------------------------------------------- #
    def heading(self, p: QPainter, text: str, top: float, px: float = 52.0) -> None:
        f = self.frame
        font = f.font(px, QFont.Bold)
        font.setLetterSpacing(QFont.PercentageSpacing, 97.5 if px <= 52 else 97.0)
        p.setFont(font)
        p.setPen(WHITE)
        p.drawText(QRectF(0, f.y(top), f.w, f.s(px * 1.2)), Qt.AlignHCenter | Qt.AlignTop, text)

    def paragraph(self, p: QPainter, text: str, left: float, top: float, width: float,
                  px: float = 19.0, line: float = 28.0, colour: Optional[QColor] = None) -> float:
        """Centred lines, wrapped by word at ``width``, ``line`` apart (the
        design's leading, which Qt's own wrapping wouldn't keep). Returns the
        bottom, in design pixels."""
        f = self.frame
        font = f.font(px)
        fm = QFontMetricsF(font)
        lines: List[str] = []
        for word in text.split():
            if lines and fm.horizontalAdvance(lines[-1] + " " + word) <= f.s(width):
                lines[-1] += " " + word
            else:
                lines.append(word)
        p.setFont(font)
        p.setPen(colour or _white(0.80))
        for i, ln in enumerate(lines):
            p.drawText(QRectF(f.x(left), f.y(top + i * line), f.s(width), f.s(line)),
                       Qt.AlignHCenter | Qt.AlignVCenter, ln)
        return top + len(lines) * line

    def caption(self, p: QPainter, text: str, top: float) -> None:
        f = self.frame
        p.setFont(f.font(15, QFont.DemiBold))
        p.setPen(_white(0.80))
        p.drawText(QRectF(0, f.y(top), f.w, f.s(20)), Qt.AlignHCenter | Qt.AlignVCenter, text)

    def paintEvent(self, _ev) -> None:
        a = self.alpha * _alpha_of(self.parentWidget())
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(a)
        self.paint(p)
        p.end()

    def paint(self, p: QPainter) -> None:
        pass


# -- step 1: where you are ---------------------------------------------------- #
class _TownField(QLineEdit):
    """The town box: white in light, graphite in dark, the accent round it
    while it has the keyboard, and the magnifier drawn in."""

    def __init__(self, page: "LocationPage") -> None:
        super().__init__(page)
        self.page = page
        self.dim = "#5d5d63"
        self.setPlaceholderText("Town or city")
        self.setAccessibleName("Your town")
        self.setFont(page.frame.font(18, QFont.Medium))
        self.setClearButtonEnabled(True)
        self.textEdited.connect(page.typed)
        self.fade = QGraphicsOpacityEffect(self)
        self.fade.setEnabled(False)
        self.setGraphicsEffect(self.fade)

    def restyle(self, dark: bool, accent: str) -> None:
        f = self.page.frame
        b = max(1, round(f.s(2)))
        if dark:
            bg, ink, edge = "#2c2c31", "#f5f5f7", theme.css(theme.rgba(255, 255, 255, 0.14))
            self.dim = theme.rgba(235, 235, 245, 0.70)
        else:
            bg, ink, edge = "#ffffff", "#1d1d1f", "transparent"
            self.dim = "#5d5d63"
        self.setStyleSheet(
            f"QLineEdit {{ background: {bg}; color: {ink}; border: {b}px solid {edge};"
            f" border-radius: {round(f.s(28))}px; padding: 0 {round(f.s(18))}px 0"
            f" {round(f.s(50))}px; selection-background-color: {accent};"
            f" selection-color: #ffffff; }}"
            f"QLineEdit:focus {{ border: {b}px solid {accent}; }}")
        pal = self.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(self.dim))
        self.setPalette(pal)

    def set_alpha(self, a: float) -> None:
        self.fade.setEnabled(a < 0.999)
        self.fade.setOpacity(a)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = theme.hints(QPainter(self))
        f = self.page.frame
        s = f.s(18)
        theme.glyph(p, "search", QRectF(f.s(20), (self.height() - s) / 2.0, s, s), self.dim,
                    max(1.4, f.s(2)))
        p.end()

    def keyPressEvent(self, ev) -> None:
        if self.page.field_key(ev):
            return
        super().keyPressEvent(ev)


def _pin_path(r: QRectF) -> QPainterPath:
    """A map pin in a 24-unit box: a teardrop over a dot."""
    u = r.width() / 24.0
    path = QPainterPath()
    path.moveTo(r.left() + 12 * u, r.top() + 21 * u)
    path.cubicTo(r.left() + 12 * u, r.top() + 21 * u, r.left() + 6 * u, r.top() + 15.3 * u,
                 r.left() + 6 * u, r.top() + 10.5 * u)
    path.arcTo(QRectF(r.left() + 6 * u, r.top() + 4.5 * u, 12 * u, 12 * u), 180, -180)
    path.cubicTo(r.left() + 18 * u, r.top() + 15.3 * u, r.left() + 12 * u, r.top() + 21 * u,
                 r.left() + 12 * u, r.top() + 21 * u)
    path.addEllipse(QPointF(r.left() + 12 * u, r.top() + 10.5 * u), 2 * u, 2 * u)
    return path


class _TownList(QWidget):
    """The matches under the town box, each with its region; or what to do
    when there are none."""

    ROW, PAD, M = 52.0, 6.0, 30.0       # design pixels; M is room for the shadow

    def __init__(self, page: "LocationPage") -> None:
        super().__init__(page)
        self.page = page
        self.frame = page.frame
        self.towns: List = []
        self.current = 0
        self.message = ""
        self.setMouseTracking(True)
        self.hide()

    def show_towns(self, towns, current: int) -> None:
        self.towns, self.current, self.message = list(towns), current, ""
        self._relayout()

    def show_message(self, text: str) -> None:
        self.towns, self.message = [], text
        self._relayout()

    def _relayout(self) -> None:
        f = self.frame
        rows = len(self.towns) if self.towns else 1
        h = 2 * self.PAD + rows * self.ROW
        m = f.s(self.M)
        self.setGeometry(round(f.x(660) - m), round(f.y(424) - m), round(f.s(480) + 2 * m),
                         round(f.s(h) + 2 * m))
        self.show()
        self.raise_()
        self.update()

    def panel(self) -> QRectF:
        m = self.frame.s(self.M)
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def row_rect(self, i: int) -> QRectF:
        f = self.frame
        p = self.panel()
        return QRectF(p.left() + f.s(self.PAD), p.top() + f.s(self.PAD + i * self.ROW),
                      p.width() - 2 * f.s(self.PAD), f.s(self.ROW))

    def _row_at(self, pos) -> int:
        for i in range(len(self.towns)):
            if self.row_rect(i).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, ev) -> None:
        i = self._row_at(ev.position())
        self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
        if i >= 0 and i != self.current:
            self.page.highlight(i)

    def mousePressEvent(self, ev) -> None:
        i = self._row_at(ev.position())
        if ev.button() == Qt.LeftButton and i >= 0:
            self.page.pick(i)
        elif not self.panel().contains(ev.position()):
            ev.ignore()

    def paintEvent(self, _ev) -> None:
        a = _alpha_of(self)
        if a <= 0.004:
            return
        f = self.frame
        dark = self.page.steps.choices.theme == "dark"
        accent = QColor(self.page.steps.accent())
        p = theme.hints(QPainter(self))
        p.setOpacity(a)
        panel = self.panel()
        rad = f.s(16)
        theme.paint_shadow(p, panel, rad, blur=max(6, round(f.s(40))), alpha=110 if dark else 90,
                           dy=f.s(18))
        p.fillPath(theme.rounded(panel, rad), QColor("#2c2c31") if dark else WHITE)
        if dark:
            p.setPen(QPen(_white(0.10), 1.0))
            p.drawPath(theme.rounded(panel.adjusted(0.5, 0.5, -0.5, -0.5), rad - 0.5))
        ink = QColor("#f5f5f7") if dark else INK
        dim = QColor(235, 235, 245, round(0.75 * 255)) if dark else QColor("#5d5d63")
        if not self.towns:
            p.setFont(f.font(15))
            p.setPen(dim if self.message != OFFLINE_SAY else ink)
            r = self.row_rect(0).adjusted(f.s(14), 0, -f.s(14), 0)
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, self.message)
            p.end()
            return
        name_f, region_f = f.font(16, QFont.DemiBold), f.font(14)
        for i, town in enumerate(self.towns):
            r = self.row_rect(i)
            chosen = i == self.current
            if chosen:
                soft = QColor(accent)
                soft.setAlphaF(0.30 if dark else 0.12)
                p.fillPath(theme.rounded(r, f.s(11)), soft)
            icon = QRectF(r.left() + f.s(14), r.center().y() - f.s(9), f.s(18), f.s(18))
            tone = (_mix(accent, WHITE, 0.45) if dark else QColor(button_fill(accent.name()))) \
                if chosen else dim
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(tone, max(1.3, f.s(2)), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(_pin_path(icon))
            x = icon.right() + f.s(12)
            p.setFont(name_f)
            p.setPen(ink)
            nw = QFontMetricsF(name_f).horizontalAdvance(town.name)
            p.drawText(QRectF(x, r.top(), nw + 2, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       town.name)
            if town.region:
                p.setFont(region_f)
                p.setPen(dim)
                room = r.right() - f.s(14) - (x + nw + f.s(12))
                text = QFontMetricsF(region_f).elidedText(town.region, Qt.ElideRight, max(0.0, room))
                p.drawText(QRectF(x + nw + f.s(12), r.top(), max(0.0, room), r.height()),
                           Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()


class LocationPage(_Page):
    """Where are you? A town search, the units beside it, and Weather in its
    three sizes for the town highlighted - or picked - below."""

    def __init__(self, steps: "Steps") -> None:
        super().__init__(steps)
        f = self.frame
        ch = steps.choices
        self.query = TownQuery(self, threaded=steps.threaded)
        self.query.changed.connect(self._searched)
        self.field = _TownField(self)
        self.field.setGeometry(f.rect(660, 360, 480, 56).toAlignedRect())
        if ch.town is not None:
            self.field.setText(ch.town.label)
        self.units = _Choice(f, self.motion, (("F", "°F"), ("C", "°C")), ch.unit, height=56,
                             px=16, inset=4, seg_w=50, name="Units", parent=self)
        self.units.place(f.x(1152), f.y(360))
        self.units.changed.connect(self._unit)
        self.list = _TownList(self)
        self._enter_waiting = False
        self._looking = None            # the town whose forecast the cards show
        self._rest = QTimer(self)
        self._rest.setSingleShot(True)
        self._rest.setInterval(REST_MS)
        self._rest.timeout.connect(self._fetch_shown)
        steps.forecasts.landed.connect(self.update)
        self.restyle()

    def restyle(self) -> None:
        self.field.restyle(self.steps.choices.theme == "dark", self.steps.accent())
        self.update()

    def set_alpha(self, a: float) -> None:
        self.field.set_alpha(a * self.steps.alpha)
        super().set_alpha(a)

    def entered(self) -> None:
        self.field.setFocus(Qt.OtherFocusReason)
        self.field.end(False)
        self._fetch_shown()

    # -- searching ---------------------------------------------------------- #
    def typed(self, text: str) -> None:
        self.query.type(text)
        if not text.strip():
            self.steps.choices.town = None
            self.list.hide()
            self._follow()

    def _searched(self) -> None:
        q = self.query
        if q.state == "results":
            self.list.show_towns(q.towns, 0)
            if self._enter_waiting:
                self._enter_waiting = False
                self.pick(0)
                return
            # The first match is the likeliest: its forecast goes now, not
            # after the pause that keeps arrowing through the list cheap.
            self._looking = q.towns[0]
            self.steps.forecasts.want(q.towns[0])
        elif q.state == "searching":
            if not self.list.towns:
                self.list.show_message(q.message())
        elif q.state in ("none", "offline"):
            self._enter_waiting = False
            self.list.show_message(q.message())
        else:
            self.list.hide()
        self._follow()

    def shown_town(self):
        if self.list.isVisible() and self.list.towns:
            return self.list.towns[self.list.current]
        return self.steps.choices.town

    def _follow(self) -> None:
        """The cards follow the highlighted town; its forecast is fetched
        once the highlight has rested, not for every town the arrow passes."""
        town = self.shown_town()
        if town_key(town) != town_key(self._looking):
            self._looking = town
            self._rest.start()
        self.update()

    def _fetch_shown(self) -> None:
        town = self.shown_town()
        if town is not None:
            self.steps.forecasts.want(town)

    def highlight(self, i: int) -> None:
        if 0 <= i < len(self.list.towns):
            self.list.current = i
            self.list.update()
            self._follow()

    def pick(self, i: int) -> None:
        if not 0 <= i < len(self.list.towns):
            return
        town = self.list.towns[i]
        self.steps.choices.town = town
        self.field.setText(f"{town.name}, {town.region}" if town.region else town.name)
        self.list.hide()
        self.list.towns = []
        self._looking = town
        self.steps.forecasts.want(town)
        self.update()

    def _unit(self, unit: str) -> None:
        self.steps.choices.unit = unit
        self.update()

    def field_key(self, ev) -> bool:
        """Keys the town box hands to the list first."""
        key = ev.key()
        listing = self.list.isVisible() and bool(self.list.towns)
        if key in (Qt.Key_Down, Qt.Key_Up) and listing:
            step = 1 if key == Qt.Key_Down else -1
            self.highlight((self.list.current + step) % len(self.list.towns))
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if listing:
                self.pick(self.list.current)
            elif self.query.pending():
                self._enter_waiting = True      # search now, and take the first match
                self.query.search_now()
            else:
                self.steps.forward()
            return True
        if key == Qt.Key_Escape and self.list.isVisible():
            self.list.hide()
            self._follow()
            return True
        return False

    def mousePressEvent(self, ev) -> None:
        if self.list.isVisible():
            self.list.hide()
            self._follow()
        super().mousePressEvent(ev)

    # -- drawing ------------------------------------------------------------- #
    def paint(self, p: QPainter) -> None:
        f = self.frame
        self.heading(p, "Where are you?", 186)
        self.paragraph(p, LOCATION_TEXT, 610, 262, 700)
        town = self.shown_town()
        if town is None:
            return
        got = self.steps.forecasts.get(town)
        if isinstance(got, Exception):
            self.caption(p, f"Couldn't reach Open-Meteo for {town.name}'s forecast. "
                            "Weather will fetch it once you're online.", 618)
            return
        self.caption(p, "Your weather, in its three sizes", 618)
        for size, (x, y, w, h) in WEATHER_BOXES:
            img = self.steps.card_image("weather", size, round(f.s(w)), round(f.s(h)), town)
            p.drawImage(QPointF(round(f.x(x)), round(f.y(y))), img)


# -- step 2: which widgets ------------------------------------------------------ #
def _slot(d: float) -> Tuple[float, float, float, float, float]:
    """A slot's x, y, width, height and opacity on the design, ``d`` widgets
    from the middle; between whole slots it is between their places."""
    a = min(abs(d), len(SLOTS) - 1.0)
    i = min(int(a), len(SLOTS) - 2)
    t = a - i
    lo, hi = SLOTS[i], SLOTS[i + 1]
    x, y, w, h, op = (lo[j] + (hi[j] - lo[j]) * t for j in range(5))
    if d < 0:
        x = W0 - (x + w)
    return x, y, w, h, op


class Carousel(QWidget):
    """The widgets, side by side, the one in the middle in focus. The wheel,
    the arrow keys, a drag or a click on a neighbour move it, one widget at a
    time, gliding on the frame clock; a click on a neighbour brings it to the
    middle."""

    def __init__(self, page: "WidgetsPage") -> None:
        super().__init__(page)
        self.page = page
        self.steps = page.steps
        self.frame = page.frame
        f = self.frame
        self.kinds = page.kinds
        self.pos = 0.0
        self.target = 0
        self._tween = None
        self._wheel = 0
        self._press = None              # (x, pos) at a press
        self._dragging = False
        # From above the tallest slot to below its shadow; the dots and the
        # hint under it are the page's, and show through.
        self.top = f.y(370)
        self.setGeometry(0, round(self.top), f.w, round(f.s(560)))

    def settled(self) -> bool:
        return not self._dragging and abs(self.pos - round(self.pos)) < 1e-4 and (
            self._tween is None or not self._running())

    def _running(self) -> bool:
        from PySide6.QtCore import QAbstractAnimation
        return self._tween is not None and self._tween.state() == QAbstractAnimation.Running

    def go(self, i: int) -> None:
        i = max(0, min(len(self.kinds) - 1, int(i)))
        self.target = i
        if not self.steps.motion:
            self._step(float(i))
            self._landed()
            return
        from ..core.frameclock import Tween
        if self._tween is None:
            self._tween = Tween(self)
            self._tween.setEasingCurve(QEasingCurve.OutCubic)
            self._tween.valueChanged.connect(self._step)
            self._tween.finished.connect(self._landed)
        self._tween.stop()
        span = abs(i - self.pos)
        self._tween.setStartValue(self.pos)
        self._tween.setEndValue(float(i))
        self._tween.setDuration(SLIDE_MS * (1.0 + 0.25 * max(0.0, min(3.0, span) - 1.0)))
        self.page.moving()
        self._tween.start()

    def step(self, n: int) -> None:
        self.go(self.target + n)

    def _step(self, v: float) -> None:
        self.pos = float(v)
        self.update()
        self.page.update_dots()

    def _landed(self) -> None:
        self.pos = float(round(self.pos))
        self.update()
        self.page.landed()

    # -- input ---------------------------------------------------------------- #
    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta()
        v = d.y() if abs(d.y()) >= abs(d.x()) else d.x()
        self._wheel += v
        while self._wheel <= -120:
            self._wheel += 120
            self.step(1)
        while self._wheel >= 120:
            self._wheel -= 120
            self.step(-1)
        ev.accept()

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return super().mousePressEvent(ev)
        self._press = (ev.position().x(), self.pos)
        self._dragging = False

    def mouseMoveEvent(self, ev) -> None:
        if self._press is None:
            return
        dx = ev.position().x() - self._press[0]
        if not self._dragging and abs(dx) > self.frame.s(8):
            self._dragging = True
            if self._tween is not None:
                self._tween.stop()
            self.page.moving()
        if self._dragging:
            pos = self._press[1] - dx / self.frame.s(SLOT_PITCH)
            lo, hi = 0.0, len(self.kinds) - 1.0
            if pos < lo:
                pos = lo - (lo - pos) * 0.3      # a little give past either end
            elif pos > hi:
                pos = hi + (pos - hi) * 0.3
            self._step(pos)

    def mouseReleaseEvent(self, ev) -> None:
        press, self._press = self._press, None
        if press is None:
            return
        if self._dragging:
            self._dragging = False
            dx = ev.position().x() - press[0]
            target = round(self.pos)
            if target == round(press[1]) and abs(dx) > self.frame.s(40):
                target += -1 if dx > 0 else 1     # a flick: on to the next one
            self.go(target)
            return
        hit = self.slot_at(ev.position())
        if hit is not None and hit != round(self.pos):
            self.go(hit)

    def slot_at(self, pos) -> Optional[int]:
        best = None
        for i in range(len(self.kinds)):
            box = self.box(i - self.pos)
            if box.contains(pos):
                if best is None or abs(i - self.pos) < abs(best - self.pos):
                    best = i
        return best

    # -- drawing ---------------------------------------------------------------- #
    def box(self, d: float) -> QRectF:
        f = self.frame
        x, y, w, h, _op = _slot(d)
        return QRectF(f.x(x), f.y(y) - self.top, f.s(w), f.s(h))

    def content(self, d: float) -> Tuple[QRectF, float]:
        """The slot's box, and the scale its contents are drawn at: the
        middle slot's layout, shrunk to the slot's width."""
        box = self.box(d)
        return box, box.width() / self.frame.s(SLOTS[0][2])

    def paintEvent(self, _ev) -> None:
        a = _alpha_of(self)
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        order = sorted(range(len(self.kinds)), key=lambda i: -abs(i - self.pos))
        settled = self.settled()
        for i in order:
            d = i - self.pos
            if abs(d) >= len(SLOTS) - 1:
                continue
            op = _slot(d)[4]
            if op <= 0.004:
                continue
            p.save()
            p.setOpacity(a * op)
            self._paint_slot(p, i, d, settled and i == round(self.pos))
            p.restore()
        p.end()

    def _paint_slot(self, p: QPainter, i: int, d: float, live: bool) -> None:
        f = self.frame
        kind = self.kinds[i]
        ch = self.steps.choices
        box, sc = self.content(d)
        near = max(0.0, 1.0 - abs(d))          # 1 in the middle, 0 a slot away
        rad = f.s(30 + 2 * near)
        if live:
            theme.paint_shadow(p, box, rad, blur=max(6, round(f.s(60))), alpha=64, dy=f.s(24))
        p.fillPath(theme.rounded(box, rad), _white(0.08 + 0.02 * near))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_white(0.14 + 0.04 * near), 1.0))
        p.drawPath(theme.rounded(box.adjusted(0.5, 0.5, -0.5, -0.5), rad - 0.5))

        p.save()
        p.translate(box.center().x(), box.top())
        p.scale(sc, sc)
        half = f.s(SLOTS[0][2]) / 2.0
        px_, py_, pw_, ph_ = PICTURE
        size = ch.sizes.get(kind) or default_size(kind)
        img = self.steps.card_image(kind, size, round(f.s(pw_)), round(f.s(ph_)))
        p.drawImage(QRectF(-half + f.s(px_), f.s(py_), f.s(pw_), f.s(ph_)), img)

        added = kind in ch.picked
        if added:
            b = f.s(30)
            badge = QRectF(half - f.s(18) - b, f.s(18), b, b)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(button_fill(self.steps.accent())))
            p.drawEllipse(badge)
            _paint_mark(p, "check", badge.adjusted(b * 0.23, b * 0.23, -b * 0.23, -b * 0.23),
                        WHITE, max(1.5, f.s(3)))

        from .app import about, name_of
        p.setFont(f.font(24, QFont.Bold))
        p.setPen(WHITE)
        p.drawText(QRectF(-half, f.s(252), 2 * half, f.s(30)), Qt.AlignHCenter | Qt.AlignVCenter,
                   name_of(kind))
        p.setFont(f.font(16))
        p.setPen(_white(0.90))
        p.drawText(QRectF(-half + f.s(24), f.s(290), 2 * half - f.s(48), f.s(44)),
                   Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, about(kind))

        # The middle slot's controls; a neighbour says Added or Add instead.
        # While the slideshow moves, every slot draws its own copy, and the
        # real controls take over once it has settled. The two share a place,
        # so one has gone before the other appears.
        controls = max(0.0, min(1.0, (near - 0.5) * 2.0))
        label = max(0.0, min(1.0, (0.5 - near) * 2.0))
        if not live and controls > 0.01:
            p.save()
            p.setOpacity(p.opacity() * controls)
            self.page.paint_controls(p, kind, f.s(340), half)
            p.restore()
        if label > 0.01:
            p.save()
            p.setOpacity(p.opacity() * label)
            p.setFont(f.font(15, QFont.DemiBold))
            p.setPen(WHITE)
            p.drawText(QRectF(-half, f.s(346), 2 * half, f.s(28)), Qt.AlignHCenter | Qt.AlignVCenter,
                       "Added" if added else "Add")
            p.restore()
        p.restore()


class WidgetsPage(_Page):
    """Choose your widgets: Light or Dark for all of them, the slideshow,
    and in the middle slot the widget's size and Add."""

    def __init__(self, steps: "Steps") -> None:
        super().__init__(steps)
        f = self.frame
        ch = steps.choices
        self.kinds = setup_kinds()
        self.look = _Choice(f, self.motion, (("light", "Light"), ("dark", "Dark")), ch.theme,
                            height=40, px=15, pad=18, inset=3, name="Widget look", parent=self)
        self.look.changed.connect(steps.set_theme)
        self.carousel = Carousel(self)
        self.prev = _Round(f, self.motion, -1, self)
        self.next_ = _Round(f, self.motion, 1, self)
        d = self.prev.visual_size()[0]
        self.prev.place(f.s(40), f.y(590))
        self.next_.place(f.w - f.s(40) - d, f.y(590))
        self.prev.clicked.connect(lambda: self.carousel.step(-1))
        self.next_.clicked.connect(lambda: self.carousel.step(1))
        first = self.kinds[0] if self.kinds else "clock"
        self.size = _Choice(f, self.motion, self._size_options(first),
                            ch.sizes.get(first, default_size(first)), height=40, px=15, pad=16,
                            inset=3, name="Size", parent=self)
        self.size.changed.connect(self._sized)
        self.add = _Pill(f, self.motion, "Add", "off", px=15, height=40, pad=18,
                         accent=steps.accent(), parent=self)
        self.add.clicked.connect(self._toggle)
        # Tab goes as the eye does: the look, the widget's size and Add, then
        # the arrows either side.
        for a, b in ((self.look, self.size), (self.size, self.add), (self.add, self.prev),
                     (self.prev, self.next_)):
            QWidget.setTabOrder(a, b)
        self._count = QRectF()
        self._lay_out_top()
        self.landed()

    def _lay_out_top(self) -> None:
        """Light/Dark and the count beside it, centred together; the count's
        width changes with the number, so this runs again when it does."""
        f = self.frame
        n = len(self.steps.choices.picked)
        cw = (QFontMetricsF(f.font(15, QFont.DemiBold)).horizontalAdvance(f"{n} added")
              + 2 * f.s(16) + f.s(16 + 8))
        lw, _lh = self.look.visual_size()
        x = (f.w - (lw + f.s(14) + cw)) / 2.0
        self.look.place(x, f.y(276))
        self._count = QRectF(x + lw + f.s(14), f.y(276), cw, f.s(40))

    @staticmethod
    def _size_options(kind: str):
        return [(s, s.capitalize()) for s in sizes_of(kind)]

    def kind(self) -> str:
        return self.kinds[max(0, min(len(self.kinds) - 1, round(self.carousel.pos)))]

    def prepare(self) -> None:
        # The neighbours' pictures, so the first frames don't wait on them.
        ch = self.steps.choices
        f = self.frame
        i = round(self.carousel.pos)
        for j in range(max(0, i - 2), min(len(self.kinds), i + 3)):
            k = self.kinds[j]
            self.steps.card_image(k, ch.sizes.get(k) or default_size(k), round(f.s(PICTURE[2])),
                                  round(f.s(PICTURE[3])))

    def entered(self) -> None:
        self.steps.setFocus(Qt.OtherFocusReason)

    def arrow(self, key) -> bool:
        if key == Qt.Key_Left:
            self.carousel.step(-1)
            return True
        if key == Qt.Key_Right:
            self.carousel.step(1)
            return True
        return False

    def restyle(self) -> None:
        self.look.set_current(self.steps.choices.theme)
        self.carousel.update()
        self.update()

    # -- the middle slot's controls ------------------------------------------ #
    def _controls_geometry(self, kind: str) -> Tuple[List[float], float, float, float]:
        """The size control's segment widths, its width, the Add button's
        width, and the gap: all the middle slot's controls, unscaled."""
        f = self.frame
        labels = [s.capitalize() for s in sizes_of(kind)]
        widths = choice_widths(f, labels, 15, 16, None)
        cw = sum(widths) + 2 * f.s(3)
        added = kind in self.steps.choices.picked
        text = "Added" if added else "Add"
        aw = QFontMetricsF(f.font(15, QFont.DemiBold)).horizontalAdvance(text) + 2 * f.s(18) + f.s(21)
        return widths, cw, aw, f.s(12)

    def paint_controls(self, p: QPainter, kind: str, top: float, half: float) -> None:
        """A picture of the middle slot's controls for ``kind``, in slot
        coordinates (x centred on 0): what a slot shows while it moves."""
        f = self.frame
        ch = self.steps.choices
        widths, cw, aw, gap = self._controls_geometry(kind)
        x = -(cw + gap + aw) / 2.0
        labels = [s.capitalize() for s in sizes_of(kind)]
        keys = list(sizes_of(kind))
        size = ch.sizes.get(kind) or default_size(kind)
        idx = keys.index(size) if size in keys else 0
        paint_choice(p, QRectF(x, top, cw, f.s(40)), f, "veil", labels, widths, float(idx), idx,
                     15, 3)
        added = kind in ch.picked
        paint_pill(p, QRectF(x + cw + gap, top, aw, f.s(40)), f, "on" if added else "off",
                   "Added" if added else "Add", "check" if added else "plus", 0.0, False,
                   f.font(15, QFont.DemiBold), self.steps.accent())

    def moving(self) -> None:
        self.size.hide()
        self.add.hide()

    def landed(self) -> None:
        """The slideshow has stopped: the real controls go over the middle
        slot, for the widget now there."""
        f = self.frame
        kind = self.kind()
        ch = self.steps.choices
        self.size.set_options(self._size_options(kind), ch.sizes.get(kind) or default_size(kind))
        self._label_add(kind)
        _widths, cw, aw, gap = self._controls_geometry(kind)
        box = self.carousel.box(0.0)
        x = box.center().x() - (cw + gap + aw) / 2.0
        y = self.carousel.y() + box.top() + f.s(340)
        self.size.place(x, y)
        self.add.place(x + cw + gap, y)
        self.size.show()
        self.add.show()
        self.size.raise_()
        self.add.raise_()
        last = len(self.kinds) - 1
        self.prev.setEnabled(self.carousel.target > 0)
        self.next_.setEnabled(self.carousel.target < last)
        self.update_dots()
        self.carousel.update()

    def _label_add(self, kind: str) -> None:
        from .app import name_of
        added = kind in self.steps.choices.picked
        self.add.set_label("Added" if added else "Add", "check" if added else "plus",
                           "on" if added else "off")
        self.add.setAccessibleName(f"{'Remove' if added else 'Add'} {name_of(kind)}")

    def _sized(self, size: str) -> None:
        kind = self.kind()
        self.steps.choices.sizes[kind] = size
        self.carousel.update()

    def _toggle(self) -> None:
        kind = self.kind()
        picked = self.steps.choices.picked
        if kind in picked:
            picked.remove(kind)
        else:
            picked.append(kind)
            picked.sort(key=self.kinds.index)
        self._lay_out_top()
        self.landed()
        self.update()

    def update_dots(self) -> None:
        self.update(QRectF(0, self.frame.y(864), self.frame.w, self.frame.s(30)).toAlignedRect())

    # -- drawing ---------------------------------------------------------------- #
    def paint(self, p: QPainter) -> None:
        f = self.frame
        self.heading(p, "Choose your widgets", 140)
        self.paragraph(p, WIDGETS_TEXT, 560, 214, 800)
        # How many are added, beside the Light/Dark control (_lay_out_top).
        count = f"{len(self.steps.choices.picked)} added"
        cf = f.font(15, QFont.DemiBold)
        pill = self._count
        cw = pill.width()
        p.fillPath(theme.rounded(pill, pill.height() / 2.0), _white(0.16))
        mark = QRectF(pill.left() + f.s(16), pill.center().y() - f.s(8), f.s(16), f.s(16))
        _paint_mark(p, "check", mark, WHITE, max(1.5, f.s(2.6)))
        p.setFont(cf)
        p.setPen(WHITE)
        p.drawText(QRectF(mark.right() + f.s(8), pill.top(), cw, pill.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, count)
        # Where in the slideshow: a dot a widget, the one in the middle long.
        dots = len(self.kinds)
        pos = self.carousel.pos
        widths = [f.s(8) + f.s(16) * max(0.0, 1.0 - abs(i - pos)) for i in range(dots)]
        total = sum(widths) + f.s(8) * (dots - 1)
        x = (f.w - total) / 2.0
        p.setPen(Qt.NoPen)
        for i, w in enumerate(widths):
            near = max(0.0, 1.0 - abs(i - pos))
            p.setBrush(_white(0.35 + 0.65 * near))
            p.drawRoundedRect(QRectF(x, f.y(874), w, f.s(8)), f.s(4), f.s(4))
            x += w + f.s(8)
        p.setFont(f.font(14))
        p.setPen(_white(0.80))
        p.drawText(QRectF(0, f.y(900), f.w, f.s(20)), Qt.AlignHCenter | Qt.AlignVCenter, SLIDE_HINT)


# -- step 3: the dock ---------------------------------------------------------------- #
class DockPage(_Page):
    """Add the Glass Dock? A card of three choices, and the real dock, as
    those choices make it, where it will sit."""

    CARD = (660, 332, 600)

    def __init__(self, steps: "Steps") -> None:
        super().__init__(steps)
        f = self.frame
        ch = steps.choices
        x, y, w = self.CARD
        self.on = _Switch(f, self.motion, ch.dock, "Glass Dock", self)
        sw, sh = self.on.visual_size()
        self.on.place(f.x(x + w - 22) - sw, f.y(y + 35) - sh / 2)
        self.look = _Choice(f, self.motion, (("glass", "Glass"), ("solid", "Solid colour")),
                            ch.dock_style, height=34, px=14, pad=16, inset=2, look="light",
                            name="Dock look", parent=self)
        lw, lh = self.look.visual_size()
        self.look.place(f.x(x + w - 22) - lw, f.y(y + 71 + 31) - lh / 2)
        self.neon = _Switch(f, self.motion, ch.neon, "Neon outline", self)
        self.neon.place(f.x(x + w - 22) - sw, f.y(y + 71 + 63 + 35) - sh / 2)
        self.on.toggled.connect(self._enabled)
        self.look.changed.connect(self._look)
        self.neon.toggled.connect(self._neon)
        self.dock = None
        self.proxy = None
        self._img: Optional[QImage] = None
        self._ticker = None
        self.restyle()

    def restyle(self) -> None:
        dark = self.steps.choices.theme == "dark"
        accent = self.steps.accent()
        for s in (self.on, self.neon):
            s.dark, s.accent = dark, accent
            s.update()
        self.look.look = "dark" if dark else "light"
        self.look.accent = accent
        self.look.update()
        self._enable_rows()
        if self.dock is not None:
            self._sync_dock()
        self.update()

    def _enable_rows(self) -> None:
        on = self.steps.choices.dock
        self.look.setEnabled(on)
        self.neon.setEnabled(on)

    def prepare(self) -> None:
        if self.dock is None:
            self._make_dock()

    def entered(self) -> None:
        self.steps.setFocus(Qt.OtherFocusReason)
        self._tick_neon()

    def left(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()

    def _make_dock(self) -> None:
        from . import dock as dock_mod
        self.proxy = _DockBoard(self.steps.board)
        self.dock = dock_mod.GlassDock(self.proxy, preview=True)
        self.dock.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.dock._s = self.steps.stage.scale
        if self.steps.snapshot is not None:
            self.dock._snap = self.steps.snapshot
        self._sync_dock()

    def desktop(self, snap) -> None:
        """The taskbar's pins and the open apps have been read."""
        if self.dock is not None and snap is not None:
            self.dock._snap = snap
            self._sync_dock()

    def _sync_dock(self) -> None:
        from . import dock as dock_mod
        ch = self.steps.choices
        st = self.proxy.state
        st.dock_style, st.dock_neon, st.dock_enabled = ch.dock_style, ch.neon, True
        self.proxy.palette = build_palette(ch.theme, st.accent)
        dock = self.dock
        s = dock._s
        f = self.frame
        dock.sync()
        # As wide as the real one would be, short of running under Back and
        # Continue: past that its apps fold into its "more" button, as they do.
        room = f.w - 2 * (self.steps.next_btn.visual_size()[0] + f.s(80))
        width = dock._relayout(max(round(320 * s), min(round(room), f.w - round(48 * s))))
        height = round((dock._top() + dock_mod.WINDOW_H - dock_mod.TOP) * s)
        dock.resize(width, height)
        stage = self.steps.stage
        dock.move(stage.x() + (f.w - width) // 2, stage.y() + f.h - height)
        dock._relayout()
        dock.invalidate()
        self._img = None
        self._tick_neon()
        self.update()

    def _tick_neon(self) -> None:
        """The neon moves in the preview as it will on the dock - only while
        it's on, moving, and this page is up."""
        ch = self.steps.choices
        moving = (ch.dock and ch.neon and self.steps.motion and self.isVisible()
                  and self.proxy is not None and self.proxy.state.dock_neon_motion != "static")
        if moving and self._ticker is None:
            from ..core.frameclock import Ticker
            from .dock import NEON_FPS
            self._ticker = Ticker(self._neon_frame, max_fps=NEON_FPS)
        if self._ticker is not None:
            if moving:
                self._ticker.start()
            else:
                self._ticker.stop()

    def _neon_frame(self) -> None:
        if self.dock is None:
            return
        self.dock._neon_img = None
        self._img = None
        self.update(self._dock_rect().toAlignedRect().adjusted(-2, -2, 2, 2))

    def _dock_rect(self) -> QRectF:
        if self.dock is None:
            return QRectF()
        f = self.frame
        return QRectF((f.w - self.dock.width()) / 2.0, f.h - self.dock.height(),
                      self.dock.width(), self.dock.height())

    def _image(self) -> QImage:
        if self._img is None and self.dock is not None:
            img = QImage(self.dock.size(), QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            self.dock.render(img, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
            self._img = img
        return self._img if self._img is not None else QImage()

    # -- choices --------------------------------------------------------------- #
    def _enabled(self, on: bool) -> None:
        self.steps.choices.dock = bool(on)
        self._enable_rows()
        self._tick_neon()
        self.update()

    def _look(self, style: str) -> None:
        self.steps.choices.dock_style = style
        if self.dock is not None:
            self._sync_dock()

    def _neon(self, on: bool) -> None:
        self.steps.choices.neon = bool(on)
        if self.dock is not None:
            self._sync_dock()

    def closeEvent(self, ev) -> None:
        self.release()
        super().closeEvent(ev)

    def release(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
        if self.dock is not None:
            self.dock.shutdown()
            self.dock.deleteLater()
            self.dock = None

    # -- drawing ---------------------------------------------------------------- #
    def paint(self, p: QPainter) -> None:
        f = self.frame
        ch = self.steps.choices
        dark = ch.theme == "dark"
        self.heading(p, "Add the Glass Dock?", 150)
        self.paragraph(p, DOCK_TEXT, 560, 224, 800)

        x, y, w = self.CARD
        card = f.rect(x, y, w, 204)
        rad = f.s(24)
        theme.paint_shadow(p, card, rad, blur=max(6, round(f.s(60))), alpha=110 if dark else 90,
                           dy=f.s(24))
        fill = QColor(36, 36, 42, round(0.92 * 255)) if dark else QColor(246, 246, 248, round(0.94 * 255))
        p.fillPath(theme.rounded(card, rad), fill)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_white(0.10) if dark else _white(0.6), 1.0))
        p.drawPath(theme.rounded(card.adjusted(0.5, 0.5, -0.5, -0.5), rad - 0.5))
        ink = QColor("#f5f5f7") if dark else INK
        dim = QColor(235, 235, 245, round(0.68 * 255)) if dark else QColor("#5d5d63")
        line = _white(0.08) if dark else QColor(0, 0, 0, 20)
        rows = ((0, 70, "Glass Dock", "Apps, music and Windows controls", True),
                (71, 62, "Look", "", ch.dock),
                (134, 70, "Neon outline", "A glowing ring round the dock", ch.dock))
        for top, h, title, sub, on in rows:
            p.save()
            if not on:
                p.setOpacity(p.opacity() * 0.45)
            ty = y + top + (h - (22 + (20 if sub else 0))) / 2
            p.setFont(f.font(17, QFont.DemiBold))
            p.setPen(ink)
            p.drawText(f.rect(x + 22, ty, 360, 22), Qt.AlignLeft | Qt.AlignVCenter, title)
            if sub:
                p.setFont(f.font(14))
                p.setPen(dim)
                p.drawText(f.rect(x + 22, ty + 24, 380, 18), Qt.AlignLeft | Qt.AlignVCenter, sub)
            p.restore()
        p.setPen(Qt.NoPen)
        p.setBrush(line)
        for sep in (70, 133):
            p.drawRect(f.rect(x + 22, y + sep, w - 22, 1))

        if ch.dock and self.dock is not None:
            r = self._dock_rect()
            self.caption(p, "Your dock", (r.top() - f.y0) / f.k - 36)
            p.drawImage(r.topLeft(), self._image())


# -- the last screen ------------------------------------------------------------------ #
class DonePage(_Page):
    """You're all set: what to do with the widgets now, and Done."""

    KEYS = ("Ctrl", "Alt", "W")

    def __init__(self, steps: "Steps") -> None:
        super().__init__(steps)
        f = self.frame
        self.done = _Pill(f, self.motion, "Done", "primary", px=17, height=52, pad=40,
                          accent=steps.accent(), parent=self)
        self.done.clicked.connect(steps.finish)
        dw, _dh = self.done.visual_size()
        self.done.place((f.w - dw) / 2.0, f.y(608))

    def entered(self) -> None:
        self.done.setFocus(Qt.OtherFocusReason)

    def words(self) -> str:
        if self.steps.choices.picked:
            return ("Your widgets are on the desktop. Drag one to move it, right-click it for "
                    "its options, and press [Ctrl] [Alt] [W] to bring them all in front of "
                    "your windows.")
        return ("Add widgets whenever you like, from the Gallery in the PC Widgets window. "
                "Press [Ctrl] [Alt] [W] to bring them in front of your windows.")

    def paint(self, p: QPainter) -> None:
        self.heading(p, "You're all set", 380, px=64)
        self._keyed_paragraph(p, self.words(), 474, 760)
        self.steps.stage._paint_mark(p)     # whose screen this is, as on the welcome

    def _keyed_paragraph(self, p: QPainter, text: str, top: float, width: float) -> None:
        """A centred paragraph whose [bracketed] words are keys."""
        f = self.frame
        font = f.font(19)
        key_font = f.font(16, QFont.DemiBold)
        fm, km = QFontMetricsF(font), QFontMetricsF(key_font)
        space = fm.horizontalAdvance(" ")

        def wide(tok: str) -> float:
            if tok.startswith("[") and "]" in tok:
                name, rest = tok[1:].split("]", 1)
                return km.horizontalAdvance(name) + f.s(14) + fm.horizontalAdvance(rest)
            return fm.horizontalAdvance(tok)

        lines: List[List[str]] = [[]]
        used = 0.0
        for tok in text.split():
            w = wide(tok)
            if lines[-1] and used + space + w > f.s(width):
                lines.append([])
                used = 0.0
            used += (space if lines[-1] else 0.0) + w
            lines[-1].append(tok)
        lh = f.s(30)
        for n, toks in enumerate(lines):
            total = sum(wide(t) for t in toks) + space * (len(toks) - 1)
            x = (f.w - total) / 2.0
            yt = f.y(top) + n * lh
            for tok in toks:
                if tok.startswith("[") and "]" in tok:
                    name, rest = tok[1:].split("]", 1)
                    kw = km.horizontalAdvance(name) + f.s(14)
                    cap = QRectF(x, yt + (lh - f.s(26)) / 2.0, kw, f.s(26))
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(_white(0.45), 1.0))
                    p.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), f.s(6), f.s(6))
                    p.setFont(key_font)
                    p.setPen(WHITE)
                    p.drawText(cap, Qt.AlignCenter, name)
                    x += kw
                    if rest:
                        p.setFont(font)
                        p.setPen(_white(0.80))
                        rw = fm.horizontalAdvance(rest)
                        p.drawText(QRectF(x, yt, rw + 2, lh), Qt.AlignLeft | Qt.AlignVCenter, rest)
                        x += rw
                else:
                    p.setFont(font)
                    p.setPen(_white(0.80))
                    tw = fm.horizontalAdvance(tok)
                    p.drawText(QRectF(x, yt, tw + 2, lh), Qt.AlignLeft | Qt.AlignVCenter, tok)
                    x += tw
                x += space


# --------------------------------------------------------------------------- #
#  The steps together
# --------------------------------------------------------------------------- #
class Steps(QWidget):
    """The pages, and what they share: the step dots at the top, Skip, Back
    and Continue at the corners. Lives on the main screen's veil."""

    desktop_read = Signal(object)

    def __init__(self, welcome, stage, again: bool = False) -> None:
        super().__init__(stage)
        self.welcome = welcome
        self.stage = stage
        self.board = welcome.board
        self.again = again
        self.motion = welcome.motion
        self.threaded = getattr(welcome, "threaded", True)
        self.setGeometry(stage.rect())
        self.setFocusPolicy(Qt.StrongFocus)
        self.frame = Frame(stage.width(), stage.height())
        self.choices = Choices.from_board(self.board)
        self.start = self.choices.copy()
        self.forecasts = Forecasts(self, threaded=self.threaded)
        self.forecasts.landed.connect(self._forecast)
        self.alpha = 1.0
        self.index = -1
        self.dots = 0.0
        self.snapshot = None
        self._images: Dict[tuple, QImage] = {}
        self._turn = None
        self._finish_turn = None
        self._watcher = None

        f = self.frame
        self.pages: List[_Page] = [LocationPage(self), WidgetsPage(self), DockPage(self),
                                   DonePage(self)]
        self.skip_btn = _Link(f, self.motion, "Cancel" if again else "Skip", parent=self)
        self.back_btn = _Pill(f, self.motion, "Back", "quiet", px=16, height=44, pad=24, parent=self)
        self.next_btn = _Pill(f, self.motion, "Continue", "primary", px=16, height=44, pad=26,
                              parent=self)
        self.skip_btn.clicked.connect(welcome.skip)
        self.back_btn.clicked.connect(self.back)
        self.next_btn.clicked.connect(self.forward)
        sw, _sh = self.skip_btn.visual_size()
        self.skip_btn.place(f.w - f.s(40) - sw, f.s(30))
        bw, bh = self.back_btn.visual_size()
        self.back_btn.place(f.s(40), f.h - f.s(40) - bh)
        nw, nh = self.next_btn.visual_size()
        self.next_btn.place(f.w - f.s(40) - nw, f.h - f.s(40) - nh)
        self.back_btn.alpha = 0.0
        self.back_btn.hide()
        self.desktop_read.connect(self._desktop)
        self._read_desktop()

    # -- shared things ------------------------------------------------------- #
    def accent(self) -> str:
        return self.board.palette.accent

    def set_theme(self, theme_key: str) -> None:
        if theme_key not in ("light", "dark") or theme_key == self.choices.theme:
            return
        self.choices.theme = theme_key
        for page in self.pages:
            page.restyle()

    def card_image(self, kind: str, size: str, w: int, h: int, town=None) -> QImage:
        """A widget as it will look, in the theme chosen, cached. Weather
        shows ``town`` (the town chosen when not given) with its forecast."""
        ch = self.choices
        extra = None
        if kind == "weather":
            town = town if town is not None else ch.town
            got = self.forecasts.get(town)
            state = ("none" if town is None else "error" if isinstance(got, Exception)
                     else "loading" if got is None else id(got))
            extra = (town_key(town), ch.unit, state, getattr(town, "label", ""))
        key = (kind, size, ch.theme, w, h, extra)
        img = self._images.get(key)
        if img is None:
            img = self._render(kind, size, w, h, town)
            if len(self._images) > 120:
                self._images.clear()
            self._images[key] = img
        return img

    def _render(self, kind: str, size: str, w: int, h: int, town) -> QImage:
        from . import previews
        st = self.board.state
        pal = build_palette(self.choices.theme, st.accent)
        glass = self.board.glass if st.glass else None
        card = previews.sample(kind, size, pal, glass)
        if card is None:
            return QImage()
        card.opacity_ = st.opacity
        card.glass_tint = st.glass_tint
        try:
            if kind == "weather":
                self._dress_weather(card, town)
            return previews.compose(card, w, h, pad=MARGIN * self.frame.k, max_scale=self.frame.k)
        finally:
            card.close()
            card.deleteLater()

    def _dress_weather(self, card, town) -> None:
        """The sample Weather card, showing ``town`` - or asking for one, as
        the real card will if no town is chosen."""
        from ..tiles.weather import Forecast
        unit = self.choices.unit
        card.unit = unit
        card.config["unit"] = unit
        if town is None:
            for k in ("lat", "lon", "place"):
                card.config.pop(k, None)
            card.needs_town = True
            card.data = Forecast()
            return
        card.config.update(lat=town.lat, lon=town.lon, place=town.label)
        card.needs_town = False
        got = self.forecasts.get(town)
        if isinstance(got, Exception):
            card.data = Forecast(place=town.label, error="No connection")
        elif got is None:
            card.data = Forecast(place=town.label)          # "loading…"
        else:
            card.data = in_unit(replace(got, place=town.label), unit)

    def _forecast(self) -> None:
        for page in self.pages:
            if page.isVisible():
                page.update()
        self.pages[1].carousel.update()

    def _read_desktop(self) -> None:
        """Read the taskbar's pins and the open apps now, so the dock preview
        is ready by the third step."""
        from . import dock as dock_mod
        from ..core import dock_shell as shell
        try:
            self._watcher = dock_mod.Watcher(shell.own_aumid())
            self._watcher.px = round(dock_mod.ICON * self.stage.scale)
        except Exception as exc:
            store.report(f"setup: the dock preview can't read the desktop ({exc!r})")
            return
        watcher = self._watcher

        def work() -> None:
            try:
                snap = read_desktop(watcher)
            except Exception as exc:
                store.report(f"setup: reading the desktop for the dock preview failed ({exc!r})")
                snap = None
            try:
                self.desktop_read.emit(snap)
            except RuntimeError:
                pass

        if self.threaded:
            threading.Thread(target=work, daemon=True, name="setup-desktop").start()
        else:
            work()

    def _desktop(self, snap) -> None:
        self.snapshot = snap
        self.pages[2].desktop(snap)

    # -- moving between pages ------------------------------------------------- #
    def current(self) -> Optional[_Page]:
        return self.pages[self.index] if 0 <= self.index < len(self.pages) else None

    def begin(self) -> None:
        for b in (self.skip_btn, self.next_btn):
            b.alpha = 0.0           # they come in with the first step
        self.go(0)

    def forward(self) -> None:
        if self.index < 0:
            return
        if self.index < len(self.pages) - 1:
            self.go(self.index + 1)
        else:
            self.finish()

    def back(self) -> None:
        if 0 < self.index < len(self.pages) - 1:
            self.go(self.index - 1)

    def finish(self) -> None:
        self.welcome.finish()

    def go(self, i: int) -> None:
        if i == self.index or not 0 <= i < len(self.pages):
            return
        if self._turn is not None:
            # Continue pressed again mid-turn: finish that turn at once, so
            # no page is left half faded behind the next one.
            self._turn.stop()
            finish, self._finish_turn = self._finish_turn, None
            if finish is not None:
                finish()
        old = self.current()
        new = self.pages[i]
        sign = 1 if i > self.index else -1
        start_dots = self.dots
        self.index = i
        last = i == len(self.pages) - 1
        new.prepare()
        new.set_alpha(0.0)
        new.show()
        new.raise_()
        for b in (self.skip_btn, self.back_btn, self.next_btn):
            b.raise_()
        self.next_btn.setEnabled(True)
        chrome = {self.skip_btn: 0.0 if last else 1.0,
                  self.back_btn: 1.0 if 0 < i < len(self.pages) - 1 else 0.0,
                  self.next_btn: 0.0 if last else 1.0}
        froms = {b: getattr(b, "alpha", 1.0) for b in chrome}
        for b, to in chrome.items():
            if to > 0:
                b.show()
        new.entered()
        if old is not None:
            old.left()
        shift = self.frame.s(28)

        def frame_at(t: float) -> None:
            # One page after the other rather than both at half strength: the
            # old one is gone by half way, the new one starts a little before.
            out, come, e = _ease(t / 0.5), _ease((t - 0.35) / 0.65), _ease(t)
            if old is not None:
                old.set_alpha(1.0 - out)
                old.move(round(-sign * shift * out), 0)
            new.set_alpha(come)
            new.move(round(sign * shift * (1.0 - come)), 0)
            for b, to in chrome.items():
                b.alpha = froms[b] + (to - froms[b]) * e
                b.update()
            self.dots = start_dots + (min(i, 2) - start_dots) * e
            self.update(QRectF(0, 0, self.frame.w, self.frame.s(70)).toAlignedRect())

        def done() -> None:
            frame_at(1.0)
            if old is not None:
                old.hide()
                old.move(0, 0)
            for b, to in chrome.items():
                if to <= 0:
                    b.hide()
            self._turn = None
            self._finish_turn = None

        if not self.motion:
            done()
            return
        from ..core.frameclock import Tween
        self._turn = Tween(self)
        self._turn.setStartValue(0.0)
        self._turn.setEndValue(1.0)
        self._turn.setDuration(PAGE_MS)
        self._turn.valueChanged.connect(frame_at)
        self._turn.finished.connect(done)
        self._finish_turn = done
        frame_at(0.0)
        self._turn.start()

    def set_alpha(self, a: float) -> None:
        """The whole setup's opacity, as it comes in over the welcome."""
        self.alpha = float(a)
        for page in self.pages:
            if page.isVisible():
                page.set_alpha(page.alpha)
        self.update()

    # -- input ------------------------------------------------------------------ #
    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if key == Qt.Key_Escape:
            self.welcome.skip()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.forward()
            return
        page = self.current()
        if page is not None and page.arrow(key):
            return
        super().keyPressEvent(ev)

    def wheelEvent(self, ev) -> None:
        page = self.current()
        if isinstance(page, WidgetsPage):
            page.carousel.wheelEvent(ev)
            return
        super().wheelEvent(ev)

    # -- drawing ---------------------------------------------------------------- #
    def paintEvent(self, _ev) -> None:
        if self.index < 0:
            return
        f = self.frame
        last = self.index == len(self.pages) - 1
        fade = getattr(self.next_btn, "alpha", 1.0) if last or self._turn is not None else 1.0
        a = self.alpha * fade
        if a <= 0.004:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(a)
        widths = [f.s(8) + f.s(16) * max(0.0, 1.0 - abs(i - self.dots)) for i in range(3)]
        x = (f.w - (sum(widths) + 2 * f.s(8))) / 2.0
        p.setPen(Qt.NoPen)
        for i, w in enumerate(widths):
            p.setBrush(_white(0.35 + 0.65 * max(0.0, 1.0 - abs(i - self.dots))))
            p.drawRoundedRect(QRectF(x, f.s(44), w, f.s(8)), f.s(4), f.s(4))
            x += w + f.s(8)
        p.end()

    def release(self) -> None:
        """Let go of the preview dock and anything still running."""
        if self._turn is not None:
            self._turn.stop()
        self.pages[2].release()
