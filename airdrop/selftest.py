"""
python -m airdrop.selftest - the AirDrop core, checked without a network or a phone.

- Archives round-trip, through both kinds of dvzip block (a stored block is
  what a photo makes, and is the path the M0 sends never took).
- Blocks split the way an iPhone splits them decode the same.
- Hostile archives are refused, leave nothing in the inbox, and write nothing
  outside it: climbing paths, absolute paths, links, names nobody accepted,
  more data than offered, a block that inflates past the limit, a truncated
  archive, junk after the end, the same name twice.
- An Ask carries every field an iOS 27 iPhone's does, bar the two only Apple's
  keys can make.
- A whole transfer - TLS, Discover, Ask, chunked dvzip Upload, unpacking - runs
  over the loopback interface, accepted and declined, and an Upload with no
  Ask behind it is turned away.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import sys
import tempfile
import traceback
import zlib

from . import archive, client, identity, link, messages, server
from .archive import UnsafeArchive

TESTS = []


def check(label: str):
    def register(fn):
        TESTS.append((label, fn))
        return fn
    return register


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="airdrop-selftest-")


def _files(root: str) -> dict:
    """{relative path: sha256} for everything under root, hidden ones included."""
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def _make(root: str, name: str, data: bytes) -> str:
    p = os.path.join(root, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _blocks(wire: bytes):
    pos, kinds = 0, []
    while pos < len(wire):
        n = int.from_bytes(wire[pos:pos + 4], "big")
        kinds.append("stored" if n & archive.STORED else "zlib")
        pos += 4 + (n & ~archive.STORED)
    return kinds


def _cpio(entries) -> bytes:
    """A hand-made archive: (name, mode, data) per entry, any name at all."""
    out = archive._header(".", archive.S_IFDIR | 0o755, 0, 0, 0, 2, 501)
    for ino, (name, mode, data) in enumerate(entries, 1):
        out += archive._header(name, mode, len(data), 0, ino, 1, 501) + data
    out += archive._header(archive.TRAILER, 0, 0, 0, len(entries) + 1, 1, 0)
    return out + b"\0" * (-len(out) % 512)


# --------------------------------------------------------------------------- #

@check("round trip: text, a stored block, a compressible megabyte, an awkward name")
def _round_trip():
    src, inbox = _tmp(), _tmp()
    try:
        paths = [_make(src, "note.txt", b"hello from the selftest\n"),
                 _make(src, "noise.bin", os.urandom(300 * 1024)),
                 _make(src, "zeros.raw", bytes(1024 * 1024)),
                 _make(src, "What now.jpg", os.urandom(1000))]
        files = [archive.Outgoing.of(p) for p in paths]
        files[3] = archive.Outgoing(paths[3], "What? now.jpg")   # "?" can't exist on Windows
        wire = b"".join(archive.dvzip(archive.cpio_stream(files)))
        kinds = _blocks(wire)
        assert "stored" in kinds and "zlib" in kinds, kinds
        # fed back in 777-byte pieces, as a socket might deliver it
        got = archive.unpack(archive.undvzip(wire[i:i + 777] for i in range(0, len(wire), 777)),
                             inbox, [f"./{f.name}" for f in files], 10 ** 9)
        assert got.files == 4, got
        have = _files(inbox)
        assert have["What_ now.jpg"] == hashlib.sha256(open(paths[3], "rb").read()).hexdigest()
        for p in paths[:3]:
            assert have[os.path.basename(p)] == hashlib.sha256(open(p, "rb").read()).hexdigest()
        assert not [n for n in os.listdir(inbox) if n.startswith(".incoming")]
    finally:
        shutil.rmtree(src), shutil.rmtree(inbox)


@check("blocks split as an iPhone splits them (each header alone) decode the same")
def _iphone_split():
    src, inbox = _tmp(), _tmp()
    try:
        p = _make(src, "IMG_0001.jpg", os.urandom(400 * 1024))
        pieces = list(archive.cpio_stream([archive.Outgoing.of(p)]))
        # The iPhone puts each header in a block of its own, then the data.
        wire = (archive._block(pieces[0]) + archive._block(pieces[1])
                + b"".join(archive.dvzip(pieces[2:])))
        archive.unpack(archive.undvzip([wire]), inbox, ["./IMG_0001.jpg"], 10 ** 9)
        assert _files(inbox)["IMG_0001.jpg"] == hashlib.sha256(open(p, "rb").read()).hexdigest()
    finally:
        shutil.rmtree(src), shutil.rmtree(inbox)


@check("legacy application/x-cpio (gzip) still unpacks")
def _legacy_gzip():
    inbox = _tmp()
    try:
        raw = _cpio([("./old.txt", archive.S_IFREG | 0o644, b"from an older Mac")])
        archive.unpack(archive.ungzip([gzip.compress(raw)]), inbox, ["./old.txt"], 1000)
        assert open(os.path.join(inbox, "old.txt"), "rb").read() == b"from an older Mac"
    finally:
        shutil.rmtree(inbox)


@check("a second copy gets a new name; nothing is ever overwritten")
def _no_overwrite():
    inbox = _tmp()
    try:
        _make(inbox, "photo.jpg", b"already here")
        raw = _cpio([("./photo.jpg", archive.S_IFREG | 0o644, b"new one")])
        got = archive.unpack(archive.undvzip(archive.dvzip([raw])), inbox, ["./photo.jpg"], 100)
        assert os.path.basename(got.paths[0]) == "photo (2).jpg", got.paths
        assert open(os.path.join(inbox, "photo.jpg"), "rb").read() == b"already here"
    finally:
        shutil.rmtree(inbox)


@check("a ._ metadata file beside an accepted one is read, checked and dropped")
def _sidecar():
    inbox = _tmp()
    try:
        raw = _cpio([("./._IMG_0018.PNG", archive.S_IFREG | 0o644, archive.APPLEDOUBLE + b"\0" * 300),
                     ("./IMG_0018.PNG", archive.S_IFREG | 0o644, b"png bytes")])
        got = archive.unpack(archive.undvzip(archive.dvzip([raw])), inbox, ["./IMG_0018.PNG"], 9)
        assert os.listdir(inbox) == ["IMG_0018.PNG"] and got.files == 1, os.listdir(inbox)
    finally:
        shutil.rmtree(inbox)


HOSTILE = {
    "a ._ file that isn't Apple metadata": (
        [("./._photo.jpg", archive.S_IFREG | 0o644, b"MZ not metadata"),
         ("./photo.jpg", archive.S_IFREG | 0o644, b"x")], ["./photo.jpg"]),
    "a ._ file beside something nobody accepted": (
        [("./._other.exe", archive.S_IFREG | 0o644, archive.APPLEDOUBLE),
         ("./photo.jpg", archive.S_IFREG | 0o644, b"x")], ["./photo.jpg"]),
    "a path that climbs out": ([("./../evil.txt", archive.S_IFREG | 0o644, b"x")], None),
    "an absolute path": ([("/tmp/evil.txt", archive.S_IFREG | 0o644, b"x")], None),
    "a climb hidden in the middle": ([("./a/../../evil.txt", archive.S_IFREG | 0o644, b"x")], None),
    "a symlink": ([("./photo.jpg", 0o120777, b"/etc/passwd")], None),
    "a name nobody accepted": ([("./photo.jpg", archive.S_IFREG | 0o644, b"x"),
                                ("./also.exe", archive.S_IFREG | 0o644, b"MZ")], ["./photo.jpg"]),
    "more data than offered": ([("./photo.jpg", archive.S_IFREG | 0o644, b"x" * 5000)], ["./photo.jpg"]),
    "the same name twice": ([("./photo.jpg", archive.S_IFREG | 0o644, b"a"),
                             ("./photo.jpg", archive.S_IFREG | 0o644, b"b")], None),
}


def _refuses(raw_wire_chunks, allowed, max_bytes=1000):
    outer = _tmp()
    inbox = os.path.join(outer, "inbox")
    try:
        try:
            archive.unpack(archive.undvzip(raw_wire_chunks), inbox, allowed, max_bytes)
        except UnsafeArchive:
            pass
        else:
            raise AssertionError("was accepted")
        assert os.listdir(inbox) == [], os.listdir(inbox)          # nothing kept, no staging left
        assert sorted(os.listdir(outer)) == ["inbox"], os.listdir(outer)   # nothing beside it
        assert not os.path.exists("/tmp/evil.txt")
    finally:
        shutil.rmtree(outer)


for _label, (_entries, _allowed) in HOSTILE.items():
    check(f"refused: {_label}")(
        lambda e=_entries, a=_allowed: _refuses(archive.dvzip([_cpio(e)]), a))


@check("refused: a block that inflates past the limit")
def _bomb():
    bomb = zlib.compress(bytes(4 * archive.MAX_BLOCK))
    _refuses([len(bomb).to_bytes(4, "big") + bomb], None)


@check("refused: an archive that stops half-way through a file")
def _truncated():
    raw = _cpio([("./photo.jpg", archive.S_IFREG | 0o644, b"x" * 600)])
    _refuses(archive.dvzip([raw[:300]]), None)


@check("refused: junk after the end of the archive")
def _junk():
    raw = _cpio([("./photo.jpg", archive.S_IFREG | 0o644, b"x")]) + b"surprise"
    _refuses(archive.dvzip([raw]), None)


@check("names: Windows-proofed, never refused for an innocent character")
def _names():
    assert archive.safe_name("./What?.jpg") == "What_.jpg"
    assert archive.safe_name("./con.txt") == "_con.txt"
    assert archive.safe_name("./notes.") == "notes"
    assert archive.safe_name("./a\\..\\b") == "a_.._b"       # a backslash is just a character
    assert archive.safe_name(".") == "" and archive.safe_name("./") == ""


@check("Ask: every field an iOS 27 iPhone sends, bar the two only Apple can make")
def _ask_shape():
    body = messages.ask_request("PC", "PC", "a1b2c3d4e5f6", "TID",
                                [messages.FileOffer("IMG_0011.jpg", "public.jpeg", 3770608)])
    keys = set(messages._load(body))
    iphone = {"TransferID", "TransferType", "SenderRecordData", "SenderID", "BundleID",
              "SenderComputerName", "SenderModelName", "Items", "Files", "FileIcon",
              "ConvertMediaFormats", "SenderIdentityAuthTag"}
    assert iphone - keys == {"SenderRecordData", "SenderIdentityAuthTag", "FileIcon"}, iphone - keys
    ask = messages.parse_ask(body)
    assert ask.transfer_id == "TID" and ask.total_size == 3770608
    assert ask.files[0].bom_path == "./IMG_0011.jpg"
    f = messages._load(body)["Files"][0]
    assert f["ShouldConvertMediaFormats"] is False and f["FileSize"] == 3770608


# --------------------------------------------------------------------------- #

@check("a whole transfer over loopback TLS: accepted, declined, and an Upload with no Ask")
def _loopback():
    keys, src, inbox = _tmp(), _tmp(), _tmp()
    lk = link.loopback()
    ident = identity.load(keys)
    asks = []

    def on_ask(ask, peer):
        asks.append(ask)
        return ask.sender_name == "Selftest"

    srv = server.Server(lk, ident, "Loopback", inbox, on_ask, port=0, announce=False)
    srv.start()
    try:
        receiver = client.Receiver("selftest", "Loopback", "::1", srv.port)
        p = _make(src, "photo.jpg", os.urandom(300 * 1024))
        events = []
        sender = client.Sender(lk, ident, "Selftest")
        assert sender.discover(client.Service("selftest", "::1", srv.port, 0)) == "Loopback"
        sender._transfer(receiver, [archive.Outgoing.of(p)], None,
                         lambda kind, d: events.append(kind))
        assert "accepted" in events and events[-1] == "done", events
        assert asks[-1].transfer_id and asks[-1].total_size == 300 * 1024
        assert _files(inbox)["photo.jpg"] == hashlib.sha256(open(p, "rb").read()).hexdigest()

        stranger = client.Sender(lk, ident, "Someone else")
        try:
            stranger._transfer(receiver, [archive.Outgoing.of(p)], None, lambda k, d: None)
        except client.Declined:
            pass
        else:
            raise AssertionError("a stranger's Ask was accepted")
        assert sorted(os.listdir(inbox)) == ["photo.jpg"]

        conn = client._Connection(lk, "::1", srv.port, ident.client_context(), 10)
        status, _ = client._post(conn, "/Upload", b"\0" * 100,
                                 {"Content-Type": messages.DVZIP, "TransferID": "made-up"})
        conn.close()
        assert status == 403, status
    finally:
        srv.stop()
        for d in (keys, src, inbox):
            shutil.rmtree(d)


def main() -> int:
    failed = []
    for label, fn in TESTS:
        try:
            fn()
            print(f"  ok    {label}")
        except Exception:
            failed.append(label)
            print(f"  FAIL  {label}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - len(failed)} of {len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
