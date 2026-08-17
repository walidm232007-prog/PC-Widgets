"""
palette.py
==========
Colours for the widget board.

Where possible this borrows the *actual* palette from the shared
``design_system`` so the widgets sit in the same visual family as the Face &
Finger Tracker and the Voice Transcriber. That package imports its own modules
by flat name (``from settings_manager import ...``), so it can't be imported as
a package - but ``tokens.py`` is pure data with no Qt and no internal imports,
which makes it safe to load on its own.

If it isn't there, the values below are the same ones it would have produced,
so the widgets always render.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

def _find_design_system() -> str:
    """Locate the shared design_system, wherever the suite currently lives.

    Originally this was an absolute path under one machine's home directory.
    Moving the suite broke it silently - the palette just fell back to its
    built-in values with nothing to indicate why - so it is searched relative to
    this file instead, and the two travel together.

    Two places: a sibling directory, which is how the suite is laid out, and a
    copy inside the project, which is how a standalone checkout gets the same
    colours instead of quietly getting the fallbacks. ``source()`` reports which
    one won, so a silent fall back to built-ins is visible in --selftest.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    project = os.path.dirname(os.path.dirname(here))      # .../pc-widgets
    candidates = [
        os.path.join(os.path.dirname(project), "design_system"),   # sibling
        os.path.join(project, "design_system"),                    # in-tree
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "tokens.py")):
            return path
    return candidates[0]


_DESIGN_SYSTEM = _find_design_system()

_tokens = None
try:
    if os.path.isdir(_DESIGN_SYSTEM):
        # Appended, never inserted: that directory holds generically-named
        # modules (theme.py, components.py) and must not shadow ours.
        if _DESIGN_SYSTEM not in sys.path:
            sys.path.append(_DESIGN_SYSTEM)
        import tokens as _tokens  # type: ignore
except Exception:
    _tokens = None


def _mix(c1: str, c2: str, t: float) -> str:
    a = tuple(int(c1.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    b = tuple(int(c2.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def lighten(c: str, t: float) -> str:
    return _tokens.lighten(c, t) if _tokens else _mix(c, "#ffffff", t)


def darken(c: str, t: float) -> str:
    return _tokens.darken(c, t) if _tokens else _mix(c, "#000000", t)


ACCENTS = {
    "blue": "#3b82f6", "purple": "#8b5cf6", "green": "#22c55e",
    "orange": "#f97316", "red": "#ef4444", "pink": "#ec4899", "teal": "#14b8a6",
}
if _tokens is not None:
    ACCENTS = dict(_tokens.ACCENTS)


@dataclass(frozen=True)
class Palette:
    """Flat colour set. Widgets paint themselves, so these are plain hex."""

    dark: bool
    accent: str
    card: str          # card fill, before the translucency in card.py
    card_edge: str
    text: str
    text_dim: str
    text_faint: str
    good: str
    warn: str
    bad: str

    @property
    def accent_soft(self) -> str:
        return _mix(self.accent, self.card, 0.62)


def build(theme: str = "dark", accent_name: str = "blue") -> Palette:
    accent = ACCENTS.get(accent_name, ACCENTS["blue"])
    dark = theme != "light"

    # Apple's label hierarchy is pure white/black at descending alpha, not
    # separate tinted greys. Using bluish greys is a big part of why type on a
    # widget can look "not quite Apple" even when the face is right. Qt reads
    # #AARRGGBB, so these carry their own alpha over the frosted material.
    if dark:
        return Palette(
            dark=True, accent=accent,
            card="#12141a", card_edge="#2b313d",
            text="#ffffff",             # label
            text_dim="#99ffffff",       # secondaryLabel, 60%
            text_faint="#4dffffff",     # tertiaryLabel, 30%
            good="#32d74b", warn="#ff9f0a", bad="#ff453a",   # iOS dark systems
        )
    return Palette(
        dark=False, accent=accent,
        card="#ffffff", card_edge="#dfe3ea",
        text="#000000",
        text_dim="#99000000",
        text_faint="#4d000000",
        good="#34c759", warn="#ff9500", bad="#ff3b30",       # iOS light systems
    )


def source() -> str:
    """Where the palette came from - surfaced in --selftest output."""
    return "design_system/tokens.py" if _tokens else "built-in fallback"
