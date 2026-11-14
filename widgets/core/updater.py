"""
updater.py
==========
Keeping an installed PC Widgets up to date by itself. AUTO-UPDATE.md has the
design: this finds an update, decides whether to trust it, downloads it, and
hands over to its installer at the next start (or on "Restart to update").

The release workflow attaches ``update.json`` to every release:

    {"manifest": {"version": "1.0.4",
                  "url": "https://github.com/.../releases/download/v1.0.4/PC-Widgets-Setup-1.0.4.exe",
                  "size": 33554432,
                  "sha256": "..."},
     "signature": "<base64 ECDSA P-256 signature of the manifest's canonical bytes>"}

GitHub serves the newest release's copy at a fixed address, so asking for it
is one small HTTPS request a day. Nothing about the PC is sent.

Trust runs in this order, and nothing is read from the manifest until the
signature has checked out against the key built into the app (signature.py):
then the version must be newer than this one, the installer's address must be
exactly this repository's file for that version, its size within bounds, and
once downloaded its length and SHA-256 must match the manifest's. A signed
manifest pointing anywhere else is still refused.

Only an installed copy updates itself. A copy run from a checkout - git owns
that folder - reports itself as off and never fetches anything.

Installing: the app starts the downloaded installer silently - /RELAUNCH says
how to reopen the app afterwards - and quits. Setup waits for it to have gone,
upgrades in place (packaging/pc-widgets.iss) and reopens it. The reopened app
settles the record: its own version is the new one, or the update didn't
take, and then that version isn't tried again by itself - so a failing update
can never loop on every start.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from . import net, paths, signature
from ..version import HOMEPAGE, VERSION

# "owner/name" of the repository the releases come from: the app's homepage.
REPO = HOMEPAGE.removeprefix("https://github.com/").strip("/")
UPDATE_URL = f"https://github.com/{REPO}/releases/latest/download/update.json"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"

# The public half of the release key, as signature.py's base64 blob. Empty
# until the release key is made (AUTO-UPDATE.md, M4): with no key, no update
# is trusted, so a build without one can never install anything.
PUBLIC_KEY = ""

MANIFEST_LIMIT = 64 * 1024              # update.json is a few hundred bytes
INSTALLER_MIN = 1024 * 1024             # an installer smaller than this is wrong
INSTALLER_LIMIT = 300 * 1024 * 1024     # and one bigger than this too
CHECK_EVERY = 24 * 3600                 # a successful check holds for a day
RETRY_AFTER = 3 * 3600                  # after a failed one, try again sooner
# How Setup reopens the app after an update: as it was running, at sign-in or
# with its window; selftest is the build's own test of an update.
RELAUNCH = ("background", "window", "selftest")
SETUP_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")


class UpdateError(Exception):
    """An update that can't be trusted or used, with the reason in words."""


def parse_version(text) -> Optional[Tuple[int, int, int]]:
    """'1.0.4' (or 'v1.0.4', or '1.2') as (1, 0, 4); None for anything else -
    a pre-release tag, a fourth part, a word."""
    if not isinstance(text, str):
        return None
    parts = text.strip().removeprefix("v").split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() and len(p) <= 6 for p in parts):
        return None
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return (nums[0], nums[1], nums[2])


