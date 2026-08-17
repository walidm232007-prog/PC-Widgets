"""
agents.py
=========
Reader for the agent activity bus that ``agents/agentsignal.py`` writes.

One file per agent in one directory, so this is a directory listing plus a JSON
read. Polling beats watching here: writes are atomic renames, the widget already
has a timer, and a missed event should degrade into "goes idle a moment late"
rather than into a status that sticks.

A known roster is kept so a bay can be drawn for an agent that has never run -
an empty workspace with two dark bays says more than an empty widget does.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

STALE_AFTER = 90.0        # matches agentsignal.py
FADE_AFTER = 6.0          # seconds after Stop that a bay still reads as "just done"

# Agents the workspace draws whether or not they have ever reported in.
# Order is display order.
KNOWN = (("claude", "Claude"), ("codex", "Codex"))


def bus_dirs() -> List[str]:
    """Everywhere an agent might have written its status.

    Must match ``agents/signal.py::bus_dirs``. Two locations because the board
    could not read ``%APPDATA%`` on the machine this was built for - not the
    media library, not its own log - while reading the app folder in the same
    process without trouble. The writer puts a copy in both; this takes
    whichever is fresher, so it works whichever location a given process can
    actually see.
    """
    dirs = []
    base = os.environ.get("APPDATA")
    if base:
        dirs.append(os.path.join(base, "AgentWorkspace"))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirs.append(os.path.join(here, ".agentbus"))
    if not dirs:
        dirs.append(os.path.join(os.path.expanduser("~"), "AgentWorkspace"))
    return dirs


def bus_dir() -> str:
    return bus_dirs()[0]


@dataclass
class AgentState:
    agent: str
    display: str
    active: bool = False
    event: str = ""
    tool: str = ""
    count: int = 0
    started: float = 0.0
    ts: float = 0.0
    seen: bool = False           # has this agent ever written a status file

    # Derived each poll.
    just_finished: bool = False
    elapsed: float = 0.0

    @property
    def status(self) -> str:
        if not self.seen:
            return "not connected"
        if self.active:
            return "working"
        if self.just_finished:
            return "done"
        return "idle"


class AgentBus:
    """Polls the bus directory and reports one AgentState per known agent."""

    def __init__(self) -> None:
        self.states: Dict[str, AgentState] = {
            key: AgentState(key, name) for key, name in KNOWN
        }
        self._mtimes: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    def poll(self) -> List[AgentState]:
        now = time.time()

        # One entry per agent, taken from whichever bus directory holds the
        # newer copy. A location that cannot be listed is skipped rather than
        # allowed to empty the workspace - "no agents" and "I could not look"
        # are very different things and used to look identical.
        newest: Dict[str, tuple] = {}
        for directory in bus_dirs():
            try:
                entries = [e for e in os.listdir(directory) if e.endswith(".json")]
            except OSError:
                continue
            for entry in entries:
                path = os.path.join(directory, entry)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                key = entry[:-5].lower()
                if key not in newest or mtime > newest[key][0]:
                    newest[key] = (mtime, path)

        for key, (mtime, path) in newest.items():
            st = self.states.get(key)
            if st is None:
                # An agent we did not know about announced itself; give it a bay.
                st = AgentState(key, key.capitalize())
                self.states[key] = st

            if self._mtimes.get(key) != mtime:
                self._mtimes[key] = mtime
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                except Exception:
                    continue        # mid-write; try again next poll
                st.display = str(raw.get("display") or st.display)
                st.active = bool(raw.get("active"))
                st.event = str(raw.get("event", ""))
                st.tool = str(raw.get("tool", ""))
                st.count = int(raw.get("count", 0) or 0)
                st.started = float(raw.get("started") or 0.0)
                st.ts = float(raw.get("ts") or 0.0)
                st.seen = True

        for st in self.states.values():
            # Wall clock, because the file's timestamps are wall clock too.
            if st.active and st.ts and (now - st.ts) > STALE_AFTER:
                st.active = False
            st.just_finished = (
                not st.active and st.seen and st.ts > 0
                and (now - st.ts) < FADE_AFTER
            )
            st.elapsed = max(0.0, now - st.started) if st.started else 0.0

        order = {key: i for i, (key, _) in enumerate(KNOWN)}
        return sorted(self.states.values(),
                      key=lambda s: (order.get(s.agent, 99), s.agent))

    def any_active(self) -> bool:
        return any(s.active for s in self.states.values())
