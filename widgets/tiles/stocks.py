"""
Stocks, via Yahoo Finance's public chart endpoint.

Keyless, like the weather tile, so it works the moment it's added. The same
endpoint serves equities, indices (``^GSPC``) and crypto (``BTC-USD``), which
means one code path covers a watchlist of mixed things.

Fetching runs on a worker thread and the result comes back through a Qt signal
- a slow or dead market API must never freeze the board, and a repaint must
never be triggered off the GUI thread.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import QInputDialog

from ..core.card import WidgetCard
from ..core.net import tls_context

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
DEFAULT_SYMBOLS = ["AAPL", "MSFT", "^GSPC", "BTC-USD"]

# Yahoo returns 4xx to obviously-scripted clients.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


@dataclass
class Quote:
    symbol: str
    name: str = ""
    price: Optional[float] = None
    prev_close: Optional[float] = None
    currency: str = "USD"
    spark: List[float] = field(default_factory=list)
    error: bool = False

    @property
    def change(self) -> float:
        if self.price is None or not self.prev_close:
            return 0.0
        return self.price - self.prev_close

    @property
    def change_pct(self) -> float:
        if self.price is None or not self.prev_close:
            return 0.0
        return (self.price - self.prev_close) / self.prev_close * 100.0

    @property
    def up(self) -> bool:
        return self.change >= 0


def fetch_one(symbol: str) -> Quote:
    url = CHART.format(sym=urllib.parse.quote(symbol)) + "?range=1d&interval=5m"
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=tls_context()) as r:
            data = json.loads(r.read().decode("utf-8"))
        res = data["chart"]["result"][0]
        meta = res["meta"]
        closes = [c for c in
                  (res.get("indicators", {}).get("quote", [{}])[0].get("close") or [])
                  if c is not None]
        return Quote(
            symbol=meta.get("symbol", symbol),
            name=meta.get("shortName") or meta.get("longName") or symbol,
            price=meta.get("regularMarketPrice"),
            prev_close=meta.get("chartPreviousClose") or meta.get("previousClose"),
            currency=meta.get("currency", "USD"),
            spark=closes[-90:],
        )
    except Exception:
        return Quote(symbol=symbol, name=symbol, error=True)


def _money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if abs(v) >= 10_000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:,.2f}"
    return f"{v:,.2f}"


class StocksWidget(WidgetCard):
    kind = "stocks"
    allow_image = True
    title = "Stocks"
    customizable = True
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 2 * 60 * 1000

    data_ready = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.symbols: List[str] = list(
            self.config.get("symbols") or DEFAULT_SYMBOLS)
        self.quotes: List[Quote] = []
        self._busy = False
        self.data_ready.connect(self.update)

    def on_start(self) -> None:
        self._refresh()

    def on_tick(self) -> None:
        self._refresh()

    def _visible_count(self) -> int:
        return {"small": 1, "medium": 3, "large": 6}[self.size_class]

    def _refresh(self) -> None:
        if self._busy or not self.symbols:
            return
        self._busy = True
        wanted = self.symbols[:self._visible_count()]

        def work():
            got = [fetch_one(s) for s in wanted]
            self.quotes = got
            self._busy = False
            self.data_ready.emit()

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ #
    def context_actions(self, menu) -> None:
        a = QAction("Edit symbols…", menu)
        a.triggered.connect(self._edit)
        menu.addAction(a)

    def _edit(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Watchlist",
            "Comma-separated symbols  (AAPL, ^GSPC, BTC-USD):",
            text=", ".join(self.symbols))
        if not ok:
            return
        syms = [s.strip().upper() for s in text.split(",") if s.strip()]
        if not syms:
            return
        self.symbols = syms
        self.config["symbols"] = syms
        self.changed.emit()
        self.quotes = []
        self._refresh()

    # ------------------------------------------------------------------ #
    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Stocks")

        if not self.quotes:
            self.text(p, r, "loading…", 10, pal.text_faint, align=Qt.AlignCenter)
            return

        if self.size_class == "small":
            self._hero(p, QRectF(r.left(), r.top() + 20,
                                 r.width(), r.height() - 20), self.quotes[0])
            return

        rows = self.quotes[:self._visible_count()]
        top = r.top() + 24
        rh = (r.bottom() - top) / max(1, len(rows))
        for i, q in enumerate(rows):
            self._row(p, QRectF(r.left(), top + i * rh, r.width(), rh), q)

    # ------------------------------------------------------------------ #
    def _tint(self, q: Quote) -> str:
        pal = self.palette_
        if q.error or q.price is None:
            return pal.text_faint
        colour = pal.good if q.up else pal.bad
        # The graph keeps its true green and red under every other setting -
        # customization deliberately leaves pal.good and pal.bad alone. The one
        # exception is the clash the request called out: a green line on a green
        # card, or red on red, disappears into its own background, so there it
        # goes white. Judged against the chosen background colour, which is what
        # "if the background is red" means.
        bg = self.config.get("bg")
        if bg:
            from ..core import appearance
            if appearance.hue_near(colour, bg):
                return "#ffffff"
        return colour

    def _hero(self, p, r: QRectF, q: Quote) -> None:
        """Small size: one symbol, big, with its intraday shape behind."""
        pal = self.palette_
        tint = self._tint(q)

        if q.spark:
            self._spark(p, QRectF(r.left(), r.bottom() - 34, r.width(), 30),
                        q.spark, tint, fill=True)

        self.text(p, QRectF(r.left(), r.top(), r.width(), 18),
                  q.symbol, 11, pal.text, QFont.DemiBold)
        self.text(p, QRectF(r.left(), r.top() + 18, r.width(), 30),
                  _money(q.price), 22, pal.text, QFont.DemiBold)
        self.text(p, QRectF(r.left(), r.top() + 46, r.width(), 18),
                  ("—" if q.error else
                   f"{'+' if q.up else ''}{q.change_pct:.2f}%"),
                  11, tint, QFont.DemiBold)

    def _row(self, p, r: QRectF, q: Quote) -> None:
        """Medium/large: symbol, sparkline, price, and a coloured change pill."""
        pal = self.palette_
        tint = self._tint(q)
        cy = r.center().y()

        self.text(p, QRectF(r.left(), cy - 15, r.width() * 0.30, 16),
                  q.symbol, 11, pal.text, QFont.DemiBold)
        if q.name and q.name != q.symbol:
            self.text(p, QRectF(r.left(), cy + 1, r.width() * 0.30, 14),
                      q.name[:16], 8, pal.text_faint)

        # Price gets a fixed right-aligned column sized for an index value like
        # "7,723.55"; the sparkline takes whatever is left. Sharing the width
        # proportionally let long numbers run back over the chart.
        pill_w, price_w, gap = 62.0, 74.0, 8.0
        price_left = r.right() - pill_w - gap - price_w
        spark_left = r.left() + r.width() * 0.32
        spark_w = max(0.0, price_left - gap - spark_left)

        if q.spark and spark_w > 24:
            self._spark(p, QRectF(spark_left, cy - 12, spark_w, 24), q.spark, tint)

        self.text(p, QRectF(price_left, cy - 10, price_w, 20),
                  _money(q.price), 12, pal.text, QFont.DemiBold,
                  align=Qt.AlignRight | Qt.AlignVCenter)

        # The change reads as a filled pill, the way Apple's Stocks widget does.
        pill = QRectF(r.right() - pill_w, cy - 11, pill_w, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tint))
        p.drawRoundedRect(pill, 7, 7)
        # The label contrasts with the pill, not a fixed white - when the clash
        # guard turns the pill white (red line on a red card) white text would
        # vanish into it.
        from ..core import appearance
        ink = appearance.contrast_text(tint) if tint == "#ffffff" else "#ffffff"
        self.text(p, pill,
                  "—" if q.error else f"{'+' if q.up else ''}{q.change_pct:.2f}%",
                  9, ink, QFont.DemiBold, align=Qt.AlignCenter)

    def _spark(self, p, rect: QRectF, values: List[float], colour: str,
               fill: bool = False) -> None:
        if len(values) < 2:
            return
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        step = rect.width() / (len(values) - 1)
        pts = [QPointF(rect.left() + i * step,
                       rect.bottom() - (v - lo) / span * rect.height())
               for i, v in enumerate(values)]

        if fill:
            area = QPainterPath()
            area.moveTo(pts[0].x(), rect.bottom())
            for pt in pts:
                area.lineTo(pt)
            area.lineTo(pts[-1].x(), rect.bottom())
            area.closeSubpath()
            c = QColor(colour)
            c.setAlpha(44)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawPath(area)

        line = QPainterPath()
        line.moveTo(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(colour), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(line)
