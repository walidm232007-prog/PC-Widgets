"""
archive.py
==========
What an AirDrop upload carries, in both directions: an odc cpio archive, and
the "dvzip" blocks iOS wraps it in.

**cpio.** The archive is the old portable format, odc (magic ``070707``):
every header a fixed 76 bytes of octal text, then the name and its NUL, then
the data, with no alignment anywhere. An iPhone on iOS 27 sends ``.`` first,
then ``./<name>`` for each file, then ``TRAILER!!!``, and pads the lot to a
multiple of 512 bytes. :func:`cpio_stream` writes exactly that - byte for byte
what the iPhone wrote for the same photo, timestamps aside.

**dvzip.** An upload from iOS 27 is ``application/x-dvzip``, not the gzipped
cpio OpenDrop sends, which iOS 27 reads to the end and then hangs up on. It is
the same cpio stream cut into blocks, each a 4-byte big-endian length then a
zlib stream - or, with the length's top bit set, the block as it is. A JPEG
doesn't compress, and the iPhone doesn't pretend it does. Blocks hold up to
128 KiB of archive.

**Unpacking is where the danger is.** In Everyone mode anybody nearby can send,
so the receiving side takes nothing on trust:

- names are checked before anything touches the disk - no absolute paths, no
  ``..`` - and characters Windows can't store, or would read as a drive or a
  stream, are replaced;
- only plain files and folders are written: no links, devices or pipes;
- every name has to be one of those the person accepted in the Ask - with one
  exception, Apple's ``._name`` metadata files (AppleDouble), which an iPhone
  sends beside any file that carries extended attributes. One is let through
  only next to a file that was accepted, only if it really is AppleDouble, and
  only small - and is then thrown away, since Windows has no use for it;
- sizes are capped: per block, so a few bytes can't inflate into gigabytes, and
  in total;
- it all lands in a fresh staging folder and moves into the inbox only once the
  archive is complete, never over anything already there, so a transfer that
  fails half-way leaves nothing behind.

OpenDrop extracts straight into the current directory with libarchive, and has
an open report of exactly the path traversal this is built to refuse.
"""

from __future__ import annotations

import itertools
import os
import shutil
import struct
import time
import uuid
import zlib
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Sequence

BLOCK = 128 * 1024          # archive bytes per dvzip block, as an iPhone cuts them
STORED = 0x80000000         # top bit of a block's length: stored, not compressed
MAX_BLOCK = 1024 * 1024     # the most either side of a received block may be

MAGIC = b"070707"
HEADER = 76                 # 6 magic + 8 six-digit fields + 11 + 6 + 11
TRAILER = "TRAILER!!!"
OWNER = 501                 # uid/gid an iPhone writes; nothing reads it back

S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000

MAX_NAME = 4096
MAX_ENTRIES = 10000

APPLEDOUBLE = b"\x00\x05\x16\x07"   # the magic a ._ metadata file starts with
SIDECAR_MAX = 1024 * 1024           # each; they hold attributes, not content
SIDECARS_MAX = 16 * 1024 * 1024     # all of them together


class UnsafeArchive(Exception):
    """The archive asked for something that will not be written. Nothing was kept."""


# --------------------------------------------------------------------------- #
#  Sending
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Outgoing:
    """A file to send: where it is, and the name it goes by."""
    path: str
    name: str

    @classmethod
    def of(cls, path: str) -> "Outgoing":
        return cls(path, os.path.basename(path))


def _header(name: str, mode: int, size: int, mtime: int, ino: int,
            nlink: int, owner: int) -> bytes:
    name_b = name.encode("utf-8") + b"\0"
    return (MAGIC + b"%06o" % 0 + b"%06o" % ino + b"%06o" % mode
            + b"%06o" % owner + b"%06o" % owner + b"%06o" % nlink
            + b"%06o" % 0 + b"%011o" % mtime + b"%06o" % len(name_b)
            + b"%011o" % size + name_b)


def cpio_stream(files: Sequence[Outgoing], mtime: Optional[int] = None) -> Iterator[bytes]:
    """The archive, piece by piece, reading each file as it goes.

    Nothing is held in memory beyond one read, so a video sends as cheaply as
    a photo. A file that changes size mid-send is an error rather than a
    corrupt archive: the header has already promised the receiver a length.
    """
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        raise ValueError("two files with the same name")
    now = int(time.time()) if mtime is None else mtime
    total = 0

    def out(piece: bytes) -> bytes:
        nonlocal total
        total += len(piece)
        return piece

    yield out(_header(".", S_IFDIR | 0o755, 0, now, 0, 2, OWNER))
    for ino, f in enumerate(files, 1):
        size = os.path.getsize(f.path)
        yield out(_header("./" + f.name, S_IFREG | 0o644, size, now, ino, 1, OWNER))
        left = size
        with open(f.path, "rb") as src:
            while left:
                piece = src.read(min(BLOCK, left))
                if not piece:
                    raise OSError(f"{f.name} got shorter while it was being sent")
                left -= len(piece)
                yield out(piece)
    # The trailer takes the next inode and no owner, as the iPhone's does.
    yield out(_header(TRAILER, 0, 0, 0, len(files) + 1, 1, 0))
    yield out(b"\0" * (-total % 512))


