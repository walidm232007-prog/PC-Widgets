"""Calendar: the date at small size, a full month grid at medium and large."""

from __future__ import annotations

import calendar
from datetime import date

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont

from ..core.card import WidgetCard


class CalendarWidget(WidgetCard):
    kind = "calendar"
    title = "Calendar"
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 60_000        # only needs to notice midnight

    def paint_content(self, p, r: QRectF) -> None:
        today = date.today()
        if self.size_class == "small":
            self._today(p, r, today)
        else:
            self._month(p, r, today)

    # ------------------------------------------------------------------ #
    def _today(self, p, r: QRectF, today: date) -> None:
        """Apple's hierarchy: red weekday, then the date, large.

        The month name is dropped - between the weekday and a two-digit number
        there was nothing for it to disambiguate, and three stacked lines of
        text in a 2x2 tile is one more than the shape can carry.
        """
        pal = self.palette_
        self.text(p, QRectF(r.left(), r.top(), r.width(), 20),
                  today.strftime("%a").upper(), 13, pal.bad, QFont.Bold)
        self.text(p, QRectF(r.left(), r.top() + 20, r.width(), 62),
                  str(today.day), 52, pal.text, QFont.DemiBold)

    def _month(self, p, r: QRectF, today: date) -> None:
        pal = self.palette_
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12),
                   today.strftime("%B %Y"))

        head_y = r.top() + 20
        grid_top = head_y + 16
        cols = 7
        cw = r.width() / cols
        weeks = calendar.monthcalendar(today.year, today.month)

        # Let the grid grow into the space it's given rather than sitting in a
        # 22px band with the rest of a large card left empty.
        avail = r.bottom() - grid_top
        ch = max(18.0, min(avail / max(1, len(weeks)), cw))
        day_pt = 10 if ch < 28 else 13
        grid_top += max(0.0, (avail - ch * len(weeks)) / 2.0)

        for i, name in enumerate(("M", "T", "W", "T", "F", "S", "S")):
            weekend = i >= 5
            self.text(p, QRectF(r.left() + i * cw, head_y, cw, 14), name, 8,
                      pal.text_faint if not weekend else pal.text_dim,
                      QFont.DemiBold, Qt.AlignCenter)

        for row, week in enumerate(weeks):
            for col, day in enumerate(week):
                if day == 0:
                    continue
                cell = QRectF(r.left() + col * cw, grid_top + row * ch, cw, ch)
                is_today = day == today.day
                if is_today:
                    # A filled pill rather than a ring: reads instantly at 22px.
                    d = min(cell.width(), cell.height()) - 3
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(pal.accent))
                    p.drawEllipse(QRectF(cell.center().x() - d / 2,
                                         cell.center().y() - d / 2, d, d))
                colour = ("#ffffff" if is_today else
                          (pal.text_dim if col >= 5 else pal.text))
                self.text(p, cell, str(day), day_pt, colour,
                          QFont.DemiBold if is_today else QFont.Normal,
                          Qt.AlignCenter)
