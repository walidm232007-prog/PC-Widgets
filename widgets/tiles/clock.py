"""Clock: an analog face at small size, a big digital readout at medium."""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPen

from ..core.card import PAD, Fader, WidgetCard


class ClockWidget(WidgetCard):
    kind = "clock"
    title = "Clock"
    customizable = True
    allow_image = True     # a photo can sit behind the time
    supported_sizes = ("small", "medium")
    refresh_ms = 1000        # the second hand needs it; nothing else here does

    # The analog face moves every second, the digital one only every minute -
    # so the tick decides, not the timer. Repainting a medium clock 59 times
    # for every time it changes is work nobody sees.
    repaint_on_tick = False

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._shown = None          # the digital block currently on the card
        self._prev = None           # the one dissolving out of it
        self._fade = Fader(self, fps=30)
        self._fade.finish()

    # ------------------------------------------------------------------ #
    def on_tick(self) -> None:
        if self.size_class == "small":
            self.update()           # the second hand really does move each tick
            return

        block = self._block(datetime.now())
        if block == self._shown:
            return                  # nothing to show; nothing to draw
        self._prev, self._shown = self._shown, block
        if self._prev is None:
            self._fade.finish()     # first paint has nothing to dissolve from
        else:
            self._fade.start(320.0)
        self.update()

    @staticmethod
    def _block(now: datetime):
        """Everything the digital face shows, as one comparable value."""
        try:
            hhmm = now.strftime("%#I:%M")        # Windows: hour, no leading zero
        except ValueError:
            hhmm = now.strftime("%I:%M").lstrip("0")
        return (now.strftime("%A, %d %B").replace(" 0", " "),
                hhmm, now.strftime("%p").upper())

    def paint_content(self, p, r: QRectF) -> None:
        now = datetime.now()
        if self.size_class == "small":
            self._analog(p, r, now)
        else:
            if self._shown is None:
                self._shown = self._block(now)
            self._digital(p, r)

    # ------------------------------------------------------------------ #
    def _analog(self, p, r: QRectF, now: datetime) -> None:
        """No label, no date - just the face, centred and as large as it fits.

        Apple's analog clock widget is only the dial; a heading pushed it
        off-centre and cost the diameter that makes it read at a glance.
        """
        pal = self.palette_

        # Use the whole card, not the padded rect: the dial is its own margin.
        face = r.adjusted(-PAD * 0.55, -PAD * 0.55, PAD * 0.55, PAD * 0.55)
        side = min(face.width(), face.height())
        c = QPointF(face.center().x(), face.center().y())
        rad = side / 2 - 2

        # Tick marks: heavier every three hours, so the face reads at a glance.
        for i in range(12):
            a = math.radians(i * 30 - 90)
            major = i % 3 == 0
            inner = rad * (0.78 if major else 0.87)
            col = QColor(pal.text_dim if major else pal.text_faint)
            col.setAlpha(255 if major else 140)
            p.setPen(QPen(col, 2.4 if major else 1.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() + math.cos(a) * inner, c.y() + math.sin(a) * inner),
                       QPointF(c.x() + math.cos(a) * rad * 0.95,
                               c.y() + math.sin(a) * rad * 0.95))

        # Each hand advances smoothly with the one below it, so the hour hand
        # sits between numerals at half past rather than jumping on the hour.
        hands = (
            (((now.hour % 12) + now.minute / 60.0) / 12.0, rad * 0.50, 3.8, pal.text),
            ((now.minute + now.second / 60.0) / 60.0, rad * 0.74, 2.8, pal.text),
        )
        for frac, length, width, colour in hands:
            a = math.radians(frac * 360 - 90)
            p.setPen(QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c, QPointF(c.x() + math.cos(a) * length,
                                  c.y() + math.sin(a) * length))

        a = math.radians(now.second / 60.0 * 360 - 90)
        p.setPen(QPen(QColor(pal.accent), 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() - math.cos(a) * rad * 0.16,
                           c.y() - math.sin(a) * rad * 0.16),
                   QPointF(c.x() + math.cos(a) * rad * 0.82,
                           c.y() + math.sin(a) * rad * 0.82))

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.accent))
        p.drawEllipse(c, 3.2, 3.2)

    # ------------------------------------------------------------------ #
    def _digital(self, p, r: QRectF) -> None:
        """12-hour, centred, with the date above. No timezone line.

        Dropping the timezone frees the bottom third, so the time can grow and
        sit on the card's true centre instead of being pushed up by a caption
        nobody reads.

        A minute change cross-fades. The whole block is dissolved rather than
        only the digits that differ, which sounds wasteful and is not: the hour
        blends with pixels identical to itself and so appears perfectly still,
        while the minute is the only thing that visibly moves. Picking out
        individual glyphs would mean measuring each one and would look no
        different.
        """
        t = self._fade.value
        if self._fade.active and self._prev is not None:
            self._draw_block(p, r, self._prev, 1.0 - t)
        self._draw_block(p, r, self._shown, t if self._fade.active else 1.0)

    def _draw_block(self, p, r: QRectF, block, alpha: float) -> None:
        if block is None or alpha <= 0.004:
            return
        pal = self.palette_
        date, hhmm, suffix = block

        p.setOpacity(alpha)
        self.text(p, QRectF(r.left(), r.top(), r.width(), 20),
                  date, 11, pal.text_dim, QFont.DemiBold,
                  Qt.AlignHCenter | Qt.AlignVCenter)

        size = 58
        p.setFont(self.font(size, QFont.DemiBold))
        fm = p.fontMetrics()
        gap = 8.0
        w_time = fm.horizontalAdvance(hhmm)

        p.setFont(self.font(17, QFont.DemiBold))
        w_suffix = p.fontMetrics().horizontalAdvance(suffix)

        block = w_time + gap + w_suffix
        left = r.left() + (r.width() - block) / 2.0
        band = QRectF(r.left(), r.top() + 20, r.width(), r.height() - 20)

        self.text(p, QRectF(left, band.top(), w_time, band.height()),
                  hhmm, size, pal.text, QFont.DemiBold,
                  Qt.AlignLeft | Qt.AlignVCenter)
        # AM/PM sits on the time's baseline rather than its centre.
        self.text(p, QRectF(left + w_time + gap,
                            band.top() + band.height() / 2 - 2,
                            w_suffix + 4, 26),
                  suffix, 17, pal.text_dim, QFont.DemiBold,
                  Qt.AlignLeft | Qt.AlignVCenter)
        p.setOpacity(1.0)
