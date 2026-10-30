"""
link.py
=======
The one part of the AirDrop core that differs by machine: which network
interface is Apple Wireless Direct Link, what a socket on it needs, and how
nearby iPhones are woken up.

- **macOS** has AWDL built in, as ``awdl0``. A socket only sees traffic on it
  with ``SO_RECV_ANYIF`` (0x1104) set - not in Python's ``socket`` module, but
  what Apple's own frameworks set for AWDL.
- **Linux** has none of its own. OWL, the open reimplementation, creates
  ``awdl0`` as a TUN interface on a Wi-Fi adapter in monitor mode. On Windows
  that is where this core runs: inside WSL2, with a USB adapter handed to it.

Everything above this file deals in IPv6 link-local addresses plus this
link's interface index - AWDL has no other kind.

**Waking.** An idle iPhone keeps its AirDrop receiver switched off until a
nearby sender's Bluetooth LE advertisement says someone wants to share; until
then it neither answers Bonjour nor listens on its port. On a Mac, Finder's
AirDrop window sends that advertisement while it is open, so :func:`wake_nearby`
opens it. Sending it ourselves - which a Windows PC will have to do - is M2.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

SO_RECV_ANYIF = 0x1104
FINDER_AIRDROP = "/System/Library/CoreServices/Finder.app/Contents/Applications/AirDrop.app"


class LinkDown(Exception):
    """There is no AWDL interface, or it has no address."""


@dataclass(frozen=True)
class Link:
    name: str
    index: int
    loopback: bool = False      # the tests' stand-in for AWDL

    def address(self) -> ipaddress.IPv6Address:
        """This machine's current link-local address on the link.

        On a Mac it changes now and then, so ask each time rather than keeping it.
        """
        if self.loopback:
            return ipaddress.IPv6Address("::1")
        import ifaddr
        for adapter in ifaddr.get_adapters():
            if adapter.name != self.name:
                continue
            for ip in adapter.ips:
                if isinstance(ip.ip, tuple):
                    addr = ipaddress.IPv6Address(ip.ip[0].split("%")[0])
                    if addr.is_link_local:
                        return addr
        raise LinkDown(f"{self.name} has no IPv6 link-local address")

    def prepare(self, sock: socket.socket) -> None:
        """Set what a socket needs to send and receive on this link."""
        if sys.platform == "darwin" and not self.loopback:
            sock.setsockopt(socket.SOL_SOCKET, SO_RECV_ANYIF, 1)

    def owns(self, peer: Tuple) -> bool:
        """Whether a connection came in over this link, from its sockaddr.

        The server listens on every interface (it has to: on a Mac the
        address it would bind to changes), so this is what keeps it from
        answering the Ethernet network.
        """
        if self.loopback:
            return peer[0] in ("::1", "::ffff:127.0.0.1")
        return len(peer) >= 4 and peer[3] == self.index

    def connect(self, address: str, port: int, timeout: float) -> socket.socket:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            self.prepare(sock)
            sock.settimeout(timeout)
            sock.connect((address, port, 0, 0 if self.loopback else self.index))
        except BaseException:
            sock.close()
            raise
        return sock


def awdl(name: str = "awdl0") -> Link:
    try:
        index = socket.if_nametoindex(name)
    except OSError:
        hint = ("macOS should always have it" if sys.platform == "darwin"
                else "is OWL running?")
        raise LinkDown(f"no {name} interface ({hint})") from None
    link = Link(name, index)
    link.address()      # raises LinkDown if it's there but not up
    return link


def loopback() -> Link:
    for name in ("lo0", "lo"):
        try:
            return Link(name, socket.if_nametoindex(name), loopback=True)
        except OSError:
            continue
    return Link("lo", 1, loopback=True)


def wake_nearby() -> bool:
    """Ask nearby iPhones to switch AirDrop on. True if something was done."""
    if sys.platform == "darwin":
        subprocess.run(["open", FINDER_AIRDROP], check=False)
        return True
    return False


def wake_hint() -> Optional[str]:
    if sys.platform == "darwin":
        return None
    return ("nothing here can send the Bluetooth wake-up yet (M2); an iPhone "
            "will only answer while something else nearby is sharing")
