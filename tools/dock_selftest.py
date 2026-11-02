"""Glass Dock checks. Isolated state: never loads or writes the user's board.

    py -3.13 tools/dock_selftest.py --out _selftest/dock
    py -3.13 tools/dock_selftest.py --windows
    py -3.13 tools/dock_selftest.py --live

The default run puts nothing on screen: renders are offscreen grabs, and the
crash-guard test uses a tool window parked off every monitor, which has no
taskbar button either. --windows opens two ordinary test windows (they flash
in the taskbar) to check that real apps get buttons. --live shows the dock
itself and hides and restores the real taskbar.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ["PCWIDGETS_NO_AUDIO"] = "1"

from PySide6.QtCore import QFileInfo, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileIconProvider, QWidget
from widgets.core import audio, dock_shell as shell, media, palette, store, winapi
from widgets.core.glass import GlassBackdrop
from widgets.ui import dock as dockmod
from widgets.ui.dock import Entry, GlassDock, Snapshot, Watcher, paint_windows_logo

NOTEPAD = r"C:\Windows\System32\notepad.exe"


class TestBoard(QObject):
    prefs_changed = Signal()
    glass_changed = Signal()
    displays_changed = Signal()

    def __init__(self):
        super().__init__()
        self.state = store.BoardState()
        self.palette = palette.build()
        self.glass = GlassBackdrop()
        self.glass.refresh()
        self.dock = None

    def set_dock(self, key, value):
        setattr(self.state, "dock_" + key, value)
        self.prefs_changed.emit()


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def fake_snapshot():
    """Running apps with real shell pictures, one with none at all, and a pin."""
    tasks = [shell.Task(101, "Untitled - Notepad", NOTEPAD),
             shell.Task(102, "notes.txt - Notepad", NOTEPAD, minimised=True),
             shell.Task(103, "Downloads", r"C:\Windows\explorer.exe",
                        "Microsoft.Windows.Explorer"),
             shell.Task(104, "Some tool", r"C:\Tools\tool.exe")]
    tasks += [shell.Task(200 + i, f"App {i}", rf"C:\Apps\app{i}.exe") for i in range(6)]
    snap = Snapshot(tasks=tasks, network="ethernet", speakers=(0.42, False),
                    battery=winapi.Battery(True, 76, False, 140))
    for t in tasks:
        snap.names[t.app] = {"exe:" + os.path.normcase(NOTEPAD): "Notepad"}.get(
            t.app, "File Explorer" if t.aumid else t.title)
    for key, name in ((shell.app_key("", NOTEPAD), NOTEPAD),
                      (shell.app_key("Microsoft.Windows.Explorer"),
                       "shell:AppsFolder\\Microsoft.Windows.Explorer")):
        snap.icons[key] = shell.shell_image(name, 32)
    return snap


def check_renders(out, board):
    shell.com_thread()
    dock = GlassDock(board, preview=True)
    snap = fake_snapshot()
    dock._snap = snap
    dock._fg = 101
    art = QImage(300, 300, QImage.Format_ARGB32_Premultiplied)
    art.fill(QColor("#d94f8a"))
    tracks = {"playing": media.NowPlaying(title="A song with a long title that should elide cleanly",
                                          artist="The artist", playing=True, available=True, art=art),
              "idle": media.NowPlaying()}
    count = 0
    for mode in ("glass", "solid"):
        board.state.dock_style = mode
        for theme in ("dark", "light"):
            board.palette = palette.build(theme)
            for side in ("left", "right"):
                board.state.dock_music_side = side
                for width in (560, 760, 1100):
                    for name, np in tracks.items():
                        dock._np = np
                        dock._listening = np.playing
                        dock._levels = (0.3, 0.75, 1.0, 0.6, 0.25)
                        dock.sync()
                        dock.resize(width, 64)
                        dock.invalidate()
                        pix = dock.grab()
                        assert not pix.isNull()
                        assert pix.save(str(out / f"{mode}-{theme}-{side}-{width}-{name}.png"))
                        count += 1
                        rects = [(i, r) for i, r, _ in dock._items]
                        cap = dock._capsule
                        for n, (ident, r) in enumerate(rects):
                            assert cap.contains(r), (width, side, ident, r, cap)
                            assert all(not r.intersects(o) for _, o in rects[n + 1:]), (width, ident)
                        music = next(r for i, r in rects if i == "music")
                        assert music.contains(dock._bars), (width, dock._bars, music)
                        idents = [i for i, _ in rects]
                        assert idents.count("start") == 1 and "clock" in idents
                        if width < 900:
                            assert "more" in idents, "a narrow dock keeps every app reachable"
    # Type and symbols in greyscale: ClearType on the transparent dock showed
    # as red and blue edges on every letter.
    board.state.dock_style, board.state.dock_color = "solid", "#202020"
    board.palette = palette.build("dark")
    dock.sync()
    dock.resize(1100, 64)
    dock.invalidate()
    img = dock.grab().toImage()
    for ident, r, _ in dock._items:
        if ident in ("tray", "quick", "clock"):
            spread = max(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue())
                         for c in (img.pixelColor(x, y) for x in range(int(r.left()), int(r.right()))
                                   for y in range(int(r.top()), int(r.bottom()))))
            assert spread <= 8, f"colour fringes on {ident}: {spread}"
    board.state.dock_style, board.state.dock_color = "glass", ""
    dock.close()
    print(f"PASS: {count} renders; light/dark, glass/solid, music left/right, narrow to wide, "
          "playing and idle; buttons inside the glass and apart; type without colour fringes")


def check_neon(out, board):
    """The neon fits inside the window, runs at an even pace round the
    outline, moves when it should and only then, and is off by default."""
    import numpy as np
    assert store.BoardState().dock_neon is False
    dock = GlassDock(board, preview=True)
    dock._snap = fake_snapshot()
    board.state.dock_neon = True
    dock.sync()
    dock.resize(1000, 74)
    dock.invalidate()
    assert dock._capsule.top() == dockmod.NEON_TOP, "no room above the capsule for the glow"
    frame = lambda motion, t, colours=("#00e5ff", "#ff2bd6"), strength=100: dock._neon.frame(
        dock.size(), dock._capsule, dockmod.RADIUS, 1.0, list(colours), motion, strength, t)
    img = frame("static", 0.0)
    top = max(img.pixelColor(x, 0).alpha() for x in range(img.width()))
    assert top <= 12, f"the glow is cut off at the window's top edge ({top})"
    assert frame("static", 0.0) is frame("static", 2.0), "a still ring was drawn again"
    a, b = frame("circulate", 0.0), frame("circulate", dockmod.LAP_S / 4)
    assert a != b, "circulate did not move"
    f1, f2 = frame("fade", 0.0), frame("fade", dockmod.FADE_S / 2)
    assert f1.pixelColor(500, int(dock._capsule.top())).alpha() < \
        f2.pixelColor(500, int(dock._capsule.top())).alpha(), "fade did not brighten"
    cap = dock._capsule
    xs = np.linspace(cap.left() + 30, cap.right() - 30, 50)
    u = dockmod._perimeter(np, xs, np.full_like(xs, cap.top()), cap, 18.0)
    steps = np.diff(u)
    assert (steps > 0).all() and steps.max() / steps.min() < 1.01, "uneven pace along the edge"
    t0 = time.perf_counter()
    for i in range(20):
        frame("circulate", i * 0.033)
    ms = (time.perf_counter() - t0) / 20 * 1000
    for motion in ("static", "fade", "circulate"):
        board.state.dock_neon_motion = motion
        dock.invalidate()
        assert dock.grab().save(str(out / f"neon-{motion}.png"))
    board.state.dock_neon, board.state.dock_neon_motion = False, "circulate"
    dock.sync()
    assert dock._capsule.top() == dockmod.TOP
    dock.close()
    print(f"PASS: neon fits the window, keeps an even pace round the outline, circulates and "
          f"fades, holds still when static ({ms:.1f} ms a moving frame)")


def check_pictures():
    """The shell's pictures come out the right way up, with their transparency."""
    img = shell.shell_image(NOTEPAD, 32)
    assert img is not None and img.size().width() == 32
    alpha = [img.pixelColor(x, y).alpha() for x in range(32) for y in range(32)]
    assert min(alpha) == 0 and max(alpha) == 255, "lost the alpha channel"
    ref = QFileIconProvider().icon(QFileInfo(NOTEPAD)).pixmap(32, 32).toImage().convertToFormat(
        QImage.Format_ARGB32_Premultiplied)

    def diff(a, b):
        return sum(abs(a.pixelColor(x, y).alpha() - b.pixelColor(x, y).alpha())
                   for x in range(32) for y in range(32))
    upright, flipped = diff(img, ref), diff(img.flipped(Qt.Vertical), ref)
    assert upright < flipped, ("upside down", upright, flipped)
    print(f"PASS: shell icons keep transparency and orientation (difference {upright} upright "
          f"vs {flipped} flipped against Qt's own)")