def dvzip(stream: Iterable[bytes]) -> Iterator[bytes]:
    """Cut an archive stream into dvzip blocks."""
    buf = bytearray()
    for piece in stream:
        buf += piece
        while len(buf) >= BLOCK:
            yield _block(bytes(buf[:BLOCK]))
            del buf[:BLOCK]
    if buf:
        yield _block(bytes(buf))


def _block(plain: bytes) -> bytes:
    packed = zlib.compress(plain)
    if len(packed) < len(plain):
        return struct.pack(">I", len(packed)) + packed
    return struct.pack(">I", len(plain) | STORED) + plain


# --------------------------------------------------------------------------- #
#  Receiving: the wire format back to an archive stream
# --------------------------------------------------------------------------- #

def undvzip(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """dvzip blocks back to the archive stream, however the chunks fall."""
    buf = bytearray()
    for chunk in chunks:
        buf += chunk
        while len(buf) >= 4:
            n = struct.unpack(">I", buf[:4])[0]
            stored = bool(n & STORED)
            n &= ~STORED
            if n > MAX_BLOCK:
                raise UnsafeArchive("a block bigger than any sender writes")
            if len(buf) < 4 + n:
                break
            body = bytes(buf[4:4 + n])
            del buf[:4 + n]
            if stored:
                yield body
                continue
            d = zlib.decompressobj()
            try:
                plain = d.decompress(body, MAX_BLOCK)
            except zlib.error:
                raise UnsafeArchive("a block that isn't zlib") from None
            if d.unconsumed_tail:
                raise UnsafeArchive("a block that inflates past the limit")
            if not d.eof:
                raise UnsafeArchive("a block that isn't a whole zlib stream")
            yield plain
    if buf:
        raise UnsafeArchive("the upload stopped part-way through a block")


def ungzip(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """``application/x-cpio``: one gzip stream, from older Macs and OpenDrop.

    Inflated in bounded steps; the total is capped by the unpacker.
    """
    d = zlib.decompressobj(wbits=31)
    try:
        for chunk in chunks:
            data = chunk
            while data:
                yield d.decompress(data, MAX_BLOCK)
                data = d.unconsumed_tail
        yield d.flush()
    except zlib.error:
        raise UnsafeArchive("not a gzip stream") from None


# --------------------------------------------------------------------------- #
#  Receiving: the archive stream onto disk, safely
# --------------------------------------------------------------------------- #

# Characters Windows can't store in a name, or reads as a drive (":"), an
# alternate data stream (":"), or a separator ("\\"). Replaced, not refused: a
# photo called "What?.jpg" from an iPhone is not an attack.
_REPLACE = set('\\:*?"<>|')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def safe_name(raw: str) -> str:
    """An archive name as a relative path that is safe to write anywhere.

    ``""`` is the archive's root. Absolute paths, ``..`` and empty parts are
    refused outright; they have no innocent use.
    """
    if "\0" in raw:
        raise UnsafeArchive("a name with a NUL in it")
    name = raw[2:] if raw.startswith("./") else raw
    if name in ("", "."):
        return ""
    if name.startswith("/"):
        raise UnsafeArchive(f"an absolute path: {raw!r}")
    parts = []
    for part in name.split("/"):
        if part in ("", ".", ".."):
            raise UnsafeArchive(f"a path that climbs or is empty: {raw!r}")
        part = "".join("_" if (c in _REPLACE or ord(c) < 32) else c for c in part)
        # Windows drops trailing dots and spaces, so "a." and "a" would be one file.
        part = part.rstrip(" .") or "_"
        if part.split(".")[0].upper() in _RESERVED:
            part = "_" + part
        if len(part.encode("utf-8")) > 255:
            raise UnsafeArchive("a name too long for any filesystem")
        parts.append(part)
    return "/".join(parts)


class _Reader:
    """Exact-length reads from a stream of chunks."""

    def __init__(self, chunks: Iterable[bytes]):
        self._it = iter(chunks)
        self._buf = bytearray()

    def read(self, n: int) -> bytes:
        while len(self._buf) < n:
            try:
                self._buf += next(self._it)
            except StopIteration:
                raise UnsafeArchive("the archive ended part-way through") from None
        piece = bytes(self._buf[:n])
        del self._buf[:n]
        return piece

    def pieces(self, n: int) -> Iterator[bytes]:
        """``n`` bytes, as they come, without gathering them."""
        while n:
            if not self._buf:
                try:
                    self._buf += next(self._it)
                except StopIteration:
                    raise UnsafeArchive("the archive ended part-way through a file") from None
            take = min(n, len(self._buf))
            yield bytes(self._buf[:take])
            del self._buf[:take]
            n -= take

    def drain(self, limit: int) -> None:
        """Whatever follows the trailer: padding, which may only be zeros."""
        seen = 0
        for chunk in itertools.chain([bytes(self._buf)], self._it):
            seen += len(chunk)
            if seen > limit or chunk.strip(b"\0"):
                raise UnsafeArchive("data after the end of the archive")
        self._buf.clear()


@dataclass
class Received:
    """What an upload left in the inbox: its top-level items, by full path."""
    paths: List[str] = field(default_factory=list)
    bytes: int = 0
    files: int = 0


def _field(hdr: bytes, start: int, width: int) -> int:
    try:
        return int(hdr[start:start + width], 8)
    except ValueError:
        raise UnsafeArchive("a header that isn't octal") from None


def unpack(stream: Iterable[bytes], inbox: str, allowed: Optional[Sequence[str]],
           max_bytes: int) -> Received:
    """Write an archive stream into ``inbox``, or nothing at all.

    ``allowed`` is the list of names the person accepted (each file's
    ``FileBomPath``); every entry must be one of them or inside one that is a
    folder. ``None`` allows any name - for tests only. ``max_bytes`` caps the
    file data written.
    """
    allow = None if allowed is None else {safe_name(a) for a in allowed} - {""}
    os.makedirs(inbox, exist_ok=True)
    staging = os.path.join(inbox, f".incoming-{uuid.uuid4().hex[:12]}")
    os.mkdir(staging, 0o700)
    tops: List[str] = []
    got = Received()
    sidecar_bytes = 0
    try:
        reader = _Reader(stream)
        for _ in range(MAX_ENTRIES + 1):
            hdr = reader.read(HEADER)
            if hdr[:6] != MAGIC:
                raise UnsafeArchive("not an odc cpio archive")
            mode = _field(hdr, 18, 6)
            namesize = _field(hdr, 59, 6)
            size = _field(hdr, 65, 11)
            if not 1 <= namesize <= MAX_NAME:
                raise UnsafeArchive("a name of impossible length")
            raw = reader.read(namesize)
            if raw[-1:] != b"\0":
                raise UnsafeArchive("a name without its NUL")
            try:
                name = raw[:-1].decode("utf-8")
            except UnicodeDecodeError:
                raise UnsafeArchive("a name that isn't UTF-8") from None
            if name == TRAILER:
                break
            rel = safe_name(name)
            kind = mode & S_IFMT
            if kind == S_IFREG and _sidecar_of_accepted(rel, allow):
                sidecar_bytes += size
                if size > SIDECAR_MAX or sidecar_bytes > SIDECARS_MAX:
                    raise UnsafeArchive("an oversized ._ metadata file")
                if b"".join(reader.pieces(size))[:4] != APPLEDOUBLE:
                    raise UnsafeArchive(f"{rel!r} isn't Apple metadata")
                continue        # read, checked, and not kept
            if rel and allow is not None and not any(
                    rel == a or rel.startswith(a + "/") for a in allow):
                raise UnsafeArchive(f"{rel!r} wasn't in what was accepted")
            target = os.path.join(staging, *rel.split("/")) if rel else staging
            if kind == S_IFDIR:
                if size:
                    raise UnsafeArchive("a folder with data in it")
                os.makedirs(target, exist_ok=True)
            elif kind == S_IFREG:
                if got.bytes + size > max_bytes:
                    raise UnsafeArchive("more data than was offered")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) \
                    | getattr(os, "O_NOFOLLOW", 0)
                try:
                    fd = os.open(target, flags, 0o644)
                except FileExistsError:
                    raise UnsafeArchive(f"{rel!r} twice") from None
                with os.fdopen(fd, "wb") as dst:
                    for piece in reader.pieces(size):
                        dst.write(piece)
                got.bytes += size
                got.files += 1
            else:
                raise UnsafeArchive("a link, device or pipe - only files and folders are accepted")
            top = rel.split("/")[0] if rel else ""
            if top and top not in tops:
                tops.append(top)
        else:
            raise UnsafeArchive("too many entries")
        reader.drain(limit=1024 * 1024)

        for top in tops:
            got.paths.append(_move_in(os.path.join(staging, top), inbox, top))
        return got
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _sidecar_of_accepted(rel: str, allow: Optional[set]) -> bool:
    """Whether ``rel`` is the ``._`` metadata file of something accepted."""
    head, _, base = rel.rpartition("/")
    if not base.startswith("._") or len(base) <= 2:
        return False
    twin = f"{head}/{base[2:]}" if head else base[2:]
    return allow is None or any(twin == a or twin.startswith(a + "/") for a in allow)


def _move_in(src: str, inbox: str, name: str) -> str:
    """Move into the inbox under ``name``, or "name (2)" and so on. Never over anything."""
    stem, ext = os.path.splitext(name)
    if os.path.isdir(src):
        stem, ext = name, ""
    for n in range(1, 1000):
        dest = os.path.join(inbox, name if n == 1 else f"{stem} ({n}){ext}")
        if os.path.lexists(dest):
            continue
        try:
            if os.path.isdir(src):
                os.rename(src, dest)
            else:
                # A hard link fails rather than replaces if something got
                # there first; rename is the fallback where links aren't.
                try:
                    os.link(src, dest)
                except FileExistsError:
                    raise
                except OSError:
                    os.rename(src, dest)
                else:
                    os.unlink(src)
            return dest
        except FileExistsError:
            continue
    raise OSError(f"no free name for {name} in {inbox}")
