"""
calsource.py
============
Calendar events, from whichever source is configured.

**ICS** is the reliable one. Outlook can publish a calendar as a secret .ics
URL; this fetches and parses it with nothing but the standard library. Read-only
and a little stale, but it sees the actual account.

**The Windows appointment store** needs no setup at all and is tried when no ICS
URL is configured. Be warned what it actually contains: on this machine an
unpackaged app *is* granted access, but new Outlook does not feed the legacy
store and Mail & Calendar has been retired, so all that turns up is the built-in
holiday calendars. It is kept because it costs nothing, works instantly for
anyone whose account does sync, and degrades to "no events" rather than to an
error.

Both sources are polled on a worker thread by the widget. That is not optional
for the WinRT one: awaiting a WinRT operation on Qt's GUI thread deadlocks,
because the completion callback needs a message pump the event loop already
holds.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .net import tls_context

MAX_HORIZON_DAYS = 21
FETCH_TIMEOUT = 12


@dataclass
class Event:
    start: dt.datetime
    end: Optional[dt.datetime]
    title: str
    location: str = ""
    all_day: bool = False
    calendar: str = ""

    @property
    def day(self) -> dt.date:
        return self.start.date()

    def duration_text(self) -> str:
        if self.all_day:
            return "all day"
        if not self.end:
            return self.start.strftime("%H:%M")
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


@dataclass
class Agenda:
    events: List[Event] = field(default_factory=list)
    source: str = ""
    error: str = ""
    fetched: float = 0.0

    def by_day(self, days: int = 7) -> List[Tuple[dt.date, List[Event]]]:
        today = dt.date.today()
        limit = today + dt.timedelta(days=days)
        buckets: Dict[dt.date, List[Event]] = {}
        for e in self.events:
            if today <= e.day < limit:
                buckets.setdefault(e.day, []).append(e)
        return [(d, sorted(buckets[d], key=lambda x: (not x.all_day, x.start)))
                for d in sorted(buckets)]


# --------------------------------------------------------------------------- #
#  ICS
# --------------------------------------------------------------------------- #
def _unfold(text: str) -> List[str]:
    """ICS wraps long lines with a leading space or tab on the continuation."""
    out: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _unescape(v: str) -> str:
    return (v.replace("\\n", " ").replace("\\N", " ")
             .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")).strip()


def _parse_dt(value: str, params: Dict[str, str]) -> Tuple[Optional[dt.datetime], bool]:
    """Return (datetime, all_day). Times are normalised to local naive."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            d = dt.datetime.strptime(value, "%Y%m%d")
            return d, True
        except ValueError:
            return None, False
    try:
        if value.endswith("Z"):
            utc = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=dt.timezone.utc)
            return utc.astimezone().replace(tzinfo=None), False
        return dt.datetime.strptime(value, "%Y%m%dT%H%M%S"), False
    except ValueError:
        return None, False


_WEEKDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _expand(ev: Event, rrule: str, exdates: set, horizon: dt.datetime) -> List[Event]:
    """Expand a recurrence into concrete instances up to the horizon.

    Outlook publishes recurring events as one VEVENT plus an RRULE, so without
    this a weekly standup simply never appears. Handles the common shapes -
    DAILY/WEEKLY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL and weekly BYDAY -
    and gives up gracefully on anything more exotic.
    """
    parts = dict(p.split("=", 1) for p in rrule.split(";") if "=" in p)
    freq = parts.get("FREQ", "")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return [ev]

    interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until = None
    if parts.get("UNTIL"):
        until, _ = _parse_dt(parts["UNTIL"], {})

    span = (ev.end - ev.start) if ev.end else dt.timedelta(0)
    bydays = [_WEEKDAY[d[-2:]] for d in parts.get("BYDAY", "").split(",")
              if d[-2:] in _WEEKDAY]

    out: List[Event] = []
    cursor = ev.start
    made = 0
    guard = 0
    while cursor <= horizon and guard < 2000:
        guard += 1
        if until and cursor > until:
            break
        if count is not None and made >= count:
            break

        emit = cursor
        if freq == "WEEKLY" and bydays:
            # Walk the week the cursor sits in and emit each requested day.
            monday = cursor - dt.timedelta(days=cursor.weekday())
            for wd in sorted(bydays):
                inst = monday + dt.timedelta(days=wd)
                inst = inst.replace(hour=cursor.hour, minute=cursor.minute,
                                    second=cursor.second)
                if inst < ev.start or inst > horizon:
                    continue
                if until and inst > until:
                    continue
                if inst.date() in exdates:
                    continue
                out.append(Event(inst, inst + span, ev.title, ev.location,
                                 ev.all_day, ev.calendar))
                made += 1
                if count is not None and made >= count:
                    break
        elif emit.date() not in exdates:
            out.append(Event(emit, emit + span, ev.title, ev.location,
                             ev.all_day, ev.calendar))
            made += 1

        if freq == "DAILY":
            cursor += dt.timedelta(days=interval)
        elif freq == "WEEKLY":
            cursor += dt.timedelta(weeks=interval)
        elif freq == "MONTHLY":
            month = cursor.month - 1 + interval
            year = cursor.year + month // 12
            month = month % 12 + 1
            day = min(cursor.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31,
                                   30, 31, 31, 30, 31, 30, 31][month - 1])
            cursor = cursor.replace(year=year, month=month, day=day)
        else:
            try:
                cursor = cursor.replace(year=cursor.year + interval)
            except ValueError:          # 29 Feb
                cursor = cursor.replace(year=cursor.year + interval, day=28)
    return out


