"""Clock: a set of faces per size, modelled on the Mac's Clock widgets.

Small
    Minimal     the original: ticks and hands on the card itself
    Classic     Apple's white dial - numerals, minute track, orange second hand
    City        a bold dial with twelve bars and the city's code
    Digital     the time set huge, with a ring of second ticks round the edge
    World       four little dials for four cities

Medium
    Digital     the original: date above, the time set large
    Flip        a split-flap clock whose cards fall when the minute turns
    World       four cities in a row, each with its offset from here

"classic" is the key of each size's original face, so a saved layout looks
exactly as it did until someone picks another face.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainterPath, QPen

from ..core.card import PAD, Fader, WidgetCard

# The time's typography, offered in the right-click menu. Qt synthesises any
# face the font lacks, so these all work on the fallback stack too.
TIME_WEIGHTS = (
    ("Thin", QFont.Thin), ("Light", QFont.Light), ("Regular", QFont.Normal),
    ("Medium", QFont.Medium), ("Semibold", QFont.DemiBold),
    ("Bold", QFont.Bold), ("Heavy", QFont.Black),
)
TIME_WIDTHS = (("Condensed", 78), ("Normal", 100), ("Wide", 122), ("Extra wide", 140))
DEFAULT_WEIGHT = QFont.DemiBold

# Apple's clock orange - the second hand on every Mac clock face.
ORANGE = "#ff9500"
# Apple's Digital clock widget sets the time in SF Pro at its compressed width,
# which may not be shipped with a Windows app. Oswald (fonts/, SIL OFL) is the
# closest free match - tall, compressed, heavy numerals - at its Bold, 700.
DIGITAL_FAMILY = "Oswald"
DIGITAL_WEIGHT = 700
DIAL_DAY = "#ffffff"
DIAL_NIGHT = "#2c2c2e"
INK_DAY = "#000000"
INK_NIGHT = "#ffffff"

# --------------------------------------------------------------------------- #
#  Cities
# --------------------------------------------------------------------------- #
# key: (name, three-letter code, IANA zone, standard UTC offset in hours).
# The offset is only a fallback for a Python with no time zone database
# (zoneinfo needs the tzdata package on Windows); it ignores daylight saving.
CITIES = {
    "local":       ("Local",        "",    None,                   None),
    "cupertino":   ("Cupertino",    "CUP", "America/Los_Angeles",  -8),
    "los_angeles": ("Los Angeles",  "LA",  "America/Los_Angeles",  -8),
    "denver":      ("Denver",       "DEN", "America/Denver",       -7),
    "chicago":     ("Chicago",      "CHI", "America/Chicago",      -6),
    "new_york":    ("New York",     "NYC", "America/New_York",     -5),
    "toronto":     ("Toronto",      "TOR", "America/Toronto",      -5),
    "sao_paulo":   ("São Paulo",    "SAO", "America/Sao_Paulo",    -3),
    "london":      ("London",       "LON", "Europe/London",         0),
    "paris":       ("Paris",        "PAR", "Europe/Paris",          1),
    "berlin":      ("Berlin",       "BER", "Europe/Berlin",         1),
    "cape_town":   ("Cape Town",    "CT",  "Africa/Johannesburg",   2),
    "cairo":       ("Cairo",        "CAI", "Africa/Cairo",          2),
    "istanbul":    ("Istanbul",     "IST", "Europe/Istanbul",       3),
    "dubai":       ("Dubai",        "DXB", "Asia/Dubai",            4),
    "karachi":     ("Karachi",      "KHI", "Asia/Karachi",          5),
    "mumbai":      ("Mumbai",       "BOM", "Asia/Kolkata",          5.5),
    "dhaka":       ("Dhaka",        "DAC", "Asia/Dhaka",            6),
    "bangkok":     ("Bangkok",      "BKK", "Asia/Bangkok",          7),
    "singapore":   ("Singapore",    "SIN", "Asia/Singapore",        8),
    "hong_kong":   ("Hong Kong",    "HKG", "Asia/Hong_Kong",        8),
    "tokyo":       ("Tokyo",        "TYO", "Asia/Tokyo",            9),
    "seoul":       ("Seoul",        "SEL", "Asia/Seoul",            9),
    "sydney":      ("Sydney",       "SYD", "Australia/Sydney",     10),
    "auckland":    ("Auckland",     "AKL", "Pacific/Auckland",     12),
}
# Apple's own line-up for the World Clock widget.
DEFAULT_CITIES = ("cupertino", "paris", "cape_town", "new_york")

_zones: dict = {}


def _zone(name: str):
    if name not in _zones:
        try:
            from zoneinfo import ZoneInfo
            _zones[name] = ZoneInfo(name)
        except Exception:
            _zones[name] = None
    return _zones[name]


def city_time(key: str, now: datetime) -> datetime:
    """``now`` (aware) as the wall clock reads in that city."""
    name, _code, zone, fixed = CITIES.get(key, CITIES["local"])
    if zone is None:
        return now.astimezone()
    tz = _zone(zone)
    if tz is None:
        tz = timezone(timedelta(hours=fixed))
    return now.astimezone(tz)


def is_night(t: datetime) -> bool:
    """Apple darkens a city's dial between six in the evening and six in the
    morning, so a row of world clocks shows at a glance who's asleep."""
    return t.hour < 6 or t.hour >= 18


