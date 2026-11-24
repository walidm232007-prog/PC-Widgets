"""
samples.py
==========
Believable data for tiles that would otherwise need the network, the market,
a music player or a coding agent before they draw anything worth looking at.

Used in two places: ``--selftest`` renders every tile from these so a render
never depends on what happens to be running, and the app window's gallery
previews tiles from them before you add one. :func:`prepare` is the gallery's
entry point; it never starts a thread, opens a socket or writes a file.
"""

from __future__ import annotations

import math
import sys
import time


def weather_response() -> dict:
    """A canned Open-Meteo answer: a mild afternoon in London, 3:30 PM, sunset
    at 4:13 PM inside the hourly row, and a shower due in the next hour.
    Shaped exactly like the real thing, so it goes through the real parser."""
    days = [f"2026-11-{d:02d}" for d in range(11, 18)]
    hours = [f"2026-11-11T{h:02d}:00" for h in range(15, 24)] + \
            [f"2026-11-12T{h:02d}:00" for h in range(0, 17)]
    temps = [21, 20, 19, 18, 17, 16, 15, 15, 14, 14, 13, 13, 13, 12, 12, 12,
             12, 13, 14, 16, 18, 19, 20, 21, 21, 20]
    codes = [2, 61, 61, 3, 3, 2, 1, 0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 2, 2, 1,
             1, 2, 2, 3, 3]
    quarters = [f"2026-11-11T{h:02d}:{m:02d}" for h, m in
                ((15, 15), (15, 30), (15, 45), (16, 0), (16, 15), (16, 30),
                 (16, 45))]
    return {
        "current": {"time": "2026-11-11T15:30", "temperature_2m": 21.0,
                    "apparent_temperature": 20.0, "weather_code": 2,
                    "is_day": 1, "wind_speed_10m": 14.0},
        "hourly": {"time": hours, "temperature_2m": temps,
                   "weather_code": codes,
                   "is_day": [1 if t[11:13] in ("07", "08", "09", "10", "11",
                                                 "12", "13", "14", "15") else 0
                              for t in hours],
                   "precipitation_probability": [10, 60, 70, 40, 20] + [5] * 21},
        "daily": {"time": days,
                  "weather_code": [2, 61, 0, 95, 3, 71, 45],
                  "temperature_2m_max": [22, 19, 24, 20, 18, 9, 14],
                  "temperature_2m_min": [14, 13, 15, 12, 11, 2, 8],
                  "sunrise": [f"{d}T07:{12 + i:02d}" for i, d in enumerate(days)],
                  "sunset": [f"{d}T16:{13 - i:02d}" for i, d in enumerate(days)],
                  "precipitation_sum": [0.6, 4.2, 0, 11.5, 0.2, 3.0, 0],
                  "precipitation_probability_max": [70, 90, 0, 95, 20, 80, 10]},
        "minutely_15": {"time": quarters,
                        "precipitation": [0, 0, 0, 0.1, 0.3, 0.4, 0.2]},
    }


def weather(card) -> None:
    """Give the weather tile deterministic data for rendering. A sample has no
    town of its own, so it is told not to ask for one: the gallery shows what
    the card looks like, not its "Set your town" button."""
    from ..tiles.weather import parse
    card.needs_town = False
    card.data = parse(weather_response(), "London, GB", "C")


def stocks(card) -> None:
    """Deterministic quotes, so a render never waits on a market API."""
    from ..tiles.stocks import Quote

    def wave(base, amp, n=60, phase=0.0):
        return [base + math.sin(i / 7.0 + phase) * amp + i * amp / n
                for i in range(n)]

    card.quotes = [
        Quote("AAPL", "Apple Inc.", 311.00, 309.38, spark=wave(309, 1.6)),
        Quote("MSFT", "Microsoft", 487.46, 492.83, spark=wave(492, -2.2, phase=2)),
        Quote("^GSPC", "S&P 500", 7723.55, 7736.7, spark=wave(7735, -6, phase=1)),
        Quote("BTC-USD", "Bitcoin", 64842.03, 64053.0, spark=wave(64100, 260, phase=3)),
        Quote("NVDA", "NVIDIA", 182.4, 178.9, spark=wave(179, 1.4, phase=4)),
        Quote("TSLA", "Tesla", 421.8, 430.2, spark=wave(430, -3.1, phase=5)),
    ]


