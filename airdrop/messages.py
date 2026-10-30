"""
messages.py
===========
The three requests of an AirDrop transfer - Discover, Ask, Upload - and their
answers, as an iPhone on iOS 27 sends and expects them. Bodies are binary
property lists; the shapes below are the ones recorded from a real iPhone in
M0 (see the spike's M0-RESULTS.md), not the iOS 12 ones OpenDrop sends.

**Discover** - "who are you?". A receiver in Everyone mode answers anyone with
the name the share sheet shows. An iPhone also sends its Apple-signed
``SenderRecordData``, which only matters to a receiver in Contacts Only.

**Ask** - "will you take these?". It blocks until the person taps Accept or
Decline. What iOS 27 adds over OpenDrop's: a ``TransferID`` that the Upload
repeats, ``TransferType``, an empty ``Items`` (URLs go there), and each file's
``FileSize``, with ``ShouldConvertMediaFormats`` where OpenDrop had
``ConvertMediaFormats``. The iPhone's 3-byte ``SenderIdentityAuthTag`` can't be
produced without Apple's keys, and Everyone mode doesn't ask for it.

**Upload** - the archive (``archive.py``), as ``application/x-dvzip`` with
``TotalBytes`` and the ``TransferID`` as headers.
"""

from __future__ import annotations

import json
import os
import plistlib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

USER_AGENT = "AirDrop/1.0"
DVZIP = "application/x-dvzip"
CPIO = "application/x-cpio"     # legacy: gzipped cpio, from older Macs and OpenDrop

# A receiver's TXT "flags". An iPhone advertises 0x1b3fb; we claim only what
# this receiver really does. 0x02 is the one that matters: "send me dvzip".
SUPPORTS_URL = 0x01
SUPPORTS_DVZIP = 0x02
SUPPORTS_MIXED_TYPES = 0x08
SUPPORTS_DISCOVER = 0x80
RECEIVER_FLAGS = SUPPORTS_DVZIP | SUPPORTS_MIXED_TYPES | SUPPORTS_DISCOVER

# Media capabilities, as JSON inside the Discover answer. Version 1 and no
# codecs asks a sender for formats anything can open - JPEG rather than HEIC -
# which is what a Windows PC wants.
MEDIA_CAPABILITIES = json.dumps({"Version": 1}).encode("utf-8")

# Uniform Type Identifiers by extension, for the Ask. Anything not listed is
# public.data, which a receiver saves to Files rather than Photos.
UTI = {
    ".jpg": "public.jpeg", ".jpeg": "public.jpeg", ".png": "public.png",
    ".gif": "com.compuserve.gif", ".heic": "public.heic", ".heif": "public.heif",
    ".webp": "org.webmproject.webp", ".tif": "public.tiff", ".tiff": "public.tiff",
    ".bmp": "com.microsoft.bmp",
    ".mov": "com.apple.quicktime-movie", ".mp4": "public.mpeg-4", ".m4v": "com.apple.m4v-video",
    ".mp3": "public.mp3", ".m4a": "com.apple.m4a-audio", ".wav": "com.microsoft.waveform-audio",
    ".pdf": "com.adobe.pdf", ".txt": "public.plain-text", ".rtf": "public.rtf",
    ".csv": "public.comma-separated-values-text", ".json": "public.json",
    ".html": "public.html", ".zip": "public.zip-archive",
    ".doc": "com.microsoft.word.doc", ".docx": "org.openxmlformats.wordprocessingml.document",
    ".xls": "com.microsoft.excel.xls", ".xlsx": "org.openxmlformats.spreadsheetml.sheet",
    ".ppt": "com.microsoft.powerpoint.ppt", ".pptx": "org.openxmlformats.presentationml.presentation",
}


def uti_for(name: str) -> str:
    return UTI.get(os.path.splitext(name)[1].lower(), "public.data")


def _dump(obj: dict) -> bytes:
    return plistlib.dumps(obj, fmt=plistlib.FMT_BINARY)


def _load(body: bytes) -> dict:
    try:
        obj = plistlib.loads(body)
    except Exception:
        raise ValueError("not a property list") from None
    if not isinstance(obj, dict):
        raise ValueError("not a dictionary")
    return obj