def check_logo(out):
    """The Start logo matches the one Windows ships, where Windows has it."""
    svg = Path(r"C:\Windows\SystemApps\MicrosoftWindows.Client.Core_cw5n1h2txyewy\Icons"
               r"\WindowsLogo.svg")
    ours = QImage(230, 230, QImage.Format_ARGB32_Premultiplied)
    ours.fill(Qt.transparent)
    p = QPainter(ours)
    p.setRenderHint(QPainter.Antialiasing)
    paint_windows_logo(p, QPointF(115, 115), 230)
    p.end()
    ours.save(str(out / "start-logo.png"))
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        QSvgRenderer = None
    if not svg.exists() or QSvgRenderer is None:
        print("SKIP: Windows' own logo is not on this machine to compare against")
        return
    theirs = QImage(240, 240, QImage.Format_ARGB32_Premultiplied)    # 12 units; logo 11.5
    theirs.fill(Qt.transparent)
    p = QPainter(theirs)
    QSvgRenderer(str(svg)).render(p, QRectF(0, 0, 240, 240))
    p.end()
    worst = 0
    for x in range(4, 226, 3):
        for y in range(4, 226, 3):
            a, b = ours.pixelColor(x, y), theirs.pixelColor(x, y)
            if a.alpha() > 250 and b.alpha() > 250:
                worst = max(worst, abs(a.red() - b.red()), abs(a.green() - b.green()),
                            abs(a.blue() - b.blue()))
    assert worst <= 6, f"colour differs from Windows' logo by {worst}"
    print(f"PASS: Start logo matches Windows' own WindowsLogo.svg (worst channel difference {worst})")