def outlook(card) -> None:
    """A realistic week, including an event in progress."""
    import datetime as _dt
    from .calsource import Agenda, Event
    now = _dt.datetime.now()
    t = now.replace(minute=0, second=0, microsecond=0)

    def ev(offset_h, mins, title, loc="", cal="work", all_day=False):
        s = t + _dt.timedelta(hours=offset_h)
        return Event(s, s + _dt.timedelta(minutes=mins), title, loc, all_day, cal)

    card.agenda = Agenda(events=[
        ev(-0.5, 60, "Design review", "Teams", "work"),
        ev(2, 30, "1:1 with Sam", "Teams", "work"),
        ev(4, 45, "Deploy window", "", "ops"),
        ev(25, 60, "Dentist", "Bay Street", "personal"),
        ev(27, 90, "Sprint planning", "Room 4", "work"),
        ev(50, 30, "Coffee with Alex", "Nero", "personal"),
        ev(74, 60, "Retro", "Teams", "work"),
    ], source="ics", fetched=_dt.datetime.now().timestamp())
    card._fetching = True          # don't kick off a live fetch


def media(card) -> None:
    """A track mid-play, with a position, so the bar and times are visible."""
    from .media import NowPlaying
    card.np = NowPlaying(
        title="Take me to the future", artist="Kavinsky", source="Spotify",
        playing=True, position=57.0, duration=145.0, available=True,
        backend="stub")


def comms(card) -> None:
    """A short exchange, including a live claim - the states worth looking at."""
    mb = sys.modules.get("agentmail")
    if mb is None:
        return
    now = time.time()

    def m(ago, sender, to, kind, subject, body=""):
        return mb.Message(id=f"{int(ago)}", ts=now - ago, sender=sender, to=to,
                          kind=kind, subject=subject, body=body,
                          thread="frosted-transport")

    card.messages = [
        m(2400, "claude", "codex", "note", "Media transport is frosted glass now",
          "Each disc blurs what is behind it, including the disc underneath."),
        m(1900, "codex", "claude", "ask", "Should the bays use the same blur?",
          "It would cost a buffer per bay."),
        m(1100, "claude", "codex", "answer", "No - keep the bays cheap",
          "The workspace repaints 11x a second."),
        m(420, "codex", "all", "claim", "editing the avatar rig"),
        m(90, "codex", "claude", "done", "Codex avatar landed"),
    ]
    card.claims = {"widgets/core/avatars.py": ("codex", now - 420, "avatar rig")}
    card._sig = "stub"          # stop on_tick replacing it with the real mailbox


def workspace(card) -> None:
    """One bay working, one idle - the state worth looking at."""
    from .agents import AgentState
    now = time.time()
    card.states = [
        AgentState("claude", "Claude", active=True, event="PostToolUse",
                   tool="Edit", count=9, started=now - 154, ts=now, seen=True,
                   elapsed=154.0),
        AgentState("codex", "Codex", active=False, event="Stop", tool="",
                   count=4, started=now - 400, ts=now - 300, seen=True),
    ]
    card.phase = 1.15
    card._emit_sparks("claude")
    for _ in range(3):
        card.on_tick()


def system(card) -> None:
    """A gently busy machine. Real memory, but a made-up CPU trace: sampling
    the CPU here would steal a reading from the live System card, which works
    from the difference between successive samples."""
    from . import winapi
    card.history.clear()
    for i in range(48):
        card.history.append(14 + 9 * math.sin(i / 5.0) + 5 * math.sin(i / 1.7))
    card.cpu = card.history[-1]
    card.mem = winapi.memory()