def newer(candidate: str, current: str = VERSION) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def canonical(manifest: dict) -> bytes:
    """The exact bytes a manifest is signed as: sorted keys, no spaces, ASCII.
    The workflow signs these and the app checks these, so the JSON can be laid
    out however it likes in the file."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def installer_url(version: str) -> str:
    """Where this repository's release puts that version's installer."""
    return f"{DOWNLOAD_PREFIX}v{version}/PC-Widgets-Setup-{version}.exe"


@dataclass(frozen=True)
class Update:
    version: str
    url: str
    size: int
    sha256: str


def read_update(raw: bytes, public_key: Optional[str] = None) -> Update:
    """An update file's contents, checked; UpdateError says what's wrong."""
    key = PUBLIC_KEY if public_key is None else public_key
    if not key:
        raise UpdateError("this build has no release key, so no update can be trusted")
    try:
        doc = json.loads(raw.decode("utf-8"))
        manifest, sig = doc["manifest"], doc["signature"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise UpdateError("the update file can't be read") from exc
    if not isinstance(manifest, dict) or not isinstance(sig, str):
        raise UpdateError("the update file can't be read")
    if not signature.verify(key, canonical(manifest), sig):
        raise UpdateError("the update's signature doesn't match the release key")
    version, url = manifest.get("version"), manifest.get("url")
    size, digest = manifest.get("size"), manifest.get("sha256")
    if parse_version(version) is None:
        raise UpdateError("the update names no version")
    if url != installer_url(version):
        raise UpdateError("the update points somewhere other than this app's releases")
    if not isinstance(size, int) or isinstance(size, bool) or \
            not INSTALLER_MIN <= size <= INSTALLER_LIMIT:
        raise UpdateError("the update's size is out of bounds")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("the update's checksum isn't a SHA-256")
    return Update(version, url, size, digest)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class Updater(QObject):
    """Finds, checks and downloads an update on a worker thread.

    ``status``: off (a checkout, or switched off), idle, checking,
    downloading, up_to_date, ready (an installer for ``version`` is on disk,
    checked), installing (Setup started; the app is quitting), updated (this
    run is the update that just installed), offline (GitHub couldn't be
    reached) or failed (``detail`` says why). ``changed`` fires on the GUI
    thread whenever it moves on.

    The network calls, starting Setup, the clock, the key and the folder can
    be swapped for the selftest's; by default they are the real ones and the
    app's own updates folder.
    """

    changed = Signal()
    _landed = Signal(object)
    _moved = Signal(int, int)

    def __init__(self, parent=None, enabled: Optional[bool] = None, folder: Optional[str] = None,
                 current: str = VERSION, public_key: Optional[str] = None,
                 fetch: Optional[Callable] = None, download: Optional[Callable] = None,
                 spawn: Optional[Callable] = None,
                 clock: Callable[[], float] = time.time, threaded: bool = True) -> None:
        super().__init__(parent)
        self.enabled = paths.FROZEN if enabled is None else enabled
        self.folder = folder or os.path.join(paths.write_dir(), "updates")
        self.current = current
        self.key = PUBLIC_KEY if public_key is None else public_key
        self._fetch = fetch or net.fetch
        self._download = download or net.download
        self._spawn = spawn or start_detached
        self.clock = clock
        self.threaded = threaded
        self.status = "idle" if self.enabled else "off"
        self.detail = ""
        self.version = ""
        self.progress = (0, 0)
        self.update: Optional[Update] = None
        self.path = ""
        self._busy = False
        self._stop = threading.Event()
        self._state = self._read_state()
        self._landed.connect(self._land)
        self._moved.connect(self._move)
        # Where the last run left things, so Settings can say so straight away.
        ready = self._state.get("ready") or {}
        failed = self._state.get("failed") or {}
        updated = self._state.get("updated") or {}
        if not self.enabled:
            pass
        elif ready and newer(ready.get("version", ""), current):
            # Downloaded on an earlier run: ready still, if the file is.
            self.status, self.version, self.path = "ready", ready["version"], ready.get("path", "")
            if self._state.get("skip") == ready["version"] and failed:
                self.status, self.detail = "failed", failed.get("reason", "")
        elif updated.get("version") == current:
            self.status, self.version = "updated", current
        elif failed:
            self.status, self.detail = failed.get("kind", "failed"), failed.get("reason", "")
        elif self._state.get("checked"):
            self.status, self.version = "up_to_date", current

    # -- preferences ------------------------------------------------------------ #
    @property
    def auto(self) -> bool:
        """Automatic updates: checked daily and installed at the next start."""
        return bool(self._state.get("auto", True))

    def set_auto(self, on: bool) -> None:
        self._state["auto"] = bool(on)
        self._write_state()
        self.changed.emit()

    def checked_at(self) -> float:
        """When the last check finished, as time.time(); 0 if never."""
        try:
            return float(self._state.get("checked") or 0)
        except (TypeError, ValueError):
            return 0.0

    def has_ready(self) -> bool:
        """An installer is waiting for a newer version - by the record alone,
        without re-hashing it (apply() does that before running anything)."""
        ready = self._state.get("ready") or {}
        return bool(self.enabled and ready.get("path") and newer(ready.get("version", ""), self.current))

    def tip_due(self) -> bool:
        """Whether the tray should say this update is ready: once a version."""
        return self.status == "ready" and self._state.get("tipped") != self.version

    def tipped(self) -> None:
        self._state["tipped"] = self.version
        self._write_state()

    # -- what's kept between runs ------------------------------------------ #
    def _state_file(self) -> str:
        return os.path.join(self.folder, "state.json")

    def _read_state(self) -> dict:
        try:
            with open(self._state_file(), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self) -> None:
        try:
            os.makedirs(self.folder, exist_ok=True)
            tmp = self._state_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
            os.replace(tmp, self._state_file())
        except OSError:
            pass            # the next check finds out again; nothing is lost

    # -- checking ------------------------------------------------------------- #
    def due(self) -> bool:
        """Whether the daily check should run now."""
        if not self.enabled or self._busy:
            return False
        last = self._state.get("checked", 0)
        wait = RETRY_AFTER if self._state.get("failed") else CHECK_EVERY
        return self.clock() - float(last or 0) >= wait

    def check(self) -> None:
        """Look for an update now (Check now, or the daily check)."""
        if not self.enabled:
            self.status, self.detail = "off", "Updates come with the installed app."
            self.changed.emit()
            return
        if self._busy:
            return
        self._busy = True
        self._stop.clear()
        self.status, self.detail = "checking", ""
        self.changed.emit()
        if self.threaded:
            threading.Thread(target=self._work, daemon=True, name="updater").start()
        else:
            self._work()

    def stop(self) -> None:
        self._stop.set()

    def _work(self) -> None:
        try:
            result = self._find()
        except UpdateError as exc:
            result = ("failed", str(exc))
        except Exception as exc:        # the network, a timeout, a full disk
            result = ("offline", f"Couldn't reach GitHub ({type(exc).__name__}).")
        try:
            self._landed.emit(result)
        except RuntimeError:
            pass                        # the app closed first

    def _find(self):
        try:
            raw = self._fetch(UPDATE_URL, headers={"User-Agent": f"pc-widgets/{self.current}"},
                              limit=MANIFEST_LIMIT)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # GitHub answered: the newest release just has no update file.
                raise UpdateError("the latest release has no update file") from exc
            raise
        upd = read_update(raw, self.key)
        if not newer(upd.version, self.current):
            return ("up_to_date", upd.version)
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, f"PC-Widgets-Setup-{upd.version}.exe")
        if os.path.exists(path) and sha256_of(path) == upd.sha256:
            return ("ready", upd, path)             # already here from an earlier run
        self._moved.emit(0, upd.size)
        got = self._download(upd.url, path, INSTALLER_LIMIT, upd.size,
                             lambda n, total: self._moved.emit(n, total or 0), self._stop,
                             headers={"User-Agent": f"pc-widgets/{self.current}"})
        if got != upd.sha256:
            try:
                os.remove(path)
            except OSError:
                pass
            raise UpdateError("the download doesn't match the release")
        return ("ready", upd, path)

    def _move(self, got: int, total: int) -> None:
        if self.status in ("checking", "downloading"):
            self.status = "downloading"
            self.progress = (got, total)
            self.changed.emit()

    def _land(self, result) -> None:
        self._busy = False
        kind = result[0]
        self._state["checked"] = self.clock()
        self._state.pop("failed", None)
        if kind == "ready":
            _, upd, path = result
            self.update, self.path, self.version = upd, path, upd.version
            self.status, self.detail = "ready", ""
            self._state["ready"] = {"version": upd.version, "path": path, "sha256": upd.sha256}
            self._tidy(keep=path)
        elif kind == "up_to_date":
            self.status, self.version, self.detail = "up_to_date", self.current, ""
            self._state.pop("ready", None)
            self._tidy(keep=None)
        else:
            self.status, self.detail = kind, result[1]
            self._state["failed"] = {"reason": result[1], "kind": kind, "at": self.clock()}
        self._write_state()
        self.changed.emit()

    def _tidy(self, keep: Optional[str]) -> None:
        """Remove installers other than the one kept, and stray part files."""
        try:
            names = os.listdir(self.folder)
        except OSError:
            return
        for name in names:
            path = os.path.join(self.folder, name)
            if name.startswith("PC-Widgets-Setup-") and (keep is None or
                                                          os.path.normcase(path) != os.path.normcase(keep)):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # -- installing ------------------------------------------------------------ #
    def apply(self, relaunch: str = "window", manual: bool = False) -> bool:
        """Start installing the downloaded update; True if Setup started, and
        then the caller quits at once so Setup can replace the files. A version
        that failed to install isn't started by itself again, only ``manual``ly
        (Restart to update)."""
        if relaunch not in RELAUNCH:
            raise ValueError(f"relaunch must be one of {RELAUNCH}")
        ready = self.ready_installer()
        if ready is None:
            return False
        version, path = ready
        if not manual and self._state.get("skip") == version:
            return False
        args = [path, *SETUP_ARGS, f"/RELAUNCH={relaunch}",
                f"/LOG={os.path.join(self.folder, f'install-{version}.log')}"]
        self._state["installing"] = {"version": version, "from": self.current, "at": self.clock()}
        self._write_state()
        try:
            self._spawn(args)
        except OSError as exc:
            self._state.pop("installing", None)
            self._state["failed"] = {"reason": f"Setup didn't start ({exc}).", "at": self.clock()}
            self._write_state()
            self.status, self.detail = "failed", f"The update's Setup didn't start ({exc})."
            self.changed.emit()
            return False
        self.status, self.version, self.detail = "installing", version, ""
        self.changed.emit()
        return True

    def finish_install(self) -> None:
        """At start: settle the record of an update this app handed over to.
        Its version is now this copy's, so it took; or this copy is still the
        old one, so it didn't - and that version is then left alone until it's
        asked for again or a newer one comes out."""
        inst = self._state.get("installing")
        if not isinstance(inst, dict):
            return
        target = str(inst.get("version", ""))
        self._state.pop("installing", None)
        if not newer(target, self.current):
            self._state["updated"] = {"version": self.current, "from": inst.get("from", ""),
                                      "at": self.clock()}
            for key in ("ready", "skip", "failed"):
                self._state.pop(key, None)
            self._tidy(keep=None)
            self.status, self.version, self.detail = "updated", self.current, ""
        else:
            reason = f"The update to {target} didn't install."
            self._state["skip"] = target
            self._state["failed"] = {"reason": reason, "at": self.clock()}
            self.status, self.version, self.detail = "failed", target, reason
        self._write_state()
        self.changed.emit()

    def ready_installer(self) -> Optional[Tuple[str, str]]:
        """(version, path) of a downloaded update that still checks out - the
        file re-hashed against what was verified - or None."""
        ready = self._state.get("ready") or {}
        path, digest, version = ready.get("path", ""), ready.get("sha256", ""), ready.get("version", "")
        if not (self.enabled and path and digest and newer(version, self.current)):
            return None
        try:
            if sha256_of(path) != digest:
                raise OSError("changed on disk")
        except OSError:
            self._state.pop("ready", None)
            self._write_state()
            if self.status == "ready":
                self.status, self.detail = "failed", "The downloaded update changed on disk."
                self.changed.emit()
            return None
        return version, path


# --------------------------------------------------------------------------- #
#  Around the app
# --------------------------------------------------------------------------- #
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def start_detached(args) -> None:
    """Start Setup so it outlives the app: its own process group, no console,
    no inherited handles (the single-instance mutex above all, which Setup
    waits to see released), and out of any job the app runs in where the job
    allows it - a job that ends its processes with the app would end Setup."""
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(list(args), close_fds=True, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(list(args), close_fds=True, creationflags=flags)


def install_at_start(background: bool, updater: Optional[Updater] = None) -> bool:
    """At startup, before the board exists: settle an update that just ran,
    then start installing one that is ready. True means Setup has started and
    the app should quit now; it reopens the way it was starting - at sign-in
    (background) or with its window."""
    u = updater if updater is not None else Updater()
    if not u.enabled:
        return False
    u.finish_install()
    if not u.auto:
        return False
    return u.apply("background" if background else "window")


def _when(stamp: float, now: float) -> str:
    """'today at 3:40 PM', 'yesterday', or 'on 12 Nov'."""
    import datetime
    then = datetime.datetime.fromtimestamp(stamp)
    days = (datetime.date.fromtimestamp(now) - then.date()).days
    if days == 0:
        return f"today at {then.hour % 12 or 12}:{then.minute:02d} {'AM' if then.hour < 12 else 'PM'}"
    if days == 1:
        return "yesterday"
    return f"on {then.day} {then.strftime('%b')}"


def describe(u: Optional[Updater], now: Optional[float] = None) -> str:
    """The line under the version in Settings › App, in words."""
    if u is None or not u.enabled:
        return "Updates come with the installed app. This copy runs from its own folder."
    s = u.status
    if s == "checking":
        return "Checking for updates…"
    if s == "downloading":
        got, total = u.progress
        return f"Downloading the update… {int(got * 100 / total) if total else 0}%"
    if s == "ready":
        return f"Version {u.version} is ready. It installs the next time PC Widgets starts."
    if s == "installing":
        return f"Installing version {u.version}…"
    if s == "updated":
        return f"Updated to version {u.version}."
    if s == "offline":
        return "Couldn't reach GitHub to check for updates. It tries again later."
    if s == "failed":
        return u.detail or "The last check for updates didn't work."
    checked = u.checked_at()
    if checked:
        return f"PC Widgets is up to date. Checked {_when(checked, now or u.clock())}."
    if u.auto:
        return "It checks for updates once a day."
    return "Automatic updates are off. Check now looks once."


def action(u: Optional[Updater]) -> Tuple[str, bool]:
    """The version row's button: its label, and whether it can be pressed."""
    if u is None or not u.enabled:
        return "Check now", False
    if u.status in ("checking", "downloading"):
        return "Checking…", False
    if u.status == "installing":
        return "Installing…", False
    if u.status in ("ready", "failed") and u.has_ready():
        return "Restart to update", True
    return "Check now", True


def schedule(u: Updater, first_ms: int = 120_000, every_ms: int = 3_600_000) -> None:
    """The daily check in a running app: a couple of minutes after it starts,
    out of the way of the board coming up, then whenever one is due."""
    from PySide6.QtCore import QTimer

    def tick() -> None:
        if u.auto and u.due():
            u.check()

    QTimer.singleShot(first_ms, tick)
    timer = QTimer(u)
    timer.setInterval(every_ms)
    timer.timeout.connect(tick)
    timer.start()
    u._schedule = timer
