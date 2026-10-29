"""
store.py
========
Board layout, presets and preferences on disk. Forgiving by design: a corrupt or
missing file means a default board, never a crash on startup.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List


def _dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "PCWidgets")


def _path() -> str:
    return os.path.join(_dir(), "board.json")


def _mirror_dir() -> str:
    """A second home for the layout, beside the app rather than in the profile."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), ".boardstate")


def _mirror_path() -> str:
    return os.path.join(_mirror_dir(), "board.json")


def _both_paths():
    """Where the layout lives, primary first.

    Two, for the reason the agent bus has two: this machine's board process has
    repeatedly been unable to read or write ``%APPDATA%`` while handling the app
    folder perfectly, and a layout that cannot be read comes back as a *default*
    board - every colour, picture and position silently gone. Mirroring it means
    one unreadable location costs nothing.
    """
    return [_path(), _mirror_path()]


@dataclass
class Placement:
    """One widget on the board."""

    kind: str                       # "clock", "weather", ...
    size: str = "small"             # "small" | "medium" | "large"
    x: int = 0
    y: int = 0
    config: Dict[str, Any] = field(default_factory=dict)
    # The display the widget belongs to, and that display's work area when x, y
    # were taken (see displays.py). For a widget waiting on another display
    # because its own isn't connected, x and y are still where it belongs.
    # Empty in a layout saved before displays were tracked, which places by
    # x and y alone.
    display: str = ""
    area: List[int] = field(default_factory=list)


@dataclass
class Preset:
    """A whole arrangement, saved by name: every widget on every display, with
    its size, look and settings - and the displays as they were, so a preset
    can be drawn as it was saved, and matched to a monitor on a new port."""

    name: str
    placements: List[Placement] = field(default_factory=list)
    displays: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    saved: float = 0.0              # time.time() when it was saved


@dataclass
class BoardState:
    theme: str = "dark"
    accent: str = "blue"
    layer: str = "desktop"          # "desktop" | "top" | "normal"
    # Windows 11 material behind each card. Off by default: DWM fills the whole
    # *window*, and the window is now larger than the card so it has a
    # transparent gutter for the shadow. With a backdrop on, that gutter gets
    # painted too and every card sits in a grey block.
    backdrop: str = "off"
    opacity: float = 1.0
    locked: bool = False            # stop accidental dragging
    snap: bool = False              # free placement by default
    glass: bool = True              # frosted wallpaper behind each card
    glass_tint: int = 112           # alpha of the tint over the blur
    close_hint_shown: bool = False  # "closing the window keeps the widgets", said once
    placements: List[Placement] = field(default_factory=list)
    # Every display a widget belongs to, connected or not: its name, bounds and
    # work area, by device path - how one that is unplugged is still named, and
    # told apart from a new monitor.
    displays: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    presets: List[Preset] = field(default_factory=list)


def default_board(work_area) -> BoardState:
    """A sensible starting board, laid out down the right-hand side."""
    wl, wt, wr, wb = work_area
    right = wr - 348 - 24
    col2 = wr - 168 - 24
    return BoardState(placements=[
        Placement("clock", "small", col2, wt + 24),
        Placement("weather", "medium", right, wt + 24 + 180),
        Placement("system", "medium", right, wt + 24 + 360),
        Placement("disk", "small", col2, wt + 24 + 540),
        Placement("notes", "small", right, wt + 24 + 540),
    ])


