"""
media.py
========
What's playing, and how to control it.

Only Spotify and Apple Music count as "playing" - see :data:`MUSIC_APPS` for why
that is an allowlist rather than a preference, and for how to widen it.

Two backends behind one interface, picked at runtime:

**WinRT** (``winsdk``) talks to the same session manager the OS media flyout
uses. It gives everything: title, artist, album, playback state, position,
duration and the album art itself. It is not part of the standard library, so it
is used only if present.

**Window titles** is the fallback and needs nothing at all. Media players
advertise the current track in their window title - Spotify's is literally
``Artist - Track`` while playing - so the track can be read without any API.
What it can't give is artwork or a playback position; the widget renders a
placeholder for the first and hides the second rather than inventing them.

Transport works in both cases, because the media keys are global: Windows
routes ``VK_MEDIA_PLAY_PAUSE`` to whichever app currently holds media focus,
which is exactly the app the widget is displaying.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_VOLUME_MUTE = 0xAD
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002


def _tap(vk: int) -> None:
    try:
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def play_pause() -> None:
    _tap(VK_MEDIA_PLAY_PAUSE)


def next_track() -> None:
    _tap(VK_MEDIA_NEXT)


def prev_track() -> None:
    _tap(VK_MEDIA_PREV)


def toggle_mute() -> None:
    _tap(VK_VOLUME_MUTE)


# --------------------------------------------------------------------------- #
@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    album: str = ""
    source: str = ""             # the app it came from
    playing: bool = False
    position: Optional[float] = None
    duration: Optional[float] = None
    art: object = None           # QImage when a backend can supply one
    available: bool = False      # is anything playing at all
    backend: str = ""

    @property
    def progress(self) -> Optional[float]:
        if self.position is None or not self.duration:
            return None
        return max(0.0, min(1.0, self.position / self.duration))

    def tint(self) -> Tuple[int, int, int]:
        """A stable colour derived from the track, for the art placeholder."""
        seed = f"{self.artist}|{self.title}".encode("utf-8", "replace")
        h = hashlib.sha1(seed).digest()
        # Keep it in a pleasant band: mid-saturation, mid-lightness.
        return (90 + h[0] % 110, 70 + h[1] % 110, 120 + h[2] % 110)


# --------------------------------------------------------------------------- #
#  Who counts as music
# --------------------------------------------------------------------------- #
# The widget follows these apps and nothing else.
#
# It used to follow whatever held the system transport slot, which sounds more
# capable and behaves worse: Discord registers a session for a voice call, Teams
# for a meeting, Edge for a muted autoplaying video, and any of them can be
# nominated the "current" session. A call would take the slot and the music
# playing behind it disappeared from the card - while the card showed nothing in
# its place, because a voice session has no track to show.
#
# Narrowing it to the two apps actually used for music makes the card mean one
# thing. Matched as substrings of :func:`_app_key`, so Store and desktop builds
# of the same app both land. Add a stem here to follow another player.
MUSIC_APPS = (
    ("spotify", "Spotify"),
    ("applemusic", "Apple Music"),
)

# How long one read of the media session may take before it counts as failed.
# Generous: a healthy read is about 10ms.
GRAB_TIMEOUT = 4.0

# What the player selector offers. The stems above are the stable ids, so the
# menu and the saved config never carry a display string.
PLAYER_CHOICES = (("auto", "Automatic"),) + MUSIC_APPS


def _app_key(app_id: str) -> str:
    """A media session's app id, reduced to something matchable.

    The same app arrives under wildly different ids depending on how it was
    installed: ``Spotify.exe`` for the desktop build, but
    ``SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify`` from the Store, and Apple
    Music is only ever ``AppleInc.AppleMusicWin_nzyj5cx40ttqa!App``. Dropping
    the entry point, the publisher hash and the extension leaves a stable stem
    to match against.
    """
    head = (app_id or "").split("!")[0]
    head = head.split("_")[0]                 # drop the UWP publisher hash
    # Strip the extension *before* splitting on dots, or "Spotify.exe" reduces
    # to "exe".
    if head.lower().endswith(".exe"):
        head = head[:-4]
    return head.lower()


def music_app(app_id: str, prefer: str = "auto") -> Optional[str]:
    """The display name if this session is a player we follow, else None.

    ``prefer`` is one of the ids in :data:`PLAYER_CHOICES`. ``"auto"`` takes
    whichever player is playing; naming one pins the card to it, so a second
    player left open in the background cannot take the card over.
    """
    key = _app_key(app_id)
    for stem, label in MUSIC_APPS:
        if stem in key:
            return label if prefer in ("auto", stem) else None
    return None


def friendly_source(app_id: str) -> str:
    """Turn a media session's app id into something worth displaying.

    Only music sessions reach the card, so this nearly always resolves through
    :data:`MUSIC_APPS`; the CamelCase split is a courtesy for a stem added there
    without a label being thought about.
    """
    named = music_app(app_id)
    if named:
        return named
    # Same reduction as _app_key, which keeps its capitals so CamelCase can be
    # split - and drops the extension *before* the dot split, or "Discord.exe"
    # comes out as "exe".
    head = (app_id or "").split("!")[0].split("_")[0]
    if head.lower().endswith(".exe"):
        head = head[:-4]
    return re.sub(r"(?<!^)(?=[A-Z])", " ",
                  head.split(".")[-1]).strip() or "Media"


def fmt(seconds: Optional[float], negative: bool = False) -> str:
    if seconds is None:
        return "--:--"
    s = int(max(0, seconds))
    return f"{'-' if negative else ''}{s // 60}:{s % 60:02d}"


# --------------------------------------------------------------------------- #
#  Backend: window titles (no dependencies)
# --------------------------------------------------------------------------- #
# exe -> (source label, does the title carry "Artist - Track"?)
#
# The same two apps as :data:`MUSIC_APPS`, for the same reason - the fallback
# should not be able to show something the real backend would refuse to.
#
# The third field is what this backend can honestly do for that player. Spotify
# writes "Artist - Track" into its window title, so the track is readable with
# no API at all. **Apple Music does not** - its window is called "Apple Music"
# and nothing else, whatever is playing - so there is no track to find here and
# pretending otherwise is worse than admitting it. See :meth:`MediaSource.poll`
# for why that mattered: a temporary WinRT failure used to drop the widget onto
# this backend permanently, and Apple Music then read as "Nothing playing" for
# the life of the process.
PLAYERS = {
    "spotify.exe": ("Spotify", "spotify", True),
    "applemusic.exe": ("Apple Music", "applemusic", False),
    "amp.exe": ("Apple Music", "applemusic", False),
}

# Titles a player shows when it is idle - not track names.
IDLE_TITLES = {
    "spotify", "spotify premium", "spotify free", "advertisement",
    "apple music", "music",
}


def _visible_windows() -> List[Tuple[str, int]]:
    import ctypes.wintypes as wt
    out: List[Tuple[str, int]] = []

    def cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if buf.value:
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.append((buf.value, pid.value))
        except Exception:
            pass
        return True

    try:
        proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb)
        user32.EnumWindows(proc, 0)
    except Exception:
        pass
    return out


_proc_cache: Tuple[float, dict] = (0.0, {})


def _pid_names() -> dict:
    """pid -> exe name, cached; tasklist is far too slow to call per frame."""
    global _proc_cache
    now = time.monotonic()
    if now - _proc_cache[0] < 4.0 and _proc_cache[1]:
        return _proc_cache[1]
    names = {}
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=6,
                             creationflags=0x08000000).stdout
        for line in out.splitlines():
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) > 1 and parts[1].isdigit():
                names[int(parts[1])] = parts[0].lower()
    except Exception:
        pass
    _proc_cache = (now, names)
    return names


def _from_titles(prefer: str = "auto") -> NowPlaying:
    names = _pid_names()
    best: Optional[NowPlaying] = None

    for title, pid in _visible_windows():
        exe = names.get(pid, "")
        raw = title.strip()

        if exe not in PLAYERS:
            continue
        label, stem, readable = PLAYERS[exe]
        if prefer not in ("auto", stem):
            continue
        if not readable:
            # The player is running and this backend cannot read it. Name it as
            # the source so the card says "Apple Music" rather than implying
            # nothing is open, and let the WinRT backend supply the track.
            if best is None:
                best = NowPlaying(source=label, backend="titles")
            continue
        if raw.lower() in IDLE_TITLES:
            # Player is open but not on a track; remember it as the source.
            if best is None:
                best = NowPlaying(source=label, backend="titles")
            continue
        artist, track = "", raw
        if " - " in raw:
            artist, track = raw.split(" - ", 1)
        return NowPlaying(title=track.strip(), artist=artist.strip(),
                          source=label, playing=True, available=True,
                          backend="titles")

    return best or NowPlaying(backend="titles")


# --------------------------------------------------------------------------- #
#  Backend: WinRT (only if winsdk is installed)
# --------------------------------------------------------------------------- #
# Two module layouts exist for the same API. ``winsdk`` was the original and has
# no wheels past Python 3.12; the ``winrt-*`` namespace packages are its
# successor and do. Prefer whichever is importable.
#
# None = not asked yet, "" = asked and failed, otherwise the package name.
_WINRT_ROOT: Optional[str] = None
_WINRT_TRIED = 0.0
_WINRT_REPORTED = False

# How long to wait before asking again whether the projection will import.
# Short, because this is the cold-boot case: the user is looking at a card that
# says nothing is playing, and an attempted import that fails costs microseconds.
WINRT_RETRY = 10.0

# How long on the fallback before giving up on polite retries and rebuilding the
# import from scratch.
ESCALATE_AFTER = 60.0


def vendor_dir() -> str:
    """The copy of the WinRT projection that ships beside the app."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "vendor")


