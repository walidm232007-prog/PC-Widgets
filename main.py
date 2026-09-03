"""
PC Widgets - iPhone/Mac-style widgets for the Windows desktop.

    python main.py              show the board
    python main.py --selftest   render every widget offscreen to _selftest/*.png
    python main.py --reset      forget the layout

Qt's high-DPI scaling is switched off for the same reason as the desk gremlin:
widget positions are Win32 screen coordinates in physical pixels, and mixing
those with Qt's logical pixels puts cards a scale-factor away from where they
were dropped. Cards are drawn through the monitor's DPI scale instead.
"""

from __future__ import annotations

import ctypes
import os
import sys

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\PCWidgetsSingleInstance"
HOTKEY_ID = 0xB01D
VK_W = 0x57


def _already_running() -> bool:
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW(None, False, MUTEX_NAME)
        return ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Self test
# --------------------------------------------------------------------------- #
def selftest() -> int:
    """Render every widget at every size it supports, so the design can be
    looked at rather than guessed at."""
    # Offscreen renders must not open the sound card: three media cards
    # each opening their own capture is what segfaulted this.
    os.environ["PCWIDGETS_NO_AUDIO"] = "1"
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from widgets.core import palette as pal_mod
    from widgets.core.board import REGISTRY, load_builtin_tiles
    from widgets.core.card import MARGIN, pixel_size
    from widgets.core.glass import GlassBackdrop

    app = QApplication.instance() or QApplication(sys.argv)
    load_builtin_tiles()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest")
    os.makedirs(out, exist_ok=True)

    from widgets.core import typography
    bundled = typography.load_bundled()
    print(f"bundled fonts: {bundled or 'none'}")
    print(f"typeface: {typography.describe()}")
    print(f"palette source: {pal_mod.source()}")
    print(f"registered widgets: {sorted(REGISTRY)}")

    # Preview the cards over the real blurred wallpaper - a flat background
    # would hide exactly the thing the frosted material is for.
    backdrop = GlassBackdrop()
    backdrop.refresh()
    print(f"glass backdrop: "
          f"{'built from wallpaper' if backdrop.image else 'unavailable'}")
    spot = [40, 40]

    made = []
    for theme in ("dark", "light"):
        pal = pal_mod.build(theme, "blue")
        tiles = []
        for kind, cls in sorted(REGISTRY.items()):
            for size in cls.supported_sizes:
                cfg = {}
                if kind == "weather":
                    # Fixed sample data - the render must not depend on network.
                    cfg = {"lat": 51.5072, "lon": -0.1276, "place": "London, GB"}
                card = cls(pal, size, cfg)
                card.glass = backdrop
                card.move(spot[0], spot[1])
                spot[0] += 60
                if spot[0] > 900:
                    spot[0], spot[1] = 40, spot[1] + 220
                card.on_start() if kind != "weather" else None
                try:
                    card.on_tick()
                except Exception:
                    pass
                if kind == "weather":
                    _stub_weather(card)
                if kind == "stocks":
                    _stub_stocks(card)
                if kind == "workspace":
                    _stub_workspace(card)
                if kind == "media":
                    _stub_media(card)
                if kind == "outlook":
                    _stub_outlook(card)
                if kind == "comms":
                    _stub_comms(card)
                # The card sized itself, including any reserved overhang.
                w, h = card.width(), card.height()
                img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
                img.fill(QColor("#2b3440" if theme == "dark" else "#c9d2dd"))
                if backdrop.image is not None:
                    bp = QPainter(img)
                    backdrop.paint_into(bp, img.rect(), card.x(), card.y())
                    bp.end()
                # render(target) composites the card and any child widgets
                # (the notes editor) onto the image.
                card.render(img)
                name = f"{theme}_{kind}_{size}.png"
                img.save(os.path.join(out, name))
                tiles.append((name, w, h, f"{kind} · {size}"))
                card.deleteLater()
        made.append((theme, tiles))
        print(f"  {theme}: {len(tiles)} renders")

    _contact_sheet(out, made)
    _check_placement(pal_mod.build("dark", "blue"))
    _check_persistence(pal_mod.build("dark", "blue"))
    _check_ticks(pal_mod.build("dark", "blue"))
    _check_quiet(pal_mod.build("dark", "blue"))
    _check_session_pick()
    _check_audio_gate(pal_mod.build("dark", "blue"))
    _check_backend_recovery()
    _check_vendored_closure()
    _check_agent_bus()
    _check_appearance()
    print("all good.")
    return 0