def check_rows(board):
    """Pins first and shared with their windows; web apps stay apart; clicks."""
    from unittest.mock import patch
    dock = GlassDock(board, preview=True)
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    tasks = [shell.Task(1, "Inbox - Chrome", chrome, "Chrome"),
             shell.Task(2, "Music - web app", chrome, "Chrome._crx_abc"),
             shell.Task(3, "Notes", NOTEPAD),
             shell.Task(4, "Notes 2", NOTEPAD)]
    pin = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Google Chrome.lnk"
    board.state.dock_pins = [pin, "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"]
    dock._snap = Snapshot(tasks=tasks, pins={pin: (shell.app_key("Chrome"), "Chrome", chrome)})
    dock._rebuild()
    keys = [(e.key, [t.hwnd for t in e.windows]) for e in dock._entries]
    assert keys[0] == (shell.app_key("Chrome"), [1]), keys
    assert keys[1][1] == [] and keys[1][0].startswith("id:microsoft.windowscalculator"), keys
    assert [k for k, _ in keys[2:]] == [shell.app_key("Chrome._crx_abc"),
                                        shell.app_key("", NOTEPAD)], keys
    notes = dock._entries[3]
    calls = []
    with patch.object(shell, "activate", lambda h: calls.append(("activate", h))), \
            patch.object(shell, "minimise", lambda h: calls.append(("minimise", h))), \
            patch.object(shell, "foreground", lambda: 3):
        dock._open(notes)               # in front: the next of its windows
        dock._open(dock._entries[0])    # behind: brought forward
    with patch.object(shell, "activate", lambda h: calls.append(("activate", h))), \
            patch.object(shell, "minimise", lambda h: calls.append(("minimise", h))), \
            patch.object(shell, "foreground", lambda: 1):
        dock._open(dock._entries[0])    # its only window is in front: minimised
    assert calls == [("activate", 4), ("activate", 1), ("minimise", 1)], calls

    # The taskbar's pins come first, in its order; one also pinned in the dock
    # shows once; one removed from the dock stays off it, the taskbar untouched.
    code = r"C:\Apps\Code\Code.lnk"
    board.state.dock_pins = [pin]
    dock._snap = Snapshot(tasks=tasks, taskbar_pins=[code, pin], pins={
        pin: (shell.app_key("Chrome"), "Chrome", chrome),
        code: (shell.app_key("Microsoft.VisualStudioCode"), "Microsoft.VisualStudioCode", "")})
    dock._rebuild()
    assert [e.pin for e in dock._entries[:2]] == [code, pin], [e.pin for e in dock._entries]
    assert sum(1 for e in dock._entries if e.pin == pin) == 1
    dock._unpin(code)
    dock._rebuild()
    assert board.state.dock_hidden == [code] and code not in [e.pin for e in dock._entries]
    dock._pin(code)
    assert board.state.dock_hidden == []
    board.state.dock_pins = []
    dock.close()
    print("PASS: the taskbar's pins lead in its order without duplicates, removing one leaves "
          "the taskbar alone; pins own their app's windows, a web app keeps its own button, "
          "clicks cycle, bring forward and minimise")


