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


def weather(card) -> None:
    """Give the weather tile deterministic data for rendering."""
    from ..tiles.weather import Day, Forecast
    card.data = Forecast(
        place="London, GB", temp=21.0, feels=20.0, code=2,
        days=[Day("Wed", 2, 22, 14), Day("Thu", 61, 19, 13),
              Day("Fri", 0, 24, 15), Day("Sat", 95, 20, 12),
              Day("Sun", 3, 18, 11), Day("Mon", 71, 9, 2),
              Day("Tue", 45, 14, 8)],
    )


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


_PREPARE = {
    "weather": weather, "stocks": stocks, "outlook": outlook, "media": media,
    "comms": comms, "system": system, "notes": _quiet_notes,
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
