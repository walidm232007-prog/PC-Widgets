"""
previews.py
===========
Pictures of widgets for the app window: samples in the gallery, and thumbnails
of the cards actually on the desktop.

Both are the real thing rather than artwork - a tile painted offscreen - so the
gallery can never drift from what a widget really looks like, and a colour or
photo chosen a moment ago shows in the list immediately. Each is painted over
the blurred wallpaper at the spot it would occupy, so frosted glass previews as
frosted glass instead of as a grey box.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from ..core import samples, winapi
from ..core.board import REGISTRY
from ..core.card import MARGIN, WidgetCard, card_size

# Shown in the gallery instead of the network-dependent defaults.
SAMPLE_CONFIG = {
    "weather": {"lat": 51.5072, "lon": -0.1276, "place": "London, GB"},
}


def compose(card: WidgetCard, w: int, h: int, glass=None,
            pad: float = 14.0, max_scale: float = 1.0) -> QImage:
    """``card`` painted over the desktop behind it, scaled to fit ``w`` x ``h``.

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

    # The desktop around the card, in the card's own coordinates. A neutral
    # wash goes down first: a card at the screen's edge has no wallpaper past
    # it, and that part of the picture would otherwise be a blank strip.
    area = QRectF(cr.center().x() - w / 2.0 / scale, cr.center().y() - h / 2.0 / scale,
                  w / scale, h / scale)
    grad = QLinearGradient(area.topLeft(), area.bottomRight())
    grad.setColorAt(0.0, QColor("#3a4150"))
    grad.setColorAt(1.0, QColor("#1c2029"))
    p.fillRect(area, grad)
    if glass is not None:
        glass.paint_into(p, area, card.x() + area.left(), card.y() + area.top())

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
        img = compose(card, w, h, glass)
    finally:
        card.close()
        card.deleteLater()
    if len(_gallery) > 160:
        _gallery.clear()
    _gallery[key] = img
    return img


def card_image(card: WidgetCard, w: int, h: int, pad: float = 10.0,
               max_scale: float = 1.0) -> QImage:
    """A card on the desktop, as it looks right now, at its real spot."""
    return compose(card, w, h, card.glass, pad=pad, max_scale=max_scale)
