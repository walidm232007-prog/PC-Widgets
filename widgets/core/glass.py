"""
glass.py
========
Apple-style frosted material: a blurred copy of what is behind the card, tinted
and clipped to the card's shape.

Windows offers Mica and Acrylic through DWM, and neither works here. Both are
applied to the whole *window*, and the window is deliberately larger than the
card so the continuous corners have room to antialias and the shadow has
somewhere to fall - so a DWM material paints the transparent gutter too and
every card ends up sitting in a grey block. DWM can round the window itself,
but only at its own ~8px radius, nowhere near the 28px the cards use.

So the blur is done here instead. That is possible because of where these
widgets live: parked on the desktop layer, the thing behind them *is* the
wallpaper. We composite the wallpaper across the virtual screen exactly as the
shell does, blur it once, and each card then paints the slice sitting behind it
- clipped to its own squircle, with any radius, perfectly antialiased.

The blur is a heavy downscale, a couple of box passes at that small size, and a
smooth upscale. At 1/14 scale a 3840x2160 desktop is a ~275x155 image, so the
whole thing costs a few milliseconds and is done once per wallpaper change.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from . import winapi

DOWNSCALE = 14           # bigger = blurrier and cheaper
BOX_PASSES = 3
BOX_RADIUS = 2


def _box_blur(img: QImage, radius: int, passes: int) -> QImage:
    """Separable box blur. Repeated passes approach a Gaussian."""
    try:
        import numpy as np
    except Exception:
        return img       # the downscale/upscale alone still reads as blurred

    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w < 3 or h < 3:
        return img

    ptr = img.bits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=w * h * 4).reshape(h, w, 4)
    buf = arr.astype(np.float32)

    k = radius * 2 + 1
    for _ in range(max(1, passes)):
        pad = np.pad(buf, ((0, 0), (radius, radius), (0, 0)), mode="edge")
        cs = np.cumsum(pad, axis=1)
        cs = np.concatenate([np.zeros((h, 1, 4), np.float32), cs], axis=1)
        buf = (cs[:, k:, :] - cs[:, :-k, :]) / k

        pad = np.pad(buf, ((radius, radius), (0, 0), (0, 0)), mode="edge")
        cs = np.cumsum(pad, axis=0)
        cs = np.concatenate([np.zeros((1, w, 4), np.float32), cs], axis=0)
        buf = (cs[k:, :, :] - cs[:-k, :, :]) / k

    out = np.ascontiguousarray(np.clip(buf, 0, 255).astype(np.uint8))
    # Keep a reference to the buffer on the QImage or it is freed underneath it.
    res = QImage(out.data, w, h, QImage.Format_RGBA8888).copy()
    return res


class GlassBackdrop:
    """The blurred desktop, in virtual-screen coordinates."""

    def __init__(self) -> None:
        self.image: Optional[QImage] = None
        self.origin: Tuple[int, int] = (0, 0)
        self.tint: QColor = QColor(30, 34, 42)
        self._signature: Optional[Tuple] = None

    # ------------------------------------------------------------------ #
    def _current_signature(self) -> Tuple:
        path = winapi.wallpaper_path()
        try:
            stamp = os.path.getmtime(path) if path else 0.0
        except OSError:
            stamp = 0.0
        return (path, stamp, winapi.virtual_screen(), tuple(winapi.monitors()))

    def refresh(self, force: bool = False) -> bool:
        """Rebuild if the wallpaper or the display layout changed."""
        sig = self._current_signature()
        if not force and sig == self._signature and self.image is not None:
            return False
        self._signature = sig

        vl, vt, vr, vb = winapi.virtual_screen()
        vw, vh = max(1, vr - vl), max(1, vb - vt)
        self.origin = (vl, vt)

        r, g, b = winapi.desktop_colour()
        self.tint = QColor(r, g, b)

        canvas = QImage(vw, vh, QImage.Format_RGBA8888)
        canvas.fill(QColor(r, g, b))

        path = winapi.wallpaper_path()
        source = QImage(path) if path else QImage()
        if not source.isNull():
            p = QPainter(canvas)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            # Scale-to-fill per monitor, the way the shell's "Fill" mode does,
            # so the blur lines up with what is actually on screen.
            for ml, mt, mr, mb in winapi.monitors():
                mw, mh = mr - ml, mb - mt
                scaled = source.scaled(mw, mh, Qt.KeepAspectRatioByExpanding,
                                       Qt.SmoothTransformation)
                sx = max(0, (scaled.width() - mw) // 2)
                sy = max(0, (scaled.height() - mh) // 2)
                p.drawImage(QRect(ml - vl, mt - vt, mw, mh), scaled,
                            QRect(sx, sy, mw, mh))
            p.end()

        small = canvas.scaled(max(1, vw // DOWNSCALE), max(1, vh // DOWNSCALE),
                              Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        small = _box_blur(small, BOX_RADIUS, BOX_PASSES)
        self.image = small.scaled(vw, vh, Qt.IgnoreAspectRatio,
                                  Qt.SmoothTransformation)
        return True

    # ------------------------------------------------------------------ #
    def paint_into(self, p: QPainter, dest, screen_x: int, screen_y: int) -> bool:
        """Paint the slice of blurred desktop sitting behind this card.

        ``dest`` is in widget coordinates; ``screen_x/y`` is where that rect
        lives on the virtual screen. Returns False if there is nothing to draw
        and the caller should fall back to a flat fill.
        """
        if self.image is None:
            return False
        ox, oy = self.origin
        src = QRect(int(screen_x - ox), int(screen_y - oy),
                    int(dest.width()), int(dest.height()))
        if not src.intersects(self.image.rect()):
            return False
        p.drawImage(dest, self.image, src)
        return True
