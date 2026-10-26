"""
appearance.py - per-card colour customization.

One card can override the two colours that matter - its background and its
text - and choose whether the background is the frosted glass everything else
uses or a flat fill. The whole thing is expressed as three keys in the card's
``config`` (``bg``, ``fg``, ``fill``), so it persists with the layout and costs
nothing when unset.

The trick that keeps it from touching every tile's paint code: a tile asks for
colour through its palette - ``pal.text``, ``pal.card`` and the dim/faint steps -
and never hard-codes a hex. So overriding those fields on a *derived* palette
recolours the tile without the tile knowing customization exists. :func:`effective`
builds that palette; :meth:`WidgetCard.set_palette_` installs it.

Two tiles need more than a recoloured palette and say so themselves: Notes keeps
its typed text legible against whatever background is chosen rather than following
``fg``, and Stocks turns a red line white on a red card so the graph never
vanishes into its own background. Both call helpers here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Tuple

from .palette import Palette, _mix

# Curated swatches for the menu. Kept small on purpose: a wall of colour is a
# worse experience than eight good ones and a "Custom…" escape hatch.
BG_SWATCHES = (
    ("Charcoal", "#1c1e24"), ("Slate", "#2b2f38"), ("Ink", "#0b0d12"),
    ("Snow", "#f4f5f7"), ("Sand", "#e9e2d4"),
    ("Ocean", "#1f3a5f"), ("Forest", "#1e3a2f"), ("Plum", "#3a1f3d"),
    ("Wine", "#4a1420"),
)
FG_SWATCHES = (
    ("White", "#ffffff"), ("Soft white", "#e6e8ee"), ("Black", "#101216"),
    ("Amber", "#ffcf70"), ("Mint", "#7fe0b0"), ("Sky", "#8fc7ff"),
    ("Rose", "#ff9fb0"),
)


def _lum(hex_colour: str) -> float:
    """Perceived luminance 0..1. Rough Rec. 601, which is plenty for a yes/no."""
    h = hex_colour.lstrip("#")
    if len(h) < 6:
        return 0.5
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b


def contrast_text(bg_hex: str) -> str:
    """Black or white, whichever stays readable on ``bg_hex``.

    The threshold sits a little above 0.5 because white text holds up on a
    mid-grey where black would not.
    """
    return "#101216" if _lum(bg_hex) > 0.6 else "#ffffff"


def _rgb(hex_colour: str) -> Tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _hsv(hex_colour: str):
    """(hue 0..360, saturation 0..1, value 0..1)."""
    r, g, b = (c / 255.0 for c in _rgb(hex_colour))
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = 60 * (((g - b) / d) % 6)
    elif mx == g:
        h = 60 * (((b - r) / d) + 2)
    else:
        h = 60 * (((r - g) / d) + 4)
    return h, (0.0 if mx == 0 else d / mx), mx


def hue_near(a_hex: str, b_hex: str, tol: float = 40.0) -> bool:
    """Do two colours read as the same family - both red, or both green?

    Compared by hue angle, not raw RGB, because "a red background" is any red:
    a dark wine (#4a1420) and a bright signal red (#ff453a) are far apart in RGB
    but the same hue, and the line vanishes into either. Both sides must be
    saturated enough to *have* a family - a grey card is not "red" however the
    numbers land - and the hue distance wraps at 360 so reds near 0 and 360 are
    recognised as one.
    """
    ah, asat, _av = _hsv(a_hex)
    bh, bsat, _bv = _hsv(b_hex)
    if asat < 0.30 or bsat < 0.30:
        return False
    dist = abs(ah - bh) % 360
    if dist > 180:
        dist = 360 - dist
    return dist <= tol


def is_solid(config: dict) -> bool:
    return config.get("fill") == "solid"


def effective(base: Palette, config: dict) -> Palette:
    """Derive a card's palette from the base theme plus its overrides.

    Unset keys fall straight through, so a card that has never been customised
    gets the base palette unchanged. When set, ``bg`` replaces the card fill and
    ``fg`` replaces the text - and the dim and faint steps are re-derived by
    fading the chosen text toward the chosen background, so "secondary" and
    "tertiary" stay proportional instead of reverting to the theme's greys.
    """
    bg = config.get("bg")
    fg = config.get("fg")
    if not bg and not fg:
        return base

    fields = {}
    ref_bg = bg or base.card
    if bg:
        fields["card"] = bg
        fields["card_edge"] = _mix(bg, "#ffffff", 0.10)
        # Keep the light/dark flag honest so the base class's sheen and default
        # alphas still make sense on a custom colour.
        fields["dark"] = _lum(bg) < 0.5

    # If a background is chosen but no text colour, pick one that reads on it
    # rather than leaving the theme's greys - a light card with dark-theme text
    # is simply broken, headers and all. An explicit fg always wins.
    ink = fg or (contrast_text(bg) if bg else None)
    if ink:
        fields["text"] = ink
        fields["text_dim"] = _mix(ink, ref_bg, 0.38)
        fields["text_faint"] = _mix(ink, ref_bg, 0.62)
    return replace(base, **fields)


# --------------------------------------------------------------------------- #
#  Menu
# --------------------------------------------------------------------------- #
def add_menu(card, menu) -> None:
    """Append an Appearance submenu. Called from a tile's ``context_actions``."""
    from PySide6.QtGui import QAction

    sub = menu.addMenu("Appearance")

    fill_solid = QAction("Solid background", sub, checkable=True)
    fill_solid.setChecked(is_solid(card.config))
    fill_solid.toggled.connect(
        lambda on: card.set_appearance("fill", "solid" if on else "glass"))
    sub.addAction(fill_solid)
    sub.addSeparator()

    _swatch_menu(card, sub.addMenu("Background"), "bg", BG_SWATCHES)
    _swatch_menu(card, sub.addMenu("Text color"), "fg", FG_SWATCHES,
                 note=("Typed text stays readable automatically"
                       if card.kind == "notes" else ""))

    if getattr(card, "allow_image", False):
        sub.addSeparator()
        pick = QAction("Background image…", sub)
        pick.triggered.connect(lambda: _pick_image(card))
        sub.addAction(pick)
        if card.config.get("image"):
            adjust = QAction("Adjust background image…", sub)
            adjust.triggered.connect(lambda: _adjust_image(card))
            sub.addAction(adjust)
            clear = QAction("Clear background image", sub)
            clear.triggered.connect(lambda: card.set_appearance("image", None))
            sub.addAction(clear)

    sub.addSeparator()
    reset = QAction("Reset appearance", sub)
    reset.setEnabled(bool(card.config.get("bg") or card.config.get("fg")
                          or card.config.get("fill")
                          or card.config.get("image")))
    reset.triggered.connect(card.reset_appearance)
    sub.addAction(reset)


def _swatch_menu(card, sub, key: str, swatches, note: str = "") -> None:
    from PySide6.QtGui import QAction, QColor, QIcon, QPixmap

    if note:
        head = QAction(note, sub)
        head.setEnabled(False)
        sub.addAction(head)
        sub.addSeparator()

    current = card.config.get(key)

    default = QAction("Theme default", sub, checkable=True)
    default.setChecked(not current)
    default.triggered.connect(lambda: card.set_appearance(key, None))
    sub.addAction(default)
    sub.addSeparator()

    for name, hex_colour in swatches:
        act = QAction(name, sub, checkable=True)
        act.setChecked(bool(current) and current.lower() == hex_colour.lower())
        pm = QPixmap(16, 16)
        pm.fill(QColor(hex_colour))
        act.setIcon(QIcon(pm))
        act.triggered.connect(lambda _c=False, h=hex_colour: card.set_appearance(key, h))
        sub.addAction(act)

    sub.addSeparator()
    custom = QAction("Custom color…", sub)
    custom.triggered.connect(lambda: _pick_custom(card, key))
    sub.addAction(custom)


def _pick_custom(card, key: str) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    start = card.config.get(key) or (card.palette_.card if key == "bg"
                                     else card.palette_.text)
    col = QColorDialog.getColor(QColor(start), card,
                                "Background" if key == "bg" else "Text color")
    if col.isValid():
        card.set_appearance(key, col.name())


def _pick_image(card) -> None:
    """Choose a picture, then position it - the editor opens straight away,
    because a photo is almost never the card's shape."""
    from ..ui import cropper
    cropper.choose(card)


def _adjust_image(card) -> None:
    from ..ui import cropper
    cropper.edit(card)
