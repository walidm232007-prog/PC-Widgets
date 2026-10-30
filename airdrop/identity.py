"""
identity.py
===========
The TLS certificate this machine presents, and the TLS contexts built on it.

AirDrop runs over HTTPS, and both ends present a certificate. Apple's devices
carry Apple-issued ones; ours is self-signed, made here on first use. In
Everyone mode neither side checks the other's - an iPhone accepted ours
throughout M0 - so the contexts don't verify either: there is no CA we could
check an iPhone's certificate against, and Contacts Only, which would need one,
needs Apple-signed records we can't have anyway.

The key never goes near the repository. It lives in the user's data folder
(``%APPDATA%\\PCWidgets\\airdrop`` on Windows, the XDG data directory on Linux
and in WSL, Application Support on a Mac), written owner-only. It is not a
secret that protects anything - it only gives the TLS session a key - but it
is shaped like one, and ``tools/prepublish.py`` would rightly refuse it.
"""

from __future__ import annotations

import datetime
import os
import ssl
import sys
from dataclasses import dataclass
from typing import Optional

CERT = "certificate.pem"
KEY = "key.pem"
VALID_DAYS = 5 * 365
RENEW_DAYS = 30


def data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PCWidgets", "airdrop")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/PCWidgets/airdrop")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "pcwidgets", "airdrop")


@dataclass(frozen=True)
class Identity:
    cert_file: str
    key_file: str

    def client_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False     # a link-local address has no name to check
        return self._finish(ctx)

    def server_context(self) -> ssl.SSLContext:
        return self._finish(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))

    def _finish(self, ctx: ssl.SSLContext) -> ssl.SSLContext:
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except (AttributeError, ValueError):
            # LibreSSL in macOS's system Python won't set it this way.
            ctx.options |= getattr(ssl, "OP_NO_TLSv1", 0) | getattr(ssl, "OP_NO_TLSv1_1", 0)
        ctx.load_cert_chain(self.cert_file, self.key_file)
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def load(directory: Optional[str] = None) -> Identity:
    """This machine's identity, made the first time and when it nears expiry."""
    directory = directory or data_dir()
    ident = Identity(os.path.join(directory, CERT), os.path.join(directory, KEY))
    if not _usable(ident.cert_file):
        _create(directory, ident)
    return ident


def _usable(cert_file: str) -> bool:
    try:
        from cryptography import x509
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
    except Exception:
        return False
    expires = getattr(cert, "not_valid_after_utc", None) \
        or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    return expires - datetime.datetime.now(datetime.timezone.utc) > datetime.timedelta(days=RENEW_DAYS)


def _create(directory: str, ident: Identity) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    os.makedirs(directory, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # A plain name, not the user's: the certificate is sent to every device
    # this one talks to.
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PC Widgets")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
    _write(ident.key_file, key_pem, 0o600)
    _write(ident.cert_file, cert.public_bytes(serialization.Encoding.PEM), 0o644)


def _write(path: str, data: bytes, mode: int) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
