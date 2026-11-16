"""
Checks for widgets/core/magnet.py - the Mac-style alignment a dragged widget
snaps to. Pure arithmetic, so it runs anywhere: ``python tools/magnet_selftest.py``.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "widgets", "core"))
import magnet  # noqa: E402

GAP = 12
WORK = (0, 0, 1920, 1040)
SMALL = (168, 168)
MEDIUM = (348, 168)


def rect(x, y, size=SMALL):
    return (x, y, size[0], size[1])


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: got {got}, want {want}")
    return ok


def main() -> int:
    a = rect(600, 300)
    results = [
        # Far from everything: left exactly where it is.
        check("free placement", magnet.suggest(rect(1000, 600), [a], WORK, GAP), None),

        # Dropped a little to the right of a card, a bit low: pulled beside it,
        # one gap away, tops lined up.
        check("beside, tops aligned",
              magnet.suggest(rect(600 + 168 + 20, 309), [a], WORK, GAP),
              (600 + 168 + GAP, 300)),

        # Dropped under a card, a bit off to the side: stacked under it, left
        # edges lined up.
        check("stacked, lefts aligned",
              magnet.suggest(rect(611, 300 + 168 + 5), [a], WORK, GAP),
              (600, 300 + 168 + GAP)),

        # A small card under a medium one lines up its centre.
        check("centred under a wide card",
              magnet.suggest(rect(690, 300 + 168 + 15), [rect(600, 300, MEDIUM)], WORK, GAP),
              (600 + (348 - 168) // 2, 300 + 168 + GAP)),

        # Near a screen corner: an even inset from both edges.
        check("corner inset",
              magnet.suggest(rect(5, 20), [], WORK, GAP),
              (magnet.EDGE_INSET, magnet.EDGE_INSET)),

        # Never onto another card.
        check("never overlaps", magnet.suggest(rect(605, 305), [a], WORK, GAP), None),

        # A held target survives a small wobble past REACH...
        check("sticky hold",
              magnet.suggest(rect(600 + 168 + GAP + 30, 300), [a], WORK, GAP,
                             held=(600 + 168 + GAP, 300)),
              (600 + 168 + GAP, 300)),
        # ...but not a real move away.
        check("lets go",
              magnet.suggest(rect(600 + 168 + GAP + 60, 300), [a], WORK, GAP,
                             held=(600 + 168 + GAP, 300)),
              (600 + 168 + GAP + 60, 300)),

        # Between two cards in a row: slots in with an even gap either side.
        check("fills a gap in a row",
              magnet.suggest(rect(600 + 168 + 18, 302),
                             [a, rect(600 + 2 * (168 + GAP), 300)], WORK, GAP),
              (600 + 168 + GAP, 300)),
    ]
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