# --------------------------------------------------------------------------- #
#  Discover
# --------------------------------------------------------------------------- #

def discover_request() -> bytes:
    return _dump({})


def discover_response(name: str, model: str) -> bytes:
    return _dump({
        "ReceiverComputerName": name,
        "ReceiverModelName": model,
        "ReceiverMediaCapabilities": MEDIA_CAPABILITIES,
    })


def receiver_name(discover_answer: bytes) -> Optional[str]:
    """The name in a Discover answer; None if the receiver won't say (Contacts Only)."""
    name = _load(discover_answer).get("ReceiverComputerName")
    return name if isinstance(name, str) and name else None


# --------------------------------------------------------------------------- #
#  Ask
# --------------------------------------------------------------------------- #

@dataclass
class FileOffer:
    name: str
    type: str = "public.data"
    size: Optional[int] = None
    bom_path: str = ""
    is_directory: bool = False

    def __post_init__(self):
        if not self.bom_path:
            self.bom_path = "./" + self.name


@dataclass
class Ask:
    """An Ask as received, already parsed. Every field is the sender's claim."""
    sender_name: str
    sender_model: str = ""
    sender_id: str = ""
    bundle_id: str = ""
    transfer_id: Optional[str] = None
    files: List[FileOffer] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    icon: Optional[bytes] = None
    signed: bool = False    # carried SenderRecordData (unverified)

    @property
    def total_size(self) -> Optional[int]:
        """Bytes offered, or None if any file left its size out (older senders)."""
        sizes = [f.size for f in self.files]
        return None if any(s is None for s in sizes) else sum(sizes)


def ask_request(sender_name: str, sender_model: str, sender_id: str,
                transfer_id: str, files: Sequence[FileOffer],
                icon: Optional[bytes] = None) -> bytes:
    body = {
        "TransferID": {"id": transfer_id},
        "TransferType": {"files": {}},
        "SenderID": sender_id,
        "BundleID": "com.apple.finder",
        "SenderComputerName": sender_name,
        "SenderModelName": sender_model,
        "Items": [],
        "Files": [{
            "FileName": f.name,
            "FileType": f.type,
            "FileSize": f.size or 0,
            "FileBomPath": f.bom_path,
            "FileIsDirectory": f.is_directory,
            "ShouldConvertMediaFormats": False,
        } for f in files],
        "ConvertMediaFormats": False,
    }
    if icon:
        body["FileIcon"] = icon
    return _dump(body)


def parse_ask(body: bytes) -> Ask:
    d = _load(body)

    def text(key: str) -> str:
        v = d.get(key)
        return v if isinstance(v, str) else ""

    files = []
    for entry in d.get("Files") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("FileName"), str):
            raise ValueError("a file entry without a name")
        size = entry.get("FileSize")
        files.append(FileOffer(
            name=entry["FileName"],
            type=entry.get("FileType") if isinstance(entry.get("FileType"), str) else "public.data",
            size=size if isinstance(size, int) and size >= 0 else None,
            bom_path=entry.get("FileBomPath") if isinstance(entry.get("FileBomPath"), str) else "",
            is_directory=bool(entry.get("FileIsDirectory")),
        ))
    tid = d.get("TransferID")
    tid = tid.get("id") if isinstance(tid, dict) else None
    icon = d.get("FileIcon")
    return Ask(
        sender_name=text("SenderComputerName") or "Someone nearby",
        sender_model=text("SenderModelName"),
        sender_id=text("SenderID"),
        bundle_id=text("BundleID"),
        transfer_id=tid if isinstance(tid, str) and tid else None,
        files=files,
        urls=[u for u in d.get("Items") or [] if isinstance(u, str)],
        icon=icon if isinstance(icon, (bytes, bytearray)) else None,
        signed=isinstance(d.get("SenderRecordData"), (bytes, bytearray)),
    )


def ask_response(name: str, model: str) -> bytes:
    return _dump({"ReceiverComputerName": name, "ReceiverModelName": model})


# --------------------------------------------------------------------------- #
#  Upload
# --------------------------------------------------------------------------- #

def upload_headers(transfer_id: str, total_bytes: int) -> dict:
    return {
        "Content-Type": DVZIP,
        "TotalBytes": str(total_bytes),
        "TransferID": transfer_id,
    }
