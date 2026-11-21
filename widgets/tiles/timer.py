"""
timer.py
========
A timer, a stopwatch, focus sessions and a countdown to a date: one card,
four faces (right-click -> Face, or the dots under a preview in the gallery).

Each face keeps its own state in the card's config, so switching faces never
stops anything: a timer left running behind the stopwatch still rings. Times
are kept as moments on the wall clock - when a timer ends, when a stopwatch
started - rather than as counters, so a running timer survives the board
restarting and a paused one comes back paused.

A running timer looks like Apple's StandBy timer - a dial of short ticks
round the time, its length in small orange capitals, a grey cross and an
orange pause button - and one not yet started is set the way iOS's timer
control sets one: minutes on a ruler that scrolls under a mark, a big orange
length, and Start Timer. Focus uses the same dial, in indigo and green.

When a timer or a focus phase ends the card says so three ways: its dial
flashes, Windows shows a notification from the tray icon, and a chime plays
("Sound when done" on the card's menu). One that ended while the board was
closed is shown as done, without the fanfare.

The digits are set with tabular figures (OpenType ``tnum``), so a counting
time doesn't shuffle sideways as its digits change width.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import time
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QDate, QDateTime, QLocale, QPointF, QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
                               QInputDialog, QLineEdit, QTimeEdit)

from ..core.card import WidgetCard

FACES_ALL = (("timer", "Timer"), ("stopwatch", "Stopwatch"), ("focus", "Focus"),
             ("countdown", "Countdown"))
PRESETS = (1, 3, 5, 10, 15, 20, 25, 30, 45, 60)     # minutes, in the Duration menu
RULER_MAX = 180                                    # minutes: the end of the setting ruler
GLIDE = 16.0           # 1/s: the ruler's spring - settled on a minute in about a quarter second
SETTLE, SETTLE_SPEED = 0.02, 0.3   # minutes, and minutes a second: near enough to stop
BLURS = (1.3, 3.0, 5.8)    # px: the ruler's three blurs, a little soft to the softest
DIM = 0.32                 # the ruler past the chosen minute, against lit
SAVE_AFTER = 600           # ms of quiet on the wheel before the length is saved
GLIDE_FPS = 60             # the ruler's frames a second, at most (frameclock.Ticker's max_fps)
FLICK = 0.12           # s a flicked ruler carries on at the speed it was let go
FOCUS = {"focus": 25, "short": 5, "long": 15, "rounds": 4}   # minutes; rounds before a long break
INDIGO = {True: "#5e5ce6", False: "#5856d6"}        # iOS indigo, dark and light: Focus's colour
MAX_TIMER = 24 * 3600                              # s: the longest timer
STALE = 60.0           # s: an end further back than this when first seen came while the board was closed
FLASH = 8.0            # s the ring flashes after an end
FAST_MS, SMOOTH_MS, SECONDS_MS, IDLE_MS = 50, 100, 250, 1000


# --------------------------------------------------------------------------- #
#  Time as text
# --------------------------------------------------------------------------- #
def down_text(seconds: float) -> str:
    """A timer's time left, Apple's way: m:ss under an hour, h:mm:ss over,
    rounded up - five minutes shows 5:00 for its whole first second, and 0:00
    only once it's over."""
    whole = int(math.ceil(max(0.0, seconds) - 1e-9))
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def up_text(seconds: float, digits: int = 2) -> str:
    """A stopwatch's time, as Apple's Stopwatch writes it: 00:12.34, and
    1:02:03.45 past the hour. Rounded down, with ``digits`` of a second."""
    # Counted in whole milliseconds: a lap of 45.91 - 21.62 seconds is
    # 24.2899... in floating point, and would show as 24.28.
    ms = int(round(max(0.0, seconds) * 1000))
    whole, rest_ms = divmod(ms, 1000)
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    if digits:
        text += f".{rest_ms // 10 ** (3 - digits):0{digits}d}"
    return text


