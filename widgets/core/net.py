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

import hashlib
import os
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional

_lock = threading.Lock()
_context: Optional[ssl.SSLContext] = None

# The most any fetch reads. A forecast is a few KB, a day of quotes tens of KB,
# a busy calendar well under a megabyte. Past this the reply is wrong or
# hostile, and reading on would only fill memory.
MAX_BYTES = 8 * 1024 * 1024


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


class _StayOnHttps(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https:// address. urllib on its own
    follows one to plain http:// too, which would send a private link - a
    published calendar's address is as good as a password - and take the
    answer back, in the clear."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.URLError(f"refused a {code} redirect off HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_capped(response, limit: int = MAX_BYTES) -> bytes:
    """At most ``limit`` bytes of a reply, or an error if there is more."""
    data = response.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"the reply ran past {limit // (1024 * 1024)} MB")
    return data


def fetch(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 10,
          limit: int = MAX_BYTES) -> bytes:
    """GET an https:// address and return its body.

    Every fetch on the board comes through here: HTTPS only (urlopen on its own
    also opens http://, file://, ftp:// and data: addresses), Windows' trusted
    roots with the usual hostname checks, redirects only to HTTPS, and at most
    ``limit`` bytes read.
    """
    with _open(url, headers, timeout) as r:
        return read_capped(r, limit)


def _open(url: str, headers: Optional[Dict[str, str]], timeout: float):
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise ValueError("only https:// addresses are fetched")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=tls_context()), _StayOnHttps())
    return opener.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout)


def download(url: str, dest: str, limit: int, size: Optional[int] = None,
             progress: Optional[Callable[[int, Optional[int]], None]] = None,
             stop: Optional[threading.Event] = None,
             headers: Optional[Dict[str, str]] = None, timeout: float = 30) -> str:
    """Stream an https:// address to the file ``dest`` and return its SHA-256.

    For what is too big to hold in memory - an update's installer - with the
    same rules as ``fetch``: HTTPS only, the trusted roots, redirects only to
    HTTPS, and never more than ``limit`` bytes (nor more than ``size``, when
    the size is known). See ``save_stream`` for how the file is written.
    """
    with _open(url, headers, timeout) as r:
        return save_stream(r, dest, limit, size, progress, stop)


def save_stream(response, dest: str, limit: int, size: Optional[int] = None,
                progress: Optional[Callable[[int, Optional[int]], None]] = None,
                stop: Optional[threading.Event] = None, chunk: int = 256 * 1024) -> str:
    """Write a reply to ``dest`` a piece at a time, hashing as it goes, and
    return the SHA-256.

    The bytes land in ``dest + ".part"`` and only become ``dest`` once they are
    all there - the length promised, nothing past the cap - so a cut-off
    download never looks like a finished one. Any failure, ``stop`` included,
    removes the part file and raises.
    """
    part = dest + ".part"
    digest = hashlib.sha256()
    got = 0
    try:
        with open(part, "wb") as fh:
            while True:
                if stop is not None and stop.is_set():
                    raise OSError("the download was stopped")
                block = response.read(chunk)
                if not block:
                    break
                got += len(block)
                if got > limit or (size is not None and got > size):
                    raise OSError("the download ran past its size")
                digest.update(block)
                fh.write(block)
                if progress is not None:
                    progress(got, size)
        if size is not None and got != size:
            raise OSError(f"the download stopped at {got} of {size} bytes")
        os.replace(part, dest)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return digest.hexdigest()