def check_native():
    assert isinstance(shell.tasks(), list)
    assert shell.network() in ("ethernet", "wifi", "cellular", "offline")
    speakers = shell.Speakers()
    try:
        level = speakers.read()
        assert level is None or (0.0 <= level[0] <= 1.0 and isinstance(level[1], bool))
    finally:
        speakers.close()
    pins = shell.taskbar_pins()
    assert all(p.startswith("shell:AppsFolder\\") or p[1:3] == ":\\" for p in pins), pins
    assert shell.overflow_window() == 0 or shell.u.IsWindowVisible(shell.overflow_window())
    w = Watcher(shell.own_aumid())
    snap = w.read()
    assert snap.signature() == w.read().signature(), "a quiet desktop reads the same twice"
    assert snap.taskbar_pins == pins and all(p in snap.pins for p in pins)
    print(f"PASS: native reads - {len(snap.tasks)} app window(s), {len(pins)} taskbar pin(s), "
          f"{shell.network()}, speakers {level}")


def check_watcher(app):
    """The real worker thread: a snapshot arrives on the GUI thread, pictures
    included, and the window-event hooks go in and come out cleanly."""
    import threading
    got = []
    watcher = Watcher(shell.own_aumid())
    watcher.ready.connect(lambda snap: got.append((snap, threading.current_thread())))
    events = shell.WindowEvents(lambda event, hwnd: None)
    try:
        watcher.start()
        end = time.monotonic() + 10
        while not got and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        assert got, "no snapshot from the watcher thread"
        snap, thread = got[0]
        assert thread is threading.main_thread(), "snapshot delivered off the GUI thread"
        assert snap.network in dockmod.NETWORK_NAME
        missing = [t.title for t in snap.tasks if t.pid != os.getpid() and
                   snap.icons.get(t.app) is None]
        assert not missing, f"no picture for {missing}"
    finally:
        watcher.stop()
        events.close()
    print(f"PASS: watcher thread hands {len(snap.tasks)} app window(s) with pictures to the GUI "
          "thread; window-event hooks install and remove")