def minutes_text(seconds: float) -> str:
    """A length as words: 25 min, 1 h 30 min, 45 s."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    h, m = divmod(s // 60, 60)
    secs = s % 60
    if h:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{m} min {secs} s" if secs else f"{m} min"


def length_caps(seconds: float) -> str:
    """A timer's length as StandBy writes it under the time: 30 MIN, 1 HR 30
    MIN, 45 SEC (set in capitals when drawn)."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} sec"
    h, m = divmod(s // 60, 60)
    rest = s % 60
    if h:
        return f"{h} hr {m} min" if m else f"{h} hr"
    return f"{m} min {rest} sec" if rest else f"{m} min"


def parse_length(text: str) -> Optional[int]:
    """A typed length in seconds: 25 (minutes), 1:30 (m:ss), 1:00:00
    (h:mm:ss), 90s, 2h, 1h30m. None if it isn't one."""
    t = text.strip().lower().replace(" ", "")
    if not t:
        return None
    try:
        if ":" in t:
            parts = [int(p) for p in t.split(":")]
            if len(parts) > 3 or any(p < 0 for p in parts):
                return None
            total = 0
            for p in parts:
                total = total * 60 + p
            return total if 0 < total <= MAX_TIMER else None
        total, number = 0, ""
        for ch in t:
            if ch.isdigit() or ch == ".":
                number += ch
            elif ch in "hms" and number:
                total += float(number) * {"h": 3600, "m": 60, "s": 1}[ch]
                number = ""
            elif ch == "i" or ch == "n":
                continue               # "min"
            else:
                return None
        if number:
            total += float(number) * 60     # a bare number is minutes
        total = int(round(total))
        return total if 0 < total <= MAX_TIMER else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  The four kinds of time, as plain functions over the card's saved dicts
# --------------------------------------------------------------------------- #
def timer_left(t: dict, now: float) -> float:
    if t.get("ends_at") is not None:
        return max(0.0, float(t["ends_at"]) - now)
    left = t.get("left")
    return float(t.get("duration", 300) if left is None else left)


def timer_state(t: dict) -> str:
    """ready (set, not started), running, paused, or done."""
    if t.get("ends_at") is not None:
        return "running"
    left = t.get("left")
    if left is None:
        return "ready"
    return "done" if float(left) <= 0 else "paused"


def stopwatch_total(s: dict, now: float) -> float:
    total = float(s.get("elapsed", 0.0))
    if s.get("started_at") is not None:
        total += max(0.0, now - float(s["started_at"]))
    return total


def focus_length(f: dict, phase: str) -> float:
    return 60.0 * float(f.get(phase, FOCUS[phase]))


def focus_left(f: dict, now: float) -> float:
    if f.get("ends_at") is not None:
        return max(0.0, float(f["ends_at"]) - now)
    left = f.get("left")
    return focus_length(f, f.get("phase", "focus")) if left is None else float(left)


def focus_next(f: dict) -> Tuple[str, int]:
    """The phase after this one, and its round: a short break after each focus
    round, a long one after the last, then focus round 1 again."""
    phase, rnd = f.get("phase", "focus"), int(f.get("round", 1))
    rounds = int(f.get("rounds", FOCUS["rounds"]))
    if phase == "focus":
        return ("long" if rnd >= rounds else "short"), rnd
    return "focus", (1 if phase == "long" else rnd + 1)


def countdown_target(c: dict, now: float) -> float:
    """When a countdown ends, as a timestamp: its saved local date and time, or
    the next New Year for a card that has none yet."""
    try:
        return dt.datetime.fromisoformat(str(c["target"])).timestamp()
    except (KeyError, ValueError, OverflowError, OSError):
        year = dt.datetime.fromtimestamp(now).year + 1
        return dt.datetime(year, 1, 1).timestamp()


def countdown_text(target: float, now: float) -> Tuple[str, str]:
    """(the big part, the words under it): 42 days, 5 h 12 min, 12:34 in
    the last hour, then Today, and 3 days ago after it."""
    left = target - now
    if left > 0:
        days = left / 86400.0
        if days >= 1:
            n = int(days)
            return str(n), "day" if n == 1 else "days"
        if left >= 3600:
            h, m = divmod(int(left // 60), 60)
            return f"{h} h {m:02d}", "min"
        return down_text(left), "left"
    then = dt.datetime.fromtimestamp(target).date()
    ago = (dt.datetime.fromtimestamp(now).date() - then).days
    if ago <= 0:
        return "Today", ""
    return str(ago), "day ago" if ago == 1 else "days ago"


def countdown_rest(target: float, now: float) -> str:
    """What a countdown of days has left after them: "and 9 h", for the
    bigger faces. Nothing under a day, or once it's over."""
    left = target - now
    if left < 86400:
        return ""
    h = int((left % 86400) // 3600)
    m = int((left % 3600) // 60)
    if h:
        return f"and {h} h" + (f" {m} min" if m else "")
    return f"and {m} min" if m else ""


def _box(a, radius: int, axis: int):
    """A box blur of the float array ``a`` along ``axis`` (0 or 1): each value
    the mean of the 2 * radius + 1 around it, nothing past the ends."""
    import numpy as np
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (radius + 1, radius)
    total = np.cumsum(np.pad(a, pad), axis=axis, dtype=np.float32)
    span = 2 * radius + 1
    if axis == 0:
        return (total[span:span + n] - total[:n]) * (1.0 / span)
    return (total[:, span:span + n] - total[:, :n]) * (1.0 / span)


def _has_numpy() -> bool:
    """Whether numpy is there to blur with."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def _soften(sharp, radius: float):
    """``sharp`` (h, w, 4) blurred by two box passes of ``radius`` px each way,
    near enough a Gaussian."""
    r = max(1, int(round(radius)))
    out = sharp
    for _ in range(2):
        out = _box(_box(out, r, 0), r, 1)
    return out


def ruler_strips(width: float, height: float, colour: str, dpr: float, font: QFont):
    """The whole ruler, 0 to RULER_MAX, drawn once as one long strip and
    blurred once at each of BLURS: (the strips as premultiplied bytes, sharp
    first; the minutes of room before 0; the numbers' rows). For a ruler
    ``width`` x ``height`` px. Safe on any thread: a QImage and numpy only."""
    import numpy as np
    spacing, label_h, _pointer, top, bottom, tick = ruler_geometry(width, height)
    pad = int(math.ceil(width / 2 / spacing)) + 2
    img = QImage(max(1, int(math.ceil((RULER_MAX + 2 * pad) * spacing * dpr))),
                 max(1, int(math.ceil(height * dpr))), QImage.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.transparent)
    # Straight alpha, so the numbers come out greyscale, as the card's type
    # does (WidgetCard.paint_card).
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing, True)
    q.setRenderHint(QPainter.TextAntialiasing, True)
    q.setFont(font)
    q.setPen(QPen(QColor(colour), tick, Qt.SolidLine, Qt.RoundCap))
    for m in range(0, RULER_MAX + 1):
        x = (m + pad) * spacing
        q.drawLine(QPointF(x, top + tick / 2), QPointF(x, bottom - tick / 2))
        if m % 5 == 0:
            q.drawText(QRectF(x - 24, 0, 48, label_h), Qt.AlignCenter, str(m))
    q.end()
    src = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    w, h = src.width(), src.height()
    rows = np.frombuffer(src.constBits(), np.uint8, src.bytesPerLine() * h).reshape(h, -1)
    sharp = rows[:, :w * 4].reshape(h, w, 4).astype(np.float32)
    levels = [sharp] + [_soften(sharp, radius * dpr) for radius in BLURS]
    return ([np.clip(level + 0.5, 0, 255).astype(np.uint8) for level in levels], pad,
            int(round(label_h * dpr)))


def ruler_geometry(width: float, height: float):
    """A ruler's (px a minute, the numbers' height, the mark's, where the ticks
    start and end below its top, a tick's width)."""
    spacing = max(8.0, width / 29.0)
    label_h = height * 0.30
    pointer = max(7.0, height * 0.12)
    top = label_h + height * 0.05
    bottom = height - pointer - height * 0.08
    return spacing, label_h, pointer, top, bottom, max(1.8, spacing * 0.27)


def out_of_focus(img: QImage) -> QImage:
    """``img`` sharp in the middle and softer toward both sides, fading out at
    the very ends: a ruler's depth of field, as iOS's timer control draws it.
    Three blurs of it, each two box passes either way (near enough a Gaussian),
    are blended column by column - the middle fifth untouched, the ends at the
    softest, still there but faint. Unchanged without numpy."""
    try:
        import numpy as np
    except ImportError:
        return img
    src = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    w, h = src.width(), src.height()
    if w < 8 or h < 4:
        return img
    dpr = src.devicePixelRatio()
    rows = np.frombuffer(src.constBits(), np.uint8, src.bytesPerLine() * h).reshape(h, -1)
    sharp = rows[:, :w * 4].reshape(h, w, 4).astype(np.float32)
    levels = [sharp] + [_soften(sharp, radius * dpr) for radius in BLURS]
    side = np.abs((np.arange(w, dtype=np.float32) + 0.5) / w - 0.5) * 2.0   # 0 middle, 1 ends
    where = np.clip((side - 0.2) / 0.72, 0.0, 1.0) * (len(levels) - 1)
    out = np.zeros_like(sharp)
    for i, level in enumerate(levels):
        out += level * np.clip(1.0 - np.abs(where - i), 0.0, 1.0)[None, :, None]
    out *= np.clip((1.0 - side) / 0.26, 0.0, 1.0)[None, :, None]   # and fainter at the ends
    data = np.ascontiguousarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))
    done = QImage(data.data, w, h, w * 4, QImage.Format_ARGB32_Premultiplied).copy()
    done.setDevicePixelRatio(dpr)
    return done


def _digits(font: QFont) -> QFont:
    """``font`` with tabular figures, so changing digits keep their places."""
    if hasattr(font, "setFeature") and hasattr(QFont, "Tag"):
        font.setFeature(QFont.Tag("tnum"), 1)
    return font


def _chime() -> None:
    """Windows' own alarm chime, without waiting for it; the plain beep where
    that file isn't there."""
    try:
        import winsound
    except ImportError:
        return
    path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Media", "Alarm01.wav")
    try:
        if os.path.isfile(path):
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
        else:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except RuntimeError:
        pass


class CountdownDialog(QDialog):
    """What a countdown counts to: a name, a date and a time."""

    def __init__(self, label: str, when: dt.datetime, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Count down to")
        form = QFormLayout(self)
        self.name = QLineEdit(label)
        self.name.setPlaceholderText("New Year, an exam, a trip")
        self.date = QDateEdit(QDate(when.year, when.month, when.day))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd d MMM yyyy")
        self.time = QTimeEdit(QTime(when.hour, when.minute))
        self.time.setDisplayFormat(QLocale.system().timeFormat(QLocale.ShortFormat))
        form.addRow("Name", self.name)
        form.addRow("Date", self.date)
        form.addRow("Time", self.time)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def result_value(self) -> Tuple[str, str]:
        d, t = self.date.date(), self.time.time()
        when = dt.datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())
        return self.name.text().strip(), when.isoformat(timespec="minutes")


# --------------------------------------------------------------------------- #
#  The card
# --------------------------------------------------------------------------- #
class TimerWidget(WidgetCard):
    kind = "timer"
    title = "Timer"
    allow_image = True
    customizable = True
    supported_sizes = ("small", "medium", "large")
    refresh_ms = IDLE_MS
    # The tick decides: a running ring is redrawn ten times a second, a card
    # with nothing running only when what it shows changes.
    repaint_on_tick = False
    strips_ready = Signal()        # the ruler's strips are built (on their own thread)

    FACES = {size: FACES_ALL for size in ("small", "medium", "large")}

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._now: Callable[[], float] = time.time      # the self-test's and samples' way in
        self._alert: Optional[Callable[[str, str], None]] = None
        self._shown = None             # what the card shows, as last drawn
        self._buttons: List[Tuple[str, QRectF]] = []
        self._pressed: Optional[str] = None
        self._hover_btn: Optional[str] = None
        self._flash_until = 0.0
        self._first = True             # the first look: ends long gone ring quietly
        self._interval = IDLE_MS
        self._ruler = None             # (rect, px a minute, minutes at the mark), as last drawn
        self._scrub = None             # (where the press was, minutes then, whether it moved)
        self._cursor = None
        self._shown_minutes: Optional[float] = None   # where the ruler is drawn, moving
        self._glide_vel = 0.0          # its speed, minutes a second
        self._glide_last: Optional[float] = None
        self._glider = None            # the frame clock's ticker while it glides
        self._drag_track: List[Tuple[float, float]] = []   # recent (time, minutes) under a drag
        self._strip = None             # (what for, the ruler drawn and blurred once): _strips
        self._drawn_digits: Optional[str] = None   # the setter's length, as last drawn
        self._drawn_pos: Optional[float] = None    # where its ruler was drawn
        self._save_timer = None        # saving the length once the wheel stops
        self._strip_job = None         # the strips being built, by what they're for
        self.strips_ready.connect(self.live_update)

    # -- faces ------------------------------------------------------------ #
    def available_faces(self):
        return list(self.FACES.get(self.size_class, ()))

    @property
    def face(self) -> str:
        chosen = (self.config.get("faces") or {}).get(self.size_class, "timer")
        return chosen if chosen in dict(self.available_faces()) else "timer"

    def set_face(self, key: str) -> None:
        if key not in dict(self.available_faces()) or key == self.face:
            return
        faces = dict(self.config.get("faces") or {})
        faces[self.size_class] = key
        self.config["faces"] = faces
        self.changed.emit()
        self._refresh(force=True)

    # -- state ------------------------------------------------------------ #
    def _part(self, name: str, defaults: dict) -> dict:
        d = self.config.get(name)
        if not isinstance(d, dict):
            d = dict(defaults)
            self.config[name] = d
        return d

    @property
    def timer(self) -> dict:
        return self._part("timer", {"duration": 300, "ends_at": None, "left": None})

    @property
    def stopwatch(self) -> dict:
        return self._part("stopwatch", {"started_at": None, "elapsed": 0.0, "laps": []})

    @property
    def focus(self) -> dict:
        return self._part("focus", dict(FOCUS, phase="focus", round=1, ends_at=None,
                                        left=None, auto=False))

    @property
    def countdown(self) -> dict:
        c = self._part("countdown", {"label": "New Year"})
        if "target" not in c:
            when = dt.datetime.fromtimestamp(countdown_target(c, self._now()))
            c["target"] = when.isoformat(timespec="minutes")
        return c

    def _save(self) -> None:
        self.changed.emit()
        self._refresh(force=True)

    # -- the timer -------------------------------------------------------- #
    def timer_toggle(self) -> None:
        t, now = self.timer, self._now()
        state = timer_state(t)
        if state == "running":
            t["left"], t["ends_at"] = timer_left(t, now), None
        else:
            base = float(t.get("duration", 300))
            if state == "paused":
                base = float(t["left"])
            t["ends_at"], t["left"] = now + base, None
            self._flash_until = 0.0
        self._save()

    def timer_reset(self) -> None:
        t = self.timer
        t["ends_at"] = t["left"] = None
        self._flash_until = 0.0
        self._save()

    def set_duration(self, seconds: int, start: bool = False, save_now: bool = True) -> None:
        was = self._ruler_minutes()
        t = self.timer
        t["duration"] = int(max(1, min(MAX_TIMER, seconds)))
        t["ends_at"] = t["left"] = None
        self._flash_until = 0.0
        if start:
            self.timer_toggle()
        else:
            self._glide_ruler(was)
            if save_now:
                self._save()
            else:
                self._save_soon()

    def _save_soon(self) -> None:
        """Save once the wheel has been still SAVE_AFTER ms: a notch at a time,
        it wrote the board's layout file on every one."""
        if self._save_timer is None:
            from PySide6.QtCore import QTimer
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.setInterval(SAVE_AFTER)
            self._save_timer.timeout.connect(self.changed.emit)
        self._save_timer.start()
        self._refresh(force=True)

    # -- the ruler's glide --------------------------------------------------- #
    def _ruler_minutes(self) -> float:
        """Where the ruler stands: wherever it's gliding or being dragged, or
        else at the timer's length."""
        if self._shown_minutes is not None:
            return self._shown_minutes
        return float(self.timer.get("duration", 300)) / 60.0

    def _setter_shown(self) -> bool:
        return (self.face == "timer" and self.size_class != "small"
                and timer_state(self.timer) == "ready")

    def _start_ticker(self) -> bool:
        """The frame clock's ticker: a step for every frame the display shows."""
        try:
            if self._glider is None:
                from ..core.frameclock import Ticker
                # 60 a second, on the display's own refreshes and evenly
                # spaced: every third on a 200 Hz monitor (67), every second
                # on a 144 Hz one (72), every one at 60. At 200 a second the
                # glide's repaints cost more than they showed.
                self._glider = Ticker(self._glide_step, max_fps=GLIDE_FPS)
            self._glider.start()
            return True
        except Exception:
            return False

    def _glide_ruler(self, start: float, velocity: float = 0.0) -> None:
        """Ease the ruler from ``start`` minutes to the timer's length rather
        than jump: a step for every frame the display shows - 60 a second on a
        60 Hz monitor, 200 on a 200 Hz one, on the board's frame clock - along
        a critically damped spring, so it never overshoots, and a new length
        mid-glide carries on from wherever it is."""
        target = float(self.timer.get("duration", 300)) / 60.0
        if not self._setter_shown() or (abs(start - target) < 1e-3 and abs(velocity) < 1e-3
                                         and self._shown_minutes is None):
            self._stop_glide_ruler()
            return
        if self._shown_minutes is None:
            self._shown_minutes = start
            self._glide_vel = velocity
        elif velocity:
            self._glide_vel = velocity
        self._glide_last = None
        if not self._start_ticker():
            self._stop_glide_ruler()         # no frame clock: just be there
        self._paint_frame()

    def _stop_glide_ruler(self) -> None:
        if self._glider is not None:
            self._glider.stop()
        self._shown_minutes, self._glide_vel = None, 0.0

    def _glide_step(self, now: Optional[float] = None) -> None:
        """One displayed frame: under a drag, the ruler painted where the
        pointer has put it - once a frame, however fast the mouse reports;
        let go, a step of its spring toward the chosen minute. Timed by the
        frame's own moment, so a frame the board misses is stepped over, not
        stretched."""
        if now is None:
            from ..core.frameclock import clock
            now = getattr(clock(), "frame_time", 0.0) or time.perf_counter()
        last, self._glide_last = self._glide_last, now
        if not self._setter_shown() or (self._shown_minutes is None and self._scrub is None):
            self._stop_glide_ruler()
            return
        if self._scrub is None:
            dt = 1.0 / 60.0 if last is None else max(0.0, min(0.05, now - last))
            target = float(self.timer.get("duration", 300)) / 60.0
            d, v = self._shown_minutes - target, self._glide_vel
            fall = math.exp(-GLIDE * dt)
            d, v = (d + (v + GLIDE * d) * dt) * fall, (v - GLIDE * (v + GLIDE * d) * dt) * fall
            # Settled within a fifth of a pixel or so: the frames past that
            # moved nothing anyone could see.
            if abs(d) < SETTLE and abs(v) < SETTLE_SPEED:
                self._stop_glide_ruler()
            else:
                self._shown_minutes, self._glide_vel = target + d, v
        self._paint_frame()

    def _paint_frame(self) -> None:
        """Repaint for the ruler having moved: the ruler alone (live_update),
        unless the length shown beside it changed too."""
        if self._setter_text() != self._drawn_digits:
            self.update()
        elif self._ruler_minutes() != self._drawn_pos:
            self.live_update()

    def nudge_duration(self, steps: int) -> None:
        """The wheel over a timer that isn't running: a minute a notch, as the
        ruler counts - ten seconds under a minute, five past an hour."""
        t = self.timer
        if timer_state(t) == "running" or not steps:
            return
        d = int(t.get("duration", 300))
        for _ in range(abs(steps)):
            up = steps > 0
            edge = d if up else d - 1
            step = 10 if edge < 60 else (60 if edge < 3600 else 300)
            d = d + step if up else d - step
            d = max(10, min(MAX_TIMER, (d // step) * step if d >= 60 else d))
        self.set_duration(d, save_now=False)

    # -- the stopwatch ---------------------------------------------------- #
    def stopwatch_toggle(self) -> None:
        s, now = self.stopwatch, self._now()
        if s.get("started_at") is not None:
            s["elapsed"], s["started_at"] = stopwatch_total(s, now), None
        else:
            s["started_at"] = now
        self._save()

    def stopwatch_side(self) -> None:
        """Lap while running, Reset once stopped - Apple's left button."""
        s, now = self.stopwatch, self._now()
        if s.get("started_at") is not None:
            s["laps"] = list(s.get("laps") or []) + [round(stopwatch_total(s, now), 3)]
        else:
            s["elapsed"], s["laps"] = 0.0, []
        self._save()

    # -- focus ------------------------------------------------------------ #
    def focus_toggle(self) -> None:
        f, now = self.focus, self._now()
        if f.get("ends_at") is not None:
            f["left"], f["ends_at"] = focus_left(f, now), None
        else:
            f["ends_at"], f["left"] = now + focus_left(f, now), None
            self._flash_until = 0.0
        self._save()

    def focus_skip(self) -> None:
        self._focus_advance(self._now(), start=self.focus.get("ends_at") is not None)
        self._save()

    def focus_reset(self) -> None:
        f = self.focus
        f.update(phase="focus", round=1, ends_at=None, left=None)
        self._save()

    def _focus_advance(self, now: float, start: bool) -> None:
        f = self.focus
        phase, rnd = focus_next(f)
        f["phase"], f["round"] = phase, rnd
        f["ends_at"], f["left"] = (now + focus_length(f, phase)) if start else None, None

    def _count_focus(self, now: float) -> None:
        f = self.focus
        today = dt.datetime.fromtimestamp(now).date().isoformat()
        kept = f.get("today") or {}
        count = int(kept.get("count", 0)) if kept.get("day") == today else 0
        f["today"] = {"day": today, "count": count + 1}

    def focus_today(self) -> int:
        f = self.focus
        today = dt.datetime.fromtimestamp(self._now()).date().isoformat()
        kept = f.get("today") or {}
        return int(kept.get("count", 0)) if kept.get("day") == today else 0

    # -- the countdown ---------------------------------------------------- #
    def set_countdown(self, label: str, target_iso: str) -> None:
        c = self.countdown
        c["label"], c["target"] = label or "Countdown", target_iso
        c.pop("rang", None)
        self._save()

    # -- ticking ---------------------------------------------------------- #
    def on_tick(self) -> None:
        now = self._now()
        stale_ok = not self._first
        self._first = False
        self._ends(now, stale_ok)
        self._refresh(now=now)

    def _ends(self, now: float, live: bool) -> None:
        """Whatever has run out since the last look: rung, unless it ran out
        long ago - while the board was closed."""
        t = self.timer
        if t.get("ends_at") is not None and float(t["ends_at"]) <= now:
            gone = now - float(t["ends_at"])
            t["ends_at"], t["left"] = None, 0.0
            self.changed.emit()
            if live or gone <= STALE:
                self._ring_out("Timer done",
                               f"Your {minutes_text(t.get('duration', 300))} timer is done.")
        f = self.focus
        if f.get("ends_at") is not None and float(f["ends_at"]) <= now:
            gone = now - float(f["ends_at"])
            loud = live or gone <= STALE
            was = f.get("phase", "focus")
            if was == "focus":
                self._count_focus(now)
            self._focus_advance(float(f["ends_at"]), start=bool(f.get("auto")) and loud)
            if f.get("ends_at") is not None and float(f["ends_at"]) <= now:
                f["ends_at"], f["left"] = None, None    # auto-started, and that ran out too
            self.changed.emit()
            if loud:
                nxt = f["phase"]
                if was == "focus":
                    title = "Focus done"
                    text = (f"Time for a {minutes_text(focus_length(f, nxt))} "
                            f"{'long break' if nxt == 'long' else 'break'}.")
                else:
                    title, text = "Break's over", "Back to focus."
                self._ring_out(title, text)
        c = self.config.get("countdown")
        if isinstance(c, dict) and "target" in c:
            target = countdown_target(c, now)
            if now >= target and c.get("rang") != c["target"]:
                c["rang"] = c["target"]
                self.changed.emit()
                if live and now - target <= STALE:
                    label = c.get("label") or "Countdown"
                    self._ring_out(label, f"{label} is here.")

    def _ring_out(self, title: str, text: str) -> None:
        """An end: the ring flashes, Windows says so, a chime plays."""
        self._flash_until = self._now() + FLASH
        if self._alert is not None:
            self._alert(title, text)
            return
        try:
            from .. import tray
            tray.notify(title, text)
        except Exception:
            pass
        if self.config.get("sound", True):
            _chime()

    def _refresh(self, now: Optional[float] = None, force: bool = False) -> None:
        """Repaint only when what's shown changed, and set the next tick by
        what's running: a stopwatch's hundredths need 20 a second, a ring 10,
        seconds 4, a card with nothing moving one."""
        now = self._now() if now is None else now
        shown = self._signature(now)
        if force or shown != self._shown:
            self._shown = shown
            self.update()
        face = self.face
        flashing = now < self._flash_until
        if face == "stopwatch" and self.stopwatch.get("started_at") is not None:
            ms = FAST_MS
        elif flashing or (face == "timer" and timer_state(self.timer) == "running") or (
                face == "focus" and self.focus.get("ends_at") is not None):
            ms = SMOOTH_MS
        elif face == "countdown" and 0 < countdown_target(self.countdown, now) - now < 86400:
            ms = SECONDS_MS
        else:
            ms = IDLE_MS
        if ms != self._interval:
            self._interval = ms
            self._timer.setInterval(ms)

    def _signature(self, now: float) -> tuple:
        face, size = self.face, self.size_class
        flash = int((self._flash_until - now) * 2) % 2 if now < self._flash_until else -1
        if face == "timer":
            t = self.timer
            state = timer_state(t)
            left = timer_left(t, now)
            frac = round(left / max(1.0, float(t.get("duration", 300))), 3)
            return (face, size, state, down_text(left), frac if state == "running" else None, flash)
        if face == "stopwatch":
            s = self.stopwatch
            return (face, size, s.get("started_at") is not None,
                    up_text(stopwatch_total(s, now)), len(s.get("laps") or []))
        if face == "focus":
            f = self.focus
            left = focus_left(f, now)
            frac = round(left / max(1.0, focus_length(f, f.get("phase", "focus"))), 3)
            return (face, size, f.get("phase"), f.get("round"), f.get("ends_at") is not None,
                    down_text(left), frac if f.get("ends_at") is not None else None, flash)
        c = self.countdown
        return (face, size, c.get("label"), countdown_text(countdown_target(c, now), now))

    # -- input ------------------------------------------------------------ #
    def _hit(self, pos) -> Optional[str]:
        for name, rect in self._buttons:
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return name
        return None

    def _on_ruler(self, pos) -> bool:
        """Whether ``pos`` is on the ruler a timer that isn't running is set on."""
        return (self._ruler is not None and self.face == "timer"
                and timer_state(self.timer) == "ready" and self._ruler[0].contains(pos))

    def _set_minutes(self, minutes: int, start: Optional[float] = None,
                     velocity: float = 0.0) -> None:
        """The ruler's choice: whole minutes, 1 to RULER_MAX, glided to from
        ``start`` (where it's drawn now). Saved on letting go."""
        was = self._ruler_minutes() if start is None else start
        t = self.timer
        t["duration"] = 60 * max(1, min(RULER_MAX, int(minutes)))
        t["ends_at"] = t["left"] = None
        self._glide_ruler(was, velocity)
        self._refresh(force=True)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            pos = ev.position()
            hit = self._hit(pos)
            if hit:
                # A button press must not also start a drag.
                self._pressed = hit
                self.update()
                ev.accept()
                return
            if self._on_ruler(pos):
                # Nor a press on the ruler: it scrolls under its mark instead,
                # caught where it is if it was still gliding.
                here = self._ruler_minutes()
                self._shown_minutes, self._glide_vel = here, 0.0
                self._drag_track = []
                self._scrub = (pos.x(), here, False)
                self._start_ticker()         # it paints the drag, once a frame
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        if self._scrub is not None:
            x0, start, moved = self._scrub
            if moved or abs(pos.x() - x0) >= 3:
                # The ruler follows the pointer exactly, between minutes too.
                self._scrub = (x0, start, True)
                spacing = self._ruler[1] if self._ruler is not None else 10.0
                here = max(1.0, min(float(RULER_MAX), start - (pos.x() - x0) / spacing))
                now = time.perf_counter()
                self._drag_track = [(t_, m_) for t_, m_ in self._drag_track if now - t_ < 0.1]
                self._drag_track.append((now, here))
                self._shown_minutes = here
                if self._glider is None or not self._glider.isActive():
                    self._paint_frame()      # no frame clock: paint it now
            ev.accept()
            return
        hit = None if self._drag_from is not None else self._hit(pos)
        if hit:
            cursor = Qt.PointingHandCursor
        elif self._drag_from is None and self._on_ruler(pos):
            cursor = Qt.SizeHorCursor
        else:
            cursor = Qt.ArrowCursor
        if hit != self._hover_btn or cursor != self._cursor:
            self._hover_btn, self._cursor = hit, cursor
            self.setCursor(cursor)
            self.update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._scrub is not None:
            _x0, start, moved = self._scrub
            self._scrub = None
            if moved:
                # Let go, it settles on a minute; flicked, it carries on a
                # little the way it was going first.
                here = self._ruler_minutes()
                speed = 0.0
                track = self._drag_track
                if len(track) >= 2 and track[-1][0] - track[0][0] > 0.008:
                    speed = (track[-1][1] - track[0][1]) / (track[-1][0] - track[0][0])
                self._set_minutes(int(round(here + speed * FLICK)), start=here, velocity=speed)
            elif self._ruler is not None:
                # A click on the ruler brings that minute to the mark.
                rect, spacing, minutes = self._ruler
                self._set_minutes(int(round(minutes + (ev.position().x() - rect.center().x())
                                            / spacing)))
            else:
                self._stop_glide_ruler()
            self.changed.emit()
            ev.accept()
            return
        if self._pressed:
            name, self._pressed = self._pressed, None
            if self._hit(ev.position()) == name:
                self._activate(name)
            self.update()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover_btn = self._pressed = self._cursor = None
        self.unsetCursor()
        super().leaveEvent(ev)

    def wheelEvent(self, ev) -> None:
        if self.face == "timer" and timer_state(self.timer) != "running":
            steps = int(round(ev.angleDelta().y() / 120))
            if steps:
                self.nudge_duration(steps)
            ev.accept()
            return
        super().wheelEvent(ev)

    def _activate(self, name: str) -> None:
        if name == "timer.go":
            if timer_state(self.timer) == "done":
                self.timer_reset()
            self.timer_toggle()
        elif name == "timer.reset":
            self.timer_reset()
        elif name == "watch.go":
            self.stopwatch_toggle()
        elif name == "watch.side":
            self.stopwatch_side()
        elif name == "focus.go":
            self.focus_toggle()
        elif name == "focus.skip":
            self.focus_skip()

    # -- menu ------------------------------------------------------------- #
    def context_actions(self, menu) -> None:
        from PySide6.QtGui import QAction, QActionGroup

        sub = menu.addMenu("Face")
        group = QActionGroup(sub)
        for key, name in self.available_faces():
            a = QAction(name, sub, checkable=True)
            a.setChecked(key == self.face)
            a.triggered.connect(lambda _c=False, k=key: self.set_face(k))
            group.addAction(a)
            sub.addAction(a)

        face = self.face
        if face == "timer":
            dm = menu.addMenu("Duration")
            dg = QActionGroup(dm)
            current = int(self.timer.get("duration", 300))
            for m in PRESETS:
                a = QAction(f"{m} min", dm, checkable=True)
                a.setChecked(current == m * 60)
                a.triggered.connect(lambda _c=False, s=m * 60: self.set_duration(s))
                dg.addAction(a)
                dm.addAction(a)
            dm.addSeparator()
            dm.addAction("Other length…", self._ask_duration)
            if timer_state(self.timer) != "ready":
                menu.addAction("Reset timer", self.timer_reset)
        elif face == "stopwatch":
            if stopwatch_total(self.stopwatch, self._now()) > 0:
                menu.addAction("Reset stopwatch", self._stopwatch_reset)
        elif face == "focus":
            f = self.focus
            for key, label, choices in (("focus", "Focus length", (25, 50)),
                                        ("short", "Break", (5, 10)),
                                        ("long", "Long break", (15, 30))):
                m = menu.addMenu(label)
                g = QActionGroup(m)
                for n in choices:
                    a = QAction(f"{n} min", m, checkable=True)
                    a.setChecked(int(f.get(key, FOCUS[key])) == n)
                    a.triggered.connect(lambda _c=False, k=key, v=n: self._set_focus(k, v))
                    g.addAction(a)
                    m.addAction(a)
            auto = QAction("Start the next one by itself", menu, checkable=True)
            auto.setChecked(bool(f.get("auto")))
            auto.triggered.connect(lambda on: self._set_focus("auto", bool(on)))
            menu.addAction(auto)
            menu.addAction("Start over at round 1", self.focus_reset)
        else:
            menu.addAction("Count down to…", self._ask_countdown)

        if face != "countdown" and face != "stopwatch":
            sound = QAction("Sound when done", menu, checkable=True)
            sound.setChecked(bool(self.config.get("sound", True)))
            sound.triggered.connect(self._set_sound)
            menu.addAction(sound)

    def _set_sound(self, on: bool) -> None:
        self.config["sound"] = bool(on)
        self.changed.emit()

    def _set_focus(self, key: str, value) -> None:
        f = self.focus
        f[key] = value
        if key in FOCUS and f.get("ends_at") is None and f.get("phase") == key:
            f["left"] = None                       # a phase not started takes the new length
        self._save()

    def _stopwatch_reset(self) -> None:
        s = self.stopwatch
        s.update(started_at=None, elapsed=0.0, laps=[])
        self._save()

    def _ask_duration(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Timer length",
            "How long? Minutes, or m:ss, or h:mm:ss (25, 1:30, 1:00:00):",
            text=minutes_text(self.timer.get("duration", 300)).replace(" min", "").replace(" ", ""))
        if ok:
            seconds = parse_length(text)
            if seconds:
                self.set_duration(seconds)

    def _ask_countdown(self) -> None:
        c = self.countdown
        when = dt.datetime.fromtimestamp(countdown_target(c, self._now()))
        dlg = CountdownDialog(c.get("label", ""), when, self)
        if dlg.exec() == QDialog.Accepted:
            self.set_countdown(*dlg.result_value())

    # -- drawing ---------------------------------------------------------- #
    def paint_content(self, p: QPainter, r: QRectF) -> None:
        self._buttons = []
        self._ruler = None
        now = self._now()
        face = self.face
        if face == "stopwatch":
            self._paint_stopwatch(p, r, now)
        elif face == "focus":
            self._paint_focus(p, r, now)
        elif face == "countdown":
            self._paint_countdown(p, r, now)
        else:
            self._paint_timer(p, r, now)

    def _digits_text(self, p, rect: QRectF, s: str, size: float, colour: str,
                     weight=QFont.DemiBold, align=Qt.AlignCenter) -> None:
        p.setFont(_digits(self.font(size, weight)))
        p.setPen(QColor(colour))
        p.drawText(rect, align, s)

    def _fit_size(self, s: str, size: float, width: float, weight=QFont.DemiBold) -> float:
        """``size`` or smaller, so ``s`` fits ``width``: an hour-long timer has
        three more characters than a five-minute one."""
        from PySide6.QtGui import QFontMetricsF
        while size > 9:
            if QFontMetricsF(_digits(self.font(size, weight))).horizontalAdvance(s) <= width:
                break
            size -= 1
        return size

    def _ink(self, colour: str) -> str:
        """A colour for text: lifted a little in the dark theme, where indigo
        and blue sat dim on warm glass, and deepened in the light one."""
        c = QColor(colour)
        return (c.lighter(128) if self.palette_.dark else c.darker(135)).name()

    def _caption(self, p, rect: QRectF, s: str, colour: Optional[str] = None,
                 size: float = 9.5, align=Qt.AlignCenter, weight=QFont.Normal) -> None:
        p.setFont(self.font(size, weight))
        p.setPen(QColor(colour or self.palette_.text_dim))
        p.drawText(rect, align, s)

    def _caps(self, p, rect: QRectF, s: str, colour: str, size: float,
              align=Qt.AlignCenter) -> None:
        """Small spaced capitals, as StandBy writes "30 MIN" under the time."""
        f = self.font(size, QFont.DemiBold)
        f.setLetterSpacing(QFont.PercentageSpacing, 108)
        p.setFont(f)
        p.setPen(QColor(colour))
        p.drawText(rect, align, s.upper())

    def _ends_at_text(self, ends_at: float) -> str:
        when = QDateTime.fromSecsSinceEpoch(int(round(ends_at))).time()
        return "Ends " + QLocale.system().toString(when, QLocale.ShortFormat)

    def _ticks(self, p, rect: QRectF, frac: float, colour: str, flash: bool = False,
               count: int = 60) -> None:
        """A dial of short ticks, as Apple's StandBy timer draws it: the time
        still to go lit, the rest dark, unwinding clockwise from twelve."""
        pal = self.palette_
        c = rect.center()
        width = max(2.0, rect.width() * 0.02)
        outer = rect.width() / 2 - width / 2
        inner = outer - rect.width() * 0.08
        exact = max(0.0, min(1.0, frac)) * count
        lit_n = int(math.floor(exact + 1e-9))
        part = exact - lit_n        # how much of the tick going dark is still lit
        on = QColor(colour)
        if flash:
            lit_n, part = count, 0.0
            on.setAlpha(255 if int(time.monotonic() * 2) % 2 == 0 else 90)
        dark = QColor(pal.text_faint)
        dark.setAlpha(44)
        # The tick going dark fades out over its share of the time, rather
        # than switching off: the dial empties smoothly, not tick by tick.
        fading = QColor(
            *(int(round(a + (b - a) * part)) for a, b in zip(dark.getRgb(), on.getRgb())))
        pens = (QPen(dark, width, Qt.SolidLine, Qt.RoundCap),
                QPen(on, width, Qt.SolidLine, Qt.RoundCap),
                QPen(fading, width, Qt.SolidLine, Qt.RoundCap))
        current = None
        for i in range(count):
            lit = 1 if i >= count - lit_n else (2 if i == count - lit_n - 1 and part > 0 else 0)
            if lit is not current:
                p.setPen(pens[lit])
                current = lit
            a = math.radians(-90.0 + i * 360.0 / count)
            ca, sa = math.cos(a), math.sin(a)
            p.drawLine(QPointF(c.x() + ca * inner, c.y() + sa * inner),
                       QPointF(c.x() + ca * outer, c.y() + sa * outer))

    def _badge(self, p, rect: QRectF, colour: str) -> None:
        """StandBy's corner badge: the timer's glyph, in its colour, on grey."""
        pal = self.palette_
        fill = QColor(pal.text)
        fill.setAlpha(30)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawEllipse(rect)
        c = rect.center()
        s = rect.width() * 0.22
        p.setPen(QPen(QColor(colour), max(1.5, rect.width() * 0.065), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        dial = QPointF(c.x(), c.y() + s * 0.15)
        p.drawArc(QRectF(dial.x() - s, dial.y() - s, 2 * s, 2 * s), 115 * 16, 310 * 16)
        p.drawLine(dial, QPointF(dial.x() + s * 0.5, dial.y() - s * 0.42))          # the hand
        p.drawLine(QPointF(c.x(), dial.y() - s * 0.98), QPointF(c.x(), dial.y() - s * 1.42))

    def _button(self, p, name: str, rect: QRectF, icon: str, tone: str) -> None:
        """A round button as StandBy draws them: the main one a solid disc in
        its colour - orange for a timer, green to start a stopwatch and red to
        stop it, indigo for focus - with a dark mark on the bright ones and a
        white one on the deep; the others a grey disc. Tinted discs with
        coloured marks, as Apple's Clock app has, went missing on a bright
        wallpaper through frosted glass."""
        pal = self.palette_
        if tone == "plain":
            fill = QColor(pal.text)
            fill.setAlpha(38 + (40 if self._pressed == name else 18 if self._hover_btn == name else 0))
            mark = QColor(pal.text)
            mark.setAlpha(220)
        else:
            fill = QColor({"timer": pal.warn, "go": pal.good, "stop": pal.bad,
                           "indigo": INDIGO[pal.dark]}[tone])
            if self._pressed == name:
                fill = fill.darker(118)
            elif self._hover_btn == name:
                fill = fill.lighter(110)
            deep = tone in ("indigo", "stop")
            mark = QColor("#ffffff" if deep else "#000000")
            mark.setAlpha(255 if deep else 225)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawEllipse(rect)
        self._icon(p, icon, rect, mark)
        self._buttons.append((name, QRectF(rect)))

    def _pill(self, p, name: str, rect: QRectF, text: str, colour: str) -> None:
        """iOS's Start Timer: a pill tinted with its colour, the words in it."""
        fill = QColor(colour)
        fill.setAlpha(58 + (40 if self._pressed == name else 22 if self._hover_btn == name else 0))
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        self._caption(p, rect, text, self._ink(colour), rect.height() * 0.32, weight=QFont.DemiBold)
        self._buttons.append((name, QRectF(rect)))

    def _icon(self, p, icon: str, rect: QRectF, colour: QColor) -> None:
        c = rect.center()
        s = rect.width() * 0.30              # the mark's half-size
        p.setBrush(colour)
        p.setPen(Qt.NoPen)
        path = QPainterPath()
        if icon == "play":
            path.moveTo(c.x() - s * 0.62, c.y() - s)
            path.lineTo(c.x() + s * 0.95, c.y())
            path.lineTo(c.x() - s * 0.62, c.y() + s)
            path.closeSubpath()
            p.drawPath(path)
        elif icon == "pause":
            w, gap = s * 0.58, s * 0.40
            for x in (c.x() - gap - w, c.x() + gap):
                p.drawRoundedRect(QRectF(x, c.y() - s * 0.95, w, s * 1.9), w * 0.32, w * 0.32)
        elif icon == "stop":
            p.drawRoundedRect(QRectF(c.x() - s * 0.8, c.y() - s * 0.8, s * 1.6, s * 1.6),
                              s * 0.3, s * 0.3)
        elif icon == "skip":
            path.moveTo(c.x() - s * 0.95, c.y() - s * 0.9)
            path.lineTo(c.x() + s * 0.45, c.y())
            path.lineTo(c.x() - s * 0.95, c.y() + s * 0.9)
            path.closeSubpath()
            p.drawPath(path)
            p.drawRoundedRect(QRectF(c.x() + s * 0.5, c.y() - s * 0.9, s * 0.42, s * 1.8),
                              s * 0.15, s * 0.15)
        elif icon == "x":
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(colour, max(1.8, rect.width() * 0.07), Qt.SolidLine, Qt.RoundCap))
            k = s * 0.74
            p.drawLine(QPointF(c.x() - k, c.y() - k), QPointF(c.x() + k, c.y() + k))
            p.drawLine(QPointF(c.x() - k, c.y() + k), QPointF(c.x() + k, c.y() - k))
        elif icon == "reset":
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(colour, max(1.6, rect.width() * 0.075), Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(c.x() - s, c.y() - s, 2 * s, 2 * s), 110 * 16, 270 * 16)
            # The arrowhead at the arc's start, pointing along it.
            tip = QPointF(c.x() + s * math.cos(math.radians(110)),
                          c.y() - s * math.sin(math.radians(110)))
            head = s * 0.62
            p.setPen(Qt.NoPen)
            p.setBrush(colour)
            arrow = QPainterPath()
            arrow.moveTo(tip.x() + head * 0.9, tip.y() - head * 0.1)
            arrow.lineTo(tip.x() - head * 0.25, tip.y() - head * 0.75)
            arrow.lineTo(tip.x() - head * 0.1, tip.y() + head * 0.55)
            arrow.closeSubpath()
            p.drawPath(arrow)
        elif icon == "lap":
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(colour, max(1.6, rect.width() * 0.075), Qt.SolidLine, Qt.RoundCap))
            pole_x = c.x() - s * 0.7
            p.drawLine(QPointF(pole_x, c.y() - s), QPointF(pole_x, c.y() + s))
            p.setPen(Qt.NoPen)
            p.setBrush(colour)
            flag = QPainterPath()
            flag.moveTo(pole_x, c.y() - s)
            flag.lineTo(c.x() + s * 0.9, c.y() - s * 0.55)
            flag.lineTo(pole_x, c.y() - s * 0.05)
            flag.closeSubpath()
            p.drawPath(flag)

    def _ruler_rect(self, r: QRectF) -> QRectF:
        """Where the setter's ruler sits in the card's inner rect ``r``."""
        if self.size_class == "medium":
            return QRectF(r.left() - 10, r.top() - 4, r.width() + 20, 74)
        return QRectF(r.left() - 10, r.top() + 132, r.width() + 20, 90)

    def paint_live(self, p: QPainter, r: QRectF) -> None:
        """The setter's ruler, drawn over the card's kept layer: it's the part
        that moves while a timer is set, so a frame of its glide repaints it
        alone (live_update)."""
        if not self._setter_shown():
            self._ruler = None
            return
        orange = self.palette_.warn
        # On light glass the ruler takes the deeper orange text does.
        rule = orange if self.palette_.dark else self._ink(orange)
        minutes = self._ruler_minutes()
        self._paint_ruler(p, self._ruler_rect(r), minutes, rule)
        self._drawn_pos = minutes

    @staticmethod
    def _ruler_geometry(rect: QRectF):
        return ruler_geometry(rect.width(), rect.height())

    def _strips(self, rect: QRectF, colour: str, dpr: float):
        """The ruler drawn and blurred once (ruler_strips) for this size,
        colour and screen scale, so a frame of a glide only cuts out the part
        in view and blends it: blurring the view afresh took 7 ms a frame,
        more than a core at 200 frames a second. Built on a thread of its own,
        a tenth of a second or so, rather than holding up the board; until
        it's there the ruler is blurred afresh at each paint, as it was, so
        it's never seen sharp. None meanwhile, or without numpy."""
        font = self.font(max(9.0, rect.height() * 0.30 * 0.6), QFont.DemiBold)
        key = (round(rect.width(), 2), round(rect.height(), 2), colour, round(dpr, 3), font.key())
        if self._strip is not None and self._strip[0] == key:
            return self._strip[1]
        try:
            import numpy  # noqa: F401
        except ImportError:
            return None
        if self._strip_job != key:
            self._strip_job = key
            import threading
            threading.Thread(target=self._build_strips,
                             args=(key, rect.width(), rect.height(), colour, dpr, QFont(font)),
                             name="timer-ruler", daemon=True).start()
        return None

    def _build_strips(self, key, width, height, colour, dpr, font) -> None:
        try:
            info = ruler_strips(width, height, colour, dpr, font)
        except Exception:
            return
        if self._strip_job == key:
            self._strip = (key, info)
            try:
                self.strips_ready.emit()     # back on the board's thread: draw them
            except RuntimeError:
                pass                         # the card has gone meanwhile

    def _paint_ruler(self, p, rect: QRectF, minutes: float, colour: str) -> None:
        """Minutes on a ruler, as iOS's timer control sets them: a tick a
        minute and a number every five, the chosen minute over a mark in the
        middle, lit up to it and dark past it, and out of focus toward both
        ends, as if seen past the mark through a lens. It scrolls under the
        mark: drag it, click a minute, or turn the wheel. Cut from the kept
        strips (_strips): the blur level each column needs, its fade, and
        whether its tick and number are lit, blended in one pass."""
        spacing, label_h, pointer, top, bottom, width = self._ruler_geometry(rect)
        dev = p.device()
        dpr = dev.devicePixelRatioF() if dev is not None else 1.0
        strips = self._strips(rect, colour, dpr)
        if strips is None:
            if _has_numpy():
                self._paint_ruler_afresh(p, rect, minutes, colour, dpr)
            else:
                self._paint_ruler_plain(p, rect, minutes, colour)
        else:
            import numpy as np
            levels, pad, number_rows = strips
            px = spacing * dpr
            w = int(math.ceil(rect.width() * dpr))
            h = levels[0].shape[0]
            start = (minutes + pad) * px - w / 2.0
            off = max(0, min(levels[0].shape[1] - w, int(round(start))))
            xs = np.arange(w, dtype=np.float32) + 0.5
            side = np.abs(xs / w - 0.5) * 2.0
            where = np.clip((side - 0.2) / 0.72, 0.0, 1.0) * (len(levels) - 1)
            fade = np.clip((1.0 - side) / 0.26, 0.0, 1.0)
            # Each column takes its nearest tick's state, and its number's.
            at = minutes + (xs - w / 2.0 + (off - start)) / px
            ticks = np.where(np.round(at) <= minutes + 1e-6, 1.0, DIM).astype(np.float32) * fade
            numbers = (np.where(5 * np.round(at / 5) <= minutes + 1e-6, 1.0, DIM)
                       .astype(np.float32) * fade)
            out = np.zeros((h, w, 4), np.float32)
            for i, level in enumerate(levels):
                weight = np.clip(1.0 - np.abs(where - i), 0.0, 1.0)
                part = level[:, off:off + w, :].astype(np.float32)
                out[:number_rows] += part[:number_rows] * (weight * numbers)[None, :, None]
                out[number_rows:] += part[number_rows:] * (weight * ticks)[None, :, None]
            data = np.ascontiguousarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))
            img = QImage(data.data, w, h, w * 4, QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            p.drawImage(rect.topLeft(), img)
        cx = rect.center().x()
        mark = QPainterPath()
        mark.moveTo(cx, rect.bottom() - pointer)
        mark.lineTo(cx + pointer * 0.68, rect.bottom())
        mark.lineTo(cx - pointer * 0.68, rect.bottom())
        mark.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colour))
        p.drawPath(mark)
        self._ruler = (QRectF(rect), spacing, minutes)

    def _paint_ruler_afresh(self, p, rect: QRectF, minutes: float, colour: str,
                            dpr: float) -> None:
        """The ruler drawn and blurred whole at this paint (out_of_focus): the
        way while its strips are being built, a few milliseconds a paint."""
        spacing, label_h, _pointer, top, bottom, width = self._ruler_geometry(rect)
        cx = rect.center().x()
        layer = QImage(max(1, int(math.ceil(rect.width() * dpr))),
                       max(1, int(math.ceil(rect.height() * dpr))), QImage.Format_ARGB32)
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.transparent)
        q = QPainter(layer)
        q.setRenderHint(QPainter.Antialiasing, True)
        q.setRenderHint(QPainter.TextAntialiasing, True)
        q.translate(-rect.left(), -rect.top())
        q.setFont(self.font(max(9.0, label_h * 0.6), QFont.DemiBold))
        first = int(math.floor(minutes - (cx - rect.left()) / spacing))
        last = int(math.ceil(minutes + (rect.right() - cx) / spacing))
        for m in range(max(0, first), min(RULER_MAX, last) + 1):
            x = cx + (m - minutes) * spacing
            if not rect.left() - width < x < rect.right() + width:
                continue
            ink = QColor(colour)
            ink.setAlphaF(1.0 if m <= minutes + 1e-6 else DIM)
            q.setPen(QPen(ink, width, Qt.SolidLine, Qt.RoundCap))
            q.drawLine(QPointF(x, rect.top() + top + width / 2),
                       QPointF(x, rect.top() + bottom - width / 2))
            if m % 5 == 0:
                q.drawText(QRectF(x - 24, rect.top(), 48, label_h), Qt.AlignCenter, str(m))
        q.end()
        p.drawImage(rect.topLeft(), out_of_focus(layer))

    def _paint_ruler_plain(self, p, rect: QRectF, minutes: float, colour: str) -> None:
        """The ruler without numpy: drawn afresh, fading at its ends but not
        out of focus."""
        spacing, label_h, _pointer, top, bottom, width = self._ruler_geometry(rect)
        cx = rect.center().x()
        p.setFont(self.font(max(9.0, label_h * 0.6), QFont.DemiBold))
        first = int(math.floor(minutes - (cx - rect.left()) / spacing))
        last = int(math.ceil(minutes + (rect.right() - cx) / spacing))
        for m in range(max(0, first), min(RULER_MAX, last) + 1):
            x = cx + (m - minutes) * spacing
            edge = min(x - rect.left(), rect.right() - x)
            if edge <= 0:
                continue
            ink = QColor(colour)
            ink.setAlphaF((1.0 if m <= minutes + 1e-6 else DIM) * min(1.0, edge / (rect.width() * 0.2)))
            p.setPen(QPen(ink, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x, rect.top() + top + width / 2),
                       QPointF(x, rect.top() + bottom - width / 2))
            if m % 5 == 0:
                p.drawText(QRectF(x - 24, rect.top(), 48, label_h), Qt.AlignCenter, str(m))

    # -- the timer face --------------------------------------------------- #
    def _paint_timer(self, p, r: QRectF, now: float) -> None:
        """Running, StandBy's timer: a dial of ticks round the time, its
        length in small orange capitals, a grey cross to cancel and an orange
        button to pause. Not yet started, iOS's timer control sets it (a
        ruler, _paint_setter) - except on a small card, which is all dial."""
        pal = self.palette_
        t = self.timer
        state = timer_state(t)
        duration = float(t.get("duration", 300))
        orange = pal.warn
        size = self.size_class
        if state == "ready" and size != "small":
            self._paint_setter(p, r, duration)
            return
        left = timer_left(t, now)
        frac = left / max(1.0, duration)
        flash = now < self._flash_until and state == "done"
        text = down_text(left)
        caption = "Done" if state == "done" else length_caps(duration)
        digit_ink = pal.text_dim if state == "paused" else pal.text
        go_icon = "pause" if state == "running" else "play"

        if size == "small":
            d = 120.0
            ring = QRectF(r.center().x() - d / 2, r.top() - 9, d, d)
            self._ticks(p, ring, frac, orange, flash)
            digits = QRectF(ring.left() + 18, ring.center().y() - 17, ring.width() - 36, 28)
            self._digits_text(p, digits, text, self._fit_size(text, 23, digits.width(), QFont.Medium),
                              digit_ink, QFont.Medium)
            self._caps(p, QRectF(ring.left(), ring.center().y() + 11, ring.width(), 13), caption,
                       self._ink(orange), 7.5)
            b = 28.0
            if state != "ready":
                self._button(p, "timer.reset", QRectF(r.left() - 5, r.bottom() - b + 5, b, b),
                             "x", "plain")
            self._button(p, "timer.go", QRectF(r.right() - b + 5, r.bottom() - b + 5, b, b),
                         go_icon, "timer")
            return

        if size == "medium":
            d = r.height() + 12
            ring = QRectF(r.left() - 6, r.top() - 6, d, d)
            self._ticks(p, ring, frac, orange, flash)
            digits = QRectF(ring.left() + 22, ring.center().y() - 20, ring.width() - 44, 32)
            self._digits_text(p, digits, text, self._fit_size(text, 27, digits.width(), QFont.Medium),
                              digit_ink, QFont.Medium)
            self._caps(p, QRectF(ring.left(), ring.center().y() + 13, ring.width(), 14), caption,
                       self._ink(orange), 8)
            col = QRectF(ring.right() + 16, r.top(), r.right() - ring.right() - 16, r.height())
            self._badge(p, QRectF(col.right() - 30, col.top() - 4, 30, 30), orange)
            self.label(p, QRectF(col.left(), col.top(), col.width() - 34, 12), "Timer")
            sub = {"paused": "Paused", "done": "Time's up"}.get(state, "")
            if state == "running":
                sub = self._ends_at_text(float(t["ends_at"]))
            self._caption(p, QRectF(col.left(), col.top() + 22, col.width(), 20), sub, None, 11,
                          Qt.AlignLeft | Qt.AlignVCenter)
            b = 42.0
            y = r.bottom() - b + 4
            self._button(p, "timer.go", QRectF(col.right() - b + 2, y, b, b), go_icon, "timer")
            self._button(p, "timer.reset", QRectF(col.right() - 2 * b - 10, y, b, b), "x", "plain")
            return

        # large: StandBy's timer, nearly to the pixel
        d = 232.0
        ring = QRectF(r.center().x() - d / 2, r.top() + 4, d, d)
        self._ticks(p, ring, frac, orange, flash)
        digits = QRectF(ring.left() + 30, ring.center().y() - 34, ring.width() - 60, 56)
        self._digits_text(p, digits, text, self._fit_size(text, 46, digits.width(), QFont.Medium),
                          digit_ink, QFont.Medium)
        self._caps(p, QRectF(ring.left(), ring.center().y() + 24, ring.width(), 20), caption,
                   self._ink(orange), 11)
        self._badge(p, QRectF(r.left() - 6, r.top() - 6, 36, 36), orange)
        b = 52.0
        self._button(p, "timer.reset", QRectF(r.left() - 6, r.bottom() - b + 6, b, b), "x", "plain")
        self._button(p, "timer.go", QRectF(r.right() - b + 6, r.bottom() - b + 6, b, b), go_icon,
                     "timer")

    def _setter_text(self) -> str:
        """The length the setter shows, in whole minutes as iOS shows it: under
        a drag, the minute at the mark; otherwise the one the ruler glides to."""
        if self._scrub is not None:
            return down_text(round(self._ruler_minutes()) * 60)
        return down_text(float(self.timer.get("duration", 300)))

    def _paint_setter(self, p, r: QRectF, duration: float) -> None:
        """A timer not yet started, as iOS's timer control sets one: the
        length big, Start Timer, and the minutes on a ruler - which, as the
        part that moves, is drawn over all this (paint_live)."""
        pal = self.palette_
        orange = pal.warn
        text = self._setter_text()
        self._drawn_digits = text
        if self.size_class == "medium":
            pill = QRectF(r.left(), r.bottom() - 40, 136, 40)
            self._pill(p, "timer.go", pill, "Start Timer", orange)
            room = QRectF(pill.right() + 10, r.bottom() - 54, r.right() - pill.right() - 10, 58)
            self._digits_text(p, room, text, self._fit_size(text, 40, room.width(), QFont.Light),
                              self._ink(orange), QFont.Light, Qt.AlignRight | Qt.AlignVCenter)
            return
        self._badge(p, QRectF(r.left() - 6, r.top() - 6, 36, 36), orange)
        big = QRectF(r.left(), r.top() + 34, r.width(), 84)
        self._digits_text(p, big, text, self._fit_size(text, 60, big.width(), QFont.Light),
                          self._ink(orange), QFont.Light)
        pill = QRectF(r.center().x() - 96, r.bottom() - 48, 192, 48)
        self._pill(p, "timer.go", pill, "Start Timer", orange)

    # -- the stopwatch face ----------------------------------------------- #
    def _paint_stopwatch(self, p, r: QRectF, now: float) -> None:
        pal = self.palette_
        s = self.stopwatch
        running = s.get("started_at") is not None
        total = stopwatch_total(s, now)
        laps = list(s.get("laps") or [])
        go_icon, go_tone = ("stop", "stop") if running else ("play", "go")
        side_icon = "lap" if running else "reset"
        size = self.size_class
        lap_now = total - (laps[-1] if laps else 0.0)

        if size == "small":
            self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Stopwatch")
            text = up_text(total, 1)
            digits = QRectF(r.left() - 4, r.top() + 30, r.width() + 8, 40)
            self._digits_text(p, digits, text, self._fit_size(text, 28, digits.width(), QFont.Medium),
                              pal.text, QFont.Medium)
            if laps:
                self._caption(p, QRectF(r.left(), r.top() + 70, r.width(), 16),
                              f"Lap {len(laps) + 1}  {up_text(lap_now, 1)}", None, 9.5)
            b = 28.0
            if running or total > 0:
                self._button(p, "watch.side", QRectF(r.left() - 5, r.bottom() - b + 5, b, b),
                             side_icon, "plain")
            self._button(p, "watch.go", QRectF(r.right() - b + 5, r.bottom() - b + 5, b, b),
                         go_icon, go_tone)
            return

        if size == "medium":
            self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Stopwatch")
            text = up_text(total, 2)
            b = 42.0
            room = r.width() - 2 * b - 30
            digits = QRectF(r.left(), r.top() + 22, room, 58)
            self._digits_text(p, digits, text, self._fit_size(text, 40, digits.width(), QFont.Medium),
                              pal.text, QFont.Medium, Qt.AlignLeft | Qt.AlignVCenter)
            line = (f"Lap {len(laps) + 1}  {up_text(lap_now, 2)}" if laps
                    else ("Running" if running else ("Stopped" if total > 0 else "Ready")))
            self._caption(p, QRectF(r.left(), r.top() + 84, room, 18), line, None, 10.5,
                          Qt.AlignLeft | Qt.AlignVCenter)
            y = r.center().y() - b / 2 + 8
            self._button(p, "watch.go", QRectF(r.right() - b + 2, y, b, b), go_icon, go_tone)
            if running or total > 0:
                self._button(p, "watch.side", QRectF(r.right() - 2 * b - 10, y, b, b),
                             side_icon, "plain")
            return

        # large: the time, the buttons, the laps - best green, worst red, as Apple does
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Stopwatch")
        text = up_text(total, 2)
        digits = QRectF(r.left(), r.top() + 22, r.width(), 60)
        self._digits_text(p, digits, text, self._fit_size(text, 46, digits.width(), QFont.Medium),
                          pal.text, QFont.Medium)
        b = 48.0
        y = r.top() + 92
        if running or total > 0:
            self._button(p, "watch.side", QRectF(r.left() - 2, y, b, b), side_icon, "plain")
        self._button(p, "watch.go", QRectF(r.right() - b + 2, y, b, b), go_icon, go_tone)
        splits = [laps[0]] + [laps[i] - laps[i - 1] for i in range(1, len(laps))] if laps else []
        rows: List[Tuple[int, float, bool]] = [(len(laps) + 1, lap_now, True)] if (laps or running) else []
        rows += [(i + 1, splits[i], False) for i in range(len(splits) - 1, -1, -1)]
        best = min(splits) if len(splits) > 1 else None
        worst = max(splits) if len(splits) > 1 else None
        top = y + b + 14
        row_h = 24.0
        for n, value, live in rows[: max(0, int((r.bottom() - top) // row_h))]:
            colour = pal.text
            if not live and best is not None and value == best:
                colour = pal.good
            elif not live and worst is not None and value == worst:
                colour = pal.bad
            line = QRectF(r.left(), top, r.width(), row_h)
            edge = QColor(pal.text_faint)
            edge.setAlpha(60)
            p.setPen(QPen(edge, 0.8))
            p.drawLine(QPointF(line.left(), line.top()), QPointF(line.right(), line.top()))
            self._caption(p, line, f"Lap {n}", colour, 11, Qt.AlignLeft | Qt.AlignVCenter)
            self._digits_text(p, line, up_text(value, 2), 11, colour, QFont.Normal,
                              Qt.AlignRight | Qt.AlignVCenter)
            top += row_h

    # -- the focus face --------------------------------------------------- #
    def _paint_focus(self, p, r: QRectF, now: float) -> None:
        """The timer's dial in indigo for focus and green for a break, the
        rounds as dots under the time."""
        pal = self.palette_
        f = self.focus
        phase = f.get("phase", "focus")
        running = f.get("ends_at") is not None
        left = focus_left(f, now)
        frac = left / max(1.0, focus_length(f, phase))
        colour = INDIGO[pal.dark] if phase == "focus" else pal.good
        tone = "indigo" if phase == "focus" else "go"
        name = {"focus": "Focus", "short": "Break", "long": "Long break"}[phase]
        rounds = int(f.get("rounds", FOCUS["rounds"]))
        rnd = int(f.get("round", 1))
        flash = now < self._flash_until and not running
        text = down_text(left)
        go_icon = "pause" if running else "play"
        size = self.size_class

        def dots(cx: float, y: float, radius: float) -> None:
            gap = radius * 3.2
            x0 = cx - gap * (rounds - 1) / 2
            for i in range(rounds):
                done = i < rnd - 1 or (i == rnd - 1 and phase != "focus")
                fill = QColor(INDIGO[pal.dark] if done or i == rnd - 1 else pal.text_faint)
                if not done and i == rnd - 1 and phase == "focus":
                    fill.setAlpha(150)
                elif not done:
                    fill.setAlpha(70)
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawEllipse(QPointF(x0 + i * gap, y), radius, radius)

        if size == "small":
            d = 120.0
            ring = QRectF(r.center().x() - d / 2, r.top() - 9, d, d)
            self._ticks(p, ring, frac, colour, flash)
            digits = QRectF(ring.left() + 18, ring.center().y() - 19, ring.width() - 36, 28)
            self._digits_text(p, digits, text, self._fit_size(text, 23, digits.width(), QFont.Medium),
                              pal.text, QFont.Medium)
            self._caps(p, QRectF(ring.left(), ring.center().y() + 8, ring.width(), 13), name,
                       self._ink(colour), 7.5)
            dots(ring.center().x(), ring.center().y() + 27, 2.2)
            b = 28.0
            self._button(p, "focus.skip", QRectF(r.left() - 5, r.bottom() - b + 5, b, b), "skip",
                         "plain")
            self._button(p, "focus.go", QRectF(r.right() - b + 5, r.bottom() - b + 5, b, b),
                         go_icon, tone)
            return

        if size == "medium":
            d = r.height() + 12
            ring = QRectF(r.left() - 6, r.top() - 6, d, d)
            self._ticks(p, ring, frac, colour, flash)
            digits = QRectF(ring.left() + 22, ring.center().y() - 20, ring.width() - 44, 32)
            self._digits_text(p, digits, text, self._fit_size(text, 27, digits.width(), QFont.Medium),
                              pal.text, QFont.Medium)
            self._caps(p, QRectF(ring.left(), ring.center().y() + 13, ring.width(), 14), name,
                       self._ink(colour), 8)
            col = QRectF(ring.right() + 16, r.top(), r.right() - ring.right() - 16, r.height())
            self.label(p, QRectF(col.left(), col.top(), col.width(), 12), f"Round {rnd} of {rounds}")
            nxt, _r = focus_next(f)
            after = (self._ends_at_text(float(f["ends_at"])) if running else
                     f"Then a {minutes_text(focus_length(f, nxt))} "
                     f"{'long break' if nxt == 'long' else 'break'}" if phase == "focus"
                     else "Then focus")
            self._caption(p, QRectF(col.left(), col.top() + 22, col.width(), 20), after, None, 11,
                          Qt.AlignLeft | Qt.AlignVCenter)
            dots(col.left() + 2.6 + (rounds - 1) * 2.6 * 3.2 / 2, col.top() + 54, 2.6)
            b = 42.0
            y = r.bottom() - b + 4
            self._button(p, "focus.go", QRectF(col.right() - b + 2, y, b, b), go_icon, tone)
            self._button(p, "focus.skip", QRectF(col.right() - 2 * b - 10, y, b, b), "skip",
                         "plain")
            return

        # large
        d = 232.0
        ring = QRectF(r.center().x() - d / 2, r.top() + 4, d, d)
        self._ticks(p, ring, frac, colour, flash)
        digits = QRectF(ring.left() + 30, ring.center().y() - 34, ring.width() - 60, 56)
        self._digits_text(p, digits, text, self._fit_size(text, 46, digits.width(), QFont.Medium),
                          pal.text, QFont.Medium)
        self._caps(p, QRectF(ring.left(), ring.center().y() + 22, ring.width(), 20), name,
                   self._ink(colour), 11)
        dots(ring.center().x(), ring.center().y() + 50, 3.0)
        b = 52.0
        self._button(p, "focus.skip", QRectF(r.left() - 6, r.bottom() - b + 6, b, b), "skip", "plain")
        self._button(p, "focus.go", QRectF(r.right() - b + 6, r.bottom() - b + 6, b, b), go_icon, tone)
        done = self.focus_today()
        line = (f"{done} today, {minutes_text(done * focus_length(f, 'focus'))}" if done else
                f"{int(f.get('focus', FOCUS['focus']))} min, "
                f"{int(f.get('short', FOCUS['short']))} min breaks")
        self._caption(p, QRectF(r.left() + b, r.bottom() - 30, r.width() - 2 * b, 30), line, None, 10.5)

    # -- the countdown face ----------------------------------------------- #
    def _paint_countdown(self, p, r: QRectF, now: float) -> None:
        pal = self.palette_
        c = self.countdown
        target = countdown_target(c, now)
        big, unit = countdown_text(target, now)
        label = c.get("label") or "Countdown"
        when = QDateTime.fromSecsSinceEpoch(int(target))
        loc = QLocale.system()
        midnight = when.time() == QTime(0, 0)
        date_short = loc.toString(when.date(), "ddd d MMM")
        date_long = loc.toString(when.date(), "dddd d MMMM yyyy")
        if not midnight:
            stamp = loc.toString(when.time(), QLocale.ShortFormat)
            date_short += f", {stamp}"
            date_long += f", {stamp}"
        size = self.size_class

        if size == "small":
            self._caption(p, QRectF(r.left(), r.top() - 2, r.width(), 18), label, self._ink(pal.accent), 11,
                          Qt.AlignLeft | Qt.AlignVCenter, QFont.DemiBold)
            digits = QRectF(r.left() - 4, r.top() + 22, r.width() + 8, 62)
            self._digits_text(p, digits, big, self._fit_size(big, 50, digits.width()), pal.text,
                              QFont.Bold, Qt.AlignLeft | Qt.AlignVCenter)
            self._caption(p, QRectF(r.left(), r.top() + 84, r.width(), 18), unit, pal.text, 12,
                          Qt.AlignLeft | Qt.AlignVCenter, QFont.DemiBold)
            self._caption(p, QRectF(r.left(), r.bottom() - 16, r.width(), 16), date_short, None,
                          9.5, Qt.AlignLeft | Qt.AlignVCenter)
            return

        if size == "medium":
            left_w = r.width() * 0.46
            digits = QRectF(r.left() - 3, r.top() + 4, left_w, 86)
            self._digits_text(p, digits, big, self._fit_size(big, 64, digits.width(), QFont.Bold),
                              pal.text, QFont.Bold, Qt.AlignLeft | Qt.AlignVCenter)
            self._caption(p, QRectF(r.left(), r.top() + 92, left_w, 22), unit, pal.text, 14,
                          Qt.AlignLeft | Qt.AlignVCenter, QFont.DemiBold)
            rest = countdown_rest(target, now)
            if rest:
                self._caption(p, QRectF(r.left(), r.top() + 114, left_w, 18), rest, None, 10.5,
                              Qt.AlignLeft | Qt.AlignVCenter)
            col = QRectF(r.left() + left_w + 12, r.top(), r.width() - left_w - 12, r.height())
            self.label(p, QRectF(col.left(), col.top(), col.width(), 12), "Countdown")
            self._caption(p, QRectF(col.left(), col.top() + 20, col.width(), 40), label,
                          self._ink(pal.accent), 16, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                          QFont.DemiBold)
            self._caption(p, QRectF(col.left(), col.bottom() - 38, col.width(), 38), date_long,
                          None, 10.5, Qt.AlignLeft | Qt.AlignBottom | Qt.TextWordWrap)
            return

        # large
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Countdown")
        self._caption(p, QRectF(r.left(), r.top() + 22, r.width(), 30), label, self._ink(pal.accent), 20,
                      Qt.AlignLeft | Qt.AlignVCenter, QFont.DemiBold)
        digits = QRectF(r.left() - 4, r.top() + 70, r.width() + 8, 120)
        self._digits_text(p, digits, big, self._fit_size(big, 96, digits.width(), QFont.Bold),
                          pal.text, QFont.Bold, Qt.AlignLeft | Qt.AlignVCenter)
        self._caption(p, QRectF(r.left(), r.top() + 190, r.width(), 28), unit, pal.text, 18,
                      Qt.AlignLeft | Qt.AlignVCenter, QFont.DemiBold)
        rest = countdown_rest(target, now)
        if rest:
            self._caption(p, QRectF(r.left(), r.top() + 218, r.width(), 24), rest, None, 13,
                          Qt.AlignLeft | Qt.AlignVCenter)
        left = target - now
        if 0 < left < 86400 * 2:
            live = down_text(left)
            self._digits_text(p, QRectF(r.left(), r.bottom() - 60, r.width(), 24), live, 15,
                              pal.text_dim, QFont.Normal, Qt.AlignLeft | Qt.AlignVCenter)
        self._caption(p, QRectF(r.left(), r.bottom() - 28, r.width(), 28), date_long, None, 12,
                      Qt.AlignLeft | Qt.AlignVCenter)
