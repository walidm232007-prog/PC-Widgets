"""System and disk tiles - CPU, memory, storage, uptime.

All readings come from :mod:`widgets.core.winapi` via ctypes, so there is no
psutil dependency. CPU in particular is a delta between successive samples,
which is why these tiles poll on a timer rather than reading on demand.
"""

from __future__ import annotations

from collections import deque
from typing import Deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen

from ..core import winapi
from ..core.card import WidgetCard


def _fmt_uptime(seconds: float) -> str:
    m = int(seconds // 60)
    d, rem = divmod(m, 1440)
    h, mm = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {mm}m"
    return f"{mm}m"


class SystemWidget(WidgetCard):
    kind = "system"
    title = "System"
    supported_sizes = ("small", "medium")
    refresh_ms = 1500

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.cpu = 0.0
        self.mem = winapi.memory()
        self.history: Deque[float] = deque(maxlen=48)

    def on_tick(self) -> None:
        self.cpu = winapi.cpu_percent()
        self.mem = winapi.memory()
        self.history.append(self.cpu)

    def _colour(self, pct: float) -> str:
        """White until it actually matters, then amber, then red.

        The accent blue made a completely idle machine look like it was
        reporting something; reserving colour for the thresholds means a
        coloured number always means "look at me".
        """
        pal = self.palette_
        return pal.bad if pct >= 90 else (pal.warn if pct >= 70 else pal.text)

    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "System")

        if self.size_class == "small":
            self.text(p, QRectF(r.left(), r.top() + 18, r.width(), 44),
                      f"{self.cpu:.0f}%", 34, self._colour(self.cpu), QFont.DemiBold)
            self.text(p, QRectF(r.left(), r.top() + 60, r.width(), 16),
                      "CPU", 9, pal.text_dim)
            self.bar(p, QRectF(r.left(), r.bottom() - 26, r.width(), 6),
                     self.mem.percent / 100.0, pal.text_dim)
            self.text(p, QRectF(r.left(), r.bottom() - 18, r.width(), 18),
                      f"RAM {self.mem.percent:.0f}%  ·  "
                      f"{self.mem.used_gb:.1f}/{self.mem.total_gb:.0f} GB",
                      9, pal.text_dim)
            return

        # Medium: sparkline on the left, readouts on the right.
        graph = QRectF(r.left(), r.top() + 20, r.width() * 0.52, r.height() - 40)
        self._sparkline(p, graph)

        col = r.left() + r.width() * 0.60
        w = r.width() - (col - r.left())
        self.text(p, QRectF(col, r.top() + 14, w, 40),
                  f"{self.cpu:.0f}%", 30, self._colour(self.cpu), QFont.DemiBold)
        self.text(p, QRectF(col, r.top() + 50, w, 14), "CPU", 9, pal.text_dim)

        self.bar(p, QRectF(col, r.top() + 72, w, 6), self.mem.percent / 100.0,
                 self._colour(self.mem.percent))
        self.text(p, QRectF(col, r.top() + 82, w, 16),
                  f"RAM  {self.mem.used_gb:.1f} / {self.mem.total_gb:.0f} GB",
                  9, pal.text_dim)
        self.text(p, QRectF(r.left(), r.bottom() - 16, r.width(), 16),
                  f"up {_fmt_uptime(winapi.uptime_seconds())}", 9, pal.text_faint)

    def _sparkline(self, p, rect: QRectF) -> None:
        pal = self.palette_
        if len(self.history) < 2:
            self.text(p, rect, "sampling…", 9, pal.text_faint,
                      align=Qt.AlignCenter)
            return

        n = len(self.history)
        step = rect.width() / max(1, self.history.maxlen - 1)
        pts = [
            QPointF(rect.right() - (n - 1 - i) * step,
                    rect.bottom() - (v / 100.0) * rect.height())
            for i, v in enumerate(self.history)
        ]

        fill = QPainterPath()
        fill.moveTo(pts[0].x(), rect.bottom())
        for pt in pts:
            fill.lineTo(pt)
        fill.lineTo(pts[-1].x(), rect.bottom())
        fill.closeSubpath()
        c = QColor(pal.text)
        c.setAlpha(44)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(fill)

        line = QPainterPath()
        line.moveTo(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(pal.text), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(line)


class DiskWidget(WidgetCard):
    kind = "disk"
    title = "Storage"
    supported_sizes = ("small", "medium")
    refresh_ms = 30_000        # disk usage does not move fast

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.drive = self.config.get("drive", "C:\\")
        self.info = winapi.disk(self.drive)

    def on_tick(self) -> None:
        self.info = winapi.disk(self.drive)

    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Storage")

        if self.info is None:
            self.text(p, r, "unavailable", 11, pal.text_faint, align=Qt.AlignCenter)
            return

        d = self.info
        colour = pal.bad if d.percent >= 90 else (pal.warn if d.percent >= 75
                                                  else pal.accent)
        free = d.total_gb - d.used_gb

        if self.size_class == "small":
            self._ring(p, QRectF(r.left() + r.width() / 2 - 32, r.top() + 18, 64, 64),
                       d.percent / 100.0, colour)
            self.text(p, QRectF(r.left(), r.bottom() - 20, r.width(), 20),
                      f"{free:.0f} GB free", 10, pal.text_dim,
                      align=Qt.AlignHCenter | Qt.AlignVCenter)
            return

        self._ring(p, QRectF(r.left(), r.top() + 16, 72, 72), d.percent / 100.0, colour)
        col = r.left() + 90
        w = r.width() - 90
        self.text(p, QRectF(col, r.top() + 20, w, 26),
                  f"Drive {d.letter}:", 14, pal.text, QFont.DemiBold)
        self.text(p, QRectF(col, r.top() + 46, w, 20),
                  f"{d.used_gb:.0f} GB of {d.total_gb:.0f} GB used", 10, pal.text_dim)
        self.text(p, QRectF(col, r.top() + 66, w, 20),
                  f"{free:.0f} GB free", 10, colour, QFont.DemiBold)

    def _ring(self, p, rect: QRectF, frac: float, colour: str) -> None:
        pal = self.palette_
        width = 8.0
        inner = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)

        track = QColor(pal.text_faint)
        track.setAlpha(52)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(track, width, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(inner, 0, 360 * 16)

        p.setPen(QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(inner, 90 * 16, -int(360 * 16 * max(0.0, min(1.0, frac))))

        self.text(p, rect, f"{frac * 100:.0f}%", 13, pal.text, QFont.DemiBold,
                  align=Qt.AlignCenter)
