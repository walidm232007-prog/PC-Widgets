"""Glass Dock checks. Isolated state: never loads or writes the user's board.

    py -3.13 tools/dock_selftest.py --out _selftest/dock
    py -3.13 tools/dock_selftest.py --live

--live shows the dock briefly and verifies native taskbar restoration. The
default tests the crash guard against a disposable window, not Explorer.
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

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QIcon, QPixmap, QColor
from PySide6.QtWidgets import QApplication, QWidget
from widgets.core import audio, dock_shell as shell, media, palette, store, winapi
from widgets.core.glass import GlassBackdrop
from widgets.ui.dock import GlassDock


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


def check_apps(app, board, out):
    """Real Qt/Win32 windows, not a mocked task list or a mere list type check."""
    from unittest.mock import patch
    windows = []
    dock = GlassDock(board, preview=True)
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
        pump(app, 0.1)
        dock._refresh_tasks()
        dock._warm_icons()
        found = {t.hwnd for t in dock._tasks}
        assert {int(w.winId()) for w in windows[:2]} <= found, "visible/minimized apps missing"
        assert int(tool.winId()) not in found, "tool window leaked into app list"
        dock.resize(720, 64)
        with patch.object(shell, "window_icon", wraps=shell.window_icon) as icon_read:
            dock.grab().save(str(out / "real-apps.png"))
            assert not icon_read.called, "paint queried native icons instead of the cache"
        hits = {h[1] for h in dock._hits}
        for w in windows[:2]:
            hwnd = int(w.winId())
            assert f"task:{hwnd}" in hits, "running app missing from painted buttons"
            path = next(t.path for t in dock._tasks if t.hwnd == hwnd)
            icon = dock._icon(path, hwnd)
            assert not icon.isNull(), "native window icon not loaded"
            assert icon.pixmap(32, 32).toImage().pixelColor(16, 16).green() > 30
        windows[0].close()
        pump(app, 0.1)
        dock._refresh_tasks()
        assert int(windows[0].winId()) not in {t.hwnd for t in dock._tasks}
    finally:
        dock.close()
        for w in windows:
            w.close()
    print("PASS: actual visible/minimized apps have buttons and native icons; tools excluded; closed apps removed")


def check_shared_audio():
    from unittest.mock import patch
    initial = audio._users, audio._listeners
    # A playing music card already holds this capture when the dock opens.
    card_tap = audio.acquire()
    audio.listen(True)
    b = TestBoard()
    with patch.object(GlassDock, "_refresh"), patch.object(GlassDock, "_poll"):
        dock = GlassDock(b)
        try:
            for _ in range(3):
                b.set_dock("enabled", True)
                assert dock._tap is card_tap, "dock opened a second audio capture"
                dock.sync()
                assert (audio._users, audio._listeners) == (initial[0] + 2, initial[1] + 2)
                b.set_dock("enabled", False)
                dock.shutdown()
                assert (audio._users, audio._listeners) == (initial[0] + 1, initial[1] + 1)
        finally:
            dock.close()
            audio.listen(False)
            audio.release()
    assert (audio._users, audio._listeners) == initial
    print("PASS: dock and media share capture; repeated enable/disable balances listeners without stopping the card")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "_selftest" / "dock"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    board = TestBoard()
    dock = GlassDock(board, preview=True)
    dock._np = media.NowPlaying(title="A song with a long title that should elide cleanly",
                               artist="The artist", playing=True, available=True)
    dock._levels = (0.3, 0.75, 1.0, 0.6, 0.25)
    dock._tasks = [shell.Task(i, f"App {i}", "") for i in range(1, 20)]
    for mode in ("glass", "solid"):
        board.state.dock_style = mode
        for theme in ("dark", "light"):
            board.palette = palette.build(theme)
            for side in ("left", "right"):
                board.state.dock_music_side = side
                for width in (640, 720, 980):
                    dock.resize(width, 64)
                    dock.invalidate()
                    pix = dock.grab()
                    assert not pix.isNull()
                    assert pix.save(str(out / f"{mode}-{theme}-{side}-{width}.png"))
                    rects = [h[0] for h in dock._hits]
                    for i, rect in enumerate(rects):
                        assert rect.left() >= 0 and rect.right() <= width, (width, side, rect)
                        assert all(not rect.intersects(other) for other in rects[i + 1:]), (width, side)
    dock.close()
    print("PASS: 24 renders; light/dark, glass/solid, left/right, compact/normal/wide; no overlapping targets")

    # Store roundtrip in a temporary directory, including legacy defaults.
    with tempfile.TemporaryDirectory() as tmp:
        original = store._both_paths
        original_dir, original_mirror = store._dir, store._mirror_dir
        store._dir = lambda: tmp
        store._mirror_dir = lambda: tmp
        store._both_paths = lambda: [str(Path(tmp) / "board.json")]
        try:
            board.state.dock_enabled = True
            board.state.dock_pins = ["example.exe"]
            assert store.save(board.state, allow_empty=True)
            loaded = store.load()
            assert loaded.dock_enabled and loaded.dock_pins == ["example.exe"]
            Path(tmp, "board.json").write_text('{"theme":"dark"}', encoding="utf-8")
            assert not store.load().dock_enabled
        finally:
            store._both_paths, store._dir, store._mirror_dir = original, original_dir, original_mirror
    print("PASS: dock preferences roundtrip and old layouts default to dock off")

    # A guard must restore even when the process owning its pipe is terminated.
    window = QWidget()
    window.setWindowTitle("Glass Dock restoration test")
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
    print("PASS: taskbar guard restores a hidden test window after parent is killed")
    assert isinstance(shell.tasks(), list)
    print("PASS: native app enumeration")
    board.state.dock_pins = []
    check_apps(app, board, out)
    check_positions()
    check_shared_audio()

    if args.live:
        board.state.dock_enabled = True
        board.state.dock_pins = []
        board.state.dock_style = "glass"
        board.state.dock_music_side = "left"
        board.palette = palette.build()
        native = shell.u.FindWindowW("Shell_TrayWnd", None)
        was_visible = bool(shell.u.IsWindowVisible(native))
        old_work = winapi.work_area()
        live = GlassDock(board)
        try:
            pump(app, 3)
            assert live.isVisible()
            if was_visible:
                assert live.lease.hwnd, "could not acquire the native taskbar"
                assert not shell.u.IsWindowVisible(native)
                assert winapi.work_area()[3] <= live.y() + 8, (
                    "maximised windows would overlap the dock", winapi.work_area(), live.geometry())
            live.grab().save(str(out / "live.png"))
            board.set_dock("enabled", False)
            pump(app, 0.2)
            assert not live.isVisible()
            if was_visible:
                assert shell.u.IsWindowVisible(native), "native taskbar was not restored"
                assert winapi.work_area() == old_work, "work area was not restored"
            board.set_dock("enabled", True)
            pump(app, 0.2)
            live.close()
            if was_visible:
                assert shell.u.IsWindowVisible(native)
        finally:
            live.shutdown()
        print("PASS: live dock enable/disable/re-enable/close and Explorer taskbar restoration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
