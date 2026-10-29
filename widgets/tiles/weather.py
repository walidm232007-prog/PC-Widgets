"""
Weather via Open-Meteo.

Chosen because it needs no API key and no account, so the widget works the
moment it's added. Location comes from the widget's own config rather than IP
geolocation - silently looking up where someone lives is a poor default for a
thing that just wants to show the temperature.

Fetching happens on a worker thread; a blocked network call must never freeze
the board.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QInputDialog

from ..core.card import RADIUS, WidgetCard, squircle
from ..core.net import tls_context

API = "https://api.open-meteo.com/v1/forecast"
GEO = "https://geocoding-api.open-meteo.com/v1/search"

# WMO weather codes, collapsed into the handful of shapes worth drawing.
CLEAR, CLOUD, PARTLY, RAIN, SNOW, STORM, FOG = range(7)
_CODES = {
    0: (CLEAR, "Clear"), 1: (PARTLY, "Mainly clear"), 2: (PARTLY, "Partly cloudy"),
    3: (CLOUD, "Overcast"), 45: (FOG, "Fog"), 48: (FOG, "Rime fog"),
    51: (RAIN, "Light drizzle"), 53: (RAIN, "Drizzle"), 55: (RAIN, "Heavy drizzle"),
    61: (RAIN, "Light rain"), 63: (RAIN, "Rain"), 65: (RAIN, "Heavy rain"),
    66: (RAIN, "Freezing rain"), 67: (RAIN, "Freezing rain"),
    71: (SNOW, "Light snow"), 73: (SNOW, "Snow"), 75: (SNOW, "Heavy snow"),
    77: (SNOW, "Snow grains"), 80: (RAIN, "Showers"), 81: (RAIN, "Showers"),
    82: (RAIN, "Violent showers"), 85: (SNOW, "Snow showers"),
    86: (SNOW, "Snow showers"), 95: (STORM, "Thunderstorm"),
    96: (STORM, "Thunderstorm"), 99: (STORM, "Thunderstorm"),
}


def describe(code: int):
    return _CODES.get(int(code), (CLOUD, "—"))


# --------------------------------------------------------------------------- #
#  Backdrops
# --------------------------------------------------------------------------- #
# A photograph of the sky behind the header, matched to the condition. Files sit
# in assets/weather/ and are named for the shape they belong to, so the mapping
# is the shape constant and nothing needs a lookup table.
#
# Loaded once per process and shared by every weather card - the images are
# ~40 KB each but decode to about 1.5 MB, and re-decoding one on every repaint
# would be paying that over and over for a picture that never changes.
SHAPE_FILES = {
    CLEAR: "clear", PARTLY: "partly", CLOUD: "cloud", RAIN: "rain",
    SNOW: "snow", STORM: "storm", FOG: "fog",
}
BACKDROP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "weather")

_backdrops: dict = {}


def backdrop(shape: int) -> Optional[QImage]:
    """The photo for this condition, or None if it isn't on disk.

    Missing files are cached as a miss so a stripped-down install doesn't retry
    the same failed load on every paint. The widget draws its normal flat card
    when there's nothing here, so the images are an enhancement, not a
    dependency.
    """
    if shape in _backdrops:
        return _backdrops[shape]
    name = SHAPE_FILES.get(shape)
    img = None
    if name:
        path = os.path.join(BACKDROP_DIR, f"{name}.jpg")
        if os.path.isfile(path):
            loaded = QImage(path)
            img = None if loaded.isNull() else loaded
    _backdrops[shape] = img
    return img


@dataclass
class Day:
    label: str
    code: int
    hi: float
    lo: float


@dataclass
class Forecast:
    place: str = ""
    temp: Optional[float] = None
    feels: Optional[float] = None
    code: int = 3
    days: List[Day] = field(default_factory=list)
    error: str = ""
    fetched: float = 0.0


def _get(url: str, params: dict) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "pc-widgets/1.0"})
    with urllib.request.urlopen(req, timeout=10, context=tls_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(name: str):
    data = _get(GEO, {"name": name, "count": 1, "language": "en", "format": "json"})
    hits = data.get("results") or []
    if not hits:
        return None
    h = hits[0]
    label = h["name"]
    if h.get("country_code"):
        label = f"{label}, {h['country_code']}"
    return (float(h["latitude"]), float(h["longitude"]), label)


def fetch(lat: float, lon: float, place: str, unit: str) -> Forecast:
    data = _get(API, {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto", "forecast_days": 7,
        "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
    })
    cur = data.get("current", {})
    daily = data.get("daily", {})
    days: List[Day] = []
    for i, iso in enumerate(daily.get("time", [])[:7]):
        # Seven columns is too narrow for "Today"/"Tomorrow", and truncating
        # them gives "Tod". Weekday abbreviations read fine in a row, and the
        # first column is today by definition.
        label = time.strftime("%a", time.strptime(iso, "%Y-%m-%d"))
        days.append(Day(label, daily["weather_code"][i],
                        daily["temperature_2m_max"][i],
                        daily["temperature_2m_min"][i]))
    return Forecast(
        place=place,
        temp=cur.get("temperature_2m"),
        feels=cur.get("apparent_temperature"),
        code=cur.get("weather_code", 3),
        days=days, fetched=time.time(),
    )


class WeatherWidget(WidgetCard):
    kind = "weather"
    customizable = True
    allow_image = True
    title = "Weather"
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 15 * 60 * 1000        # the forecast does not change faster

    # Emitted from the fetch thread. Qt queues it across to the GUI thread,
    # which is the only place a repaint may be triggered from.
    data_ready = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.unit = self.config.get("unit", "C")
        self.data = Forecast()
        self._busy = False
        # Composed bands, keyed by condition and size. The photo never changes,
        # so scaling and fading it on every repaint would be pure waste.
        self._band_cache: dict = {}
        self.data_ready.connect(self.update)

    def on_start(self) -> None:
        self._refresh()

    def on_tick(self) -> None:
        self._refresh()

    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        if self._busy:
            return
        lat, lon = self.config.get("lat"), self.config.get("lon")
        if lat is None or lon is None:
            self.data = Forecast(error="Right-click → Set location")
            return
        self._busy = True
        place = self.config.get("place", "")

        def work():
            try:
                got = fetch(float(lat), float(lon), place, self.unit)
            except Exception as exc:
                got = Forecast(place=place, error=type(exc).__name__)
            self.data = got
            self._busy = False
            self.data_ready.emit()      # hops back to the GUI thread

        threading.Thread(target=work, daemon=True).start()

    def context_actions(self, menu) -> None:
        a = QAction("Set location…", menu)
        a.triggered.connect(self._ask_location)
        menu.addAction(a)
        u = QAction(f"Switch to °{'F' if self.unit == 'C' else 'C'}", menu)
        u.triggered.connect(self._toggle_unit)
        menu.addAction(u)
        menu.addSeparator()
        # Open-Meteo publish their data under CC BY 4.0, so the credit is a
        # condition of using it, not a courtesy. Shown where the data is.
        credit = QAction("Weather data by Open-Meteo (CC BY 4.0)", menu)
        credit.setEnabled(False)
        menu.addAction(credit)
        photos = QAction("Sky photographs from Unsplash", menu)
        photos.setEnabled(False)
        menu.addAction(photos)

    def _ask_location(self) -> None:
        text, ok = QInputDialog.getText(self, "Weather location",
                                        "Town or city:",
                                        text=self.config.get("place", ""))
        if not ok or not text.strip():
            return
        try:
            hit = geocode(text.strip())
        except Exception:
            hit = None
        if not hit:
            self.data = Forecast(error=f"Couldn't find “{text.strip()}”")
            self.update()
            return
        lat, lon, label = hit
        self.config.update({"lat": lat, "lon": lon, "place": label})
        self.changed.emit()
        self._refresh()

    def _toggle_unit(self) -> None:
        self.unit = "F" if self.unit == "C" else "C"
        self.config["unit"] = self.unit
        self.changed.emit()
        self._refresh()

    # ------------------------------------------------------------------ #
    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        d = self.data

        if d.error:
            self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Weather")
            self.text(p, r, d.error, 10, pal.text_dim, align=Qt.AlignCenter)
            return
        if d.temp is None:
            self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Weather")
            self.text(p, r, "loading…", 10, pal.text_faint, align=Qt.AlignCenter)
            return

        shape, words = describe(d.code)
        photo = self._backdrop(p, shape)

        # Over a photograph the palette's own label colours stop working - in the
        # light theme they are dark grey, which lands invisibly on a bright cloud,
        # and the faint tier disappears in either theme. Under a photo the text is
        # always light, whatever the theme.
        c_place = "#e6ffffff" if photo else pal.text_dim
        c_temp = "#ffffff" if photo else pal.text
        c_words = "#e0ffffff" if photo else pal.text_dim
        c_feels = "#a8ffffff" if photo else pal.text_faint

        head = QRectF(r.left(), r.top(), r.width(), 16)
        if photo:
            self.text(p, head, d.place or "Weather", 10.5, c_place, QFont.DemiBold)
        else:
            self.label(p, QRectF(r.left(), r.top(), r.width(), 12),
                       d.place or "Weather")

        if self.size_class == "small":
            self._glyph(p, QRectF(r.right() - 34, r.top() + 14, 30, 30), shape)
            self.text(p, QRectF(r.left(), r.top() + 24, r.width(), 44),
                      f"{d.temp:.0f}°", 38, c_temp, QFont.DemiBold)
            self.text(p, QRectF(r.left(), r.bottom() - 18, r.width(), 18),
                      words, 9, c_words)
            return

        self._glyph(p, QRectF(r.left(), r.top() + 16, 40, 40), shape)
        self.text(p, QRectF(r.left() + 48, r.top() + 12, r.width() - 48, 44),
                  f"{d.temp:.0f}°", 32, c_temp, QFont.DemiBold)
        p.setFont(self.font(32, QFont.DemiBold))
        w = p.fontMetrics().horizontalAdvance(f"{d.temp:.0f}°")
        self.text(p, QRectF(r.left() + 54 + w, r.top() + 20, r.width(), 20),
                  words, 10, c_words)
        if d.feels is not None:
            self.text(p, QRectF(r.left() + 54 + w, r.top() + 37, r.width(), 18),
                      f"feels {d.feels:.0f}°", 9, c_feels)

        if self.size_class == "medium":
            self._week(p, QRectF(r.left(), r.top() + 74,
                                 r.width(), r.bottom() - r.top() - 74),
                       d.days, compact=True)
            return

        self._week(p, QRectF(r.left(), r.top() + 92,
                             r.width(), r.bottom() - r.top() - 92),
                   d.days, compact=False)

    # ------------------------------------------------------------------ #
    # Fraction of the card's height the photo covers. Bounded by where the
    # forecast strip starts - +74 into the padded area on a medium card, +92 on a
    # large one, and the padding is 18 - so the photo has finished dissolving
    # before the first day label.
    BAND_DEPTH = {"small": 0.62, "medium": 0.56, "large": 0.32}

    def _backdrop(self, p, shape: int) -> bool:
        """The sky behind the header, matched to the condition.

        Covers the top band and bleeds to the card's edges, clipped to the
        squircle so the corners stay true - the same treatment as the media
        player's album art, for the same reason: a photo inset inside the padding
        reads as a picture pasted onto a card, while one running to the edge
        reads as the card itself.

        Composed offscreen, which is not incidental. The band has to *dissolve*
        into the card, and the obvious way - painting the card's own colour over
        the photo in a gradient - does not work: the card is frosted glass at
        about a third opacity, so a fade painted in that colour at full strength
        comes out darker than the card and ends on a visible horizontal line.
        The fix is to take alpha away from the photo instead of adding paint on
        top, so whatever material is behind the card shows through. That needs a
        surface of its own, because erasing alpha straight onto the widget would
        punch a hole through the card as well.

        Returns whether anything was drawn, so the caller can pick text colours
        that survive a photograph.
        """
        img = backdrop(shape)
        if img is None or img.isNull():
            return False

        card = self.card_rect()
        band = QRectF(card.left(), card.top(), card.width(),
                      card.height() * self.BAND_DEPTH[self.size_class])
        w, h = int(band.width()), int(band.height())
        if w < 4 or h < 4:
            return False

        key = (shape, w, h, self.palette_.dark)
        strip = self._band_cache.get(key)
        if strip is None:
            strip = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            strip.fill(Qt.transparent)
            bp = QPainter(strip)
            bp.setRenderHint(QPainter.SmoothPixmapTransform, True)

            scaled = img.scaled(w, h, Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            sx = max(0, (scaled.width() - w) // 2)
            sy = max(0, (scaled.height() - h) // 2)
            bp.drawImage(QRectF(0, 0, w, h), scaled, QRectF(sx, sy, w, h))

            # Scrim: heaviest at the top, where the place name and temperature
            # sit over whatever the photograph happens to be doing there.
            scrim = QLinearGradient(0, 0, 0, h)
            scrim.setColorAt(0.0, QColor(0, 0, 0, 128))
            scrim.setColorAt(0.5, QColor(0, 0, 0, 76))
            scrim.setColorAt(1.0, QColor(0, 0, 0, 30))
            bp.setPen(Qt.NoPen)
            bp.setBrush(scrim)
            bp.drawRect(QRectF(0, 0, w, h))

            # The dissolve, as alpha removal rather than paint.
            bp.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            fade = QLinearGradient(0, h * 0.46, 0, h)
            fade.setColorAt(0.0, QColor(0, 0, 0, 0))
            fade.setColorAt(1.0, QColor(0, 0, 0, 255))
            bp.setBrush(fade)
            bp.drawRect(QRectF(0, h * 0.46, w, h - h * 0.46))
            bp.end()
            self._band_cache[key] = strip

        p.save()
        p.setClipPath(squircle(card, RADIUS))
        p.drawImage(band.topLeft(), strip)
        p.restore()
        return True

    # ------------------------------------------------------------------ #
    def _week(self, p, strip: QRectF, days: List[Day], compact: bool) -> None:
        """The seven-day outlook.

        Medium has about 60px under the current reading, which is enough for a
        day label and a high - so it drops the icons and the lows rather than
        cramming four rows into the space. Large gets the full column.
        """
        pal = self.palette_
        if not days or strip.height() < 24:
            return

        days = days[:7]
        cw = strip.width() / len(days)
        gh = strip.height()

        for i, day in enumerate(days):
            x = strip.left() + i * cw
            cell = QRectF(x, strip.top(), cw, gh)

            if compact:
                self.text(p, QRectF(x, cell.top() + 2, cw, 16),
                          day.label[:3], 9, pal.text_dim,
                          align=Qt.AlignHCenter | Qt.AlignVCenter)
                icon = min(20.0, gh * 0.34)
                self._glyph(p, QRectF(x + cw / 2 - icon / 2,
                                      cell.top() + 17, icon, icon),
                            describe(day.code)[0])
                self.text(p, QRectF(x, cell.bottom() - 19, cw, 18),
                          f"{day.hi:.0f}°", 11, pal.text, QFont.DemiBold,
                          align=Qt.AlignHCenter | Qt.AlignVCenter)
                continue

            icon = 30.0
            block = 18 + icon + 4 + 22 + 18          # label, icon, hi, lo
            top = strip.top() + max(0.0, (gh - block) / 2.0)

            self.text(p, QRectF(x, top, cw, 18), day.label[:3], 10, pal.text_dim,
                      align=Qt.AlignHCenter | Qt.AlignVCenter)
            self._glyph(p, QRectF(x + cw / 2 - icon / 2, top + 20, icon, icon),
                        describe(day.code)[0])
            self.text(p, QRectF(x, top + 22 + icon + 4, cw, 22),
                      f"{day.hi:.0f}°", 13, pal.text, QFont.DemiBold,
                      align=Qt.AlignHCenter | Qt.AlignVCenter)
            self.text(p, QRectF(x, top + 44 + icon, cw, 18),
                      f"{day.lo:.0f}°", 10, pal.text_faint,
                      align=Qt.AlignHCenter | Qt.AlignVCenter)

    # ------------------------------------------------------------------ #
    def _glyph(self, p, box: QRectF, shape: int) -> None:
        """Weather icons drawn as paths - no icon font, no image assets."""
        pal = self.palette_
        sun = QColor("#f6c445")
        cloud = QColor(pal.text if pal.dark else "#93a3b5")
        cloud.setAlpha(210)
        wet = QColor(pal.accent)

        cx, cy, s = box.center().x(), box.center().y(), box.width()

        def draw_cloud(off_x=0.0, off_y=0.0, scale=1.0):
            path = QPainterPath()
            w, h = s * 0.78 * scale, s * 0.42 * scale
            x, y = cx - w / 2 + off_x, cy - h / 2 + off_y
            path.addEllipse(QRectF(x, y + h * 0.22, w * 0.46, h * 0.78))
            path.addEllipse(QRectF(x + w * 0.20, y, w * 0.52, h * 0.92))
            path.addEllipse(QRectF(x + w * 0.50, y + h * 0.26, w * 0.50, h * 0.74))
            path.addRect(QRectF(x + w * 0.16, y + h * 0.52, w * 0.70, h * 0.46))
            # The puffs overlap, and simplified() keeps what the fill rule fills:
            # under the default odd-even rule every overlap came out as a hole.
            path.setFillRule(Qt.WindingFill)
            p.setPen(Qt.NoPen)
            p.setBrush(cloud)
            p.drawPath(path.simplified())

        if shape in (CLEAR, PARTLY):
            r0 = s * (0.26 if shape == CLEAR else 0.20)
            ox = 0 if shape == CLEAR else -s * 0.16
            oy = 0 if shape == CLEAR else -s * 0.14
            p.setPen(Qt.NoPen)
            p.setBrush(sun)
            p.drawEllipse(QRectF(cx + ox - r0, cy + oy - r0, r0 * 2, r0 * 2))
            if shape == CLEAR:
                p.setPen(QPen(sun, 2.0, Qt.SolidLine, Qt.RoundCap))
                for i in range(8):
                    a = math.radians(i * 45)
                    p.drawLine(
                        cx + math.cos(a) * r0 * 1.35, cy + math.sin(a) * r0 * 1.35,
                        cx + math.cos(a) * r0 * 1.70, cy + math.sin(a) * r0 * 1.70)
            else:
                draw_cloud(s * 0.06, s * 0.10, 0.92)
            return

        draw_cloud(0, -s * 0.06 if shape in (RAIN, SNOW, STORM) else 0)

        if shape == RAIN:
            p.setPen(QPen(wet, 2.2, Qt.SolidLine, Qt.RoundCap))
            for i in (-1, 0, 1):
                x = cx + i * s * 0.18
                p.drawLine(x, cy + s * 0.16, x - s * 0.05, cy + s * 0.34)
        elif shape == SNOW:
            p.setPen(QPen(QColor("#cfe8ff"), 2.0, Qt.SolidLine, Qt.RoundCap))
            for i in (-1, 0, 1):
                x = cx + i * s * 0.18
                p.drawPoint(int(x), int(cy + s * 0.26))
                p.drawLine(x - 3, cy + s * 0.26, x + 3, cy + s * 0.26)
                p.drawLine(x, cy + s * 0.26 - 3, x, cy + s * 0.26 + 3)
        elif shape == STORM:
            bolt = QPainterPath()
            bolt.moveTo(cx + s * 0.04, cy + s * 0.10)
            bolt.lineTo(cx - s * 0.10, cy + s * 0.34)
            bolt.lineTo(cx + s * 0.01, cy + s * 0.33)
            bolt.lineTo(cx - s * 0.06, cy + s * 0.50)
            bolt.lineTo(cx + s * 0.14, cy + s * 0.26)
            bolt.lineTo(cx + s * 0.03, cy + s * 0.27)
            bolt.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f6c445"))
            p.drawPath(bolt)
        elif shape == FOG:
            p.setPen(QPen(cloud, 2.2, Qt.SolidLine, Qt.RoundCap))
            for i in range(3):
                y = cy + s * (0.16 + i * 0.11)
                p.drawLine(cx - s * 0.30, y, cx + s * 0.30, y)
