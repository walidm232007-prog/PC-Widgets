"""
mailbox.py — a shared mailbox for coding agents working the same project.

The activity bus next door answers "is anyone working?". This answers "what did
they say?". Two agents in one folder need to tell each other things: what they
just changed, what they need decided, and which files they are in the middle of
so the other one keeps out.

Design follows the bus, for the same reason: **no shared writer anywhere.**

* One file per message, in one directory, named so a plain sort is chronological.
  A sender only ever creates new files, so two agents writing at the same instant
  cannot collide - there is nothing to merge.
* Written to a temp name and renamed, so a reader never sees half a message.
* Read state lives in a separate file per reader (``seen-<agent>.json``), because
  marking a message read is the *reader* writing. Mutating the message itself
  would put two writers on one file, which is exactly what this avoids.
* Polling, not watching. Both agents run in turns and a message that arrives a
  second late costs nothing.

There is no daemon, no port, no lock file, and nothing to start. If neither agent
has ever run, the directory does not exist and every read returns empty.

Message kinds are a small deliberate vocabulary - see :data:`KINDS`. ``claim``
and ``release`` are the load-bearing pair: they turn the mailbox into a soft lock
over files, which is the one thing two agents editing one repo actually need.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# What a message is for. Kept short on purpose: a long list of kinds means
# nobody picks the right one.
KINDS = {
    "note":    "here is something you should know",
    "ask":     "I need an answer before I can go on",
    "answer":  "replying to an ask",
    "claim":   "I am working in these files, keep out",
    "release": "done with those files, they are yours",
    "blocked": "I am stuck on something you own",
    "done":    "I finished a piece of work",
}

EVERYONE = "all"
KEEP_MESSAGES = 400       # oldest beyond this are dropped on write
_ID_RE = re.compile(r"^\d{19}-[a-z0-9_-]+-[0-9a-f]{6}$")


# --------------------------------------------------------------------------- #
#  Where it lives
# --------------------------------------------------------------------------- #
def bus_dirs() -> List[str]:
    """Everywhere the mailbox is kept. Must match ``widgets/core/agents.py``.

    Two locations, for the reason documented there: the widget process could not
    read ``%APPDATA%`` on the machine this was built for - not the Python
    libraries it needed, not its own log, not the agent bus - while reading the
    app folder perfectly, in the same process, at the same moment. Nobody found
    out why.

    Messages are written to both and read as the union of both, so an agent and
    a widget that can each only see one of the two still share a mailbox.
    """
    dirs = []
    base = os.environ.get("APPDATA")
    if base:
        dirs.append(os.path.join(base, "AgentWorkspace"))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.path.join(here, ".agentbus"))
    if not dirs:
        dirs.append(os.path.join(os.path.expanduser("~"), "AgentWorkspace"))
    return dirs


def bus_dir() -> str:
    return bus_dirs()[0]


def mail_dirs() -> List[str]:
    return [os.path.join(d, "mail") for d in bus_dirs()]


def mail_dir() -> str:
    return mail_dirs()[0]


def _find(name: str) -> Optional[str]:
    """The freshest copy of one mailbox file, wherever it lives."""
    best, at = None, -1.0
    for directory in mail_dirs():
        path = os.path.join(directory, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > at:
            best, at = path, mtime
    return best


def _names() -> List[str]:
    """Every message/seen file name across all locations, de-duplicated.

    A directory that cannot be listed is skipped rather than treated as empty -
    "no messages" and "I could not look" must not be the same answer.
    """
    found = set()
    for directory in mail_dirs():
        try:
            found.update(os.listdir(directory))
        except OSError:
            continue
    return sorted(found)


def seen_path(agent: str) -> str:
    return os.path.join(mail_dir(), f"seen-{slug(agent)}.json")


def slug(text: str) -> str:
    out = "".join(c if (c.isalnum() or c in "-_") else "-"
                  for c in (text or "").strip().lower())
    return re.sub(r"-{2,}", "-", out).strip("-") or "agent"


# --------------------------------------------------------------------------- #
#  Identity
# --------------------------------------------------------------------------- #
def whoami(explicit: str = "") -> str:
    """Which agent am I?

    Explicit wins, then ``AGENT_ID``, then the runner's own environment - each
    CLI leaves its fingerprints there, and guessing from those beats defaulting
    to a name and having one agent post as the other.
    """
    if explicit:
        return slug(explicit)
    if os.environ.get("AGENT_ID"):
        return slug(os.environ["AGENT_ID"])
    keys = os.environ.keys()
    if any(k == "CLAUDECODE" or k.startswith("CLAUDE_CODE") for k in keys):
        return "claude"
    if any(k.startswith("CODEX") for k in keys):
        return "codex"
    return ""


# --------------------------------------------------------------------------- #
#  Messages
# --------------------------------------------------------------------------- #
@dataclass
class Message:
    id: str
    ts: float
    sender: str
    to: str
    kind: str = "note"
    subject: str = ""
    body: str = ""
    refs: List[str] = field(default_factory=list)
    reply_to: str = ""
    thread: str = ""

    # ------------------------------------------------------------------ #
    def addressed_to(self, agent: str) -> bool:
        return self.to in (slug(agent), EVERYONE)

    def age(self) -> float:
        return max(0.0, time.time() - self.ts)

    def when(self) -> str:
        """Short relative time - what you want when scanning a log."""
        s = self.age()
        if s < 45:
            return "just now"
        if s < 3600:
            return f"{int(s // 60)}m ago"
        if s < 86400:
            return f"{int(s // 3600)}h ago"
        return time.strftime("%d %b %H:%M", time.localtime(self.ts))

    def one_line(self) -> str:
        # ASCII only. This goes to a console whose encoding is not ours to
        # choose - on Windows that is cp1252, and a stray arrow raised
        # UnicodeEncodeError right in the middle of printing the inbox.
        head = f"[{self.kind}] {self.subject}" if self.subject else f"[{self.kind}]"
        return f"{self.sender} -> {self.to}  {head}"


def _new_id(sender: str) -> str:
    # Nanoseconds first, so sorting filenames sorts the conversation. The random
    # tail is what makes two agents writing in the same nanosecond safe.
    return f"{time.time_ns():019d}-{slug(sender)}-{uuid.uuid4().hex[:6]}"


def _thread_for(subject: str, reply_to: str) -> str:
    """Which strand this belongs to.

    A reply inherits its parent's thread, which needs a lookup; anything else is
    just its own subject, which needs nothing. Worth the branch - resolving the
    thread by loading the whole log made every send O(number of messages), i.e.
    400 file reads to post one line.
    """
    if reply_to:
        parent = next((m for m in load() if m.id == reply_to), None)
        if parent is not None:
            return parent.thread or slug(parent.subject)
    return slug(subject)[:48] or "general"


def send(sender: str, to: str, subject: str, body: str = "",
         kind: str = "note", refs: Iterable[str] = (),
         reply_to: str = "") -> Optional[Message]:
    """Post one message. Returns it, or None if it could not be written."""
    sender = slug(sender)
    to = slug(to) if to else EVERYONE
    if kind not in KINDS:
        kind = "note"

    msg = Message(
        id=_new_id(sender), ts=time.time(), sender=sender, to=to, kind=kind,
        subject=subject.strip(), body=body.rstrip(),
        refs=[str(r) for r in refs if str(r).strip()],
        reply_to=reply_to.strip(),
        thread=_thread_for(subject, reply_to.strip()),
    )

    payload = asdict(msg)
    landed = False
    for directory in mail_dirs():
        try:
            os.makedirs(directory, exist_ok=True)
            final = os.path.join(directory, msg.id + ".json")
            tmp = final + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, final)      # atomic: no reader sees half a message
            landed = True
        except Exception:
            continue                    # one location failing is survivable
    if not landed:
        return None

    _prune()
    return msg


def load(limit: Optional[int] = None, thread: str = "",
         since: float = 0.0) -> List[Message]:
    """Every message, oldest first. Unreadable files are skipped, not fatal.

    Read as the union of every mailbox location - message ids are unique and the
    filename *is* the id, so the same message present in both places is one
    entry, not two.
    """
    names = [n for n in _names()
             if n.endswith(".json") and _ID_RE.match(n[:-5] or "")]

    out: List[Message] = []
    for name in names:
        path = _find(name)
        if path is None:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            continue            # mid-write or corrupt; it will be there next poll
        msg = Message(
            id=str(raw.get("id") or name[:-5]),
            ts=float(raw.get("ts") or 0.0),
            sender=slug(raw.get("sender", "")),
            to=slug(raw.get("to", EVERYONE)),
            kind=str(raw.get("kind", "note")),
            subject=str(raw.get("subject", "")),
            body=str(raw.get("body", "")),
            refs=[str(r) for r in (raw.get("refs") or [])],
            reply_to=str(raw.get("reply_to", "")),
            thread=str(raw.get("thread", "")),
        )
        if since and msg.ts <= since:
            continue
        if thread and msg.thread != slug(thread):
            continue
        out.append(msg)

    return out[-limit:] if limit else out


# --------------------------------------------------------------------------- #
#  Read state
# --------------------------------------------------------------------------- #
def _seen_ids(agent: str) -> set:
    """This agent's read cursor, merged across locations.

    Union rather than newest-wins: a message read while only one location was
    reachable is still read, and re-showing something already answered is worse
    than the file being slightly larger than it needs to be.
    """
    name = f"seen-{slug(agent)}.json"
    ids: set = set()
    for directory in mail_dirs():
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8") as fh:
                ids.update(json.load(fh).get("ids") or [])
        except Exception:
            continue
    return ids


def _write_seen(agent: str, ids: Iterable[str]) -> bool:
    """Only this agent ever writes its own cursor, so no locking is needed."""
    name = f"seen-{slug(agent)}.json"
    payload = {"ids": sorted(ids), "ts": time.time()}
    landed = False
    for directory in mail_dirs():
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
            landed = True
        except Exception:
            continue
    return landed


def unread(agent: str) -> List[Message]:
    """Messages for me that I have not marked read. My own never count."""
    me = slug(agent)
    seen = _seen_ids(me)
    return [m for m in load()
            if m.sender != me and m.addressed_to(me) and m.id not in seen]


def mark_read(agent: str, ids: Iterable[str]) -> int:
    me = slug(agent)
    # Prune to messages that still exist, so the cursor cannot grow forever.
    alive = {m.id for m in load()}
    keep = (_seen_ids(me) | {i for i in ids}) & alive
    before = len(_seen_ids(me) & alive)
    _write_seen(me, keep)
    return len(keep) - before


def mark_all_read(agent: str) -> int:
    return mark_read(agent, [m.id for m in unread(agent)])


def wait_for(agent: str, timeout: float = 300.0,
             poll: float = 1.0) -> List[Message]:
    """Block until something arrives for me, or the timeout runs out.

    For a genuine handoff - "I need your answer before I can finish" - where the
    alternative is ending the turn and hoping the user relays it.
    """
    deadline = time.time() + max(0.0, timeout)
    while True:
        got = unread(agent)
        if got or time.time() >= deadline:
            return got
        time.sleep(max(0.2, poll))


# --------------------------------------------------------------------------- #
#  Claims: a soft lock over files
# --------------------------------------------------------------------------- #
def claims() -> Dict[str, Tuple[str, float, str]]:
    """Which files are currently claimed, and by whom.

    Replayed from the message log rather than stored: a ``claim`` puts a path in,
    a ``release`` from the same agent takes it out. Cheap, and it means the log
    is the only state - nothing can drift out of sync with it.
    """
    held: Dict[str, Tuple[str, float, str]] = {}
    for m in load():
        for ref in m.refs:
            key = ref.replace("\\", "/").strip().lstrip("./")
            if m.kind == "claim":
                held[key] = (m.sender, m.ts, m.subject)
            elif m.kind == "release" and key in held and held[key][0] == m.sender:
                del held[key]
    return held


def claimed_by_other(agent: str, paths: Iterable[str]) -> List[Tuple[str, str]]:
    """Any of these paths held by someone else, as (path, holder) pairs."""
    me = slug(agent)
    held = claims()
    out = []
    for p in paths:
        key = str(p).replace("\\", "/").strip().lstrip("./")
        who = held.get(key, (None,))[0]
        if who and who != me:
            out.append((key, who))
    return out


# --------------------------------------------------------------------------- #
def _prune(keep: int = KEEP_MESSAGES) -> None:
    """Drop the oldest messages once the log gets long. Never touches cursors.

    Decided on the union of names, then removed from every location, so the two
    copies stay in step and a message cannot come back from the dead because one
    directory still had it.
    """
    names = [n for n in _names()
             if n.endswith(".json") and _ID_RE.match(n[:-5] or "")]
    # Count from the front rather than slicing from the back: names[:-keep]
    # with keep == 0 is names[:0], i.e. the empty list, so _prune(0) quietly
    # kept everything instead of clearing the log.
    cut = len(names) - max(0, keep)
    for name in names[:cut] if cut > 0 else []:
        for directory in mail_dirs():
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass


def stats() -> Dict[str, object]:
    msgs = load()
    return {
        "messages": len(msgs),
        "senders": sorted({m.sender for m in msgs}),
        "threads": sorted({m.thread for m in msgs if m.thread}),
        "claims": len(claims()),
        "dir": mail_dir(),
        "dirs": mail_dirs(),
    }