def _quiet_notes(card) -> None:
    """A sample note must never save. It loads the real notes file to show
    it, and a stray save from a sample built a moment ago could write that
    snapshot back over something typed into the real card since."""
    card._save_timer.stop()
    card._save = lambda: None
    try:
        card.edit.textChanged.disconnect(card._touched)
    except (RuntimeError, TypeError):
        pass


def timer(card) -> None:
    """A timer part-way through, a stopwatch with laps, a focus round and a
    countdown to New Year, on a clock that stands still at 3 pm on 10 December
    2026 - so a render is the same whenever it's made."""
    fixed = time.mktime((2026, 12, 10, 15, 0, 0, 0, 0, -1))
    card._now = lambda: fixed
    card.config.update({
        "timer": {"duration": 1500, "ends_at": fixed + 1122.0, "left": None},
        "stopwatch": {"started_at": None, "elapsed": 83.47, "laps": [21.62, 45.91, 71.04]},
        "focus": {"focus": 25, "short": 5, "long": 15, "rounds": 4, "phase": "focus",
                  "round": 2, "ends_at": fixed + 800.0, "left": None, "auto": False,
                  "today": {"day": "2026-12-10", "count": 1}},
        "countdown": {"label": "New Year", "target": "2027-01-01T00:00", "rang": None},
    })


def headlines(card) -> None:
    """Five stories from three made-up sources, with drawn pictures (one
    without), on a clock that stands still - so a render needs no network,
    names no real outlet, and looks the same whenever it's made."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

    from ..tiles.headlines import Story
    fixed = time.mktime((2026, 12, 10, 15, 0, 0, 0, 0, -1))
    card._now = lambda: fixed

    def picture(top: str, bottom: str, sun: str, ground: str) -> QImage:
        img = QImage(640, 400, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        sky = QLinearGradient(0, 0, 0, 400)
        sky.setColorAt(0.0, QColor(top))
        sky.setColorAt(1.0, QColor(bottom))
        p.fillRect(img.rect(), sky)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(sun))
        p.drawEllipse(QPointF(450, 150), 64, 64)
        p.setBrush(QColor(ground))
        p.drawEllipse(QRectF(-160, 270, 980, 420))
        p.end()
        return img

    card.stories = [
        Story("City opens its first library that runs entirely on solar power",
              "https://example.com/news/1", "Morning Post", fixed - 12 * 60, None,
              picture("#3f86d8", "#f4c27c", "#fff1c2", "#2f5d3a"), feed=0),
        Story("Foldable laptops get thinner, and their hinges quieter",
              "https://example.com/news/2", "Circuit", fixed - 50 * 60, None,
              picture("#1f2340", "#7b4fd6", "#ff8fb1", "#141629"), feed=1),
        Story("Scientists map a whole coral reef from orbit for the first time",
              "https://example.com/news/3", "Field Notes", fixed - 2 * 3600, None,
              picture("#0f5c8a", "#5fd0d6", "#e8fbff", "#0b3d52"), feed=2),
        Story("Night trains return to three routes this winter",
              "https://example.com/news/4", "Morning Post", fixed - 5 * 3600, None,
              picture("#2b1d3d", "#e0795a", "#ffd9a0", "#1c1424"), feed=0),
        Story("A small studio's puzzle game tops the charts in its first week",
              "https://example.com/news/5", "Circuit", fixed - 26 * 3600, None, None, feed=1),
    ]
    card._ok = True
    card.speak()


_PREPARE = {
    "weather": weather, "stocks": stocks, "outlook": outlook, "media": media,
    "comms": comms, "system": system, "notes": _quiet_notes, "timer": timer,
    "headlines": headlines,
}


def prepare(card) -> None:
    """Fill a gallery sample with data, without starting anything."""
    if card.kind == "workspace":
        card._last_poll = time.monotonic()     # keep the stub, skip the bus
        workspace(card)
        return
    fill = _PREPARE.get(card.kind)
    if fill is not None:
        try:
            fill(card)
        except Exception:
            pass
