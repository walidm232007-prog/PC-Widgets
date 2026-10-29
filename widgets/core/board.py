"""
board.py
========
Owns every widget on screen: creating them from saved placements, keeping them
on the right layer and the right display, persisting moves, saving and applying
presets, and summoning the whole set forward on a hotkey.

The layering is the interesting part. Phone and Mac widgets sit *on* the
desktop, below your work - which is right until you actually want to look at
one. So the board has two states: parked on the desktop, and peeked to the
front. The hotkey toggles between them.

The displays are the other part. Every card belongs to a monitor rather than to
a spot in Windows' shared coordinate space (see displays.py), so the board
listens for monitors coming, going, moving and changing resolution, and puts
each card back where it belongs on the screens there are now.
"""

from __future__ import annotations

import time
from dataclasses import fields
from typing import Dict, List, Optional, Sequence, Set, Tuple, Type

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from . import card, displays, winapi
from .card import MARGIN, WidgetCard, card_size
from .displays import Anchor, Display
from .glass import GlassBackdrop
from .palette import Palette, build as build_palette
from .store import BoardState, Placement, Preset, save   # noqa: F401 (BoardState: fields())

# --------------------------------------------------------------------------- #
#  Registry
# --------------------------------------------------------------------------- #
REGISTRY: Dict[str, Type[WidgetCard]] = {}


def register(cls: Type[WidgetCard]) -> Type[WidgetCard]:
    REGISTRY[cls.kind] = cls
    return cls


def load_builtin_tiles() -> None:
    """Import the shipped tiles so they self-register."""
    from ..tiles.calendar import CalendarWidget
    from ..tiles.clock import ClockWidget
    from ..tiles.comms import CommsWidget
    from ..tiles.files import FilesWidget
    from ..tiles.media import MediaWidget
    from ..tiles.notes import NotesWidget
    from ..tiles.outlook import OutlookWidget
    from ..tiles.stocks import StocksWidget
    from ..tiles.system import DiskWidget, SystemWidget
    from ..tiles.weather import WeatherWidget
    from ..tiles.workspace import WorkspaceWidget

    for cls in (ClockWidget, WeatherWidget, StocksWidget, SystemWidget,
                DiskWidget, CalendarWidget, NotesWidget, FilesWidget,
                WorkspaceWidget, MediaWidget, OutlookWidget, CommsWidget):
        register(cls)


def available() -> List[Type[WidgetCard]]:
    return sorted(REGISTRY.values(), key=lambda c: c.title)


def _config_key(config: dict) -> tuple:
    return tuple(sorted((k, repr(v)) for k, v in config.items()))


def placement_size(pl: Placement) -> Tuple[int, int]:
    """The visible size of the card a placement makes."""
    w, h = pl.config.get("w"), pl.config.get("h")
    if isinstance(w, int) and isinstance(h, int):       # Files' free size: the window
        m = int(MARGIN) * 2
        return max(1, w - m), max(1, h - m)
    return card_size(pl.size)


