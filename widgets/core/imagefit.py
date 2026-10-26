"""
imagefit.py
===========
Where a background picture sits inside its card.

A picture is described by the point that sits at the card's centre and how far
it is zoomed - not by a rectangle cut out of it. A rectangle only fits one
shape: switch a medium card to large and a crop saved as "this 2:1 strip"
either distorts or has to be thrown away. A centre and a zoom carry over to any
shape, which matters here because the same card can be small, medium or large,
and Files can be dragged to any size at all.

Zoom is relative to *cover*: 1.0 is the smallest size at which the picture
still fills the card, which is exactly what every card did before cropping
existed - so a card with no crop saved looks precisely as it always has. Below
1.0 the whole picture fits inside the card and the card's own background shows
around it; above 1.0 it is enlarged.

The crop editor and the card both place the picture through :func:`place`, so
what the editor shows is, to the pixel, what the card paints.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import NamedTuple, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader

MAX_ZOOM = 8.0
LONG_SIDE = 2400          # decoded pictures are capped here; see load()
_KEEP = 3                 # decoded pictures held for reuse


@dataclass(frozen=True)
class Crop:
    """Where the picture sits: the point of it at the card's centre, and zoom."""

    x: float = 0.5        # fraction of the picture's width
    y: float = 0.5        # fraction of its height
    zoom: float = 1.0     # 1.0 = just covers the card

    @property
    def is_default(self) -> bool:
        return (abs(self.x - 0.5) < 1e-4 and abs(self.y - 0.5) < 1e-4
                and abs(self.zoom - 1.0) < 1e-4)

    def key(self) -> tuple:
        """Hashable and stable, for caches."""
        return (round(self.x, 5), round(self.y, 5), round(self.zoom, 5))


def from_config(config: dict) -> Crop:
    """The card's saved crop. Anything malformed reads as the default."""
    raw = config.get("crop")
    if not isinstance(raw, dict):
        return Crop()
    try:
        crop = Crop(float(raw.get("x", 0.5)), float(raw.get("y", 0.5)),
                    float(raw.get("zoom", 1.0)))
    except (TypeError, ValueError):
        return Crop()
    if not all(math.isfinite(v) for v in (crop.x, crop.y, crop.zoom)) \
            or crop.zoom <= 0:
        return Crop()
    return crop


def to_config(crop: Crop) -> Optional[dict]:
    """The value to save, or None for the default so an untouched card has no key."""
    if crop.is_default:
        return None
    return {"x": round(crop.x, 4), "y": round(crop.y, 4),
            "zoom": round(crop.zoom, 4)}


# --------------------------------------------------------------------------- #
#  Geometry - all in card pixels, with the card's top-left at (0, 0)
# --------------------------------------------------------------------------- #
def cover_scale(iw: float, ih: float, cw: float, ch: float) -> float:
    return max(cw / iw, ch / ih)


def zoom_range(iw: float, ih: float, cw: float, ch: float) -> Tuple[float, float]:
    """Smallest zoom shows the whole picture; the largest is :data:`MAX_ZOOM`."""
    fit = min(cw / iw, ch / ih)
    return (min(1.0, fit / cover_scale(iw, ih, cw, ch)), MAX_ZOOM)


def _clamp_axis(offset: float, size: float, span: float) -> float:
    """Keep a picture ``size`` long covering a card ``span`` long on one axis -
    or, when the picture is the shorter of the two, inside it."""
    lo, hi = min(0.0, span - size), max(0.0, span - size)
    return max(lo, min(hi, offset))


def place(iw: float, ih: float, cw: float, ch: float,
          crop: Crop) -> Tuple[float, float, float, float]:
    """(left, top, width, height) of the whole picture, in card coordinates.

    Clamped, so a crop saved for another card shape - or edited by hand - can
    never leave a gap at the edge of a card the picture is meant to fill.
    """
    if min(iw, ih, cw, ch) <= 0:
        return (0.0, 0.0, float(cw), float(ch))
    lo, hi = zoom_range(iw, ih, cw, ch)
    s = cover_scale(iw, ih, cw, ch) * max(lo, min(hi, crop.zoom))
    w, h = iw * s, ih * s
    left = _clamp_axis(cw / 2.0 - crop.x * w, w, cw)
    top = _clamp_axis(ch / 2.0 - crop.y * h, h, ch)
    return (left, top, w, h)


def covers(iw: float, ih: float, cw: float, ch: float, crop: Crop) -> bool:
    """Does the picture fill the whole card, leaving no background showing?"""
    left, top, w, h = place(iw, ih, cw, ch, crop)
    eps = 0.5
    return (left <= eps and top <= eps
            and left + w >= cw - eps and top + h >= ch - eps)


def _from_placement(iw, ih, cw, ch, left, top, w) -> Crop:
    h = w * ih / iw
    return Crop((cw / 2.0 - left) / w, (ch / 2.0 - top) / h,
                w / (iw * cover_scale(iw, ih, cw, ch)))


def normalise(iw: float, ih: float, cw: float, ch: float, crop: Crop) -> Crop:
    """The crop exactly as the card would show it: zoom in range, picture in
    bounds. What the editor saves, so the file never holds an unreachable value."""
    left, top, w, _h = place(iw, ih, cw, ch, crop)
    return _from_placement(iw, ih, cw, ch, left, top, w)


