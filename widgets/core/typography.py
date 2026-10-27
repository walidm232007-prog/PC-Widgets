"""
typography.py
=============
Apple's type system, as closely as it can be reproduced off an Apple platform.

Three things make Apple-set text look the way it does, and only the first is the
typeface:

**The face.** Inter, which ships in ``fonts/``. Apple's own SF Pro can't be used:
its licence covers designing for Apple platforms, not shipping it with a Windows
app. Inter is the closest free relative, and its licence (the SIL OFL) lets it
travel with the code. If the files are missing the stack falls back to
Helvetica Neue (SF's direct ancestor and Apple's own system font through
iOS 7-8), then Segoe UI Variable.

**Optical sizes.** Apple sets anything from 20pt up in SF Pro *Display* and
smaller text in SF Pro *Text*: the display cut has tighter spacing and finer
detail. Inter's variable font has the same idea as a continuous ``opsz`` axis,
from its text cut at 14 to its Display cut at 32, and :func:`font` sets it from
the point size. Families that ship the split as separate faces instead, like
Segoe UI Variable, switch at Apple's 20pt.

**Tracking.** This is the part people miss, and it matters more than the face.
Apple applies a size-dependent letter-spacing curve - noticeably loose at
caption sizes, progressively tighter as type grows, about -3% by the time you
reach a 40pt number. Big numerals set at default spacing are the single
clearest giveaway that something is not Apple type. :data:`TRACKING` is Apple's
published table for SF and :func:`tracking_percent` interpolates it; Inter gets
the same curve, which has the shape of Inter's own guidance.
"""

from __future__ import annotations

import functools
import os
from bisect import bisect_left
from typing import Dict, List, Optional, Tuple

from PySide6.QtGui import QFont, QFontDatabase

# Faces sitting next to the app are loaded privately with addApplicationFont:
# available to this process only, nothing installed, nothing else on the
# machine changed. Inter ships there; any .otf/.ttf in ``fonts/`` is picked up.
FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fonts")

_loaded: List[str] = []


def load_bundled() -> List[str]:
    """Register any fonts shipped beside the app. Call once, after QApplication."""
    global _loaded
    if _loaded or not os.path.isdir(FONT_DIR):
        return _loaded
    names = set()
    for entry in sorted(os.listdir(FONT_DIR)):
        if not entry.lower().endswith((".otf", ".ttf", ".ttc")):
            continue
        fid = QFontDatabase.addApplicationFont(os.path.join(FONT_DIR, entry))
        if fid != -1:
            names.update(QFontDatabase.applicationFontFamilies(fid))
    _loaded = sorted(names)
    return _loaded

# Apple's tracking table for SF: point size -> tracking in 1/1000 em.
TRACKING: List[Tuple[float, float]] = [
    (6, 41), (8, 26), (9, 19), (10, 12), (11, 6), (12, 0), (13, -6),
    (14, -11), (15, -16), (16, -20), (17, -21), (18, -22), (20, -24),
    (22, -26), (24, -27), (28, -29), (32, -30), (36, -31), (40, -32),
    (44, -33), (48, -34), (64, -37), (80, -39), (96, -40),
]
_SIZES = [t[0] for t in TRACKING]

# Best first. Display and Text variants are picked per size where both exist.
# "Inter Variable" is the bundled one; plain Inter is a system-wide install.
DISPLAY_STACK = [
    "Inter Variable", "Inter Display", "Inter", "Helvetica Neue", "Helvetica",
    "Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI", "Arial",
]
TEXT_STACK = [
    "Inter Variable", "Inter", "Inter Display", "Helvetica Neue", "Helvetica",
    "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Arial",
]

# Apple switches from the text cut to the display cut here.
OPTICAL_BREAK = 20.0

# Families with an optical-size axis, and its range. Within it the axis follows
# the point size, so the cut changes continuously rather than at one break.
# Qt can only set an axis from 6.7; on 6.6 the font draws at its default, the
# text cut.
OPSZ_AXIS: Dict[str, Tuple[float, float]] = {"Inter Variable": (14.0, 32.0)}
_OPSZ = QFont.Tag("opsz") if hasattr(QFont, "Tag") else None

