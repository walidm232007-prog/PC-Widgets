"""
client.py
=========
Sending: find the receiver, then Discover, Ask and Upload, the way an iPhone on
iOS 27 does it.

Two timings from M0 shape this:

- An iPhone closes its AirDrop port a few seconds after the last request, so a
  "find" followed later by a "send" meets a closed port. The send runs from
  inside discovery, the moment the receiver answers.
- Ask blocks until the person taps Accept or Decline - one took 72 s - so it
  gets a long timeout, and Upload follows on the same connection, as the
  iPhone's does. If the receiver closed it after answering, the Upload goes
  again on a fresh one.

Only a receiver matching the target by name or ID is ever sent anything: in a
café, half the Macs in the room answer Discover.
"""

from __future__ import annotations

import http.client
import logging
import os
import queue
import ssl
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, List, Optional, Sequence

from . import archive, messages
from .discovery import Browser, Service
from .identity import Identity
from .link import Link

ASK_TIMEOUT = 180.0     # how long a person may take to tap Accept
IO_TIMEOUT = 30.0
DISCOVER_TIMEOUT = 8.0

log = logging.getLogger("airdrop.client")

# Events, for a UI: ("found" | "skipped" | "asking" | "accepted" | "progress"
# | "done", details). Called from worker threads.
Events = Callable[[str, dict], None]


class SendError(Exception):
    pass


class Declined(SendError):
    """The person tapped Decline, or the receiver refused the Ask."""


class NotFound(SendError):
    """No matching receiver answered in time."""


@dataclass(frozen=True)
class Receiver:
    ident: str
    name: str
    address: str
    port: int


class _Connection(http.client.HTTPSConnection):
    """HTTPS to a link-local address, with the link's scope and socket options."""

    def __init__(self, link: Link, address: str, port: int,
                 context: ssl.SSLContext, timeout: float):
        super().__init__(address, port, timeout=timeout, context=context)
        self._link = link
        self._address = address

    def connect(self) -> None:
        raw = self._link.connect(self._address, self.port, self.timeout)
        self.sock = self._context.wrap_socket(raw)

    def retime(self, timeout: float) -> None:
        self.timeout = timeout
        if self.sock is not None:
            self.sock.settimeout(timeout)


def _post(conn: _Connection, path: str, body, headers: Optional[dict] = None):
    hdrs = {"User-Agent": messages.USER_AGENT, "Accept": "*/*",
            "Content-Type": "application/octet-stream", "Connection": "keep-alive"}
    hdrs.update(headers or {})
    # An iterable body goes out chunked, as an iPhone's Upload does.
    conn.request("POST", path, body=body, headers=hdrs)
    resp = conn.getresponse()
    return resp.status, resp.read()