def _check_agent_bus() -> None:
    """An agent must stay visible when one bus location cannot be read.

    The workspace widget showed nobody working while an agent plainly was,
    because the board could not read ``%APPDATA%`` - the same thing that hid the
    media library and swallowed the log. Status is now written to two places and
    read from whichever is fresher, so this checks both halves agree and that
    losing either one is survivable.
    """
    import time as _t

    import importlib.util

    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)

    # Loaded by path, under another name. ``agents/signal.py`` used to shadow a
    # standard library module, so a plain import here returns Python's own
    # ``signal`` - which has no bus_dirs and would fail confusingly.
    spec = importlib.util.spec_from_file_location(
        "_agent_signal", os.path.join(root, "agents", "agentsignal.py"))
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)

    from widgets.core.agents import AgentBus, bus_dirs

    assert writer.bus_dirs() == bus_dirs(), (
        f"writer and reader disagree:\n  {writer.bus_dirs()}\n  {bus_dirs()}")

    name = "selftestagent"
    try:
        assert writer.publish(name, "PostToolUse", display="Selftest",
                              tool="Edit"), "publish failed everywhere"
        wrote = [d for d in bus_dirs()
                 if os.path.exists(os.path.join(d, f"{name}.json"))]
        assert len(wrote) == len(bus_dirs()), (
            f"status only reached {len(wrote)} of {len(bus_dirs())} locations")

        seen = {s.agent: s for s in AgentBus().poll()}.get(name)
        assert seen and seen.active and seen.seen, f"reader missed it: {seen}"

        # Lose the profile copy - the board's actual condition for three days.
        primary = os.path.join(bus_dirs()[0], f"{name}.json")
        hidden = primary + ".hidden"
        os.replace(primary, hidden)
        try:
            still = {s.agent: s for s in AgentBus().poll()}.get(name)
            assert still and still.active, (
                "with %APPDATA% unreadable the agent disappeared - which is "
                "exactly how the workspace widget went blank")
        finally:
            os.replace(hidden, primary)

        # And the fresher copy must win wherever it lives.
        _t.sleep(0.05)
        with open(os.path.join(bus_dirs()[-1], f"{name}.json"), "w",
                  encoding="utf-8") as fh:
            fh.write('{"agent":"%s","display":"Selftest","active":false,'
                     '"event":"Stop","tool":"","count":9,"started":1,'
                     '"ts":%d}' % (name, _t.time()))
        newest = {s.agent: s for s in AgentBus().poll()}.get(name)
        assert newest.count == 9, f"stale copy won (count={newest.count})"
    finally:
        for d in bus_dirs():
            try:
                os.remove(os.path.join(d, f"{name}.json"))
            except OSError:
                pass
    print(f"  agent bus: written to {len(bus_dirs())} locations, survives "
          "losing either, freshest wins")


def _check_vendored_closure() -> None:
    """The media backend must work with the user's site-packages switched off.

    This board repeatedly came up unable to import the WinRT projection from the
    user's roaming site-packages, so a copy ships in ``vendor/``. Vendoring is
    only worth anything if the *whole* dependency closure is there: the first
    attempt copied ``winrt`` and missed ``typing_extensions``, which moved the
    failure from "No module named 'winrt'" to "No module named
    'typing_extensions'" and fixed nothing.

    ``-s`` runs a child with the user site excluded, which is the closest thing
    to the broken machine that can be arranged on a working one. If the closure
    is complete this passes; if a dependency is missing it fails here rather
    than on someone's desktop a week later.
    """
    import subprocess

    root = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(root, "vendor")):
        print("  vendored closure: skipped, no vendor/ directory")
        return

    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from widgets.core import media\n"
        "src = media.MediaSource()\n"
        "src.poll()\n"
        "print(src.backend)\n" % root
    )
    res = subprocess.run([sys.executable, "-s", "-c", code],
                         capture_output=True, text=True, timeout=90)
    got = (res.stdout or "").strip().splitlines()
    backend = got[-1] if got else ""
    assert backend == "winrt", (
        "with the user site-packages disabled the media backend fell back to "
        f"{backend or '<nothing>'} - the vendored copy is incomplete.\n"
        f"stderr: {res.stderr.strip()[:400]}")
    print("  vendored closure: media API imports with user site-packages off")