_resolved: Dict[str, str] = {}


def _first_available(stack: List[str]) -> str:
    families = set(QFontDatabase.families())
    for name in stack:
        if name in families:
            return name
    return "Segoe UI"


def resolve() -> Dict[str, str]:
    """Pick the display and text families once, after QApplication exists."""
    global _resolved
    if not _resolved:
        load_bundled()
        _resolved = {
            "display": _first_available(DISPLAY_STACK),
            "text": _first_available(TEXT_STACK),
        }
    return _resolved


def family_for(size: float) -> str:
    r = resolve()
    return r["display"] if size >= OPTICAL_BREAK else r["text"]


def tracking_percent(size: float) -> float:
    """Apple's tracking for a point size, as a Qt percentage spacing.

    Qt's PercentageSpacing is 100 = normal, so -30/1000 em becomes 97.
    """
    if size <= _SIZES[0]:
        per_mille = TRACKING[0][1]
    elif size >= _SIZES[-1]:
        per_mille = TRACKING[-1][1]
    else:
        i = bisect_left(_SIZES, size)
        if _SIZES[i] == size:
            per_mille = TRACKING[i][1]
        else:
            s0, t0 = TRACKING[i - 1]
            s1, t1 = TRACKING[i]
            f = (size - s0) / (s1 - s0)
            per_mille = t0 + (t1 - t0) * f
    return 100.0 + per_mille / 10.0


def font(size: float, weight: int = QFont.Normal,
         tracking: bool = True, italic: bool = False,
         stretch: int = 0) -> QFont:
    """A font set the way Apple would set it at this size.

    ``italic`` and ``stretch`` exist for the clock's customizable time. Inter
    has a real italic; where a family has no dedicated face Qt synthesises one -
    an oblique slant, or for width a horizontal scale, since Inter has no width
    axis - so a widened or italic time works on any family. ``stretch`` is a
    percentage (100 = normal); 0 leaves it at the font's own width.

    Built once per combination and handed out as copies. Every paint asks for
    several fonts, and building one - family, tracking, hinting - cost about as
    much as drawing the text it was for. A QFont copy is shared until changed,
    so a caller adjusting its copy cannot touch the cached one.
    """
    return QFont(_font(float(size), int(weight), bool(tracking), bool(italic),
                       int(stretch)))


@functools.lru_cache(maxsize=512)
def _font(size: float, weight: int, tracking: bool, italic: bool,
          stretch: int) -> QFont:
    family = family_for(size)
    f = QFont(family)
    f.setPointSizeF(float(size))
    # Coerce to the Weight enum: a weight loaded from JSON is a plain int,
    # and PySide6's setWeight rejects those.
    f.setWeight(QFont.Weight(int(weight)))
    axis = OPSZ_AXIS.get(family)
    if axis is not None and _OPSZ is not None:
        f.setVariableAxis(_OPSZ, min(axis[1], max(axis[0], float(size))))
    if italic:
        f.setItalic(True)
    if stretch:
        f.setStretch(int(stretch))

    # Windows grid-fits glyphs to whole pixels by default, which sharpens stems
    # at the cost of distorting the letterform - the reason type can look
    # chiselled and slightly uneven. Apple renders from the outline with almost
    # no hinting, so shapes stay true and edges stay soft. PreferNoHinting plus
    # PreferAntialias asks Qt for the same treatment.
    f.setHintingPreference(QFont.PreferNoHinting)
    f.setStyleStrategy(
        QFont.StyleStrategy(QFont.PreferAntialias | QFont.PreferQuality))

    if tracking:
        f.setLetterSpacing(QFont.PercentageSpacing, tracking_percent(size))
    return f


def describe() -> str:
    r = resolve()
    if r["display"] == r["text"]:
        return r["display"]
    return f"{r['display']} / {r['text']}"