# --------------------------------------------------------------------------- #
#  Board
# --------------------------------------------------------------------------- #
class Board(QObject):
    # The app window, the tray menu and the cards' own menus can all change the
    # board, so each listens here rather than assuming it made the change.
    cards_changed = Signal()            # a card was added or removed
    card_changed = Signal(object)       # one card's size, config or display changed
    prefs_changed = Signal()            # theme, accent, layer, glass, peek...
    presets_changed = Signal()          # a preset was saved, renamed, removed or applied
    displays_changed = Signal()         # a monitor came, went, moved or changed size
    glass_changed = Signal()            # the blurred wallpaper was rebuilt

    def __init__(self, state: BoardState) -> None:
        super().__init__()
        self.state = state
        self.palette: Palette = build_palette(state.theme, state.accent)
        self.cards: List[WidgetCard] = []
        self.peeked = False
        self._saved_sig: Optional[tuple] = None
        self._spotlit: Set[WidgetCard] = set()
        self._holding = 0               # mid-way through a change of many cards
        self._closed = False

        # The monitors, as of the last look; every card is placed against
        # these. Plugging one in or out arrives as a burst of changes - a screen
        # added, another resized, the main one moved - so the board lets it
        # settle before putting anything back.
        self.screens: List[Display] = displays.current()
        self._display_sig = displays.signature()
        self._redisplaying = False
        self._redisplay = QTimer(self)
        self._redisplay.setSingleShot(True)
        self._redisplay.timeout.connect(self._displays_settled)
        self._watch_screens()

        self.glass = GlassBackdrop()
        self.glass.refresh()
        # The wallpaper can change under us - a slideshow, a theme switch, a
        # monitor being plugged in. Cheap to check, only rebuilt when it moved.
        self._glass_timer = QTimer(self)
        self._glass_timer.timeout.connect(self._refresh_glass)
        self._glass_timer.start(20_000)

        # In desktop mode another window can end up drawn over a widget when
        # focus changes; a slow re-assert keeps them parked without polling
        # hard enough to matter.
        self._park = QTimer(self)
        self._park.timeout.connect(self._reassert_layer)

        # Layout safety net. Dropping a card already persists it, and quitting
        # persists again - but both are single points of failure, and when one
        # silently stopped working a whole afternoon's arrangement was still
        # sitting only in the live windows. This compares the board against
        # what's on disk and writes only when they differ, so it costs a tuple
        # comparison per tick and nothing else. It also means a board killed
        # outright - Task Manager, a crash, a reboot - loses at most a few
        # seconds of nudging instead of everything since the last drag.
        self._autosave = QTimer(self)
        self._autosave.timeout.connect(self.persist)
        self._autosave.start(4000)

        # Stop painting behind a game. The foreground window is asked about
        # once here for the whole board - the answer is about the machine, not
        # about any one card, so asking nine times would be nine times the work
        # for one result - and then each card is told whether it is under it.
        self._quiet = False
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._check_fullscreen)
        self._watch.start(1000)

    # ------------------------------------------------------------------ #
    def build(self) -> None:
        wanted = len(self.state.placements)
        made = [c for c in (self._make(pl) for pl in list(self.state.placements))
                if c is not None]
        # Placed all together, before any is shown: a card waiting for its own
        # display needs to know where every other card is to find room.
        self.cards.extend(made)
        self._place_all()
        for c in made:
            self._show(c, place=False)
        self._remember_displays()
        self.apply_layer()
        if self.state.layer == "desktop":
            self._park.start(1500)

        # Write the config so a first run leaves an editable board.json behind -
        # but only if the board actually came up. Persisting a board that failed
        # to build is how a saved layout gets erased.
        if self.cards or not wanted:
            self.persist()

        # An empty board is invisible and looks exactly like the app not
        # starting, so say so where it can be seen.
        if wanted and not self.cards:
            print(f"warning: {wanted} saved widget(s) could not be created; "
                  f"layout left untouched")

    def _make(self, pl: Placement) -> Optional[WidgetCard]:
        """A card for a placement, set up but not yet placed or shown."""
        cls = REGISTRY.get(pl.kind)
        if cls is None:
            return None
        c = cls(self.palette, pl.size, pl.config)
        c.opacity_ = self.state.opacity
        c.locked = self.state.locked
        c.snap_to_grid = self.state.snap
        c.glass = self.glass if self.state.glass else None
        c.glass_tint = self.state.glass_tint
        c.anchor = self._anchor_for(pl, c)
        return c

    def _show(self, c: WidgetCard, place: bool = True) -> None:
        """Put a made card on the board and on screen."""
        if c not in self.cards:
            self.cards.append(c)
        if place:
            self._rekey()
            x, y = self._spot(c, self.cards)
            c._placed_at = (x, y)
            c.move(x, y)
        # Saved coordinates can stop being valid: a monitor gets unplugged, the
        # resolution changes, or the card geometry itself changes between
        # versions. Without this a widget can restore off-screen and be
        # unreachable, with no way to drag it back.
        c.settle()
        c._placed_at = (c.x(), c.y())
        c.start()
        winapi.apply_backdrop(int(c.winId()), self.state.backdrop,
                              self.palette.dark)
        c.moved.connect(lambda c=c: self._card_moved(c))
        c.changed.connect(self.persist)
        c.changed.connect(lambda c=c: self.card_changed.emit(c))
        c.removed.connect(self.remove)

    # ------------------------------------------------------------------ #
    #  Displays
    # ------------------------------------------------------------------ #
    def _anchor_for(self, pl: Placement, c: WidgetCard) -> Anchor:
        """Where a placement belongs. One saved before displays were tracked
        has only a position, so it belongs to whichever display that is on."""
        area = tuple(pl.area or ())
        if pl.display and len(area) != 4:
            area = tuple(self.state.displays.get(pl.display, {}).get("work") or ())
        if pl.display and len(area) == 4:
            return Anchor(pl.display, int(pl.x), int(pl.y), tuple(int(v) for v in area))
        r = c.card_rect()
        d = displays.at(pl.x + r.center().x(), pl.y + r.center().y(), self.screens)
        return Anchor(d.key, int(pl.x), int(pl.y), d.work)

    def _anchor_here(self, c: WidgetCard) -> None:
        """Make a card belong to the display it is on now, at the spot it is at."""
        r = c.card_rect()
        d = displays.at(c.x() + r.center().x(), c.y() + r.center().y(),
                        displays.current())
        c.anchor = Anchor(d.key, c.x(), c.y(), d.work)
        c.displaced = False
        c._placed_at = (c.x(), c.y())

    def _sync_anchor(self, c: WidgetCard) -> None:
        """Follow a card that has moved on its own display without being
        dragged - clamped after growing, snapped to the grid, resized from its
        grip.

        Only on its *own* display, and only while that display is here. A card
        Windows has pushed onto another monitor, because its own went away
        before the board caught up, must keep its anchor - or the layout saved a
        moment later would forget where it belongs.
        """
        a = c.anchor
        if a is None:
            self._anchor_here(c)
            return
        if c.displaced or c._drag_from is not None:
            return
        r = c.card_rect()
        d = displays.at(c.x() + r.center().x(), c.y() + r.center().y(), self.screens)
        if d.key == a.display and (a.x, a.y, a.area) != (c.x(), c.y(), d.work):
            c.anchor = Anchor(d.key, c.x(), c.y(), d.work)
            c._placed_at = (c.x(), c.y())

    def _rekey(self) -> None:
        """Point each anchor at the name its monitor goes by now.

        A monitor moved to another port, or given a new device path by a driver
        update, is still the monitor its cards belong to (displays.resolve).
        """
        here = {d.key for d in self.screens}
        missing = {c.anchor.display for c in self.cards
                   if c.anchor is not None and c.anchor.display not in here}
        if not missing:
            return
        table = self.state.displays
        found = displays.resolve({k: table.get(k, {}) for k in missing},
                                 self.screens, known=table)
        for c in self.cards:
            d = found.get(c.anchor.display) if c.anchor is not None else None
            if d is not None:
                a = c.anchor
                c.anchor = Anchor(d.key, a.x, a.y, a.area)

    def _spot(self, c: WidgetCard, among: Sequence[WidgetCard]) -> Tuple[int, int]:
        """Where a card goes on the displays connected now: on its own display
        if that is here, otherwise on the main one, clear of the cards in
        ``among``."""
        a = c.anchor
        r = c.card_rect()
        cw, ch, m = int(r.width()), int(r.height()), int(MARGIN)
        d = displays.find(a.display, self.screens)
        c.displaced = d is None
        if d is not None:
            x, y = displays.carry(a.x + m, a.y + m, cw, ch, a.area, d.work)
            return x - m, y - m
        main = displays.primary(self.screens)
        x, y = displays.carry(a.x + m, a.y + m, cw, ch, a.area, main.work)
        return self._free_spot(cw, ch, main.work, prefer=(x, y), among=among, skip=c)

    def _place_all(self) -> None:
        """Put every card where its anchor says, on the displays connected now.

        Cards whose own display is here go first, so one that has to wait on
        the main display finds room around them instead of landing on one.
        """
        for c in self.cards:
            if c.anchor is None:
                self._anchor_here(c)
        self._rekey()
        here = {d.key for d in self.screens}
        home = [c for c in self.cards if c.anchor.display in here]
        away = [c for c in self.cards if c.anchor.display not in here]
        placed: List[WidgetCard] = list(home)
        for c in home + away:
            if c._drag_from is not None:
                continue                    # in the user's hand right now
            x, y = self._spot(c, placed)
            if c in away:
                placed.append(c)
            c._placed_at = (x, y)
            if (c.x(), c.y()) != (x, y):
                c.move(x, y)
                c.update()                  # the frosted slice goes by position

    def _remember_displays(self) -> None:
        """Keep a note of every display a card belongs to, connected or not, so
        one that isn't here can still be named - and told from a new monitor."""
        table = dict(self.state.displays)
        for d in self.screens:
            table[d.key] = d.info()
        wanted = ({d.key for d in self.screens}
                  | {c.anchor.display for c in self.cards if c.anchor is not None})
        self.state.displays = {k: v for k, v in table.items() if k in wanted}

    def _watch_screens(self) -> None:
        """Hear about monitor changes as Windows announces them - Qt turns
        WM_DISPLAYCHANGE and the work-area notices into these. persist() checks
        as well, in case one is missed."""
        app = QGuiApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screens_moved)
        app.primaryScreenChanged.connect(self._screens_moved)
        for s in app.screens():
            self._screen_added(s, settle=False)

    def _unwatch_screens(self) -> None:
        app = QGuiApplication.instance()
        if app is None:
            return
        pairs = [(app.screenAdded, self._screen_added),
                 (app.screenRemoved, self._screens_moved),
                 (app.primaryScreenChanged, self._screens_moved)]
        for s in app.screens():
            pairs += [(s.geometryChanged, self._screens_moved),
                      (s.availableGeometryChanged, self._screens_moved)]
        for sig, slot in pairs:
            try:
                sig.disconnect(slot)
            except Exception:
                pass

    def _screen_added(self, screen, settle: bool = True) -> None:
        screen.geometryChanged.connect(self._screens_moved)
        screen.availableGeometryChanged.connect(self._screens_moved)
        if settle:
            self._screens_moved()

    def _screens_moved(self, *_args) -> None:
        if not self._closed:
            self._redisplay.start(900)

    def _displays_settled(self) -> None:
        if not self._closed and self._displays_moved():
            self.persist()

    def _check_displays(self) -> None:
        if not self._redisplaying and displays.signature() != self._display_sig:
            self._displays_moved()

    def _displays_moved(self) -> bool:
        """Catch up with the monitors: look again, and put every card back where
        it belongs on the displays there are now. Returns whether any changed."""
        if self._redisplaying:
            return False
        self._redisplaying = True
        try:
            self._display_sig = displays.signature()
            before = [(d.key, d.rect, d.work, d.primary) for d in self.screens]
            self.screens = displays.current(force=True)
            if [(d.key, d.rect, d.work, d.primary) for d in self.screens] == before:
                return False
            self._place_all()
            for c in self.cards:
                self._sync_anchor(c)        # in the arrangement's new terms
            self._remember_displays()
            self._refresh_glass()
            self.displays_changed.emit()
            # Windows puts windows back where *it* thinks they go after a
            # monitor change, sometimes after this has run. One more look a
            # moment later puts back any card it moved.
            QTimer.singleShot(1500, self._reassert_spots)
            return True
        finally:
            self._redisplaying = False

    def _reassert_spots(self) -> None:
        if self._closed:
            return
        for c in self.cards:
            at = getattr(c, "_placed_at", None)
            if at is not None and c._drag_from is None and (c.x(), c.y()) != at:
                c.move(*at)
                c.update()

    def display_of(self, c: WidgetCard) -> Optional[Display]:
        """The connected display a card belongs to - None while it waits for
        one that isn't here."""
        if c.anchor is None or c.displaced:
            return None
        return displays.find(c.anchor.display, self.screens)

    def display_name(self, key: str) -> str:
        """A display's own name, whether or not it is connected."""
        d = displays.find(key, self.screens)
        if d is not None:
            return d.name
        return str(self.state.displays.get(key, {}).get("name") or "its display")

    def move_to_display(self, c: WidgetCard, key: str) -> bool:
        """Send a card to another display, keeping as near its spot on this one
        as the other's shape and the cards already there allow.

        Choosing the display a waiting card is already on settles it there:
        it stops waiting for its own.
        """
        d = displays.find(key, self.screens)
        if d is None or c not in self.cards:
            return False
        r = c.card_rect()
        cw, ch, m = int(r.width()), int(r.height()), int(MARGIN)
        here = displays.at(c.x() + r.center().x(), c.y() + r.center().y(), self.screens)
        if here.key != d.key:
            x, y = displays.carry(c.x() + m, c.y() + m, cw, ch, here.work, d.work)
            c.move(*self._free_spot(cw, ch, d.work, prefer=(x, y), skip=c))
            c.settle()
            c.update()
        self._anchor_here(c)
        self.persist()
        self.card_changed.emit(c)
        return True

    # ------------------------------------------------------------------ #
    def _free_spot(self, cw: int, ch: int, work, prefer=None, among=None,
                   skip=None) -> Tuple[int, int]:
        """Window position for a ``cw`` x ``ch`` card in a gap that fits it.

        New cards all used to land at the same spot in the top-left corner,
        stacking on whatever was already there - harmless from a tray menu used
        once, but adding several from the gallery would pile them into one
        heap. This walks the display in columns from the left, the way the
        old default was anchored, and takes the first place clear of every card
        with a little room around it. Given ``prefer`` - a card position - it
        takes the clear place nearest that instead, which is how a card that
        has lost its own display keeps roughly its spot on the one it waits on.
        """
        m = int(MARGIN)
        wl, wt, wr, wb = work
        gap, step, edge = 16, 20, 24
        taken = []
        for c in (self.cards if among is None else among):
            if c is skip:
                continue
            r = c.card_rect()
            x, y = c.x() + r.left(), c.y() + r.top()
            taken.append((x - gap, y - gap, x + r.width() + gap,
                          y + r.height() + gap))

        def clear(x, y) -> bool:
            return not any(x < tx2 and tx1 < x + cw and y < ty2 and ty1 < y + ch
                           for tx1, ty1, tx2, ty2 in taken)

        spots = ((x, y) for x in range(wl + edge, wr - cw - edge + 1, step)
                 for y in range(wt + edge, wb - ch - edge + 1, step))
        if prefer is None:
            for x, y in spots:
                if clear(x, y):
                    return x - m, y - m
            return wl + 40, wt + 40
        px, py = prefer
        if clear(px, py):
            return px - m, py - m
        best = min((s for s in spots if clear(*s)), default=None,
                   key=lambda s: (s[0] - px) ** 2 + (s[1] - py) ** 2)
        if best is None:
            return px - m, py - m           # nowhere clear; overlap rather than vanish
        return best[0] - m, best[1] - m

    def add(self, kind: str, size: str = "small",
            display: Optional[str] = None) -> Optional[WidgetCard]:
        """A new card in the first clear spot on a display - the main one
        unless another is named."""
        d = displays.find(display, self.screens) if display else None
        d = d or displays.primary(self.screens)
        cw, ch = card_size(size)
        x, y = self._free_spot(cw, ch, d.work)
        c = self._make(Placement(kind, size, x, y, display=d.key, area=list(d.work)))
        if c is None:
            return None
        self._show(c)
        c.snap()
        self._layer_one(c)
        self.persist()
        self.cards_changed.emit()
        return c

    def remove(self, c: WidgetCard) -> Optional[Placement]:
        """Take a card off the board. Returns what it was, so it can be undone."""
        if c not in self.cards:
            return None
        self._sync_anchor(c)
        pl = self._placements([c])[0]
        self.cards.remove(c)
        self._spotlit.discard(c)
        c.close()
        c.deleteLater()
        self.persist()
        self.cards_changed.emit()
        return pl

    def restore(self, pl: Placement) -> Optional[WidgetCard]:
        """Put a removed card back exactly as it was - the app window's undo."""
        c = self._make(Placement(pl.kind, pl.size, pl.x, pl.y, dict(pl.config),
                                 pl.display, list(pl.area)))
        if c is None:
            return None
        self._show(c)
        self._layer_one(c)
        self.persist()
        self.cards_changed.emit()
        return c

    def spotlight(self, card: WidgetCard, ms: int = 2400) -> None:
        """Point a card out: lift it above every window, ringed, then park it.

        In the default layer a new card lands *behind* the app window that just
        added it, which looks exactly like the Add button doing nothing. This is
        also how "Show on desktop" finds one card among nine.
        """
        if card not in self.cards:
            return
        self._spotlit.add(card)
        card.spotlit = True
        winapi.bring_to_front(int(card.winId()), topmost=True)
        card.force_update()
        QTimer.singleShot(ms, lambda c=card: self._unspot(c))

    def _unspot(self, card: WidgetCard) -> None:
        self._spotlit.discard(card)
        if card not in self.cards:
            return
        card.spotlit = False
        self._layer_one(card)
        card.force_update()

    def _card_moved(self, c: WidgetCard) -> None:
        """A card was dragged and let go: it now belongs wherever it was
        dropped - a card that was waiting for its own display included."""
        self._anchor_here(c)
        self.persist()
        self.card_changed.emit(c)

    def _placements(self, cards: Optional[Sequence[WidgetCard]] = None) -> List[Placement]:
        """The cards as the layout file holds them: where each one *belongs*,
        which for a card waiting on another display is not where it is."""
        out = []
        for c in (self.cards if cards is None else cards):
            if c.anchor is None:
                self._anchor_here(c)
            a = c.anchor
            out.append(Placement(c.kind, c.size_class, a.x, a.y, dict(c.config),
                                 a.display, list(a.area)))
        return out

    def _signature(self, placements: Sequence[Placement]) -> tuple:
        """What the saved file would contain, cheap enough to compare per tick.

        The preferences are in here as well as the cards. Theme, layer, glass and
        the rest move no widget, so a positions-only signature would have quietly
        stopped saving every one of them - and so would presets.
        """
        prefs = tuple((f.name, repr(getattr(self.state, f.name)))
                      for f in fields(BoardState) if f.name != "placements")
        cards = tuple((p.kind, p.size, p.x, p.y, p.display, tuple(p.area),
                       _config_key(p.config)) for p in placements)
        return (prefs, cards)

    def persist(self, force: bool = False) -> bool:
        """Write the layout if it has changed since the last successful write.

        A failed write clears the remembered signature rather than recording it,
        so the autosave tick retries instead of believing the board is safe.

        The monitors are checked first. Windows moves the windows off a monitor
        that has just been unplugged, sometimes before the board has heard about
        it, and a layout saved in between would record every card from that
        monitor as belonging wherever Windows had put it.
        """
        if self._closed:
            return False
        if self._holding:
            return True                     # written once the change is whole
        self._check_displays()
        for c in self.cards:
            self._sync_anchor(c)
        self._remember_displays()
        placements = self._placements()
        sig = self._signature(placements)
        if not force and sig == self._saved_sig:
            return True

        self.state.placements = placements
        ok = save(self.state)
        self._saved_sig = sig if ok else None
        return ok

    # ------------------------------------------------------------------ #
    #  Presets
    # ------------------------------------------------------------------ #
    def snapshot(self, name: str = "") -> Preset:
        """The board as it is now, as a preset: every card on every display,
        and the displays themselves."""
        for c in self.cards:
            self._sync_anchor(c)
        self._remember_displays()
        placements = self._placements()
        keys = {p.display for p in placements} | {d.key for d in self.screens}
        table = {k: dict(v) for k, v in self.state.displays.items() if k in keys}
        return Preset(name, placements, table, time.time())

    def find_preset(self, name: str) -> Optional[Preset]:
        want = name.strip().casefold()
        return next((p for p in self.state.presets if p.name.casefold() == want), None)

    def next_preset_name(self) -> str:
        n = 1
        while self.find_preset(f"Preset {n}") is not None:
            n += 1
        return f"Preset {n}"

    def save_preset(self, name: str = "") -> Optional[Preset]:
        """Save the board as a preset, replacing one of the same name.

        None for an empty board: a preset that puts nothing back isn't one, and
        applying it would take every widget off the desktop.
        """
        if not self.cards:
            return None
        snap = self.snapshot(name.strip() or self.next_preset_name())
        old = self.find_preset(snap.name)
        if old is not None:
            self.state.presets[self.state.presets.index(old)] = snap
        else:
            self.state.presets.append(snap)
        self.persist()
        self.presets_changed.emit()
        return snap

    def update_preset(self, preset: Preset) -> Optional[Preset]:
        """Overwrite a preset with the board as it is now, keeping its name and
        its place in the list. Returns the new one; the old is the undo."""
        if preset not in self.state.presets or not self.cards:
            return None
        snap = self.snapshot(preset.name)
        self.state.presets[self.state.presets.index(preset)] = snap
        self.persist()
        self.presets_changed.emit()
        return snap

    def rename_preset(self, preset: Preset, name: str) -> bool:
        """Names stay unique, ignoring case, so the tray menu can't show two
        that look the same."""
        name = name.strip()
        other = self.find_preset(name) if name else None
        if not name or preset not in self.state.presets or other not in (None, preset):
            return False
        if name != preset.name:
            preset.name = name
            self.persist()
            self.presets_changed.emit()
        return True

    def remove_preset(self, preset: Preset) -> int:
        """Delete a preset. Returns where it was in the list, for undo."""
        if preset not in self.state.presets:
            return -1
        i = self.state.presets.index(preset)
        self.state.presets.pop(i)
        self.persist()
        self.presets_changed.emit()
        return i

    def put_preset(self, preset: Preset, index: Optional[int] = None) -> None:
        """Put a preset back - the undo for removing or updating one. One with
        the same name, which is what an update leaves, is replaced."""
        if preset in self.state.presets:
            return
        same = self.find_preset(preset.name)
        if same is not None:
            self.state.presets[self.state.presets.index(same)] = preset
        else:
            n = len(self.state.presets)
            self.state.presets.insert(n if index is None else max(0, min(index, n)),
                                      preset)
        self.persist()
        self.presets_changed.emit()

    def apply_preset(self, preset: Preset) -> Optional[Preset]:
        """Lay the board out as a preset has it. Returns the layout it
        replaced, so the change can be undone by applying that - or None for a
        preset with nothing in it, which would only empty the desktop."""
        if not preset.placements:
            return None
        before = self.snapshot()
        self.apply_layout(preset.placements, preset.displays)
        return before

    def apply_layout(self, placements: Sequence[Placement],
                     table: Dict[str, dict]) -> None:
        """Make the board exactly ``placements``, whose displays ``table``
        describes as they were when it was saved.

        A card already on the board as the same widget, with the same look and
        settings, is moved into place rather than rebuilt; only the rest are
        taken off or made new. That is not just tidier. A rebuilt card starts
        from nothing - weather and stocks fetch again, a photo is decoded again -
        and a preset that only rearranges things should look like things moving.

        A widget whose display isn't connected waits on the main display, still
        belonging to its own, the same as one whose monitor was unplugged.
        """
        table = table or {}
        found = displays.resolve(table, self.screens)
        for k, info in table.items():
            if k not in found and isinstance(info, dict):
                self.state.displays.setdefault(k, dict(info))   # named while it waits
        wanted = []
        for pl in placements:
            d = found.get(pl.display)
            wanted.append(Placement(pl.kind, pl.size, pl.x, pl.y, dict(pl.config),
                                    d.key if d is not None else pl.display,
                                    list(pl.area)))
        self._holding += 1
        try:
            spare = list(self.cards)
            keep: List[Tuple[WidgetCard, Placement]] = []
            fresh: List[Placement] = []
            for pl in wanted:
                same = next((c for c in spare
                             if c.kind == pl.kind and c.config == pl.config), None)
                if same is None:
                    fresh.append(pl)
                else:
                    spare.remove(same)
                    keep.append((same, pl))
            for c in spare:
                self.cards.remove(c)
                self._spotlit.discard(c)
                c.close()
                c.deleteLater()
            for c, pl in keep:
                c.set_size_class(pl.size)
                c.anchor = self._anchor_for(pl, c)
            made = [c for c in (self._make(pl) for pl in fresh) if c is not None]
            self.cards.extend(made)
            self._place_all()
            for c, _pl in keep:
                c.settle()
                c._placed_at = (c.x(), c.y())
            for c in made:
                self._show(c, place=False)
            self._remember_displays()
        finally:
            self._holding -= 1
        self.apply_layer()
        self.persist()
        self.cards_changed.emit()
        self.presets_changed.emit()

    def preset_in_use(self, preset: Preset) -> bool:
        """Whether the board is laid out exactly as ``preset`` has it: the same
        widgets, looking the same, each where the preset puts it on the
        displays there are now."""
        if self._holding or not self.cards or len(preset.placements) != len(self.cards):
            return False
        found = displays.resolve(preset.displays, self.screens)
        m = int(MARGIN)

        def where(pl: Placement):
            d = found.get(pl.display)
            x, y = pl.x, pl.y
            if d is not None and len(pl.area) == 4 and tuple(pl.area) != d.work:
                w, h = placement_size(pl)
                x, y = displays.carry(x + m, y + m, w, h, tuple(pl.area), d.work)
                x, y = x - m, y - m
            return (pl.kind, pl.size, d.key if d is not None else pl.display, x, y,
                    _config_key(pl.config))

        for c in self.cards:
            self._sync_anchor(c)
        here = sorted((p.kind, p.size, p.display, p.x, p.y, _config_key(p.config))
                      for p in self._placements())
        return sorted(where(pl) for pl in preset.placements) == here

    # ------------------------------------------------------------------ #
    #  Appearance and layering
    # ------------------------------------------------------------------ #
    def set_theme(self, theme: str) -> None:
        self.state.theme = theme
        self.palette = build_palette(theme, self.state.accent)
        for c in self.cards:
            c.set_palette_(self.palette)
            winapi.apply_backdrop(int(c.winId()), self.state.backdrop,
                                  self.palette.dark)
        self.persist()
        self.prefs_changed.emit()

    def set_accent(self, accent: str) -> None:
        self.state.accent = accent
        self.palette = build_palette(self.state.theme, accent)
        for c in self.cards:
            c.set_palette_(self.palette)
        self.persist()
        self.prefs_changed.emit()

    def set_opacity(self, value: float) -> None:
        self.state.opacity = max(0.35, min(1.0, value))
        for c in self.cards:
            c.opacity_ = self.state.opacity
            c.update()
        self.persist()
        self.prefs_changed.emit()

    def _check_fullscreen(self) -> None:
        """Go quiet behind a game, and catch up when it leaves.

        Only the cards on the game's own display: a game on one monitor hides
        nothing on another, and widgets on a second screen beside a game are
        often the whole reason for putting them there.
        """
        rect = winapi.fullscreen_monitor()
        for c in self.cards:
            r = c.card_rect()
            x, y = c.x() + r.center().x(), c.y() + r.center().y()
            hidden = (rect is not None and rect[0] <= x < rect[2]
                      and rect[1] <= y < rect[3])
            if hidden != c.quiet:
                c.quiet = hidden
                if not hidden:
                    # It skipped its repaints while quiet, so it is showing a
                    # stale frame. One forced repaint brings it back.
                    c.force_update()
        # Stop the visualiser listening too: with every media card hidden its
        # bars are analysing audio nobody can see, and that is all the CPU quiet
        # mode would otherwise still be spending.
        media = [c for c in self.cards if c.kind == "media"]
        paused = rect is not None and all(c.quiet for c in media)
        if paused == self._quiet:
            return
        self._quiet = paused
        try:
            from . import audio
            audio.set_paused(paused)
        except Exception:
            pass

    def _refresh_glass(self) -> None:
        # Kept current with the widgets' glass switched off too: the app
        # window's sidebar is frosted with the same picture.
        if self.glass.refresh():
            self.glass_changed.emit()
            if self.state.glass:
                for c in self.cards:
                    c.update()

    def set_glass(self, on: bool) -> None:
        self.state.glass = on
        if on:
            self.glass.refresh(force=True)
        for c in self.cards:
            c.glass = self.glass if on else None
            c.update()
        self.persist()
        self.prefs_changed.emit()

    def set_glass_tint(self, alpha: int) -> None:
        self.state.glass_tint = int(alpha)
        for c in self.cards:
            c.glass_tint = int(alpha)
            c.update()
        self.persist()
        self.prefs_changed.emit()

    def set_snap(self, snap: bool) -> None:
        self.state.snap = snap
        for c in self.cards:
            c.snap_to_grid = snap
        self.persist()
        self.prefs_changed.emit()

    def set_locked(self, locked: bool) -> None:
        self.state.locked = locked
        for c in self.cards:
            c.locked = locked
            c.update()              # Files hides its resize grip when locked
        self.persist()
        self.prefs_changed.emit()

    def set_layer(self, layer: str) -> None:
        self.state.layer = layer
        self.peeked = False
        self.apply_layer()
        self._park.start(1500) if layer == "desktop" else self._park.stop()
        self.persist()
        self.prefs_changed.emit()

    def _layer_one(self, card: WidgetCard) -> None:
        hwnd = int(card.winId())
        if self.state.layer == "top" or self.peeked:
            winapi.bring_to_front(hwnd, topmost=True)
        elif self.state.layer == "desktop":
            winapi.bring_to_front(hwnd, topmost=False)
            winapi.send_to_bottom(hwnd)
        else:
            winapi.bring_to_front(hwnd, topmost=False)

    def apply_layer(self) -> None:
        for c in self.cards:
            if c not in self._spotlit:
                self._layer_one(c)

    def _reassert_layer(self) -> None:
        if self.state.layer == "desktop" and not self.peeked:
            for c in self.cards:
                if c not in self._spotlit:
                    winapi.send_to_bottom(int(c.winId()))

    def toggle_peek(self) -> None:
        """Summon the board over everything, or send it back down."""
        if self.state.layer == "top":
            return          # already permanently in front
        self.peeked = not self.peeked
        self.apply_layer()
        self.prefs_changed.emit()

    # ------------------------------------------------------------------ #
    def close_all(self) -> None:
        self._park.stop()
        self._glass_timer.stop()
        self._autosave.stop()
        self._watch.stop()
        self._redisplay.stop()
        self._unwatch_screens()
        card.set_quiet(False)
        # Force it: closing is the last chance, and a stale signature must not
        # be able to skip the final write.
        self.persist(force=True)
        self._closed = True
        for c in self.cards:
            c.close()
        self.cards.clear()
