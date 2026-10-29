"""
net.py
======
The TLS context every HTTPS fetch on the board goes through: Windows' trusted
roots, and nothing else.

Python's default context on Windows also trusts the intermediate store, which
collects intermediate certificates over the years and never drops the expired
ones. OpenSSL looks at what it trusts before the chain the server sends, so a
stale copy of an intermediate there wins over the valid one in the handshake,
and the fetch fails with "certificate has expired" - while every browser on the
machine is fine, because Windows' own verifier copes and the standard library
cannot use it.

That is what took the weather tile down on this machine. One of Open-Meteo's
two servers moved to Let's Encrypt's newer chain, which runs through ISRG Root
X2, and the intermediate store still held the X2 cross-certificate that expired
in September 2025. Whenever DNS handed out that server, all the card could say
was "URLError".

Leaving the intermediate store out costs nothing that matters: a server is
meant to send its own intermediates, and Python on Linux and macOS has never
had that store to lean on.
"""

from __future__ import annotations

import ssl
import threading
from typing import Optional

_lock = threading.Lock()
_context: Optional[ssl.SSLContext] = None


def _from_root_store() -> ssl.SSLContext:
    # The filter the standard library applies to the store: certificates
    # Windows trusts for everything, or for server authentication.
    purpose = ssl.Purpose.SERVER_AUTH.oid
    roots = b"".join(
        cert for cert, encoding, trust in ssl.enum_certificates("ROOT")
        if encoding == "x509_asn" and (trust is True or purpose in trust))
    if not roots:
        raise OSError("no trusted roots in the Windows store")
    # cadata replaces the stores create_default_context would load; every
    # other default - hostname checks, strict verification - stays.
    return ssl.create_default_context(cadata=roots)


def tls_context() -> ssl.SSLContext:
    """The context to pass as ``urlopen(..., context=...)``.

    Built once and shared, as urllib's own default is, rather than re-reading
    the store for every symbol the stocks tile asks about.
    """
    global _context
    with _lock:
        if _context is None:
            try:
                _context = _from_root_store()
            except Exception:
                # Not Windows, the store could not be read, or a root in it
                # would not parse. The stock context is no worse than before,
                # and the next call tries the store again.
                return ssl.create_default_context()
        return _context
