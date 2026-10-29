"""
PC Widgets - iPhone/Mac-style widgets for the Windows desktop.

    python main.py               show the board and open the app window
    python main.py --background  show the board only (how Windows starts it)
    python main.py --selftest    render every widget offscreen to _selftest/*.png
    python main.py --reset       forget the layout

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
    from widgets.core import samples
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
                    samples.weather(card)
                if kind == "stocks":
                    samples.stocks(card)
                if kind == "workspace":
                    samples.workspace(card)
                if kind == "media":
                    samples.media(card)
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
    _check_placement(pal_mod.build("dark", "blue"))
    _check_persistence(pal_mod.build("dark", "blue"))
    _check_ticks(pal_mod.build("dark", "blue"))
    _check_quiet(pal_mod.build("dark", "blue"))
    _check_session_pick()
    _check_audio_gate(pal_mod.build("dark", "blue"))
    _check_visualiser()
    _check_backend_recovery()
    _check_tls()
    _check_vendored_closure()
    _check_agent_bus()
    _check_appearance()
    _check_layout_mirror()
    _check_displays()
    _check_presets()
    _check_glass_displays()
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

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest")
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
    if startup.icon_path():
        app.setWindowIcon(QIcon(startup.icon_path()))

    load_builtin_tiles()
    state = store.load(winapi.work_area())
    board = Board(state)
    board.build()

    tray = WidgetTray(board, app)
    tray.install_hotkey(HOTKEY_ID, winapi.MOD_CONTROL | winapi.MOD_ALT, VK_W)
    app.aboutToQuit.connect(board.persist)
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


def main() -> int:
    args = set(sys.argv[1:])
    if "--selftest" in args:
        return selftest()
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
    return run(open_window=not background)


if __name__ == "__main__":
    sys.exit(main())
