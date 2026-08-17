"""
board.py
========
Owns every widget on screen: creating them from saved placements, keeping them
on the right layer, persisting moves, and summoning the whole set forward on a
hotkey.

The layering is the interesting part. Phone and Mac widgets sit *on* the
desktop, below your work - which is right until you actually want to look at
one. So the board has two states: parked on the desktop, and peeked to the
front. The hotkey toggles between them.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Dict, List, Optional, Type

from PySide6.QtCore import QObject, QTimer

from . import card, winapi
from .card import WidgetCard
from .glass import GlassBackdrop
from .palette import Palette, build as build_palette
from .store import BoardState, Placement, save          # noqa: F401 (BoardState: fields())

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
    from ..tiles.media import MediaWidget
    from ..tiles.notes import NotesWidget
    from ..tiles.outlook import OutlookWidget
    from ..tiles.stocks import StocksWidget
    from ..tiles.system import DiskWidget, SystemWidget
    from ..tiles.weather import WeatherWidget
    from ..tiles.workspace import WorkspaceWidget

    for cls in (ClockWidget, WeatherWidget, StocksWidget, SystemWidget,
                DiskWidget, CalendarWidget, NotesWidget,
                WorkspaceWidget, MediaWidget, OutlookWidget, CommsWidget):
        register(cls)


def available() -> List[Type[WidgetCard]]:
    return sorted(REGISTRY.values(), key=lambda c: c.title)


# --------------------------------------------------------------------------- #
#  Board
# --------------------------------------------------------------------------- #
class Board(QObject):
    def __init__(self, state: BoardState) -> None:
        super().__init__()
        self.state = state
        self.palette: Palette = build_palette(state.theme, state.accent)
        self.cards: List[WidgetCard] = []
        self.peeked = False
        self._saved_sig: Optional[tuple] = None

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

        # Stop painting behind a game. Checked once here for the whole board -
        # the answer is about the foreground window, not about any one card, so
        # asking nine times would be nine times the work for one result.
        self._quiet = False
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._check_fullscreen)
        self._watch.start(1000)

    # ------------------------------------------------------------------ #
    def build(self) -> None:
        wanted = len(self.state.placements)
        for pl in list(self.state.placements):
            self._spawn(pl)
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

    def _spawn(self, pl: Placement) -> Optional[WidgetCard]:
        cls = REGISTRY.get(pl.kind)
        if cls is None:
            return None
        card = cls(self.palette, pl.size, pl.config)
        card.opacity_ = self.state.opacity
        card.locked = self.state.locked
        card.snap_to_grid = self.state.snap
        card.glass = self.glass if self.state.glass else None
        card.glass_tint = self.state.glass_tint
        card.move(pl.x, pl.y)
        # Saved coordinates can stop being valid: a monitor gets unplugged, the
        # resolution changes, or the card geometry itself changes between
        # versions. Without this a widget can restore off-screen and be
        # unreachable, with no way to drag it back.
        card.settle()
        card.start()
        winapi.apply_backdrop(int(card.winId()), self.state.backdrop,
                              self.palette.dark)
        card.moved.connect(self.persist)
        card.changed.connect(self.persist)
        card.removed.connect(self.remove)
        self.cards.append(card)
        return card

    # ------------------------------------------------------------------ #
    def add(self, kind: str, size: str = "small") -> Optional[WidgetCard]:
        wl, wt, wr, wb = winapi.work_area()
        pl = Placement(kind, size, wl + 40, wt + 40)
        card = self._spawn(pl)
        if card is None:
            return None
        card.snap()
        self._layer_one(card)
        self.persist()
        return card

    def remove(self, card: WidgetCard) -> None:
        if card not in self.cards:
            return
        self.cards.remove(card)
        card.close()
        card.deleteLater()
        self.persist()

    def _signature(self) -> tuple:
        """What the saved file would contain, cheap enough to compare per tick.

        The preferences are in here as well as the cards. Theme, layer, glass and
        the rest move no widget, so a positions-only signature would have quietly
        stopped saving every one of them.
        """
        prefs = tuple((f.name, repr(getattr(self.state, f.name)))
                      for f in fields(BoardState) if f.name != "placements")
        cards = tuple(
            (c.kind, c.size_class, c.x(), c.y(),
             tuple(sorted((k, repr(v)) for k, v in c.config.items())))
            for c in self.cards
        )
        return (prefs, cards)

    def persist(self, force: bool = False) -> bool:
        """Write the layout if it has changed since the last successful write.

        A failed write clears the remembered signature rather than recording it,
        so the autosave tick retries instead of believing the board is safe.
        """
        sig = self._signature()
        if not force and sig == self._saved_sig:
            return True

        self.state.placements = [
            Placement(c.kind, c.size_class, c.x(), c.y(), dict(c.config))
            for c in self.cards
        ]
        ok = save(self.state)
        self._saved_sig = sig if ok else None
        return ok

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

    def set_accent(self, accent: str) -> None:
        self.state.accent = accent
        self.palette = build_palette(self.state.theme, accent)
        for c in self.cards:
            c.set_palette_(self.palette)
        self.persist()

    def set_opacity(self, value: float) -> None:
        self.state.opacity = max(0.35, min(1.0, value))
        for c in self.cards:
            c.opacity_ = self.state.opacity
            c.update()
        self.persist()

    def _check_fullscreen(self) -> None:
        """Go quiet while a game is in front, and catch up when it leaves."""
        now = winapi.foreground_is_fullscreen()
        if now == self._quiet:
            return
        self._quiet = now
        card.set_quiet(now)
        # Stop the visualiser listening too: with the board hidden its bars are
        # analysing audio nobody can see, and that is all the CPU quiet mode
        # would otherwise still be spending.
        try:
            from . import audio
            audio.set_paused(now)
        except Exception:
            pass
        if not now:
            # Every card skipped its repaints while quiet, so each one is showing
            # a stale frame. One forced repaint each brings the board back.
            for c in self.cards:
                c.force_update()

    def _refresh_glass(self) -> None:
        if self.state.glass and self.glass.refresh():
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

    def set_glass_tint(self, alpha: int) -> None:
        self.state.glass_tint = int(alpha)
        for c in self.cards:
            c.glass_tint = int(alpha)
            c.update()
        self.persist()

    def set_snap(self, snap: bool) -> None:
        self.state.snap = snap
        for c in self.cards:
            c.snap_to_grid = snap
        self.persist()

    def set_locked(self, locked: bool) -> None:
        self.state.locked = locked
        for c in self.cards:
            c.locked = locked
        self.persist()

    def set_layer(self, layer: str) -> None:
        self.state.layer = layer
        self.peeked = False
        self.apply_layer()
        self._park.start(1500) if layer == "desktop" else self._park.stop()
        self.persist()

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
            self._layer_one(c)

    def _reassert_layer(self) -> None:
        if self.state.layer == "desktop" and not self.peeked:
            for c in self.cards:
                winapi.send_to_bottom(int(c.winId()))

    def toggle_peek(self) -> None:
        """Summon the board over everything, or send it back down."""
        if self.state.layer == "top":
            return          # already permanently in front
        self.peeked = not self.peeked
        self.apply_layer()

    # ------------------------------------------------------------------ #
    def close_all(self) -> None:
        self._park.stop()
        self._glass_timer.stop()
        self._autosave.stop()
        self._watch.stop()
        card.set_quiet(False)
        # Force it: closing is the last chance, and a stale signature must not
        # be able to skip the final write.
        self.persist(force=True)
        for c in self.cards:
            c.close()
        self.cards.clear()
