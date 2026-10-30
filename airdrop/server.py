"""
server.py
=========
Receiving: answer Discover, put each Ask in front of the person, and unpack an
accepted Upload - safely - into the inbox.

The server listens on every interface, because on a Mac the AWDL address it
would bind to keeps changing, and drops any connection that didn't come in over
the AWDL link before TLS starts, so the Ethernet network never sees it.

Nothing is accepted on its own. ``on_ask`` is called for every Ask and blocks
until it decides; the sender waits on its Ask meanwhile, which is the protocol
- an iPhone shows "Waiting…" for as long as that takes. An Upload is only taken
if it follows an accepted Ask from the same address, carries that Ask's
TransferID, and holds nothing beyond the files that were accepted, no larger
than they were said to be (``archive.unpack``).
"""

from __future__ import annotations

import http.server
import logging
import shutil
import socket
import socketserver
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, Optional

from . import archive, messages
from .discovery import Announcer
from .identity import Identity
from .link import Link

PORT = 8770
SMALL_BODY = 8 * 1024 * 1024        # Discover and Ask; an Ask's icon is ~120 KB
DEFAULT_MAX = 16 * 1024 ** 3        # when an older sender leaves sizes out
SPARE_DISK = 512 * 1024 * 1024      # never fill the disk to the last byte
IDLE = 30.0                         # seconds a kept-alive connection may sit
PENDING_FOR = 600.0                 # an accepted Ask waits this long for its Upload

log = logging.getLogger("airdrop.server")

AskHandler = Callable[[messages.Ask, str], bool]
ReceivedHandler = Callable[[messages.Ask, archive.Received], None]


class _Refused(Exception):
    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status = status


@dataclass
class _Pending:
    ask: messages.Ask
    peer: str
    at: float