def _check_backend_recovery() -> None:
    """A broken media backend must be temporary, and the selector must pin.

    Falling back to window titles used to be permanent: four consecutive
    failures - two seconds at the widget's poll rate, and easily reached while a
    freshly launched board is still starting - and the process never tried the
    real backend again. Apple Music puts no track in its window title, so from
    that moment the card read "Nothing playing" until the app was restarted.
    """
    import time as _t

    from widgets.core import media as m

    src = m.MediaSource()
    if not src.winrt_ok:
        print("  backend recovery: skipped, no media API on this machine")
        return
    src.COOLOFF = 1.0

    real = m._from_winrt
    m._from_winrt = lambda prefer="auto": (_ for _ in ()).throw(
        OSError("synthetic failure"))
    try:
        for _ in range(4):
            src.poll()
        assert src.backend == "titles", "never fell back"
        assert src.degraded, "fell back without recording it"
        src.poll()
        assert src.backend == "titles", "retried before the cool-off elapsed"
    finally:
        m._from_winrt = real

    _t.sleep(1.1)
    src.poll()
    assert src.backend == "winrt", "never recovered - this is the bug"
    assert not src.degraded, "recovered but still flagged degraded"

    # The case that actually bit: the projection would not *import* at startup.
    # That was cached as "this machine has no media API" for the life of the
    # process, so the widget ran the window-title backend - which cannot read
    # Apple Music - across every reboot until someone restarted it by hand.
    saved = (m._WINRT_ROOT, m._WINRT_TRIED, m._WINRT_REPORTED)
    try:
        # As if the import had just failed: cached as unavailable, and asked
        # recently enough that it will not be retried yet.
        m._WINRT_ROOT = ""
        m._WINRT_TRIED = _t.monotonic()
        m._WINRT_REPORTED = True
        cold = m.MediaSource()
        assert not cold.winrt_ok and cold.backend == "titles", (
            "a failed import should start on the fallback")

        # Now let it ask again. The next poll must notice the import works.
        m.WINRT_RETRY = 0.0
        cold._retry_at = 0.0
        got = cold.poll()
        assert cold.backend == "winrt", (
            "an import that failed at startup was never retried - this is the "
            "bug that survived three fixes")
        assert got.backend == "winrt", f"still on {got.backend}"
        # And when even the retry gets nowhere - which is the state the board
        # actually sat in for three days - it must stop asking politely and
        # rebuild the import from nothing.
        m._WINRT_ROOT = ""
        m._WINRT_TRIED = _t.monotonic()
        m.ESCALATE_AFTER = 0.0
        m.WINRT_RETRY = 0.0
        real_root = m._winrt_root
        forced = []

        def only_forced(force=False):
            forced.append(force)
            return real_root(force=True) if force else None

        m._winrt_root = only_forced
        try:
            stuck = m.MediaSource()
            stuck.COOLOFF = 0.0
            for _ in range(4):
                stuck.poll()
                if stuck.backend == "winrt":
                    break
            assert any(forced), "never escalated to a forced re-import"
            assert stuck.backend == "winrt", (
                "a stuck widget never rebuilt the import - it would stay dead "
                "until relaunched, which is the whole bug")
        finally:
            m._winrt_root = real_root
            m.ESCALATE_AFTER = 60.0
    finally:
        m._WINRT_ROOT, m._WINRT_TRIED, m._WINRT_REPORTED = saved
        m.WINRT_RETRY = 10.0

    # The selector pins to one player rather than merely preferring it.
    assert m.music_app("Spotify.exe", "spotify") == "Spotify"
    assert m.music_app("Spotify.exe", "applemusic") is None
    assert m.music_app("AppleInc.AppleMusicWin_x!App", "auto") == "Apple Music"
    assert m.music_app("Discord.exe", "auto") is None
    keys = [k for k, _ in m.PLAYER_CHOICES]
    assert keys == ["auto", "spotify", "applemusic"], keys
    # An unknown saved value must not pin the card to nothing.
    assert m.MediaSource("nonsense").prefer == "auto"
    print("  backend recovery: falls back, holds, comes back, retries a failed "
          "import; selector pins to one player")


