"""
lens.py
=======
Liquid glass: the wallpaper seen *through* a pane, bent along its rim.

Frosted glass is a blur of whatever is behind a card. Liquid glass, as Apple
ships it in iOS 26 and macOS Tahoe, is clearer than that and reads as a thick
pane instead. Near the edge it shows what lies a little further in, the way a
curved rim would, red bending a touch further than blue, and a thin line of
light runs round the rim.

The maths is Glisse's (Kelvin Zhang, MIT, github.com/0kzh/glisse), which does
this for the web with an SVG displacement map over the live page:

- A band along the rim bends, a quarter of the pane's shorter side wide.
- How far each point looks inward is the slope of a shallow dome there, so the
  pull is nothing in the middle and strongest at the edge, smoothed over the
  band's inner edge by an error function. Each axis has its own dome, so the
  pull near a side points across it and near a corner points into it.
- Red is pulled further than blue, green between: 1.1x and 1.05x at the 0.5
  dispersion used here.
- The rim's light is white, brightest at the very edge and gone 1.22 px in, on
  every side but fading out over the two corners away from a light at the top
  left, over a 0.5 px edge line.

A browser has to redo that every frame, on the GPU. A desktop widget doesn't:
what is behind it is the wallpaper, which glass.py already composites, and that
only changes when the card moves or the wallpaper does. So each card bends its
slice once, here, in numpy, and keeps it; a card sitting still costs nothing.

Three changes for widget-sized glass. Glisse scales everything with the pane,
which suits a 44 px switch, but on a 348 px card that is an 87 px band with a
78 px reach - a fishbowl. The band is held to 26 px and the reach to 70 px, so
the rim looks the same on every size. The bend is worked out on a 2 px grid -
the clear copy of the desktop is kept at half size anyway, so there is no more
detail to find - and drawn scaled up, a quarter of the work; a card being
dragged is bent on a 4 px grid, a quarter again (see card.DRAG_STEP). The lit
rim, the one sharp thing, is drawn at full size. And the rim follows card.squircle's continuous
corner exactly, measured to the curve itself, where CSS gets a circle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Tuple

import numpy as np
from PySide6.QtGui import QImage

# --------------------------------------------------------------------------- #
#  How the glass bends (Glisse's Glass, with the band and reach held)
# --------------------------------------------------------------------------- #
BEND = 0.45          # how far in the rim looks, as a share of the short side
DEPTH = 0.25         # the band that bends, as a share of the short side...
DEPTH_MAX = 26.0     # ...but never wider than this, in px
REACH_MAX = 70.0     # and never reaching further than this
DOME = 0.5           # the dome's sag, as a share of the short side
DISPERSION = 0.5     # red bends (1 + 0.2 x this) as far as blue

STILL = 0.2          # px: a pull smaller than this is drawn as no pull at all
STEP = 2             # px: the grid the bend is worked out on, then scaled up

# Red, green and blue, each scaling the same pull.
SPLIT = (1.0 + 0.2 * DISPERSION, 1.0 + 0.1 * DISPERSION, 1.0)

# --------------------------------------------------------------------------- #
#  The rim's light (Glisse's rimMap, measured from iOS 26.1)
# --------------------------------------------------------------------------- #
RIM_WIDTH = 1.22     # px: brightest at the edge, gone this far in
EDGE_WIDTH = 0.5     # px: the line under the light
SHINE_DARK = 0.28    # Glisse's 0.13 is iOS over pure black; a widget sits on
SHINE_LIGHT = 0.5    # a picture, where that line all but vanished
EDGE_DARK = (255, 255, 255, 0.08)
EDGE_LIGHT = (0, 0, 0, 0.08)

_SQRT2 = math.sqrt(2.0)


# --------------------------------------------------------------------------- #
#  Shapes
# --------------------------------------------------------------------------- #
def rrect_sdf(x, y, hw: float, hh: float, r: float):
    """Signed distance to a rounded rectangle centred on the origin; negative inside."""
    qx = np.abs(x) - hw + r
    qy = np.abs(y) - hh + r
    return (np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
            + np.minimum(np.maximum(qx, qy), 0.0) - r)


def squircle_outline(w: float, h: float, radius: float, n: int = 24) -> np.ndarray:
    """card.squircle's outline, centred on the origin, clockwise, as a closed
    polyline of (x, y) points: four straight runs and four cubic corners."""
    r = min(radius, min(w, h) / 2.0)
    a = min(r * 1.5286, min(w, h) / 2.0)
    c = a * 0.45
    x1, y1, x2, y2 = -w / 2.0, -h / 2.0, w / 2.0, h / 2.0
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]

    def corner(p0, p1, p2, p3):
        p0, p1, p2, p3 = (np.asarray(v, dtype=np.float64) for v in (p0, p1, p2, p3))
        u = 1.0 - t
        return u ** 3 * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t ** 3 * p3

    # The straight runs are split at their middles, so every segment lies in
    # one quadrant (see rim).
    return np.concatenate([
        [[0.0, y1], [x2 - a, y1]],
        corner((x2 - a, y1), (x2 - c, y1), (x2, y1 + c), (x2, y1 + a)),
        [[x2, 0.0], [x2, y2 - a]],
        corner((x2, y2 - a), (x2, y2 - c), (x2 - c, y2), (x2 - a, y2)),
        [[0.0, y2], [x1 + a, y2]],
        corner((x1 + a, y2), (x1 + c, y2), (x1, y2 - c), (x1, y2 - a)),
        [[x1, 0.0], [x1, y1 + a]],
        corner((x1, y1 + a), (x1, y1 + c), (x1 + c, y1), (x1 + a, y1)),
    ])


# --------------------------------------------------------------------------- #
#  The bend
# --------------------------------------------------------------------------- #
def _arc(sag: float, half: float) -> Tuple[float, float]:
    """The circle that sags by ``sag`` across ``half``, and the scale that makes
    its slope average one half over that span: (radius, scale)."""
    radius = (half * half + sag * sag) / (2.0 * sag)
    xs = np.linspace(0.0, half, 201)
    slope = xs / np.sqrt(np.maximum(radius * radius - xs * xs, 1e-12))
    mean = (slope.sum() - 0.5 * (slope[0] + slope[-1])) / 200.0
    return radius, (0.5 / mean if mean > 0 else 1.0)


def _slope(a, radius: float, scale: float):
    a = np.minimum(a, 0.999 * radius)
    return a / np.sqrt(radius * radius - a * a) * scale


@dataclass(frozen=True)
class Field:
    """Where each point of a pane looks, for one size and corner, on a grid of
    ``step`` px: point (i, j) stands for the block whose top-left pixel is
    (i * step, j * step)."""
    w: int               # the pane, px
    h: int
    step: int
    cols: int            # the grid
    rows: int
    ox: np.ndarray       # (rows, cols) float32: how far across each point looks, px
    oy: np.ndarray       # (rows, cols) float32: how far down
    bent_rows: np.ndarray   # the points that bend at all: their row...
    bent_cols: np.ndarray   # ...and column
    bent_flat: np.ndarray   # ...and index in the grid, row by row
    bent_x: np.ndarray   # their middles, px from the pane's left...
    bent_y: np.ndarray   # ...and top
    pull_x: np.ndarray   # their pulls, flattened
    pull_y: np.ndarray
    reach: float         # the furthest any colour looks, px


@lru_cache(maxsize=48)
def field(w: int, h: int, radius: float, step: int = STEP) -> Field:
    """The bend for a pane ``w`` x ``h`` with ``radius`` corners. Cached: a board
    has a handful of sizes and each is worked out once."""
    w, h, step = max(1, int(w)), max(1, int(h)), max(1, int(step))
    cols, rows = -(-w // step), -(-h // step)
    hw, hh = w / 2.0, h / 2.0
    r = min(max(radius, 0.0), min(hw, hh))
    # Each point at the middle of its block.
    x = ((np.arange(cols) * step + step / 2.0) - hw)[None, :]
    y = ((np.arange(rows) * step + step / 2.0) - hh)[:, None]
    share = min(w, h)
    depth = min(DEPTH * share, DEPTH_MAX)
    iw, ih = max(0.0, hw - depth), max(0.0, hh - depth)
    ir = min(r, min(iw, ih))
    ramp = 1.0 / (depth * _SQRT2) if depth > 0 else 1e6
    sag = max(0.01, min(DOME * share, min(hw, hh) - 1.0))
    across = _arc(sag, hw)
    down = _arc(sag, hh)
    dx = np.sign(x) * _slope(np.abs(x), *across)
    dy = np.sign(y) * _slope(np.abs(y), *down)
    # erf(z) ~ tanh(sqrt(pi) z), as Glisse has it.
    falloff = 0.5 * (1.0 + np.tanh(1.7724538509 * rrect_sdf(x, y, iw, ih, ir) * ramp))
    inside = rrect_sdf(x, y, hw, hh, r) < 0
    reach = min(BEND * share, REACH_MAX)
    # A pixel looks *inward*, by half the reach at most: on the right of the
    # pane it shows what is to its left.
    k = -0.5 * reach
    ox = (k * np.clip(dx * falloff, -1.0, 1.0) * inside).astype(np.float32)
    oy = (k * np.clip(dy * falloff, -1.0, 1.0) * inside).astype(np.float32)
    moving = (np.abs(ox) * SPLIT[0] > STILL) | (np.abs(oy) * SPLIT[0] > STILL)
    br, bc = np.nonzero(moving)
    return Field(w, h, step, cols, rows, ox, oy, br.astype(np.int32), bc.astype(np.int32),
                 (br * cols + bc).astype(np.int64),
                 (bc * step + step / 2.0).astype(np.float32),
                 (br * step + step / 2.0).astype(np.float32),
                 ox[br, bc], oy[br, bc],
                 float(max(np.abs(ox).max(initial=0.0), np.abs(oy).max(initial=0.0)) * SPLIT[0]))


# Each colour's share of a pull, in a column to scale a row of pulls by.
_SPLIT = np.array(SPLIT, np.float32)[:, None]


def _straight(planes: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """All three ``planes`` resampled at the grid gx (columns) by gy (rows),
    bilinear, with no bend: separable, so two passes over a small crop.
    Returns (3, rows, cols) float32."""
    _c, H, W = planes.shape
    gx = np.clip(gx, 0.0, W - 1.001)
    gy = np.clip(gy, 0.0, H - 1.001)
    x0 = gx.astype(np.int32)
    y0 = gy.astype(np.int32)
    fx = gx - x0
    fy = gy - y0
    c0, c1 = int(x0.min()), int(x0.max()) + 2
    r0, r1 = int(y0.min()), int(y0.max()) + 2
    crop = planes[:, r0:r1, c0:c1].astype(np.float32)
    xi = x0 - c0
    yi = y0 - r0
    left, right = crop[:, :, xi], crop[:, :, xi + 1]
    across = left + (right - left) * fx
    top, bottom = across[:, yi, :], across[:, yi + 1, :]
    return top + (bottom - top) * fy[:, None]


def _sample(planes: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """``planes`` (C-contiguous, (3, H, W)) at scattered points, bilinear: row c
    of ``x`` and ``y`` is where colour c looks. All three at once, with flat
    ``take``s - a quarter as many numpy calls as a pass per colour, and 2-D
    fancy indexing costs about twice as much again."""
    C, H, W = planes.shape
    x = np.clip(x, 0.0, W - 1.001)
    y = np.clip(y, 0.0, H - 1.001)
    x0 = x.astype(np.int32)
    y0 = y.astype(np.int32)
    fx = x - x0
    fy = y - y0
    flat = planes.reshape(-1)
    at = y0 * W + x0 + (np.arange(C, dtype=np.int32) * (H * W))[:, None]
    a = flat.take(at).astype(np.float32)
    b = flat.take(at + 1).astype(np.float32)
    c = flat.take(at + W).astype(np.float32)
    d = flat.take(at + W + 1).astype(np.float32)
    top = a + (b - a) * fx
    return top + (c + (d - c) * fx - top) * fy


def bend(planes: np.ndarray, scale: int, origin: Tuple[int, int],
         sx: int, sy: int, w: int, h: int, radius: float, step: int = STEP) -> QImage:
    """The pane ``w`` x ``h`` at ``sx, sy`` on the virtual screen, looking
    through its rim at ``planes``: the clear desktop as (3, H, W) uint8 red,
    green and blue, kept at 1/``scale`` size with its top-left at ``origin``.

    Returns an opaque image of the pane on a ``step`` px grid - draw it scaled
    to ``w`` x ``h`` - corners and all; the caller clips it. A coarser step is
    for a pane on the move, where the detail it loses can't be seen."""
    f = field(w, h, radius, step)
    ex, ey = sx - origin[0], sy - origin[1]
    # Each point's place, in the copy's pixels.
    gx = (np.arange(f.cols, dtype=np.float32) * f.step + f.step / 2.0 + ex) / scale - 0.5
    gy = (np.arange(f.rows, dtype=np.float32) * f.step + f.step / 2.0 + ey) / scale - 0.5
    value = _straight(planes, gx, gy)
    if f.bent_flat.size:
        look = _SPLIT / scale
        bx = (f.bent_x + ex) / scale - 0.5
        by = (f.bent_y + ey) / scale - 0.5
        value.reshape(3, -1)[:, f.bent_flat] = _sample(
            planes, bx[None, :] + f.pull_x[None, :] * look, by[None, :] + f.pull_y[None, :] * look)
    rgb = np.clip(value + 0.5, 0, 255).astype(np.uint8)
    # Format_RGB32 is 0xffRRGGBB, so blue, green, red in memory.
    out = np.empty((f.rows, f.cols, 4), np.uint8)
    out[..., 0] = rgb[2]
    out[..., 1] = rgb[1]
    out[..., 2] = rgb[0]
    out[..., 3] = 255
    return QImage(out.data, f.cols, f.rows, f.cols * 4, QImage.Format_RGB32).copy()