def _ensure_user_site() -> None:
    """Make sure the WinRT projection can actually be found.

    The app folder goes on the path *first*, and that is the whole fix. The
    projection normally lives in the user's roaming site-packages, and this
    board repeatedly came up unable to import it from there - ``No module named
    'winrt'``, with that directory present, populated, and on ``sys.path``. The
    same process could not write to its ``%APPDATA%`` folder either, while
    writing happily to the app folder. I never found out why.

    So the dependency was removed instead of explained: ``winrt`` is 3MB, it
    ships in ``vendor/`` next to the code, and it is found there before anything
    under the profile is consulted. It is a namespace package, which is resolved
    by scanning directories - precisely the thing that was failing.

    The user site is still appended as a fallback, for an install without the
    vendored copy.
    """
    import site

    changed = False
    vendor = vendor_dir()
    if os.path.isdir(vendor) and vendor not in sys.path:
        sys.path.insert(0, vendor)      # ahead of the profile, deliberately
        changed = True
    try:
        user = site.getusersitepackages()
        if user and os.path.isdir(user) and user not in sys.path:
            sys.path.append(user)
            changed = True
    except Exception:
        pass
    if changed:
        importlib.invalidate_caches()


def _winrt_root(force: bool = False) -> Optional[str]:
    """Which WinRT projection is importable - re-asked if the answer was "none".

    Caching the *failure* forever is what kept this widget dead across reboots,
    and it is the reason three previous fixes did not help. The projection lives
    in the user's site-packages and pulls in native modules; that import can
    fail at a Windows login, when everything on the machine is starting at once
    and the files are being read - and scanned - for the first time. One failure
    at that moment and the process spent its entire life on the window-title
    backend, which cannot read Apple Music at all, because Apple Music's window
    is called "Apple Music" whatever is playing. The card then said "Nothing
    playing" until it was restarted by hand.

    Nothing was ever logged, because from the code's point of view nothing had
    gone wrong: the module simply "was not installed". So this also says so, out
    loud, once.

    Retrying costs one attempted import every :data:`WINRT_RETRY` seconds until
    one succeeds, and then never again.
    """
    global _WINRT_ROOT, _WINRT_TRIED, _WINRT_REPORTED

    if _WINRT_ROOT:
        return _WINRT_ROOT
    now = time.monotonic()
    if _WINRT_ROOT is not None and not force and now - _WINRT_TRIED < WINRT_RETRY:
        return None                      # asked recently, still no
    _WINRT_TRIED = now

    _ensure_user_site()
    if force:
        # Start the import from nothing. A part-completed import leaves the
        # namespace parents behind, and while a plain retry usually copes,
        # "usually" is not a basis for a widget that has already been dead for
        # three days.
        for name in [m for m in list(sys.modules)
                     if m == "winrt" or m.startswith(("winrt.", "winsdk"))]:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()

    problems = []
    for root in ("winrt", "winsdk"):
        try:
            __import__(f"{root}.windows.media.control")
            _WINRT_ROOT = root
            if _WINRT_REPORTED:
                _report(f"media: {root} imported on a retry - full track data "
                        "is available again")
            return root
        except Exception as exc:
            problems.append(f"{root}: {exc!r}")

    if not _WINRT_REPORTED or force:
        _WINRT_REPORTED = True
        _report("media: no media API could be imported, so only window titles "
                "are readable - and Apple Music does not put its track there. "
                f"Retrying every {WINRT_RETRY:.0f}s. sys.path has "
                f"{len(sys.path)} entries, user site "
                f"{'on' if _user_site_on_path() else 'MISSING FROM'} it. "
                "Reasons - " + "; ".join(problems))
    _WINRT_ROOT = ""
    return None