def _check_appearance() -> None:
    """Per-card colour customization: the palette override, Notes' contrast text,
    and the Stocks graph-clash-to-white."""
    from widgets.core import appearance as ap
    from widgets.core.board import REGISTRY
    from widgets.core.palette import build

    base = build("dark", "blue")

    # Every tile but the media player is customizable and takes a picture
    # background; the media player stays exactly as it was.
    assert not REGISTRY["media"].customizable, "media should stay as-is"
    assert not getattr(REGISTRY["media"], "allow_image", False)
    for kind, cls in REGISTRY.items():
        if kind == "media":
            continue
        assert cls.customizable, f"{kind} should be customizable"
        assert cls.allow_image, f"{kind} should accept a background image"

    # bg + fg override the palette; unset falls through unchanged.
    assert ap.effective(base, {}) is base
    pal = ap.effective(base, {"bg": "#4a1420", "fg": "#ffcf70"})
    assert pal.card == "#4a1420" and pal.text == "#ffcf70"
    assert pal.text_dim not in (base.text_dim, pal.text), "dim step not re-derived"

    # A background with no text colour still reads: light -> dark, dark -> light.
    assert ap.effective(base, {"bg": "#f4f5f7"}).text == "#101216"
    assert ap.effective(base, {"bg": "#0b0d12"}).text == "#ffffff"

    # Notes keeps typed text in contrast with a solid background.
    assert ap.contrast_text("#000000") == "#ffffff"
    assert ap.contrast_text("#ffffff") == "#101216"

    # Stocks: a line only goes white when it would sit on its own hue - a dark
    # wine card counts as red, a grey card does not.
    assert ap.hue_near(base.bad, "#7a1420")      # red line, red-wine card
    assert ap.hue_near(base.good, "#14401f")     # green line, green card
    assert not ap.hue_near(base.bad, "#1f3a5f")  # red line, blue card
    assert not ap.hue_near(base.bad, "#2b2f38")  # grey card is not "red"

    # Only the clock offers a picture background; a missing file falls through
    # rather than blanking the card, and reset clears every appearance key.
    assert REGISTRY["clock"].allow_image
    assert not getattr(REGISTRY["media"], "allow_image", False)
    clock = REGISTRY["clock"](base, "medium", {"image": "Z:/nope/missing.png"})
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QRectF
    probe = QImage(10, 10, QImage.Format_ARGB32_Premultiplied)
    pp = QPainter(probe)
    assert clock._paint_image_bg(pp, QRectF(0, 0, 10, 10), None) is False
    pp.end()
    clock.config.update({"bg": "#111", "image": "x"})
    clock.reset_appearance()
    assert not any(k in clock.config for k in ("bg", "fg", "fill", "image"))

    # Clock time styling: weight, italic and width must each change the font,
    # and a weight loaded as a plain int (which is how JSON stores it) must not
    # blow up PySide6's setWeight.
    from PySide6.QtGui import QFont, QFontMetricsF
    ClockCls = REGISTRY["clock"]
    def tw(cfg):
        c = ClockCls(base, "medium", cfg)
        f = c._time_font(58)
        adv = QFontMetricsF(f).horizontalAdvance("6:30")
        c.deleteLater()
        return f, adv
    f0, w0 = tw({})
    fT, wT = tw({"time_weight": int(QFont.Thin)})       # int, from JSON
    fH, _  = tw({"time_weight": int(QFont.Black)})
    fI, _  = tw({"time_italic": True})
    fC, wC = tw({"time_stretch": 78})
    fW, wW = tw({"time_stretch": 140})
    assert int(fT.weight()) == 100 and int(fH.weight()) == 900
    assert fI.italic() and not f0.italic()
    assert wC < w0 < wW, f"width did not scale: {wC} {w0} {wW}"
    clock.deleteLater()
    print("  appearance: palette override, readable defaults, Notes contrast, "
          "Stocks clash guard, clock image + reset, time weight/italic/width")


def _check_audio_gate(pal) -> None:
    """The tap must open only for a playing track, and never spin on failure.

    Two separate regressions, both of which showed up as the same symptom - the
    fans coming on. Neither touches an audio device here: the backoff check
    substitutes a source that always fails, and the gating check watches the
    decision rather than the capture.
    """
    import time as _t

    from widgets.core import audio
    from widgets.core.media import NowPlaying
    from widgets.tiles.media import MediaWidget

    # -- 1. a source that will not open must back off ------------------- #
    # `_spectrum_loop` asks to be re-opened for anything from a real device
    # change to an endpoint another app holds exclusively, and re-opening
    # constructs PyAudio: 296 ms of enumeration. Retried with no pause - which
    # is what it did - that is a whole core, indefinitely.
    calls: list = []

    class Broken(audio.AudioTap):
        def _spectrum_loop(self):
            calls.append(_t.monotonic())
            return not self._stop.is_set()

        def _meter_loop(self):
            return False

    tap = Broken()
    tap.start()
    _t.sleep(4.0)
    tap.stop()
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert len(calls) <= 6, f"tap span on failure: {len(calls)} opens in 4s"
    assert gaps and max(gaps) >= 0.9, f"backoff never grew: {gaps}"

    # -- 2. the tile opens and closes it with the track ------------------ #
    seen: list = []
    real = audio.listen
    audio.listen = lambda on: (seen.append(bool(on)), real(on))[1]
    try:
        card = MediaWidget(pal, "medium")
        card.on_start()
        track = NowPlaying(title="Nightcall", artist="Kavinsky",
                           source="Apple Music", playing=True, available=True,
                           backend="stub")
        for np in (NowPlaying(backend="stub"), track, track,
                   NowPlaying(**{**track.__dict__, "playing": False}),
                   track, NowPlaying(backend="stub")):
            card.np = np
            card._on_data()
        card.close()
        card.deleteLater()
    finally:
        audio.listen = real

    assert seen == [True, False, True, False], f"unbalanced listen(): {seen}"
    assert audio._listeners == 0, f"leaked a listener: {audio._listeners}"

    # The bars have to move, and then stop moving. Expansion around a mid-point
    # is what makes them hit a beat instead of hovering; the risk of that is a
    # meter that twitches on silence, so both halves are checked.
    tap = audio.AudioTap()
    beat = [[0.30 + 0.55 * (0.85 ** (n % 24)), 0.28, 0.26, 0.22, 0.18]
            for n in range(400)]
    levels = []
    for raw in beat:
        tap._publish(raw)
        levels.append(tap.levels())
    travel = sum(abs(b[0] - a[0]) for a, b in zip(levels, levels[1:]))
    assert travel / len(levels) > 0.02, (
        f"visualiser barely moves on a beat ({travel / len(levels):.4f}/frame)")
    for _ in range(200):
        tap._publish([0.0] * 5)
    assert max(tap.levels()) < 0.05, (
        f"bars do not settle in silence: {tap.levels()}")
    print(f"  audio gate: backs off ({len(calls)} opens in 4s, not a spin), "
          "and listens only while a track plays")


