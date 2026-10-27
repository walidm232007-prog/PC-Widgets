"""
previews.py
===========
Pictures of widgets for the app window: samples in the gallery, and thumbnails
of the cards actually on the desktop.

Both are the real thing rather than artwork - a tile painted offscreen - so the
gallery can never drift from what a widget really looks like, and a colour or
photo chosen a moment ago shows in the list immediately. The picture is just
the card and its shadow, on transparent: a frosted card brings its own frosting
- it paints the blurred wallpaper from the spot it would occupy - so it needs
nothing behind it, and the window's plain well shows round it.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from ..core import samples, winapi
from ..core.board import REGISTRY
from ..core.card import MARGIN, WidgetCard, card_size

# Shown in the gallery instead of the network-dependent defaults.
SAMPLE_CONFIG = {
    "weather": {"lat": 51.5072, "lon": -0.1276, "place": "London, GB"},
}


def compose(card: WidgetCard, w: int, h: int, pad: float = 14.0,
            max_scale: float = 1.0) -> QImage:
    """``card`` scaled to fit ``w`` x ``h``, centred, on transparent.

    Painted through ``QWidget.render`` so child widgets come too - the notes
    tile's text is a real editor sitting on top of the painted card.
    """
    img = QImage(max(1, w), max(1, h), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    cr = card.card_rect()
    scale = min(max_scale, (w - 2 * pad) / cr.width(), (h - 2 * pad) / cr.height())
    scale = max(0.05, scale)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.translate(w / 2.0, h / 2.0)
    p.scale(scale, scale)
    p.translate(-cr.center().x(), -cr.center().y())
    card.render(p, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    p.end()
    return img


def sample_spot(size: str) -> Tuple[int, int]:
    """Where a sample pretends to sit: the middle of the main screen, so the
    gallery shows every sample over the same stretch of wallpaper."""
    wl, wt, wr, wb = winapi.work_area()
    cw, ch = card_size(size)
    return (int((wl + wr - cw) / 2 - MARGIN), int((wt + wb - ch) / 2 - MARGIN))


def sample(kind: str, size: str, palette, glass=None) -> Optional[WidgetCard]:
    """A throwaway tile filled with sample data. Never shown, never started."""
    cls = REGISTRY.get(kind)
    if cls is None:
        return None
    card = cls(palette, size, dict(SAMPLE_CONFIG.get(kind, {})))
    card.glass = glass
    card.move(*sample_spot(card.size_class))
    samples.prepare(card)
    return card


_gallery: Dict[tuple, QImage] = {}


def gallery_image(kind: str, size: str, palette, glass, opacity: float,
                  glass_tint: int, w: int, h: int) -> QImage:
    """A sample tile, composed for the gallery. Cached until the look changes."""
    key = (kind, size, palette, glass is not None, opacity, glass_tint, w, h,
           getattr(glass, "_signature", None))
    hit = _gallery.get(key)
    if hit is not None:
        return hit
    card = sample(kind, size, palette, glass)
    if card is None:
        return QImage()
    card.opacity_ = opacity
    card.glass_tint = glass_tint
    try:
        img = compose(card, w, h)
    finally:
        card.close()
        card.deleteLater()
    if len(_gallery) > 160:
        _gallery.clear()
    _gallery[key] = img
    return img


def card_image(card: WidgetCard, w: int, h: int, pad: float = 10.0,
               max_scale: float = 1.0) -> QImage:
    """A card on the desktop, as it looks right now."""
    return compose(card, w, h, pad=pad, max_scale=max_scale)
