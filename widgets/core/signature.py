"""
signature.py
============
ECDSA P-256 signatures through Windows' own cryptography (CNG, bcrypt.dll):
how the updater knows an update came from whoever publishes PC Widgets.

The installer isn't code-signed, so nothing else vouches for it. Each
release's update file is signed by the release workflow with a private key
kept in the repository's secrets; the app carries the public half
(updater.PUBLIC_KEY) and installs nothing whose signature doesn't check out.
CNG is the library Windows uses for its own signatures, so this needs no
package of its own - Python's standard library can hash but can't check a
signature.

Keys travel as CNG's own blobs, base64-encoded: a BCRYPT_ECCKEY_BLOB header
(a magic number, then the key size in bytes) followed by X and Y, and for a
private key d. A signature is 64 bytes, r then s (IEEE P1363), over the
SHA-256 of the signed bytes.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import struct
from ctypes import wintypes
from typing import Tuple

ALGORITHM = "ECDSA_P256"
PUBLIC_BLOB, PRIVATE_BLOB = "ECCPUBLICBLOB", "ECCPRIVATEBLOB"
PUBLIC_MAGIC = 0x31534345           # "ECS1": an ECDSA P-256 public key
PRIVATE_MAGIC = 0x32534345          # "ECS2": an ECDSA P-256 private key
KEY_BYTES = 32
SIGNATURE_BYTES = 64

_lib = None


def _bcrypt():
    """bcrypt.dll with the few calls used here typed, loaded on first use."""
    global _lib
    if _lib is None:
        b = ctypes.WinDLL("bcrypt")
        h, ph = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        u, pu, buf = wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p
        for name, args in (
                ("BCryptOpenAlgorithmProvider", [ph, wintypes.LPCWSTR, wintypes.LPCWSTR, u]),
                ("BCryptCloseAlgorithmProvider", [h, u]),
                ("BCryptGenerateKeyPair", [h, ph, u, u]),
                ("BCryptFinalizeKeyPair", [h, u]),
                ("BCryptExportKey", [h, h, wintypes.LPCWSTR, buf, u, pu, u]),
                ("BCryptImportKeyPair", [h, h, wintypes.LPCWSTR, ph, buf, u, u]),
                ("BCryptSignHash", [h, buf, buf, u, buf, u, pu, u]),
                ("BCryptVerifySignature", [h, buf, buf, u, buf, u, u]),
                ("BCryptDestroyKey", [h])):
            fn = getattr(b, name)
            fn.argtypes = args
            fn.restype = ctypes.c_long          # NTSTATUS: 0 is success
        _lib = b
    return _lib


def _check(status: int, what: str) -> None:
    if status != 0:
        raise OSError(f"Windows couldn't {what} (0x{status & 0xFFFFFFFF:08X})")


class _Provider:
    """The ECDSA P-256 algorithm handle, closed when done."""

    def __enter__(self) -> ctypes.c_void_p:
        self.handle = ctypes.c_void_p()
        _check(_bcrypt().BCryptOpenAlgorithmProvider(ctypes.byref(self.handle), ALGORITHM,
                                                    None, 0), "open ECDSA P-256")
        return self.handle

    def __exit__(self, *_exc) -> None:
        _bcrypt().BCryptCloseAlgorithmProvider(self.handle, 0)


def _bytes(data: bytes):
    return (ctypes.c_ubyte * len(data)).from_buffer_copy(data)


def _export(key, kind: str) -> bytes:
    b = _bcrypt()
    size = wintypes.ULONG()
    _check(b.BCryptExportKey(key, None, kind, None, 0, ctypes.byref(size), 0), "size a key")
    out = (ctypes.c_ubyte * size.value)()
    _check(b.BCryptExportKey(key, None, kind, out, size.value, ctypes.byref(size), 0),
           "export a key")
    return bytes(out[:size.value])


def _blob(text: str, magic: int, parts: int) -> bytes:
    """A key blob from base64, or ValueError if it isn't the kind expected."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError("not base64") from exc
    if len(raw) != 8 + parts * KEY_BYTES or struct.unpack("<II", raw[:8]) != (magic, KEY_BYTES):
        raise ValueError("not an ECDSA P-256 key of that kind")
    return raw


def generate() -> Tuple[str, str]:
    """A new key pair, (private, public), each base64."""
    b = _bcrypt()
    with _Provider() as alg:
        key = ctypes.c_void_p()
        _check(b.BCryptGenerateKeyPair(alg, ctypes.byref(key), 256, 0), "make a key")
        try:
            _check(b.BCryptFinalizeKeyPair(key, 0), "finish a key")
            private, public = _export(key, PRIVATE_BLOB), _export(key, PUBLIC_BLOB)
        finally:
            b.BCryptDestroyKey(key)
    return base64.b64encode(private).decode("ascii"), base64.b64encode(public).decode("ascii")


def public_of(private: str) -> str:
    """The public half of a private key: the same X and Y under the public magic."""
    raw = _blob(private, PRIVATE_MAGIC, 3)
    public = struct.pack("<II", PUBLIC_MAGIC, KEY_BYTES) + raw[8:8 + 2 * KEY_BYTES]
    return base64.b64encode(public).decode("ascii")


def sign(private: str, data: bytes) -> str:
    """The base64 signature of ``data`` by ``private``."""
    b = _bcrypt()
    raw = _blob(private, PRIVATE_MAGIC, 3)
    digest = hashlib.sha256(data).digest()
    with _Provider() as alg:
        key = ctypes.c_void_p()
        _check(b.BCryptImportKeyPair(alg, None, PRIVATE_BLOB, ctypes.byref(key), _bytes(raw),
                                     len(raw), 0), "read the private key")
        try:
            out = (ctypes.c_ubyte * SIGNATURE_BYTES)()
            got = wintypes.ULONG()
            _check(b.BCryptSignHash(key, None, _bytes(digest), len(digest), out, SIGNATURE_BYTES,
                                    ctypes.byref(got), 0), "sign")
        finally:
            b.BCryptDestroyKey(key)
    return base64.b64encode(bytes(out[:got.value])).decode("ascii")


def verify(public: str, data: bytes, signature: str) -> bool:
    """Whether ``signature`` is ``public``'s over ``data``. Anything malformed
    - a key or signature that isn't base64, the wrong length or kind, a point
    off the curve - is simply not a match; it never raises for bad input."""
    try:
        raw = _blob(public, PUBLIC_MAGIC, 2)
        sig = base64.b64decode(signature, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return False
    if len(sig) != SIGNATURE_BYTES:
        return False
    b = _bcrypt()
    digest = hashlib.sha256(data).digest()
    with _Provider() as alg:
        key = ctypes.c_void_p()
        if b.BCryptImportKeyPair(alg, None, PUBLIC_BLOB, ctypes.byref(key), _bytes(raw),
                                 len(raw), 0) != 0:
            return False
        try:
            return b.BCryptVerifySignature(key, None, _bytes(digest), len(digest), _bytes(sig),
                                           len(sig), 0) == 0
        finally:
            b.BCryptDestroyKey(key)
