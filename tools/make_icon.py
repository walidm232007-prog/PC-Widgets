"""
Generate assets/widgets.ico from the same mark the tray icon draws.

Windows picks a different size out of an .ico depending on where it appears -
16px in the taskbar, 32px in the Start menu, 256px in large-icon Explorer views
- so all of them are rendered at their own size rather than downscaled from one
big one. A 16px tile drawn as 16px keeps its corners; the same tile scaled down
from 256px turns to mush.
"""

import io
import os
import sys

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from PySide6.QtCore import QBuffer, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
ACCENT = "#3b82f6"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "widgets.ico")


def render(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)

    u = size / 64.0                     # design the mark on a 64px grid
    gap = max(1.0, 4 * u)
    inset = max(1.0, 5 * u)
    tile = (size - inset * 2 - gap) / 2
    radius = max(1.0, tile * 0.30)      # continuous-ish corners, like the cards

    for i, (cx, cy) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
        x = inset + cx * (tile + gap)
        y = inset + cy * (tile + gap)
        if i in (0, 3):
            g = QLinearGradient(x, y, x + tile, y + tile)
            g.setColorAt(0.0, QColor(ACCENT).lighter(118))
            g.setColorAt(1.0, QColor(ACCENT))
            p.setBrush(g)
        else:
            p.setBrush(QColor(168, 180, 198, 235))
        p.drawRoundedRect(QRectF(x, y, tile, tile), radius, radius)
    p.end()
    return pm


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed to write a multi-size .ico")
        return 1

    frames = []
    for s in SIZES:
        buf = QBuffer()
        buf.open(QBuffer.ReadWrite)
        render(s).save(buf, "PNG")
        frames.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA"))

    frames[-1].save(OUT, format="ICO",
                    sizes=[(f.width, f.height) for f in frames])
    print(f"wrote {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB, "
          f"{len(SIZES)} sizes: {SIZES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
