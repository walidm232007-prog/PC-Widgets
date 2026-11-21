"""
PC Widgets - iPhone/Mac-style widgets for the Windows desktop.

    python main.py               show the board and open the app window
    python main.py --background  show the board only (how Windows starts it)
    python main.py --selftest    render every widget offscreen to _selftest/*.png
    python main.py --reset       forget the layout

Installed, ``PC Widgets.exe`` takes the same flags, plus two it runs as its own
helper processes (``--guard`` and ``--winrt-read``; see widgets/core/paths.py).

Launching it while it is already running opens the running copy's window
rather than starting a second board.

Qt's high-DPI scaling is switched off for the same reason as the desk gremlin:
widget positions are Win32 screen coordinates in physical pixels, and mixing
those with Qt's logical pixels puts cards a scale-factor away from where they
were dropped. Cards are drawn through the monitor's DPI scale instead.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import sys

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\PCWidgetsSingleInstance"
HOTKEY_ID = 0xB01D
VK_W = 0x57
APP_ID = "PCWidgets.Board"
BACKGROUND_FLAGS = {"--background", "--tray"}
# The installed exe's helper modes. From a checkout each is a script run by
# Python; the exe can't run a script, so it is started again with one of these.
HELPER_MODES = {"--guard", "--winrt-read"}


def _write_dir() -> str:
    from widgets.core import paths
    return paths.write_dir()


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
    # Nor follow a live wallpaper: the boards built here would each grab
    # whatever this PC's desktop runs, and a frame landing mid-check would
    # change what the glass checks see. _check_live_wallpaper covers it.
    os.environ["PCWIDGETS_NO_LIVE_WALLPAPER"] = "1"
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from widgets.core import palette as pal_mod
    from widgets.core import samples
    from widgets.core.board import REGISTRY, load_builtin_tiles
    from widgets.core.card import MARGIN, pixel_size
    from widgets.core.glass import GlassBackdrop

    app = QApplication.instance() or QApplication(sys.argv)
    load_builtin_tiles()
    out = os.path.join(_write_dir(), "_selftest")
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
    # The clear copy liquid glass bends is built on a thread; the board shows
    # frosted glass until it lands, but these renders should show the real thing.
    backdrop.wait_clear()
    print(f"glass backdrop: "
          f"{'built from wallpaper' if backdrop.image else 'unavailable'}, "
          f"liquid glass {'ready' if backdrop.ready else 'unavailable (frosted)'}")
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
                    samples.weather(card)
                if kind == "stocks":
                    samples.stocks(card)
                if kind == "workspace":
                    samples.workspace(card)
                if kind == "media":
                    samples.media(card)
                if kind == "timer":
                    samples.timer(card)
                if kind == "outlook":
                    samples.outlook(card)
                if kind == "comms":
                    samples.comms(card)
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
    _check_typeface()
    _check_weather_data(pal_mod.build("dark", "blue"))
    _check_weather_faces(out)
    _check_placement(pal_mod.build("dark", "blue"))
    _check_persistence(pal_mod.build("dark", "blue"))
    _check_first_run()
    _check_town_search()
    _check_welcome()
    _check_setup_steps()
    _check_button_shades()
    _check_updater()
    _check_update_install()
    _check_update_ui()
    _check_ticks(pal_mod.build("dark", "blue"))
    _check_quiet(pal_mod.build("dark", "blue"))
    _check_session_pick()
    _check_audio_gate(pal_mod.build("dark", "blue"))
    _check_visualiser()
    _check_backend_recovery()
    _check_tls()
    _check_fetch()
    _check_vendored_closure()
    _check_agent_bus()
    _check_appearance()
    _check_layout_mirror()
    _check_displays()
    _check_presets()
    _check_glass_displays()
    _check_liquid_glass(pal_mod.build("dark", "blue"))
    _check_glass_settings(pal_mod.build("dark", "blue"))
    _check_live_wallpaper()
    _check_smooth_glass(pal_mod.build("dark", "blue"))
    _check_live_glass(pal_mod.build("dark", "blue"))
    _check_gpu_glass(pal_mod.build("dark", "blue"))
    _check_glass_layer(pal_mod.build("dark", "blue"))
    _check_timer(pal_mod.build("dark", "blue"))
    _check_crop()
    _check_app_window()
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


def _check_typeface() -> None:
    """The board must be set in the bundled Inter, at the right optical size.

    The fonts ship in fonts/, where .gitignore keeps every other font file out.
    An exception there that stops matching, or a loader that skips the folder,
    would leave a clone drawing in a fallback face with nothing to say so.
    """
    from PySide6.QtGui import QFont

    from widgets.core import typography

    r = typography.resolve()
    assert r == {"display": "Inter Variable", "text": "Inter Variable"}, (
        f"the type resolved to {r}, not the bundled Inter")
    if typography._OPSZ is not None:
        big = typography.font(40, QFont.DemiBold).variableAxisValue(typography._OPSZ)
        small = typography.font(11).variableAxisValue(typography._OPSZ)
        assert (big, small) == (32.0, 14.0), (
            f"optical size {big} at 40pt and {small} at 11pt - the clock would "
            "lose its display cut")
    print("  typeface: bundled Inter wins the stack; display cut at 40pt, text "
          "cut at 11pt")


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

    from widgets.core import paths
    if paths.FROZEN:
        _check_vendored_closure_here()
        return
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

    # The visualiser's audio capture, the same way. Installed only in the user
    # site it was invisible to the board, which then ran the visualiser on the
    # peak meter - every bar moving together - and no tuning of the spectrum
    # made any difference. Loaded through the tap's own path, and opened far
    # enough to find the speakers' loopback endpoint; nothing is recorded.
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from widgets.core import audio\n"
        "audio._use_vendored()\n"
        "import pyaudiowpatch as pa\n"
        "print(pa.__file__)\n"
        "p = pa.PyAudio()\n"
        "try:\n"
        "    print(sum(1 for _ in p.get_loopback_device_info_generator()))\n"
        "finally:\n"
        "    p.terminate()\n" % root
    )
    res = subprocess.run([sys.executable, "-s", "-c", code],
                         capture_output=True, text=True, timeout=90)
    got = (res.stdout or "").strip().splitlines()
    assert len(got) == 2 and os.path.join(root, "vendor") in got[0], (
        "with the user site-packages disabled the audio capture did not load "
        f"from vendor/ - the visualiser would fall back to the peak meter.\n"
        f"stdout: {got} stderr: {res.stderr.strip()[:400]}")
    print(f"  vendored closure: media API and audio capture import with user "
          f"site-packages off ({got[1]} loopback endpoint(s))")


def _check_vendored_closure_here() -> None:
    """The same, in the installed exe. There is no interpreter to start with
    ``-s``, and no need: the exe never sees a user site, so this process is
    already the broken machine's case. Only the vendored copies can load."""
    from widgets.core import audio, media

    src = media.MediaSource()
    src.poll()
    assert src.backend == "winrt", (
        f"the installed exe's media backend fell back to {src.backend} - "
        "vendor/ is missing from the bundle or incomplete")
    audio._use_vendored()
    import pyaudiowpatch as pa
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    assert pa.__file__.startswith(vendor), (
        f"the audio capture loaded from {pa.__file__}, not the bundled vendor/")
    p = pa.PyAudio()
    try:
        endpoints = sum(1 for _ in p.get_loopback_device_info_generator())
    finally:
        p.terminate()
    print(f"  vendored closure: media API and audio capture load from the "
          f"bundle ({endpoints} loopback endpoint(s))")


def _check_tls() -> None:
    """The board's HTTPS must trust Windows' root certificates and nothing else.

    Python's default context on Windows also trusts the intermediate store,
    and this machine's still holds an ISRG Root X2 cross-certificate that
    expired in September 2025. Trusted, it made one of Open-Meteo's two servers
    fail with "certificate has expired" while every browser was fine, and the
    weather card said "URLError". Checked without the network: what the
    context trusts, not a handshake.
    """
    import hashlib
    import ssl

    from widgets.core import net

    def prints(certs):
        return {hashlib.sha1(c).hexdigest() for c in certs}

    ctx = net.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname, (
        "the board's HTTPS stopped checking certificates")
    ours = prints(ctx.get_ca_certs(binary_form=True))
    roots = prints(c for c, enc, _trust in ssl.enum_certificates("ROOT")
                   if enc == "x509_asn")
    assert ours and ours <= roots, (
        f"{len(ours - roots)} certificate(s) trusted from outside the root store")
    stale = "151682f5218c0a511c28f4060a73b9ca78ce9a53"     # the one that broke weather
    default = prints(ssl.create_default_context().get_ca_certs(binary_form=True))
    assert stale not in ours, "the expired ISRG Root X2 cross-certificate is trusted"
    note = ("the expired X2 cross-certificate Python's default trusts here is left out"
            if stale in default else "nothing from the intermediate store")
    print(f"  tls: {len(ours)} Windows root certificates trusted; {note}")


def _check_fetch() -> None:
    """Every fetch is HTTPS only, stays on HTTPS through redirects, and stops
    reading at a cap - checked without the network. urlopen on its own opens
    http://, file://, ftp:// and data: too, and follows a redirect to plain
    http://; the calendar's address, which works like a password, was fetched
    whatever it said."""
    import io
    import urllib.error
    import urllib.request

    from widgets.core import calsource, net

    for url in ("http://example.com/x", "file:///C:/Windows/win.ini", "data:,hello",
                "ftp://example.com/x"):
        try:
            net.fetch(url)
        except ValueError:
            continue
        raise AssertionError(f"net.fetch went to {url}")
    handler = net._StayOnHttps()
    req = urllib.request.Request("https://a.example/cal.ics")
    try:
        handler.redirect_request(req, None, 302, "Found", {}, "http://b.example/cal.ics")
        raise AssertionError("a redirect off HTTPS was followed")
    except urllib.error.URLError:
        pass
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://b.example/cal.ics"), \
        "an HTTPS redirect was refused"
    assert net.read_capped(io.BytesIO(b"x" * 100), 100) == b"x" * 100, "a reply at the cap was cut"
    try:
        net.read_capped(io.BytesIO(b"x" * 101), 100)
        raise AssertionError("a reply over the cap was read whole")
    except OSError:
        pass
    assert calsource.https_link(" webcal://p.example/c.ics ") == "https://p.example/c.ics"
    for bad in ("http://p.example/c.ics", "file:///C:/cal.ics", "C:\\cal.ics", ""):
        assert calsource.https_link(bad) is None, f"{bad!r} would be fetched as a calendar"
    agenda = calsource.from_ics("http://p.example/c.ics")
    assert agenda.error and not agenda.events, "an http:// calendar was fetched"
    print("  fetch: HTTPS only, redirects stay on HTTPS, replies capped "
          f"({net.MAX_BYTES // (1024 * 1024)} MB); calendars from https:// or webcal:// only")


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


@contextlib.contextmanager
def _sandboxed_layout():
    """Point *both* copies of the layout at a scratch directory for a check.

    Only %APPDATA% used to be redirected, so the checks that save a layout
    wrote their test board over the real mirror beside the app - and the mirror
    check then deleted it. On this machine the mirror is the copy that is
    current, because the board process cannot write %APPDATA%, so a single
    selftest run could hand the next launch a layout weeks out of date.
    """
    import tempfile
    from widgets.core import store

    real_appdata = os.environ.get("APPDATA")
    real_mirror = store._mirror_dir
    sandbox = tempfile.mkdtemp(prefix="pcwidgets-selftest-")
    os.environ["APPDATA"] = sandbox
    store._mirror_dir = lambda: os.path.join(sandbox, ".boardstate")
    try:
        yield sandbox
    finally:
        store._mirror_dir = real_mirror
        if real_appdata is not None:
            os.environ["APPDATA"] = real_appdata
        shutil.rmtree(sandbox, ignore_errors=True)


def _close_test_board(board) -> None:
    """Shut a check's board down and let its cards go. Deleting the cards
    alone left them in the board's list, and the glass finishing its clear
    copy on a thread a moment later repainted cards that were already gone."""
    cards = list(board.cards)
    board.close_all()
    for c in cards:
        c.deleteLater()


@contextlib.contextmanager
def _fake_displays(screens):
    """Stand a set of made-up monitors in for the real ones, and let a check
    plug, unplug and rearrange them by changing ``box["screens"]``.

    Everything that asks about monitors - the board, the card's drop clamp, the
    glass - goes through ``displays.current`` and ``displays.signature``, so
    replacing those two is enough to put a whole board on a desk that isn't
    this one.
    """
    from widgets.core import displays as dm

    real = (dm.current, dm.signature)
    box = {"screens": list(screens)}
    dm.current = lambda force=False: list(box["screens"])
    dm.signature = lambda: tuple((d.key, d.rect, d.work, d.primary)
                                 for d in box["screens"])
    try:
        yield box
    finally:
        dm.current, dm.signature = real


def _desk():
    """Two made-up monitors shaped like this machine's: a main landscape one and
    a portrait one on its left, set lower."""
    from widgets.core.displays import Display

    main = Display(r"\\?\DISPLAY#AAA0001#1&1&UID1#{g}", "Alpha",
                   (0, 0, 1920, 1080), (0, 0, 1920, 1040), True)
    left = Display(r"\\?\DISPLAY#BBB0002#1&1&UID2#{g}", "Beta",
                   (-1080, -413, 0, 1507), (-1080, -413, 0, 1507))
    return main, left


def _quiet_board(placements):
    """A board on a made-up desk whose cards never reach the screen and whose
    timers never fire - for checks that drive it by hand."""
    from widgets.core import store
    from widgets.core.board import Board

    board = Board(store.BoardState(placements=list(placements)))
    for timer in (board._autosave, board._watch, board._glass_timer, board._park):
        timer.stop()
    board.build()
    return board


def _check_displays() -> None:
    """Widgets belong to a monitor, not to a spot in Windows' shared space.

    Plugging a monitor in on the left, choosing another one as main, or dragging
    the arrangement about in Display settings changes the coordinates of every
    card without anything on screen moving - so a layout of positions alone
    comes apart. And a monitor that is unplugged must not take its widgets'
    places with it: they wait on the main display, still belonging to it, and go
    back when it does - including on another port.
    """
    from PySide6.QtCore import QRectF

    from widgets.core import displays as dm
    from widgets.core import store
    from widgets.core.card import MARGIN, WidgetCard
    from widgets.core.displays import Display

    main, left = _desk()
    m = int(MARGIN)

    # -- the parts that are only arithmetic ------------------------------ #
    assert dm.model_of(main.key) == "AAA0001" and dm.model_of("primary") == ""
    assert dm.labels([main, left]) == {main.key: "Main", left.key: "Left"}
    r1 = Display("r1", "", (1920, 0, 3840, 1080), (1920, 0, 3840, 1080))
    r2 = Display("r2", "", (3840, 0, 5760, 1080), (3840, 0, 5760, 1080))
    up = Display("up", "", (0, -1080, 1920, 0), (0, -1080, 1920, 0))
    assert dm.labels([r2, main, r1, up]) == {
        main.key: "Main", r1.key: "Right", r2.key: "Right 2", up.key: "Above"}
    # The same size carries a card over unchanged; a new size keeps it the same
    # distance from the nearer edge - 24 px in from the right stays 24 px in.
    assert dm.carry(100, 50, 348, 168, main.work, r1.work) == (2020, 50)
    assert dm.carry(1548, 848, 348, 168, (0, 0, 1920, 1040),
                    (0, 0, 2560, 1400)) == (2188, 1208)
    assert dm.carry(24, 24, 348, 168, (0, 0, 1920, 1040), (0, 0, 1280, 720)) == (24, 24)
    assert dm.carry(1700, 900, 348, 168, (0, 0, 1920, 1040), (0, 0, 800, 600)) == (452, 432)

    saved = {main.key: main.info(), left.key: left.info()}
    assert dm.resolve(saved, [main, left]) == {main.key: main, left.key: left}
    # Another port is still the same monitor ...
    ported = Display(left.key.replace("UID2", "UID7"), "Beta", left.rect, left.work)
    assert dm.resolve(saved, [main, ported])[left.key] is ported
    # ... a new monitor exactly where the old one sat is its replacement ...
    other = Display(r"\\?\DISPLAY#ZZZ9999#1&1&UID5#{g}", "Zed", left.rect, left.work)
    assert dm.resolve(saved, [main, other])[left.key] is other
    # ... but of two identical monitors, the one still here is only itself.
    twin = Display(left.key.replace("UID2", "UID3"), "Beta", (1920, 0, 3000, 1920),
                   (1920, 0, 3000, 1920))
    assert left.key not in dm.resolve(saved, [main, twin], known=[twin.key]), (
        "the remaining one of two identical monitors was taken for the other")

    # -- the board ------------------------------------------------------- #
    def area(c):
        r = c.card_rect()
        return QRectF(c.x() + r.left(), c.y() + r.top(), r.width(), r.height())

    real_start = WidgetCard.start
    WidgetCard.start = lambda self: None        # never put a card on screen
    try:
        with _sandboxed_layout(), _fake_displays([main, left]) as desk:
            board = _quiet_board([
                store.Placement("clock", "small", 100, 100),     # saved before displays
                store.Placement("system", "medium", -1000, 200,
                                display=left.key, area=list(left.work)),
            ])
            on_main, on_left = board.cards
            assert on_main.anchor.display == main.key, (
                "a layout from before displays were tracked lost its cards' screen")
            assert (on_left.x(), on_left.y()) == (-1000, 200)

            def settle_desk(screens):
                desk["screens"] = screens
                board._displays_moved()
                board.persist()

            # Left is dragged round to the other side of Main in Display
            # settings: its card goes with it, the one on Main doesn't move.
            moved = Display(left.key, "Beta", (1920, 0, 3000, 1920), (1920, 0, 3000, 1920))
            settle_desk([main, moved])
            assert (on_left.x(), on_left.y()) == (3000 - 1000, 200 + 413), (
                f"a rearranged monitor left its card at {on_left.x(), on_left.y()}")
            assert (on_main.x(), on_main.y()) == (100, 100)

            # Unplugged: the card waits on Main, clear of what is there, and the
            # saved layout still says it belongs to Left.
            settle_desk([main])
            assert on_left.displaced and main.work[0] <= area(on_left).left()
            assert QRectF(*main.work[:2], main.work[2] - main.work[0],
                          main.work[3] - main.work[1]).contains(area(on_left))
            assert not area(on_left).intersects(area(on_main)), (
                "a waiting card landed on top of one already there")
            board.persist(force=True)
            kept = {p.kind: p for p in store.load().placements}["system"]
            assert kept.display == left.key and (kept.x, kept.y) == (2000, 613), (
                f"the layout forgot where a waiting card belongs: {kept}")
            assert store.load().displays[left.key]["name"] == "Beta"

            # Back - on another port, so under another device path - and the
            # card goes home.
            back = Display(left.key.replace("UID2", "UID7"), "Beta", moved.rect, moved.work)
            settle_desk([main, back])
            assert not on_left.displaced and (on_left.x(), on_left.y()) == (2000, 613), (
                "a monitor on a new port did not get its widgets back")
            assert on_left.anchor.display == back.key

            # A waiting card dropped somewhere belongs there from then on.
            settle_desk([main])
            on_left.move(700, 500)
            board._card_moved(on_left)
            settle_desk([main, back])
            assert (on_left.x(), on_left.y()) == (700, 500), (
                "a card dropped on Main went back to the monitor it had left")

            # Sent to another display, a card keeps as near its spot as fits.
            assert board.move_to_display(on_main, back.key)
            assert board.display_of(on_main) == back and not on_main.displaced
            assert QRectF(*back.work[:2], back.work[2] - back.work[0],
                          back.work[3] - back.work[1]).contains(area(on_main))

            # A resolution change keeps a card flush against its edge - and the
            # monitor beside it, pushed along, takes its own cards with it.
            flush = board.cards[1]                    # system, on Main
            flush.move(main.work[2] - 24 - m - int(area(flush).width()), 300)
            board._card_moved(flush)
            clock_at = (on_main.x(), on_main.y())
            settle_desk([Display(main.key, "Alpha", (0, 0, 2560, 1440),
                                 (0, 0, 2560, 1400), True),
                         Display(back.key, "Beta", (2560, 0, 3640, 1920),
                                 (2560, 0, 3640, 1920))])
            assert int(area(flush).right()) == 2560 - 24, (
                f"a card 24 px from the right edge ended up at {area(flush).right()}")
            assert (on_main.x(), on_main.y()) == (clock_at[0] + 640, clock_at[1])
            board.close_all()
    finally:
        WidgetCard.start = real_start
    print("  displays: cards follow their monitor when rearranged, wait on Main "
          "when it's unplugged (saved as still belonging to it), go home on a new "
          "port; resolution changes keep edges; identical monitors stay apart")


def _check_presets() -> None:
    """A preset must put back every widget on every display, exactly as saved,
    and moving rather than rebuilding the ones already there."""
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QMenu

    from widgets.core import store
    from widgets.core.card import WidgetCard
    from widgets.tray import WidgetTray

    def area(c):
        r = c.card_rect()
        return QRectF(c.x() + r.left(), c.y() + r.top(), r.width(), r.height())

    main, left = _desk()
    real_start = WidgetCard.start
    WidgetCard.start = lambda self: None
    try:
        with _sandboxed_layout(), _fake_displays([main, left]) as desk:
            place = {"lat": 51.5072, "lon": -0.1276, "place": "London, GB"}
            board = _quiet_board([
                store.Placement("clock", "small", 100, 100, display=main.key,
                                area=list(main.work)),
                store.Placement("weather", "medium", 500, 100, dict(place),
                                display=main.key, area=list(main.work)),
                store.Placement("notes", "small", -1000, 300, display=left.key,
                                area=list(left.work)),
            ])
            clock, weather, notes = board.cards
            work = board.save_preset("Work")
            assert work and len(work.placements) == 3
            assert set(work.displays) == {main.key, left.key}, work.displays
            assert board.preset_in_use(work), "a preset just saved isn't in use"

            clock.move(300, 400)
            board._card_moved(clock)
            board.remove(notes)
            board.add("disk", "small", display=main.key)
            assert not board.preset_in_use(work)

            before = board.apply_preset(work)
            assert sorted(c.kind for c in board.cards) == ["clock", "notes", "weather"]
            assert clock in board.cards and weather in board.cards, (
                "a card that had only moved was rebuilt")
            assert (clock.x(), clock.y()) == (100, 100)
            notes2 = next(c for c in board.cards if c.kind == "notes")
            assert (notes2.x(), notes2.y()) == (-1000, 300) and not notes2.displaced
            assert board.preset_in_use(work)

            board.apply_layout(before.placements, before.displays)       # undo
            assert sorted(c.kind for c in board.cards) == ["clock", "disk", "weather"]
            assert (clock.x(), clock.y()) == (300, 400), "undo did not put it back"

            back = store.load()
            assert [p.name for p in back.presets] == ["Work"], back.presets
            assert isinstance(back.presets[0].placements[0], store.Placement)
            assert back.presets[0].placements[2].display == left.key

            # One name, one preset - ignoring case; renaming keeps them unique.
            assert board.save_preset("work") and len(board.state.presets) == 1
            evenings = board.save_preset("Gaming")
            assert not board.rename_preset(evenings, "WORK")
            assert board.rename_preset(evenings, "Evenings")
            i = board.remove_preset(evenings)
            assert [p.name for p in board.state.presets] == ["work"]
            board.put_preset(evenings, i)
            assert [p.name for p in board.state.presets] == ["work", "Evenings"]
            assert board.next_preset_name() == "Preset 1"

            # Applied with one of its displays unplugged: those widgets wait on
            # Main, clear of the others, and go to their own when it is back.
            desk["screens"] = [main]
            board._displays_moved()
            board.apply_preset(work)
            notes3 = next(c for c in board.cards if c.kind == "notes")
            assert notes3.displaced and notes3.anchor.display == left.key
            assert not any(area(notes3).intersects(area(c))
                           for c in board.cards if c is not notes3)
            assert board.preset_in_use(work), "a waiting card made its preset look unused"
            desk["screens"] = [main, left]
            board._displays_moved()
            assert not notes3.displaced and (notes3.x(), notes3.y()) == (-1000, 300)

            # The tray's submenu, filled without a tray icon ever appearing: a
            # preset per line, the one in use ticked.
            board.apply_preset(board.find_preset("Evenings"))

            class Stub:
                pass
            stub = Stub()
            stub.board = board
            stub._apply_preset = stub._save_preset = lambda *a: None
            stub.show_window = lambda *a: None
            menu = QMenu()
            WidgetTray._fill_presets(stub, menu)
            names = [a.text() for a in menu.actions() if a.isCheckable()]
            ticked = [a.text() for a in menu.actions() if a.isCheckable() and a.isChecked()]
            assert names == ["work", "Evenings"] and "Evenings" in ticked, (names, ticked)
            menu.deleteLater()
            board.close_all()
    finally:
        WidgetCard.start = real_start
    print("  presets: save every display, apply moving the cards already there, "
          "undo, unique names, a missing display's widgets wait and go home")


