"""
Generate the installer's pictures in packaging/wizard/: the PC Widgets mark in
the corner of the wizard's pages, the panel on its last page, and Setup's own
icon.

Inno Setup picks the picture closest to the space it has at the user's Windows
scale, so each one is drawn at every size its documentation lists for 100% to
250% (SIDE_SIZES, CORNER_SIZES) rather than one big picture shrunk to fit - the
same reason tools/make_icon.py draws every icon size itself.

- side-<width>.png: the last page's panel - the mark, then the name and the
  publisher (from widgets/version.py) in Inter. Transparent: the panel's colour is
  WizardImageBackColor in packaging/pc-widgets.iss, so one set serves the light
  page and the dark one.
- corner-<size>.png and corner-dark-<size>.png: the mark alone, in the text
  colour on the light page and white on the dark one - widgets/core/mark.py's
  own rule.
- setup.ico: the mark on a dark tile. The app's icon is the bare white mark,
  which disappears on a light title bar and in a light Explorer window; on a
  tile, Setup.exe stays visible on both.

    python tools/make_installer_art.py

Needs PySide6 only: the .ico is assembled here rather than with Pillow.
"""

import os
import runpy
import struct
import sys

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QBuffer, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter

from widgets.core import mark

OUT = os.path.join(ROOT, "packaging", "wizard")

# Inno Setup's image areas at 100%, 125% ... 250%, from its help pages for
# WizardImageFile and WizardSmallImageFile (Inno Setup 6.6 and later).
SIDE_SIZES = [(202, 386), (269, 515), (336, 643), (403, 772), (430, 824),
              (498, 953), (534, 1022)]
CORNER_SIZES = [58, 77, 97, 116, 124, 143, 159]
ICON_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

TEXT_LIGHT = "#1a1d23"   # the light theme's text colour (design_system/tokens.py)
WHITE = "#ffffff"
TILE = "#1f2430"         # Setup's icon tile: dark enough for the white mark


def canvas(width: int, height: int):
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    return img, p


def side(width: int, height: int, inter: str, publisher: str) -> QImage:
    """The last page's panel, laid out at 202 px wide and scaled from there."""
    s = width / 202.0
    img, p = canvas(width, height)
    size, gap, line = 56 * s, 14 * s, 20 * s
    top = height * 0.46 - (size + gap + line) / 2
    mark.paint(p, QRectF((width - size) / 2, top, size, size), WHITE)

    name = QFont(inter)
    name.setPixelSize(round(15 * s))
    name.setWeight(QFont.DemiBold)
    p.setFont(name)
    p.setPen(QColor(WHITE))
    p.drawText(QRectF(0, top + size + gap, width, line), Qt.AlignHCenter | Qt.AlignVCenter,
               "PC Widgets")

    small = QFont(inter)
    small.setPixelSize(round(11 * s))
    p.setFont(small)
    p.setPen(QColor(255, 255, 255, 168))   # white at 66%: 8:1 on the panel
    p.drawText(QRectF(0, height - 34 * s, width, 16 * s), Qt.AlignHCenter | Qt.AlignVCenter,
               publisher)
    p.end()
    return img


def corner(size: int, colour: str) -> QImage:
    """The mark alone, at about the size of the page's title block."""
    img, p = canvas(size, size)
    m = size * 0.72
    mark.paint(p, QRectF((size - m) / 2, (size - m) / 2, m, m), colour)
    p.end()
    return img


def tile(size: int) -> QImage:
    """Setup's icon: the mark on a dark rounded tile, a little roomier when tiny."""
    img, p = canvas(size, size)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(TILE))
    radius = size * 0.22
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    inner = size * (0.66 if size <= 24 else 0.6)
    mark.paint(p, QRectF((size - inner) / 2, (size - inner) / 2, inner, inner), WHITE)
    p.end()
    return img


def png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: str, images: list) -> None:
    """A multi-size .ico of PNG frames, which Windows has read since Vista."""
    frames = [(img.width(), png(img)) for img in images]
    head = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, data = b"", b""
    for size, blob in frames:
        dim = 0 if size >= 256 else size      # 0 means 256 in an icon entry
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob),
                               offset + len(data))
        data += blob
    with open(path, "wb") as fh:
        fh.write(head + entries + data)


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)
    meta = runpy.run_path(os.path.join(ROOT, "widgets", "version.py"))

    # Inter, the app's bundled font, for all the panel's text. Offscreen, Qt
    # sees no system fonts at all, so asking for any other name would quietly
    # draw in whatever is loaded - better to stop.
    fid = QFontDatabase.addApplicationFont(os.path.join(ROOT, "fonts", "InterVariable.ttf"))
    families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    if not families:
        print("fonts/InterVariable.ttf would not load")
        return 1
    inter = families[0]

    written = []
    for w, h in SIDE_SIZES:
        name = f"side-{w}.png"
        side(w, h, inter, meta["PUBLISHER"]).save(os.path.join(OUT, name), "PNG")
        written.append(name)
    for n in CORNER_SIZES:
        for prefix, colour in (("corner", TEXT_LIGHT), ("corner-dark", WHITE)):
            name = f"{prefix}-{n}.png"
            corner(n, colour).save(os.path.join(OUT, name), "PNG")
            written.append(name)
    write_ico(os.path.join(OUT, "setup.ico"), [tile(n) for n in ICON_SIZES])
    written.append("setup.ico")

    total = sum(os.path.getsize(os.path.join(OUT, n)) for n in written)
    print(f"wrote {len(written)} files to {OUT} ({total / 1024:.0f} KB), Inter as {inter!r}")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