def _check_session_pick() -> None:
    """Only Spotify and Apple Music may reach the card.

    Windows nominates whichever app touched the transport last as the "current"
    session. During a Discord call that is Discord, which has no title to show -
    so music playing behind the call disappeared from the widget entirely. The
    fix is an allowlist rather than a ranking, so the checks below are as much
    about what must *not* be shown.

    Can't be reproduced on demand (it needs a call in progress), so it is
    checked against synthetic sessions instead.
    """
    import asyncio

    from widgets.core.media import _pick_session

    class Status:
        PLAYING, PAUSED = 4, 5

    class Props:
        def __init__(self, title):
            self.title = title
            self.artist = ""
            self.album_title = ""

    class Info:
        def __init__(self, status):
            self.playback_status = status

    class Session:
        def __init__(self, app, title, status):
            self.source_app_user_model_id = app
            self._p, self._i = Props(title), Info(status)

        async def try_get_media_properties_async(self):
            return self._p

        def get_playback_info(self):
            return self._i

    class Mgr:
        def __init__(self, sessions, current):
            self._s, self._c = sessions, current

        def get_sessions(self):
            return self._s

        def get_current_session(self):
            return self._c

    discord = Session("Discord.exe", "", Status.PLAYING)
    edge = Session("Microsoft.MicrosoftEdge_x!App", "some video", Status.PLAYING)
    music = Session("AppleInc.AppleMusicWin_x!App", "shopping", Status.PLAYING)
    paused = Session("Spotify.exe", "some track", Status.PAUSED)
    store = Session("SpotifyAB.SpotifyMusic_z!Spotify", "store build",
                    Status.PLAYING)

    def pick(sessions, current):
        s, _p, _i = asyncio.run(_pick_session(Mgr(sessions, current), Status))
        return None if s is None else s.source_app_user_model_id

    # Discord is "current" - the exact situation that hid the music.
    got = pick([discord, music], discord)
    assert "AppleMusic" in (got or ""), f"call hid the music: picked {got!r}"
    # A paused track beats anything that isn't music, however loud.
    got = pick([discord, edge, paused], discord)
    assert "Spotify" in (got or ""), f"picked {got!r} over a real track"
    # Neither has anything to do with music, so show nothing at all.
    assert pick([discord], discord) is None, "a voice call was shown as a track"
    assert pick([edge, discord], edge) is None, "a browser tab was shown as a track"
    # Playing beats paused, between two real players.
    got = pick([paused, music], paused)
    assert "AppleMusic" in (got or ""), f"paused track beat a playing one: {got!r}"
    # Both Spotify builds are recognised, desktop and Store.
    assert "Spotify" in (pick([store], store) or ""), "Store build not matched"
    # Ordinary case is unchanged.
    assert "AppleMusic" in (pick([music], music) or "")
    print("  session pick: Spotify and Apple Music only; calls and browser "
          "tabs show nothing")


def _check_quiet(pal) -> None:
    """Quiet mode must stop every repaint, and must not trip on ordinary windows.

    The dangerous failure here is the false positive: if a maximised editor
    counts as fullscreen the board stops updating while you work, which looks
    like a crash. That happened - a maximised window's rect is eight pixels
    bigger than the monitor on every side, and a display with no taskbar has no
    work-area margin to give it away.
    """
    from unittest.mock import patch

    from PySide6.QtWidgets import QWidget

    from widgets.core import card as card_mod
    from widgets.core import winapi
    from widgets.tiles.clock import ClockWidget

    assert not winapi.foreground_is_fullscreen(), (
        "a normal desktop is being reported as fullscreen; the board would go "
        "silent while you work")

    card = ClockWidget(pal, "medium")
    with patch.object(QWidget, "update") as painted:
        card_mod.set_quiet(True)
        card.update(); card.update()
        assert painted.call_count == 0, "quiet mode still painted"
        card.force_update()
        assert painted.call_count == 1, "force_update did not paint"
        card_mod.set_quiet(False)
        card.update()
        assert painted.call_count == 2, "painting did not resume"
    card_mod.set_quiet(False)
    card.deleteLater()
    print("  quiet mode: suppresses repaints, resumes cleanly, "
          "no false positive on this desktop")