def _user_site_on_path() -> bool:
    import site
    try:
        return site.getusersitepackages() in sys.path
    except Exception:
        return False


def _report(msg: str) -> None:
    """Write a diagnostic somewhere it will survive.

    ``store._log`` swallows its own failures - it has to, it is called from
    paths that must not raise - but that means a message can vanish without
    trace, and a diagnostic that can vanish is worth very little. This one is
    written twice, to the data directory and to the app folder, so one path
    being unwritable cannot hide the evidence again.
    """
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n"
    try:
        from .store import _log
        _log(msg)
    except Exception:
        pass
    try:
        here = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with open(os.path.join(here, "_media.log"), "a",
                  encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _winrt_available(force: bool = False) -> bool:
    return _winrt_root(force) is not None


async def _pick_session(mgr, Status, prefer: str = "auto"):
    """The music session worth showing, or nothing.

    ``get_current_session()`` returns whichever app grabbed the transport slot
    last, and that is not the same question. Only sessions belonging to
    :data:`MUSIC_APPS` are considered at all; everything else - calls, meetings,
    browser video, games - is invisible to this widget by construction, rather
    than being out-ranked by a scoring rule that the next unusual app can
    defeat.

    Among the music sessions the order is: playing beats paused, having a title
    beats not, and the system's current session breaks the tie, so with one
    player open the behaviour is exactly what it always was.

    Returns ``(session, props, info)``; all three are None when nothing
    qualifies.
    """
    try:
        sessions = list(mgr.get_sessions())
    except Exception:
        sessions = []

    current = None
    try:
        current = mgr.get_current_session()
    except Exception:
        pass
    if current is not None and not sessions:
        sessions = [current]

    best = None
    for s in sessions:
        app_id = s.source_app_user_model_id or ""
        if music_app(app_id, prefer) is None:
            continue
        try:
            props = await s.try_get_media_properties_async()
            info = s.get_playback_info()
        except Exception:
            continue
        title = (props.title or "").strip()
        score = (
            1 if info.playback_status == Status.PLAYING else 0,
            1 if title else 0,
            1 if (current is not None
                  and app_id == current.source_app_user_model_id) else 0,
        )
        if best is None or score > best[0]:
            best = (score, s, props, info)

    if best is None:
        return None, None, None
    # A session with no track name has nothing to put on the card.
    _score, session, props, info = best
    if not (props.title or "").strip():
        return None, None, None
    return session, props, info


# Decoded cover art, keyed by track. Holds one entry: the moment the track
# changes the old sleeve is no longer wanted, and a 1200x1200 image is not
# something to keep a collection of.
_art_cache: dict = {}
_art_missing: set = set()


def _read_stream(reader, size: int) -> bytes:
    """DataReader.read_bytes has changed shape across projections."""
    try:
        return bytes(reader.read_bytes(size))
    except TypeError:
        buf = bytearray(size)
        reader.read_bytes(buf)
        return bytes(buf)


def _from_winrt(prefer: str = "auto") -> NowPlaying:
    """Full data via the system media session: art, position, exact state."""
    import asyncio
    import importlib

    global _art_cache, _art_missing

    root = _winrt_root()
    control = importlib.import_module(f"{root}.windows.media.control")
    streams = importlib.import_module(f"{root}.windows.storage.streams")
    Manager = control.GlobalSystemMediaTransportControlsSessionManager
    Status = control.GlobalSystemMediaTransportControlsSessionPlaybackStatus
    DataReader = streams.DataReader

    async def grab():
        mgr = await Manager.request_async()
        session, props, info = await _pick_session(mgr, Status, prefer)
        if session is None:
            return NowPlaying(backend="winrt")

        timeline = session.get_timeline_properties()

        # Artwork is cached per track, and that is not a micro-optimisation.
        # Covers arrive as full-size JPEGs - 1200x1200 is typical - and reading
        # the stream and decoding it took 25ms. Doing that twice a second for a
        # track that has not changed was 5% of a core, permanently, to produce
        # an image identical to the one already on screen.
        key = (props.artist, props.title, props.album_title)
        art = _art_cache.get(key)
        if art is None and key not in _art_missing:
            try:
                if props.thumbnail is not None:
                    stream = await props.thumbnail.open_read_async()
                    reader = DataReader(stream)
                    await reader.load_async(stream.size)
                    data = _read_stream(reader, stream.size)
                    from PySide6.QtGui import QImage
                    img = QImage()
                    if img.loadFromData(data):
                        art = img
                        _art_cache.clear()          # one track's cover is enough
                        _art_cache[key] = img
                        _art_missing.clear()
                if art is None:
                    # Remember the miss too, or a track with no cover pays the
                    # full round-trip on every single poll.
                    _art_missing.add(key)
            except Exception:
                art = None  # artwork is a bonus; never let it cost the track

        def secs(v):
            return v.total_seconds() if hasattr(v, "total_seconds") else float(v)

        dur = secs(timeline.end_time) - secs(timeline.start_time)
        pos = secs(timeline.position) - secs(timeline.start_time)

        return NowPlaying(
            title=props.title or "", artist=props.artist or "",
            album=props.album_title or "",
            source=friendly_source(session.source_app_user_model_id or ""),
            playing=info.playback_status == Status.PLAYING,
            position=pos if dur > 0 else None,
            duration=dur if dur > 0 else None,
            art=art, available=bool(props.title), backend="winrt",
        )

    # Bounded, because "await" on a WinRT operation is not a promise that it
    # will ever come back. If the completion never fires, an unbounded await
    # takes the poll thread with it and the card stops updating with nothing to
    # show for it. A timeout turns that into an ordinary failure, which the
    # caller already knows how to fall back from and recover.
    async def bounded():
        return await asyncio.wait_for(grab(), GRAB_TIMEOUT)

    return asyncio.run(bounded())


# --------------------------------------------------------------------------- #
#  Backend: a fresh interpreter, when this one cannot import the projection
# --------------------------------------------------------------------------- #
HELPER_TIMEOUT = 8.0


def _helper_python() -> str:
    """The console interpreter, so no window flashes on every poll."""
    exe = sys.executable or "python"
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            return cand
    return exe


def _from_subprocess(prefer: str, known: str) -> Tuple[NowPlaying, str]:
    """Read the session in a *new* process, and return it with its track key.

    The last resort, and the one that finally has a chance of working. The board
    has come up after every reboot unable to import the WinRT projection - user
    site directory present, on ``sys.path``, and ``winrt`` still not importable -
    while a process started seconds later imports it perfectly. Rather than keep
    theorising about why one interpreter's import state is wrong, ask a fresh
    one, which gets a fresh ``sys.path`` and fresh import caches.

    About a quarter of a second per call, so the caller polls slower in this
    mode. A card a second behind beats a card that has been wrong for days.
    """
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "winrt_read.py")
    try:
        res = subprocess.run(
            [_helper_python(), helper, prefer, known],
            capture_output=True, text=True, timeout=HELPER_TIMEOUT,
            creationflags=0x08000000)          # CREATE_NO_WINDOW
        data = json.loads(res.stdout.strip() or "{}")
    except Exception as exc:
        raise RuntimeError(f"helper failed: {exc!r}") from exc

    if not data.get("ok"):
        raise RuntimeError(f"helper: {data.get('error', 'no output')}")
    if not data.get("available"):
        return NowPlaying(backend="helper"), ""

    art = None
    blob = data.get("art") or ""
    if blob:
        try:
            from PySide6.QtGui import QImage
            img = QImage()
            if img.loadFromData(base64.b64decode(blob)):
                art = img
        except Exception:
            art = None

    return NowPlaying(
        title=data.get("title", ""), artist=data.get("artist", ""),
        album=data.get("album", ""), source=data.get("source", ""),
        playing=bool(data.get("playing")),
        position=data.get("position"), duration=data.get("duration"),
        art=art, available=True, backend="helper",
    ), data.get("key", "")