class _HTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int, handler, link: Link):
        self.link = link
        super().__init__(("::", port), handler, bind_and_activate=False)
        try:
            link.prepare(self.socket)
            self.server_bind()
            self.server_activate()
        except BaseException:
            self.server_close()
            raise

    def server_bind(self) -> None:
        # HTTPServer's own looks up a fully qualified name for "::", which can
        # stall on a machine with slow DNS. Nothing here uses it.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "airdrop", self.server_address[1]

    def verify_request(self, request, client_address) -> bool:
        if self.link.owns(client_address):
            return True
        log.info("dropped a connection from %s: not on %s", client_address[0], self.link.name)
        return False

    def handle_error(self, request, client_address) -> None:
        log.debug("connection from %s failed", client_address[0], exc_info=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AirDrop/1.0"
    sys_version = ""
    timeout = IDLE
    receiver: "Server"

    def setup(self) -> None:
        # TLS per connection, on this connection's own thread: a slow
        # handshake mustn't hold up everyone else's accept().
        self.request.settimeout(self.timeout)
        self.request = self.receiver.context.wrap_socket(self.request, server_side=True)
        super().setup()

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s %s", self.client_address[0], fmt % args)

    # ----------------------------------------------------------------- #

    def _body(self, cap: int) -> Iterator[bytes]:
        if self.headers.get("Expect", "").lower() == "100-continue":
            self.send_response_only(100)
            self.end_headers()
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            seen = 0
            while True:
                line = self.rfile.readline(1024)
                try:
                    size = int(line.split(b";")[0].strip(), 16)
                except ValueError:
                    raise _Refused(400, "a broken chunk") from None
                if size == 0:
                    while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                        pass        # trailers
                    return
                seen += size
                if seen > cap:
                    raise _Refused(413, "more than was offered")
                yield from self._exactly(size)
                self.rfile.readline(4)
        else:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise _Refused(400, "a broken Content-Length") from None
            if length > cap:
                raise _Refused(413, "more than was offered")
            yield from self._exactly(length)

    def _exactly(self, n: int) -> Iterator[bytes]:
        while n:
            piece = self.rfile.read(min(n, 64 * 1024))
            if not piece:
                raise ConnectionError("the sender went away mid-request")
            n -= len(piece)
            yield piece

    def _reply(self, status: int, body: bytes = b"", close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:
        peer = self.client_address[0]
        try:
            if self.path == "/Discover":
                self._discover()
            elif self.path == "/Ask":
                self._ask(peer)
            elif self.path == "/Upload":
                self._upload(peer)
            else:
                self._reply(404, close=True)
        except _Refused as e:
            log.info("%s %s: refused (%s)", peer, self.path, e)
            self._reply(e.status, close=True)
        except (archive.UnsafeArchive, ValueError) as e:
            log.warning("%s %s: refused (%s)", peer, self.path, e)
            self._reply(400, close=True)

    def do_GET(self) -> None:
        self._reply(404, close=True)

    # ----------------------------------------------------------------- #

    def _discover(self) -> None:
        b"".join(self._body(SMALL_BODY))
        r = self.receiver
        self._reply(200, messages.discover_response(r.name, r.model))

    def _ask(self, peer: str) -> None:
        r = self.receiver
        ask = messages.parse_ask(b"".join(self._body(SMALL_BODY)))
        log.info("Ask from %r (%s): %s", ask.sender_name, peer,
                 [f.name for f in ask.files] or ask.urls)
        offered = ask.total_size
        if offered is not None and offered > r.free_space():
            log.warning("declined: %d bytes won't fit in %s", offered, r.inbox)
            raise _Refused(507, "not enough space")
        try:
            accepted = bool(r.on_ask(ask, peer))
        except Exception:
            log.exception("on_ask")
            accepted = False
        if not accepted:
            raise _Refused(403, "declined")
        if ask.files:
            r._remember(ask, peer)
        self._reply(200, messages.ask_response(r.name, r.model))
        if not ask.files and ask.urls:
            r._delivered(ask, archive.Received())   # links come in the Ask itself

    def _upload(self, peer: str) -> None:
        r = self.receiver
        pending = r._take(self.headers.get("TransferID"), peer)
        if pending is None:
            raise _Refused(403, "no accepted Ask for this upload")
        kind = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if kind == messages.DVZIP:
            decode = archive.undvzip
        elif kind == messages.CPIO:
            decode = archive.ungzip
        else:
            raise _Refused(415, f"unknown upload type {kind!r}")
        offered = pending.ask.total_size
        cap = offered if offered is not None else min(DEFAULT_MAX, r.free_space())
        # Stored blocks cost 4 bytes per 128 KiB; compression can only shrink.
        wire = cap + cap // 1024 + 1024 * 1024
        got = archive.unpack(decode(self._body(wire)), r.inbox,
                             [f.bom_path for f in pending.ask.files], cap)
        log.info("received %d file(s), %d bytes, from %r", got.files, got.bytes,
                 pending.ask.sender_name)
        self._reply(200, close=True)
        r._delivered(pending.ask, got)


class Server:
    """An AirDrop receiver: announced, listening, and asking before it takes anything."""

    def __init__(self, link: Link, identity: Identity, name: str, inbox: str,
                 on_ask: AskHandler, on_received: Optional[ReceivedHandler] = None, *,
                 model: str = "PC", port: int = PORT, announce: bool = True):
        self.link = link
        self.name = name
        self.model = model
        self.inbox = inbox
        self.on_ask = on_ask
        self.on_received = on_received
        self.context = identity.server_context()
        self.ident = uuid.uuid4().hex[:12]
        self._pending: Dict[str, _Pending] = {}
        self._lock = threading.Lock()
        handler = type("Handler", (_Handler,), {"receiver": self})
        last: Optional[OSError] = None
        for p in range(port, port + 5):     # 8770 may be the Mac's own AirDrop
            try:
                self._http = _HTTPServer(p, handler, link)
                break
            except OSError as e:
                last = e
        else:
            raise last
        self.port = self._http.server_address[1]
        self._announcer = (Announcer(link, self.ident, self.port, messages.RECEIVER_FLAGS)
                           if announce else None)

    def start(self) -> None:
        threading.Thread(target=self._http.serve_forever, daemon=True, name="airdrop-server").start()
        if self._announcer:
            self._announcer.start()
        log.info("receiving as %r on port %d (%s)", self.name, self.port, self.link.name)

    def stop(self) -> None:
        if self._announcer:
            self._announcer.stop()
        self._http.shutdown()
        self._http.server_close()

    def free_space(self) -> int:
        try:
            return max(0, shutil.disk_usage(self.inbox).free - SPARE_DISK)
        except OSError:
            return 0

    # ----------------------------------------------------------------- #

    def _remember(self, ask: messages.Ask, peer: str) -> None:
        key = ask.transfer_id or f"peer:{peer}"     # older senders have no TransferID
        with self._lock:
            now = time.monotonic()
            for k in [k for k, p in self._pending.items() if now - p.at > PENDING_FOR]:
                del self._pending[k]
            self._pending[key] = _Pending(ask, peer, now)

    def _take(self, transfer_id: Optional[str], peer: str) -> Optional[_Pending]:
        key = transfer_id or f"peer:{peer}"
        with self._lock:
            p = self._pending.get(key)
            if p is None or p.peer != peer or time.monotonic() - p.at > PENDING_FOR:
                return None
            return self._pending.pop(key)       # one Ask, one Upload

    def _delivered(self, ask: messages.Ask, got: archive.Received) -> None:
        if self.on_received:
            try:
                self.on_received(ask, got)
            except Exception:
                log.exception("on_received")
