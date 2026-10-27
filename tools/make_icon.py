"""
Generate assets/widgets.ico: the PC Widgets mark, four white tiles
(widgets/core/mark.py).

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
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QBuffer
from PySide6.QtWidgets import QApplication

from widgets.core import mark

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
OUT = os.path.join(ROOT, "assets", "widgets.ico")


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
        mark.pixmap(s).save(buf, "PNG")
        frames.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA"))

    frames[-1].save(OUT, format="ICO",
                    sizes=[(f.width, f.height) for f in frames])
    print(f"wrote {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB, "
          f"{len(SIZES)} sizes: {SIZES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