def load(work_area=None) -> BoardState:
    """Read the layout, from whichever copy is newest and readable.

    Falling back to a default board is a last resort, not a first one. It used to
    happen on *any* exception from the single file - and a default board means
    every position, colour and background picture the user set is silently gone,
    which is indistinguishable from the app having forgotten them.
    """
    best = None
    for src in _both_paths():
        try:
            mtime = os.path.getmtime(src)
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, src)

    tried = []
    order = [best[1]] if best else []
    order += [q for q in _both_paths() if q not in order]
    for src in order:
        try:
            with open(src, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            # Filter to known fields so an older or newer file never blows up.
            b_keys = ({f.name for f in fields(BoardState)}
                      - {"placements", "presets", "displays"})
            placements = [_placement(p) for p in raw.get("placements", [])]
            known = {k: v for k, v in raw.items() if k in b_keys}
            if tried:
                report(f"layout read from {src} after {tried[0]} failed")
            return BoardState(placements=placements,
                              displays=_table(raw.get("displays")),
                              presets=_presets(raw.get("presets")), **known)
        except FileNotFoundError:
            tried.append(src)
        except Exception as exc:
            tried.append(src)
            report(f"could not read {src}: {exc!r}")

    report("no readable layout; starting from a default board")
    return default_board(work_area or (0, 0, 1920, 1080))


def _placement(raw: Dict[str, Any]) -> Placement:
    keys = {f.name for f in fields(Placement)}
    return Placement(**{k: v for k, v in raw.items() if k in keys})


def _table(raw) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _presets(raw) -> List[Preset]:
    """The saved presets, skipping any that can't be read rather than losing
    the layout over one of them."""
    out: List[Preset] = []
    for q in raw if isinstance(raw, list) else []:
        try:
            out.append(Preset(name=str(q.get("name") or "Preset"),
                              placements=[_placement(p) for p in q.get("placements", [])],
                              displays=_table(q.get("displays")),
                              saved=float(q.get("saved") or 0.0)))
        except Exception as exc:
            report(f"skipped an unreadable preset: {exc!r}")
    return out


def _saved_placement_count() -> int:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            return len(json.load(fh).get("placements", []))
    except Exception:
        return 0


def log_path() -> str:
    return os.path.join(_dir(), "errors.log")


def _log(msg: str) -> None:
    """Record a failure somewhere it can be found.

    ``save`` has to swallow exceptions - a failed write must never take the
    board down - but swallowing them *silently* is how the layout once stopped
    being saved for half an hour with nothing looking wrong. A widget dragged
    somewhere and not saved is invisible until the next launch, so the only
    evidence has to be written down.
    """
    try:
        os.makedirs(_dir(), exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def report(msg: str) -> None:
    """Write a diagnostic to both the data directory and the app folder.

    store._log alone is not enough: the log under %APPDATA% is exactly the one a
    process may be unable to write, and a message that lands nowhere is how the
    media widget stayed broken for a week. Two places, so losing one cannot hide
    the evidence.
    """
    _log(msg)
    try:
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with open(os.path.join(here, "_widgets.log"), "a",
                  encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def save(state: BoardState, allow_empty: bool = False) -> bool:
    """Write the board, refusing to replace a real layout with an empty one.

    The board persists on startup, so a single run that comes up with no cards -
    for any reason at all - used to overwrite the saved layout with an empty
    list, and every launch after that was empty too. One transient failure
    destroyed the arrangement permanently. Writing nothing over something is
    never what the user meant, so it takes an explicit override.

    Returns whether the layout reached the disk, so a caller can retry rather
    than assume.
    """
    if not state.placements and not allow_empty and _saved_placement_count():
        _log("refused to write an empty layout over a saved one")
        return False

    try:
        os.makedirs(_dir(), exist_ok=True)
        blob = json.dumps(asdict(state), indent=2)
    except Exception as exc:
        _log(f"could not serialise the layout: {exc!r}")
        return False

    landed = []
    for dest in _both_paths():
        tmp = dest + ".tmp"
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(blob)

            # Keep the previous good layout one step back - but only once the
            # replacement is safely on disk. The old order renamed board.json to
            # .bak *before* writing, so a write that failed left no board.json
            # at all and the next launch came up with a default board.
            if os.path.exists(dest):
                try:
                    shutil.copy2(dest, dest + ".bak")
                except OSError:
                    pass

            os.replace(tmp, dest)    # atomic; a crash mid-write can't corrupt it
            landed.append(dest)
        except Exception as exc:
            _log(f"could not write {dest}: {exc!r}")
    return bool(landed)


def notes_path() -> str:
    return os.path.join(_dir(), "notes.txt")