def moved(iw: float, ih: float, cw: float, ch: float, crop: Crop,
          dx: float, dy: float) -> Crop:
    """The crop after dragging the picture ``(dx, dy)`` card pixels."""
    left, top, w, _h = place(iw, ih, cw, ch, crop)
    probe = _from_placement(iw, ih, cw, ch, left + dx, top + dy, w)
    return normalise(iw, ih, cw, ch, probe)


def zoomed(iw: float, ih: float, cw: float, ch: float, crop: Crop,
           zoom: float, about: Optional[Tuple[float, float]] = None) -> Crop:
    """The crop at a new zoom, keeping the picture point under ``about`` still.

    ``about`` is in card coordinates and defaults to the centre - zooming toward
    the cursor is what makes a scroll wheel feel like it is pointing at
    something rather than pumping the whole picture in and out.
    """
    left, top, w, h = place(iw, ih, cw, ch, crop)
    ax, ay = about if about is not None else (cw / 2.0, ch / 2.0)
    u, v = (ax - left) / w, (ay - top) / h
    lo, hi = zoom_range(iw, ih, cw, ch)
    z = max(lo, min(hi, zoom))
    nw = iw * cover_scale(iw, ih, cw, ch) * z
    nh = nw * ih / iw
    probe = _from_placement(iw, ih, cw, ch, ax - u * nw, ay - v * nh, nw)
    return normalise(iw, ih, cw, ch, probe)


# --------------------------------------------------------------------------- #
#  Pixels
# --------------------------------------------------------------------------- #
class Piece(NamedTuple):
    """The part of a placed picture that is actually visible, ready to draw."""

    image: QImage
    target: Tuple[float, float, float, float]    # where ``image`` goes
    visible: Tuple[float, float, float, float]   # picture area inside the view


def render_region(img: QImage, placed: Tuple[float, float, float, float],
                  view: Tuple[float, float, float, float],
                  dev: float = 1.0) -> Optional[Piece]:
    """Cut out and scale only the part of a placed picture inside ``view``.

    ``placed`` is the whole picture's (left, top, width, height) and ``view``
    the visible window, both in the same coordinates. Only the visible part is
    scaled, so an 8x zoom costs no more than no zoom at all. ``dev`` is device
    pixels per unit, so a card drawn enlarged - in the crop editor - gets a
    sharp picture rather than a stretched one.

    The source is widened to whole pixels and the target widened to match, so
    the cut lands exactly where :func:`place` says rather than up to a pixel off.
    """
    left, top, w, h = placed
    if img.isNull() or w <= 0 or h <= 0:
        return None
    vx, vy, vw, vh = view
    x0, y0 = max(vx, left), max(vy, top)
    x1, y1 = min(vx + vw, left + w), min(vy + vh, top + h)
    if x1 - x0 < 0.5 or y1 - y0 < 0.5:
        return None
    s = w / img.width()
    sx0 = max(0, int(math.floor((x0 - left) / s)))
    sy0 = max(0, int(math.floor((y0 - top) / s)))
    sx1 = min(img.width(), int(math.ceil((x1 - left) / s)))
    sy1 = min(img.height(), int(math.ceil((y1 - top) / s)))
    if sx1 <= sx0 or sy1 <= sy0:
        return None
    tx, ty = left + sx0 * s, top + sy0 * s
    tw, th = (sx1 - sx0) * s, (sy1 - sy0) * s
    piece = img.copy(sx0, sy0, sx1 - sx0, sy1 - sy0)
    pw, ph = max(1, round(tw * dev)), max(1, round(th * dev))
    if (pw, ph) != (piece.width(), piece.height()):
        piece = piece.scaled(pw, ph, Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation)
    return Piece(piece, (tx, ty, tw, th), (x0, y0, x1 - x0, y1 - y0))


def render(img: QImage, cw: float, ch: float, crop: Crop,
           dev: float = 1.0) -> Optional[Piece]:
    """What a ``cw`` x ``ch`` card shows of ``img`` under ``crop``."""
    placed = place(img.width(), img.height(), cw, ch, crop)
    return render_region(img, placed, (0.0, 0.0, float(cw), float(ch)), dev)


_decoded: "OrderedDict[tuple, QImage]" = OrderedDict()


def load(path: str) -> Optional[QImage]:
    """Decode a picture the right way up, at no more than :data:`LONG_SIDE`.

    Two things a plain ``QImage(path)`` gets wrong. Phone photos are usually
    stored sideways with an EXIF note saying which way is up, and without
    auto-transform the card shows them on their side. And a 48-megapixel photo
    decoded at full size is ~190 MB of pixels to fill a card a few hundred wide,
    so it is decoded straight to a sensible size instead - JPEG can do that
    during the decode, far cheaper than scaling afterwards.

    The last few are kept, keyed by path and modification time, so dragging a
    picture around in the editor does not decode it again on every frame, and
    replacing the file on disk is still noticed.
    """
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    key = (path, stamp)
    hit = _decoded.get(key)
    if hit is not None:
        _decoded.move_to_end(key)
        return hit

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > LONG_SIDE:
        f = LONG_SIDE / max(size.width(), size.height())
        reader.setScaledSize(QSize(max(1, round(size.width() * f)),
                                   max(1, round(size.height() * f))))
    img = reader.read()
    if img.isNull():
        return None
    if img.hasAlphaChannel():
        img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)

    _decoded[key] = img
    while len(_decoded) > _KEEP:
        _decoded.popitem(last=False)
    return img