def _offset_label(there: datetime, here: datetime) -> str:
    hours = ((there.utcoffset() or timedelta()) - (here.utcoffset() or timedelta())
             ).total_seconds() / 3600.0
    text = f"{hours:+.1f}".rstrip("0").rstrip(".")
    return f"{text}HRS"


def _day_label(there: datetime, here: datetime) -> str:
    d = (there.date() - here.date()).days
    return "Today" if d == 0 else ("Tomorrow" if d > 0 else "Yesterday")


def _ink(hex_colour: str, alpha: int) -> QColor:
    c = QColor(hex_colour)
    c.setAlpha(alpha)
    return c


# --------------------------------------------------------------------------- #
#  The tile
# --------------------------------------------------------------------------- #
class ClockWidget(WidgetCard):
    kind = "clock"
    title = "Clock"
    customizable = True
    allow_image = True     # a photo can sit behind the time
    supported_sizes = ("small", "medium")
    refresh_ms = 1000        # second hands need it; the tick decides what repaints

    # A face with a second hand moves every second; the digital and flip faces
    # only every minute - so the tick decides, not the timer. Repainting a
    # medium clock 59 times for every time it changes is work nobody sees.
    repaint_on_tick = False

    FACES = {
        "small": (("classic", "Minimal"), ("dial", "Classic"), ("city", "City"),
                  ("digital", "Digital"), ("world", "World clock")),
        "medium": (("classic", "Digital"), ("flip", "Flip"), ("world", "World clock")),
    }
    # The faces whose picture changes every second.
    SECONDS = {"small": {"classic", "dial", "city", "digital", "world"},
               "medium": {"world"}}

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._shown = None          # the digital block currently on the card
        self._prev = None           # the one dissolving out of it
        self._fade = Fader(self, fps=30)
        self._fade.finish()
        # The flip face: what each card shows, what it showed, and the fall.
        self._flip_shown = None
        self._flip_prev = None
        self._flip = Fader(self, fps=None)
        self._flip.finish()

    # ------------------------------------------------------------------ #
    #  Faces
    # ------------------------------------------------------------------ #
    def available_faces(self):
        return list(self.FACES.get(self.size_class, ()))

    @property
    def face(self) -> str:
        chosen = (self.config.get("faces") or {}).get(self.size_class, "classic")
        return chosen if chosen in dict(self.available_faces()) else "classic"

    def set_face(self, key: str) -> None:
        if key not in dict(self.available_faces()) or key == self.face:
            return
        faces = dict(self.config.get("faces") or {})
        faces[self.size_class] = key
        self.config["faces"] = faces
        self._shown = self._prev = self._flip_shown = self._flip_prev = None
        self.changed.emit()
        self.on_tick()
        self.update()

    def set_size_class(self, size: str) -> None:
        super().set_size_class(size)
        self._shown = self._prev = self._flip_shown = self._flip_prev = None

    @property
    def cities(self) -> list:
        """The four cities the world faces show: the saved ones, topped up
        from Apple's defaults."""
        saved = [c for c in (self.config.get("cities") or ()) if c in CITIES][:4]
        return saved + [c for c in DEFAULT_CITIES if c not in saved][:4 - len(saved)]

    def set_city(self, slot: int, key: str) -> None:
        cities = list(self.cities)
        if key not in CITIES or cities[slot] == key:
            return
        cities[slot] = key
        self.config["cities"] = cities
        self.changed.emit()
        self.update()

    # ------------------------------------------------------------------ #
    def on_tick(self) -> None:
        face = self.face
        if face in self.SECONDS.get(self.size_class, ()):
            self.update()           # a second hand really does move each tick
            return
        now = datetime.now()
        if face == "flip":
            block = self._flip_block(now)
            if block == self._flip_shown:
                return
            self._flip_prev, self._flip_shown = self._flip_shown, block
            if self._flip_prev is None:
                self._flip.finish()
            else:
                self._flip.start(560.0)
            self.update()
            return

        block = self._block(now)
        if block == self._shown:
            return                  # nothing to show; nothing to draw
        self._prev, self._shown = self._shown, block
        if self._prev is None:
            self._fade.finish()     # first paint has nothing to dissolve from
        else:
            self._fade.start(320.0)
        self.update()

    @staticmethod
    def _hour12(now: datetime) -> str:
        return str(now.hour % 12 or 12)

    @classmethod
    def _block(cls, now: datetime):
        """Everything the digital face shows, as one comparable value."""
        return (now.strftime("%A, %d %B").replace(" 0", " "),
                f"{cls._hour12(now)}:{now.minute:02d}", now.strftime("%p").upper())

    @classmethod
    def _flip_block(cls, now: datetime):
        return (cls._hour12(now), f"{now.minute:02d}", now.strftime("%p").upper())

    def paint_content(self, p, r: QRectF) -> None:
        now = datetime.now()
        face = self.face
        if self.size_class == "small":
            {"classic": self._analog, "dial": self._paint_dial,
             "city": self._paint_city, "digital": self._paint_digital_ring,
             "world": self._paint_world_grid}[face](p, r, now)
            return
        if face == "flip":
            if self._flip_shown is None:
                self._flip_shown = self._flip_block(now)
            self._paint_flip(p, r)
        elif face == "world":
            self._paint_world_row(p, r, now)
        else:
            if self._shown is None:
                self._shown = self._block(now)
            self._digital(p, r)

    # ------------------------------------------------------------------ #
    #  Small: Minimal (the original analog face)
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
    #  Apple dials, shared by Classic, City and both World faces
    # ------------------------------------------------------------------ #
    def _dial(self, p, c: QPointF, rad: float, t: datetime, *,
              numerals: bool = True, bars: bool = False, code: str = "",
              minute_track: bool = True) -> None:
        """One of Apple's clock dials, at ``c`` with radius ``rad``, showing
        wall time ``t``. White by day, graphite by night."""
        night = is_night(t)
        ink = INK_NIGHT if night else INK_DAY
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(DIAL_NIGHT if night else DIAL_DAY))
        p.drawEllipse(c, rad, rad)

        def at(angle_deg: float, dist: float) -> QPointF:
            a = math.radians(angle_deg - 90)
            return QPointF(c.x() + math.cos(a) * dist, c.y() + math.sin(a) * dist)

        if bars:
            # City: twelve heavy bars, no numerals.
            p.setPen(QPen(_ink(ink, 120), max(2.0, rad * 0.065), Qt.SolidLine, Qt.RoundCap))
            for i in range(12):
                p.drawLine(at(i * 30, rad * 0.70), at(i * 30, rad * 0.88))
        elif minute_track:
            for i in range(60):
                five = i % 5 == 0
                p.setPen(QPen(_ink(ink, 150 if five else 80),
                              max(0.8, rad * (0.022 if five else 0.014)),
                              Qt.SolidLine, Qt.FlatCap))
                p.drawLine(at(i * 6, rad * (0.86 if five else 0.89)), at(i * 6, rad * 0.95))

        if numerals:
            p.setFont(self.font(max(5.0, rad * 0.21), QFont.DemiBold))
            p.setPen(QColor(ink))
            dist = rad * (0.68 if minute_track else 0.76)
            box = rad * 0.34
            for n in range(1, 13):
                q = at(n * 30, dist)
                p.drawText(QRectF(q.x() - box, q.y() - box / 2 - 0.5, box * 2, box),
                           Qt.AlignCenter, str(n))

        if code:
            p.setFont(self.font(max(5.0, rad * (0.24 if bars else 0.17)), QFont.DemiBold))
            p.setPen(_ink(ink, 130))
            p.drawText(QRectF(c.x() - rad, c.y() - rad * (0.52 if bars else 0.48),
                              rad * 2, rad * 0.32), Qt.AlignCenter, code)

        # Hands: a slim stem off the centre, then the broad blade - Apple's
        # shape. Hour and minute in the ink, the second hand orange.
        hour = ((t.hour % 12) + t.minute / 60.0) * 30.0
        minute = (t.minute + t.second / 60.0) * 6.0
        second = t.second * 6.0
        for angle, length, blade in ((hour, 0.52, 0.075), (minute, 0.80, 0.060)):
            stem_end = at(angle, rad * 0.16)
            p.setPen(QPen(QColor(ink), max(1.0, rad * 0.03), Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c, stem_end)
            p.setPen(QPen(QColor(ink), max(1.6, rad * blade), Qt.SolidLine, Qt.RoundCap))
            p.drawLine(stem_end, at(angle, rad * length))

        p.setPen(QPen(QColor(ORANGE), max(0.9, rad * 0.022), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(at(second + 180, rad * 0.18), at(second, rad * 0.90))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ink))
        p.drawEllipse(c, rad * 0.07, rad * 0.07)
        p.setBrush(QColor(ORANGE))
        p.drawEllipse(c, rad * 0.045, rad * 0.045)
        p.setBrush(QColor(DIAL_NIGHT if night else DIAL_DAY))
        p.drawEllipse(c, rad * 0.018, rad * 0.018)
        p.restore()

    @staticmethod
    def _full(r: QRectF, inset: float) -> QRectF:
        """The whole card, less ``inset`` - a dial makes its own margin, so the
        padded content rect would waste the room that lets it read."""
        return r.adjusted(-PAD + inset, -PAD + inset, PAD - inset, PAD - inset)

    def _heavy(self, size: float, wght: float, stretch: int) -> QFont:
        """A display weight that holds at any size. The weight is also set on
        Inter's own wght axis: some Qt builds draw a variable font at its
        default weight however heavy the QFont asks for, and these faces are
        nothing without their weight."""
        f = self.font(size, QFont.Black if wght >= 850 else QFont.Bold, stretch=stretch)
        try:
            f.setVariableAxis(QFont.Tag("wght"), float(wght))
        except Exception:
            pass
        return f

    def _digital_font(self, px: float) -> QFont:
        """Oswald Bold at ``px`` pixels, or Inter set heavy and narrow if the
        file is missing - the face still reads, just less like the Mac."""
        from PySide6.QtGui import QFontDatabase
        from ..core import typography
        typography.load_bundled()
        if self.config.get("font") in typography.FONT_CHOICES:
            f = self.font(60, QFont.Bold)          # the card's own font wins
            f.setPixelSize(int(px))
            return f
        if DIGITAL_FAMILY not in QFontDatabase.families():
            f = self._heavy(60, 860, 70)
            f.setPixelSize(int(px))
            return f
        f = QFont(DIGITAL_FAMILY)
        f.setPixelSize(int(px))
        f.setWeight(QFont.Bold)
        try:
            f.setVariableAxis(QFont.Tag("wght"), float(DIGITAL_WEIGHT))
        except Exception:
            pass
        # Set a touch tight, as Apple sets big figures: the colon tucks in.
        f.setLetterSpacing(QFont.PercentageSpacing, 95.0)
        f.setHintingPreference(QFont.PreferNoHinting)
        f.setStyleStrategy(QFont.StyleStrategy(QFont.PreferAntialias | QFont.PreferQuality))
        return f

    def _paint_dial(self, p, r: QRectF, now: datetime) -> None:
        box = self._full(r, 10)
        rad = min(box.width(), box.height()) / 2
        self._dial(p, box.center(), rad, now.astimezone())

    def _paint_city(self, p, r: QRectF, now: datetime) -> None:
        box = self._full(r, 12)
        rad = min(box.width(), box.height()) / 2
        key = self.cities[0]
        code = CITIES[key][1] or "HERE"
        self._dial(p, box.center(), rad, city_time(key, now.astimezone()),
                   numerals=False, bars=True, code=code)

    def _paint_world_grid(self, p, r: QRectF, now: datetime) -> None:
        box = self._full(r, 12)
        gap = 8.0
        cell = (min(box.width(), box.height()) - gap) / 2
        here = now.astimezone()
        for i, key in enumerate(self.cities):
            cx = box.left() + (i % 2) * (cell + gap) + cell / 2
            cy = box.top() + (i // 2) * (cell + gap) + cell / 2
            self._dial(p, QPointF(cx, cy), cell / 2, city_time(key, here),
                       code=CITIES[key][1], minute_track=False)

    # ------------------------------------------------------------------ #
    #  Small: Digital, with its ring of seconds
    # ------------------------------------------------------------------ #
    def _paint_digital_ring(self, p, r: QRectF, now: datetime) -> None:
        pal = self.palette_
        card = self._full(r, 0)
        cx, cy = card.center().x(), card.center().y()
        # Sixty ticks round the edge of the squircle, pointing in at the centre.
        # The current second is solid; the ones behind it fade away, so the ring
        # sweeps like a second hand.
        hw, hh = card.width() / 2 - 9, card.height() / 2 - 9
        n = 5.0                              # superellipse: a squircle's outline
        for i in range(60):
            a = math.radians(i * 6 - 90)
            ca, sa = math.cos(a), math.sin(a)
            rr = (abs(ca / hw) ** n + abs(sa / hh) ** n) ** (-1 / n)
            age = (now.second - i) % 60
            alpha = int(255 - (255 - 46) * (age / 59.0) ** 0.6)
            p.setPen(QPen(_ink(pal.text, alpha), 2.3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx + ca * rr, cy + sa * rr),
                       QPointF(cx + ca * (rr - 8), cy + sa * (rr - 8)))

        # Sized by its figures, the way Apple's are: about 0.48 of the card's
        # height tall, and never wider than the space inside the ring.
        text = f"{self._hour12(now)}:{now.minute:02d}"
        px = 100.0
        f = self._digital_font(px)
        path = QPainterPath()
        path.addText(0, 0, f, text)
        b = path.boundingRect()
        px *= min(card.height() * 0.48 / max(1.0, b.height()),
                  (card.width() - 52) / max(1.0, b.width()))
        f = self._digital_font(px)
        path = QPainterPath()
        path.addText(0, 0, f, text)
        path.setFillRule(Qt.WindingFill)
        b = path.boundingRect()
        p.save()
        p.translate(card.center().x() - b.center().x(), card.center().y() - b.center().y())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.text))
        p.drawPath(path)
        p.restore()

    # ------------------------------------------------------------------ #
    #  Medium: Digital (the original)
    # ------------------------------------------------------------------ #
    def _digital(self, p, r: QRectF) -> None:
        """12-hour, centred, with the date above. No timezone line.

        Dropping the timezone frees the bottom third, so the time can grow and
        sit on the card's true centre instead of being pushed up by a caption
        nobody reads.

        A minute change cross-fades. The whole block is dissolved rather than
        only the digits that differ, which sounds wasteful and is not: the hour
        blends with pixels identical to itself and so appears perfectly still,
        while the minute is the only thing that visibly moves.
        """
        t = self._fade.value
        if self._fade.active and self._prev is not None:
            self._draw_block(p, r, self._prev, 1.0 - t)
        self._draw_block(p, r, self._shown, t if self._fade.active else 1.0)

    # ------------------------------------------------------------------ #
    #  Medium: Flip
    # ------------------------------------------------------------------ #
    def _paint_flip(self, p, r: QRectF) -> None:
        """Two split-flap cards, hours and minutes.

        When a number changes its card falls: the top half of the old number
        folds down onto the hinge, uncovering the new number's top, then the
        new number's bottom half swings down over the old one. Each half is
        squashed toward the hinge to fake the turn - with a shade that deepens
        as it goes edge-on - which reads as a real flap at this size.
        """
        box = self._full(r, 14)
        gap = 10.0
        w = (box.width() - gap) / 2
        hour, minute, suffix = self._flip_shown
        prev = self._flip_prev if self._flip.active else None
        t = self._flip.value if prev else 1.0
        for i, (now_s, old_s) in enumerate(((hour, prev[0] if prev else hour),
                                            (minute, prev[1] if prev else minute))):
            card = QRectF(box.left() + i * (w + gap), box.top(), w, box.height())
            self._flap(p, card, now_s, old_s if old_s != now_s else None, t,
                       suffix if i == 0 else "")

    def _flap(self, p, card: QRectF, new: str, old, t: float, tag: str) -> None:
        radius = 10.0
        path = QPainterPath()
        path.addRoundedRect(card, radius, radius)
        mid = card.center().y()
        top = QRectF(card.left(), card.top(), card.width(), mid - card.top())
        bottom = QRectF(card.left(), mid, card.width(), card.bottom() - mid)
        fill, ink = QColor("#0e0e0f"), QColor("#c7c7c7")
        font = self._heavy(card.height() * 0.70, 760, 84)

        def number(s: str, clip: QRectF, squash: float = 1.0, shade: float = 0.0) -> None:
            """``s`` drawn across the card, cut to ``clip`` and squashed toward
            the hinge by ``squash`` (1 flat, 0 edge-on)."""
            if squash <= 0.01:
                return
            p.save()
            p.setClipPath(path)
            p.setClipRect(clip, Qt.IntersectClip)
            p.translate(0, mid)
            p.scale(1.0, squash)
            p.translate(0, -mid)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRect(clip)
            p.setFont(font)
            p.setPen(ink)
            p.drawText(card.adjusted(0, card.height() * 0.02, 0, 0), Qt.AlignCenter, s)
            if shade > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, int(200 * shade)))
                p.drawRect(clip)
            p.restore()

        if old is None:
            number(new, top)
            number(new, bottom)
        elif t < 0.5:
            k = t / 0.5                         # the old top folding down
            number(new, top)                    # uncovered behind it
            number(old, bottom)
            number(old, top, 1.0 - k, shade=k * 0.8)
        else:
            k = (t - 0.5) / 0.5                 # the new bottom swinging down
            number(new, top)
            number(old, bottom, shade=(1 - k) * 0.35)
            number(new, bottom, k, shade=(1 - k) * 0.8)

        # The hinge, and a little lift off the card behind.
        p.save()
        p.setPen(QPen(QColor(0, 0, 0, 230), 1.6))
        p.drawLine(QPointF(card.left(), mid), QPointF(card.right(), mid))
        p.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        p.drawLine(QPointF(card.left(), mid + 1.2), QPointF(card.right(), mid + 1.2))
        if tag:
            p.setFont(self.font(9.5, QFont.Bold))
            p.setPen(ink)
            p.drawText(QRectF(card.left() + 10, card.top() + 8, 40, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, tag)
        p.restore()

    # ------------------------------------------------------------------ #
    #  Medium: World clock
    # ------------------------------------------------------------------ #
    def _paint_world_row(self, p, r: QRectF, now: datetime) -> None:
        pal = self.palette_
        box = self._full(r, 12)
        here = now.astimezone()
        n = 4
        cell = box.width() / n
        rad = min(cell - 10, box.height() * 0.56) / 2
        for i, key in enumerate(self.cities):
            cx = box.left() + cell * (i + 0.5)
            cy = box.top() + rad + 2
            there = city_time(key, here)
            self._dial(p, QPointF(cx, cy), rad, there)
            name = CITIES[key][0]
            col = QRectF(cx - cell / 2, cy + rad + 5, cell, 15)
            p.setFont(self.font(11, QFont.Bold))
            p.setPen(QColor(pal.text))
            fm = p.fontMetrics()
            p.drawText(col, Qt.AlignCenter, fm.elidedText(name, Qt.ElideRight, int(cell - 4)))
            p.setFont(self.font(10, QFont.DemiBold))
            p.setPen(QColor(pal.text_dim))
            p.drawText(col.translated(0, 14), Qt.AlignCenter, _day_label(there, here))
            p.drawText(col.translated(0, 27), Qt.AlignCenter, _offset_label(there, here))

    # ------------------------------------------------------------------ #
    #  Menu
    # ------------------------------------------------------------------ #
    def context_actions(self, menu) -> None:
        from PySide6.QtGui import QAction, QActionGroup

        faces = self.available_faces()
        if len(faces) > 1:
            sub = menu.addMenu("Face")
            g = QActionGroup(sub)
            for key, name in faces:
                f = QAction(name, sub, checkable=True)
                f.setChecked(key == self.face)
                f.triggered.connect(lambda _c=False, k=key: self.set_face(k))
                g.addAction(f)
                sub.addAction(f)

        face = self.face
        if face in ("world", "city"):
            slots = 1 if face == "city" else 4
            cm = menu.addMenu("City" if slots == 1 else "Cities")
            for slot, current in enumerate(self.cities[:slots]):
                target = cm if slots == 1 else cm.addMenu(
                    f"{slot + 1}. {CITIES[current][0]}")
                g = QActionGroup(target)
                for key, (name, *_rest) in CITIES.items():
                    a = QAction(name, target, checkable=True)
                    a.setChecked(key == current)
                    a.triggered.connect(lambda _c=False, s=slot, k=key: self.set_city(s, k))
                    g.addAction(a)
                    target.addAction(a)

        if face == "classic" and self.size_class == "medium":
            self._time_style_menu(menu)

    def _time_style_menu(self, menu) -> None:
        """A Time style submenu: weight, width and italic for the digits."""
        from PySide6.QtGui import QAction, QActionGroup

        style = menu.addMenu("Time style")

        wsub = style.addMenu("Weight")
        wg = QActionGroup(wsub)
        wg.setExclusive(True)
        cur_w = int(self.config.get("time_weight", DEFAULT_WEIGHT))
        for name, val in TIME_WEIGHTS:
            a = QAction(name, wsub, checkable=True)
            a.setChecked(cur_w == val)
            a.triggered.connect(lambda _c=False, v=val: self._set_time("time_weight", v))
            wg.addAction(a)
            wsub.addAction(a)

        dsub = style.addMenu("Width")
        dg = QActionGroup(dsub)
        dg.setExclusive(True)
        cur_s = int(self.config.get("time_stretch", 0)) or 100
        for name, val in TIME_WIDTHS:
            a = QAction(name, dsub, checkable=True)
            a.setChecked(cur_s == val)
            # Normal is stored as absent, so a default clock carries no width key.
            a.triggered.connect(
                lambda _c=False, v=val: self._set_time("time_stretch",
                                                       None if v == 100 else v))
            dg.addAction(a)
            dsub.addAction(a)

        italic = QAction("Italic", style, checkable=True)
        italic.setChecked(bool(self.config.get("time_italic", False)))
        italic.toggled.connect(
            lambda on: self._set_time("time_italic", True if on else None))
        style.addAction(italic)

        style.addSeparator()
        reset = QAction("Reset time style", style)
        reset.setEnabled(any(k in self.config
                             for k in ("time_weight", "time_stretch", "time_italic")))
        reset.triggered.connect(self._reset_time)
        style.addAction(reset)

    def _set_time(self, key: str, value) -> None:
        if value is None:
            self.config.pop(key, None)
        else:
            self.config[key] = value
        self.changed.emit()          # persist with the layout
        self.update()

    def _reset_time(self) -> None:
        for k in ("time_weight", "time_stretch", "time_italic"):
            self.config.pop(k, None)
        self.changed.emit()
        self.update()

    def _time_font(self, size: float):
        """The time's font, built from the saved weight, italic and width."""
        return self.font(
            size,
            int(self.config.get("time_weight", DEFAULT_WEIGHT)),
            italic=bool(self.config.get("time_italic", False)),
            stretch=int(self.config.get("time_stretch", 0)),
        )

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
        time_font = self._time_font(size)
        p.setFont(time_font)
        fm = p.fontMetrics()
        gap = 8.0
        w_time = fm.horizontalAdvance(hhmm)

        suffix_font = self._time_font(17)
        p.setFont(suffix_font)
        w_suffix = p.fontMetrics().horizontalAdvance(suffix)

        block = w_time + gap + w_suffix
        left = r.left() + (r.width() - block) / 2.0
        band = QRectF(r.left(), r.top() + 20, r.width(), r.height() - 20)

        p.setFont(time_font)
        p.setPen(QColor(pal.text))
        p.drawText(QRectF(left, band.top(), w_time, band.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, hhmm)
        # AM/PM sits on the time's baseline rather than its centre.
        p.setFont(suffix_font)
        p.setPen(QColor(pal.text_dim))
        p.drawText(QRectF(left + w_time + gap,
                          band.top() + band.height() / 2 - 2,
                          w_suffix + 4, 26),
                   Qt.AlignLeft | Qt.AlignVCenter, suffix)
        p.setOpacity(1.0)