def check_guard(app):
    """A guard must restore even when the process owning its pipe is killed.
    The window is a tool window parked off every monitor: nothing to see."""
    window = QWidget(None, Qt.Tool | Qt.FramelessWindowHint)
    window.setAttribute(Qt.WA_ShowWithoutActivating)
    window.setGeometry(-32000, -32000, 40, 40)
    window.show()
    pump(app, 0.1)
    hwnd = int(window.winId())
    script = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,sys.argv[1],'--guard',sys.argv[2]],"
        "stdin=subprocess.PIPE,creationflags=0x08000000); "
        "print('ready',flush=True); time.sleep(30)"
    )
    parent = subprocess.Popen([sys.executable, "-c", script, shell.__file__, str(hwnd)],
                              stdout=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert parent.stdout.readline().strip() == b"ready"
        shell.u.ShowWindow(hwnd, 0)
        assert not shell.u.IsWindowVisible(hwnd)
        parent.kill()
        parent.wait(timeout=3)
        pump(app, 1.5)
        assert shell.u.IsWindowVisible(hwnd), "guard did not restore after parent termination"
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.stdout.close()
        window.close()
    print("PASS: taskbar guard restores a hidden window after its parent is killed")


def check_store():
    with tempfile.TemporaryDirectory() as tmp:
        original = store._both_paths
        original_dir, original_mirror = store._dir, store._mirror_dir
        store._dir = lambda: tmp
        store._mirror_dir = lambda: tmp
        store._both_paths = lambda: [str(Path(tmp) / "board.json")]
        try:
            state = store.BoardState()
            state.dock_enabled = True
            state.dock_pins = ["example.exe", "shell:AppsFolder\\Some.App_1234!App"]
            assert store.save(state, allow_empty=True)
            loaded = store.load()
            assert loaded.dock_enabled and loaded.dock_pins == state.dock_pins
            Path(tmp, "board.json").write_text('{"theme":"dark"}', encoding="utf-8")
            assert not store.load().dock_enabled
        finally:
            store._both_paths, store._dir, store._mirror_dir = original, original_dir, original_mirror
    print("PASS: dock preferences roundtrip and old layouts default to dock off")


def check_positions():
    from dataclasses import replace
    from unittest.mock import patch
    from main import _sandboxed_layout, _fake_displays, _desk
    from widgets.core.board import Board, load_builtin_tiles
    from widgets.core.card import WidgetCard
    load_builtin_tiles()
    primary, secondary = _desk()
    reduced = replace(primary, work=(0, 0, 1920, 992))
    placements = [store.Placement("clock", "small", 1600, 846, display=primary.key,
                                  area=list(primary.work)),
                  store.Placement("clock", "small", -700, 900, display=secondary.key,
                                  area=list(secondary.work))]
    with _sandboxed_layout(), _fake_displays([primary, secondary]) as desk, \
            patch.object(WidgetCard, "show"), patch.object(Board, "apply_layer"):
        board = Board(store.BoardState(placements=placements))
        board.build()
        before = [(c.x(), c.y()) for c in board.cards]
        for _ in range(3):
            for screen in (reduced, primary):
                desk["screens"] = [screen, secondary]
                # Simulate the OS clamping a window before delivering its
                # work-area notification; the board must undo that nudge too.
                board.cards[0].move(1600, 800)
                board._displays_moved()
                board._reassert_spots()
                board.persist()
                assert [(c.x(), c.y()) for c in board.cards] == before
                assert [(p.x, p.y) for p in store.load().placements] == before
        desk["screens"] = [reduced, secondary]
        board._displays_moved()
        board.close_all()
        saved = store.load()
        desk["screens"] = [primary, secondary]
        restarted = Board(saved)
        restarted.build()
        assert [(c.x(), c.y()) for c in restarted.cards] == before, "restart shifted widgets"
        restarted.close_all()
    print("PASS: widget coordinates and saved positions survive repeated work-area changes and restart")


def check_listening():
    """The dock shares Now Playing's capture and opens it only while a track
    plays: a paused or idle dock must not hold the speakers open."""
    from unittest.mock import patch
    initial = audio._users, audio._listeners
    card_tap = audio.acquire()          # a music card already holds the capture
    b = TestBoard()
    with patch.object(GlassDock, "_refresh"), patch.object(GlassDock, "_poll"), \
            patch.object(Watcher, "start"), patch.object(shell, "WindowEvents"):
        dock = GlassDock(b)
        try:
            for _ in range(3):
                b.set_dock("enabled", True)
                assert dock._tap is card_tap, "dock opened a second audio capture"
                assert audio._listeners == initial[1], "listening with nothing playing"
                dock._on_track(media.NowPlaying(title="x", playing=True, available=True))
                assert audio._listeners == initial[1] + 1
                dock._on_track(media.NowPlaying(title="x", playing=False, available=True))
                assert audio._listeners == initial[1], "still listening after pause"
                dock._on_track(media.NowPlaying(title="x", playing=True, available=True))
                b.set_dock("enabled", False)
                assert (audio._users, audio._listeners) == (initial[0] + 1, initial[1])
        finally:
            dock.close()
            audio.release()
    assert (audio._users, audio._listeners) == initial
    print("PASS: one shared capture, open only while a track plays, balanced across on/off")


def check_fullscreen(app):
    """Fullscreen: the dock slides off the edge; the pointer resting on the
    bottom edge brings it up, leaving lets it go, and a hidden pointer (a game
    holding the mouse) never does. Nothing reaches the screen: the window is
    never mapped, and the work area and taskbar are left alone."""
    from unittest.mock import patch
    b = TestBoard()
    b.state.dock_replace_taskbar = False
    g = app.primaryScreen().geometry()
    full, maxed = [None], [False]
    pointer = [(g.center().x(), g.center().y(), True)]
    with patch.object(GlassDock, "_poll"), patch.object(Watcher, "start"), \
            patch.object(shell, "WindowEvents"), patch.object(shell.WorkArea, "reserve"), \
            patch.object(winapi, "fullscreen_monitor", lambda: full[0]), \
            patch.object(shell, "maximised_on", lambda hwnds, monitor: maxed[0]), \
            patch.object(shell, "cursor", lambda: pointer[0]):
        dock = GlassDock(b)
        dock.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            b.set_dock("enabled", True)
            up_y = dock.y()
            assert dock.isVisible()
            maxed[0] = True
            dock._refresh()
            pump(app, 0.5)
            assert not dock.isVisible(), "stayed up over a maximised window"
            pointer[0] = (g.center().x(), g.y() + g.height() - 1, False)
            pump(app, 0.6)
            assert not dock.isVisible(), "came up for a hidden (captured) pointer"
            pointer[0] = (g.center().x(), g.y() + g.height() - 1, True)
            pump(app, 0.6)
            assert dock.isVisible() and dock.y() == up_y, "the bottom edge did not bring it up"
            dock._refresh()
            assert dock.isVisible(), "the 1 s refresh sent it back down under the pointer"
            pointer[0] = (g.center().x(), g.center().y(), True)
            pump(app, 1.2)
            assert not dock.isVisible(), "did not go back down when the pointer left"
            maxed[0] = False
            dock._refresh()
            pump(app, 0.5)
            assert dock.isVisible() and dock.y() == up_y, "did not come back after the window"
            full[0] = (g.x(), g.y(), g.x() + g.width(), g.y() + g.height())
            dock._refresh()
            pump(app, 0.5)
            assert not dock.isVisible(), "stayed up over a fullscreen app"
            full[0] = None
            dock._refresh()
            pump(app, 0.5)
            assert dock.isVisible(), "did not come back after fullscreen"
        finally:
            dock.close()
    print("PASS: a maximised or fullscreen window sends the dock below the edge; resting on the "
          "edge brings it up, leaving lets it down, a captured pointer never does, and it "
          "returns afterwards")


def check_rehide(app):
    """Explorer re-shows a hidden auto-hiding taskbar when the pointer touches
    the bottom edge, the same move that brings the dock up over a maximised
    window. The dock must hide it again, there too, but leave alone a bar
    raised on purpose for its tray. A stand-in lease: the real bar is never
    touched."""
    from unittest.mock import patch

    class Lease:
        hwnd = 4242

        def __init__(self):
            self.calls = []

        def hide(self):
            self.calls.append("hide")
            return True

        def rehide(self):
            self.calls.append("rehide")

        def reveal(self):
            self.calls.append("reveal")

        def restore(self):
            self.calls.append("restore")

    b = TestBoard()
    b.state.dock_replace_taskbar = True
    with patch.object(GlassDock, "_poll"), patch.object(Watcher, "start"), \
            patch.object(shell, "WindowEvents"), patch.object(shell.WorkArea, "reserve"), \
            patch.object(winapi, "fullscreen_monitor", lambda: None), \
            patch.object(shell, "maximised_on", lambda hwnds, monitor: True):
        dock = GlassDock(b)
        dock.setAttribute(Qt.WA_DontShowOnScreen)
        lease = dock.lease = Lease()
        try:
            b.set_dock("enabled", True)
            assert "hide" in lease.calls, "a maximised window stopped the dock hiding the taskbar"
            lease.calls.clear()
            dock._window_event(shell.EVENT_SHOW, Lease.hwnd)
            assert lease.calls == ["rehide"], "Explorer showing the bar was not undone"
            lease.calls.clear()
            dock._window_event(shell.EVENT_SHOW, 777)
            assert "rehide" not in lease.calls, "hid a window that is not the taskbar"
            dock._tray_since = time.monotonic()
            lease.calls.clear()
            dock._window_event(shell.EVENT_SHOW, Lease.hwnd)
            dock._refresh()
            assert not {"hide", "rehide"} & set(lease.calls), "hid the bar raised for its tray"
            dock._tray_since = 0.0
            b.state.dock_replace_taskbar = False
            lease.calls.clear()
            dock._window_event(shell.EVENT_SHOW, Lease.hwnd)
            assert "rehide" not in lease.calls, "hid the bar with 'replace the taskbar' off"
        finally:
            dock.close()
    print("PASS: Explorer re-showing the hidden taskbar is undone, over maximised windows too, "
          "but not when it was raised for its tray or the dock sits beside it")


def check_windows(app, board, out):
    """Real app windows get buttons with their own icons; tool windows do not."""
    windows = []
    try:
        for name, colour in (("Dock app test A", "#26cc70"), ("Dock app test B", "#ff7518")):
            w = QWidget()
            w.setWindowTitle(name)
            pix = QPixmap(32, 32)
            pix.fill(QColor(colour))
            w.setWindowIcon(QIcon(pix))
            w.show()
            windows.append(w)
        tool = QWidget(None, Qt.Tool)
        tool.setWindowTitle("Dock tool test")
        tool.show()
        windows.append(tool)
        windows[1].showMinimized()
        pump(app, 0.2)
        found = {t.hwnd: t for t in shell.tasks(shell.own_aumid())}
        assert {int(w.winId()) for w in windows[:2]} <= set(found), "visible/minimised apps missing"
        assert int(tool.winId()) not in found, "tool window leaked into the app list"
        assert found[int(windows[1].winId())].minimised
    finally:
        for w in windows:
            w.close()
    print("PASS: real app windows (shown and minimised) are listed; tool windows are not")


def check_live(app, board, out):
    board.state.dock_enabled = True
    board.state.dock_pins = []
    board.state.dock_style = "glass"
    board.state.dock_music_side = "left"
    board.palette = palette.build()
    native = shell.taskbar()
    was_visible = bool(shell.u.IsWindowVisible(native))
    old_work = winapi.work_area()
    live = GlassDock(board)
    try:
        pump(app, 3)
        assert live.isVisible()
        if was_visible:
            assert live.lease.hwnd, "could not acquire the native taskbar"
            assert not shell.u.IsWindowVisible(native)
        assert winapi.work_area() == old_work, "the dock took screen space it moves out of"
        live.grab().save(str(out / "live.png"))
        board.set_dock("enabled", False)
        pump(app, 0.3)
        assert not live.isVisible()
        if was_visible:
            assert shell.u.IsWindowVisible(native), "native taskbar was not restored"
        assert winapi.work_area() == old_work, "work area was not restored"
    finally:
        live.shutdown()
        live.close()
    print("PASS: live dock shows, reserves its space, and gives the taskbar and work area back")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "_selftest" / "dock"))
    parser.add_argument("--windows", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    board = TestBoard()
    check_pictures()
    check_logo(out)
    check_renders(out, board)
    check_neon(out, board)
    check_rows(board)
    check_native()
    check_watcher(app)
    check_store()
    check_guard(app)
    check_positions()
    check_listening()
    check_fullscreen(app)
    check_rehide(app)
    if args.windows:
        check_windows(app, board, out)
    if args.live:
        check_live(app, board, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