def _check_glass_displays() -> None:
    """Each monitor's frosted glass blurs that monitor's own wallpaper.

    Windows reports one wallpaper for the whole desktop, and with a different
    picture on each monitor that put one monitor's picture behind every card on
    the others. Checked against two made-up monitors with a solid picture each,
    so the colours say which picture landed where.
    """
    import tempfile

    from PySide6.QtGui import QColor, QImage

    from widgets.core import displays as dm
    from widgets.core import winapi
    from widgets.core.displays import Display
    from widgets.core.glass import GlassBackdrop

    tmp = tempfile.mkdtemp(prefix="pcwidgets-glass-")
    try:
        paths = {}
        for name, colour in (("red", "#ff0000"), ("blue", "#0000ff")):
            img = QImage(400, 300, QImage.Format_RGB32)
            img.fill(QColor(colour))
            paths[name] = os.path.join(tmp, f"{name}.png")
            img.save(paths[name])
        a = Display("A", "A", (0, 0, 800, 600), (0, 0, 800, 600), True)
        b = Display("B", "B", (800, -200, 1400, 600), (800, -200, 1400, 600))
        virtual = (0, -200, 1400, 600)
        img, origin = GlassBackdrop.compose([(a, paths["red"]), (b, paths["blue"])],
                                            winapi.WALL_FILL, virtual, QColor("#000000"))
        assert origin == (0, -200)
        on_a, on_b = img.pixelColor(300, 500), img.pixelColor(1100, 300)
        assert on_a.red() > 200 and on_a.blue() < 60, f"monitor A blurred {on_a.name()}"
        assert on_b.blue() > 200 and on_b.red() < 60, f"monitor B blurred {on_b.name()}"

        img, _ = GlassBackdrop.compose([(a, paths["red"]), (b, paths["blue"])],
                                       winapi.WALL_SPAN, virtual, QColor("#000000"))
        far = img.pixelColor(1300, 400)
        assert far.red() > 200, "a spanned wallpaper did not cross to the second monitor"

        mode, pics = GlassBackdrop.pictures(dm.current())
        own = sum(1 for _d, p in pics if p)
        print(f"  glass: each monitor blurs its own wallpaper; this desk has "
              f"{len(pics)} display(s), {own} with a picture (fit mode {mode})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_liquid_glass(pal) -> None:
    """The cards' liquid glass bends the desktop along its rim, and only there.

    Checked against a made-up desktop whose colour rises one step per column,
    so the colour a card shows says which column it looked at. The middle of a
    card has to show what is straight behind it; near the left edge it has to
    show something further right, near the right edge something further left,
    and red has to look further than blue. The rim has to be lit along the top
    and left and hardly at all across the corner away from the light. A large
    card has to bend in a few milliseconds, since a drag bends it at every
    step - and a PC too slow for that has to show frosted glass while the card
    moves and bend it where it lands.
    """
    import time as _t

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter

    try:
        import numpy as np
    except ImportError:
        print("  liquid glass: no numpy, so cards stay frosted (skipped)")
        return

    from widgets.core import lens
    from widgets.core.board import REGISTRY
    from widgets.core.card import BEND_BUDGET_MS, MARGIN, RADIUS
    from widgets.core.glass import GlassBackdrop

    # A desktop 1200x800, kept at half size like the real clear copy: column u
    # of the copy is u - 150 bright in red, green and blue alike.
    ramp = np.clip(np.arange(600) - 150, 0, 255).astype(np.uint8)
    planes = np.ascontiguousarray(np.broadcast_to(ramp, (3, 400, 600)))
    g = GlassBackdrop()
    g.clear, g.clear_origin, g.generation = planes, (0, 0), 1
    assert g.ready

    sx, sy, w, h = 400, 200, 348, 348
    img = g.bend(sx, sy, w, h, RADIUS)
    f = lens.field(w, h, RADIUS)
    assert (img.width(), img.height()) == (f.cols, f.rows), "bent on the wrong grid"

    def seen(j: int, i: int):
        c = img.pixelColor(j, i)
        return c.red(), c.blue()

    def straight(j: int) -> float:       # the copy's column behind grid point j
        return (sx + j * f.step + f.step / 2.0) / 2.0 - 0.5 - 150

    mid_i, mid_j = f.rows // 2, f.cols // 2
    red, blue = seen(mid_j, mid_i)
    assert abs(red - straight(mid_j)) <= 1 and abs(red - blue) <= 1, (
        f"the middle of the glass is bent: shows {red}/{blue}, behind it is {straight(mid_j):.0f}")
    red, blue = seen(1, mid_i)
    assert red > straight(1) + 8, (
        f"the left rim doesn't look inward: shows {red}, behind it is {straight(1):.0f}")
    assert red > blue, f"red doesn't bend further than blue at the rim ({red} vs {blue})"
    edge_red, _ = seen(f.cols - 2, mid_i)
    assert edge_red < straight(f.cols - 2) - 8, (
        f"the right rim doesn't look inward: shows {edge_red}, behind it is "
        f"{straight(f.cols - 2):.0f}")

    rim = g.rim(w, h, RADIUS, True)
    alpha = lambda x, y: rim.pixelColor(x, y).alpha()
    lit = max(alpha(k, k) for k in range(30))                 # the corner facing the light
    away = max(alpha(w - 1 - k, k) for k in range(30))        # the one away from it
    assert alpha(w // 2, 0) > 30 and alpha(0, h // 2) > 30, "the rim isn't lit along the top and left"
    assert lit > 2 * away and away < 20, (
        f"the rim is as bright across the far corner ({away}) as the near one ({lit})")
    assert alpha(w // 2, h // 2) == 0, "the rim's light reaches into the middle"

    times = []
    for k in range(7):
        started = _t.perf_counter()
        g.bend(sx + k, sy + k, w, h, RADIUS)
        times.append((_t.perf_counter() - started) * 1000.0)
    times.sort()
    # Generous, for GitHub's slower machines and a busy PC; it measures about
    # 4.5 ms on a Ryzen 5 5600G.
    assert times[0] < 30.0, f"bending a large card takes {times[0]:.1f} ms"

    # A card: liquid at rest; bent at every step of a drag on a quick PC, on
    # the coarser drag grid; on a slow one frosted while moving; bent in full
    # where it lands either way.
    card = REGISTRY["clock"](pal, "small", {})
    card.glass = g
    card.move(sx - int(MARGIN), sy - int(MARGIN))
    buf = QImage(card.size(), QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(buf)
    try:
        r = card.card_rect()
        full = lens.field(int(r.width()), int(r.height()), RADIUS).cols
        assert card._paint_liquid(painter, r), "a card at rest isn't liquid"
        assert card._bent[1].width() == full, "a card at rest isn't bent in full"
        card._drag_from = QPoint(10, 10)
        card._drag_ms = 1.0
        card.move(card.x() + 7, card.y())
        assert card._paint_liquid(painter, r), "a quick PC stopped bending during a drag"
        assert card._bent[1].width() < full, "a drag step was bent on the full grid"
        moved_to = card._bent[0]
        card._drag_ms = BEND_BUDGET_MS + 5.0
        card.move(card.x() + 7, card.y())
        assert not card._paint_liquid(painter, r), "a slow PC kept bending during a drag"
        assert card._bent[0] == moved_to, "a slow PC bent a step of the drag anyway"
        card._drag_from = None
        assert card._paint_liquid(painter, r) and card._bent[0] != moved_to, (
            "a slow PC didn't bend the glass where the card was let go")
        assert card._bent[1].width() == full, "where it was let go, it isn't bent in full"
        g.clear = None
        assert not card._paint_liquid(painter, r), "a card bent with no clear copy to bend"
    finally:
        painter.end()
        card.close()
        card.deleteLater()

    print(f"  liquid glass: bends inward along the rim only, red further than blue, "
          f"lit top-left; a large card bends in {times[len(times) // 2]:.1f} ms "
          f"({f.bent_rows.size} of {f.cols * f.rows} points); slow PCs frost while dragging")


def _check_glass_settings(pal) -> None:
    """Settings' Glass style and Blur do what they say.

    Frosted turns liquid glass off: the clear copy is dropped and a card
    shows the blur. Each Blur level softens more than the one before, judged on
    a made-up wallpaper of 8px black and white squares. Changing the Blur keeps
    the old clear copy in use until the new one is built, so the cards never
    flash frosted, and the new one is a new generation, so their kept bends
    are redone. Style and blur survive a save as well.
    """
    import tempfile

    from PySide6.QtGui import QColor, QImage, QPainter

    try:
        import numpy as np
    except ImportError:
        print("  glass settings: no numpy, so glass is always frosted (skipped)")
        return

    from widgets.core import store, winapi
    from widgets.core.board import REGISTRY
    from widgets.core.card import MARGIN
    from widgets.core.displays import Display
    from widgets.core.glass import FROSTS, GlassBackdrop

    tmp = tempfile.mkdtemp(prefix="pcwidgets-blur-")
    try:
        check = QImage(400, 300, QImage.Format_RGB32)
        check.fill(QColor("#000000"))
        p = QPainter(check)
        for y in range(0, 300, 8):
            for x in range(0, 400, 8):
                if (x // 8 + y // 8) % 2:
                    p.fillRect(x, y, 8, 8, QColor("#ffffff"))
        p.end()
        picture = os.path.join(tmp, "check.png")
        check.save(picture)
        screen = Display("A", "A", (0, 0, 400, 300), (0, 0, 400, 300), True)
        last = ([(screen, picture)], winapi.WALL_STRETCH, (0, 0, 400, 300), QColor("#000000"))

        spread = {}
        for level in ("none", "light", "more"):
            planes, _origin = GlassBackdrop.compose_clear(*last, FROSTS[level])
            spread[level] = float(planes[0].astype(np.float32).std())
        assert spread["none"] > spread["light"] > spread["more"], (
            f"the blur levels don't soften in order: {spread}")

        g = GlassBackdrop()
        g._last = last
        g._start_clear(*last)
        assert g.wait_clear() and g.ready, "the clear copy wasn't built"
        before, gen = g.clear, g.generation
        g.set_frost("more")
        assert g.ready and g.clear is before, "changing the blur dropped the glass meanwhile"
        assert g.wait_clear() and g.clear is not before and g.generation > gen, (
            "the new blur's clear copy didn't replace the old one")

        card = REGISTRY["clock"](pal, "small", {})
        card.glass = g
        card.move(40 - int(MARGIN), 40 - int(MARGIN))
        buf = QImage(card.size(), QImage.Format_ARGB32_Premultiplied)
        painter = QPainter(buf)
        try:
            assert card._paint_liquid(painter, card.card_rect()), "a card isn't liquid"
            g.set_liquid(False)
            assert g.clear is None and not g.ready, "frosted kept the clear copy"
            assert not card._paint_liquid(painter, card.card_rect()), "frosted still bent"
            g.set_liquid(True)
            assert g.wait_clear() and card._paint_liquid(painter, card.card_rect()), (
                "back to liquid, the card didn't bend again")
        finally:
            painter.end()
            card.close()
            card.deleteLater()

        path = os.path.join(tmp, "board.json")
        state = store.BoardState(glass_style="frosted", glass_blur="more")
        import json
        from dataclasses import asdict
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["glass_style"] == "frosted" and raw["glass_blur"] == "more"
        fresh = store.BoardState()
        assert (fresh.glass_style, fresh.glass_blur) == ("liquid", "light"), \
            "a new board doesn't start liquid with a light blur"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"  glass settings: frosted drops the clear copy and cards blur; blur levels soften "
          f"in order (spread {spread['none']:.0f} / {spread['light']:.0f} / "
          f"{spread['more']:.0f}); a new blur swaps in without a frosted flash")


def _check_live_wallpaper() -> None:
    """The glass follows a live wallpaper (Wallpaper Engine and the like)
    while one runs, and goes back to the wallpaper files when it quits.

    Stand-ins play the wallpaper layer, its grabs, a full-screen game and the
    desktop's wallpaper file, so this checks the same on every PC whatever its
    desktop runs. A grab lands in both copies, a frame that hasn't changed is
    skipped, a black grab is ignored, nothing is grabbed under a full-screen
    window, Glass style and Blur rebuild a still frame, and the files' glass
    can't overwrite a live frame. The pixel copy itself is checked on a real
    GDI bitmap, and the real desktop is asked what it runs, for the log.
    """
    import ctypes as C
    import ctypes.wintypes as W
    import tempfile
    import threading
    import time

    from PySide6.QtGui import QColor, QImage, QPainter

    from widgets.core import glass as glass_mod
    from widgets.core import livewall, winapi
    from widgets.core.displays import Display
    from widgets.core.glass import GlassBackdrop

    try:
        import numpy  # noqa: F401
        liquid = True
    except ImportError:
        liquid = False

    # The pixel copy: a GDI bitmap of an odd width (rows padded) filled with
    # one colour must read back as that colour, opaque.
    gdi, user = C.WinDLL("gdi32"), C.WinDLL("user32")
    user.GetDC.argtypes, user.GetDC.restype = [W.HWND], W.HDC
    user.ReleaseDC.argtypes = [W.HWND, W.HDC]
    user.FillRect.argtypes = [W.HDC, C.POINTER(W.RECT), W.HBRUSH]
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [W.HDC], W.HDC
    gdi.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]
    gdi.CreateCompatibleBitmap.restype = W.HBITMAP
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [W.HDC, W.HGDIOBJ], W.HGDIOBJ
    gdi.CreateSolidBrush.argtypes, gdi.CreateSolidBrush.restype = [W.COLORREF], W.HBRUSH
    gdi.DeleteObject.argtypes = [W.HGDIOBJ]
    gdi.DeleteDC.argtypes = [W.HDC]
    screen_dc = user.GetDC(None)
    dc = gdi.CreateCompatibleDC(screen_dc)
    bmp = gdi.CreateCompatibleBitmap(screen_dc, 37, 5)
    brush = gdi.CreateSolidBrush(200 | (40 << 8) | (120 << 16))
    try:
        old = gdi.SelectObject(dc, bmp)
        user.FillRect(dc, C.byref(W.RECT(0, 0, 37, 5)), brush)
        gdi.SelectObject(dc, old)
        copied = livewall.read_bitmap(dc, bmp, 37, 5)
    finally:
        gdi.DeleteObject(brush)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(dc)
        user.ReleaseDC(None, screen_dc)
    assert copied is not None, "a GDI bitmap wouldn't read back"
    px = copied.pixelColor(36, 4)
    assert (px.red(), px.green(), px.blue(), px.alpha()) == (200, 40, 120, 255), (
        f"the grab's pixels come back wrong: {px.getRgb()}")

    # The fingerprint's verdicts.
    def frame(rgb, size=(400, 300)) -> QImage:
        img = QImage(size[0], size[1], QImage.Format_RGB32)
        img.fill(QColor(*rgb))
        p = QPainter(img)
        p.fillRect(0, 0, 40, 40, QColor("#ffffff"))     # so it isn't one flat colour
        p.end()
        return img

    red_mark = livewall.fingerprint(frame((220, 30, 40)))
    flat = QImage(400, 300, QImage.Format_RGB32)
    flat.fill(QColor(0, 0, 0))
    assert livewall.blank(livewall.fingerprint(flat)), "a black grab didn't count as failed"
    flat.fill(QColor(220, 30, 40))
    assert livewall.blank(livewall.fingerprint(flat)), "a flat red grab didn't count as failed"
    assert not livewall.blank(red_mark), "a real frame counted as failed"
    assert not livewall.differs(red_mark, livewall.fingerprint(frame((220, 30, 40)))), (
        "the same frame counted as new")
    assert livewall.differs(red_mark, livewall.fingerprint(frame((30, 60, 220)))), (
        "a new frame counted as the same")
    assert livewall.differs(None, red_mark), "a first frame counted as seen"

    # A grab off the virtual screen's corner is placed where it sits.
    part = GlassBackdrop._place(frame((220, 30, 40), (200, 150)), (100, 50),
                                (0, 0, 400, 300), QColor(0, 0, 0))
    assert (part.width(), part.height()) == (400, 300)
    assert part.pixelColor(250, 150).red() > 200 and part.pixelColor(20, 280).red() < 10, (
        "a grab wasn't placed where it sits on the desktop")
    whole = frame((220, 30, 40))
    assert GlassBackdrop._place(whole, (0, 0), (0, 0, 400, 300), QColor(0, 0, 0)) is whole

    tmp = tempfile.mkdtemp(prefix="pcwidgets-live-")
    stand = {"layer": 0, "frame": frame((220, 30, 40)), "full": None, "grabs": 0}

    def grab(layer, keep=False):
        stand["grabs"] += 1
        return (stand["frame"], (0, 0)) if layer == stand["layer"] else None

    saved = (livewall.find_layer, livewall.alive, livewall.grab, winapi.fullscreen_monitor,
             winapi.virtual_screen, winapi.desktop_colour, glass_mod.LIVE_EVERY)
    livewall.find_layer = lambda: stand["layer"]
    livewall.alive = lambda layer: bool(layer) and layer == stand["layer"]
    livewall.grab = grab
    winapi.fullscreen_monitor = lambda: stand["full"]
    winapi.virtual_screen = lambda: (0, 0, 400, 300)
    winapi.desktop_colour = lambda: (0, 0, 0)
    glass_mod.LIVE_EVERY = 0.05

    def until(test, timeout=5.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if test():
                return True
            time.sleep(0.01)
        return test()

    def shows(img, rgb, tol=16) -> bool:
        c = img.pixelColor(img.width() // 2, img.height() // 2)
        return all(abs(a - b) <= tol for a, b in zip((c.red(), c.green(), c.blue()), rgb))

    def bends(g, rgb, tol=16) -> bool:
        planes = g.clear
        if planes is None:
            return False
        h, w = planes.shape[1:]
        return all(abs(int(planes[i, h // 2, w // 2]) - rgb[i]) <= tol for i in range(3))

    def following() -> bool:
        return any(t.name == "glass-live" and t.is_alive() for t in threading.enumerate())

    RED, BLUE, GREEN = (220, 30, 40), (30, 60, 220), (40, 160, 60)
    g = GlassBackdrop()
    g.live_glass = False                    # the look every few seconds; _check_live_glass has the rest
    try:
        green = os.path.join(tmp, "green.png")
        frame(GREEN).save(green)
        files = Display("A", "A", (0, 0, 400, 300), (0, 0, 400, 300), True)
        g._read = lambda: (("files", green), winapi.WALL_STRETCH, [(files, green)])
        assert g.refresh() and g.wait_clear() == liquid and shows(g.image, GREEN), (
            "the files' glass wasn't built")

        assert not g.check_live() and not g.live and not following(), (
            "followed a live wallpaper with none running")

        # One starts: its frame lands in both copies.
        stand["layer"] = 0x5150
        gen = g.generation
        assert not g.check_live() and g.live == 0x5150
        assert until(lambda: g.showing_live and shows(g.image, RED)), (
            "the live wallpaper's frame didn't reach the glass")
        assert g.generation > gen
        if liquid:
            assert until(lambda: bends(g, RED)), "the clear copy isn't the live frame"

        # The same frame again is skipped; a new one is built.
        gen, grabs = g.generation, stand["grabs"]
        assert until(lambda: stand["grabs"] >= grabs + 4), "the live wallpaper isn't grabbed again"
        assert g.generation == gen, "an unchanged frame was built again"
        stand["frame"] = frame(BLUE)
        assert until(lambda: shows(g.image, BLUE)), "a new frame didn't reach the glass"
        if liquid:
            assert bends(g, BLUE), "the clear copy kept the old frame"

        # A black grab is a failed one, and changes nothing.
        gen, grabs = g.generation, stand["grabs"]
        stand["frame"] = flat.copy()
        stand["frame"].fill(QColor(0, 0, 0))
        assert until(lambda: stand["grabs"] >= grabs + 3)
        assert g.generation == gen and shows(g.image, BLUE), "a black grab reached the glass"

        # Under a full-screen game nothing is grabbed, and it catches up after.
        stand["frame"] = frame(RED)
        stand["full"] = (0, 0, 400, 300)
        time.sleep(0.15)                    # one grab may already be under way
        grabs = stand["grabs"]
        time.sleep(0.4)
        assert stand["grabs"] <= grabs + 1, (
            f"grabbed {stand['grabs'] - grabs} times under a full-screen window")
        stand["full"] = None
        assert until(lambda: shows(g.image, RED)), "the glass didn't catch up after the game"

        # The files' glass can't take over while the live frame shows.
        image, clear = g.image, g.clear
        g._signature = None
        assert not g.refresh(), "the files' glass replaced the live frame"
        g._start_clear(*g._last)
        time.sleep(0.3)                     # long enough for a 400x300 build to land
        assert g.image is image and g.clear is clear and shows(g.image, RED), (
            "a build from the files replaced the live frame")

        # Glass style and Blur rebuild a still frame.
        if liquid:
            before, gen = g.clear, g.generation
            g.set_frost("more")
            assert until(lambda: g.clear is not before and g.generation > gen), (
                "a new Blur didn't rebuild the live frame's clear copy")
            g.set_liquid(False)
            assert g.clear is None and not g.ready, "frosted kept the live frame's clear copy"
            g.set_liquid(True)
            assert until(lambda: g.ready and bends(g, RED)), (
                "back to liquid, the live frame wasn't bent again")

        # It quits: the files' glass comes back - blurred and clear copies
        # together, once the clear one is built - and the thread ends.
        stand["layer"] = 0
        assert g.check_live(), "the glass didn't go back to the files"
        assert not g.live and not g.showing_live
        assert g.wait_clear() == liquid and shows(g.image, GREEN), (
            "the files' glass wasn't built again")
        assert not liquid or bends(g, GREEN), "the files' clear copy wasn't built again"
        assert until(lambda: not following()), "the live wallpaper's thread didn't end"
    finally:
        g.stop_live()
        (livewall.find_layer, livewall.alive, livewall.grab, winapi.fullscreen_monitor,
         winapi.virtual_screen, winapi.desktop_colour, glass_mod.LIVE_EVERY) = saved
        shutil.rmtree(tmp, ignore_errors=True)

    # What this PC's desktop runs, for the log.
    layer = livewall.find_layer()
    if not layer:
        here = "none running here"
    else:
        started = time.perf_counter()
        got = livewall.grab(layer)
        took = (time.perf_counter() - started) * 1000
        here = ("one running here, but its grab failed" if got is None else
                f"one running here, grabbed {got[0].width()}x{got[0].height()} in {took:.0f} ms"
                f"{' (blank)' if livewall.blank(livewall.fingerprint(got[0])) else ''}")
    print(f"  live wallpaper: followed into both copies, unchanged and black frames skipped, "
          f"nothing grabbed under a full-screen window, back to the files when it quits; "
          f"{here}")


def _check_smooth_glass(pal) -> None:
    """Glass changes reach only the cards they are behind, and fade in.

    A live wallpaper moving behind one card rebuilds the glass behind that
    card alone - the clear copy patched there, coming out as building it whole
    would - and the other card keeps its bend and doesn't fade. Motion behind
    no card builds nothing. A card whose glass changes fades from the old
    slice to the new; one being moved switches. A new wallpaper lands whole,
    the old glass kept meanwhile, so liquid cards never go frosted in between.
    And a live wallpaper's first frame is built before anything is drawn.
    """
    import tempfile
    import threading

    from PySide6.QtGui import QColor, QImage, QPainter

    try:
        import numpy as np
    except ImportError:
        print("  smooth glass: no numpy, so no liquid glass to patch (skipped)")
        return

    from widgets.core import glass as glass_mod
    from widgets.core import livewall, winapi
    from widgets.core.board import REGISTRY
    from widgets.core.card import MARGIN
    from widgets.core.displays import Display
    from widgets.core.glass import FROSTS, GlassBackdrop

    W, H = 800, 400
    LAYER = 0x5150
    stand = {"frame": None}
    saved = (livewall.find_layer, livewall.alive, livewall.grab, winapi.fullscreen_monitor,
             winapi.virtual_screen, winapi.desktop_colour, glass_mod.LIVE_EVERY)
    livewall.find_layer = lambda: LAYER
    livewall.alive = lambda layer: layer == LAYER
    livewall.grab = lambda layer, keep=False: (stand["frame"], (0, 0))
    winapi.fullscreen_monitor = lambda: None
    winapi.virtual_screen = lambda: (0, 0, W, H)
    winapi.desktop_colour = lambda: (0, 0, 0)
    glass_mod.LIVE_EVERY = 0.05

    RED, GREEN, BLUE, GREY = (220, 30, 40), (40, 170, 60), (30, 60, 220), (90, 90, 90)
    m = int(MARGIN)
    card_a = REGISTRY["clock"](pal, "small", {})
    card_b = REGISTRY["clock"](pal, "small", {})
    card_a.move(60 - m, 60 - m)
    card_b.move(500 - m, 100 - m)
    ra, rb = card_a.card_rect(), card_b.card_rect()
    A = (60, 60, int(ra.width()), int(ra.height()))
    B = (500, 100, int(rb.width()), int(rb.height()))

    def scene(a, b, extra=False) -> QImage:
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(QColor(*GREY))
        p = QPainter(img)
        p.fillRect(0, 0, 40, 40, QColor("#ffffff"))        # so it isn't one flat colour
        for (x, y, w, h), rgb in ((A, a), (B, b)):
            p.fillRect(x - 30, y - 30, w + 60, h + 60, QColor(*rgb))
        if extra:
            p.fillRect(300, 250, 120, 80, QColor("#ffffff"))   # behind neither card
        p.end()
        return img

    def colour(img: QImage, x: int, y: int):
        c = img.pixelColor(x, y)
        return c.red(), c.green(), c.blue()

    def near(rgb, want, tol=20) -> bool:
        return all(abs(a - b) <= tol for a, b in zip(rgb, want))

    def glass_of(card) -> tuple:
        """What the card's glass alone shows at its middle, fade and all."""
        buf = QImage(card.size(), QImage.Format_ARGB32_Premultiplied)
        buf.fill(QColor(0, 0, 0, 0))
        p = QPainter(buf)
        card._paint_glass(p, card.card_rect())
        p.end()
        return colour(buf, card.width() // 2, card.height() // 2)

    frost = FROSTS["light"]
    tmp = tempfile.mkdtemp(prefix="pcwidgets-smooth-")
    g = GlassBackdrop()
    g.live_glass = False                    # Live glass off: the updates that fade
    try:
        # A live wallpaper behind both cards; then it moves behind A only.
        g.regions = (A, B)
        job, _wake = g._prepare_live(LAYER)
        stand["frame"] = scene(RED, BLUE)
        assert g._live_once(LAYER, job) == (True, False), "the first frame wasn't built"
        for card in (card_a, card_b):
            card.glass = g
        first_a, first_b = glass_of(card_a), glass_of(card_b)
        assert near(first_a, RED) and near(first_b, BLUE), (first_a, first_b)
        bent_b, gen = card_b._bent[1], g.generation
        before = g.clear.copy()

        stand["frame"] = scene(GREEN, BLUE)
        assert g._live_once(LAYER, job) == (True, False), "a change behind a card wasn't built"
        assert g.changed_since(gen, A) and not g.changed_since(gen, B), (
            "the change behind one card reached the other")
        whole = GlassBackdrop._planes(stand["frame"], (0, 0, W, H), frost)[0]
        ax, ay, aw, ah = (v // 2 for v in A)
        bx, by, bw, bh = (v // 2 for v in B)
        patched = int(np.abs(g.clear[:, ay:ay + ah, ax:ax + aw].astype(int)
                             - whole[:, ay:ay + ah, ax:ax + aw]).max())
        assert patched <= 1, f"the patched glass differs from a whole build by {patched}"
        assert np.array_equal(g.clear[:, by:by + bh, bx:bx + bw], before[:, by:by + bh, bx:bx + bw]), (
            "the glass behind the card that didn't change was rebuilt")
        assert card_a.glass_stale() and not card_b.glass_stale(), (
            "the board would repaint the wrong card")

        # A fades from red to green; B keeps its bend and doesn't fade.
        start_a = glass_of(card_a)
        assert card_a._pane_from is not None and card_a._glass_fade.active, "A didn't start a fade"
        assert near(start_a, RED), f"A's fade didn't start from the old glass: {start_a}"
        card_a._glass_fade._elapsed = card_a._glass_fade._duration / 2
        card_a._glass_fade._advance()
        mid_a = glass_of(card_a)
        assert mid_a[1] > RED[1] + 30 and mid_a[0] < RED[0] - 30, f"A's fade isn't halfway: {mid_a}"
        card_a._glass_fade.finish()
        assert near(glass_of(card_a), GREEN), "A's fade didn't end on the new glass"
        glass_of(card_b)
        assert card_b._bent[1] is bent_b and card_b._pane_from is None, (
            "B was bent again or faded with nothing new behind it")

        # Motion behind neither card builds nothing; the same frame neither.
        gen = g.generation
        stand["frame"] = scene(GREEN, BLUE, extra=True)
        assert g._live_once(LAYER, job) == (False, False) and g.generation == gen, (
            "motion behind no card rebuilt the glass")
        assert g._live_once(LAYER, job) == (False, False), "the same frame was built again"

        # A card being moved switches rather than fading.
        g._wake_live()
        stand["frame"] = scene(BLUE, BLUE)
        g._live_once(LAYER, job)
        card_a.move(card_a.x() + 6, card_a.y())
        glass_of(card_a)
        assert card_a._pane_from is None, "a moved card faded from glass that was somewhere else"
        g.stop_live()

        # A new wallpaper lands whole, blurred and clear together: the old
        # glass stays meanwhile, still liquid, never frosted in between.
        files = Display("A", "A", (0, 0, W, H), (0, 0, W, H), True)
        pictures = {}
        for name, rgb in (("one", RED), ("two", GREEN)):
            pictures[name] = os.path.join(tmp, name + ".png")
            scene(rgb, rgb).save(pictures[name])
        f = GlassBackdrop()
        f._read = lambda: (("files", "one"), winapi.WALL_STRETCH, [(files, pictures["one"])])
        assert f.refresh() and f.wait_clear() and near(colour(f.image, 150, 150), RED)
        gate = threading.Event()
        build = GlassBackdrop.compose_clear

        def held(*args):
            gate.wait(10)
            return build(*args)

        f.compose_clear = held
        old_image, old_clear = f.image, f.clear
        f._read = lambda: (("files", "two"), winapi.WALL_STRETCH, [(files, pictures["two"])])
        assert f.refresh(), "a new wallpaper wasn't noticed"
        assert f.image is old_image and f.clear is old_clear and f.ready, (
            "the old glass didn't stay while the new clear copy was built")
        gate.set()
        assert f.wait_clear() and near(colour(f.image, 150, 150), GREEN), (
            "the new wallpaper's glass didn't land")
        assert f.clear is not old_clear

        # The first frame of a live wallpaper is there as soon as asked for.
        s = GlassBackdrop()
        s.live_glass = False
        stand["frame"] = scene(BLUE, BLUE)
        assert not s.check_live(now=True)
        assert s.showing_live and near(colour(s.image, 150, 150), BLUE), (
            "the first live frame wasn't built straight away")
        assert not s.refresh(), "the files' glass was built over the first live frame"
        s.stop_live()
    finally:
        g.stop_live()
        (livewall.find_layer, livewall.alive, livewall.grab, winapi.fullscreen_monitor,
         winapi.virtual_screen, winapi.desktop_colour, glass_mod.LIVE_EVERY) = saved
        for card in (card_a, card_b):
            card.close()
            card.deleteLater()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"  smooth glass: a change behind one card rebuilds only its glass (patch within "
          f"{patched} of a whole build) and only it fades; motion behind no card builds nothing; "
          f"a new wallpaper lands whole with no frosted gap; the first live frame is built at once")


def _check_live_glass(pal) -> None:
    """Live glass follows a live wallpaper frame by frame, and the cards stay
    in screenshots: it reads back patches of the wallpaper layer behind them,
    and no window is ever left out of captures.

    Patches of a GDI bitmap read back clipped and right. A frame that moves
    behind one card patches the glass there and bends that card's pane ahead
    on the drag grid; the card shows it at once, unfaded, and the other card
    is left alone. Once the wallpaper rests, the coarse bend is bent in full
    and fades in. A card hidden behind a window is skipped, and catches up
    when it's back in view. Frosted cards get live slices.
    """
    import ctypes as C
    import ctypes.wintypes as W

    from PySide6.QtGui import QColor, QImage, QPainter

    try:
        import numpy  # noqa: F401
    except ImportError:
        print("  live glass: no numpy, so no liquid glass to follow (skipped)")
        return

    import time as _t

    from PySide6.QtCore import QRect, Qt

    from widgets.core import glass as glass_mod
    from widgets.core import lens, livewall, winapi
    from widgets.core.board import REGISTRY
    from widgets.core.card import DRAG_STEP, MARGIN, RADIUS, SHADOW_ALPHA, SHADOW_LAYERS, squircle
    from widgets.core.glass import GlassBackdrop

    # Patches of a GDI bitmap, red on the left and blue on the right, read
    # back as the live glass reads them: clipped to it, one off it skipped.
    gdi, user = C.WinDLL("gdi32"), C.WinDLL("user32")
    user.GetDC.argtypes, user.GetDC.restype = [W.HWND], W.HDC
    user.ReleaseDC.argtypes = [W.HWND, W.HDC]
    user.FillRect.argtypes = [W.HDC, C.POINTER(W.RECT), W.HBRUSH]
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [W.HDC], W.HDC
    gdi.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]
    gdi.CreateCompatibleBitmap.restype = W.HBITMAP
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [W.HDC, W.HGDIOBJ], W.HGDIOBJ
    gdi.CreateSolidBrush.argtypes, gdi.CreateSolidBrush.restype = [W.COLORREF], W.HBRUSH
    gdi.DeleteObject.argtypes = [W.HGDIOBJ]
    gdi.DeleteDC.argtypes = [W.HDC]
    screen_dc = user.GetDC(None)
    dc = gdi.CreateCompatibleDC(screen_dc)
    bmp = gdi.CreateCompatibleBitmap(screen_dc, 64, 40)
    brushes = [gdi.CreateSolidBrush(200 | (40 << 8) | (40 << 16)),
               gdi.CreateSolidBrush(30 | (60 << 8) | (210 << 16))]
    try:
        old = gdi.SelectObject(dc, bmp)
        user.FillRect(dc, C.byref(W.RECT(0, 0, 32, 40)), brushes[0])
        user.FillRect(dc, C.byref(W.RECT(32, 0, 64, 40)), brushes[1])
        parts = livewall.read_parts(dc, (100, 50, 64, 40),
                                    [(100, 50, 20, 20), (150, 70, 30, 30), (500, 500, 10, 10)])
        gdi.SelectObject(dc, old)
    finally:
        for b in brushes:
            gdi.DeleteObject(b)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(dc)
        user.ReleaseDC(None, screen_dc)
    assert parts[2] is None, "a patch off the layer was read back"
    (r0, i0), (r1, i1) = parts[0], parts[1]
    assert r0 == (100, 50, 20, 20) and r1 == (150, 70, 14, 20), f"patches clipped wrong: {r0}, {r1}"
    c0, c1 = i0.pixelColor(10, 10), i1.pixelColor(7, 10)
    assert (c0.red(), c0.green(), c0.blue()) == (200, 40, 40) and (
        c1.red(), c1.green(), c1.blue()) == (30, 60, 210), "patches read back the wrong pixels"

    W_, H_, LAYER = 800, 400, 0x5150
    stand = {"frame": None, "hidden": set()}

    def grab_parts(layer, rects, keep=False):
        out = []
        for x, y, w, h in rects:
            x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W_), min(y + h, H_)
            out.append(None if x1 <= x0 or y1 <= y0 else
                       ((x0, y0, x1 - x0, y1 - y0), stand["frame"].copy(x0, y0, x1 - x0, y1 - y0)))
        return out, (0, 0)

    saved = (livewall.grab, livewall.grab_parts, winapi.fullscreen_monitor,
             winapi.virtual_screen, winapi.desktop_colour, glass_mod._hidden)
    livewall.grab = lambda layer, keep=False: (stand["frame"], (0, 0))
    livewall.grab_parts = grab_parts
    winapi.fullscreen_monitor = lambda: None
    winapi.virtual_screen = lambda: (0, 0, W_, H_)
    winapi.desktop_colour = lambda: (0, 0, 0)
    glass_mod._hidden = lambda info: set(stand["hidden"])

    RED, GREEN, BLUE, GREY = (220, 30, 40), (40, 170, 60), (30, 60, 220), (90, 90, 90)
    m = int(MARGIN)
    card_a = REGISTRY["clock"](pal, "small", {})
    card_b = REGISTRY["clock"](pal, "small", {})
    card_a.move(60 - m, 60 - m)
    card_b.move(500 - m, 100 - m)
    A = (60, 60, int(card_a.card_rect().width()), int(card_a.card_rect().height()))
    B = (500, 100, int(card_b.card_rect().width()), int(card_b.card_rect().height()))
    key_a = A + (float(RADIUS),)

    def scene(a, b) -> QImage:
        img = QImage(W_, H_, QImage.Format_RGB32)
        img.fill(QColor(*GREY))
        p = QPainter(img)
        p.fillRect(0, 0, 40, 40, QColor("#ffffff"))
        for (x, y, w, h), rgb in ((A, a), (B, b)):
            p.fillRect(x - 30, y - 30, w + 60, h + 60, QColor(*rgb))
        p.end()
        return img

    def glass_of(card) -> tuple:
        buf = QImage(card.size(), QImage.Format_ARGB32_Premultiplied)
        buf.fill(QColor(0, 0, 0, 0))
        p = QPainter(buf)
        card._paint_glass(p, card.card_rect())
        p.end()
        c = buf.pixelColor(card.width() // 2, card.height() // 2)
        return c.red(), c.green(), c.blue()

    def near(rgb, want, tol=20) -> bool:
        return all(abs(a - b) <= tol for a, b in zip(rgb, want))

    g = GlassBackdrop()
    try:
        assert g.live_glass, "live glass isn't on by default"
        g.regions = (A, B)
        g.region_info = {A: (0, RADIUS), B: (0, RADIUS)}
        job, _wake = g._prepare_live(LAYER)
        stand["frame"] = scene(RED, BLUE)
        assert g._live_once(LAYER, job) == (True, False), "the first frame wasn't built"
        for card in (card_a, card_b):
            card.glass = g
        assert near(glass_of(card_a), RED) and near(glass_of(card_b), BLUE)
        bent_b, gen = card_b._bent[1], g.generation

        # A frame moves behind A: straight in, bent ahead, B left alone.
        stand["frame"] = scene(GREEN, BLUE)
        assert g._live_frame(LAYER, job) == (True, False), "a frame behind a card wasn't built"
        assert g.changed_since(gen, A) and not g.changed_since(gen, B), "the frame reached B"
        assert not g.fade_since(gen, A), "a live frame asks to be faded into"
        ahead = g._ahead.get(key_a)
        assert ahead is not None and ahead.width() == lens.field(A[2], A[3], RADIUS, DRAG_STEP).cols, (
            "A's pane wasn't bent ahead on the drag grid")
        shown = glass_of(card_a)
        assert near(shown, GREEN) and card_a._pane_from is None, f"A faded into its frame: {shown}"
        assert card_a._bent[1] is ahead, "A bent its own pane instead of taking the one bent ahead"
        glass_of(card_b)
        assert card_b._bent[1] is bent_b, "B was bent again with nothing new behind it"

        # It rests: the coarse bend is bent in full, and that fades in.
        gen = g.generation
        assert g._live_frame(LAYER, job) == (False, False)
        sharp = g._ahead.get(key_a)
        assert sharp is not None and sharp.width() == lens.field(A[2], A[3], RADIUS).cols, (
            "resting, A's pane wasn't bent in full")
        assert g.fade_since(gen, A), "the full bend doesn't fade in"
        glass_of(card_a)
        assert card_a._pane_from is not None and card_a._bent[1] is sharp
        card_a._glass_fade.finish()

        # Hidden behind a window: skipped; back in view, it catches up.
        stand["hidden"], stand["frame"], gen = {A}, scene(BLUE, BLUE), g.generation
        g._hidden_kept = None               # read the windows again, not in a quarter second
        assert g._live_frame(LAYER, job) == (False, False) and g.generation == gen, (
            "the glass behind a hidden card was built")
        stand["hidden"] = set()
        g._hidden_kept = None
        assert g._live_frame(LAYER, job) == (True, False) and g.changed_since(gen, A), (
            "a card back in view didn't catch up")
        assert near(glass_of(card_a), BLUE)

        # Frosted: live slices for the cards.
        g.set_liquid(False)
        stand["frame"] = scene(BLUE, GREEN)
        assert g._live_frame(LAYER, job) == (True, False)
        assert g.frost_ahead(*B) is not None, "frosted glass got no live slice"
        shown = glass_of(card_b)
        assert card_b._pane[0][2] == "live" and shown[1] > shown[0] + 40 and shown[1] > shown[2] + 40, (
            f"the frosted card doesn't show its live slice: {shown}")

        # Windows' capture: frames read the same way, the first one whole.
        class Frame:
            def close(self):
                pass

        class Capture:
            def __init__(self):
                self.frames, self.reads, self.wholes = [], 0, 0

            def latest(self):
                return self.frames.pop(0) if self.frames else None

            def origin(self):
                return (0, 0)

            def read(self, frame, rects, at=None):
                self.reads += 1
                return grab_parts(LAYER, rects)[0]

            def whole(self, frame):
                self.wholes += 1
                return stand["frame"].copy()

        cap, f = Capture(), GlassBackdrop()
        f.regions, f.region_info = (A, B), {A: (0, RADIUS), B: (0, RADIUS)}
        fjob, _ = f._prepare_live(LAYER)
        assert f._live_capture(cap, LAYER, fjob, 0) == (False, False, False), (
            "built from Windows' capture with no frame in it")
        stand["frame"] = scene(RED, BLUE)
        cap.frames.append(Frame())
        assert f._live_capture(cap, LAYER, fjob, 0)[0] and cap.wholes == 1, (
            "the first frame from Windows' capture wasn't taken whole")
        gen = f.generation
        stand["frame"] = scene(GREEN, BLUE)
        cap.frames.append(Frame())
        assert f._live_capture(cap, LAYER, fjob, 0) == (True, False, True) and cap.reads == 1
        assert f.changed_since(gen, A) and not f.changed_since(gen, B), (
            "a frame from Windows' capture reached the card nothing moved behind")
        f.stop_live()

        # Its change test reads Windows' pixels and PrintWindow's alike.
        frame32 = scene(GREEN, BLUE)
        area = QRect(50, 50, 200, 150)
        assert glass_mod._mark_of(frame32, area) == glass_mod._mark_of(
            frame32.convertToFormat(QImage.Format_BGR888), area), (
            "the change test reads the two captures differently")

        # Repaints: the shadow drawn once and kept, as drawn directly; the
        # weather icons' curves built once.
        r = card_a.card_rect()
        kept = card_a._shadow(r, 1.0)
        assert card_a._shadow(r, 1.0) is kept, "the shadow is drawn again at every repaint"
        direct = QImage(card_a.size(), QImage.Format_ARGB32_Premultiplied)
        direct.fill(QColor(0, 0, 0, 0))
        p = QPainter(direct)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        for i in range(SHADOW_LAYERS, 0, -1):
            grow = i * 1.7
            p.setBrush(QColor(0, 0, 0, max(2, int(SHADOW_ALPHA / (i * 0.85)))))
            p.drawPath(squircle(r.adjusted(-grow, -grow + 2.0, grow, grow + 3.0), RADIUS + grow))
        p.end()
        laid = QImage(card_a.size(), QImage.Format_ARGB32_Premultiplied)
        laid.fill(QColor(0, 0, 0, 0))
        p = QPainter(laid)
        p.drawImage(0, 0, kept)
        p.end()
        assert laid == direct, "the kept shadow isn't the shadow"
        from widgets.tiles import weather as weather_mod
        assert weather_mod.icon_paths(weather_mod.PARTLY) is weather_mod.icon_paths(
            weather_mod.PARTLY), "the weather icons' curves are built at every repaint"

        # And nothing was left out of captures: the cards stay in screenshots.
        affinity = W.DWORD()
        for card in (card_a, card_b):
            user.GetWindowDisplayAffinity(W.HWND(int(card.winId())), C.byref(affinity))
            assert affinity.value == 0, "a card was left out of screen captures"
    finally:
        g.stop_live()
        (livewall.grab, livewall.grab_parts, winapi.fullscreen_monitor,
         winapi.virtual_screen, winapi.desktop_colour, glass_mod._hidden) = saved
        for card in (card_a, card_b):
            card.close()
            card.deleteLater()
    # Windows' capture on this PC's desktop, for the log: it's there, or why not.
    here = "no live wallpaper here to capture"
    layer = livewall.find_layer()
    if layer:
        import threading as _th
        from widgets.core import wgc
        seen = {}

        def look():
            wgc.join_apartment()
            try:
                capture = wgc.Capture(wgc.target(layer))
                end, frames = _t.perf_counter() + 1.0, 0
                while _t.perf_counter() < end:
                    got = capture.latest()
                    if got is None:
                        _t.sleep(0.003)
                        continue
                    frames += 1
                    got.close()
                capture.close()
                seen["text"] = f"Windows' capture of the desktop: {frames} frames in 1 s, borderless"
            except Exception as e:
                seen["text"] = f"Windows' capture unavailable here ({e}); PrintWindow instead"
            finally:
                wgc.leave_apartment()

        worker = _th.Thread(target=look)
        worker.start()
        worker.join(10)
        here = seen.get("text", "Windows' capture didn't answer")
    print("  live glass: patches read back clipped and right; a frame behind one card goes "
          "straight in, bent ahead on the drag grid, the other card untouched; at rest bent in "
          "full and faded in; hidden cards skipped; frosted cards get live slices; Windows' "
          "capture's frames read alike; shadows and weather icons kept; cards stay in screen "
          f"captures; {here}")


def _check_gpu_glass(pal) -> None:
    """Live glass bent on the graphics card (gpuglass.py) is the CPU's glass,
    and keeps up with a wallpaper drawing sixty frames a second.

    A card's pane bent on the graphics card matches lens.bend's within a level
    or two, at three sizes and frosts; six cards bent together - one frost for
    all of them - match each bent alone. The capture is asked for frames at
    the wallpaper's own pace (_Pace): on a simulated clock nearly every frame
    of a 60 and a 26 frames-a-second wallpaper is taken within a couple of
    milliseconds, asking a few times a frame. Short waits keep to the
    millisecond, where Event.wait rounds them up to 15.6 ms. And while the
    graphics card bends the cards, a whole look along the way is built on a
    thread of its own: that frame's panes come at once, and are kept.
    """
    try:
        import numpy as np
    except ImportError:
        print("  gpu glass: no numpy, so no liquid glass to bend (skipped)")
        return
    import ctypes as C
    import math
    import random
    import threading
    import time as _t
    import types

    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient

    from widgets.core import glass as glass_mod
    from widgets.core import lens, wgc, winapi
    from widgets.core.card import RADIUS
    from widgets.core.glass import GlassBackdrop

    def pixels(img: QImage):
        rows = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * img.height())
        return rows.reshape(img.height(), -1)[:, :img.width() * 4].astype(np.int16)

    # The graphics card's glass against the CPU's, from one busy picture:
    # gradients and stripes, so a wrong pull shows.
    W_, H_ = 800, 480
    pic = QImage(W_, H_, QImage.Format_RGB32)
    p = QPainter(pic)
    grad = QLinearGradient(0, 0, W_, H_)
    grad.setColorAt(0, QColor("#ff3060"))
    grad.setColorAt(1, QColor("#2050ff"))
    p.fillRect(pic.rect(), grad)
    for x in range(0, W_, 23):
        p.fillRect(x, 0, 6, H_, QColor(255, 255, 255, 90))
    glow = QRadialGradient(400, 240, 200)
    glow.setColorAt(0, QColor(30, 200, 90, 220))
    glow.setColorAt(1, QColor(30, 200, 90, 0))
    p.fillRect(pic.rect(), glow)
    p.end()

    def patch_of(card):
        px, py, pw, ph = glass_mod._aligned(card, (0, 0, W_, H_), glass_mod.PATCH_MARGIN)
        px, py = max(0, px), max(0, py)
        return (px, py, min(pw, W_ - px), min(ph, H_ - py))

    gpu_note = "no Direct3D 11 here, so the CPU bends (skipped)"
    device, context, level = C.c_void_p(), C.c_void_p(), C.c_int()
    hr, kind = -1, ""
    if wgc._d3d11 is not None:
        for driver, kind in ((1, "the graphics card"), (5, "WARP")):   # hardware, then software
            hr = wgc._d3d11.D3D11CreateDevice(None, driver, None, 0x20, None, 0, 7, C.byref(device),
                                              C.byref(level), C.byref(context))
            if hr >= 0:
                break
    if hr >= 0:
        from widgets.core import gpuglass
        gpu = gpuglass.GpuGlass(types.SimpleNamespace(_device=device, _context=context))
        data = np.frombuffer(pic.constBits(), np.uint8, pic.bytesPerLine() * H_).copy()
        tex = gpu._texture(W_, H_, 87, 0x8, data, pic.bytesPerLine())
        try:
            worst = 0
            for card, frost in (((200, 120, 348, 168), (1, 3)), ((420, 200, 168, 168), (2, 4)),
                                ((100, 60, 348, 348), (0, 0))):
                patch = patch_of(card)
                px, py, pw, ph = patch
                on_gpu = gpu.pane(tex, patch, card, RADIUS, frost)
                planes = GlassBackdrop._planes(pic.copy(px, py, pw, ph), (0, 0, pw, ph), frost)[0]
                on_cpu = lens.bend(planes, 2, (px, py), *card, RADIUS, lens.STEP)
                assert on_gpu.size() == on_cpu.size(), "the graphics card's pane is the wrong size"
                d = np.abs(pixels(on_gpu) - pixels(on_cpu))
                assert d.max() <= 2 and d.mean() < 0.6, (
                    f"the graphics card's glass isn't the CPU's: {card}, frost {frost}: "
                    f"{d.max()} levels apart, {d.mean():.2f} on the whole")
                worst = max(worst, int(d.max()))
            cards = [(40, 40, 348, 168), (420, 40, 348, 168), (40, 260, 168, 168),
                     (260, 260, 168, 168), (470, 250, 168, 168), (600, 300, 168, 168)]
            jobs = [(patch_of(c), c, float(RADIUS)) for c in cards]
            for frost in ((1, 3), (2, 4)):
                together = gpu.panes(tex, jobs, frost)
                for job, img in zip(jobs, together):
                    alone = gpu.pane(tex, job[0], job[1], job[2], frost)
                    assert np.abs(pixels(img) - pixels(alone)).max() <= 1, (
                        "cards bent together came out unlike each bent alone")
            n, started = 100, _t.perf_counter()
            for _ in range(n):
                gpu.panes(tex, jobs, (1, 3))
            per = 1000 * (_t.perf_counter() - started) / n
            levels = "level" if worst == 1 else "levels"
            gpu_note = (f"bent on {kind} within {worst} {levels} of the CPU, six cards together "
                        f"as each alone, six a frame in {per:.2f} ms")
        finally:
            wgc._release(tex)
            gpu.close()
            wgc._release(context)
            wgc._release(device)

    # The pace: frames a wallpaper draws at its own rate, shown at a 60 Hz
    # screen's next refresh and handed over just after, taken by the loop.
    def paced(fps: float, seconds: float = 6.0):
        rnd, vsync = random.Random(7), 1 / 60
        shown = sorted({math.ceil(i / fps / vsync - 1e-6) for i in range(int(seconds * fps))})
        arrivals = [s * vsync + rnd.uniform(0.0005, 0.0015) for s in shown]
        pace, now, k, asks, taken, late = glass_mod._Pace(), 0.0, 0, 0, 0, []
        while now < seconds:
            asks += 1
            newest = None
            while k < len(arrivals) and arrivals[k] <= now:
                newest, k = k, k + 1
            if newest is not None:
                taken += 1
                late.append(now - arrivals[newest])
                pace.took(shown[newest] * vsync + 0.003, now)
                now += 0.0015                               # building it
                now += max(pace.wait(now), 0.0015)
            else:
                now += pace.wait(now)
            now += rnd.uniform(0, 0.0004)                   # a wait runs over a little
        late.sort()
        return taken / len(arrivals), late[len(late) // 2], asks / len(arrivals)

    for fps, slack in ((60, 0.002), (26, 0.004)):
        share, late, asks = paced(fps)
        assert share >= 0.98 and late < slack and asks <= 6, (
            f"a {fps} fps wallpaper's frames aren't kept up with: {share:.0%} taken, "
            f"{1000 * late:.1f} ms late, asked {asks:.1f} times a frame")
    share60, late60, asks60 = paced(60)

    wake = threading.Event()
    naps = []
    for _ in range(5):
        started = _t.perf_counter()
        glass_mod._nap(wake, 0.004)
        naps.append(_t.perf_counter() - started)
    naps.sort()
    assert naps[2] < 0.010, f"a 4 ms wait took {1000 * naps[2]:.1f} ms"
    threading.Timer(0.01, wake.set).start()
    started = _t.perf_counter()
    glass_mod._nap(wake, 0.09)
    assert _t.perf_counter() - started < 0.06, "a wait went on after it was woken"

    # A whole look along the way, beside the frames the graphics card bends.
    W2, H2, LAYER = 640, 360, 0x5151
    A, B = (60, 60, 160, 160), (380, 120, 160, 160)

    def scene(rgb) -> QImage:
        # Not one flat colour, which reads as a grab that failed.
        img = QImage(W2, H2, QImage.Format_RGB32)
        img.fill(QColor(90, 90, 90))
        p = QPainter(img)
        p.fillRect(0, 0, 40, 40, QColor("#ffffff"))
        for x, y, w, h in (A, B):
            p.fillRect(x - 30, y - 30, w + 60, h + 60, QColor(*rgb))
        p.end()
        return img

    stand = {"frame": scene((200, 40, 40))}

    class Frame:
        size, texture, time = (W2, H2), None, None

        def close(self):
            pass

    class Capture:
        def __init__(self):
            self.frames, self.wholes = [], 0

        def latest(self):
            return self.frames.pop(0) if self.frames else None

        def origin(self):
            return (0, 0)

        def whole(self, frame):
            self.wholes += 1
            return stand["frame"].copy()

    class Gpu:
        def __init__(self):
            self.frames = 0

        def panes(self, texture, cards, frost, step=lens.STEP):
            self.frames += 1
            out = []
            for _patch, card, radius in cards:
                f = lens.field(card[2], card[3], radius, step)
                img = QImage(f.cols, f.rows, QImage.Format_RGB32)
                img.fill(QColor(10, 20, 40 + 20 * self.frames))
                out.append(img)
            return out

    where = []
    real_once = GlassBackdrop._live_once

    def once(self, *a, **kw):
        where.append(threading.current_thread().name)
        return real_once(self, *a, **kw)

    saved = (winapi.fullscreen_monitor, winapi.virtual_screen, winapi.desktop_colour,
             glass_mod._hidden, GlassBackdrop._live_once)
    winapi.fullscreen_monitor = lambda: None
    winapi.virtual_screen = lambda: (0, 0, W2, H2)
    winapi.desktop_colour = lambda: (0, 0, 0)
    glass_mod._hidden = lambda info: set()
    GlassBackdrop._live_once = once
    g = GlassBackdrop()
    try:
        g.regions, g.region_info = (A, B), {A: (0, RADIUS), B: (0, RADIUS)}
        job, _ = g._prepare_live(LAYER)
        cap, fake = Capture(), Gpu()
        cap.frames.append(Frame())
        g._live_capture(cap, LAYER, job, 0, fake)
        here = threading.current_thread().name
        assert cap.wholes == 1 and where == [here] and g.showing_live, (
            "live glass's first frame wasn't taken whole, at once")
        g._live_moving, g._live_look_at = True, 0.0     # moving, and a whole look due
        stand["frame"] = scene((40, 170, 60))
        cap.frames.append(Frame())
        g._live_capture(cap, LAYER, job, 1, fake)
        assert fake.frames == 1, "the cards' frame waited on a whole look"
        g._whole_wait()
        assert cap.wholes == 2 and where[-1] == "glass-whole", (
            "a whole look along the way was built in the frames' way")
        assert all(r + (float(RADIUS),) in g._ahead for r in (A, B)), (
            "a whole look along the way dropped the panes the graphics card bent")
    finally:
        g.stop_live()
        (winapi.fullscreen_monitor, winapi.virtual_screen, winapi.desktop_colour,
         glass_mod._hidden, GlassBackdrop._live_once) = saved
    print(f"  gpu glass: {gpu_note}; a 60 fps wallpaper's frames taken {share60:.0%}, "
          f"{1000 * late60:.1f} ms after they come, asking {asks60:.1f} times a frame; short waits "
          f"to the millisecond; a whole look along the way built beside the frames")


def _check_glass_layer(pal) -> None:
    """Live glass reaches every card smoothly, and a repaint for new glass
    alone is cheap and comes out the same.

    The change check hands on a pane changed by three levels anywhere, or a
    tenth of one on the whole, but not by a level or two. Ten levels used to
    hold a small card's glass back to a third of its frames, in steps. A card
    repainted for new glass alone (glass_update) lays the layer kept from its
    last paint - tint, rim and content - on the new glass, pixel for pixel
    what drawing it again gives, while a repaint asked for any other way
    (update) draws it again. That layer's type is greyscale, without
    ClearType's coloured edges, and a card a picture covers has no glass to
    go stale.
    """
    try:
        import numpy as np
    except ImportError:
        print("  glass layer: no numpy, so no liquid glass (skipped)")
        return
    import os as _os
    import tempfile
    import time as _t

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from widgets.core import glass as glass_mod
    from widgets.core import lens, samples
    from widgets.core.board import REGISTRY
    from widgets.core.card import MARGIN, RADIUS
    from widgets.core.glass import GlassBackdrop

    # The change check: flicker stays, any real change goes.
    base = QImage(40, 30, QImage.Format_RGB32)
    base.fill(QColor(100, 100, 100))

    def nudged(levels: int, everywhere: bool = False) -> QImage:
        img = base.copy()
        for x in (range(40) if everywhere else (5,)):
            for y in (range(30) if everywhere else (5,)):
                img.setPixelColor(x, y, QColor(100 + levels, 100, 100))
        return img

    assert glass_mod._differ(base, nudged(3)), "a pane three levels out in one place was held back"
    assert not glass_mod._differ(base, nudged(2)), "a pane two levels out in one place was handed on"
    assert glass_mod._differ(base, nudged(1, everywhere=True)), (
        "a pane a level out all over was held back")

    def pixels(img: QImage):
        rows = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * img.height())
        return rows.reshape(img.height(), -1)[:, :img.width() * 4].astype(np.int16)

    g = GlassBackdrop()
    g.clear = np.zeros((3, 2, 2), np.float32)    # ready to bend: the panes come ahead, as live glass's
    card = REGISTRY["clock"](pal, "medium", {})
    card.glass = g
    card.move(700, 300)
    samples.prepare(card)
    r = card.card_rect()
    key = (card.x() + int(MARGIN), card.y() + int(MARGIN), int(round(r.width())),
           int(round(r.height())), float(RADIUS))
    f = lens.field(key[2], key[3], RADIUS, lens.STEP)
    panes = []
    for rgb in ((110, 110, 110), (60, 60, 60)):
        pane = QImage(f.cols, f.rows, QImage.Format_RGB32)
        pane.fill(QColor(*rgb))
        panes.append(pane)

    def frame(i: int) -> None:
        g._bump([key[:4]], fade=False)
        g._ahead[key] = panes[i]

    def painted(over) -> QImage:
        img = QImage(card.size(), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        card.paint_card(p, over=over)
        p.end()
        return img

    def as_board() -> QImage:
        # paintEvent's choice, onto an image.
        reuse = card._glass_only and not card._over_stale
        card._glass_only = card._over_stale = False
        return painted("reuse" if reuse else "keep")

    try:
        frame(0)
        first = as_board()
        assert card._over_kept is not None, "nothing was kept from a card's paint"
        kept = card._over_kept[1]
        frame(1)
        card.glass_update()
        laid = as_board()
        assert card._over_kept[1] is kept, "a repaint for new glass alone drew the layer again"
        again = painted("keep")
        assert np.abs(pixels(laid) - pixels(again)).max() == 0, (
            "the kept layer on new glass isn't what drawing it again gives")
        assert np.abs(pixels(laid) - pixels(first)).max() > 20, "the new glass didn't show"
        card.update()                        # the clock ticking: drawn again
        as_board()
        assert card._over_kept[1] is not kept, "a repaint asked for by update() kept the old layer"
        def fringes(img: QImage) -> int:
            # Pixels whose channels spread apart: on grey glass under a
            # neutral tint, only ClearType's coloured edges do.
            a = pixels(img)
            b_, g_, r_ = a[:, 0::4], a[:, 1::4], a[:, 2::4]
            top = np.maximum(np.maximum(b_, g_), r_)
            return int((top - np.minimum(np.minimum(b_, g_), r_) > 16).sum())

        fringed = fringes(painted(None))
        assert fringes(laid) == 0, "the kept layer's type has coloured edges"

        def full():
            frame(0)
            card.update()
            as_board()

        def glass_alone():
            frame(1)
            card.glass_update()
            as_board()

        n = 40
        started = _t.perf_counter()
        for _ in range(n):
            full()
        drawn = 1000 * (_t.perf_counter() - started) / n
        started = _t.perf_counter()
        for _ in range(n):
            glass_alone()
        alone = 1000 * (_t.perf_counter() - started) / n

        # A picture over the whole card: no glass shows, none goes stale.
        folder = tempfile.mkdtemp(prefix="pcwidgets-layer-")
        path = _os.path.join(folder, "picture.png")
        picture = QImage(400, 300, QImage.Format_RGB32)
        picture.fill(QColor(200, 120, 40))
        picture.save(path)
        card.allow_image = True
        card.config["image"] = path
        card.update()
        as_board()
        assert card._covered and not card.glass_stale(), "a card a picture covers went on repainting for glass"
    finally:
        card.close()
        card.deleteLater()
    print(f"  glass layer: three levels' change goes, two don't; a repaint for new glass alone lays "
          f"the kept layer on it, pixel for pixel, in {alone:.2f} ms against {drawn:.2f} drawn again; "
          f"update() draws it again; its type greyscale ({fringed} coloured-edge pixels drawn "
          f"straight on the glass here, none in the layer); a covered card never goes stale")


def _check_timer(pal) -> None:
    """The Timer card keeps time on all four faces, rings once, and survives
    a restart.

    On a stand-in clock: a five-minute timer counts down as Apple's Clock
    writes it, pauses and resumes, and runs out once - one alert, the ring
    flashing. A new card made from its saved settings mid-run carries on
    running, and one that ran out while the board was closed comes back done,
    without an alert. The stopwatch laps, stops and resets; focus goes focus,
    break, ..., a long break after the fourth round, and starts the next by
    itself when asked; the countdown reads in days, then hours, then minutes,
    then Today, and rings as it arrives. Its buttons take a click without
    starting a drag, and so does the ruler a timer is set on: a drag scrolls
    it under its mark, a flick carries on a little and a click brings a minute
    to it. It glides there a frame at a time without overshooting, in the
    same time at 60 Hz and 144, and goes out of focus toward its ends. The wheel sets a
    length, and every face draws at every size in both themes
    (_selftest/_timer_dark.png, _timer_light.png).
    """
    import copy
    import datetime as _dt

    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QWheelEvent

    from widgets import tray
    from widgets.core import palette as pal_mod
    from widgets.core import samples
    from widgets.tiles import timer as tm

    now = [1_800_000_000.0]
    said = []

    def card(size: str = "small", cfg=None):
        c = tm.TimerWidget(pal, size, {} if cfg is None else cfg)
        c._now = lambda: now[0]
        c._alert = lambda title, text: said.append(title)
        return c

    # The time as text.
    assert (tm.down_text(300), tm.down_text(299.2), tm.down_text(299)) == ("5:00", "5:00", "4:59")
    assert (tm.down_text(0.2), tm.down_text(0), tm.down_text(3723)) == ("0:01", "0:00", "1:02:03")
    assert tm.up_text(83.47) == "01:23.47" and tm.up_text(45.91 - 21.62) == "00:24.29", (
        "a lap's time lost a hundredth to floating point")
    assert tm.up_text(3723.4, 1) == "1:02:03.4"
    assert [tm.parse_length(x) for x in ("25", "1:30", "1:00:00", "90s", "1h30m", "soon")] == [
        1500, 90, 3600, 90, 5400, None]

    # The timer: counts, pauses, rings once.
    c = card()
    t = c.timer
    assert tm.timer_state(t) == "ready" and tm.timer_left(t, now[0]) == 300
    c.timer_toggle()
    now[0] += 61.5
    c.on_tick()
    assert tm.timer_state(t) == "running" and tm.down_text(tm.timer_left(t, now[0])) == "3:59"
    c.timer_toggle()
    now[0] += 100
    assert tm.timer_state(t) == "paused" and tm.down_text(tm.timer_left(t, now[0])) == "3:59"
    c.timer_toggle()
    again = card(cfg=copy.deepcopy(c.config))      # the board restarting mid-run
    assert tm.timer_state(again.timer) == "running" and abs(
        tm.timer_left(again.timer, now[0]) - tm.timer_left(t, now[0])) < 1e-6, (
        "a running timer didn't survive a restart")
    now[0] += 238.6
    c.on_tick()
    assert tm.timer_state(t) == "done" and said == ["Timer done"] and now[0] < c._flash_until
    c.on_tick()
    assert said == ["Timer done"], "a timer rang twice"
    said.clear()
    late = card(cfg={"timer": {"duration": 60, "ends_at": now[0] - 600, "left": None}})
    late.on_tick()
    assert tm.timer_state(late.timer) == "done" and said == [], (
        "a timer that ran out while the board was closed rang on starting")

    # The wheel: a minute a notch, as the ruler counts; ten seconds under one, five past an hour.
    w = card()
    lengths = []
    for steps in (1, -5, -2, 2, 9, 1):
        w.nudge_duration(steps)
        lengths.append(w.timer["duration"])
    w.set_duration(3600)
    for steps in (1, -2):
        w.nudge_duration(steps)
        lengths.append(w.timer["duration"])
    assert lengths == [360, 60, 40, 60, 600, 660, 3900, 3540], f"the wheel's steps: {lengths}"

    # The stopwatch.
    s = card()
    sw = s.stopwatch
    s.stopwatch_toggle()
    now[0] += 12.34
    s.stopwatch_side()                               # a lap
    now[0] += 5.0
    s.stopwatch_toggle()                             # stopped
    now[0] += 50.0
    assert tm.up_text(tm.stopwatch_total(sw, now[0])) == "00:17.34" and sw["laps"] == [12.34]
    s.stopwatch_side()                               # reset
    assert tm.stopwatch_total(sw, now[0]) == 0 and sw["laps"] == []

    # Focus: four rounds, then a long break, then round 1 again.
    f = card()
    fo = f.focus
    f.on_tick()
    seen = []
    for _ in range(8):
        f.focus_toggle()
        now[0] += tm.focus_length(fo, fo["phase"]) + 0.5
        f.on_tick()
        seen.append((fo["phase"], fo["round"]))
    assert seen == [("short", 1), ("focus", 2), ("short", 2), ("focus", 3), ("short", 3),
                    ("focus", 4), ("long", 4), ("focus", 1)], f"focus went {seen}"
    assert f.focus_today() == 4 and said.count("Focus done") == 4 and said.count("Break's over") == 4
    f._set_focus("auto", True)
    f.focus_toggle()
    now[0] += tm.focus_length(fo, "focus") + 0.5
    f.on_tick()
    assert fo["phase"] == "short" and fo["ends_at"] is not None, "the next phase didn't start by itself"

    # The countdown.
    base = _dt.datetime(2026, 12, 10, 15, 0).timestamp()
    new_year = _dt.datetime(2027, 1, 1).timestamp()
    assert tm.countdown_text(new_year, base) == ("21", "days")
    assert tm.countdown_rest(new_year, base) == "and 9 h"
    assert tm.countdown_text(base + 5 * 3600 + 12 * 60 + 30, base) == ("5 h 12", "min")
    assert tm.countdown_text(base + 12 * 60 + 34, base) == ("12:34", "left")
    assert tm.countdown_text(base - 60, base) == ("Today", "")
    assert tm.countdown_text(base - 3 * 86400, base) == ("3", "days ago")
    said.clear()
    cd = card()
    cd.on_tick()
    soon = _dt.datetime.fromtimestamp(now[0] + 120).replace(second=0, microsecond=0)
    cd.set_countdown("Exam", soon.isoformat(timespec="minutes"))
    now[0] = soon.timestamp() + 1
    cd.on_tick()
    cd.on_tick()
    assert said == ["Exam"], f"a countdown arriving rang {said}"

    # The faces.
    for size in tm.TimerWidget.supported_sizes:
        assert [k for k, _ in card(size).available_faces()] == [
            "timer", "stopwatch", "focus", "countdown"]
    fc = card()
    fc.set_face("focus")
    assert fc.config["faces"] == {"small": "focus"} and fc.face == "focus"

    # A button takes a click without starting a drag; the wheel sets a length.
    b = card("medium")
    img = QImage(b.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    b.paint_card(p)
    p.end()
    go = next(rect for name, rect in b._buttons if name == "timer.go").center()
    at = QPointF(go)
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        ev = QMouseEvent(kind, at, at, Qt.LeftButton,
                         Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton,
                         Qt.NoModifier)
        (b.mousePressEvent if kind == QEvent.MouseButtonPress else b.mouseReleaseEvent)(ev)
        if kind == QEvent.MouseButtonPress:
            assert b._drag_from is None, "pressing a timer's button started a drag"
    assert tm.timer_state(b.timer) == "running", "clicking start didn't start the timer"
    b.timer_reset()

    # The ruler glides to a new length, a step a displayed frame, rather than
    # jumping: along a spring that never overshoots, done in about a quarter
    # second whether the display runs at 60 Hz or 144. Here the frames are
    # stepped by hand; on the board the frame clock steps them.
    b.set_duration(15 * 60)
    settled = {}
    for hz in (60, 144):
        b._stop_glide_ruler()
        b._glide_ruler(10.0)                     # from 10 minutes to the 15 set
        if b._glider is not None:
            b._glider.stop()
        seen, t_ = [], 0.0
        while b._shown_minutes is not None and t_ < 2.0:
            t_ += 1.0 / hz
            b._glide_step(now=t_)
            if b._shown_minutes is not None:
                seen.append(b._shown_minutes)
        assert b._shown_minutes is None, f"the ruler never settled at {hz} Hz"
        assert seen == sorted(seen) and max(seen) <= 15 + 1e-6, f"the ruler overshot at {hz} Hz"
        assert len(seen) > 6, f"the ruler jumped instead of gliding at {hz} Hz ({len(seen)} frames)"
        settled[hz] = t_
    assert 0.12 < settled[60] < 0.7 and abs(settled[60] - settled[144]) < 0.06, (
        f"the glide's length depends on the display's rate: {settled}")

    # A drag follows the pointer exactly and settles on a minute; a flick
    # carries on a little; a click brings a minute to the mark; none moves
    # the card.
    img.fill(Qt.transparent)
    p = QPainter(img)
    b.paint_card(p)
    p.end()
    assert b._ruler is not None, "a timer not yet started has no ruler"
    rect, spacing, _m = b._ruler
    mid = QPointF(rect.center().x(), rect.center().y())

    def mouse(kind, at_):
        down = kind != QEvent.MouseButtonRelease
        ev = QMouseEvent(kind, at_, at_, Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                         Qt.LeftButton if down else Qt.NoButton, Qt.NoModifier)
        {QEvent.MouseButtonPress: b.mousePressEvent, QEvent.MouseMove: b.mouseMoveEvent,
         QEvent.MouseButtonRelease: b.mouseReleaseEvent}[kind](ev)

    def settle():
        if b._glider is not None:
            b._glider.stop()
        t_ = 0.0
        while b._shown_minutes is not None and t_ < 2.0:
            t_ += 1.0 / 60
            b._glide_step(now=t_)
        img.fill(Qt.transparent)
        q = QPainter(img)
        b.paint_card(q)
        q.end()
        return b._ruler

    mouse(QEvent.MouseButtonPress, mid)
    assert b._drag_from is None, "pressing the ruler started a drag"
    mouse(QEvent.MouseMove, QPointF(mid.x() - 2.6 * spacing, mid.y()))
    assert abs(b._shown_minutes - 17.6) < 1e-6, "a dragged ruler didn't follow the pointer exactly"
    mouse(QEvent.MouseButtonRelease, QPointF(mid.x() - 2.6 * spacing, mid.y()))
    assert b.timer["duration"] == 18 * 60, f"dragging the ruler 2.6 minutes set {b.timer['duration']} s"
    rect, spacing, _m = settle()
    mouse(QEvent.MouseButtonPress, mid)
    mouse(QEvent.MouseMove, QPointF(mid.x() - spacing, mid.y()))
    b._drag_track = [(0.0, 18.0), (0.05, 19.0)]          # a minute in a twentieth of a second
    mouse(QEvent.MouseButtonRelease, QPointF(mid.x() - spacing, mid.y()))
    assert b.timer["duration"] == 21 * 60, f"a flick from 19 settled on {b.timer['duration']} s"
    rect, spacing, _m = settle()
    two = QPointF(rect.center().x() + 2 * spacing, rect.center().y())
    mouse(QEvent.MouseButtonPress, two)
    mouse(QEvent.MouseButtonRelease, two)
    assert b.timer["duration"] == 23 * 60, f"clicking two minutes along set {b.timer['duration']} s"
    settle()
    b.timer_reset()

    # And it's out of focus toward its ends: stripes stay sharp in the middle
    # and run together at the sides.
    stripes = QImage(300, 40, QImage.Format_ARGB32)
    stripes.fill(Qt.transparent)
    sp = QPainter(stripes)
    for x in range(0, 300, 10):
        sp.fillRect(x, 0, 3, 40, QColor(255, 159, 10))
    sp.end()
    soft = tm.out_of_focus(stripes)
    try:
        import numpy  # noqa: F401
        blurs = True
    except ImportError:
        blurs = False
    if blurs:
        def spread(x0: int, x1: int) -> int:
            alphas = [QColor.fromRgba(soft.pixel(x, 20)).alpha() for x in range(x0, x1)]
            return max(alphas) - min(alphas)
        assert spread(140, 160) > 200, "the ruler's middle went soft"
        assert spread(20, 40) < 120, "the ruler's ends stayed sharp"
    before = b.timer["duration"]
    b.wheelEvent(QWheelEvent(at, at, QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                             Qt.NoScrollPhase, False))
    assert b.timer["duration"] == before + 60, "the wheel didn't lengthen the timer a minute"
    assert tray.notify("Timer done", "test") is False, "a notification went out with no tray"

    # Every face at every size, in both themes, for looking at.
    out = os.path.join(_write_dir(), "_selftest")
    os.makedirs(out, exist_ok=True)
    faces = [k for k, _ in tm.FACES_ALL]
    for theme in ("dark", "light"):
        tpal = pal_mod.build(theme, "blue")
        sheet = QImage(1080, 4 * 420 + 40, QImage.Format_ARGB32_Premultiplied)
        sheet.fill(QColor(30, 30, 34) if theme == "dark" else QColor(236, 236, 240))
        sp = QPainter(sheet)
        for row, face in enumerate(faces):
            for x, size in zip((40, 260, 660), tm.TimerWidget.supported_sizes):
                fcard = tm.TimerWidget(tpal, size, {})
                samples.timer(fcard)
                fcard.config["faces"] = {size: face}
                pic = QImage(fcard.size(), QImage.Format_ARGB32_Premultiplied)
                pic.fill(Qt.transparent)
                q = QPainter(pic)
                fcard.paint_card(q)
                q.end()
                sp.drawImage(x, 20 + row * 420, pic)
                fcard.close()
                fcard.deleteLater()
        sp.end()
        sheet.save(os.path.join(out, f"_timer_{theme}.png"))
    for c_ in (c, again, late, w, s, f, cd, fc, b):
        c_.close()
        c_.deleteLater()
    print("  timer: counts down as Apple's Clock writes it, pauses, rings once and survives a "
          "restart, quiet for an end while the board was closed; the wheel's steps; stopwatch "
          "laps and resets; focus rounds to a long break and starts the next by itself; the "
          "countdown in days, hours, minutes and Today, ringing as it arrives; buttons and the "
          "ruler click and scrub without dragging the card; faces in _selftest/_timer_*.png")


def _check_layout_mirror() -> None:
    """The saved layout must survive one of its two locations being unreadable.

    A layout that cannot be read comes back as a *default* board - every
    position, colour and background picture silently gone, which is
    indistinguishable from the app having forgotten them. This machine's board
    process has repeatedly been unable to read or write %APPDATA%, so the layout
    is mirrored beside the app and read from whichever copy is newest.
    """
    from widgets.core import store as st

    with _sandboxed_layout():
        cfg = {"bg": "#000000", "fill": "solid", "image": "C:/pic.jpg"}
        state = st.BoardState(
            placements=[st.Placement("clock", "medium", 40, 40, dict(cfg))])
        assert st.save(state), "save failed"
        assert os.path.exists(st._path()), "no profile copy"
        assert os.path.exists(st._mirror_path()), "no mirrored copy"

        os.remove(st._path())            # the profile copy goes unreadable
        back = st.load()
        assert back.placements and back.placements[0].config == cfg, (
            "layout lost when the profile copy was unavailable - this is the "
            "failure that looks like the app forgetting your settings")

        st.save(state)
        os.remove(st._mirror_path())     # and the other way round
        assert st.load().placements[0].config == cfg
        print("  layout mirror: survives losing either copy")


def _check_crop() -> None:
    """A background photo must sit where its crop says, on any card shape.

    Four properties: a photo zoomed to fill can never leave a gap, whatever
    centre is saved; zooming toward the cursor keeps that point still; the
    pixels really land on the part of the photo asked for; and the editor
    keeps the crop on Done and restores everything on Cancel - including when
    the photo itself was swapped mid-edit.
    """
    import tempfile

    from PySide6.QtGui import QColor, QImage, QPainter

    from widgets.core import imagefit
    from widgets.core.board import REGISTRY
    from widgets.core.imagefit import Crop
    from widgets.core.palette import build
    from widgets.ui import cropper

    iw, ih = 1200, 600
    for cw, ch in ((168, 168), (348, 168), (348, 348)):
        for z in (1.0, 2.5, 8.0):
            for x in (-3.0, 0.0, 0.5, 1.0, 4.0):
                l, t, w, h = imagefit.place(iw, ih, cw, ch, Crop(x, 1 - x, z))
                assert (l <= 0.01 and t <= 0.01 and l + w >= cw - 0.01
                        and t + h >= ch - 0.01), (
                    f"gap on a {cw}x{ch} card at zoom {z}, centre {x}")
        lo, _hi = imagefit.zoom_range(iw, ih, cw, ch)
        l, t, w, h = imagefit.place(iw, ih, cw, ch, Crop(0.9, 0.1, lo))
        assert (l >= -0.01 and t >= -0.01 and l + w <= cw + 0.01
                and t + h <= ch + 0.01), "a photo zoomed out to fit spilled out"

    c0 = Crop(0.5, 0.5, 2.0)
    l, t, w, h = imagefit.place(iw, ih, 348, 168, c0)
    u, v = (200 - l) / w, (90 - t) / h
    l, t, w, h = imagefit.place(
        iw, ih, 348, 168, imagefit.zoomed(iw, ih, 348, 168, c0, 3.0, (200, 90)))
    assert abs(l + u * w - 200) < 0.01 and abs(t + v * h - 90) < 0.01, (
        "zooming drifted away from the cursor")

    assert imagefit.to_config(Crop()) is None, "a default crop was saved"
    assert imagefit.from_config({"crop": {"x": "nan"}}) == Crop()
    back = imagefit.from_config({"crop": imagefit.to_config(Crop(0.25, 0.75, 2.0))})
    assert back == Crop(0.25, 0.75, 2.0), f"crop did not round-trip: {back}"

    tmp = tempfile.mkdtemp(prefix="pcwidgets-crop-")
    try:
        pic = os.path.join(tmp, "quad.png")
        img = QImage(iw, ih, QImage.Format_RGB32)
        p = QPainter(img)
        for (x, y), col in (((0, 0), "#ff0000"), ((600, 0), "#00ff00"),
                            ((0, 300), "#0000ff"), ((600, 300), "#ffff00")):
            p.fillRect(x, y, 600, 300, QColor(col))
        p.end()
        img.save(pic)
        pal = build("dark", "blue")
        Clock = REGISTRY["clock"]

        def centre(size, crop):
            card = Clock(pal, size, {"image": pic, "crop": crop})
            r = card.card_rect()
            out = QImage(card.width(), card.height(),
                         QImage.Format_ARGB32_Premultiplied)
            out.fill(QColor("#000000"))
            pp = QPainter(out)
            assert card._paint_image_bg(pp, r), "the photo was not drawn"
            pp.end()
            card.deleteLater()
            c = out.pixelColor(int(r.center().x()), int(r.center().y()))
            return c.red(), c.green(), c.blue()

        r_, g_, b_ = centre("medium", {"x": 0.25, "y": 0.25, "zoom": 2.0})
        assert r_ > 150 and g_ < 60 and b_ < 60, f"top-left crop showed {r_, g_, b_}"
        r_, g_, b_ = centre("medium", {"x": 0.75, "y": 0.75, "zoom": 2.0})
        assert r_ > 150 and g_ > 150 and b_ < 60, f"bottom-right crop showed {r_, g_, b_}"
        # The same crop on another shape still shows the same part of the photo.
        r_, g_, b_ = centre("small", {"x": 0.25, "y": 0.75, "zoom": 2.0})
        assert b_ > 150 and r_ < 60, f"crop lost its place on a small card: {r_, g_, b_}"

        card = Clock(pal, "medium", {"image": pic})
        dlg = cropper.CropDialog(card, pic, imagefit.load(pic))
        dlg.canvas.zoom_to(2.0, (40.0, 40.0))
        dlg.accept()
        assert card.config.get("crop") and card.config["image"] == pic, (
            "Done did not keep the crop")
        kept = dict(card.config)
        other = os.path.join(tmp, "other.png")
        swap = QImage(400, 400, QImage.Format_RGB32)
        swap.fill(QColor("#808080"))
        swap.save(other)
        dlg = cropper.CropDialog(card, other, imagefit.load(other))
        dlg.canvas.nudge(-50, 0)
        assert card.config["image"] == other, "the editor is not previewing live"
        dlg.reject()
        assert card.config == kept, "Cancel did not put the photo and crop back"
        card.deleteLater()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("  crop: fills at any centre and zoom, zooms about the cursor, lands on "
          "the requested part, editor keeps on Done and restores on Cancel")


def _check_app_window() -> None:
    """The app window: a gallery tile for every widget, and Add, Remove and
    Undo really change the board. Renders each page, in both themes, to
    ``_selftest/_app_*.png`` so it can be looked at."""
    import time as _t

    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRectF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    from widgets.core import store
    from widgets.core.board import REGISTRY, Board
    from widgets.core.card import WidgetCard
    from widgets.ui.app import MainWindow

    # Earlier checks queued their cards for deletion; let that happen before
    # any event processing here, so none of them gets to run a timer first.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def pump(sec: float) -> None:
        end = _t.monotonic() + sec
        while _t.monotonic() < end:
            QApplication.processEvents()
            _t.sleep(0.005)

    def area(c):
        r = c.card_rect()
        return QRectF(c.x() + r.left(), c.y() + r.top(), r.width(), r.height())

    out = os.path.join(_write_dir(), "_selftest")
    real_start = WidgetCard.start
    WidgetCard.start = lambda self: None        # never put a card on screen
    try:
        with _sandboxed_layout():
            board = Board(store.BoardState())
            for timer in (board._autosave, board._watch, board._glass_timer,
                          board._park):
                timer.stop()
            win = MainWindow(board)
            win.setAttribute(Qt.WA_DontShowOnScreen)
            win.show()
            assert {t.kind for t in win.gallery.tiles} == set(REGISTRY), (
                "the gallery is missing a widget")

            tile = next(t for t in win.gallery.tiles if t.kind == "clock")
            tile._add()
            tile._add()
            assert [c.kind for c in board.cards] == ["clock", "clock"], (
                "Add did not put cards on the board")
            a, b = board.cards
            assert not area(a).intersects(area(b)), (
                "a new widget landed on top of an existing one")
            assert len(win.desktop.rows) == 2, "the desktop page missed a card"

            win.desktop.select(b)
            win.desktop.inspector._remove()
            assert len(board.cards) == 1, "Remove left the card on the board"
            win.toast_box._act()                 # the toast's Undo
            assert len(board.cards) == 2, "Undo did not bring the card back"

            # Add goes to the display picked in the gallery, when there is a
            # choice; with one display there is nothing to pick.
            many = len(board.screens) > 1
            assert (win.gallery.target() is None) == (not many)
            assert win.gallery.where.isHidden() == (not many)
            if many:
                other = next(d for d in board.screens if not d.primary)
                win.gallery.where.set_current(other.key)
                tile = next(t for t in win.gallery.tiles if t.kind == "weather")
                tile._add()
                assert board.display_of(board.cards[-1]) == other, (
                    "Add ignored the display picked for it")
                win.desktop.select(board.cards[-1])
                assert not win.desktop.inspector.where_row.isHidden()

            # Weather faces in the gallery: dots page through the size's faces,
            # the preview changes with them, a size change starts again on
            # Classic, and Add brings the face that was showing.
            wt = next(t for t in win.gallery.tiles if t.kind == "weather")
            wt._size_changed("large")
            wt.render_preview()
            assert len(wt.dot_rects()) == 3 and wt.face() == "classic"
            before = wt._preview.cacheKey()
            wt.set_face(1)
            assert wt.face() == "daily" and wt._preview.cacheKey() != before
            wt.set_face(5)
            assert wt.face() == "nexthour", "paging past the end did not wrap"
            n_cards = len(board.cards)
            wt._add()
            added = board.cards[-1]
            assert len(board.cards) == n_cards + 1 and added.size_class == "large"
            assert added.config.get("faces") == {"large": "nexthour"}, added.config
            assert added.face == "nexthour", "Add left the face behind"
            board.remove(added)
            wt._size_changed("small")
            assert wt.face() == "classic" and len(wt.dot_rects()) == 5
            # A widget with one look shows no dots. Notes, not Clock, which
            # has faces of its own now.
            plain = next(t for t in win.gallery.tiles if t.kind == "notes")
            assert plain.dot_rects() == [] and plain.face() == ""
            wt._size_changed(wt.sizes.current)       # back in step with the size control

            # Presets: saving one starts naming it, Apply puts it back, Undo
            # puts back what it replaced.
            pp = win.presets
            win.show_page("presets")
            pp._save_new()
            assert len(board.state.presets) == 1 and pp.rows[0]._editing, (
                "saving a preset did not start naming it")
            pp.rows[0].field.setText("Desk")
            pp.rows[0]._commit()
            assert board.state.presets[0].name == "Desk", board.state.presets[0].name
            assert pp.rows[0].in_use, "a preset just saved isn't marked in use"
            moved = board.cards[0]
            home = (moved.x(), moved.y())
            moved.move(home[0] + 60, home[1] + 40)
            board._card_moved(moved)
            assert not pp.rows[0].in_use, "a moved card left its preset marked in use"
            pp.apply(board.state.presets[0])
            assert (moved.x(), moved.y()) == home and pp.rows[0].in_use
            win.toast_box._act()                 # Undo
            assert (moved.x(), moved.y()) == (home[0] + 60, home[1] + 40)
            board.save_preset("Evenings")
            assert [r.preset.name for r in pp.rows] == ["Desk", "Evenings"]

            for theme in ("dark", "light"):
                board.set_theme(theme)
                for page in ("gallery", "desktop", "presets", "settings"):
                    win.show_page(page)
                    pump(0.6)
                    win.grab().save(os.path.join(out, f"_app_{theme}_{page}.png"))

            # The window is cut to the widgets' shape: solid inside, see-through
            # past the corner's curve, with its shadow in a margin round it that
            # resizes the window - and the margin goes when it is maximised.
            from widgets.ui import chrome, theme as look
            assert win.testAttribute(Qt.WA_TranslucentBackground), "the window is not layered"
            m = win.frame_margin()
            assert m == look.WINDOW_SHADOW, f"the shadow margin is {m}"
            shot = win.grab().toImage()
            body = win.body_rect()
            solid = shot.pixelColor(body.center()).alpha()
            corner = shot.pixelColor(body.left() + 2, body.top() + 2).alpha()
            far = shot.pixelColor(0, 0).alpha()
            assert solid == 255 and corner < 250 and far == 0, (
                f"window shape: body {solid}, past the curve {corner}, margin corner {far}")
            mid_y = body.center().y()
            for pt, want in ((QPointF(body.center()), None),
                             (QPointF(m - 4, mid_y), chrome.HTLEFT),
                             (QPointF(body.right() + 4, mid_y), chrome.HTRIGHT),
                             (QPointF(body.left() + 3, body.top() + 3), chrome.HTTOPLEFT),
                             (QPointF(body.right() - 2, body.bottom() - 2),
                              chrome.HTBOTTOMRIGHT)):
                got = win.edge_hit(pt)
                assert got == want, f"edge at {pt.x(), pt.y()} answered {got}, not {want}"

            # The sidebar is frosted glass right to the window's edge, with a
            # hairline between it and the page - not a panel floating inside.
            from PySide6.QtGui import QColor, QImage
            from widgets.core import winapi
            side = win.side_rect()
            y = side.center().y()
            edge = shot.pixelColor(side.left() + 3, y)
            assert edge.alpha() == 255 and edge.rgb() != QColor(look.current().base).rgb(), (
                f"the sidebar stops short of the window's edge ({edge.name()})")
            line, page = shot.pixelColor(side.right(), y), shot.pixelColor(side.right() + 4, y)
            assert line.rgb() != page.rgb(), "no hairline between the sidebar and the page"
            # It is frosted with what lies behind it: put a red desktop there
            # and the glass turns red.
            glass = board.glass
            real = (glass.image, glass.origin)
            red = QImage(6000, 4000, QImage.Format_RGB32)
            red.fill(QColor("#ff0000"))
            glass.image, glass.origin = red, (-3000, -2000)
            try:
                win.update()
                pump(0.2)
                frosted = win.grab().toImage().pixelColor(side.center())
            finally:
                glass.image, glass.origin = real
                win.update()
            assert frosted.red() > frosted.green() + 20, (
                f"the sidebar isn't frosted with the wallpaper behind it: {frosted.name()}")
            # Hanging off the edge of the desktop, where there is no wallpaper,
            # it stays solid: a see-through gap would be a hole in the window.
            home = win.pos()
            vl, vt, _vr, _vb = winapi.virtual_screen()
            win.move(vl - side.right() - 40, home.y())
            pump(0.2)
            off = win.grab().toImage().pixelColor(side.left() + 3, y)
            win.move(home)
            pump(0.2)
            assert off.alpha() == 255, "the sidebar went see-through off the desktop's edge"

            win.showMaximized()
            pump(0.2)
            assert win.frame_margin() == 0 and win.edge_hit(QPointF(2, 2)) is None, (
                "a maximised window kept its margin")
            assert win.grab().toImage().pixelColor(1, 1).alpha() == 255, (
                "maximised, the sidebar's glass doesn't reach the screen's corner")
            win.showNormal()
            pump(0.2)
            assert win.frame_margin() == m, "the margin did not come back on restore"

            # A wheel notch glides through many positions rather than jumping,
            # and lands exactly where one notch should.
            win.show_page("gallery")
            pump(0.3)
            area = win.gallery.area
            bar = area.verticalScrollBar()
            bar.setValue(0)
            QApplication.sendEvent(area.viewport(), QWheelEvent(
                QPointF(100, 100), QPointF(100, 100), QPoint(0, 0), QPoint(0, -120),
                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
            seen = set()
            end = _t.monotonic() + 1.5
            while _t.monotonic() < end and (area._timer.isActive() or not seen):
                QApplication.processEvents()
                seen.add(bar.value())
                _t.sleep(0.003)
            want = min(bar.maximum(), int(area.STEP))
            assert bar.value() == want, f"a wheel notch landed at {bar.value()}, not {want}"
            assert len(seen) >= 5, f"a wheel notch jumped instead of gliding: {sorted(seen)}"
            win.hide()
            win.deleteLater()
            board.close_all()
    finally:
        WidgetCard.start = real_start
    print("  app window: a tile per widget, Add places clear of other cards on the "
          "display picked, Remove and Undo round-trip, presets save, rename, apply "
          "and undo, cut to the widgets' shape with a margin that resizes and goes "
          "when maximised, sidebar frosted to the edge with its hairline, wheel "
          "scrolling glides; pages in _selftest/_app_*.png")


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


def _check_visualiser() -> None:
    """Now Playing's bars: bass on the left to hi-hats on the right, each one
    hitting the beat in its own part of the range instead of hovering.

    They used to sit on a 56 dB scale under one gain shared by all five, so the
    bass pinned the first bar near the top and a hi-hat moved the last one about
    a tenth of its height. Played a synthetic 120 BPM loop - kick, snare,
    16th-note hats over a bass line - offline; no audio device is opened.
    """
    try:
        import numpy as np
    except Exception:
        print("  visualiser: skipped, no numpy")
        return
    from widgets.core import audio

    rate, block = 48000, audio.BLOCK
    rng = np.random.default_rng(5)

    def noise(n, lo, hi):
        spec = np.fft.rfft(rng.standard_normal(n))
        f = np.fft.rfftfreq(n, 1.0 / rate)
        spec[(f < lo) | (f > hi)] = 0
        x = np.fft.irfft(spec, n)
        return (x / np.abs(x).max()).astype(np.float32)

    def tone(hz, n):
        return np.sin(2 * np.pi * hz * np.arange(n) / rate).astype(np.float32)

    # Each part of the range lands in its own bar, left to right.
    n = block * 4
    parts = (("55 Hz", tone(55, n)), ("180 Hz", tone(180, n)), ("800 Hz", tone(800, n)),
             ("2-4 kHz", noise(n, 2000, 4000)), ("7-14 kHz", noise(n, 7000, 14000)))
    for want, (name, sig) in enumerate(parts):
        an = audio.Analyser(rate)
        for k in range(4):
            db = an.feed(sig[k * block:(k + 1) * block])
        got = max(range(audio.BANDS), key=lambda i: db[i])
        assert got == want, f"{name} lit bar {got + 1}, not bar {want + 1}"

    # A loop: kicks every half second, 16th-note hats, a bass line under both -
    # balanced the way real music measures through the analyser, hats a few
    # dB under the kick band rather than thirty. A synthetic loop with a
    # thumping kick and faint hats would test a mix nobody listens to.
    secs, beat = 4.0, 0.5
    total = int(secs * rate)
    mix = np.zeros(total, np.float32)
    t = np.arange(total) / rate
    mix += 0.06 * np.sin(2 * np.pi * np.where((t // 1.0) % 2, 73.4, 55.0) * t).astype(np.float32)
    kick_len, hat_len = int(0.4 * rate), int(0.12 * rate)
    tk = np.arange(kick_len) / rate
    kick = (np.sin(2 * np.pi * np.cumsum(45 + 65 * np.exp(-tk / 0.03)) / rate)
            * np.exp(-tk / 0.15)).astype(np.float32)
    th = np.arange(hat_len) / rate
    hat = noise(hat_len, 6500, 15000) * np.exp(-th / 0.025).astype(np.float32)
    kicks = np.arange(0.0, secs, beat)
    for at in kicks:
        i = int(at * rate)
        mix[i:i + kick_len] += 0.2 * kick[:total - i]
    for at in np.arange(0.0, secs, beat / 4):
        i = int(at * rate)
        mix[i:i + hat_len] += 0.8 * hat[:total - i]
    mix /= np.abs(mix).max() / 0.9

    an, sh = audio.Analyser(rate), audio.Shaper()
    levels = np.array([sh.feed(an.feed(mix[s:s + block]))
                       for s in range(0, total - block, block)])
    dt = block / rate

    def mean_after(band, start, end):
        rows = sorted({int((at + d) / dt) for at in kicks if at >= 1.0
                       for d in np.arange(start, end, dt)})
        return float(levels[[r for r in rows if r < len(levels)], band].mean())

    # The bass bars keep a held note, so the bass line under the kicks holds
    # the kick bar up between them; a kick still has to stand clear of that.
    hit, between = mean_after(0, 0.0, 0.06), mean_after(0, 0.30, 0.45)
    assert hit > 0.7 and hit - between > 0.15, (
        f"the kick bar doesn't hit the kicks: {hit:.2f} on them, {between:.2f} between")
    settled = levels[int(1.0 / dt):]
    travel = np.abs(np.diff(settled, axis=0)).sum(axis=0) / (len(settled) * dt)
    assert travel[4] > 4.0, f"the hi-hat bar moves {travel[4]:.1f} heights a second"

    for _ in range(60):
        out = sh.feed(an.feed(np.zeros(block, np.float32)))
    assert max(out) < 0.02, f"bars do not settle in silence: {out}"

    # A bass line alone must not move the right-hand bars. Scaling each band to
    # its own range did exactly that: a bass note's attack and harmonics, tens
    # of dB down in the upper bands, were stretched to full height, so the whole
    # visualiser jumped on every note.
    notes = np.zeros(total, np.float32)
    note_len = int(0.45 * rate)
    tn = np.arange(note_len) / rate
    env = np.minimum(1, tn / 0.005) * np.exp(-tn / 0.35)
    for k, f0 in enumerate((55.0, 41.2, 49.0, 61.7, 55.0, 73.4, 46.2, 55.0)):
        i = int(k * 0.5 * rate)
        tone = sum(np.sin(2 * np.pi * f0 * h * tn) / h for h in range(1, 6))
        notes[i:i + note_len] += (env * tone)[:total - i].astype(np.float32)
    notes /= np.abs(notes).max() / 0.9
    an, sh = audio.Analyser(rate), audio.Shaper()
    bass_bars = np.array([sh.feed(an.feed(notes[s:s + block]))
                          for s in range(0, total - block, block)])[int(1.0 / dt):]
    leak = float(bass_bars[:, 3:].max())
    assert bass_bars[:, :2].max() > 0.9 and leak < 0.25, (
        f"bass notes alone moved the treble bars to {leak:.2f}")
    print(f"  visualiser: bass to hats left to right; the kick bar hits {hit:.2f} on "
          f"the kick and {between:.2f} between, the hat bar moves "
          f"{travel[4]:.1f} heights a second; bass alone keeps the treble bars "
          f"under {leak:.2f}; silence settles")


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

    # A game covers one monitor: the cards on it go quiet, the ones beside it
    # keep going - a board on the second screen is often the reason it's there.
    from widgets.core import store
    from widgets.core.card import WidgetCard

    main, left = _desk()
    real_start, real_full = WidgetCard.start, winapi.fullscreen_monitor
    WidgetCard.start = lambda self: None
    try:
        with _sandboxed_layout(), _fake_displays([main, left]):
            board = _quiet_board([
                store.Placement("clock", "small", 100, 100, display=main.key,
                                area=list(main.work)),
                store.Placement("clock", "small", -1000, 100, display=left.key,
                                area=list(left.work)),
            ])
            under, beside = board.cards
            winapi.fullscreen_monitor = lambda: main.rect
            board._check_fullscreen()
            assert under.quiet and not beside.quiet, (
                "a game on one monitor silenced the widgets on another")
            winapi.fullscreen_monitor = lambda: None
            board._check_fullscreen()
            assert not under.quiet and not beside.quiet, "quiet did not lift"
            board.close_all()
    finally:
        WidgetCard.start, winapi.fullscreen_monitor = real_start, real_full
    print("  quiet mode: suppresses repaints, resumes cleanly, only under the "
          "game's own monitor, no false positive on this desktop")


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
    """Putting a card somewhere must leave it exactly there.

    Lining up with neighbours (magnet.py) happens only while a card is being
    dragged, where the outline shows it coming. A card the app places -
    restored, added, regrown - stays put with the snap setting on or off: one
    that shifted by itself on launch would look like a bug. The alignment
    arithmetic itself is checked by tools/magnet_selftest.py.
    """
    from widgets.tiles.clock import ClockWidget

    card = ClockWidget(pal, "small")
    spot = (417, 263)              # deliberately off any grid
    for snap in (False, True):
        card.snap_to_grid = snap
        card.move(*spot)
        card.settle()
        assert (card.x(), card.y()) == spot, (
            f"settle (snap={snap}) moved the card to {(card.x(), card.y())}")
    print(f"  placement: a placed card stays at {spot}, snap on or off")
    card.deleteLater()


def _check_first_run() -> None:
    """Only a fresh install is marked for the first-run setup, and it starts
    from the person's own Windows choices.

    The line that matters is between three cases that all used to end in the
    same "starting from a default board": no layout anywhere (a new install,
    which gets the setup), a layout from before the setup existed (an update,
    which must not), and a layout that can't be read (a fault, which gets a
    plain default board and a log line, not a welcome). Windows' readings are
    stubbed, so the check is the same on every machine.
    """
    import json

    from widgets.core import palette as pal_mod
    from widgets.core import store, winapi
    from widgets.core.board import REGISTRY

    real = (winapi.apps_use_light_theme, winapi.uses_us_measurements)
    area = (0, 0, 1920, 1040)

    def write_both(text: str) -> None:
        for path in store._both_paths():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)

    try:
        winapi.apps_use_light_theme = lambda: True
        winapi.uses_us_measurements = lambda: True
        with _sandboxed_layout():
            fresh = store.load(area)
            assert fresh.setup == "pending", "a fresh install was not marked for the setup"
            assert fresh.theme == "light", "a fresh install ignored Windows' light app mode"
            kinds = [pl.kind for pl in fresh.placements]
            assert kinds == ["clock", "weather", "system", "disk", "notes"], kinds
            unit = next(pl.config.get("unit") for pl in fresh.placements if pl.kind == "weather")
            assert unit == "F", f"the starter Weather card is in {unit}, not the region's F"
            assert store.save(fresh) and store.load().setup == "pending", \
                "the setup's state did not survive a save"

            raw = json.loads(json.dumps(store.asdict(fresh)))
            raw.pop("setup")
            write_both(json.dumps(raw))
            assert store.load(area).setup == "done", "an update was treated as a first run"

            write_both("{not json")
            broken = store.load(area)
            assert broken.setup == "done" and broken.placements, \
                "an unreadable layout was treated as a first run"

        winapi.apps_use_light_theme = lambda: None      # can't be read: keep the default
        winapi.uses_us_measurements = lambda: False
        with _sandboxed_layout():
            fresh = store.load(area)
            unit = next(pl.config.get("unit") for pl in fresh.placements if pl.kind == "weather")
            assert fresh.theme == "dark" and unit == "C", (fresh.theme, unit)

        # A new card starts in the region's units; only Weather has any to give.
        winapi.uses_us_measurements = lambda: True
        assert REGISTRY["weather"].new_config() == {"unit": "F"}
        winapi.uses_us_measurements = lambda: None
        assert REGISTRY["weather"].new_config() == {"unit": "C"}
        assert REGISTRY["clock"].new_config() == {}
    finally:
        winapi.apps_use_light_theme, winapi.uses_us_measurements = real

    worst = min(pal_mod.contrast(pal_mod.button_fill(a), "#ffffff") for a in pal_mod.ACCENTS.values())
    for accent in pal_mod.ACCENTS.values():
        fill = pal_mod.button_fill(accent)
        assert pal_mod.contrast(fill, "#ffffff") >= 4.5, f"white on {fill} fails 4.5:1"
        assert pal_mod.luminance(fill) <= pal_mod.luminance(accent), f"{fill} is lighter than {accent}"
    print("  first run: only a fresh install is marked (not an update, not an unreadable "
          "layout); it starts in Windows' app mode and the region's units; "
          f"button shades {worst:.2f}:1 or better with white on all {len(pal_mod.ACCENTS)} accents")


def _check_town_search() -> None:
    """Typing a town lists the matches with their regions, the two failures
    say different things, and an empty Weather card is a button rather than a
    hint to right-click. The searches are canned: no network. Renders go to
    _selftest/_townsearch_*.png and _weather_settown_*.png."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtCore import Qt

    from widgets.core import palette as pal_mod
    from widgets.tiles import weather
    from widgets.ui.townsearch import TownSearch

    canned = {"results": [
        {"name": "Chicago", "admin1": "Illinois", "country": "United States",
         "country_code": "US", "latitude": 41.85, "longitude": -87.65},
        {"name": "Chico", "admin1": "California", "country": "United States",
         "country_code": "US", "latitude": 39.73, "longitude": -121.84},
        {"name": "Chicopee", "admin1": "Massachusetts", "country": "United States",
         "country_code": "US", "latitude": 42.15, "longitude": -72.61},
        {"name": "No coordinates"},         # malformed: skipped, not fatal
    ]}
    asked = []
    real_get = weather._get
    weather._get = lambda url, params: (asked.append((url, dict(params))), canned)[1]
    try:
        towns = weather.find_towns("  Chic ")
        assert [t.name for t in towns] == ["Chicago", "Chico", "Chicopee"], towns
        assert towns[0].region == "Illinois, United States" and towns[0].label == "Chicago, US"
        assert asked[0][0] == weather.GEO and asked[0][1]["name"] == "Chic"
        assert weather.find_towns("C") == [] and len(asked) == 1, "a single letter was sent"
    finally:
        weather._get = real_get

    def offline(_q):
        raise OSError("no route to host")

    out = os.path.join(_write_dir(), "_selftest")
    for theme in ("light", "dark"):
        pal = pal_mod.build(theme, "blue")
        picked = []

        box = TownSearch(pal, picked.append, finder=lambda q: towns, threaded=False)
        box.field.setText("Chic")
        box.q.type("Chic")
        assert box.q.pending(), "typing searched at once instead of after a pause"
        box.q.search_now()
        assert box.state == "results" and len(box.towns) == 3, box.state
        box.q._landed(box.q._seq - 1, [])
        assert box.state == "results", "an older, overtaken search replaced the list"
        box.grab().save(os.path.join(out, f"_townsearch_{theme}.png"))
        box.current = 1
        box.pick(box.current)
        assert picked == [towns[1]], picked

        for finder, state in ((lambda q: [], "none"), (offline, "offline")):
            other = TownSearch(pal, picked.append, finder=finder, threaded=False)
            other.field.setText("Zzqx")
            other.q.type("Zzqx")
            other.q.search_now()
            assert other.state == state, (state, other.state)
            if theme == "light":
                other.grab().save(os.path.join(out, f"_townsearch_{state}.png"))
            other.deleteLater()

        card = weather.WeatherWidget(pal, "medium", {})
        card._refresh()
        assert card.needs_town, "a card with no town didn't ask for one"
        assert "town" in card.accessibleDescription().lower(), card.accessibleDescription()
        card.config["faces"] = {"medium": "hourly"}
        assert card.face == "hourly" and not card.tinted, \
            "a card waiting for its town put its button on a sky photo"
        del card.config["faces"]
        img = QImage(card.width(), card.height(), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        card.render(img)
        img.save(os.path.join(out, f"_weather_settown_{theme}.png"))
        btn = card._town_btn
        assert btn is not None and card.town_button_at(btn.center())
        assert not card.town_button_at(QPointF(4, 4)), "the whole card counts as the button"
        got = img.pixelColor(int(btn.left() + 9), int(btn.center().y()))
        want = QColor(pal_mod.button_fill(pal.accent))
        assert max(abs(got.red() - want.red()), abs(got.green() - want.green()),
                   abs(got.blue() - want.blue())) <= 8, (got.name(), want.name())
        card._refresh = lambda: None        # picking a town must not reach the network here
        card.set_town(towns[0])
        assert not card.needs_town and card.config["place"] == "Chicago, US"
        assert (card.config["lat"], card.config["lon"]) == (41.85, -87.65)
        card.deleteLater()
    print("  town search: every match with its region, a single letter not sent, "
          "no match and no connection told apart, an older answer can't overwrite a "
          "newer one; an empty Weather card shows Set your town in the accent's "
          "button shade; renders in _selftest/_townsearch_*.png")


def _check_welcome() -> None:
    """The first-run welcome, offscreen: a veil over every screen with the
    words and buttons on the main one; Start Setup an outline until the pointer
    is over it; Esc is Skip, which leaves the five starter widgets and marks the
    setup done; it waits while a full-screen game has the foreground; a start at
    sign-in never shows it; and the veil darkens a bright wallpaper until white
    text keeps 7:1. Renders to _selftest/_welcome.png and _welcome_hover.png."""
    from PySide6.QtCore import QEvent, QRect, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent
    from PySide6.QtWidgets import QApplication

    from widgets.core import palette as pal_mod
    from widgets.core import store, winapi
    from widgets.core.board import Board
    from widgets.core.card import WidgetCard
    from widgets.ui import welcome as wmod

    white = QImage(320, 180, QImage.Format_RGB32)
    white.fill(QColor("#ffffff"))
    dim = wmod.dim_for(white, white.rect())
    k = round(255 * (1 - dim))
    assert pal_mod.contrast("#ffffff", QColor(k, k, k).name()) >= wmod.WHITE_TEXT_RATIO, dim
    navy = QImage(320, 180, QImage.Format_RGB32)
    navy.fill(QColor("#1f2a44"))
    assert wmod.dim_for(navy, navy.rect()) == wmod.DIM, "an ordinary wallpaper got an extra dim"
    assert wmod.dim_for(None, QRect()) == 1.0

    area = (0, 0, 1920, 1040)
    st = store.first_run_board(area)
    assert wmod.welcome_due(st, True) and st.placements == [], "the welcome opened over widgets"
    st = store.first_run_board(area)
    assert not wmod.welcome_due(st, False) and len(st.placements) == 5, \
        "a start at sign-in should show the starter widgets and no welcome"
    st.placements = []
    assert not wmod.welcome_due(st, False) and len(st.placements) == 5, \
        "an unfinished welcome left the desktop empty at the next sign-in"
    assert not wmod.welcome_due(store.BoardState(), True), "an update was welcomed"

    from widgets.ui import setup_steps
    from widgets.ui.dock import Snapshot

    real_start, real_full = WidgetCard.start, winapi.foreground_is_fullscreen
    real_read = setup_steps.read_desktop
    WidgetCard.start = lambda self: None            # never put a card on screen
    setup_steps.read_desktop = lambda watcher: Snapshot()   # the dock preview's desktop
    out = os.path.join(_write_dir(), "_selftest")
    starter = ["clock", "weather", "system", "disk", "notes"]
    try:
        with _sandboxed_layout():
            state = store.first_run_board(area)
            wmod.welcome_due(state, True)
            board = Board(state)
            w = wmod.Welcome(board, offscreen=True, motion=False, threaded=False)
            winapi.foreground_is_fullscreen = lambda: False
            w.open()
            assert w.state == "ready", w.state
            screens = QGuiApplication.screens()
            assert len(w.stages) == len(screens) and sum(s.main for s in w.stages) == 1
            for stage, screen in zip(w.stages, screens):
                assert stage.geometry() == screen.geometry(), "a veil doesn't cover its screen"
            main = w.main
            shot = main.grab().toImage()
            shot.scaled(shot.width() // 2, shot.height() // 2, Qt.KeepAspectRatio,
                        Qt.SmoothTransformation).save(os.path.join(out, "_welcome.png"))
            b = main.start_btn
            spot = (b.x() + int(b.height() * 0.55), b.y() + b.height() // 2)
            assert not b.filled(), "Start Setup opened filled"
            outline = shot.pixelColor(*spot)
            b.set_hover(True)
            lit = main.grab().toImage()
            assert outline.lightness() < 140 and lit.pixelColor(*spot).lightness() > 240, \
                (outline.name(), lit.pixelColor(*spot).name())
            top = main.title.y() - 30
            lit.copy(QRect(b.x() - 260, top, b.width() + 520,
                           main.skip_btn.y() + main.skip_btn.height() + 30 - top)).save(
                os.path.join(out, "_welcome_hover.png"))
            b.set_hover(False)
            assert b.fill.t == 0.0, "the fill didn't go when the pointer left"

            # With motion the fill eases in over FILL_MS on the frame clock
            # instead of switching, and a reversal half way takes half as long.
            from PySide6.QtCore import QAbstractAnimation
            from PySide6.QtWidgets import QWidget
            probe = QWidget()
            fade = wmod._Fade(probe, wmod.FILL_MS, True)
            fade.to(1.0)
            assert fade.t < 1.0 and fade._tween.state() == QAbstractAnimation.Running, \
                "the hover fill switched instead of fading"
            fade._tween.stop()
            fade.t = 0.5
            fade.to(0.0)
            assert abs(fade._tween._ms - wmod.FILL_MS * 0.5) < 1.0, fade._tween._ms
            fade._tween.stop()
            probe.deleteLater()
            QApplication.sendEvent(main, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
            assert w.state == "closed" and board.state.setup == "done", w.state
            assert [c.kind for c in board.cards] == starter, [c.kind for c in board.cards]
            saved = store.load(area)
            assert saved.setup == "done" and len(saved.placements) == 5, "Skip wasn't saved"
            _close_test_board(board)

        with _sandboxed_layout():
            state = store.first_run_board(area)
            wmod.welcome_due(state, True)
            board = Board(state)
            w = wmod.Welcome(board, offscreen=True, motion=False, glass=False, threaded=False)
            winapi.foreground_is_fullscreen = lambda: True
            w.open()
            assert w.state == "waiting" and not w.stages, "it opened over a full-screen game"
            winapi.foreground_is_fullscreen = lambda: False
            w._retry()
            assert w.state == "ready" and all(s.backdrop is None for s in w.stages), \
                "transparency effects off should give a plain dark screen"
            w.start()
            assert w.state == "steps" and w.main.steps is not None, "Start Setup didn't open the steps"
            assert not board.cards, "a widget appeared before the setup finished"
            w.skip()
            assert w.state == "closed" and board.state.setup == "done"
            assert [c.kind for c in board.cards] == starter
            _close_test_board(board)
    finally:
        WidgetCard.start, winapi.foreground_is_fullscreen = real_start, real_full
        setup_steps.read_desktop = real_read
    print(f"  welcome: a veil over each of {len(QGuiApplication.screens())} screen(s), "
          "Start Setup an outline that fills under the pointer, Esc skips to the "
          "starter widgets and saves the setup as done, waits out a full-screen game, "
          f"never at sign-in; a white wallpaper is dimmed to {dim:.2f} for 7:1 text; "
          "renders in _selftest/_welcome*.png")


def _check_setup_steps() -> None:
    """The first-run steps, offscreen, with the town search and the forecast
    canned. A town picked from several, by the arrow keys and Enter; Weather in
    its three sizes for it, in °F or °C from one fetch; the slideshow moved by
    the keys, the wheel and a click, the real size and Add controls only on the
    settled middle slot; the dock's switches changing the preview and nothing
    else. Done lays the widgets down the right edge exactly as the starter
    board does, with the town, units, theme and dock applied and saved. Skip
    part way keeps what was chosen; on a second run Cancel changes nothing and
    Done touches only what changed. Renders go to _selftest/_setup_*.png."""
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
    from PySide6.QtWidgets import QApplication

    from widgets.core import store, winapi
    from widgets.core.board import Board
    from widgets.core.card import WidgetCard
    from widgets.tiles import weather
    from widgets.ui import setup_steps as ss
    from widgets.ui import welcome as wmod
    from widgets.ui.dock import Snapshot

    area = (0, 0, 1920, 1040)

    # The packer puts the starter five exactly where the starter board does,
    # steps round a card already there, and starts a column to the left when
    # one runs out of height.
    starter = store.default_board(area).placements
    got = ss.pack([(pl.kind, pl.size) for pl in starter], area)
    assert got == [(pl.kind, pl.size, pl.x, pl.y) for pl in starter], got
    top_right = QRectF(1920 - 24 - 168 + 18, 24 + 18, 168, 168)
    got = ss.pack([("clock", "small")], area, [top_right])
    assert got[0][2:] == (1920 - 372, 24), "a new card didn't take the free half beside one"
    got = ss.pack([("weather", "medium")] * 7, area)
    assert {x for _k, _s, x, _y in got} == {1548, 1188}, "a full column didn't carry on leftwards"
    hot = weather.Forecast(place="x", temp=10.0, feels=0.0, hi=20.0, lo=-5.0,
                           days=[weather.Day("Mon", 0, 20.0, -5.0, precip=1.5, pop=40)],
                           hours=[weather.Hour("1 PM", 0, 10.0)])
    f = ss.in_unit(hot, "F")
    assert (f.temp, f.feels, f.days[0].hi, f.days[0].lo) == (50.0, 32.0, 68.0, 23.0)
    assert (f.hi, f.lo, f.hours[0].temp) == (68.0, 23.0, 50.0), "a face's temperatures stayed °C"
    assert (f.days[0].precip, f.days[0].pop) == (1.5, 40), "°F lost the day's rain"

    geo = {"results": [
        {"name": "Chicago", "admin1": "Illinois", "country": "United States",
         "country_code": "US", "latitude": 41.85, "longitude": -87.65},
        {"name": "Chico", "admin1": "California", "country": "United States",
         "country_code": "US", "latitude": 39.73, "longitude": -121.84},
        {"name": "Chicopee", "admin1": "Massachusetts", "country": "United States",
         "country_code": "US", "latitude": 42.15, "longitude": -72.61}]}
    days = [f"2026-11-{d:02d}" for d in range(13, 20)]
    forecast = {"current": {"temperature_2m": 9.0, "apparent_temperature": 6.4,
                            "weather_code": 2},
                "daily": {"time": days, "weather_code": [2, 61, 0, 95, 3, 71, 45],
                          "temperature_2m_max": [11, 9, 13, 10, 8, 3, 6],
                          "temperature_2m_min": [4, 5, 6, 3, 1, -2, 0]}}
    asked = []

    def canned(url, params):
        asked.append(url)
        if url == weather.GEO:
            return geo
        assert params.get("temperature_unit") == "celsius", "one fetch should serve both units"
        return forecast

    def key(widget, k):
        QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))

    def shot(stage, name):
        img = stage.grab().toImage()
        img.scaled(img.width() // 2, img.height() // 2, Qt.KeepAspectRatio,
                   Qt.SmoothTransformation).save(os.path.join(out, name))

    def start_setup(board, **kw):
        w = wmod.Welcome(board, offscreen=True, motion=False, threaded=False, **kw)
        w.open()
        if not kw.get("again"):
            w.start()
        assert w.state == "steps", w.state
        return w, w.main.steps

    out = os.path.join(_write_dir(), "_selftest")
    real = (WidgetCard.start, winapi.foreground_is_fullscreen, weather._get, ss.read_desktop)
    WidgetCard.start = lambda self: None
    winapi.foreground_is_fullscreen = lambda: False
    weather._get = canned
    ss.read_desktop = lambda watcher: Snapshot()
    try:
        with _sandboxed_layout():
            state = store.first_run_board(area)
            wmod.welcome_due(state, True)
            board = Board(state)
            first_theme = board.state.theme
            w, steps = start_setup(board)
            assert steps.skip_btn.text() == "Skip" and not steps.back_btn.isVisible()

            # Step 1: several matches with their regions; the cards follow the
            # highlight; Enter picks; the units come from the region.
            loc = steps.pages[0]
            loc.field.setText("Chic")
            loc.typed("Chic")
            assert loc.query.pending(), "it searched on the first keystroke"
            loc.query.search_now()
            assert [t.name for t in loc.list.towns] == ["Chicago", "Chico", "Chicopee"]
            key(loc.field, Qt.Key_Down)
            assert loc.shown_town().name == "Chico", "the cards didn't follow the highlight"
            loc._fetch_shown()
            assert steps.forecasts.get(loc.shown_town()) is not None
            key(loc.field, Qt.Key_Up)
            shot(w.main, "_setup_location.png")
            key(loc.field, Qt.Key_Return)
            ch = steps.choices
            assert ch.town is not None and ch.town.name == "Chicago" and not loc.list.isVisible()
            assert ch.unit == winapi.temperature_unit(), ch.unit
            loc.units.set_current("C", emit=True)
            assert ch.unit == "C"
            loc.units.set_current("F", emit=True)
            fetches = asked.count(weather.API)
            assert fetches <= 2, f"{fetches} forecast fetches for two towns"
            assert board.state.theme == first_theme and not board.cards, "a choice applied early"

            # Step 2: the slideshow, by keys, the wheel and a click.
            key(steps, Qt.Key_Return)
            assert steps.index == 1 and steps.back_btn.isVisible()
            wp = steps.pages[1]
            kinds = wp.kinds
            assert kinds[:2] == ["clock", "weather"] and "workspace" not in kinds
            assert wp.size.isVisible() and wp.add.isVisible() and wp.add.text() == "Added"
            key(steps, Qt.Key_Right)
            assert wp.kind() == "weather" and wp.size.current == "medium"
            wheel = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            wp.carousel.wheelEvent(wheel)
            assert wp.kind() == "calendar", wp.kind()
            assert wp.add.text() == "Add" and wp.size.current == "small"
            wp.add.click()
            wp.size.set_current("medium", emit=True)
            assert "calendar" in ch.picked and ch.sizes["calendar"] == "medium"
            back = wp.carousel.box(-1.0).center()
            for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                QApplication.sendEvent(wp.carousel, QMouseEvent(
                    kind, back, back, Qt.LeftButton,
                    Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton,
                    Qt.NoModifier))
            assert wp.kind() == "weather", "a click on a neighbour didn't bring it to the middle"
            wp.carousel.go(2)
            other = "light" if ch.theme == "dark" else "dark"
            wp.look.set_current(other, emit=True)
            assert ch.theme == other and board.state.theme == first_theme
            wp.carousel.go(len(kinds) - 1)
            assert not wp.next_.isEnabled() and wp.prev.isEnabled()
            wp.carousel.go(2)
            shot(w.main, "_setup_widgets.png")

            # Step 3: the dock, previewed and not applied.
            steps.forward()
            dp = steps.pages[2]
            assert not dp.look.isEnabled() and not dp.neon.isEnabled(), "dock rows live while off"
            dp.on.click()
            assert ch.dock and dp.look.isEnabled() and dp.dock is not None and dp.dock.width() > 200
            dp.look.set_current("solid", emit=True)
            dp.neon.click()
            assert dp.proxy.state.dock_style == "solid" and dp.proxy.state.dock_neon
            assert not board.state.dock_enabled and board.state.dock_style == "glass"
            img = dp._image()
            assert not img.isNull() and img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 0
            shot(w.main, "_setup_dock.png")

            steps.forward()
            assert steps.index == 3 and not steps.next_btn.isVisible()
            shot(w.main, "_setup_done.png")
            dp_page = steps.pages[3]
            key(dp_page.done, Qt.Key_Return)
            assert w.state == "closed" and board.state.setup == "done", w.state

            want = ["clock", "weather", "system", "disk", "notes", "calendar"]
            assert [c.kind for c in board.cards] == want, [c.kind for c in board.cards]
            rects = [c.card_rect().translated(c.x(), c.y()) for c in board.cards]
            for i, r in enumerate(rects):
                assert 0 <= r.left() and r.right() <= 1920 and 0 <= r.top() and r.bottom() <= 1040
                assert not any(r.intersects(o) for o in rects[i + 1:]), "widgets overlap"
            wcard = next(c for c in board.cards if c.kind == "weather")
            assert wcard.config.get("place") == "Chicago, US" and wcard.config.get("unit") == "F"
            assert next(c for c in board.cards if c.kind == "calendar").size_class == "medium"
            st = board.state
            assert st.theme == other and st.dock_enabled and st.dock_style == "solid" and st.dock_neon
            saved = store.load(area)
            assert saved.setup == "done" and len(saved.placements) == 6 and saved.dock_enabled

            # A second run: Cancel changes nothing at all.
            before = [(c.kind, c.size_class, c.x(), c.y()) for c in board.cards]
            w2, steps2 = start_setup(board, again=True)
            assert steps2.skip_btn.text() == "Cancel" and steps2.choices.town.label == "Chicago, US"
            steps2.set_theme(first_theme)
            steps2.choices.picked.remove("disk")
            w2.skip()
            assert [(c.kind, c.size_class, c.x(), c.y()) for c in board.cards] == before
            assert board.state.theme == other, "Cancel applied a choice"

            # And Done touches only what changed: Storage off, Clock medium,
            # Stocks on in a free spot; the rest stay exactly where they are.
            w3, steps3 = start_setup(board, again=True)
            c3 = steps3.choices
            c3.picked.remove("disk")
            c3.sizes["clock"] = "medium"
            c3.picked.append("stocks")
            steps3.go(3)
            w3.finish()
            kinds_now = [c.kind for c in board.cards]
            assert "disk" not in kinds_now and "stocks" in kinds_now, kinds_now
            assert next(c for c in board.cards if c.kind == "clock").size_class == "medium"
            kept = {(c.kind, c.x(), c.y()) for c in board.cards}
            assert {(k, x, y) for k, s, x, y in before if k not in ("disk", "clock")} <= kept
            assert next(c for c in board.cards if c.kind == "weather").config["place"] == \
                "Chicago, US", "a second run lost the town"
            _close_test_board(board)

        # Skip part way keeps what was chosen so far.
        with _sandboxed_layout():
            state = store.first_run_board(area)
            wmod.welcome_due(state, True)
            board = Board(state)
            w, steps = start_setup(board)
            loc = steps.pages[0]
            loc.field.setText("Chic")
            loc.typed("Chic")
            loc.query.search_now()
            loc.pick(1)
            steps.forward()
            steps.choices.picked.remove("notes")
            key(steps, Qt.Key_Escape)
            assert w.state == "closed" and board.state.setup == "done"
            assert [c.kind for c in board.cards] == ["clock", "weather", "system", "disk"]
            assert next(c for c in board.cards if c.kind == "weather").config["place"] == "Chico, US"
            _close_test_board(board)
    finally:
        WidgetCard.start, winapi.foreground_is_fullscreen, weather._get, ss.read_desktop = real
    print("  setup steps: a town picked from three by the keys, its weather in both units "
          "from one fetch; the slideshow by keys, wheel and click; the dock previewed, not "
          "applied; Done lays the widgets out as the starter board does with the town, "
          "theme and dock saved; Skip keeps what was chosen; on a second run Cancel changes "
          "nothing and Done only what changed; renders in _selftest/_setup_*.png")


def _check_button_shades() -> None:
    """Every white label on a filled accent keeps 4.5:1, on all seven accents
    in light and dark: the app's primary buttons at rest, under the pointer
    and pressed, the green "Added" they flash, and Calendar's today circle. The
    raw accents fail it - blue gives 3.7:1, green 2.3:1 - which is why each of
    these takes the accent's button shade instead (palette.button_fill)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage

    from widgets.core import palette as pal_mod
    from widgets.tiles.calendar import CalendarWidget
    from widgets.ui import theme

    worst = (99.0, "")
    before = theme.current()
    try:
        for name in pal_mod.ACCENTS:
            for mode in ("light", "dark"):
                pal = pal_mod.build(mode, name)
                theme.set_current(theme.build(pal))
                b = theme.Button("Add", "primary", "plus")
                b.resize(b.sizeHint())
                for state in ("rest", "hover", "down", "flash"):
                    b._hover.t = 1.0 if state == "hover" else 0.0
                    b.setDown(state == "down")
                    b._flash = ("Added", "check", theme.current().good) if state == "flash" else None
                    # Inside the capsule, left of the glyph: the fill itself.
                    c = b.grab().toImage().pixelColor(6, b.height() // 2)
                    ratio = pal_mod.contrast("#ffffff", c.name())
                    worst = min(worst, (ratio, f"{name} {mode} {state}"))
                    assert ratio >= 4.5, (name, mode, state, c.name(), round(ratio, 2))
                b.deleteLater()

                card = CalendarWidget(pal, "medium", {})
                img = QImage(card.width(), card.height(), QImage.Format_ARGB32_Premultiplied)
                img.fill(Qt.transparent)
                card.render(img)
                # Today's circle is small and mostly under its number: count the
                # pixels of the button shade, and of the raw accent, which the
                # card otherwise doesn't use.
                shade, raw = QColor(pal_mod.button_fill(pal.accent)), QColor(pal.accent)

                def count(c):
                    return sum(1 for y in range(img.height()) for x in range(img.width())
                               if max(abs(img.pixelColor(x, y).red() - c.red()),
                                      abs(img.pixelColor(x, y).green() - c.green()),
                                      abs(img.pixelColor(x, y).blue() - c.blue())) <= 3)
                hits = count(shade)
                assert hits >= 20, f"today's circle isn't in the button shade ({name} {mode}: {hits})"
                if shade != raw:
                    assert count(raw) < hits // 4, f"today's circle is the raw accent ({name} {mode})"
                card.deleteLater()
    finally:
        theme.set_current(before)
    print(f"  button shades: white labels on filled accents {worst[0]:.2f}:1 or better "
          f"(lowest: {worst[1]}) - primary buttons at rest, hovered, pressed and flashing "
          "Added, and Calendar's today circle, on every accent in light and dark")


def _check_weather_data(pal) -> None:
    """What the weather faces are drawn from, read from a canned response.

    The hourly row, the sun and the next hour are all worked out on the
    forecast place's own clock, so these are checked as strings and minutes
    rather than against the PC's time; and one missing series must not cost
    the card its whole forecast.
    """
    import copy
    import json
    from widgets.core import samples
    from widgets.tiles import weather as w

    raw = samples.weather_response()
    fc = w.parse(raw, "London, GB", "C")
    labels = [h.label for h in fc.hours]
    assert labels[:3] == ["3 PM", "4 PM", "4:13 PM"], labels[:4]
    assert fc.hours[2].event == w.SUNSET and not fc.hours[2].is_day
    rise = [h for h in fc.hours if h.event == w.SUNRISE]
    assert [h.label for h in rise] == ["7:13 AM"], rise
    # 24 hours, the sunset today and tomorrow's sunrise; tomorrow's sunset
    # falls after the last hour and stays out.
    assert len(fc.hours) == 26 and sum(1 for h in fc.hours if not h.event) == 24
    assert (fc.now_min, fc.sunrise_min, fc.sunset_min) == (930, 432, 973)
    assert (fc.sunrise_next, fc.sunset_next) == ("7:13 AM", "4:13 PM")
    assert (fc.hi, fc.lo, fc.wind, fc.wind_unit) == (22, 14, 14.0, "km/h")
    assert fc.days[1].precip == 4.2 and fc.days[1].pop == 90
    assert fc.next_hour == [0, 0, 0.1, 0.3, 0.4], fc.next_hour

    def said(vals, code=2, unit="mm"):
        f = w.Forecast(code=code, next_hour=vals, rain_unit=unit)
        return w.next_hour_summary(f)

    assert said(fc.next_hour) == "Rain starting in 15 min"
    assert said([0, 0, 0, 0, 0.3]) == "Rain starting in 45 min"
    assert said([0.2, 0.3, 0.3, 0.2, 0.1]) == "Rain for the next hour"
    assert said([0.2, 0.3, 0, 0, 0]) == "Rain stopping in 15 min"
    assert said([0.2, 0.3, 0.2, 0.01, 0]) == "Rain stopping in 30 min"
    assert said([0, 0.01, 0.02, 0, 0]) == "No rain for the next hour"
    assert said([0.2] * 5, code=73) == "Snow for the next hour"
    assert said([0, 0.004, 0.004, 0.004, 0.004], unit="in") == "Rain for the next hour"
    assert said([]) == ""

    q = w.query(1, 2, "F")
    assert (q["temperature_unit"], q["wind_speed_unit"],
            q["precipitation_unit"]) == ("fahrenheit", "mph", "inch")
    assert w.parse(raw, "", "F").wind_unit == "mph"

    # A series missing, or no current time at all, leaves the rest standing.
    thin = copy.deepcopy(raw)
    del thin["hourly"]["weather_code"], thin["minutely_15"], thin["daily"]["sunset"]
    got = w.parse(thin, "x")
    assert len(got.hours) == 25 and got.next_hour == [] and got.sunset_min is None
    assert len(got.days) == 7 and got.temp == 21.0
    del thin["current"]["time"]
    got = w.parse(thin, "x")
    assert got.hours == [] and got.temp == 21.0 and len(got.days) == 7

    # The face: remembered per size, a face that isn't built draws Classic,
    # and the choice is plain JSON in the card's config.
    card = w.WeatherWidget(pal, "small", {})
    assert card.face == "classic" and card.available_faces()[0] == ("classic", "Classic")
    card.set_face("nope")                        # not a face: ignored
    assert "faces" not in card.config
    card.config["faces"] = {"small": "nope"}
    assert card.face == "classic", "an unbuilt face was drawn"
    built = {"classic": card._paint_classic, "conditions": card._paint_classic,
             "hourly": card._paint_classic}
    card._face_painters = lambda: built
    saved = []
    card.changed.connect(lambda: saved.append(1))
    card.config["faces"] = {"small": "conditions"}
    card.set_face("conditions")
    assert card.face == "conditions" and not saved, "re-choosing saved again"
    card.set_size_class("medium")
    assert card.face == "classic"
    card.set_face("hourly")
    assert card.config["faces"] == {"small": "conditions", "medium": "hourly"}
    assert saved, "choosing a face did not save"
    card.set_size_class("small")
    assert card.face == "conditions", "the small face was forgotten"
    json.dumps(card.config)
    card.deleteLater()
    print("  weather data: hourly row with sunset 4:13 PM and sunrise 7:13 AM in "
          "place, sun on the place's clock, next hour 'Rain starting in 15 min', "
          "°F brings mph and inches, missing series leave the rest; faces kept "
          "per size, unbuilt ones draw Classic")


def _check_weather_faces(out: str) -> None:
    """Every weather face paints, in both themes, from good data and from the
    awkward kind: no next-hour readings, no sunrise (the polar winter), no
    daily forecast. Painted straight onto an image rather than through Qt's
    paint event, which would print an exception and carry on."""
    import copy
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QMenu
    from widgets.core import palette as pal_mod, samples
    from widgets.core.card import PAD
    from widgets.tiles import weather as w

    fc = w.parse(samples.weather_response(), "London, GB")
    assert w.precip_summary(fc) == "0.6 mm today", w.precip_summary(fc)
    fc2 = copy.deepcopy(fc)
    fc2.days[0].precip = 0
    assert w.precip_summary(fc2) == "4.2 mm tomorrow"
    for d in fc2.days:
        d.precip = 0
    assert w.precip_summary(fc2) == "None for 7d"
    fc2.days[3].precip, fc2.rain_unit = 0.25, "in"
    assert w.precip_summary(fc2) == "0.25 in Sat", w.precip_summary(fc2)
    bars = w.rain_levels(fc, 15)
    assert bars[0] == 0 and bars == sorted(bars) and 0 < bars[-1] < 1, bars
    fc2.next_hour = [9, 9, 9, 9, 9]
    assert max(w.rain_levels(fc2, 15)) == 1.0, "a downpour overflowed the chart"
    assert w._deg(-0.3) == "0°" and w._clock_min(973) == "4:13 PM"
    assert w.spoken(fc) == ("London, 21°, Partly cloudy. High 22°, low 14°. "
                            "Rain starting in 15 min. Sunset at 4:13 PM."), w.spoken(fc)

    # The sky tint: worst case is a white wallpaper showing through it.
    def lum(c):
        def ch(v):
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())

    def over(fg, bg, a):
        return QColor(*(round(f * a + b * (1 - a)) for f, b in
                        ((fg.red(), bg.red()), (fg.green(), bg.green()),
                         (fg.blue(), bg.blue()))))

    def ratio(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    white = QColor("#ffffff")
    dim_alpha = QColor(w.SKY_DIM).alphaF()
    skies = list(w._SKY.values()) + [w._SKY_NIGHT, w._SKY_NIGHT_CLOUD]
    worst = {"text": 99.0, "secondary": 99.0, "bars": 99.0}
    for pair in skies:
        for hexc in pair:
            bg = over(QColor(hexc), white, w.SKY_ALPHA)
            worst["text"] = min(worst["text"], ratio(white, bg))
            worst["secondary"] = min(worst["secondary"],
                                     ratio(over(white, bg, dim_alpha), bg))
            worst["bars"] = min(worst["bars"], ratio(QColor(w.SKY_BAR), bg))
    assert worst["text"] >= 4.5 and worst["secondary"] >= 4.5 and worst["bars"] >= 3.0, worst

    # The sky photographs under their sky colour, as the faces draw them at
    # large size: measured over the brightest 2% of each composite.
    from PySide6.QtCore import QRectF as _R
    from PySide6.QtGui import QLinearGradient
    photo_worst = 99.0
    photo_dim = QColor(w.PHOTO_DIM).alphaF()
    for shape, name in w.SHAPE_FILES.items():
        if w.backdrop(shape) is None:
            continue
        for day in (True, False):
            probe = w.WeatherWidget(pal_mod.build("dark", "blue"), "large",
                                    {"faces": {"large": "daily"}})
            img = QImage(312, 312, QImage.Format_ARGB32)
            img.fill(QColor("#ffffff"))
            p = QPainter(img)
            p.drawImage(0, 0, probe._full_photo(shape, 312, 312))
            top, bottom = w.sky_tint(shape, day)
            grad = QLinearGradient(0, 0, 0, 312)
            grad.setColorAt(0, QColor(top))
            grad.setColorAt(1, QColor(bottom))
            p.setOpacity(w.photo_sky_alpha(shape, day))
            p.fillRect(_R(0, 0, 312, 312), grad)
            p.end()
            probe.deleteLater()
            ls = sorted(lum(img.pixelColor(x, y)) for y in range(0, 312, 4)
                        for x in range(0, 312, 4))
            hi = ls[int(0.98 * (len(ls) - 1))]
            text = 1.05 / (hi + 0.05)
            second = (photo_dim + (1 - photo_dim) * hi + 0.05) / (hi + 0.05)
            assert text >= 4.5 and second >= 4.4, (name, day, round(text, 2), round(second, 2))
            photo_worst = min(photo_worst, text, second)

    def broken(f):
        f.next_hour, f.sunrise_min, f.sunset_min, f.days = [], None, None, []
        f.hi = f.lo = f.wind = None

    def night(f):
        f.now_min, f.is_day = 22 * 60, False

    wrote = 0
    for theme in ("dark", "light"):
        pal = pal_mod.build(theme, "blue")
        for size in w.WeatherWidget.supported_sizes:
            card = w.WeatherWidget(pal, size, {})
            faces = card.available_faces()
            assert [k for k, _ in faces] == [k for k, _ in w.WeatherWidget.FACES[size]], (
                f"{size}: a listed face isn't built: {faces}")
            if size == "small":
                assert [k for k, _ in faces] == ["classic", "conditions",
                                                 "forecast", "nexthour", "sun"]
                menu = QMenu()
                card.context_actions(menu)
                # One call to menu(): a second wrapper for the same submenu
                # can take the C++ object with it when the first is dropped.
                face_action = next(a for a in menu.actions() if a.text() == "Face")
                sub = face_action.menu()
                assert [a.text() for a in sub.actions()][1:] == [
                    "Conditions", "Forecast", "Next hour", "Sunrise and sunset"]
                del sub, menu
            for key, _name in faces:
                card.config["faces"] = {size: key}
                for tweak in (None, night, broken):
                    samples.weather(card)
                    if tweak:
                        tweak(card.data)
                    img = QImage(card.width(), card.height(),
                                 QImage.Format_ARGB32_Premultiplied)
                    img.fill(QColor("#2b3440" if theme == "dark" else "#c9d2dd"))
                    p = QPainter(img)
                    r = card.card_rect().adjusted(PAD, PAD, -PAD, -PAD)
                    try:
                        card._face_painters()[key](p, r)
                    finally:
                        p.end()
                    if tweak is None and key != "classic":
                        card.render(img)
                        img.save(os.path.join(out, f"{theme}_weather_{size}_{key}.png"))
                        wrote += 1
            card.deleteLater()
    print(f"  weather faces: every face paints in both themes, at night and with "
          f"no next hour, sun or daily data; precipitation 'None for 7d', bars "
          f"capped; text on every sky photo at least {photo_worst:.1f}:1 over its "
          f"brightest 2%, and without photos over a white wallpaper text "
          f"{worst['text']:.1f}:1, secondary {worst['secondary']:.1f}:1, bars "
          f"{worst['bars']:.1f}:1; screen readers get a sentence; {wrote} renders "
          f"in _selftest/*_weather_*_<face>.png")


def _check_updater() -> None:
    """Finding, trusting and downloading an update, with GitHub canned.

    Windows' own crypto signs and checks; a changed byte, another key or
    garbage never passes. An update file is believed only after its signature
    checks out, and even then only if it names a newer version, this
    repository's own installer for it, a sane size and a SHA-256. A download
    is the length promised and nothing more, or it leaves no file. The
    updater fetches nothing in a checkout, reuses an installer it already has,
    throws away one that doesn't match, and checks again a day later - or
    three hours after a failure."""
    import hashlib
    import io
    import json
    import tempfile
    import threading
    import urllib.error

    from widgets.core import net, signature, updater as up

    private, public = signature.generate()
    assert signature.public_of(private) == public
    sig = signature.sign(private, b"PC Widgets")
    assert signature.verify(public, b"PC Widgets", sig)
    assert not signature.verify(public, b"PC Widgetz", sig), "a changed message passed"
    assert not signature.verify(signature.generate()[1], b"PC Widgets", sig), "another key passed"
    for bad_key, bad_sig in (("", sig), ("not base64!", sig), (private, sig), (public, ""),
                             (public, "AAAA"), (public, sig[:-4] + "AAAA")):
        assert not signature.verify(bad_key, b"PC Widgets", bad_sig), (bad_key[:8], bad_sig[:8])

    assert up.parse_version("1.0.4") == (1, 0, 4) and up.parse_version("v1.2") == (1, 2, 0)
    for junk in ("", "1.0.4-beta", "1.0.4.1", "one", None, "1..2"):
        assert up.parse_version(junk) is None, junk
    assert up.newer("1.0.10", "1.0.9") and up.newer("1.1", "1.0.9") and not up.newer("1.0.2", "1.0.2")
    assert not up.newer("1.0.1", "1.0.2") and not up.newer("junk", "1.0.2")
    assert up.canonical({"b": 1, "a": "é"}) == b'{"a":"\\u00e9","b":1}'

    payload = bytes(range(256)) * 8192                      # 2 MB: an "installer"
    digest = hashlib.sha256(payload).hexdigest()

    def update_file(version="1.0.4", key=private, **changes):
        manifest = {"version": version, "url": up.installer_url(version),
                    "size": len(payload), "sha256": digest}
        manifest.update(changes)
        return json.dumps({"manifest": manifest,
                           "signature": signature.sign(key, up.canonical(manifest))}).encode()

    good = up.read_update(update_file(), public)
    assert (good.version, good.size, good.sha256) == ("1.0.4", len(payload), digest)
    assert good.url.startswith(f"https://github.com/{up.REPO}/releases/download/v1.0.4/")
    tampered = json.loads(update_file())
    tampered["manifest"]["version"] = "9.9.9"
    refused = {
        "no key in the build": (update_file(), ""),
        "a manifest changed after signing": (json.dumps(tampered).encode(), public),
        "another key's signature": (update_file(key=signature.generate()[0]), public),
        "somewhere else's installer": (update_file(url="https://example.com/x.exe"), public),
        "another version's installer": (update_file(url=up.installer_url("1.0.3")), public),
        "a tiny installer": (update_file(size=10), public),
        "a checksum that isn't one": (update_file(sha256="xyz"), public),
        "a version that isn't one": (update_file(version="1.0.4-rc"), public),
        "not JSON": (b"<html>", public),
    }
    for what, (raw, key) in refused.items():
        try:
            up.read_update(raw, key)
        except up.UpdateError:
            continue
        raise AssertionError(f"read_update accepted {what}")

    folder = tempfile.mkdtemp(prefix="pcwidgets-update-")
    try:
        dest = os.path.join(folder, "x.bin")
        assert net.save_stream(io.BytesIO(payload), dest, 10 ** 8, len(payload)) == digest
        assert open(dest, "rb").read() == payload
        os.remove(dest)
        for what, kw in (("short", {"size": len(payload) + 1}), ("long", {"size": len(payload) - 1}),
                         ("past the cap", {"limit": 1000})):
            args = {"limit": 10 ** 8, "size": None}
            args.update(kw)
            try:
                net.save_stream(io.BytesIO(payload), dest, args["limit"], args["size"])
            except OSError:
                assert not os.path.exists(dest) and not os.path.exists(dest + ".part"), what
                continue
            raise AssertionError(f"a {what} download was kept")
        stop = threading.Event()
        stop.set()
        try:
            net.save_stream(io.BytesIO(payload), dest, 10 ** 8, None, stop=stop)
            raise AssertionError("a stopped download was kept")
        except OSError:
            assert not os.path.exists(dest + ".part")

        # What the release workflow writes (tools/sign_update.py), read back
        # the way an installed copy reads it.
        installer = os.path.join(folder, "PC-Widgets-Setup-1.0.4.exe")
        with open(installer, "wb") as fh:
            fh.write(payload)
        made = up.read_update(up.make_update_file(installer, "1.0.4", private), public)
        assert made == good, (made, good)
        if up.PUBLIC_KEY:
            assert signature.public_of(private) != up.PUBLIC_KEY
            signature._blob(up.PUBLIC_KEY, signature.PUBLIC_MAGIC, 2)   # raises if it isn't one
            try:
                up.read_update(up.make_update_file(installer, "1.0.4", private))
                raise AssertionError("the app's own key accepted another key's update")
            except up.UpdateError:
                pass

        now = [1_000_000.0]
        served = {"file": update_file(), "body": payload, "fetches": 0, "downloads": 0}

        def fetch(url, headers=None, limit=0):
            served["fetches"] += 1
            assert url == up.UPDATE_URL and limit == up.MANIFEST_LIMIT
            assert headers["User-Agent"].startswith("pc-widgets/")
            if isinstance(served["file"], Exception):
                raise served["file"]
            return served["file"]

        def download(url, path, limit, size, progress, stop, headers=None):
            served["downloads"] += 1
            assert url == up.installer_url("1.0.4")
            return net.save_stream(io.BytesIO(served["body"]), path, limit, size, progress, stop)

        def updater(**kw):
            args = dict(enabled=True, folder=os.path.join(folder, "updates"), current="1.0.3",
                        public_key=public, fetch=fetch, download=download,
                        clock=lambda: now[0], threaded=False)
            args.update(kw)
            return up.Updater(**args)

        off = updater(enabled=False)
        off.check()
        assert off.status == "off" and served["fetches"] == 0, "a checkout looked for updates"
        # On by itself exactly when installed: this same test runs from a
        # checkout and inside the built exe (tools/build_exe.py).
        from widgets.core import paths
        assert up.Updater(threaded=False).enabled == paths.FROZEN, \
            "the updater's default doesn't follow installed versus checkout"

        u = updater()
        assert u.due()
        u.check()
        assert u.status == "ready" and u.version == "1.0.4" and served["downloads"] == 1, u.detail
        assert u.progress == (len(payload), len(payload))
        version, path = u.ready_installer()
        assert version == "1.0.4" and open(path, "rb").read() == payload
        assert not u.due(), "it checked again straight away"
        now[0] += up.CHECK_EVERY + 1
        assert u.due(), "a day later it didn't check"

        again = updater()                           # the next run: still ready
        assert again.status == "ready" and again.version == "1.0.4"
        again.check()
        assert served["downloads"] == 1, "an installer already here was downloaded again"

        with open(path, "r+b") as fh:               # changed on disk since
            fh.write(b"MZ")
        assert again.ready_installer() is None and again.status == "failed"

        served["body"] = payload[:-1] + b"!"        # GitHub serves the wrong bytes
        bad = updater()
        bad.check()
        assert bad.status == "failed" and "match" in bad.detail, bad.detail
        assert not any(n.startswith("PC-Widgets-Setup-") for n in os.listdir(bad.folder))

        served["file"] = update_file(version="1.0.3")
        same = updater()
        same.check()
        assert same.status == "up_to_date", same.status

        served["file"] = update_file(key=signature.generate()[0])
        before = served["downloads"]
        forged = updater()
        forged.check()
        assert forged.status == "failed" and served["downloads"] == before, \
            "an update signed by another key was downloaded"

        served["file"] = urllib.error.URLError("no route")
        lost = updater()
        lost.check()
        assert lost.status == "offline" and not lost.due()
        now[0] += up.RETRY_AFTER + 1
        assert lost.due(), "after a failure it didn't try again three hours later"

        served["file"] = urllib.error.HTTPError(up.UPDATE_URL, 404, "Not Found", None, None)
        missing = updater()
        missing.check()
        assert missing.status == "failed" and "no update file" in missing.detail
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("  updater: Windows' ECDSA P-256 signs and checks, and a changed byte, another key "
          "or garbage never passes; an update is believed only once signed, newer, this "
          "repository's own installer, a sane size and a SHA-256; downloads are the length "
          "promised or leave nothing; off in a checkout, reuses what it has, drops what "
          "changed or doesn't match, checks daily and three hours after a failure")


def _check_update_install() -> None:
    """Handing over to Setup, and settling up afterwards, with Setup's start
    recorded instead of run. The installer that was checked starts silently,
    told how to reopen the app, and nothing else is ever started; at the next
    start the app knows whether it's now the new version. A version that
    didn't install isn't started again by itself - only from Restart to update
    - so a failing update can't loop; and none of it happens in a checkout or
    with automatic updates off. (tools/build_exe.py runs a real update.)"""
    import hashlib
    import json
    import tempfile

    from widgets.core import updater as up

    folder = tempfile.mkdtemp(prefix="pcwidgets-install-")
    started = []
    now = [5_000_000.0]
    try:
        updates = os.path.join(folder, "updates")
        os.makedirs(updates)
        setup = os.path.join(updates, "PC-Widgets-Setup-1.0.4.exe")
        body = b"MZ" + bytes(4096)
        with open(setup, "wb") as fh:
            fh.write(body)

        def ready(**extra):
            state = {"ready": {"version": "1.0.4", "path": setup,
                               "sha256": hashlib.sha256(body).hexdigest()}}
            state.update(extra)
            with open(os.path.join(updates, "state.json"), "w", encoding="utf-8") as fh:
                json.dump(state, fh)

        def updater(current="1.0.3", spawn=None, **kw):
            return up.Updater(enabled=kw.pop("enabled", True), folder=updates, current=current,
                              public_key="unused", spawn=spawn or started.append,
                              clock=lambda: now[0], threaded=False, **kw)

        def state():
            with open(os.path.join(updates, "state.json"), encoding="utf-8") as fh:
                return json.load(fh)

        ready()
        u = updater()
        assert u.status == "ready" and u.auto
        try:
            u.apply("--anything")
            raise AssertionError("an unknown way to reopen was accepted")
        except ValueError:
            pass
        assert up.install_at_start(True, u) and u.status == "installing"
        args = started[-1]
        assert args[0] == setup and args[1:5] == list(up.SETUP_ARGS), args
        assert args[5] == "/RELAUNCH=background" and args[6].startswith("/LOG=")
        assert len(args) == 7, "Setup got something more on its command line"
        assert state()["installing"]["version"] == "1.0.4"

        took = updater(current="1.0.4")             # Setup reopened the new version
        took.finish_install()
        assert took.status == "updated" and took.version == "1.0.4"
        assert "installing" not in state() and "ready" not in state()
        assert not os.path.exists(setup), "the installer that ran was kept"
        assert updater(current="1.0.4").status == "updated"

        with open(setup, "wb") as fh:
            fh.write(body)
        ready(installing={"version": "1.0.4", "from": "1.0.3", "at": now[0]})
        stuck = updater()                           # reopened, and still the old one
        assert not up.install_at_start(False, stuck), "a failed update was started again by itself"
        assert stuck.status == "failed" and "didn't install" in stuck.detail
        assert state()["skip"] == "1.0.4" and len(started) == 1
        assert updater().apply("window", manual=True), "Restart to update couldn't try again"
        assert started[-1][5] == "/RELAUNCH=window"

        ready()
        with open(setup, "r+b") as fh:
            fh.write(b"XX")                         # changed since it was checked
        count = len(started)
        assert not updater().apply("window") and len(started) == count, \
            "an installer that changed on disk was started"

        with open(setup, "wb") as fh:
            fh.write(body)
        ready()

        def refuse(_args):
            raise OSError("access denied")
        broke = updater(spawn=refuse)
        assert not broke.apply("window") and broke.status == "failed"
        assert "installing" not in state(), "a Setup that never started was recorded as running"

        ready()
        off = updater()
        off.set_auto(False)
        count = len(started)
        assert not up.install_at_start(True, updater()) and len(started) == count, \
            "it installed with automatic updates off"
        assert not up.install_at_start(True, updater(enabled=False)), "a checkout installed"
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("  update install: the checked installer starts silently, told how to reopen and "
          "nothing more; the next start knows whether it took; a failed version isn't "
          "started again by itself, only from Restart to update; a changed installer, a "
          "Setup that won't start, automatic updates off and a checkout all start nothing")


def _check_update_ui() -> None:
    """What the updater shows: Settings › App's Updates group and the tray's
    one tip. Every state has its words and the one button that fits; a copy
    run from its folder says updates come with the installed app, with both
    controls resting; in an installed copy (GitHub canned) Check now finds an
    update and the button becomes Restart to update; the switch is kept; and
    the tray says an update is ready once a version. Renders go to
    _selftest/_app_updates_*.png."""
    import datetime
    import hashlib
    import json
    import tempfile
    import types

    from PySide6.QtCore import Qt

    from widgets.core import net, signature, store
    from widgets.core import palette as pal_mod
    from widgets.core import updater as up
    from widgets.core.board import Board
    from widgets.ui import theme
    from widgets.ui.app import MainWindow

    noon = datetime.datetime(2026, 11, 14, 15, 40).timestamp()
    assert up._when(noon, noon + 60) == "today at 3:40 PM"
    assert up._when(noon - 86400, noon) == "yesterday"
    assert up._when(noon - 3 * 86400, noon) == "on 11 Nov"

    private, public = signature.generate()
    payload = b"MZ" + bytes(2 * 1024 * 1024)
    manifest = {"version": "1.0.4", "url": up.installer_url("1.0.4"), "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}
    signed = json.dumps({"manifest": manifest,
                         "signature": signature.sign(private, up.canonical(manifest))}).encode()
    now = [noon]
    out = os.path.join(_write_dir(), "_selftest")
    folder = tempfile.mkdtemp(prefix="pcwidgets-updateui-")
    before = theme.current()
    try:
        def updater(**kw):
            args = dict(enabled=True, folder=os.path.join(folder, "updates"), current="1.0.3",
                        public_key=public, threaded=False, clock=lambda: now[0],
                        fetch=lambda url, headers=None, limit=0: signed,
                        download=lambda url, path, limit, size, progress, stop, headers=None:
                            net.save_stream(__import__("io").BytesIO(payload), path, limit, size,
                                            progress, stop),
                        spawn=lambda args: None)
            args.update(kw)
            return up.Updater(**args)

        assert up.describe(None).startswith("Updates come with the installed app")
        assert up.action(None) == ("Check now", False)
        u = updater()
        assert up.describe(u) == "It checks for updates once a day." and up.action(u) == ("Check now", True)
        u.status, u.progress = "downloading", (512, 1024)
        assert up.describe(u) == "Downloading the update… 50%" and up.action(u) == ("Checking…", False)
        u.status = "idle"

        with _sandboxed_layout():
            for mode in ("light", "dark"):
                board = Board(store.BoardState(theme=mode))
                for timer in (board._autosave, board._watch, board._glass_timer, board._park):
                    timer.stop()
                theme.set_current(theme.build(pal_mod.build(mode, "blue")))

                plain = MainWindow(board)                   # a copy run from its folder
                page = plain.settings
                assert not page.auto_update.isEnabled() and not page.update_btn.isEnabled()
                assert page.version_row.desc.text().startswith("Updates come with the installed app")

                shutil.rmtree(os.path.join(folder, "updates"), ignore_errors=True)
                u = updater()
                win = MainWindow(board, types.SimpleNamespace(updater=u))
                page = win.settings
                page.on_show()
                assert page.auto_update.isEnabled() and page.auto_update.isChecked()
                assert page.update_btn.text() == "Check now" and page.update_btn.isEnabled()
                page.update_btn.click()                     # Check now
                assert u.status == "ready", (u.status, u.detail)
                assert page.update_btn.text() == "Restart to update"
                assert "1.0.4 is ready" in page.version_row.desc.text(), page.version_row.desc.text()
                assert u.tip_due(), "the tray wouldn't say the update is ready"
                u.tipped()
                assert not u.tip_due() and not updater().tip_due(), "the tray would say it twice"
                page.updates_group.grab().save(os.path.join(out, f"_app_updates_{mode}.png"))
                page.auto_update.click()
                assert not u.auto and not updater().auto, "switching automatic updates off wasn't kept"
                page.auto_update.click()
                assert updater().auto
                # The windows aren't deleted: the board's signals still reach
                # them, as _check_app_window's, and a deleted one would be
                # repainted when the glass lands.
                _close_test_board(board)

        stale = updater()                                   # a later run, last check a day old
        stale._state.pop("ready", None)
        stale.status = "up_to_date"
        now[0] = noon + 86400
        assert up.describe(stale).startswith("PC Widgets is up to date. Checked yesterday"), \
            up.describe(stale)
    finally:
        theme.set_current(before)
        shutil.rmtree(folder, ignore_errors=True)
    print("  update settings: each state has its words and its one button; a copy run from "
          "its folder rests both controls; Check now finds an update and becomes Restart to "
          "update; the switch is kept; the tray tip comes once a version; renders in "
          "_selftest/_app_updates_*.png")


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
    from widgets.core import store
    from widgets.core.board import Board
    from widgets.tiles.clock import ClockWidget

    with _sandboxed_layout():
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
def run(open_window: bool = True) -> int:
    import threading

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from widgets.core import startup, store, winapi
    from widgets.core.board import Board, load_builtin_tiles
    from widgets.tray import WidgetTray

    winapi.set_app_id(APP_ID)           # before any window exists
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("PC Widgets")
    from widgets.version import VERSION
    app.setApplicationVersion(VERSION)
    if startup.icon_path():
        app.setWindowIcon(QIcon(startup.icon_path()))

    load_builtin_tiles()
    state = store.load(winapi.work_area())
    # A fresh install opened the ordinary way starts with no widgets: the
    # welcome ends by putting them up. Started at sign-in, it gets the starter
    # widgets and the welcome waits for the window (widgets/ui/welcome.py).
    from widgets.ui.welcome import welcome_due
    welcome_due(state, open_window)
    board = Board(state)
    board.build()

    tray = WidgetTray(board, app)
    tray.install_hotkey(HOTKEY_ID, winapi.MOD_CONTROL | winapi.MOD_ALT, VK_W)
    app.aboutToQuit.connect(board.persist)
    # The daily look for an update, in an installed copy only; Settings and
    # the tray read it from the tray.
    from widgets.core.updater import Updater, schedule
    tray.watch_updates(Updater(app))
    if tray.updater.enabled:
        schedule(tray.updater)
    if open_window:
        QTimer.singleShot(0, tray.show_window)

    # A Startup shortcut made before the app had a window would open it at
    # every boot. Checking costs a file read; fixing it, once, is a PowerShell
    # call - so off the GUI thread.
    threading.Thread(target=startup.refresh_startup_link, daemon=True).start()

    print(f"{len(board.cards)} widgets on the board. "
          f"Ctrl+Alt+W brings them to the front. Tray icon to open the app.")
    return app.exec()


def _knock() -> bool:
    """Ask the copy that is already running to open its window.

    Retried for a few seconds: a launch right behind another can arrive while
    that board is still building and has no window to receive the knock yet.
    """
    import time

    from widgets.core import winapi
    msg = winapi.register_message(winapi.SHOW_MESSAGE)
    for _ in range(10):
        if winapi.signal_window(winapi.CONTROL_TITLE, msg):
            return True
        time.sleep(0.3)
    return False


def _helper(mode: str) -> int:
    """Run as a helper process: nothing of the board, and no Qt.

    The guard has to be up before the dock hides the taskbar, and the media
    helper is started every few seconds, so neither waits for anything else.
    """
    # A windowed exe starts with no sys.stdin or sys.stdout even when the parent
    # handed it pipes; the pipes are still descriptors 0 and 1.
    for name, fd, how in (("stdin", 0, "r"), ("stdout", 1, "w")):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, os.fdopen(fd, how, encoding="utf-8"))
            except OSError:
                pass
    # sys.argv is the same as when they run as scripts: the mode, then its
    # arguments.
    if mode == "--guard":
        from widgets.core import dock_shell
        dock_shell._guard(*dock_shell.guard_args(sys.argv))
        return 0
    from widgets.core import winrt_read
    return winrt_read._main()


def _logged_selftest() -> int:
    """The self test, from the installed exe.

    The exe has no console, so the report would go nowhere, and an exception
    ends in an error dialog rather than a traceback. Both go to
    ``_selftest/selftest.log`` in the write directory instead, and the exit
    code says whether it passed.
    """
    import traceback

    out = os.path.join(_write_dir(), "_selftest")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "selftest.log"), "w", encoding="utf-8") as log:
        sys.stdout = sys.stderr = log
        try:
            return selftest()
        except BaseException:
            traceback.print_exc()
            return 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in HELPER_MODES:
        return _helper(sys.argv[1])
    if len(sys.argv) > 1 and sys.argv[1] == "--install-update":
        # Install the update that's downloaded and checked, now, then reopen
        # as asked (background, window or selftest): the build's own test of
        # an update. Only ever the installer the updater verified.
        from widgets.core import updater
        how = sys.argv[2] if len(sys.argv) > 2 else "window"
        return 0 if how in updater.RELAUNCH and updater.Updater().apply(how, manual=True) else 1
    args = set(sys.argv[1:])
    if "--selftest" in args:
        from widgets.core import paths
        return _logged_selftest() if paths.FROZEN else selftest()
    # The shortcut flags combine - the README's "fix all three in one go" is
    # `--app --shortcut --install-startup` - so each one runs, then it exits.
    shortcut_flags = {"--install-startup", "--startup", "--autostart",
                      "--remove-startup", "--shortcut", "--app", "--start-menu"}
    if args & shortcut_flags:
        from widgets.core import startup
        ok = True
        if args & {"--install-startup", "--startup", "--autostart"}:
            done = startup.enable()
            ok &= done
            print(f"start with Windows: {'on' if done else 'FAILED'}"
                  f"  ({startup.startup_link()})")
        if "--remove-startup" in args:
            startup.disable()
            print("start with Windows: off")
        if "--shortcut" in args:
            path = startup.create_desktop_shortcut()
            ok &= bool(path)
            print(f"desktop shortcut: {path or 'FAILED'}")
        if "--app" in args:
            path = startup.create_app_shortcut()
            ok &= bool(path)
            print(f"launcher in folder: {path or 'FAILED'}")
        if "--start-menu" in args:
            path = startup.create_start_menu_shortcut()
            ok &= bool(path)
            print(f"start menu: {path or 'FAILED'}")
        return 0 if ok else 1
    if "--reset" in args:
        from widgets.core import store, winapi
        # A reset forgets the layout, not the presets saved from it.
        fresh = store.default_board(winapi.work_area())
        fresh.presets = store.load().presets
        store.save(fresh)
        print("layout reset; presets kept." if fresh.presets else "layout reset.")
        return 0
    background = bool(args & BACKGROUND_FLAGS)
    if _already_running():
        if not background and _knock():
            print("PC Widgets is already running - opened its window.")
        else:
            print("PC Widgets is already running.")
        return 0
    # An installed copy with a checked update waiting installs it now, before
    # anything is on screen; Setup reopens it the same way (AUTO-UPDATE.md).
    from widgets.core import updater
    if updater.install_at_start(background):
        print("Installing an update - PC Widgets reopens when it's done.")
        return 0
    return run(open_window=not background)


if __name__ == "__main__":
    sys.exit(main())
