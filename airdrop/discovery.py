"""
discovery.py
============
Finding AirDrop receivers, and being one: Bonjour (multicast DNS) for
``_airdrop._tcp`` on the AWDL link, through python-zeroconf.

An iPhone's receiver is ``<12 hex digits>._airdrop._tcp.local.`` on port 8770,
with a TXT record ``flags``, and it is only there while its AirDrop is awake
(see link.py). Two things M0 learned the hard way:

- zeroconf reports a record that changes as an *update*, not an addition, and
  an iPhone waking up arrives as exactly that. OpenDrop's browser had no
  handler for updates; the exception killed zeroconf's dispatcher thread and
  nothing after it was ever seen. Here one handler takes every kind of change.
- An announced host must not be this machine's own name ("Mac.local."), which
  the system's responder already answers for. A random one per run, as Apple's
  devices use, can't clash with anything.

zeroconf binds to an address, and on a Mac AWDL's moves now and then, so both
classes watch the link and start over on the new one.
"""

from __future__ import annotations

import logging
import sys
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

from .link import Link, LinkDown

TYPE = "_airdrop._tcp.local."
WATCH = 5.0         # seconds between looks at the link's address
RESOLVE_MS = 3000

log = logging.getLogger("airdrop.discovery")


@dataclass(frozen=True)
class Service:
    """An announced receiver, before anyone has asked its name."""
    ident: str
    address: str        # IPv6 link-local, no scope
    port: int
    flags: int


class _OnLink:
    """zeroconf on the link's current address, restarted when that moves."""

    def __init__(self, link: Link):
        self.link = link
        self._zc: Optional[Zeroconf] = None
        self._addr = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        self._restart()
        threading.Thread(target=self._watch, daemon=True,
                         name=f"{type(self).__name__}-watch").start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._close()

    def _watch(self) -> None:
        while not self._stop.wait(WATCH):
            try:
                addr = self.link.address()
            except LinkDown:
                continue
            if addr != self._addr:
                log.info("%s moved to %s; starting over", self.link.name, addr)
                self._restart()

    def _restart(self) -> None:
        with self._lock:
            self._close()
            if self._stop.is_set():
                return
            self._addr = self.link.address()
            self._zc = Zeroconf(interfaces=[str(self._addr)], ip_version=IPVersion.V6Only,
                                apple_p2p=sys.platform == "darwin" and not self.link.loopback)
            self._begin(self._zc)

    def _close(self) -> None:
        if self._zc is None:
            return
        try:
            self._end(self._zc)
            self._zc.close()
        except Exception:
            log.debug("closing zeroconf", exc_info=True)
        self._zc = None

    def _begin(self, zc: Zeroconf) -> None:
        raise NotImplementedError

    def _end(self, zc: Zeroconf) -> None:
        raise NotImplementedError


class Browser(_OnLink):
    """Reports every receiver that appears or changes, possibly more than once."""

    def __init__(self, link: Link, on_found: Callable[[Service], None]):
        super().__init__(link)
        self.on_found = on_found
        self._browser: Optional[ServiceBrowser] = None

    def _begin(self, zc: Zeroconf) -> None:
        self._browser = ServiceBrowser(zc, TYPE, handlers=[self._changed])

    def _end(self, zc: Zeroconf) -> None:
        if self._browser is not None:
            self._browser.cancel()

    def _changed(self, zeroconf: Zeroconf, service_type: str, name: str,
                 state_change: ServiceStateChange) -> None:
        if state_change is ServiceStateChange.Removed:
            return
        # Resolving waits on the network: never on zeroconf's own thread.
        threading.Thread(target=self._resolve, args=(zeroconf, name), daemon=True).start()

    def _resolve(self, zc: Zeroconf, name: str) -> None:
        try:
            info = zc.get_service_info(TYPE, name, timeout=RESOLVE_MS)
        except Exception:
            return
        if info is None or not info.port:
            return
        addrs = [a.split("%")[0] for a in info.parsed_addresses(IPVersion.V6Only)
                 if a.lower().startswith("fe80")]
        if not addrs:
            return
        try:
            flags = int((info.properties or {}).get(b"flags") or 0)
        except ValueError:
            flags = 0
        try:
            self.on_found(Service(name.split(".")[0], addrs[0], info.port, flags))
        except Exception:
            log.exception("on_found")


class Announcer(_OnLink):
    """Announces this machine as a receiver for as long as it runs."""

    def __init__(self, link: Link, ident: str, port: int, flags: int):
        super().__init__(link)
        self.ident = ident
        self.port = port
        self.flags = flags
        self.host = f"{uuid.uuid4()}.local."
        self._info: Optional[ServiceInfo] = None

    def _begin(self, zc: Zeroconf) -> None:
        self._info = ServiceInfo(
            TYPE, f"{self.ident}.{TYPE}", port=self.port, server=self.host,
            properties={b"flags": str(self.flags).encode("ascii")},
            addresses=[self._addr.packed])
        zc.register_service(self._info)

    def _end(self, zc: Zeroconf) -> None:
        if self._info is not None:
            zc.unregister_service(self._info)
