"""
mark.py
=======
The PC Widgets mark: four rounded tiles, the usual sign for widgets.

One drawing for everywhere the mark appears - the generated .ico that the tray,
the taskbar and the shortcuts show (tools/make_icon.py), the tray's fallback
when that file is missing, and the app window's sidebar - so none of them can
drift from the others. It is a single flat colour: white, except where it would
sit on white, in the light theme's sidebar, which gives it the text colour.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

WHITE = "#ffffff"


def paint(p: QPainter, rect: QRectF, colour: str = WHITE) -> None:
    """The mark, filling the square ``rect``. Designed on a 64px grid, with the
    gaps and corners held to at least a pixel so a 16px icon stays four tiles."""
    size = min(rect.width(), rect.height())
    u = size / 64.0
    gap = max(1.0, 4 * u)
    inset = max(1.0, 5 * u)
    tile = (size - inset * 2 - gap) / 2
    radius = max(1.0, tile * 0.30)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(colour))
    for cx in (0, 1):
        for cy in (0, 1):
            p.drawRoundedRect(QRectF(rect.left() + inset + cx * (tile + gap),
                                     rect.top() + inset + cy * (tile + gap), tile, tile),
                              radius, radius)
    p.restore()


def pixmap(size: int, colour: str = WHITE) -> QPixmap:
    """The mark drawn at ``size`` px - drawn at that size, never scaled to it."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint(p, QRectF(0, 0, size, size), colour)
    p.end()
    return pm