# --------------------------------------------------------------------------- #
#  The rim
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=48)
def rim(w: int, h: int, radius: float, dark: bool) -> QImage:
    """The lit rim and edge line for a pane, premultiplied, the pane's size.
    Lies over the tint: on a light page the light lifts it towards white, on a
    dark one it reads as a glint."""
    w, h = max(1, int(w)), max(1, int(h))
    shine = SHINE_DARK if dark else SHINE_LIGHT
    er, eg, eb, ea = EDGE_DARK if dark else EDGE_LIGHT
    hw, hh = w / 2.0, h / 2.0
    X, Y = np.meshgrid((np.arange(w) + 0.5) - hw, (np.arange(h) + 0.5) - hh)
    # Only pixels near the outline matter; a circle-cornered stand-in finds
    # them. A continuous corner starts its curve 1.53 radii along each edge and
    # crosses the diagonal about where a circle of that radius would - a circle
    # of the card's own radius runs nearly 5 px outside it there.
    near = rrect_sdf(X, Y, hw, hh, min(radius * 1.5286, min(hw, hh)))
    band = (near > -5.0) & (near < 4.0)
    px, py = X[band], Y[band]

    # The distance to the outline itself, segment by segment - but only to the
    # segments in a pixel's own quadrant, which is where its nearest point is.
    start = squircle_outline(w, h, radius)
    end = np.roll(start, -1, axis=0)
    mid = (start + end) / 2.0
    depth = np.empty(len(px))
    nx = np.empty(len(px))
    ny = np.empty(len(px))
    for sgx in (-1.0, 1.0):
        for sgy in (-1.0, 1.0):
            mine = ((px >= 0) == (sgx > 0)) & ((py >= 0) == (sgy > 0))
            segs = ((mid[:, 0] >= 0) == (sgx > 0)) & ((mid[:, 1] >= 0) == (sgy > 0))
            if not mine.any() or not segs.any():
                continue
            a, ab = start[segs], (end - start)[segs]
            qx, qy = px[mine], py[mine]
            length2 = np.maximum((ab ** 2).sum(1), 1e-12)
            t = np.clip(((qx[:, None] - a[None, :, 0]) * ab[None, :, 0]
                         + (qy[:, None] - a[None, :, 1]) * ab[None, :, 1]) / length2[None, :],
                        0.0, 1.0)
            cx = a[None, :, 0] + t * ab[None, :, 0]
            cy = a[None, :, 1] + t * ab[None, :, 1]
            d2 = (qx[:, None] - cx) ** 2 + (qy[:, None] - cy) ** 2
            nearest = d2.argmin(1)
            each = np.arange(len(qx))
            seg = np.sqrt(length2[nearest])
            sx_, sy_ = ab[nearest, 1] / seg, -ab[nearest, 0] / seg     # outward
            outside = ((qx - cx[each, nearest]) * sx_ + (qy - cy[each, nearest]) * sy_) > 0
            dist = np.sqrt(d2[each, nearest])
            depth[mine] = np.where(outside, -dist, dist)              # px inside the edge
            nx[mine], ny[mine] = sx_, sy_

    half = 0.5
    # 1 along the sides, sqrt 2 on the two corners facing the light and its
    # reflection, 0 on the two between.
    facing = np.abs(nx + ny)
    along = np.maximum(0.0, depth) / (RIM_WIDTH * np.sqrt(np.maximum(1.0, facing)))
    cover = np.clip(depth + half, 0.0, 1.0)
    lined = np.clip(np.minimum(EDGE_WIDTH, depth + half) - np.maximum(0.0, depth - half), 0.0, 1.0)
    light = np.where(along < 1.0,
                     shine * np.minimum(1.0, facing) ** 1.5
                     * (1.0 - np.minimum(along, 1.0) ** 1.5) * cover, 0.0)
    light = np.where(depth < -half, 0.0, light)
    line = np.where(depth < -half, 0.0, ea * lined * (1.0 - light))
    alpha = np.clip(light + line, 0.0, 1.0)

    out = np.zeros((h, w, 4), np.float32)
    # Format_ARGB32_Premultiplied: blue, green, red, alpha in memory.
    out[band] = np.stack([255 * light + eb * line, 255 * light + eg * line,
                          255 * light + er * line, 255 * alpha], axis=1)
    out = np.ascontiguousarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))
    return QImage(out.data, w, h, w * 4, QImage.Format_ARGB32_Premultiplied).copy()
