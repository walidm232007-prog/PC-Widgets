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
from dataclasses import dataclass, field, replace
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
    QPainterPathStroker, QPen, QRadialGradient, QTransform,
)
from PySide6.QtWidgets import QInputDialog

from ..core.card import RADIUS, WidgetCard, squircle
from ..core.net import tls_context

API = "https://api.open-meteo.com/v1/forecast"
GEO = "https://geocoding-api.open-meteo.com/v1/search"

# WMO weather codes, collapsed into the handful of shapes worth drawing.
CLEAR, CLOUD, PARTLY, RAIN, SNOW, STORM, FOG, OVERCAST, SLEET = range(9)
# Not weather, but drawn with the same set: the sun on the horizon with an
# arrow, for sunrise and sunset in the hourly row and the Sunrise face.
SUNUP, SUNDOWN = 20, 21
_CODES = {
    0: (CLEAR, "Clear"), 1: (PARTLY, "Mainly clear"), 2: (PARTLY, "Partly cloudy"),
    3: (OVERCAST, "Overcast"), 45: (FOG, "Fog"), 48: (FOG, "Rime fog"),
    51: (RAIN, "Light drizzle"), 53: (RAIN, "Drizzle"), 55: (RAIN, "Heavy drizzle"),
    56: (SLEET, "Freezing drizzle"), 57: (SLEET, "Freezing drizzle"),
    61: (RAIN, "Light rain"), 63: (RAIN, "Rain"), 65: (RAIN, "Heavy rain"),
    66: (SLEET, "Freezing rain"), 67: (SLEET, "Freezing rain"),
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
    SNOW: "snow", STORM: "storm", FOG: "fog", OVERCAST: "cloud", SLEET: "rain",
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
    precip: float = 0.0         # the day's total, in the forecast's rain unit
    pop: int = 0                # highest chance of precipitation, percent


SUNRISE, SUNSET = "sunrise", "sunset"


@dataclass
class Hour:
    """One slot of the hourly row: an hour, or a sunrise or sunset between two.

    Apple's widgets put the sun event in the row at its own time ("8:14 PM",
    with the sunset symbol) and drop the last hour to keep the count, so the
    timeline carries events as slots of their own rather than as a side list.
    """
    label: str                  # "10 AM", or "8:14 PM" for a sun event
    code: int
    temp: float
    is_day: bool = True
    pop: int = 0
    event: str = ""             # "", SUNRISE or SUNSET


@dataclass
class Forecast:
    place: str = ""
    temp: Optional[float] = None
    feels: Optional[float] = None
    code: int = 3
    is_day: bool = True
    days: List[Day] = field(default_factory=list)
    error: str = ""
    fetched: float = 0.0
    # Today's range, for "H:48° L:39°".
    hi: Optional[float] = None
    lo: Optional[float] = None
    wind: Optional[float] = None
    wind_unit: str = "km/h"
    rain_unit: str = "mm"
    # The next 24 hours from the current one, sun events included.
    hours: List[Hour] = field(default_factory=list)
    # Precipitation over the next hour in 15-minute steps: five readings, at
    # now, +15, +30, +45 and +60 minutes, each the amount that fell in the 15
    # minutes before it. Empty when the service sent none.
    next_hour: List[float] = field(default_factory=list)
    # Today's sun, as minutes after local midnight at the forecast's place,
    # with "now" on the same clock, so a face can place the sun on its arc
    # without knowing the place's time zone.
    now_min: Optional[int] = None
    sunrise_min: Optional[int] = None
    sunset_min: Optional[int] = None
    sunrise_next: str = ""      # next sunrise from now, "6:29 AM"
    sunset_next: str = ""       # next sunset from now, "7:54 PM"


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


def query(lat: float, lon: float, unit: str) -> dict:
    """The Open-Meteo request, apart from the network call, so it can be read
    and tested. Imperial temperatures bring imperial wind and rain with them:
    nobody who wants °F wants km/h."""
    imperial = unit == "F"
    return {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,is_day,"
                   "wind_speed_10m",
        "hourly": "temperature_2m,weather_code,is_day,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,"
                 "sunset,precipitation_sum,precipitation_probability_max",
        "minutely_15": "precipitation",
        "timezone": "auto", "forecast_days": 7,
        # Today's 15-minute steps up to an hour from now; past ones are
        # skipped by index, so a few spare cover a fetch late in the quarter.
        "past_minutely_15": 1, "forecast_minutely_15": 6,
        "forecast_hours": 26,
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
        "precipitation_unit": "inch" if imperial else "mm",
    }


def fetch(lat: float, lon: float, place: str, unit: str) -> Forecast:
    return parse(_get(API, query(lat, lon, unit)), place, unit)


def _clock(iso: str) -> str:
    """'2026-11-13T20:14' -> '8:14 PM'; on the hour, '8 PM'."""
    h, m = int(iso[11:13]), int(iso[14:16])
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {ampm}" if m == 0 else f"{h12}:{m:02d} {ampm}"


def _minutes(iso: str) -> int:
    return int(iso[11:13]) * 60 + int(iso[14:16])


def _list(block: dict, key: str, n: int, default=0):
    """block[key] padded to n with default, so one missing series can't take
    the whole forecast down with an IndexError."""
    got = list(block.get(key) or [])
    return [default if v is None else v for v in got[:n]] + [default] * (n - len(got))


def parse(data: dict, place: str, unit: str = "C") -> Forecast:
    """An Open-Meteo response as a Forecast.

    All times in the response are local to the forecast's place (timezone
    "auto"), and so is the "current" block's time, so everything here is
    worked out on that one clock and never touches the PC's time zone - the
    widget can show a town eight hours away without the sun setting at the
    wrong hour.
    """
    cur = data.get("current", {})
    daily = data.get("daily", {})
    now = cur.get("time") or ""
    nd = len(daily.get("time") or [])

    codes = _list(daily, "weather_code", nd, 3)
    highs = _list(daily, "temperature_2m_max", nd, 0.0)
    lows = _list(daily, "temperature_2m_min", nd, 0.0)
    sums = _list(daily, "precipitation_sum", nd, 0.0)
    pops = _list(daily, "precipitation_probability_max", nd, 0)
    days: List[Day] = []
    for i, iso in enumerate((daily.get("time") or [])[:7]):
        # Seven columns is too narrow for "Today"/"Tomorrow", and truncating
        # them gives "Tod". Weekday abbreviations read fine in a row, and the
        # first column is today by definition.
        label = time.strftime("%a", time.strptime(iso, "%Y-%m-%d"))
        days.append(Day(label, codes[i], highs[i], lows[i],
                        float(sums[i]), int(pops[i])))

    imperial = unit == "F"
    fc = Forecast(
        place=place,
        temp=cur.get("temperature_2m"),
        feels=cur.get("apparent_temperature"),
        code=cur.get("weather_code", 3),
        is_day=bool(cur.get("is_day", 1)),
        days=days, fetched=time.time(),
        hi=days[0].hi if days else None,
        lo=days[0].lo if days else None,
        wind=cur.get("wind_speed_10m"),
        wind_unit="mph" if imperial else "km/h",
        rain_unit="in" if imperial else "mm",
    )
    if not now:
        return fc

    rises = [t for t in (daily.get("sunrise") or []) if t]
    sets = [t for t in (daily.get("sunset") or []) if t]
    fc.now_min = _minutes(now)
    if rises and rises[0][:10] == now[:10]:
        fc.sunrise_min = _minutes(rises[0])
    if sets and sets[0][:10] == now[:10]:
        fc.sunset_min = _minutes(sets[0])
    # ISO strings in one zone compare correctly as text.
    fc.sunrise_next = next((_clock(t) for t in rises if t > now), "")
    fc.sunset_next = next((_clock(t) for t in sets if t > now), "")

    fc.hours = _timeline(data.get("hourly", {}), now, rises + sets, set(sets))
    fc.next_hour = _next_hour(data.get("minutely_15", {}), now)
    return fc


def _timeline(hourly: dict, now: str, events, sunsets, span: int = 24) -> List[Hour]:
    times = list(hourly.get("time") or [])
    n = len(times)
    temps = _list(hourly, "temperature_2m", n, 0.0)
    codes = _list(hourly, "weather_code", n, 3)
    days = _list(hourly, "is_day", n, 1)
    pops = _list(hourly, "precipitation_probability", n, 0)

    start = now[:13] + ":00"                    # the current hour
    first = next((i for i, t in enumerate(times) if t >= start), None)
    if first is None:
        return []
    picked = list(range(first, min(n, first + span)))
    if not picked:
        return []
    end = times[picked[-1]]

    out: List[Hour] = []
    for i in picked:
        t = times[i]
        out.append(Hour(_clock(t), int(codes[i]), float(temps[i]),
                        bool(days[i]), int(pops[i])))
        nxt = times[i + 1] if i + 1 < n else None
        # A sun event after this hour starts and before the next one does,
        # carrying this hour's temperature: Apple shows the reading there too.
        for e in sorted(events):
            if t < e and (nxt is None or e < nxt) and e <= end and e > now:
                kind = SUNSET if e in sunsets else SUNRISE
                out.append(Hour(_clock(e), int(codes[i]), float(temps[i]),
                                kind == SUNRISE, int(pops[i]), kind))
    return out


def _next_hour(block: dict, now: str) -> List[float]:
    times = list(block.get("time") or [])
    vals = _list(block, "precipitation", len(times), 0.0)
    first = next((i for i, t in enumerate(times) if t >= now), None)
    if first is None or first + 5 > len(times):
        return []
    return [float(v) for v in vals[first:first + 5]]


# A 15-minute amount at or under this is a trace - rounding noise in the model,
# not rain anyone would notice. 0.1 mm in 15 minutes is light drizzle.
_TRACE = {"mm": 0.05, "in": 0.002}


def next_hour_summary(fc: Forecast) -> str:
    """What the next hour holds, in Apple's words: 'Rain for the next hour',
    'Rain stopping in 30 min', 'Rain starting in 15 min', or none at all."""
    vals = fc.next_hour
    if not vals:
        return ""
    trace = _TRACE.get(fc.rain_unit, 0.05)
    wet = [v > trace for v in vals]
    shape = describe(fc.code)[0]
    word = "Snow" if shape == SNOW else "Rain"
    if all(wet[1:]):
        return f"{word} for the next hour"
    if not any(wet):
        return "No rain for the next hour"
    # Reading i covers the quarter from (i-1)*15 to i*15 minutes from now.
    if wet[0] or wet[1]:
        stop = next(i for i in range(1, 5) if not wet[i])
        return f"{word} stopping in {max(15, (stop - 1) * 15)} min"
    start = next(i for i in range(1, 5) if wet[i])
    return f"{word} starting in {(start - 1) * 15 or 15} min"


def spoken(fc: Forecast) -> str:
    """The forecast as one sentence for a screen reader."""
    if fc.error:
        return fc.error
    if fc.temp is None:
        return "Loading"
    place = (fc.place or "").split(",")[0]
    parts = [f"{place + ', ' if place else ''}{_deg(fc.temp)}, {describe(fc.code)[1]}"]
    if fc.hi is not None and fc.lo is not None:
        parts.append(f"high {_deg(fc.hi)}, low {_deg(fc.lo)}")
    hour = next_hour_summary(fc)
    if hour:
        parts.append(hour)
    nxt = fc.sunset_next if fc.is_day else fc.sunrise_next
    if nxt:
        parts.append(f"{'sunset' if fc.is_day else 'sunrise'} at {nxt}")
    return ". ".join(p[0].upper() + p[1:] for p in parts) + "."


def _deg(t: Optional[float]) -> str:
    """'21°'. Rounded first, so -0.3 reads 0° and never '-0°'."""
    return "—" if t is None else f"{int(round(t))}°"


def _clock_min(m: int) -> str:
    return _clock(f"0000-00-00T{m // 60:02d}:{m % 60:02d}")


def _wind(fc: Forecast) -> str:
    return "—" if fc.wind is None else f"{int(round(fc.wind))} {fc.wind_unit}"


def _amount(v: float, unit: str) -> str:
    if unit == "in":
        return f"{v:.2f} in" if v < 1 else f"{v:.1f} in"
    return f"{v:.1f} mm" if v < 10 else f"{v:.0f} mm"


# A day with less than this is a dry day; Apple's "None" means much the same.
_DRY_DAY = {"mm": 0.1, "in": 0.004}


def precip_summary(fc: Forecast) -> str:
    """The Conditions face's precipitation line: today's amount, else the next
    wet day's, else how long it stays dry ('None for 7d')."""
    dry = _DRY_DAY.get(fc.rain_unit, 0.1)
    for i, day in enumerate(fc.days):
        if day.precip >= dry:
            when = "today" if i == 0 else ("tomorrow" if i == 1 else day.label[:3])
            return f"{_amount(day.precip, fc.rain_unit)} {when}"
    return f"None for {len(fc.days)}d" if fc.days else "—"


# The faces' sky, top to bottom, by condition: blue for a clear day, slate for
# cloud and rain, darker for storms, navy at night. Laid over the glass at
# SKY_ALPHA, so the wallpaper still shows faintly. Picked so that even over a
# white wallpaper, at the gradient's lightest end, white and 85% white
# (secondary text, SKY_DIM) both keep 4.5:1 and the rain bars 3:1; the
# selftest recomputes all three.
SKY_ALPHA = 0.92
SKY_DIM = "#d9ffffff"
SKY_BAR = "#8fdcff"          # the rain bars' lower, darker end on the sky
_SKY = {
    CLEAR: ("#245ba8", "#18478a"),
    PARTLY: ("#355a84", "#274a6f"),
    CLOUD: ("#4f5864", "#3b444f"),
    OVERCAST: ("#4f5864", "#3b444f"),
    FOG: ("#4f5864", "#3b444f"),
    RAIN: ("#4f5967", "#343e4a"),
    SLEET: ("#4f5967", "#343e4a"),
    STORM: ("#414856", "#272c38"),
    SNOW: ("#525c66", "#3e4854"),
}
_SKY_NIGHT = ("#1d2843", "#0d1426")
_SKY_NIGHT_CLOUD = ("#2a3241", "#151b26")


# How strongly the sky colour covers each photograph, by day and by night.
# Measured, not picked by eye: each is the least that keeps white text and
# 90% white (secondary text over a photo) at 4.5:1 over the brightest 2% of
# that photo, at card size. The storm photo is dark already and needs little;
# the bright clouds of the partly cloudy one need most. The photos are daytime
# skies, so night never goes under NIGHT_FLOOR, or night would read as dusk.
# The selftest measures the real composites again.
_PHOTO_SKY = {
    "clear": (0.76, 0.56), "partly": (0.82, 0.64), "cloud": (0.74, 0.58),
    "rain": (0.82, 0.66), "storm": (0.50, 0.42), "snow": (0.70, 0.52),
    "fog": (0.68, 0.48),
}
NIGHT_FLOOR = 0.72
PHOTO_DIM = "#e6ffffff"      # secondary text on a photo face: 90% white


def photo_sky_alpha(shape: int, is_day: bool) -> float:
    day, night = _PHOTO_SKY.get(SHAPE_FILES.get(shape, "cloud"), (0.82, 0.66))
    return day if is_day else max(night, NIGHT_FLOOR)


def sky_tint(shape: int, is_day: bool):
    if is_day:
        return _SKY.get(shape, _SKY[CLOUD])
    return _SKY_NIGHT if shape in (CLEAR, PARTLY) else _SKY_NIGHT_CLOUD


# Apple's temperature scale for the range bars, in °C: cold blue through
# green and yellow to hot red.
_TEMP_STOPS = ((-10, "#5e9cf5"), (0, "#5ac8fa"), (10, "#7fd6a4"),
               (18, "#d8dc4a"), (25, "#f7c843"), (32, "#ff9f0a"),
               (40, "#ff453a"))


def _temp_colour(c: float) -> QColor:
    stops = _TEMP_STOPS
    if c <= stops[0][0]:
        return QColor(stops[0][1])
    for (t0, a), (t1, b) in zip(stops, stops[1:]):
        if c <= t1:
            f = (c - t0) / (t1 - t0)
            ca, cb = QColor(a), QColor(b)
            return QColor(round(ca.red() + (cb.red() - ca.red()) * f),
                          round(ca.green() + (cb.green() - ca.green()) * f),
                          round(ca.blue() + (cb.blue() - ca.blue()) * f))
    return QColor(stops[-1][1])


def _rain_level(rate_mm_h: float) -> float:
    """Bar height, 0..1, for a rate in mm an hour. Thirds for light (up to
    2.5 mm/h), moderate (7.6) and heavy - the Met Office's bands, and the
    dashed lines on the chart mark the two boundaries."""
    if rate_mm_h < 0.2:
        return 0.0
    if rate_mm_h <= 2.5:
        return 0.08 + (rate_mm_h - 0.2) / 2.3 * (1 / 3 - 0.08)
    if rate_mm_h <= 7.6:
        return 1 / 3 + (rate_mm_h - 2.5) / 5.1 / 3
    return min(1.0, 2 / 3 + (rate_mm_h - 7.6) / 42.4 / 3)


def rain_levels(fc: Forecast, n: int) -> List[float]:
    """n bar heights across the next hour.

    Open-Meteo gives four quarters, not minutes, so each quarter's rate sits at
    its middle (7.5, 22.5, 37.5 and 52.5 minutes) and the bars between are
    drawn on the straight line joining them: a smooth chart, honest about
    where the readings are.
    """
    q = fc.next_hour[1:5]
    if len(q) < 4:
        return [0.0] * n
    to_mm = 25.4 if fc.rain_unit == "in" else 1.0
    rates = [v * 4 * to_mm for v in q]          # 15-minute amount -> per hour
    out = []
    for i in range(n):
        m = (i + 0.5) * 60.0 / n
        pos = min(3.0, max(0.0, (m - 7.5) / 15.0))
        lo = int(pos)
        hi = min(3, lo + 1)
        rate = rates[lo] + (rates[hi] - rates[lo]) * (pos - lo)
        out.append(_rain_level(rate))
    return out


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
        self.data_ready.connect(self.speak)
        self.setAccessibleName("Weather")

    def speak(self) -> None:
        """What a screen reader says for the card: everything it shows is
        painted, so without this it would hear nothing at all."""
        self.setAccessibleDescription(spoken(self.data))

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
        faces = self.available_faces()
        if len(faces) > 1:
            sub = menu.addMenu("Face")
            for key, name in faces:
                f = QAction(name, sub, checkable=True)
                f.setChecked(key == self.face)
                f.triggered.connect(lambda _c, k=key: self.set_face(k))
                sub.addAction(f)
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

    # ------------------------------------------------------------------ #
    #  Faces
    # ------------------------------------------------------------------ #
    # Each size has its own set, modelled on Apple's Weather widgets for the
    # Mac, and the card remembers a choice per size: going medium and back to
    # small brings back the small face you had. "classic" is the original
    # design and the default everywhere, so a saved layout looks the same
    # until someone picks something else.
    FACES = {
        "small": (("classic", "Classic"), ("conditions", "Conditions"),
                  ("forecast", "Forecast"), ("nexthour", "Next hour"),
                  ("sun", "Sunrise and sunset")),
        "medium": (("classic", "Classic"), ("hourly", "Hourly"),
                   ("nexthour", "Next hour")),
        "large": (("classic", "Classic"), ("daily", "Hourly and daily"),
                  ("nexthour", "Next hour")),
    }

    def _face_painters(self) -> dict:
        """The faces that are built, by key. A face listed in FACES but not
        here stays out of the menu, and a saved choice of it draws Classic."""
        by_size = {
            "small": {"conditions": self._paint_conditions,
                      "forecast": self._paint_forecast,
                      "nexthour": self._paint_nexthour,
                      "sun": self._paint_sun},
            "medium": {"hourly": self._paint_hourly,
                       "nexthour": self._paint_nexthour_wide},
            "large": {"daily": self._paint_daily,
                      "nexthour": self._paint_nexthour_wide},
        }
        return {"classic": self._paint_classic,
                **by_size.get(self.size_class, {})}

    def available_faces(self):
        built = self._face_painters()
        return [f for f in self.FACES.get(self.size_class, ()) if f[0] in built]

    @property
    def face(self) -> str:
        chosen = (self.config.get("faces") or {}).get(self.size_class, "classic")
        return chosen if chosen in self._face_painters() else "classic"

    def set_face(self, key: str) -> None:
        if key not in dict(self.available_faces()):
            return
        faces = dict(self.config.get("faces") or {})
        if faces.get(self.size_class) == key:
            return
        faces[self.size_class] = key
        self.config["faces"] = faces
        self.changed.emit()
        self.update()

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
        if not self.tinted:
            self._face_painters()[self.face](p, r)
            return
        # On the sky tint, text and icons are white whatever the theme, the
        # way Apple's weather widgets are.
        base = self.palette_
        on_photo = backdrop(describe(self.data.code)[0]) is not None
        self.palette_ = replace(base, dark=True, text="#ffffff",
                                text_dim=PHOTO_DIM if on_photo else SKY_DIM,
                                text_faint="#70ffffff")
        try:
            self._face_painters()[self.face](p, r)
        finally:
            self.palette_ = base

    # ------------------------------------------------------------------ #
    #  Sky background
    # ------------------------------------------------------------------ #
    @property
    def tinted(self) -> bool:
        """The new faces sit on the condition's sky. Classic keeps its photo
        band, and a colour or picture someone chose for the card keeps theirs."""
        c = self.config
        return (self.face != "classic" and not c.get("image")
                and not c.get("bg") and not c.get("fg")
                and c.get("fill") != "solid")

    def paint_tint(self, p, path: QPainterPath, r: QRectF) -> None:
        """The condition's photograph across the whole card, under its sky
        colour - just enough of it to keep the text readable on that photo
        (PHOTO_SKY). Without the photo on disk, the sky colour alone."""
        if not self.tinted:
            return
        d = self.data
        shape = describe(d.code)[0]
        photo = self._full_photo(shape, int(r.width()), int(r.height()))
        alpha = SKY_ALPHA
        p.save()
        p.setClipPath(path)
        if photo is not None:
            p.drawImage(r.topLeft(), photo)
            alpha = photo_sky_alpha(shape, d.is_day)
        top, bottom = sky_tint(shape, d.is_day)
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, QColor(top))
        grad.setColorAt(1.0, QColor(bottom))
        p.setOpacity(p.opacity() * alpha)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRect(r)
        p.restore()

    def _full_photo(self, shape: int, w: int, h: int) -> Optional[QImage]:
        """The photo cropped to fill the card, scaled once per size."""
        img = backdrop(shape)
        if img is None or w < 4 or h < 4:
            return None
        key = ("full", shape, w, h)
        hit = self._band_cache.get(key)
        if hit is None:
            scaled = img.scaled(w, h, Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            hit = scaled.copy((scaled.width() - w) // 2,
                              (scaled.height() - h) // 2, w, h)
            self._band_cache[key] = hit
        return hit

    def _paint_classic(self, p, r: QRectF) -> None:
        pal = self.palette_
        d = self.data
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

        night = not d.is_day
        if self.size_class == "small":
            self._glyph(p, QRectF(r.right() - 34, r.top() + 14, 30, 30), shape,
                        night, photo)
            self.text(p, QRectF(r.left(), r.top() + 24, r.width(), 44),
                      f"{d.temp:.0f}°", 38, c_temp, QFont.DemiBold)
            self.text(p, QRectF(r.left(), r.bottom() - 18, r.width(), 18),
                      words, 9, c_words)
            return

        self._glyph(p, QRectF(r.left(), r.top() + 16, 40, 40), shape, night, photo)
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
    # ------------------------------------------------------------------ #
    #  Apple-style faces
    # ------------------------------------------------------------------ #
    # Sized from Apple's Mac widgets scaled to this card: a 170 pt widget with
    # 13 pt titles and a 40 pt temperature is a 10 pt title and a 30 pt
    # temperature on a 168 px card. The temperature is regular weight, not
    # bold - Apple sets the big number light and lets size carry it.
    def _fit(self, p, s: str, size: float, weight, width: float) -> str:
        p.setFont(self.font(size, weight))
        return p.fontMetrics().elidedText(s, Qt.ElideRight, int(width))

    def _head(self, p, r: QRectF) -> None:
        """Place name on the left, the condition's icon on the right."""
        d, pal = self.data, self.palette_
        place = (d.place or "Weather").split(",")[0]
        self.text(p, QRectF(r.left(), r.top() - 2, r.width() - 26, 18),
                  self._fit(p, place, 10.5, QFont.DemiBold, r.width() - 26),
                  10.5, pal.text, QFont.DemiBold)
        self._glyph(p, QRectF(r.right() - 20, r.top() - 3, 20, 20),
                    describe(d.code)[0], not d.is_day)

    def _temp_range(self, p, r: QRectF, top: float) -> None:
        """The big temperature, today's high and low stacked at the right."""
        d, pal = self.data, self.palette_
        self.text(p, QRectF(r.left() - 1, top, r.width(), 42), _deg(d.temp),
                  30, pal.text, QFont.Normal)
        if d.hi is not None and d.lo is not None:
            right = Qt.AlignRight | Qt.AlignVCenter
            self.text(p, QRectF(r.left(), top + 5, r.width(), 16),
                      f"↑{_deg(d.hi)}", 10, pal.text, QFont.DemiBold, right)
            self.text(p, QRectF(r.left(), top + 22, r.width(), 16),
                      f"↓{_deg(d.lo)}", 10, pal.text_dim, QFont.DemiBold, right)

    def _paint_conditions(self, p, r: QRectF) -> None:
        """Small: temperature and range, then precipitation and wind."""
        d, pal = self.data, self.palette_
        self._head(p, r)
        self._temp_range(p, r, r.top() + 14)
        rows = (("Precipitation", precip_summary(d)),
                ("Wind", _wind(d)))
        y = r.bottom() - 62
        for title, value in rows:
            self.text(p, QRectF(r.left(), y, r.width(), 15), title, 9.5,
                      pal.text, QFont.DemiBold)
            self.text(p, QRectF(r.left(), y + 15, r.width(), 15),
                      self._fit(p, value, 9.5, QFont.DemiBold, r.width()),
                      9.5, pal.text_dim, QFont.DemiBold)
            y += 32

    def _paint_forecast(self, p, r: QRectF) -> None:
        """Small: temperature and range over the next four days."""
        d, pal = self.data, self.palette_
        self._head(p, r)
        self._temp_range(p, r, r.top() + 14)
        days = d.days[1:5]
        if not days:
            return
        top = r.top() + 58
        rh = (r.bottom() - top) / 4
        right = Qt.AlignRight | Qt.AlignVCenter
        for i, day in enumerate(days):
            y = top + i * rh
            self.text(p, QRectF(r.left(), y, 40, rh), day.label[:3], 10,
                      pal.text, QFont.DemiBold)
            icon = min(16.0, rh - 2)
            self._glyph(p, QRectF(r.left() + r.width() * 0.40 - icon / 2,
                                  y + (rh - icon) / 2, icon, icon),
                        describe(day.code)[0])
            self.text(p, QRectF(r.left(), y, r.width(), rh), _deg(day.hi), 10,
                      pal.text, QFont.DemiBold, right)
            self.text(p, QRectF(r.left(), y, r.width() - 34, rh),
                      f"{round(day.lo):.0f}", 10, pal.text_dim, QFont.DemiBold,
                      right)

    def _paint_nexthour(self, p, r: QRectF) -> None:
        """Small: what the next hour holds, in words and in bars."""
        d, pal = self.data, self.palette_
        self._head(p, r)
        # Keep "15 min" together when the line wraps.
        words = next_hour_summary(d).replace(" min", " min")
        if not words:
            self.text(p, QRectF(r.left(), r.top() + 20, r.width(), 60),
                      "Next-hour precipitation isn't available here", 10,
                      pal.text_dim, QFont.DemiBold,
                      Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap)
            return
        self.text(p, QRectF(r.left(), r.top() + 18, r.width(), 36), words, 10.5,
                  pal.text, QFont.DemiBold,
                  Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap)
        chart = QRectF(r.left(), r.top() + 62, r.width(), r.height() - 62 - 16)
        self._rain_bars(p, chart, 15)
        foot = QRectF(r.left(), r.bottom() - 14, r.width(), 14)
        self.text(p, foot, "Now", 8.5, pal.text_dim, QFont.DemiBold)
        self.text(p, foot, "60m", 8.5, pal.text_dim, QFont.DemiBold,
                  Qt.AlignRight | Qt.AlignVCenter)

    def _rain_bars(self, p, chart: QRectF, n: int, gap: float = 3.0) -> None:
        """Bars standing on the chart's floor, one per slice of the hour, under
        the dashed lines Apple draws at the light and heavy marks."""
        pal = self.palette_
        dark = pal.dark
        guide = QColor(pal.text_faint)
        p.save()
        p.setPen(QPen(guide, 1.0, Qt.DashLine))
        for frac in (1 / 3, 2 / 3):
            y = round(chart.bottom() - chart.height() * frac) + 0.5
            p.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
        p.restore()

        bw = (chart.width() - gap * (n - 1)) / n
        grad = QLinearGradient(0, chart.top(), 0, chart.bottom())
        grad.setColorAt(0.0, QColor("#c4f0ff" if dark else "#1c9ddb"))
        grad.setColorAt(1.0, QColor(SKY_BAR if dark else "#62c4f0"))
        p.save()
        p.setPen(Qt.NoPen)
        for i, level in enumerate(rain_levels(self.data, n)):
            x = chart.left() + i * (bw + gap)
            if level <= 0:
                p.setBrush(guide)
                p.drawRoundedRect(QRectF(x, chart.bottom() - 2, bw, 2), 1, 1)
                continue
            h = max(3.0, chart.height() * level)
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(x, chart.bottom() - h, bw, h),
                              min(bw / 2, 2.5), min(bw / 2, 2.5))
        p.restore()

    # ------------------------------------------------------------------ #
    #  Medium and large faces
    # ------------------------------------------------------------------ #
    def _wide_head(self, p, r: QRectF, aside: str = "") -> None:
        """Place and icon, the big temperature, and at the right either the
        condition over 'H:22° L:14°' or, when given, one line of its own."""
        d, pal = self.data, self.palette_
        self._head(p, r)
        self.text(p, QRectF(r.left() - 1, r.top() + 14, r.width(), 42),
                  _deg(d.temp), 30, pal.text, QFont.Normal)
        right = Qt.AlignRight | Qt.AlignVCenter
        room = r.width() * 0.62
        box = QRectF(r.right() - room, r.top(), room, 0)
        if aside:
            self.text(p, QRectF(box.left(), r.top() + 26, room, 20),
                      self._fit(p, aside, 10.5, QFont.DemiBold, room),
                      10.5, pal.text, QFont.DemiBold, right)
            return
        words = describe(d.code)[1]
        self.text(p, QRectF(box.left(), r.top() + 20, room, 17), words, 10,
                  pal.text, QFont.DemiBold, right)
        if d.hi is not None and d.lo is not None:
            self.text(p, QRectF(box.left(), r.top() + 37, room, 17),
                      f"H:{_deg(d.hi)} L:{_deg(d.lo)}", 10, pal.text,
                      QFont.DemiBold, right)

    def _rule(self, p, r: QRectF, y: float) -> None:
        c = QColor(self.palette_.text)
        c.setAlphaF(0.22)
        p.save()
        p.setPen(QPen(c, 1.0))
        y = round(y) + 0.5
        p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.restore()

    def _hourly_row(self, p, row: QRectF, n: int = 6) -> None:
        """Time, icon and temperature per hour; a sunrise or sunset takes its
        own slot at its own time, as in Apple's row."""
        d, pal = self.data, self.palette_
        hours = d.hours[:n]
        if not hours:
            self.text(p, row, "No hourly forecast", 9.5, pal.text_dim,
                      QFont.DemiBold, Qt.AlignCenter)
            return
        cw = row.width() / n
        centre = Qt.AlignHCenter | Qt.AlignVCenter
        icon = min(22.0, row.height() - 34)
        for i, h in enumerate(hours):
            x = row.left() + i * cw
            self.text(p, QRectF(x - 4, row.top(), cw + 8, 14), h.label, 8.5,
                      pal.text_dim, QFont.DemiBold, centre)
            shape = (SUNDOWN if h.event == SUNSET else
                     SUNUP if h.event == SUNRISE else describe(h.code)[0])
            mid = row.top() + 14 + (row.height() - 30 - icon) / 2
            self._glyph(p, QRectF(x + cw / 2 - icon / 2, mid, icon, icon),
                        shape, not h.is_day)
            self.text(p, QRectF(x, row.bottom() - 16, cw, 16), _deg(h.temp),
                      10.5, pal.text, QFont.DemiBold, centre)

    def _rain_chart(self, p, area: QRectF, n: int) -> None:
        """'Next-Hour Precipitation', the bars, and minute marks under them."""
        pal = self.palette_
        self.text(p, QRectF(area.left(), area.top(), area.width(), 15),
                  "Next-Hour Precipitation", 9, pal.text_dim, QFont.DemiBold)
        lead = 21 if area.height() > 100 else 16
        chart = QRectF(area.left(), area.top() + lead, area.width(),
                       area.height() - lead - 17)
        self._rain_bars(p, chart, n, gap=2.0)
        tick = QColor(pal.text)
        tick.setAlphaF(0.35)
        p.save()
        p.setPen(QPen(tick, 1.0))
        for i in range(6):
            x = round(chart.left() + chart.width() * i / 6) + 0.5
            p.drawLine(QPointF(x, chart.bottom() + 1), QPointF(x, chart.bottom() + 4))
        p.restore()
        for i in range(6):
            x = chart.left() + chart.width() * i / 6
            self.text(p, QRectF(x + 3, chart.bottom() + 3, 40, 15),
                      "Now" if i == 0 else f"{i * 10}m", 9, pal.text_dim,
                      QFont.DemiBold)

    def _paint_hourly(self, p, r: QRectF) -> None:
        """Medium: the header, then the next six hours."""
        self._wide_head(p, r)
        self._hourly_row(p, QRectF(r.left(), r.top() + 64, r.width(),
                                   r.bottom() - r.top() - 64))

    def _paint_nexthour_wide(self, p, r: QRectF) -> None:
        """Medium: the header with the hour's sentence, then the chart.
        Large: the chart, the sentence under it, then the hourly row."""
        d, pal = self.data, self.palette_
        words = next_hour_summary(d)
        large = self.size_class == "large"
        if not words:
            self._wide_head(p, r)
            self.text(p, QRectF(r.left(), r.top() + 64, r.width(),
                                (r.height() - 64) if not large else 120),
                      "Next-hour precipitation isn't available here", 10,
                      pal.text_dim, QFont.DemiBold, Qt.AlignCenter)
            if large:
                self._rule(p, r, r.bottom() - 74)
                self._hourly_row(p, QRectF(r.left(), r.bottom() - 66,
                                           r.width(), 66))
            return
        if not large:
            self._wide_head(p, r, aside=words)
            self._rain_chart(p, QRectF(r.left(), r.top() + 50, r.width(),
                                       r.bottom() - r.top() - 50), 48)
            return
        self._wide_head(p, r)
        self._rain_chart(p, QRectF(r.left(), r.top() + 72, r.width(), 130), 48)
        self.text(p, QRectF(r.left(), r.top() + 208, r.width(), 22), words, 11,
                  pal.text, QFont.DemiBold)
        self._rule(p, r, r.bottom() - 74)
        self._hourly_row(p, QRectF(r.left(), r.bottom() - 66, r.width(), 66))

    def _paint_daily(self, p, r: QRectF) -> None:
        """Large: header, the hourly row, then five days with range bars."""
        d, pal = self.data, self.palette_
        self._wide_head(p, r)
        self._rule(p, r, r.top() + 64)
        self._hourly_row(p, QRectF(r.left(), r.top() + 72, r.width(), 66))
        self._rule(p, r, r.top() + 148)
        days = d.days[:5]
        if not days:
            return
        top = r.top() + 152
        rh = (r.bottom() - top) / 5
        w = r.width()
        lo_all = min(day.lo for day in days)
        hi_all = max(day.hi for day in days)
        if d.temp is not None:
            lo_all, hi_all = min(lo_all, d.temp), max(hi_all, d.temp)
        span = max(1e-6, hi_all - lo_all)
        bar_l, bar_r = r.left() + w * 0.44, r.left() + w * 0.83
        right = Qt.AlignRight | Qt.AlignVCenter
        for i, day in enumerate(days):
            y = top + i * rh
            self.text(p, QRectF(r.left(), y, w * 0.26, rh),
                      "Today" if i == 0 else day.label[:3], 10.5, pal.text,
                      QFont.DemiBold)
            icon = min(20.0, rh - 6)
            self._glyph(p, QRectF(r.left() + w * 0.29 - icon / 2,
                                  y + (rh - icon) / 2, icon, icon),
                        describe(day.code)[0])
            self.text(p, QRectF(r.left(), y, bar_l - r.left() - 8, rh),
                      _deg(day.lo), 10.5, pal.text_dim, QFont.DemiBold, right)
            self.text(p, QRectF(r.left(), y, w, rh), _deg(day.hi), 10.5,
                      pal.text, QFont.DemiBold, right)
            cy = y + rh / 2
            self._range_bar(p, bar_l, bar_r, cy,
                            (day.lo - lo_all) / span, (day.hi - lo_all) / span,
                            day.lo, day.hi,
                            (d.temp - lo_all) / span if i == 0 and d.temp is not None
                            else None)

    def _range_bar(self, p, left: float, right: float, cy: float, f0: float,
                   f1: float, t0: float, t1: float, now) -> None:
        """A day's low-to-high on the week's scale, coloured by temperature
        the way Apple colours it, with a dot for now on today's bar."""
        pal = self.palette_
        h = 4.0
        track = QColor(pal.text)
        track.setAlphaF(0.14)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(left, cy - h / 2, right - left, h), h / 2, h / 2)
        x0 = left + (right - left) * f0
        x1 = max(x0 + h, left + (right - left) * f1)
        grad = QLinearGradient(x0, 0, x1, 0)
        grad.setColorAt(0.0, _temp_colour(self._celsius(t0)))
        grad.setColorAt(1.0, _temp_colour(self._celsius(t1)))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(x0, cy - h / 2, x1 - x0, h), h / 2, h / 2)
        if now is not None:
            nx = left + (right - left) * now
            p.setBrush(QColor(0, 0, 0, 90) if pal.dark else QColor(255, 255, 255, 200))
            p.drawEllipse(QPointF(nx, cy), 5, 5)
            p.setBrush(QColor("#ffffff") if pal.dark else QColor("#1d1d1f"))
            p.drawEllipse(QPointF(nx, cy), 3.2, 3.2)
        p.restore()

    def _celsius(self, t: float) -> float:
        return (t - 32) / 1.8 if self.unit == "F" else t

    def _paint_sun(self, p, r: QRectF) -> None:
        """Small: the next sunrise or sunset, the sun on its day-long arc."""
        d, pal = self.data, self.palette_
        rise, sett, now = d.sunrise_min, d.sunset_min, d.now_min
        if rise is None or sett is None or now is None or sett <= rise:
            self._head(p, r)
            self.text(p, QRectF(r.left(), r.top() + 20, r.width(), r.height() - 20),
                      "No sunrise or sunset here today", 10, pal.text_dim,
                      QFont.DemiBold, Qt.AlignCenter | Qt.TextWordWrap)
            return
        setting = rise <= now < sett
        title = "SUNSET" if setting else "SUNRISE"
        when = (d.sunset_next if setting else d.sunrise_next) or \
            _clock_min(sett if setting else rise)
        other = (f"Sunrise: {_clock_min(rise)}" if setting
                 else f"Sunset: {_clock_min(sett)}")

        self._glyph(p, QRectF(r.left() - 1, r.top() - 3, 18, 18),
                    SUNDOWN if setting else SUNUP)
        self.text(p, QRectF(r.left() + 21, r.top() - 2, r.width(), 16), title,
                  10, pal.text, QFont.DemiBold)
        hm, _, ampm = when.partition(" ")
        p.setFont(self.font(24, QFont.Medium))
        tw = p.fontMetrics().horizontalAdvance(hm)
        self.text(p, QRectF(r.left() - 1, r.top() + 14, r.width(), 34), hm, 24,
                  pal.text, QFont.Medium)
        self.text(p, QRectF(r.left() + tw + 1, r.top() + 23, 40, 22), ampm, 12,
                  pal.text, QFont.DemiBold)

        self._sun_arc(p, rise, sett, now, horizon=r.top() + 94)
        self.text(p, QRectF(r.left(), r.bottom() - 15, r.width(), 15), other,
                  9.5, pal.text, QFont.DemiBold)

    def _sun_arc(self, p, rise: int, sett: int, now: int, horizon: float) -> None:
        """The sun's height through the whole day as a wave across the card,
        the horizon where it crosses at sunrise and sunset. A cosine centred
        on solar noon: the real path is close to one, and it is what Apple
        draws."""
        pal = self.palette_
        card = self.card_rect()
        noon = (rise + sett) / 2.0
        c0 = math.cos(2 * math.pi * (rise - noon) / 1440.0)
        k = min(30.0 / max(0.05, 1 - c0), 26.0 / max(0.05, 1 + c0))

        def y_at(m: float) -> float:
            return horizon - k * (math.cos(2 * math.pi * (m - noon) / 1440.0) - c0)

        def x_at(m: float) -> float:
            return card.left() + card.width() * m / 1440.0

        wave = QPainterPath(QPointF(x_at(0), y_at(0)))
        for i in range(1, 97):
            m = 1440.0 * i / 96
            wave.lineTo(x_at(m), y_at(m))

        p.save()
        p.setClipPath(squircle(card, RADIUS))
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 34 if pal.dark else 18))
        p.drawRect(QRectF(card.left(), horizon, card.width(),
                          card.bottom() - horizon))
        line = QColor(pal.text)
        line.setAlphaF(0.30)
        p.setPen(QPen(line, 1.0))
        p.drawLine(QPointF(card.left(), horizon + 0.5),
                   QPointF(card.right(), horizon + 0.5))

        sky = QColor(pal.text)
        sky.setAlphaF(0.55)
        under = QColor(pal.text)
        under.setAlphaF(0.22)
        for colour, clip in ((sky, QRectF(card.left(), card.top(), card.width(),
                                          horizon - card.top())),
                             (under, QRectF(card.left(), horizon, card.width(),
                                            card.bottom() - horizon))):
            p.save()
            p.setClipRect(clip, Qt.IntersectClip)
            p.setPen(QPen(colour, 2.4, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawPath(wave)
            p.restore()

        sx, sy = x_at(now), y_at(now)
        up = sy <= horizon
        glow = QRadialGradient(QPointF(sx, sy), 13)
        glow.setColorAt(0.0, QColor(255, 255, 255, 120 if up else 40))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(sx, sy), 13, 13)
        dot = QColor("#ffffff") if pal.dark or up else QColor(pal.text_dim)
        if not up:
            dot.setAlphaF(0.6)
        p.setBrush(dot)
        if not pal.dark:
            p.setPen(QPen(QColor(0, 0, 0, 60), 1.0))
        p.drawEllipse(QPointF(sx, sy), 5, 5)
        p.restore()

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
    def _glyph(self, p, box: QRectF, shape: int, night: bool = False,
               on_photo: bool = False) -> None:
        # Over a photograph the header is always light text on a dark scrim,
        # so its icon takes the dark-theme colours whatever the theme.
        draw_icon(p, box, shape, night=night,
                  dark=self.palette_.dark or on_photo)


# --------------------------------------------------------------------------- #
#  Icons
# --------------------------------------------------------------------------- #
# Flat, filled shapes in the manner of Apple's Weather symbols: a solid yellow
# sun with square-cut rays, flat-bottomed clouds, a crescent moon with
# sparkles, blue rain dashes. Where one shape overlaps another (the sun behind
# a cloud, a bolt through one) the back shape is cut away around the front one
# with a thin gap rather than drawn underneath it, which is what keeps them
# readable at 20 px.
#
# Everything is drawn on a 100-unit square and scaled to the box, as paths -
# no icon font and no image files, so they stay sharp at any size and DPI.
_ICON_DARK = {
    "sun": "#ffd60a", "cloud": "#d3d6dc", "heavy": "#8e939e",
    "ink": "#ffffff", "wet": "#4cb8f5", "white": "#ffffff",
}
# On a light card pale grey would barely show, so the set moves a step darker.
# Lightning and snow stay white in both themes; on a light card they get a
# thin grey outline instead, or a white flake would vanish into white glass.
_ICON_LIGHT = {
    "sun": "#f5bf00", "cloud": "#adb4bf", "heavy": "#717885",
    "ink": "#475063", "wet": "#1e9be6", "white": "#ffffff",
}
_GAP = 4.0          # units cut between overlapping shapes


def _stroke(path: QPainterPath, width: float) -> QPainterPath:
    s = QPainterPathStroker()
    s.setWidth(width)
    s.setCapStyle(Qt.FlatCap)
    s.setJoinStyle(Qt.MiterJoin)
    return s.createStroke(path)


def _grow(path: QPainterPath, by: float) -> QPainterPath:
    """The shape plus a band `by` units wide all round - what gets cut away."""
    return path.united(_stroke(path, by * 2)).simplified()


def _cloud(cx: float = 50, cy: float = 49, k: float = 1.0) -> QPainterPath:
    """Flat-bottomed cloud: one big puff, one small one, on a pill.

    Its bounding box is centred on (cx, cy); at k=1 it is 72 x 54 units.
    """
    path = QPainterPath()
    path.addRoundedRect(QRectF(14, 48, 72, 28), 14, 14)
    puffs = QPainterPath()
    puffs.addEllipse(QPointF(43, 44), 22, 22)
    puffs.addEllipse(QPointF(66, 53), 15, 15)
    puffs.setFillRule(Qt.WindingFill)
    path = path.united(puffs)
    t = QTransform()
    t.translate(cx, cy)
    t.scale(k, k)
    t.translate(-50, -49)
    path = t.map(path)
    # Overlaps would come out as holes under the default odd-even rule.
    path.setFillRule(Qt.WindingFill)
    return path.simplified()


def _sun(cx: float, cy: float, r: float, ray_in: float, ray_out: float,
         ray_w: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    rays = QPainterPath()
    for i in range(8):
        a = math.radians(i * 45)
        rays.moveTo(cx + math.cos(a) * ray_in, cy + math.sin(a) * ray_in)
        rays.lineTo(cx + math.cos(a) * ray_out, cy + math.sin(a) * ray_out)
    return path.united(_stroke(rays, ray_w))


def _moon(cx: float, cy: float, r: float) -> QPainterPath:
    disc = QPainterPath()
    disc.addEllipse(QPointF(cx, cy), r, r)
    bite = QPainterPath()
    bite.addEllipse(QPointF(cx + r * 0.64, cy - r * 0.48), r * 0.84, r * 0.84)
    return disc.subtracted(bite)


def _sparkle(cx: float, cy: float, r: float) -> QPainterPath:
    """A four-pointed star with concave sides."""
    c = r * 0.16
    path = QPainterPath(QPointF(cx, cy - r))
    path.quadTo(cx + c, cy - c, cx + r, cy)
    path.quadTo(cx + c, cy + c, cx, cy + r)
    path.quadTo(cx - c, cy + c, cx - r, cy)
    path.quadTo(cx - c, cy - c, cx, cy - r)
    path.closeSubpath()
    return path


def _snowflake(cx: float, cy: float, arm: float, w: float) -> QPainterPath:
    lines = QPainterPath()
    for i in range(3):
        a = math.radians(90 + i * 60)
        dx, dy = math.cos(a), math.sin(a)
        lines.moveTo(cx - dx * arm, cy - dy * arm)
        lines.lineTo(cx + dx * arm, cy + dy * arm)
        # A V near each end of the arm, opening outwards.
        for sign in (1, -1):
            bx, by = cx + sign * dx * arm * 0.55, cy + sign * dy * arm * 0.55
            out = a if sign > 0 else a + math.pi
            for turn in (40, -40):
                b = out + math.radians(turn)
                lines.moveTo(bx, by)
                lines.lineTo(bx + math.cos(b) * arm * 0.38,
                             by + math.sin(b) * arm * 0.38)
    path = _stroke(lines, w)
    path.setFillRule(Qt.WindingFill)
    return path.simplified()


def _dashes(points, w: float = 4.6, length: float = 11.0) -> QPainterPath:
    """Rain: short strokes leaning forward, each from its top end."""
    lines = QPainterPath()
    dx, dy = -length * 0.42, length * 0.91
    for x, y in points:
        lines.moveTo(x, y)
        lines.lineTo(x + dx, y + dy)
    return _stroke(lines, w)


def icon_paths(shape: int, night: bool = False):
    """The icon as (colour role, path) pairs in 100-unit space, back to front."""
    if shape == CLEAR:
        if night:
            return [("ink", _moon(46, 55, 25)),
                    ("ink", _sparkle(77, 29, 9)),
                    ("ink", _sparkle(63, 16, 5))]
        return [("sun", _sun(50, 50, 17, 25, 38, 5.0))]

    if shape == PARTLY:
        if night:
            cloud = _cloud(45, 62, 0.8)
            cut = _grow(cloud, _GAP)
            return [("ink", _moon(70, 34, 14).subtracted(cut)),
                    ("ink", _sparkle(22, 30, 5).subtracted(cut)),
                    ("ink", _sparkle(32, 19, 3.4)),
                    ("heavy", cloud)]
        cloud = _cloud(59, 62, 0.8)
        return [("sun", _sun(37, 36, 13, 19, 28, 4.4)
                 .subtracted(_grow(cloud, _GAP))),
                ("cloud", cloud)]

    if shape == OVERCAST:
        return [("heavy", _cloud())]

    if shape == RAIN:
        cloud = _cloud(50, 39)
        return [("cloud", cloud),
                ("wet", _dashes([(32, 72), (46, 77), (60, 72), (74, 77)]))]

    if shape == STORM:
        cloud = _cloud(50, 40)
        bolt = QPainterPath(QPointF(56, 36))
        for x, y in ((37, 64), (49, 64), (42, 92), (67, 55), (55, 55),
                     (63, 36)):
            bolt.lineTo(x, y)
        bolt.closeSubpath()
        return [("cloud", cloud.subtracted(_grow(bolt, _GAP))),
                ("white", bolt)]

    if shape == SNOW:
        return [("white", _snowflake(50, 50, 32, 4.6))]

    if shape == SLEET:
        cloud = _cloud(50, 36, 0.92)
        flake = _snowflake(50, 63, 21, 4.2)
        return [("cloud", cloud.subtracted(_grow(flake, _GAP))),
                ("white", flake),
                ("wet", _dashes([(24, 70), (30, 82), (78, 70), (84, 82)],
                                4.2, 9.0))]

    if shape == FOG:
        cloud = _cloud(50, 37)
        lines = QPainterPath()
        for y, half in ((74, 24), (84, 24), (94, 16)):
            lines.moveTo(50 - half, y)
            lines.lineTo(50 + half, y)
        return [("cloud", cloud), ("heavy", _stroke(lines, 4.0))]

    if shape in (SUNUP, SUNDOWN):
        line = QPainterPath()
        line.moveTo(8, 80)
        line.lineTo(92, 80)
        half = QPainterPath()
        half.addEllipse(QPointF(50, 80), 15, 15)
        above = QPainterPath()
        above.addRect(QRectF(0, 0, 100, 80))
        rays = QPainterPath()
        for deg in (-160, -125, -90, -55, -20):
            a = math.radians(deg)
            rays.moveTo(50 + math.cos(a) * 21, 80 + math.sin(a) * 21)
            rays.lineTo(50 + math.cos(a) * 29, 80 + math.sin(a) * 29)
        sun = half.intersected(above).united(_stroke(rays, 4.6))
        arrow = QPainterPath()
        tip, tail = (8, 38) if shape == SUNUP else (38, 8)
        arrow.moveTo(50, tail)
        arrow.lineTo(50, tip)
        step = 9 if shape == SUNUP else -9
        arrow.moveTo(50 - 9, tip + step)
        arrow.lineTo(50, tip)
        arrow.lineTo(50 + 9, tip + step)
        # The ray straight up would run into the arrow's tail.
        sun = sun.subtracted(_grow(_stroke(arrow, 4.6), _GAP))
        return [("sun", sun), ("ink", _stroke(line, 4.6)),
                ("ink", _stroke(arrow, 4.6))]

    return [("cloud", _cloud())]


def draw_icon(p: QPainter, box: QRectF, shape: int, night: bool = False,
              dark: bool = True) -> None:
    colours = _ICON_DARK if dark else _ICON_LIGHT
    s = min(box.width(), box.height()) / 100.0
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(box.center().x() - 50 * s, box.center().y() - 50 * s)
    p.scale(s, s)
    p.setPen(Qt.NoPen)
    for role, path in icon_paths(shape, night):
        if role == "white" and not dark:
            p.setBrush(QColor(colours["heavy"]))
            p.drawPath(_grow(path, 2.4))
        p.setBrush(QColor(colours[role]))
        p.drawPath(path)
    p.restore()
