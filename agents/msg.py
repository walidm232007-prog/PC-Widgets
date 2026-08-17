"""
msg.py — talk to the other agent working this project.

    python msg.py inbox                          anything new for me
    python msg.py send codex "Media buttons are frosted now"
    python msg.py send codex "Which palette?" --kind ask --body "..."
    python msg.py reply <id> "Use the dark one"
    python msg.py log -n 20                      the whole conversation
    python msg.py log --thread media-buttons     one strand of it
    python msg.py claim widgets/tiles/media.py   I'm in here, keep out
    python msg.py release widgets/tiles/media.py done, it's yours
    python msg.py claims                         who is holding what
    python msg.py check widgets/core/card.py     is this file free?
    python msg.py wait --timeout 120             block until they answer

Who you are is worked out from the environment (``CLAUDECODE`` means claude,
``CODEX_*`` means codex) and can be forced with ``--as codex`` or ``AGENT_ID``.

Reading the inbox marks it read. ``--peek`` looks without marking.

Exit codes are meaningful, so a hook or a script can branch on them:
``0`` fine, ``1`` bad usage, ``2`` nothing found (empty inbox, no such message,
``wait`` timed out), ``3`` a path is claimed by the other agent.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agentmail as mb                                      # noqa: E402

OK, USAGE, NOTHING, CLAIMED = 0, 1, 2, 3

# Message bodies are written by agents and will contain em dashes, arrows and
# whatever else. The console encoding is not ours to choose - cp1252 on Windows -
# so make the streams tolerant rather than letting a quote mark crash a hook.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
def _parse(argv):
    """Positionals plus --flags. argparse costs more than everything else here."""
    args, flags, key = [], {}, None
    for tok in argv:
        if tok.startswith("--"):
            key = tok[2:]
            flags[key] = True
        elif tok.startswith("-") and len(tok) == 2 and not tok[1].isdigit():
            key = tok[1:]
            flags[key] = True
        elif key is not None:
            flags[key] = tok
            key = None
        else:
            args.append(tok)
    return args, flags


def _me(flags) -> str:
    who = mb.whoami(flags.get("as") if isinstance(flags.get("as"), str) else "")
    if not who:
        print("Can't tell which agent you are. Pass --as claude|codex, "
              "or set AGENT_ID.", file=sys.stderr)
        sys.exit(USAGE)
    return who


def _other(me: str) -> str:
    """The obvious counterpart, so `send` can leave the recipient out."""
    return "codex" if me == "claude" else "claude"


def _show(m: mb.Message, body: bool = True, indent: str = "") -> None:
    print(f"{indent}{m.when():<10} {m.one_line()}")
    if m.refs:
        print(f"{indent}           files: {', '.join(m.refs)}")
    if body and m.body:
        for line in m.body.splitlines():
            print(f"{indent}           {line}")
    print(f"{indent}           id {m.id}")


# --------------------------------------------------------------------------- #
def cmd_send(args, flags, me) -> int:
    if not args:
        print("usage: msg.py send [to] \"subject\" [--body ...] [--kind ...]",
              file=sys.stderr)
        return USAGE

    # "send codex subject" or just "send subject" - the recipient is optional
    # because with two agents there is only ever one other one.
    known = {"claude", "codex", mb.EVERYONE}
    if len(args) > 1 and mb.slug(args[0]) in known:
        to, subject = mb.slug(args[0]), args[1]
    else:
        to, subject = _other(me), args[0]

    refs = flags.get("ref") or flags.get("refs") or ""
    refs = [r for r in str(refs).replace(",", " ").split() if r] if refs is not True else []
    kind = flags.get("kind") if isinstance(flags.get("kind"), str) else "note"
    body = flags.get("body") if isinstance(flags.get("body"), str) else ""
    reply = flags.get("reply-to") if isinstance(flags.get("reply-to"), str) else ""

    if kind not in mb.KINDS:
        print(f"unknown kind {kind!r}. one of: {', '.join(sorted(mb.KINDS))}",
              file=sys.stderr)
        return USAGE

    msg = mb.send(me, to, subject, body=body, kind=kind, refs=refs, reply_to=reply)
    if msg is None:
        print("could not write the message", file=sys.stderr)
        return USAGE
    print(f"sent to {msg.to}: [{msg.kind}] {msg.subject}")
    print(f"  id {msg.id}   thread {msg.thread}")
    return OK


def cmd_reply(args, flags, me) -> int:
    if len(args) < 2:
        print("usage: msg.py reply <id> \"subject\" [--body ...]", file=sys.stderr)
        return USAGE
    target = next((m for m in mb.load() if m.id == args[0]
                   or m.id.endswith(args[0])), None)
    if target is None:
        print(f"no message matching {args[0]!r}", file=sys.stderr)
        return NOTHING
    kind = flags.get("kind") if isinstance(flags.get("kind"), str) else "answer"
    body = flags.get("body") if isinstance(flags.get("body"), str) else ""
    msg = mb.send(me, target.sender, args[1], body=body, kind=kind,
                  reply_to=target.id)
    if msg is None:
        return USAGE
    print(f"replied to {target.sender}: [{msg.kind}] {msg.subject}")
    return OK


def cmd_inbox(args, flags, me) -> int:
    got = mb.unread(me)
    if not got:
        if not flags.get("quiet"):
            print(f"nothing new for {me}.")
        return NOTHING
    print(f"{len(got)} new for {me}:\n")
    for m in got:
        _show(m)
        print()
    if not flags.get("peek"):
        mb.mark_all_read(me)
        print("(marked read)")
    return OK


def cmd_context(args, flags, me) -> int:
    """Inbox as a block to inject into an agent's context. Silent when empty.

    Deliberately terse and prefixed: it lands in the middle of a session, so it
    has to be obvious where it came from and cost nothing when there's no mail.

    **Always exits 0**, unlike every other command here. This one is meant to be
    run from a UserPromptSubmit hook, where exit code 2 means "blocking error"
    and stops the prompt being processed - so returning NOTHING for an empty
    inbox, which is the normal case, would block the user's every turn. A caller
    that wants to know whether there was mail can look at stdout.
    """
    got = mb.unread(me)
    if not got:
        return OK
    print(f"[agent mailbox] {len(got)} message(s) for you from the other agent "
          f"working this project. Reply with: python "
          f"{os.path.join('pc-widgets', 'agents', 'msg.py')} reply <id> \"...\"")
    for m in got:
        print(f"- {m.sender} ({m.when()}) [{m.kind}] {m.subject}")
        if m.refs:
            print(f"    files: {', '.join(m.refs)}")
        for line in m.body.splitlines():
            print(f"    {line}")
        print(f"    id {m.id}")
    held = mb.claims()
    mine_not = {p: w for p, (w, _t, _s) in held.items() if w != me}
    if mine_not:
        print("[agent mailbox] files claimed by the other agent right now: "
              + ", ".join(sorted(mine_not)))
    if not flags.get("peek"):
        mb.mark_all_read(me)
    return OK


def cmd_log(args, flags, me) -> int:
    n = flags.get("n") or flags.get("tail") or 25
    try:
        n = int(n) if n is not True else 25
    except ValueError:
        n = 25
    thread = flags.get("thread") if isinstance(flags.get("thread"), str) else ""
    msgs = mb.load(limit=n, thread=thread)
    if not msgs:
        print("no messages yet." if not thread else f"nothing in thread {thread!r}.")
        return NOTHING
    seen = mb._seen_ids(me)
    for m in msgs:
        flag = " " if (m.sender == me or m.id in seen) else "*"
        print(f"{flag}", end="")
        _show(m, body=bool(flags.get("full")))
    return OK


def cmd_claim(args, flags, me) -> int:
    if not args:
        print("usage: msg.py claim <path> [path...]", file=sys.stderr)
        return USAGE
    taken = mb.claimed_by_other(me, args)
    if taken and not flags.get("force"):
        for path, who in taken:
            print(f"{path} is already claimed by {who}", file=sys.stderr)
        print("pass --force to claim anyway", file=sys.stderr)
        return CLAIMED
    subject = flags.get("why") if isinstance(flags.get("why"), str) else \
        f"working in {len(args)} file(s)"
    msg = mb.send(me, mb.EVERYONE, subject, kind="claim", refs=args)
    if msg is None:
        return USAGE
    print(f"claimed: {', '.join(args)}")
    return OK


def cmd_release(args, flags, me) -> int:
    held = [p for p, (w, _t, _s) in mb.claims().items() if w == me]
    paths = args or held
    if not paths:
        print("you are not holding anything.")
        return NOTHING
    msg = mb.send(me, mb.EVERYONE, "released", kind="release", refs=paths)
    if msg is None:
        return USAGE
    print(f"released: {', '.join(paths)}")
    return OK


def cmd_claims(args, flags, me) -> int:
    held = mb.claims()
    if not held:
        print("nothing is claimed.")
        return NOTHING
    for path, (who, ts, why) in sorted(held.items()):
        mark = "you" if who == me else who
        age = mb.Message(id="", ts=ts, sender="", to="").when()
        print(f"{path}  --  {mark}, {age}" + (f"  ({why})" if why else ""))
    return OK


def cmd_check(args, flags, me) -> int:
    """Is it safe for me to edit these? Exit 3 if not, so scripts can branch."""
    if not args:
        print("usage: msg.py check <path> [path...]", file=sys.stderr)
        return USAGE
    taken = mb.claimed_by_other(me, args)
    if not taken:
        print(f"clear: {', '.join(args)}")
        return OK
    for path, who in taken:
        print(f"{path} is claimed by {who}")
    return CLAIMED


def cmd_wait(args, flags, me) -> int:
    try:
        timeout = float(flags.get("timeout") or 300)
    except (TypeError, ValueError):
        timeout = 300.0
    print(f"waiting up to {timeout:.0f}s for a message to {me}...", flush=True)
    got = mb.wait_for(me, timeout=timeout)
    if not got:
        print("timed out, nothing arrived.")
        return NOTHING
    for m in got:
        _show(m)
    mb.mark_all_read(me)
    return OK


def cmd_status(args, flags, me) -> int:
    s = mb.stats()
    print(f"you are: {me}")
    # Both locations, because "which one is it in" is the first question when
    # one process can see the mailbox and another cannot.
    for i, d in enumerate(s.get("dirs") or [s["dir"]]):
        print(f"mailbox: {d}" if i == 0 else f"     and: {d}")
    print(f"messages: {s['messages']}   unread for you: {len(mb.unread(me))}")
    print(f"senders: {', '.join(s['senders']) or 'none'}")
    print(f"threads: {', '.join(s['threads']) or 'none'}")
    print(f"claims held: {s['claims']}")
    return OK


COMMANDS = {
    "send": cmd_send, "reply": cmd_reply, "inbox": cmd_inbox,
    "context": cmd_context, "log": cmd_log, "claim": cmd_claim,
    "release": cmd_release, "claims": cmd_claims, "check": cmd_check,
    "wait": cmd_wait, "status": cmd_status,
}


def main(argv) -> int:
    args, flags = _parse(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return OK if args else USAGE
    cmd = COMMANDS.get(args[0])
    if cmd is None:
        print(f"unknown command {args[0]!r}. one of: {', '.join(COMMANDS)}",
              file=sys.stderr)
        return USAGE
    return cmd(args[1:], flags, _me(flags))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(NOTHING)
