"""
magnet.py
=========
Where a dragged widget would like to land - macOS Sonoma's desktop-widget
alignment, without a fixed grid.

How the Mac does it, and so how this does it:

* There is no grid. Far from other widgets a card goes exactly where it's let
  go, to the pixel.
* Near another widget it is *pulled* into line with it: edges aligned, or set
  beside it with the standard gap between them. Near a screen edge it sits a
  fixed inset in from that edge, and in a corner, in from both.
* The card itself keeps following the cursor the whole time - nothing jumps
  under the mouse. What snaps is an outline of where it will go (card.py draws
  it), and the card glides into that outline when it's dropped.
* Each axis is pulled on its own, so a card can line up its top with one
  neighbour and sit beside another.
* A spot that would put the card on top of another widget is never suggested.
* Once a target is showing it holds on a little longer than it took to catch
  (STICKY), so the outline doesn't flicker between two spots as the mouse
  wobbles near a boundary.

Pure arithmetic on ``(x, y, w, h)`` tuples in screen pixels, so it can be tested
without Qt or Windows (tools/magnet_selftest.py).
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]            # x, y, w, h
Point = Tuple[int, int]

# How close, in pixels, an edge has to come before it is pulled into line.
REACH = 26
# A held target lets go only this much further away than REACH.
STICKY = 1.6
# How far in from the edge of the work area a card settles. Matches the gap
# between cards, so a column against the screen edge looks evenly spaced.
EDGE_INSET = 12


def _overlaps(a: Rect, b: Rect, gap: int) -> bool:
    """True if ``a`` comes closer than ``gap`` to ``b``. Touching at exactly
    ``gap`` apart - the spacing the magnet itself produces - is fine."""
    slack = max(0, gap - 1)
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax < bx + bw + slack and bx < ax + aw + slack and
            ay < by + bh + slack and by < ay + ah + slack)


def _near(lo1: int, len1: int, lo2: int, len2: int, reach: int) -> bool:
    """Whether two 1-D spans overlap once each is widened by ``reach``."""
    return lo1 < lo2 + len2 + reach and lo2 < lo1 + len1 + reach


def _axis_candidates(pos: int, size: int, cross_pos: int, cross_size: int,
                     others: Sequence[Tuple[int, int, int, int]],
                     lo: int, hi: int, gap: int, reach: int,
                     inset: int, close: int) -> List[int]:
    """Every place along one axis this card could be pulled to.

    ``others`` are given as (pos, size, cross_pos, cross_size) on this axis.
    """
    out: List[int] = []
    for opos, osize, ocross, ocsize in others:
        # Only a widget the card is genuinely close to counts: within
        # ``close`` of it on both axes. Further off - even lined up in the
        # same column across the screen - nothing is suggested and no outline
        # appears; the card goes where it's dropped.
        if not _near(cross_pos, cross_size, ocross, ocsize, close):
            continue
        if not _near(pos, size, opos, osize, close):
            continue
        beside = _near(cross_pos, cross_size, ocross, ocsize, 0)
        out += [opos, opos + osize - size]                  # align the edges
        if beside:
            out += [opos + osize + gap, opos - gap - size]  # set beside it
        else:
            # Stacked above or below: also align centres, which is how a
            # small card sits under the middle of a wide one.
            out.append(opos + (osize - size) // 2)
    out += [lo + inset, hi - inset - size]                  # the screen edges
    return out


def _ranked(pos: int, cands: Iterable[int], reach: int,
            held: Optional[int]) -> List[Tuple[float, int]]:
    """The candidates in reach, nearest first, as (cost, value). The one
    already held stays in the running out to REACH * STICKY and is ranked as
    if it were where the card is now."""
    seen = {}
    for c in cands:
        d = abs(c - pos)
        if c == held and d <= reach * STICKY:
            d = 0.0
        elif d > reach:
            continue
        if c not in seen or d < seen[c]:
            seen[c] = d
    return sorted((d, c) for c, d in seen.items())


def suggest(moving: Rect, others: Sequence[Rect], work: Rect, gap: int,
            reach: int = REACH, inset: int = EDGE_INSET,
            held: Optional[Point] = None) -> Optional[Point]:
    """Where a card at ``moving`` would be pulled to, or None to leave it be.

    ``others`` are the other cards on the same display and ``work`` is that
    display's work area (left, top, right, bottom). ``held`` is the target
    currently showing, if any, which gets the benefit of the doubt.
    """
    x, y, w, h = moving
    wl, wt, wr, wb = work

    # One gap and REACH away from a neighbour; a little further while an
    # outline is already showing, so it holds through a wobble.
    close = int(gap + reach * (STICKY if held is not None else 1.0))
    xs = _axis_candidates(x, w, y, h, [(ox, ow, oy, oh) for ox, oy, ow, oh in others],
                          wl, wr, gap, reach, inset, close)
    ys = _axis_candidates(y, h, x, w, [(oy, oh, ox, ow) for ox, oy, ow, oh in others],
                          wt, wb, gap, reach, inset, close)

    hx, hy = held if held is not None else (None, None)
    rx = _ranked(x, xs, reach, hx)
    ry = _ranked(y, ys, reach, hy)
    if not rx and not ry:
        return None

    # Pulled on both axes where that's clear of every other card, else on just
    # one - always the arrangement that moves the card least.
    tries = [(dx + dy, (cx, cy)) for dx, cx in rx for dy, cy in ry]
    tries.sort()
    tries = [p for _d, p in tries]
    singles = sorted([(d, (c, y)) for d, c in rx] + [(d, (x, c)) for d, c in ry])
    tries += [p for _d, p in singles]

    for tx, ty in tries:
        if not any(_overlaps((tx, ty, w, h), o, gap) for o in others):
            return (tx, ty)
    return None
