"""
python -m airdrop - the core from a terminal.

    python -m airdrop find
    python -m airdrop send "Sam’s iPhone" photo.jpg [more files...]
    python -m airdrop receive [--inbox ~/Downloads]

The other device has to be in Everyone mode (Everyone for 10 Minutes, on an
iPhone) with its screen on. ``send`` wakes nearby iPhones where it can - on a
Mac by opening Finder's AirDrop window.

``receive`` asks in the terminal before taking anything. With no terminal to
ask in, it declines everything - unless ``--accept-from`` names a sender to
take without asking, which is for testing with your own phone.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import socket
import sys
import threading
import time

from . import client, identity, link, server


def _name() -> str:
    host = socket.gethostname()
    return host[:-6] if host.endswith(".local") else host


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return str(n)


def _icon(paths) -> bytes:
    """A JPEG 2000 thumbnail of the first photo, as an iPhone sends - if Pillow is here."""
    try:
        from PIL import Image
        im = Image.open(paths[0])
        im.thumbnail((540, 540))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG2000")
        return out.getvalue()
    except Exception:
        return b""


def cmd_find(args, lk, ident) -> int:
    print("Looking for AirDrop receivers - Ctrl+C to stop early ...")
    if link.wake_nearby():
        print("(opened Finder's AirDrop window to wake nearby iPhones)")
    s = client.Sender(lk, ident, args.name)
    try:
        found = s.find(args.seconds, lambda r: print(f"  {r.name}   (ID {r.ident})"))
    except KeyboardInterrupt:
        return 0
    if not found:
        print("Nothing answered. Is the other device in Everyone mode, awake, and close by?")
    return 0


def cmd_send(args, lk, ident) -> int:
    for p in args.files:
        if not os.path.isfile(p):
            print(f"not a file: {p}", file=sys.stderr)
            return 2
    if not args.no_wake and link.wake_nearby():
        print("Opened Finder's AirDrop window to wake nearby iPhones.")
    elif link.wake_hint():
        print(f"Note: {link.wake_hint()}")
    print(f"Looking for {args.target!r} ...")
    last = [0.0]

    def event(kind: str, d: dict) -> None:
        if kind == "found":
            print(f"Found it - asking {d['receiver'].name} to accept ...")
        elif kind == "accepted":
            print("Accepted - sending ...")
        elif kind == "progress" and time.monotonic() - last[0] > 0.5:
            last[0] = time.monotonic()
            print(f"  {_size(d['sent'])} of {_size(d['total'])}", end="\r")
        elif kind == "done":
            print(f"Sent {_size(d['bytes'])} to {d['receiver'].name}.        ")

    s = client.Sender(lk, ident, args.name)
    try:
        s.send(args.target, args.files, find_timeout=args.seconds,
               icon=_icon(args.files) or None, on_event=event)
    except client.Declined as e:
        print(e)
        return 1
    except client.SendError as e:
        print(f"Not sent: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_receive(args, lk, ident) -> int:
    inbox = os.path.abspath(os.path.expanduser(args.inbox))
    asking = threading.Lock()

    def on_ask(ask, peer) -> bool:
        what = ", ".join(f"{f.name} ({_size(f.size)})" if f.size is not None else f.name
                         for f in ask.files) or ", ".join(ask.urls)
        if args.accept_from and ask.sender_name == args.accept_from:
            print(f"Accepting from {ask.sender_name}: {what}")
            return True
        if not sys.stdin.isatty():
            print(f"Declined {ask.sender_name}: {what} (no one here to ask)")
            return False
        with asking:
            reply = input(f"{ask.sender_name} wants to send {what}. Accept? [y/N] ")
        return reply.strip().lower() in ("y", "yes")

    def on_received(ask, got) -> None:
        if got.paths:
            for p in got.paths:
                print(f"  saved {p}")
        for u in ask.urls:
            print(f"  link: {u}")

    srv = server.Server(lk, ident, args.name, inbox, on_ask, on_received)
    srv.start()
    print(f"Receiving as {args.name!r} into {inbox} - Ctrl+C to stop.")
    try:
        if args.seconds:
            time.sleep(args.seconds)
        else:
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m airdrop", description="AirDrop from a terminal.")
    p.add_argument("--interface", default="awdl0", help="the AWDL interface (default awdl0)")
    p.add_argument("--name", default=_name(), help="the name others see (default: this machine's)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("find", help="list receivers nearby")
    f.add_argument("--seconds", type=float, default=15)

    s = sub.add_parser("send", help="send files to one receiver")
    s.add_argument("target", help="the receiver's name or ID, as `find` shows it")
    s.add_argument("files", nargs="+")
    s.add_argument("--seconds", type=float, default=60, help="how long to look for it")
    s.add_argument("--no-wake", action="store_true", help="don't open Finder's AirDrop window")

    r = sub.add_parser("receive", help="be a receiver")
    r.add_argument("--inbox", default="~/Downloads")
    r.add_argument("--seconds", type=float, default=0, help="stop after this long (default: never)")
    r.add_argument("--accept-from", metavar="NAME",
                   help="take from this sender without asking (testing with your own phone)")

    args = p.parse_args(argv)
    # Line by line even into a file or a pipe, so a receiver's log is current.
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s: %(message)s")
    try:
        lk = link.awdl(args.interface)
    except link.LinkDown as e:
        print(f"No AirDrop link: {e}", file=sys.stderr)
        return 2
    ident = identity.load()
    return {"find": cmd_find, "send": cmd_send, "receive": cmd_receive}[args.command](args, lk, ident)


if __name__ == "__main__":
    sys.exit(main())
