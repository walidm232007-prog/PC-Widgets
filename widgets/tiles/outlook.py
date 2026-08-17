"""
Calendar — an agenda of what's coming up.

A large tile: today's date at the top, then the next week's events grouped under
day headings, each with a coloured spine, its time and its location. Designed to
be read at a glance from across the desk, so the current day is emphasised and
an event happening *now* is marked rather than merely listed.

Fetching runs on a worker thread and returns through a Qt signal. That is
mandatory for the Windows appointment store: awaiting a WinRT operation on the
GUI thread deadlocks, and because the whole board shares that thread the freeze
takes every other widget down with it.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import QInputDialog

from ..core import calsource
from ..core.card import WidgetCard

# Spine colours, cycled per calendar so different sources stay distinguishable.
SPINES = ("#0a84ff", "#ff9f0a", "#32d74b", "#bf5af2", "#ff375f", "#64d2ff")


class OutlookWidget(WidgetCard):
    kind = "outlook"
    title = "Calendar"
    supported_sizes = ("medium", "large")
    refresh_ms = 30_000            # the agenda does not move fast

    data_ready = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.source = calsource.CalendarSource(self.config.get("ics", ""))
        self.agenda = calsource.Agenda()
        self._fetching = False
        self._spines: dict = {}
        self.data_ready.connect(self.update)

    # ------------------------------------------------------------------ #
    def on_start(self) -> None:
        self._fetch()

    def on_tick(self) -> None:
        self._fetch()

    def _fetch(self) -> None:
        if self._fetching:
            return
        self._fetching = True

        def work():
            try:
                got = self.source.fetch()
            except Exception as exc:
                got = calsource.Agenda(error=type(exc).__name__)
            self.agenda = got
            self._fetching = False
            self.data_ready.emit()

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ #
    def context_actions(self, menu) -> None:
        a = QAction("Set ICS calendar link…", menu)
        a.triggered.connect(self._ask_ics)
        menu.addAction(a)
        if self.config.get("ics"):
            c = QAction("Clear link (use Windows calendar)", menu)
            c.triggered.connect(self._clear_ics)
            menu.addAction(c)
        s = QAction(f"Source: {self.source.kind}", menu)
        s.setEnabled(False)
        menu.addAction(s)

    def _ask_ics(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Outlook calendar",
            "Published ICS link\n"
            "(outlook.com → Settings → Calendar → Shared calendars → Publish):",
            text=self.config.get("ics", ""))
        if not ok:
            return
        url = text.strip()
        self.config["ics"] = url
        self.source = calsource.CalendarSource(url)
        self.changed.emit()
        self.agenda = calsource.Agenda()
        self._fetch()

    def _clear_ics(self) -> None:
        self.config.pop("ics", None)
        self.source = calsource.CalendarSource("")
        self.changed.emit()
        self._fetch()

    # ------------------------------------------------------------------ #
    def _spine(self, calendar: str) -> str:
        if calendar not in self._spines:
            self._spines[calendar] = SPINES[len(self._spines) % len(SPINES)]
        return self._spines[calendar]

    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        today = dt.date.today()
        large = self.size_class == "large"

        # --- header ----------------------------------------------------- #
        self.text(p, QRectF(r.left(), r.top(), r.width(), 18),
                  today.strftime("%A").upper(), 10, pal.bad, QFont.Bold)
        self.text(p, QRectF(r.left(), r.top() + 16, r.width() * 0.6, 34),
                  today.strftime("%-d %B") if False else
                  today.strftime("%d %B").lstrip("0"),
                  22, pal.text, QFont.DemiBold)

        agenda = self.agenda
        groups = agenda.by_day(7 if large else 3)
        count = sum(len(evs) for _, evs in groups)
        self.text(p, QRectF(r.left(), r.top() + 20, r.width(), 22),
                  f"{count} event{'s' if count != 1 else ''}" if count else "",
                  10, pal.text_dim, align=Qt.AlignRight | Qt.AlignTop)

        body = QRectF(r.left(), r.top() + 54, r.width(), r.bottom() - r.top() - 54)

        # --- empty / error states --------------------------------------- #
        if agenda.error:
            self._notice(p, body, agenda.error, pal.warn)
            return
        if not agenda.fetched:
            self._notice(p, body, "loading…", pal.text_faint)
            return
        if not groups:
            hint = ("Nothing scheduled this week."
                    if self.source.kind == "ics" else
                    "No events. Right-click → Set ICS calendar link\n"
                    "to connect your Outlook calendar.")
            self._notice(p, body, hint, pal.text_dim)
            return

        # --- agenda ----------------------------------------------------- #
        y = body.top()
        row_h = 30.0 if large else 26.0
        head_h = 19.0
        now = dt.datetime.now()

        for day, events in groups:
            if y + head_h > body.bottom():
                break
            label = ("Today" if day == today else
                     "Tomorrow" if day == today + dt.timedelta(days=1) else
                     day.strftime("%a %d %b").replace(" 0", " "))
            self.text(p, QRectF(body.left(), y, body.width(), head_h),
                      label, 9.5,
                      pal.text if day == today else pal.text_dim, QFont.DemiBold)
            y += head_h

            for ev in events:
                if y + row_h > body.bottom():
                    remaining = count - self._drawn
                    if remaining > 0:
                        self.text(p, QRectF(body.left(), y, body.width(), 16),
                                  f"+{remaining} more", 8.5, pal.text_faint)
                    return
                self._row(p, QRectF(body.left(), y, body.width(), row_h), ev, now)
                y += row_h
            y += 4

    _drawn = 0

    def _row(self, p, rect: QRectF, ev, now: dt.datetime) -> None:
        pal = self.palette_
        self._drawn += 1
        live = (ev.start <= now <= (ev.end or ev.start)) and not ev.all_day

        colour = QColor(self._spine(ev.calendar))
        # A running event gets a filled spine and a brighter title; a scheduled
        # one just a thin bar. This is the difference between "what's on today"
        # and "what is happening right now".
        spine = QRectF(rect.left(), rect.top() + 3,
                       3.5 if not live else 4.5, rect.height() - 6)
        p.setPen(Qt.NoPen)
        p.setBrush(colour if live else QColor(colour.red(), colour.green(),
                                              colour.blue(), 168))
        p.drawRoundedRect(spine, 2, 2)

        if live:
            glow = QColor(colour)
            glow.setAlpha(34)
            p.setBrush(glow)
            p.drawRoundedRect(rect.adjusted(-2, 1, 2, -1), 6, 6)

        left = rect.left() + 12
        w = rect.width() - 12

        title = self._elide(p, ev.title, w - 62, 10.5, QFont.DemiBold)
        self.text(p, QRectF(left, rect.top() + 1, w - 62, 16),
                  title, 10.5, pal.text if live else pal.text, QFont.DemiBold)

        sub = ev.duration_text()
        if ev.location:
            sub += f"  ·  {ev.location}"
        self.text(p, QRectF(left, rect.top() + 14, w - 62, 14),
                  self._elide(p, sub, w - 62, 8.5), 8.5, pal.text_dim)

        if live:
            self.text(p, QRectF(rect.right() - 58, rect.top() + 2, 58, 16),
                      "NOW", 8.5, colour.name(), QFont.Bold,
                      align=Qt.AlignRight | Qt.AlignVCenter)
        elif not ev.all_day and ev.start.date() == now.date():
            mins = int((ev.start - now).total_seconds() // 60)
            if 0 < mins < 600:
                self.text(p, QRectF(rect.right() - 58, rect.top() + 2, 58, 16),
                          f"in {mins}m" if mins < 60 else f"in {mins // 60}h",
                          8.5, pal.text_faint,
                          align=Qt.AlignRight | Qt.AlignVCenter)

    def _notice(self, p, area: QRectF, text: str, colour: str) -> None:
        self.text(p, area, text, 10, colour, align=Qt.AlignCenter)

    def _elide(self, p, text: str, width: float, size: float,
               weight: int = QFont.Normal) -> str:
        p.setFont(self.font(size, weight))
        fm = p.fontMetrics()
        return (text if fm.horizontalAdvance(text) <= width
                else fm.elidedText(text, Qt.ElideRight, int(max(10, width))))

    def paintEvent(self, ev):
        self._drawn = 0
        super().paintEvent(ev)