# --------------------------------------------------------------------------- #
class MediaSource:
    """Polls the best backend available, and remembers the last good track."""

    # How long to sit on the fallback before trying the real backend again.
    COOLOFF = 20.0

    def __init__(self, prefer: str = "auto") -> None:
        self.winrt_ok = _winrt_available()      # is the module there at all
        self.use_winrt = self.winrt_ok          # what it is using right now
        self.prefer = prefer if any(prefer == k for k, _ in PLAYER_CHOICES) \
            else "auto"
        self.last = NowPlaying()
        self.degraded = False        # on the fallback after a failure
        self._failures = 0
        self._retry_at = 0.0
        self._titles_since = 0.0     # when this got stuck on the fallback
        self._escalated = False
        self.use_helper = False      # read via a fresh process instead
        self._helper_key = ""        # the sleeve the helper need not resend

    @property
    def backend(self) -> str:
        if self.use_winrt:
            return "winrt"
        return "helper" if self.use_helper else "titles"

    def poll(self) -> NowPlaying:
        """Read the current track, preferring WinRT and recovering if it breaks.

        The recovery is the point, and there are two ways to end up needing it.
        Falling back after a burst of failures used to be **permanent** - four
        consecutive failures, two seconds at this poll rate. And a projection
        that would not import at startup was treated as a machine that simply
        does not have one, forever, which is the case that actually bit: see
        :func:`_winrt_root`.

        What made either invisible rather than merely degraded is that Apple
        Music puts no track in its window title, so the fallback had nothing to
        find and the card read "Nothing playing" while music was audibly
        playing.

        So: fall back, but keep asking, and write down that it happened.
        """
        clock = time.monotonic()
        if not self.use_winrt and clock >= self._retry_at:
            # Two different reasons to be here, so two cadences. A backend that
            # was working and started failing gets a proper cool-off; one that
            # merely would not import at startup is asked again promptly,
            # because that is a cold-boot hiccup and the user is sitting there
            # looking at a card that says nothing is playing.
            self._retry_at = clock + (self.COOLOFF if self.degraded
                                      else WINRT_RETRY)

            # Escalate if the ordinary retry is getting nowhere. A widget that
            # has been on the window-title backend for a minute is, in practice,
            # a widget that is going to stay there - that is exactly how this
            # spent three days dead - so stop asking politely and rebuild the
            # import from nothing.
            if self._titles_since == 0.0:
                self._titles_since = clock
            stuck = clock - self._titles_since
            force = stuck > ESCALATE_AFTER and not self._escalated
            if force:
                self._escalated = True
                _report(f"media: still on window titles after {stuck:.0f}s - "
                        "forcing a clean re-import of the media API")

            if _winrt_available(force):
                self.winrt_ok = self.use_winrt = True
                self.degraded = False
                self._failures = 0
                self._titles_since = 0.0
                self._escalated = False
                self.use_helper = False
                _report("media: media API is available; reading full track data")
            elif force and not self.use_helper:
                # This interpreter will not import it, however politely or
                # forcefully it is asked. Stop trying to fix this process and
                # ask a new one instead.
                self.use_helper = True
                _report("media: this process cannot import the media API at "
                        "all; reading it in a separate process instead")

        began = time.monotonic()
        try:
            if self.use_winrt:
                now = _from_winrt(self.prefer)
            elif self.use_helper:
                now, self._helper_key = _from_subprocess(self.prefer,
                                                         self._helper_key)
            else:
                now = _from_titles(self.prefer)
        except Exception as exc:
            took = time.monotonic() - began
            self._failures += 1
            if self._failures > 3 and self.use_winrt:
                self.use_winrt = False
                self.degraded = True
                self._failures = 0
                self._retry_at = clock + self.COOLOFF
                # Through _report, not store._log: the log under %APPDATA% is
                # exactly the one this process may not be able to write, and a
                # failure reason that lands nowhere is how this widget stayed
                # broken for a week. The recovery message already went to both.
                _report(f"media: winrt backend failed 4x after {took:.1f}s, "
                        f"using window titles for {self.COOLOFF:.0f}s ({exc!r})")
            return self.last

        self._failures = 0
        self.degraded = False
        # Keep showing the last track when a player goes quiet, the way the OS
        # flyout does - an empty card every time a song ends reads as broken.
        if not now.available and self.last.available:
            self.last.playing = False
            self.last.position = None
            return self.last
        self.last = now
        return now

    def set_preference(self, prefer: str) -> None:
        """Pin the card to one player, or "auto" for whichever is playing."""
        if prefer == self.prefer:
            return
        self.prefer = prefer
        # Drop the remembered track: it may belong to the player just deselected,
        # and `poll` deliberately keeps showing the last one when nothing is
        # available - which would leave the old player's track on the card.
        self.last = NowPlaying()