class Sender:
    def __init__(self, link: Link, identity: Identity, name: str, model: str = "PC"):
        self.link = link
        self.name = name
        self.model = model
        self.ident = uuid.uuid4().hex[:12]      # new each run, as Apple's devices do
        self._ctx = identity.client_context()

    # ----------------------------------------------------------------- #

    def discover(self, service: Service) -> Optional[str]:
        """The receiver's name, or None if it won't say."""
        conn = _Connection(self.link, service.address, service.port, self._ctx, DISCOVER_TIMEOUT)
        try:
            status, body = _post(conn, "/Discover", messages.discover_request(),
                                 {"Connection": "close"})
        finally:
            conn.close()
        return messages.receiver_name(body) if status == 200 else None

    def find(self, seconds: float, on_receiver: Callable[[Receiver], None]) -> List[Receiver]:
        """Every receiver that answers Discover within ``seconds``."""
        found: List[Receiver] = []
        asked = set()
        lock = threading.Lock()

        def seen(service: Service) -> None:
            key = (service.ident, service.address)
            with lock:
                if key in asked:
                    return
                asked.add(key)
            try:
                name = self.discover(service)
            except (OSError, http.client.HTTPException, ValueError):
                with lock:
                    asked.discard(key)      # asleep, perhaps: let a later update retry
                return
            if name:
                r = Receiver(service.ident, name, service.address, service.port)
                with lock:
                    found.append(r)
                on_receiver(r)

        browser = Browser(self.link, seen)
        browser.start()
        try:
            threading.Event().wait(seconds)
        finally:
            browser.stop()
        return found

    def send(self, target: str, paths: Sequence[str], *, find_timeout: float = 60.0,
             icon: Optional[bytes] = None, on_event: Optional[Events] = None) -> Receiver:
        """Send ``paths`` to the receiver named (or with the ID) ``target``."""
        files = [archive.Outgoing.of(p) for p in paths]
        for f in files:
            if not os.path.isfile(f.path):
                raise SendError(f"not a file: {f.path}")
        emit = on_event or (lambda kind, details: None)
        outcome: "queue.Queue" = queue.Queue()
        claimed = threading.Lock()
        busy = set()
        busy_lock = threading.Lock()

        def seen(service: Service) -> None:
            key = (service.ident, service.address)
            with busy_lock:
                if key in busy or claimed.locked():
                    return
                busy.add(key)
            try:
                try:
                    name = self.discover(service)
                except (OSError, http.client.HTTPException, ValueError) as e:
                    log.debug("%s: no answer to Discover (%s)", service.ident, type(e).__name__)
                    return
                if not name or target not in (name, service.ident):
                    emit("skipped", {"name": name, "ident": service.ident})
                    return
                if not claimed.acquire(blocking=False):
                    return
                receiver = Receiver(service.ident, name, service.address, service.port)
                emit("found", {"receiver": receiver})
                try:
                    self._transfer(receiver, files, icon, emit)
                    outcome.put(receiver)
                except BaseException as e:
                    outcome.put(e)
            finally:
                with busy_lock:
                    busy.discard(key)

        browser = Browser(self.link, seen)
        browser.start()
        try:
            try:
                result = outcome.get(timeout=find_timeout)
            except queue.Empty:
                if not claimed.locked():
                    raise NotFound(f"{target!r} didn't answer within {find_timeout:.0f} s") from None
                result = outcome.get()      # found in time; the transfer has its own timeouts
        finally:
            browser.stop()
        if isinstance(result, BaseException):
            raise result
        return result

    # ----------------------------------------------------------------- #

    def _transfer(self, receiver: Receiver, files: Sequence[archive.Outgoing],
                  icon: Optional[bytes], emit: Events) -> None:
        transfer_id = str(uuid.uuid4()).upper()
        offers = [messages.FileOffer(f.name, messages.uti_for(f.name), os.path.getsize(f.path))
                  for f in files]
        total = sum(o.size for o in offers)
        conn = _Connection(self.link, receiver.address, receiver.port, self._ctx, ASK_TIMEOUT)
        try:
            emit("asking", {"receiver": receiver, "transfer": transfer_id})
            status, _ = _post(conn, "/Ask", messages.ask_request(
                self.name, self.model, self.ident, transfer_id, offers, icon))
            if status != 200:
                raise Declined(f"{receiver.name} declined ({status})")
            emit("accepted", {"receiver": receiver})
            conn.retime(IO_TIMEOUT)
            headers = messages.upload_headers(transfer_id, total)
            for attempt in (1, 2):
                body = archive.dvzip(_counted(archive.cpio_stream(files), total, emit))
                try:
                    status, _ = _post(conn, "/Upload", body, headers)
                    break
                except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                    if attempt == 2:
                        raise SendError(f"{receiver.name} hung up on the upload") from None
                    conn.close()
                    conn = _Connection(self.link, receiver.address, receiver.port,
                                       self._ctx, IO_TIMEOUT)
            if status != 200:
                raise SendError(f"{receiver.name} refused the upload ({status})")
            emit("done", {"receiver": receiver, "bytes": total})
        finally:
            conn.close()


def _counted(stream: Iterable[bytes], total: int, emit: Events) -> Iterator[bytes]:
    sent = 0
    for piece in stream:
        sent = min(total, sent + len(piece))
        emit("progress", {"sent": sent, "total": total})
        yield piece
