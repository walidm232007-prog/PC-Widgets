"""
signal.py — the agent activity bus writer.

Any coding agent can announce what it's doing by running this from a hook:

    python signal.py --agent codex --event PostToolUse
    python signal.py --agent codex --event Stop

One small JSON file per agent, in one directory. No sockets, no ports, no
daemon, no shared writer — each agent owns exactly one file, so two agents can
never race each other and neither has to know the other exists.

Written to be safe inside someone else's event loop:

* **It never fails the turn.** Every path is wrapped and every exit is 0. A
  broken status widget must not be able to break your agent.
* **It's fast.** Standard library only, no argparse, so process start dominates
  and that's about 50 ms. Declare the hook async if your runner supports it.
* **It's atomic.** Written to a temp file and renamed, so a reader never sees a
  half-written file.

Fields written to ``%APPDATA%\\AgentWorkspace\\<agent>.json``::

    agent    lowercase id, matches the avatar registry key   "codex"
    display  name shown in the widget                        "Codex"
    active   is it working right now                          true
    event    the hook that fired                              "PostToolUse"
    tool     tool name, when the event carries one            "Edit"
    count    tools used this turn, reset on a new prompt       12
    started  unix ts the current turn began                   1785...
    ts       unix ts of this write                            1785...

See AGENTS.md next to this file for the full contract and how to add an avatar.
"""

import json
import os
import sys
import time

STALE_AFTER = 90.0        # a turn with no events for this long is treated as over
RESURRECT_GUARD = 5.0     # ignore a late async tool write landing after Stop

# Events that mean "a turn is beginning". Anything else continues the turn.
START_EVENTS = {"UserPromptSubmit", "TurnStart", "SessionStart", "Start"}
STOP_EVENTS = {"Stop", "TurnEnd", "SessionEnd", "Idle"}


def bus_dirs() -> list:
    """Every place the bus is kept. Status is written to all of them.

    Two locations rather than one, because one is not reliably readable. On the
    machine this was built for, the board process could not import from
    ``%APPDATA%`` or write to it - while reading and writing the app folder
    perfectly, in the same process, at the same moment. The cause was never
    found. A bus that lives only under the profile is a bus that widget cannot
    see, so it lives in both and the reader takes whichever file is fresher.

    Writing 200 bytes twice costs nothing next to a status widget that is wrong.
    """
    dirs = []
    base = os.environ.get("APPDATA")
    if base:
        dirs.append(os.path.join(base, "AgentWorkspace"))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.path.join(here, ".agentbus"))
    if not dirs:                      # no APPDATA and no app folder: last resort
        dirs.append(os.path.join(os.path.expanduser("~"), "AgentWorkspace"))
    return dirs


def bus_dir() -> str:
    return bus_dirs()[0]


def _safe(agent: str) -> str:
    return "".join(c for c in agent.lower() if c.isalnum() or c in "-_") or "agent"


def bus_path(agent: str) -> str:
    return os.path.join(bus_dir(), f"{_safe(agent)}.json")


def _args(argv):
    """Tiny flag parser - argparse costs more than the rest of this script."""
    out = {}
    key = None
    for tok in argv:
        if tok.startswith("--"):
            key = tok[2:]
            out[key] = True
        elif key:
            out[key] = tok
            key = None
        else:
            out.setdefault("event", tok)
    return out


def _stdin_payload():
    """Hook runners pipe the event as JSON on stdin. Absence is fine."""
    try:
        if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def publish(agent, event, display=None, tool=None, payload=None):
    """Write one status file to every bus directory.

    Returns the state dict, or None if it declined. Success is "at least one
    copy landed" - if the profile is unwritable the app-folder copy still gets
    through, which is the whole point of there being two.
    """
    payload = payload or {}
    name = f"{_safe(agent)}.json"

    # Read back the freshest previous state, wherever it is, so the turn's tool
    # count and start time survive even if one location is unavailable.
    prev, best = {}, -1.0
    for directory in bus_dirs():
        candidate = os.path.join(directory, name)
        try:
            mtime = os.path.getmtime(candidate)
            if mtime <= best:
                continue
            with open(candidate, "r", encoding="utf-8") as fh:
                prev, best = json.load(fh), mtime
        except Exception:
            continue

    now = time.time()
    stopping = event in STOP_EVENTS

    # A tool event can arrive just after Stop when hooks run async; letting it
    # through would revive a finished turn until the staleness timeout.
    if (not stopping
            and prev.get("event") in STOP_EVENTS
            and (now - prev.get("ts", 0)) < RESURRECT_GUARD
            and event not in START_EVENTS):
        return None

    fresh = event in START_EVENTS or (now - prev.get("ts", 0)) > STALE_AFTER
    count = 0 if fresh else int(prev.get("count", 0) or 0)
    if tool:
        count += 1

    state = {
        "agent": agent.lower(),
        "display": display or prev.get("display") or agent.capitalize(),
        "active": not stopping,
        "event": event,
        "tool": tool or "",
        "count": count,
        "started": now if fresh else float(prev.get("started") or now),
        "ts": now,
    }

    landed = False
    for directory in bus_dirs():
        path = os.path.join(directory, name)
        try:
            os.makedirs(directory, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, path)       # atomic: a reader never sees a half file
            landed = True
        except Exception:
            continue                    # one location failing is survivable
    return state if landed else None


def main():
    a = _args(sys.argv[1:])
    agent = a.get("agent") or "agent"
    event = a.get("event") or "PostToolUse"
    payload = _stdin_payload()

    # Tool name may come from the flag or from the runner's event payload.
    tool = a.get("tool")
    if tool is True or not tool:
        tool = payload.get("tool_name") or payload.get("tool") or ""
    if event in STOP_EVENTS or event in START_EVENTS:
        tool = ""

    publish(agent, event, display=a.get("display"), tool=tool, payload=payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)      # a status hook must never fail the turn it reports on