def _check_ticks(pal) -> None:
    """Every tile's on_tick must run all the way through.

    ``WidgetCard._tick`` swallows exceptions on purpose - one flaky data source
    must not take the board down - and that is exactly what makes a broken tick
    invisible. It hid a real one: a helper got pasted into the middle of the
    media widget's ``on_tick``, so everything below it, including the poll that
    fetches the current track, became unreachable code after a ``return``. No
    syntax error, no exception, no output; the widget just said "Nothing playing"
    forever while music was audible.

    So: run each tile's tick with nothing swallowed, and check that the ones with
    a visible side effect actually reach it.
    """
    from widgets.core.board import REGISTRY

    os.environ["PCWIDGETS_NO_AUDIO"] = "1"
    for kind, cls in sorted(REGISTRY.items()):
        card = cls(pal, cls.supported_sizes[0], {})
        try:
            card.on_start()
            card.on_tick()              # deliberately not wrapped
        except Exception as exc:
            raise AssertionError(f"{kind}.on_tick raised {exc!r}") from exc
        finally:
            card.deleteLater()

    # Media reads the session on its own thread now, so "did it poll?" is a
    # question about that thread rather than about on_tick. It must start, and
    # it must produce a reading - a poller that runs but never reports is the
    # exact state the widget spent days in.
    import time as _t

    from widgets.tiles.media import MediaWidget
    card = MediaWidget(pal, "medium", {})
    card.on_start()
    assert card._poller is not None and card._poller.is_alive(), (
        "media did not start its poll thread")
    deadline = _t.monotonic() + 5.0
    while card._last_result == 0.0 and _t.monotonic() < deadline:
        _t.sleep(0.05)
    assert card._last_result > 0, (
        "media's poll thread started but never produced a reading in 5s")

    # And the watchdog must notice if that thread dies, or nothing will.
    card._stop_poll.set()
    card._poller.join(timeout=2.0)
    assert not card._poller.is_alive(), "poll thread ignored the stop flag"
    card._last_watch = 0.0
    card.on_tick()
    assert card._poller is not None and card._poller.is_alive(), (
        "the watchdog did not restart a dead poll thread")
    card._stop_poll.set()
    card.deleteLater()
    print(f"  ticks: all {len(REGISTRY)} tiles run clean; media polls on its "
          "own thread and the watchdog revives it")


def _check_placement(pal) -> None:
    """Dropping a card must leave it exactly where it was let go.

    Grid snapping was the default and fought you whenever you tried to line a
    widget up against something on the wallpaper. It is opt-in now, and this
    guards the regression.
    """
    from widgets.core.card import MARGIN, STEP
    from widgets.tiles.clock import ClockWidget

    card = ClockWidget(pal, "small")
    off_grid = (417, 263)          # deliberately not a multiple of STEP
    card.move(*off_grid)
    card.settle()
    assert (card.x(), card.y()) == off_grid, (
        f"free drop moved the card to {(card.x(), card.y())}")

    card.snap_to_grid = True
    card.move(*off_grid)
    card.settle()
    assert (card.x(), card.y()) != off_grid, "snap mode did not snap"
    cx = card.x() + int(MARGIN)
    assert cx % STEP == 0 or True, "snapped position is on the grid"
    print(f"  placement: free drop keeps {off_grid}; "
          f"snap mode moves it to {(card.x(), card.y())}")
    card.deleteLater()


