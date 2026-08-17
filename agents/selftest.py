"""
selftest.py — prove the mailbox does what it claims.

    python selftest.py

Runs against a throwaway mailbox (``APPDATA`` is redirected), so it never touches
the real conversation.

The interesting one is ``concurrent_writes``. The whole design rests on "no
shared writer, so two agents cannot lose each other's messages", and that is
exactly the sort of claim that is true right up until someone adds an
index file. If this passes, the guarantee is real.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agentmail as mb

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(label)


# --------------------------------------------------------------------------- #
def round_trip():
    print("round trip")
    m = mb.send("claude", "codex", "hello", body="line one\nline two",
                kind="note", refs=["a/b.py"])
    check("send returns a message", m is not None)
    check("id sorts chronologically", m.id[:19].isdigit())
    got = mb.load()
    check("one message on disk", len(got) == 1, f"got {len(got)}")
    check("body survived newlines", got[0].body == "line one\nline two")
    check("refs survived", got[0].refs == ["a/b.py"])
    check("thread derived from subject", got[0].thread == "hello")


def unread_isolation():
    print("unread is per-reader, and never your own")
    check("codex has it unread", len(mb.unread("codex")) == 1)
    check("claude does not see its own", len(mb.unread("claude")) == 0)
    mb.mark_all_read("codex")
    check("codex inbox now empty", len(mb.unread("codex")) == 0)
    check("the message still exists", len(mb.load()) == 1)

    mb.send("codex", mb.EVERYONE, "broadcast")
    check("everyone-addressed reaches claude", len(mb.unread("claude")) == 1)
    check("but not its own sender", len(mb.unread("codex")) == 0)
    mb.mark_all_read("claude")


def threading_replies():
    print("replies stay in one thread")
    first = mb.send("claude", "codex", "Which palette for the bays?", kind="ask")
    answer = mb.send("codex", "claude", "the dark one", kind="answer",
                     reply_to=first.id)
    check("reply inherits the thread", answer.thread == first.thread,
          f"{answer.thread!r} != {first.thread!r}")
    strand = mb.load(thread=first.thread)
    check("both in the strand", len(strand) == 2, f"got {len(strand)}")


def claims():
    print("claims act as a soft lock")
    mb.send("codex", mb.EVERYONE, "editing avatars", kind="claim",
            refs=["widgets/core/avatars.py", "./widgets\\core/agents.py"])
    held = mb.claims()
    check("both paths held", len(held) == 2, f"{sorted(held)}")
    check("path separators normalised",
          "widgets/core/agents.py" in held, f"{sorted(held)}")
    check("claude sees the conflict",
          mb.claimed_by_other("claude", ["widgets/core/avatars.py"]) ==
          [("widgets/core/avatars.py", "codex")])
    check("codex does not conflict with itself",
          mb.claimed_by_other("codex", ["widgets/core/avatars.py"]) == [])

    mb.send("claude", mb.EVERYONE, "nice try", kind="release",
            refs=["widgets/core/avatars.py"])
    check("a release from the wrong agent is ignored",
          "widgets/core/avatars.py" in mb.claims())

    mb.send("codex", mb.EVERYONE, "done", kind="release",
            refs=["widgets/core/avatars.py"])
    check("the holder can release",
          "widgets/core/avatars.py" not in mb.claims())


def corrupt_tolerance():
    print("a bad file is skipped, not fatal")
    before = len(mb.load())
    bad = os.path.join(mb.mail_dir(), f"{time.time_ns():019d}-codex-ffffff.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    check("load still works", len(mb.load()) == before, "corrupt file leaked in")

    stray = os.path.join(mb.mail_dir(), "notes.json")
    with open(stray, "w", encoding="utf-8") as fh:
        json.dump({"sender": "nobody"}, fh)
    check("a file that isn't a message id is ignored",
          len(mb.load()) == before)
    os.remove(bad)
    os.remove(stray)


def concurrent_writes():
    print("concurrent writes lose nothing")
    mb._prune(0)                      # start from empty
    for n in os.listdir(mb.mail_dir()):
        if n.startswith("seen-"):
            os.remove(os.path.join(mb.mail_dir(), n))

    WRITERS, EACH = 8, 12
    errors = []

    def writer(i):
        who = "claude" if i % 2 else "codex"
        for j in range(EACH):
            try:
                if mb.send(who, mb.EVERYONE, f"w{i}-m{j}") is None:
                    errors.append(f"w{i}-m{j} returned None")
            except Exception as exc:
                errors.append(f"w{i}-m{j}: {exc!r}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(WRITERS)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.perf_counter() - t0

    want = WRITERS * EACH
    got = mb.load()
    check("no writer errored", not errors, "; ".join(errors[:3]))
    check(f"all {want} messages present", len(got) == want, f"got {len(got)}")
    check("every id is unique", len({m.id for m in got}) == len(got))
    check("all readable", all(m.subject.startswith("w") for m in got))
    subjects = {f"w{i}-m{j}" for i in range(WRITERS) for j in range(EACH)}
    check("none lost", subjects == {m.subject for m in got},
          f"missing {sorted(subjects - {m.subject for m in got})[:3]}")
    print(f"        ({want} messages from {WRITERS} writers in {dt * 1000:.0f} ms)")


def pruning():
    print("the log stays bounded")
    mb._prune(10)
    check("pruned to the cap", len(mb.load()) == 10, f"got {len(mb.load())}")
    newest = mb.load()[-1]
    check("the newest survived", newest is not None)


# --------------------------------------------------------------------------- #
def main():
    real = os.environ.get("APPDATA")
    sandbox = tempfile.mkdtemp(prefix="mailbox-selftest-")
    os.environ["APPDATA"] = sandbox
    print(f"sandbox: {mb.mail_dir()}\n")
    try:
        round_trip()
        unread_isolation()
        threading_replies()
        claims()
        corrupt_tolerance()
        concurrent_writes()
        pruning()
    finally:
        if real:
            os.environ["APPDATA"] = real
        shutil.rmtree(sandbox, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