def parse_ics(text: str, horizon_days: int = MAX_HORIZON_DAYS) -> List[Event]:
    horizon = dt.datetime.now() + dt.timedelta(days=horizon_days)
    events: List[Event] = []
    cur: Optional[dict] = None

    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()}
            continue
        if line == "END:VEVENT":
            if cur and cur.get("start") and cur.get("title"):
                ev = Event(cur["start"], cur.get("end"), cur["title"],
                           cur.get("location", ""), cur.get("all_day", False),
                           cur.get("calendar", ""))
                if cur.get("rrule"):
                    events.extend(_expand(ev, cur["rrule"], cur["exdates"], horizon))
                else:
                    events.append(ev)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue

        head, value = line.split(":", 1)
        bits = head.split(";")
        name = bits[0].upper()
        params = {}
        for b in bits[1:]:
            if "=" in b:
                k, v = b.split("=", 1)
                params[k.upper()] = v

        if name == "DTSTART":
            cur["start"], cur["all_day"] = _parse_dt(value, params)
        elif name == "DTEND":
            cur["end"], _ = _parse_dt(value, params)
        elif name == "SUMMARY":
            cur["title"] = _unescape(value)
        elif name == "LOCATION":
            cur["location"] = _unescape(value)
        elif name == "RRULE":
            cur["rrule"] = value.strip().upper()
        elif name == "EXDATE":
            for piece in value.split(","):
                d, _ = _parse_dt(piece, params)
                if d:
                    cur["exdates"].add(d.date())

    return sorted(events, key=lambda e: e.start)


def from_ics(url: str) -> Agenda:
    import time
    req = urllib.request.Request(url, headers={"User-Agent": "pc-widgets/1.0"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT,
                                context=tls_context()) as r:
        raw = r.read().decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in raw:
        return Agenda(source="ics", error="that URL isn't an ICS calendar")
    return Agenda(events=parse_ics(raw), source="ics", fetched=time.time())


# --------------------------------------------------------------------------- #
#  Windows appointment store
# --------------------------------------------------------------------------- #
def winrt_available() -> bool:
    try:
        import winrt.windows.applicationmodel.appointments  # noqa: F401
        return True
    except Exception:
        return False


def from_winrt(days: int = MAX_HORIZON_DAYS) -> Agenda:
    import asyncio
    import time

    from winrt.windows.applicationmodel.appointments import (
        AppointmentManager, AppointmentStoreAccessType)

    async def grab():
        store = await AppointmentManager.request_store_async(
            AppointmentStoreAccessType.ALL_CALENDARS_READ_ONLY)
        start = dt.datetime.now() - dt.timedelta(hours=12)
        found = await store.find_appointments_async(
            start, dt.timedelta(days=days + 1))

        out: List[Event] = []
        seen = set()
        for a in found:
            begin = a.start_time
            if begin.tzinfo is not None:
                begin = begin.astimezone().replace(tzinfo=None)
            span = a.duration if a.duration else dt.timedelta(0)
            title = (a.subject or "").strip() or "(no title)"
            # The holiday calendars are registered several times over, so the
            # same event arrives two or three times.
            key = (begin, title)
            if key in seen:
                continue
            seen.add(key)
            out.append(Event(begin, begin + span, title,
                             (a.location or "").strip(), bool(a.all_day),
                             str(a.calendar_id or "")))
        return Agenda(events=sorted(out, key=lambda e: e.start),
                      source="windows", fetched=time.time())

    return asyncio.run(grab())


# --------------------------------------------------------------------------- #
class CalendarSource:
    """Picks a source and fetches from it. Call from a worker thread."""

    def __init__(self, ics_url: str = "") -> None:
        self.ics_url = (ics_url or "").strip()

    @property
    def kind(self) -> str:
        if self.ics_url:
            return "ics"
        return "windows" if winrt_available() else "none"

    def fetch(self) -> Agenda:
        if self.ics_url:
            try:
                return from_ics(self.ics_url)
            except Exception as exc:
                return Agenda(source="ics", error=type(exc).__name__)
        if winrt_available():
            try:
                return from_winrt()
            except Exception as exc:
                return Agenda(source="windows", error=type(exc).__name__)
        return Agenda(error="no calendar source configured")
