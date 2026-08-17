"""
store.py
========
Board layout and preferences on disk. Forgiving by design: a corrupt or missing
file means a default board, never a crash on startup.
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


@dataclass
class Placement:
    """One widget on the board."""

    kind: str                       # "clock", "weather", ...
    size: str = "small"             # "small" | "medium" | "large"
    x: int = 0
    y: int = 0
    config: Dict[str, Any] = field(default_factory=dict)


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
    placements: List[Placement] = field(default_factory=list)


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
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        # Filter to known fields so an older or newer file never blows up.
        p_keys = {f.name for f in fields(Placement)}
        b_keys = {f.name for f in fields(BoardState)} - {"placements"}
        placements = [
            Placement(**{k: v for k, v in p.items() if k in p_keys})
            for p in raw.get("placements", [])
        ]
        known = {k: v for k, v in raw.items() if k in b_keys}
        return BoardState(placements=placements, **known)
    except Exception:
        return default_board(work_area or (0, 0, 1920, 1080))


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

    tmp = _path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(blob)

        # Keep the previous good layout one step back - but only once the
        # replacement is safely on disk. The old order renamed board.json to
        # .bak *before* writing, so a write that failed left no board.json at
        # all and the next launch came up with a default board.
        if os.path.exists(_path()):
            try:
                shutil.copy2(_path(), _path() + ".bak")
            except OSError:
                pass

        os.replace(tmp, _path())     # atomic; a crash mid-write can't corrupt it
        return True
    except Exception as exc:
        _log(f"could not write {_path()}: {exc!r}")
        return False


def notes_path() -> str:
    return os.path.join(_dir(), "notes.txt")