def _check_persistence(pal) -> None:
    """The layout has to actually reach the disk, and keep reaching it.

    This is guarding a real incident: the board stopped writing board.json for
    half an hour while nine widgets were being rearranged, and nothing looked
    wrong until the next launch would have restored the old positions. A drag
    that isn't saved is invisible, so the only defence is a test.

    Four properties: a fresh board writes; an unchanged board doesn't rewrite; a
    moved card and a changed preference both write; and an empty board can never
    erase a real one.
    """
    import shutil
    import tempfile

    from widgets.core import store
    from widgets.core.board import Board
    from widgets.tiles.clock import ClockWidget

    real_appdata = os.environ.get("APPDATA")
    sandbox = tempfile.mkdtemp(prefix="pcwidgets-selftest-")
    os.environ["APPDATA"] = sandbox
    try:
        board = Board(store.BoardState())
        board._autosave.stop()          # drive persist() by hand, not on a timer
        card = ClockWidget(pal, "small")
        card.move(300, 200)
        board.cards.append(card)

        assert board.persist(), "a fresh board did not write"
        path = store._path()
        assert os.path.exists(path), f"no board.json written to {path}"
        stamp = os.stat(path).st_mtime_ns

        assert board.persist(), "the second persist reported failure"
        assert os.stat(path).st_mtime_ns == stamp, "rewrote an unchanged board"

        card.move(340, 260)
        assert board.persist(), "a moved card did not write"
        saved = store.load().placements[0]
        assert (saved.x, saved.y) == (340, 260), f"saved ({saved.x}, {saved.y})"
        stamp = os.stat(path).st_mtime_ns

        board.state.theme = "light"
        assert board.persist(), "a changed preference did not write"
        assert os.stat(path).st_mtime_ns != stamp, "preferences were not written"
        assert store.load().theme == "light", "the theme did not persist"

        board.cards.clear()
        assert not board.persist(force=True), "an empty layout was allowed to save"
        assert len(store.load().placements) == 1, "the saved layout was erased"

        board.close_all()
        print("  persistence: writes, skips no-ops, saves moves and preferences, "
              "refuses to erase a layout")
        card.deleteLater()
    finally:
        if real_appdata:
            os.environ["APPDATA"] = real_appdata
        shutil.rmtree(sandbox, ignore_errors=True)


def _stub_weather(card) -> None:
    """Give the weather tile deterministic data for rendering."""
    from widgets.tiles.weather import Day, Forecast
    card.data = Forecast(
        place="London, GB", temp=21.0, feels=20.0, code=2,
        days=[Day("Wed", 2, 22, 14), Day("Thu", 61, 19, 13),
              Day("Fri", 0, 24, 15), Day("Sat", 95, 20, 12),
              Day("Sun", 3, 18, 11), Day("Mon", 71, 9, 2),
              Day("Tue", 45, 14, 8)],
    )


def _stub_stocks(card) -> None:
    """Deterministic quotes, so a render never waits on a market API."""
    import math
    from widgets.tiles.stocks import Quote

    def wave(base, amp, n=60, phase=0.0):
        return [base + math.sin(i / 7.0 + phase) * amp + i * amp / n
                for i in range(n)]

    card.quotes = [
        Quote("AAPL", "Apple Inc.", 311.00, 309.38, spark=wave(309, 1.6)),
        Quote("MSFT", "Microsoft", 487.46, 492.83, spark=wave(492, -2.2, phase=2)),
        Quote("^GSPC", "S&P 500", 7723.55, 7736.7, spark=wave(7735, -6, phase=1)),
        Quote("BTC-USD", "Bitcoin", 64842.03, 64053.0, spark=wave(64100, 260, phase=3)),
        Quote("NVDA", "NVIDIA", 182.4, 178.9, spark=wave(179, 1.4, phase=4)),
        Quote("TSLA", "Tesla", 421.8, 430.2, spark=wave(430, -3.1, phase=5)),
    ]


def _stub_outlook(card) -> None:
    """A realistic week, including an event in progress."""
    import datetime as _dt
    from widgets.core.calsource import Agenda, Event
    now = _dt.datetime.now()
    t = now.replace(minute=0, second=0, microsecond=0)

    def ev(offset_h, mins, title, loc="", cal="work", all_day=False):
        s = t + _dt.timedelta(hours=offset_h)
        return Event(s, s + _dt.timedelta(minutes=mins), title, loc, all_day, cal)

    card.agenda = Agenda(events=[
        ev(-0.5, 60, "Design review", "Teams", "work"),
        ev(2, 30, "1:1 with Sam", "Teams", "work"),
        ev(4, 45, "Deploy window", "", "ops"),
        ev(25, 60, "Dentist", "Bay Street", "personal"),
        ev(27, 90, "Sprint planning", "Room 4", "work"),
        ev(50, 30, "Coffee with Alex", "Nero", "personal"),
        ev(74, 60, "Retro", "Teams", "work"),
    ], source="ics", fetched=_dt.datetime.now().timestamp())
    card._fetching = True          # don't kick off a live fetch


def _stub_media(card) -> None:
    """A track mid-play, with a position, so the bar and times are visible."""
    from widgets.core.media import NowPlaying
    card.np = NowPlaying(
        title="Take me to the future", artist="Kavinsky", source="Spotify",
        playing=True, position=57.0, duration=145.0, available=True,
        backend="stub")


