"""
tokens.py
=========
The design tokens (colours, spacing, radius, type scale) that everything else in
the design system is built from. No Qt imports here - this is pure data so it can
be reasoned about and tested in isolation.

A :class:`Palette` is computed from the user's choices (theme + accent + density
+ radius + glass), and the ThemeManager turns it into a Qt stylesheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

# --------------------------------------------------------------------------- #
#  Accent colours
# --------------------------------------------------------------------------- #
ACCENTS: Dict[str, str] = {
    "blue":   "#3b82f6",
    "purple": "#8b5cf6",
    "green":  "#22c55e",
    "orange": "#f97316",
    "red":    "#ef4444",
    "pink":   "#ec4899",
    "teal":   "#14b8a6",
}


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c1: str, c2: str, t: float) -> str:
    """Linear blend between two hex colours (t=0 -> c1, t=1 -> c2)."""
    a, b = _hex_to_rgb(c1), _hex_to_rgb(c2)
    r = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % r


def lighten(c: str, t: float) -> str:
    return _mix(c, "#ffffff", t)


def darken(c: str, t: float) -> str:
    return _mix(c, "#000000", t)


def with_alpha(c: str, a: float) -> str:
    """Return an 'rgba(r,g,b,a)' string for use in QSS."""
    r, g, b = _hex_to_rgb(c)
    return f"rgba({r}, {g}, {b}, {a:.3f})"


# --------------------------------------------------------------------------- #
#  Radius / spacing / type scales
# --------------------------------------------------------------------------- #
RADIUS = {"minimal": 6, "rounded": 12, "extra": 18}
DENSITY = {"comfortable": 1.0, "compact": 0.72}
FONT_SCALE = {"small": 0.92, "medium": 1.0, "large": 1.12}

# Glass surface opacity per mode (how see-through the cards/sidebar are).
GLASS_ALPHA = {
    "off":     1.00,
    "mica":    0.82,
    "acrylic": 0.66,
    "frosted": 0.55,
}


@dataclass
class Palette:
    mode: str            # "dark" | "light"
    accent: str
    accent_hover: str
    accent_press: str
    accent_soft: str     # translucent accent for selected rows
    bg: str
    surface: str         # cards / panels (may be rgba in glass mode)
    surface_solid: str   # always-opaque variant
    surface_alt: str
    surface_hover: str
    border: str
    border_strong: str
    text: str
    text_dim: str
    text_faint: str
    on_accent: str       # text colour that sits on the accent
    shadow_alpha: float
    glass: bool


def build_palette(
    theme: str = "dark",
    accent_name: str = "blue",
    custom_accent: str = "",
    glass_mode: str = "off",
    transparency: float = 0.0,
    high_contrast: bool = False,
) -> Palette:
    """Compute a full palette from the user's appearance settings."""
    accent = custom_accent if (accent_name == "custom" and custom_accent) else \
        ACCENTS.get(accent_name, ACCENTS["blue"])

    is_dark = theme == "dark"
    glass = glass_mode != "off"
    # Card opacity: blend the per-mode glass alpha with the user's transparency.
    base_alpha = GLASS_ALPHA.get(glass_mode, 1.0)
    alpha = base_alpha if not glass else max(0.30, base_alpha - transparency * 0.30)

    if is_dark:
        bg = "#0e1014"
        surface_solid = "#171a21"
        surface_alt = "#1d212a"
        surface_hover = "#232834"
        border = "#272c38"
        border_strong = "#333a48"
        text = "#eceef3" if not high_contrast else "#ffffff"
        text_dim = "#9aa1ad"
        text_faint = "#6b7280"
    else:
        bg = "#eef0f4"
        surface_solid = "#ffffff"
        surface_alt = "#f6f7f9"
        surface_hover = "#eceef2"
        border = "#e3e6eb"
        border_strong = "#d4d8df"
        text = "#1a1d23" if not high_contrast else "#000000"
        text_dim = "#586070"
        text_faint = "#98a0ab"

    surface = with_alpha(surface_solid, alpha) if glass else surface_solid

    return Palette(
        mode=theme,
        accent=accent,
        accent_hover=lighten(accent, 0.10) if is_dark else darken(accent, 0.06),
        accent_press=darken(accent, 0.10),
        accent_soft=with_alpha(accent, 0.16),
        bg=bg,
        surface=surface,
        surface_solid=surface_solid,
        surface_alt=surface_alt,
        surface_hover=surface_hover,
        border=border if not high_contrast else border_strong,
        border_strong=border_strong,
        text=text,
        text_dim=text_dim,
        text_faint=text_faint,
        on_accent="#ffffff",
        shadow_alpha=0.0 if glass else (0.45 if is_dark else 0.18),
        glass=glass,
    )
