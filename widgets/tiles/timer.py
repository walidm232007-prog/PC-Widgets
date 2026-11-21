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

When a timer or a focus phase ends the card says so three ways: its ring
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

from PySide6.QtCore import QDate, QDateTime, QLocale, QPointF, QRectF, Qt, QTime
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
                               QInputDialog, QLineEdit, QTimeEdit)

from ..core.card import WidgetCard

FACES_ALL = (("timer", "Timer"), ("stopwatch", "Stopwatch"), ("focus", "Focus"),
             ("countdown", "Countdown"))
PRESETS = (1, 3, 5, 10, 15, 20, 25, 30, 45, 60)     # minutes, in the Duration menu
CHIPS = (1, 5, 10, 25)                             # the large timer's quick starts
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

    def set_duration(self, seconds: int, start: bool = False) -> None:
        t = self.timer
        t["duration"] = int(max(1, min(MAX_TIMER, seconds)))
        t["ends_at"] = t["left"] = None
        self._flash_until = 0.0
        if start:
            self.timer_toggle()
        else:
            self._save()

    def nudge_duration(self, steps: int) -> None:
        """The wheel over a timer that isn't running: ten seconds at a time
        under a minute, a minute under ten, five past that."""
        t = self.timer
        if timer_state(t) == "running" or not steps:
            return
        d = int(t.get("duration", 300))
        for _ in range(abs(steps)):
            up = steps > 0
            edge = d if up else d - 1
            step = 10 if edge < 60 else (60 if edge < 600 else 300)
            d = d + step if up else d - step
            d = max(10, min(MAX_TIMER, (d // step) * step if d >= 60 else d))
        self.set_duration(d)

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

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            hit = self._hit(ev.position())
            if hit:
                # A button press must not also start a drag.
                self._pressed = hit
                self.update()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        hit = None if self._drag_from is not None else self._hit(ev.position())
        if hit != self._hover_btn:
            self._hover_btn = hit
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._pressed:
            name, self._pressed = self._pressed, None
            if self._hit(ev.position()) == name:
                self._activate(name)
            self.update()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover_btn = self._pressed = None
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
        elif name.startswith("timer.chip."):
            self.set_duration(60 * int(name.rsplit(".", 1)[1]), start=True)
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

    def _ring(self, p, rect: QRectF, frac: float, colour: str, width: float,
              flash: bool = False) -> None:
        """A track and the part of it still to go, from twelve o'clock
        clockwise, with round ends."""
        pal = self.palette_
        inner = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)
        track = QColor(pal.text_faint)
        track.setAlpha(52)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(track, width, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(inner, 0, 360 * 16)
        if flash:
            lit = QColor(colour)
            lit.setAlpha(255 if int(time.monotonic() * 2) % 2 == 0 else 110)
            p.setPen(QPen(lit, width, Qt.SolidLine, Qt.FlatCap))
            p.drawArc(inner, 0, 360 * 16)
            return
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            p.setPen(QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(inner, 90 * 16, -max(1, int(round(360 * 16 * frac))))

    def _button(self, p, name: str, rect: QRectF, icon: str, tone: str) -> None:
        """A round button as iOS's interactive widgets draw them: the main one
        a solid disc in its colour with a white mark - green to start, orange
        to pause, indigo for focus - and the others a grey one. Tinted discs
        with coloured marks, as Apple's Clock app has, went missing on a bright
        wallpaper through frosted glass: orange on orange."""
        pal = self.palette_
        if tone == "plain":
            fill = QColor(pal.text)
            fill.setAlpha(34 + (40 if self._pressed == name else 18 if self._hover_btn == name else 0))
            mark = QColor(pal.text)
        else:
            fill = QColor({"go": pal.good, "pause": pal.warn, "indigo": INDIGO[pal.dark]}[tone])
            if self._pressed == name:
                fill = fill.darker(118)
            elif self._hover_btn == name:
                fill = fill.lighter(110)
            mark = QColor("#ffffff")
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawEllipse(rect)
        self._icon(p, icon, rect, mark)
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
            p.setRenderHint(QPainter.Antialiasing, True)
            p.drawPath(path)
        elif icon == "pause":
            w, gap = s * 0.55, s * 0.42
            for x in (c.x() - gap - w, c.x() + gap):
                p.drawRoundedRect(QRectF(x, c.y() - s * 0.95, w, s * 1.9), w * 0.3, w * 0.3)
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
        elif icon == "reset":
            p.setBrush(Qt.NoBrush)
            pen = QPen(colour, max(1.6, rect.width() * 0.075), Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            arc = QRectF(c.x() - s, c.y() - s, 2 * s, 2 * s)
            p.drawArc(arc, 110 * 16, 270 * 16)
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

    def _ink(self, colour: str) -> str:
        """A colour for text: lifted a little in the dark theme, where indigo
        and blue sat dim on warm glass."""
        return QColor(colour).lighter(128).name() if self.palette_.dark else colour

    def _caption(self, p, rect: QRectF, s: str, colour: Optional[str] = None,
                 size: float = 9.5, align=Qt.AlignCenter, weight=QFont.Normal) -> None:
        p.setFont(self.font(size, weight))
        p.setPen(QColor(colour or self.palette_.text_dim))
        p.drawText(rect, align, s)

    def _ends_at_text(self, ends_at: float) -> str:
        when = QDateTime.fromSecsSinceEpoch(int(round(ends_at))).time()
        return "Ends " + QLocale.system().toString(when, QLocale.ShortFormat)

    # -- the timer face --------------------------------------------------- #
    def _paint_timer(self, p, r: QRectF, now: float) -> None:
        pal = self.palette_
        t = self.timer
        state = timer_state(t)
        left = timer_left(t, now)
        duration = float(t.get("duration", 300))
        frac = left / max(1.0, duration)
        flash = now < self._flash_until and state == "done"
        text = down_text(left)
        caption = {"ready": minutes_text(duration), "paused": "Paused",
                   "done": "Done"}.get(state) or self._ends_at_text(float(t["ends_at"]))
        go_icon, go_tone = ("pause", "pause") if state == "running" else ("play", "go")
        size = self.size_class

        if size == "small":
            d = 112.0
            ring = QRectF(r.center().x() - d / 2, r.top() - 6, d, d)
            self._ring(p, ring, frac, pal.warn, 7.0, flash)
            digits = QRectF(ring.left() + 10, ring.center().y() - 19, ring.width() - 20, 30)
            self._digits_text(p, digits, text, self._fit_size(text, 25, digits.width()), pal.text)
            self._caption(p, QRectF(ring.left(), ring.center().y() + 11, ring.width(), 16),
                          caption, pal.warn if state == "done" else None, 9)
            b = 28.0
            if state != "ready":
                self._button(p, "timer.reset", QRectF(r.left() - 4, r.bottom() - b + 4, b, b),
                             "reset", "plain")
            self._button(p, "timer.go", QRectF(r.right() - b + 4, r.bottom() - b + 4, b, b),
                         go_icon, go_tone)
            return

        if size == "medium":
            d = r.height() + 8
            ring = QRectF(r.left() - 4, r.top() - 4, d, d)
            self._ring(p, ring, frac, pal.warn, 8.0, flash)
            inner = ring.adjusted(14, 14, -14, -14)
            self._digits_text(p, inner, text, self._fit_size(text, 28, inner.width()), pal.text)
            col = QRectF(ring.right() + 18, r.top(), r.right() - ring.right() - 18, r.height())
            self.label(p, QRectF(col.left(), col.top(), col.width(), 12), "Timer")
            self._digits_text(p, QRectF(col.left(), col.top() + 20, col.width(), 26),
                              minutes_text(duration), 17, pal.text, QFont.DemiBold,
                              Qt.AlignLeft | Qt.AlignVCenter)
            self._caption(p, QRectF(col.left(), col.top() + 46, col.width(), 18), caption,
                          pal.warn if state == "done" else None, 10.5,
                          Qt.AlignLeft | Qt.AlignVCenter,
                          QFont.DemiBold if state == "done" else QFont.Normal)
            b = 40.0
            y = r.bottom() - b + 2
            self._button(p, "timer.go", QRectF(col.right() - b, y, b, b), go_icon, go_tone)
            if state != "ready":
                self._button(p, "timer.reset", QRectF(col.right() - 2 * b - 12, y, b, b),
                             "reset", "plain")
            return

        # large
        d = 196.0
        ring = QRectF(r.center().x() - d / 2, r.top() + 2, d, d)
        self._ring(p, ring, frac, pal.warn, 10.0, flash)
        digits = QRectF(ring.left() + 18, ring.center().y() - 30, ring.width() - 36, 46)
        self._digits_text(p, digits, text, self._fit_size(text, 40, digits.width()), pal.text)
        self._caption(p, QRectF(ring.left(), ring.center().y() + 18, ring.width(), 20), caption,
                      pal.warn if state == "done" else None, 11)
        b = 46.0
        y = ring.bottom() - b + 6
        if state != "ready":
            self._button(p, "timer.reset", QRectF(r.left() - 2, y, b, b), "reset", "plain")
        self._button(p, "timer.go", QRectF(r.right() - b + 2, y, b, b), go_icon, go_tone)
        # Quick starts along the bottom: a tap sets the length and starts it.
        chips = len(CHIPS)
        gap = 8.0
        cw = (r.width() - gap * (chips - 1)) / chips
        top = r.bottom() - 30
        for i, m in enumerate(CHIPS):
            rect = QRectF(r.left() + i * (cw + gap), top, cw, 30)
            name = f"timer.chip.{m}"
            chosen = int(duration) == m * 60
            if chosen:
                fill = QColor(pal.warn)
                if self._pressed == name:
                    fill = fill.darker(118)
            else:
                fill = QColor(pal.text)
                fill.setAlpha(26 + (36 if self._pressed == name else 16 if self._hover_btn == name else 0))
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(rect, 15, 15)
            self._caption(p, rect, f"{m} min", "#ffffff" if chosen else pal.text, 10.5,
                          weight=QFont.DemiBold)
            self._buttons.append((name, QRectF(rect)))

    # -- the stopwatch face ----------------------------------------------- #
    def _paint_stopwatch(self, p, r: QRectF, now: float) -> None:
        pal = self.palette_
        s = self.stopwatch
        running = s.get("started_at") is not None
        total = stopwatch_total(s, now)
        laps = list(s.get("laps") or [])
        go_icon, go_tone = ("stop", "pause") if running else ("play", "go")
        side_icon = "lap" if running else "reset"
        size = self.size_class
        lap_now = total - (laps[-1] if laps else 0.0)

        if size == "small":
            self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Stopwatch")
            text = up_text(total, 1)
            digits = QRectF(r.left() - 4, r.top() + 30, r.width() + 8, 40)
            self._digits_text(p, digits, text, self._fit_size(text, 28, digits.width()), pal.text)
            if laps:
                self._caption(p, QRectF(r.left(), r.top() + 70, r.width(), 16),
                              f"Lap {len(laps) + 1}  {up_text(lap_now, 1)}", None, 9.5)
            b = 28.0
            if running or total > 0:
                self._button(p, "watch.side", QRectF(r.left() - 4, r.bottom() - b + 4, b, b),
                             side_icon, "plain")
            self._button(p, "watch.go", QRectF(r.right() - b + 4, r.bottom() - b + 4, b, b),
                         go_icon, go_tone)
            return

        if size == "medium":
            self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Stopwatch")
            text = up_text(total, 2)
            b = 42.0
            room = r.width() - 2 * b - 30
            digits = QRectF(r.left(), r.top() + 22, room, 58)
            self._digits_text(p, digits, text, self._fit_size(text, 40, digits.width()), pal.text,
                              align=Qt.AlignLeft | Qt.AlignVCenter)
            line = (f"Lap {len(laps) + 1}  {up_text(lap_now, 2)}" if laps
                    else ("Running" if running else ("Stopped" if total > 0 else "Ready")))
            self._caption(p, QRectF(r.left(), r.top() + 84, room, 18), line, None, 10.5,
                          Qt.AlignLeft | Qt.AlignVCenter)
            y = r.center().y() - b / 2 + 8
            self._button(p, "watch.go", QRectF(r.right() - b, y, b, b), go_icon, go_tone)
            if running or total > 0:
                self._button(p, "watch.side", QRectF(r.right() - 2 * b - 12, y, b, b),
                             side_icon, "plain")
            return

        # large: the time, the buttons, the laps - best green, worst red, as Apple does
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Stopwatch")
        text = up_text(total, 2)
        digits = QRectF(r.left(), r.top() + 22, r.width(), 60)
        self._digits_text(p, digits, text, self._fit_size(text, 46, digits.width()), pal.text)
        b = 46.0
        y = r.top() + 92
        if running or total > 0:
            self._button(p, "watch.side", QRectF(r.left(), y, b, b), side_icon, "plain")
        self._button(p, "watch.go", QRectF(r.right() - b, y, b, b), go_icon, go_tone)
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
        pal = self.palette_
        f = self.focus
        phase = f.get("phase", "focus")
        running = f.get("ends_at") is not None
        left = focus_left(f, now)
        frac = left / max(1.0, focus_length(f, phase))
        colour = INDIGO[pal.dark] if phase == "focus" else pal.good
        name = {"focus": "Focus", "short": "Break", "long": "Long break"}[phase]
        rounds = int(f.get("rounds", FOCUS["rounds"]))
        rnd = int(f.get("round", 1))
        flash = now < self._flash_until
        text = down_text(left)
        tone = "indigo" if phase == "focus" else "go"
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
            d = 112.0
            ring = QRectF(r.center().x() - d / 2, r.top() - 6, d, d)
            self._ring(p, ring, frac, colour, 7.0, flash and not running)
            self._caption(p, QRectF(ring.left(), ring.center().y() - 30, ring.width(), 14),
                          name, None, 9, weight=QFont.DemiBold)
            digits = QRectF(ring.left() + 10, ring.center().y() - 16, ring.width() - 20, 30)
            self._digits_text(p, digits, text, self._fit_size(text, 24, digits.width()), pal.text)
            dots(ring.center().x(), ring.center().y() + 22, 2.4)
            b = 28.0
            self._button(p, "focus.skip", QRectF(r.left() - 4, r.bottom() - b + 4, b, b),
                         "skip", "plain")
            self._button(p, "focus.go", QRectF(r.right() - b + 4, r.bottom() - b + 4, b, b),
                         go_icon, tone)
            return

        if size == "medium":
            d = r.height() + 8
            ring = QRectF(r.left() - 4, r.top() - 4, d, d)
            self._ring(p, ring, frac, colour, 8.0, flash and not running)
            inner = ring.adjusted(14, 14, -14, -14)
            self._digits_text(p, inner, text, self._fit_size(text, 28, inner.width()), pal.text)
            col = QRectF(ring.right() + 18, r.top(), r.right() - ring.right() - 18, r.height())
            self.label(p, QRectF(col.left(), col.top(), col.width(), 12),
                       f"Round {rnd} of {rounds}")
            self._caption(p, QRectF(col.left(), col.top() + 20, col.width(), 26),
                          name, self._ink(colour), 17, Qt.AlignLeft | Qt.AlignVCenter, QFont.DemiBold)
            nxt, _r = focus_next(f)
            after = (self._ends_at_text(float(f["ends_at"])) if running else
                     f"Then a {minutes_text(focus_length(f, nxt))} "
                     f"{'long break' if nxt == 'long' else 'break'}" if phase == "focus"
                     else "Then focus")
            self._caption(p, QRectF(col.left(), col.top() + 48, col.width(), 18), after, None,
                          10.5, Qt.AlignLeft | Qt.AlignVCenter)
            dots(col.left() + 2.6 + (rounds - 1) * 2.6 * 3.2 / 2, col.top() + 76, 2.6)
            b = 40.0
            y = r.bottom() - b + 2
            self._button(p, "focus.go", QRectF(col.right() - b, y, b, b), go_icon, tone)
            self._button(p, "focus.skip", QRectF(col.right() - 2 * b - 12, y, b, b), "skip",
                         "plain")
            return

        # large
        d = 196.0
        ring = QRectF(r.center().x() - d / 2, r.top() + 2, d, d)
        self._ring(p, ring, frac, colour, 10.0, flash and not running)
        self._caption(p, QRectF(ring.left(), ring.center().y() - 50, ring.width(), 20), name,
                      self._ink(colour), 12, weight=QFont.DemiBold)
        digits = QRectF(ring.left() + 18, ring.center().y() - 28, ring.width() - 36, 46)
        self._digits_text(p, digits, text, self._fit_size(text, 40, digits.width()), pal.text)
        dots(ring.center().x(), ring.center().y() + 32, 3.2)
        b = 46.0
        y = ring.bottom() - b + 6
        self._button(p, "focus.skip", QRectF(r.left() - 2, y, b, b), "skip", "plain")
        self._button(p, "focus.go", QRectF(r.right() - b + 2, y, b, b), go_icon, tone)
        done = self.focus_today()
        line = (f"{done} focus session{'s' if done != 1 else ''} today, "
                f"{minutes_text(done * focus_length(f, 'focus'))}" if done else
                f"{int(f.get('focus', FOCUS['focus']))} min focus, "
                f"{int(f.get('short', FOCUS['short']))} min breaks")
        self._caption(p, QRectF(r.left(), r.bottom() - 22, r.width(), 22), line, None, 11)

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