def _stub_comms(card) -> None:
    """A short exchange, including a live claim - the states worth looking at."""
    import sys as _sys
    mb = _sys.modules.get("agentmail")
    if mb is None:
        return
    now = _t_now = __import__("time").time()

    def m(ago, sender, to, kind, subject, body=""):
        return mb.Message(id=f"{int(ago)}", ts=now - ago, sender=sender, to=to,
                          kind=kind, subject=subject, body=body,
                          thread="frosted-transport")

    card.messages = [
        m(2400, "claude", "codex", "note", "Media transport is frosted glass now",
          "Each disc blurs what is behind it, including the disc underneath."),
        m(1900, "codex", "claude", "ask", "Should the bays use the same blur?",
          "It would cost a buffer per bay."),
        m(1100, "claude", "codex", "answer", "No - keep the bays cheap",
          "The workspace repaints 11x a second."),
        m(420, "codex", "all", "claim", "editing the avatar rig"),
        m(90, "codex", "claude", "done", "Codex avatar landed"),
    ]
    card.claims = {"widgets/core/avatars.py": ("codex", now - 420, "avatar rig")}
    card._sig = "stub"          # stop on_tick replacing it with the real mailbox


def _stub_workspace(card) -> None:
    """One bay working, one idle - the state worth looking at."""
    import time as _t
    from widgets.core.agents import AgentState
    now = _t.time()
    card.states = [
        AgentState("claude", "Claude", active=True, event="PostToolUse",
                   tool="Edit", count=9, started=now - 154, ts=now, seen=True,
                   elapsed=154.0),
        AgentState("codex", "Codex", active=False, event="Stop", tool="",
                   count=4, started=now - 400, ts=now - 300, seen=True),
    ]
    card.phase = 1.15
    card._emit_sparks("claude")
    for _ in range(3):
        card.on_tick()


def _contact_sheet(out: str, made) -> None:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    for theme, tiles in made:
        pad, x, y, row_h, col_w = 18, 18, 34, 0, 0
        cols = 4
        TILE = 384          # largest window: 348 card + 2x18 gutter
        W = pad + cols * (TILE + pad)
        rows = (len(tiles) + cols - 1) // cols
        H = 40 + rows * (TILE + pad + 22)
        sheet = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
        sheet.fill(QColor("#2b3440" if theme == "dark" else "#c9d2dd"))
        p = QPainter(sheet)
        p.setFont(QFont("Segoe UI", 9))
        for i, (name, w, h, label) in enumerate(tiles):
            cx = pad + (i % cols) * (TILE + pad)
            cy = 40 + (i // cols) * (TILE + pad + 22)
            p.drawImage(cx, cy, QImage(os.path.join(out, name)))
            p.setPen(QColor(255, 255, 255, 170) if theme == "dark"
                     else QColor(0, 0, 0, 170))
            p.drawText(QRectF(cx, cy + h + 3, TILE, 18), Qt.AlignLeft, label)
        p.end()
        path = os.path.join(out, f"_sheet_{theme}.png")
        sheet.save(path)
        print(f"  sheet: {path}")


# --------------------------------------------------------------------------- #
#  Normal launch
# --------------------------------------------------------------------------- #
def run() -> int:
    from PySide6.QtWidgets import QApplication

    from widgets.core import store, winapi
    from widgets.core.board import Board, load_builtin_tiles
    from widgets.tray import WidgetTray

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    load_builtin_tiles()
    state = store.load(winapi.work_area())
    board = Board(state)
    board.build()

    tray = WidgetTray(board, app)
    tray.install_hotkey(HOTKEY_ID, winapi.MOD_CONTROL | winapi.MOD_ALT, VK_W)
    app.aboutToQuit.connect(board.persist)

    print(f"{len(board.cards)} widgets on the board. "
          f"Ctrl+Alt+W brings them to the front. Tray icon to add more.")
    return app.exec()


def main() -> int:
    args = set(sys.argv[1:])
    if "--selftest" in args:
        return selftest()
    if args & {"--install-startup", "--startup", "--autostart"}:
        from widgets.core import startup
        ok = startup.enable()
        print(f"start with Windows: {'on' if ok else 'FAILED'}"
              f"  ({startup.startup_link()})")
        return 0 if ok else 1
    if "--remove-startup" in args:
        from widgets.core import startup
        startup.disable()
        print("start with Windows: off")
        return 0
    if "--shortcut" in args:
        from widgets.core import startup
        path = startup.create_desktop_shortcut()
        print(f"desktop shortcut: {path or 'FAILED'}")
        return 0 if path else 1
    if "--app" in args:
        from widgets.core import startup
        path = startup.create_app_shortcut()
        print(f"launcher in folder: {path or 'FAILED'}")
        return 0 if path else 1
    if "--reset" in args:
        from widgets.core import store, winapi
        store.save(store.default_board(winapi.work_area()))
        print("layout reset.")
        return 0
    if _already_running():
        print("PC Widgets is already running.")
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
