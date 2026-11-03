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


def check_neon_bars(out, board):
    """The visualiser copies the neon outline when asked, and stays the accent
    by default."""
    from types import SimpleNamespace
    from widgets.core.board import Board
    assert store.BoardState().dock_bars == "default"
    fake = SimpleNamespace(state=store.BoardState(), persist=lambda: None,
                           prefs_changed=SimpleNamespace(emit=lambda: None))
    Board.set_dock(fake, "bars", "rainbow")
    assert fake.state.dock_bars == "default", "an unknown visualiser style was accepted"
    Board.set_dock(fake, "bars", "neon")
    assert fake.state.dock_bars == "neon", "the board refused the neon visualiser"
    cyber = ["#00e5ff", "#ff2bd6"]
    still = dockmod.neon_bars(cyber, "static", 0.0)
    assert still[0][0].name() == cyber[0] and still[-1][0].name() == cyber[1], \
        "still colours do not spread once across the bars"
    assert still == dockmod.neon_bars(cyber, "static", 4.0), "still bars changed over time"
    moved = dockmod.neon_bars(cyber, "circulate", dockmod.LAP_S / 4)
    assert [c.name() for c, _ in moved] != [c.name() for c, _ in
                                            dockmod.neon_bars(cyber, "circulate", 0.0)], \
        "circulating colours did not move"
    comet = [v for _, v in dockmod.neon_bars(["#00e5ff"], "circulate", 1.3)]
    assert min(comet) >= 0.35 and max(comet) - min(comet) > 0.15, f"no comet, or a bar vanished {comet}"
    dim = dockmod.neon_bars(cyber, "fade", 0.0)[0][1]
    bright = dockmod.neon_bars(cyber, "fade", dockmod.FADE_S / 2)[0][1]
    assert dim < bright, "fading bars did not brighten"

    dock = GlassDock(board, preview=True)
    dock._snap = fake_snapshot()
    art = QImage(300, 300, QImage.Format_ARGB32_Premultiplied)
    art.fill(QColor("#d94f8a"))
    dock._np = media.NowPlaying(title="A song", artist="The artist", playing=True,
                                available=True, art=art)
    dock._listening = True
    dock._levels = (0.3, 0.75, 1.0, 0.6, 0.25)
    board.palette = palette.build("dark")
    board.state.dock_neon_colors, board.state.dock_neon_motion = cyber, "static"

    def bars_crop(name):
        dock.sync()
        dock.resize(1000, 64)
        dock.invalidate()
        img = dock.grab().toImage()
        r = dock._bars.toAlignedRect().adjusted(-8, -8, 8, 8)
        crop = img.copy(r)
        crop.scaled(crop.width() * 6, crop.height() * 6, Qt.IgnoreAspectRatio,
                    Qt.FastTransformation).save(str(out / f"bars-{name}.png"))
        return img, dock._bars

    img, bars = bars_crop("default")
    accent = QColor(board.palette.accent)
    mid = img.pixelColor(int(bars.center().x()), int(bars.center().y()))
    assert abs(mid.hue() - accent.hue()) <= 12 or accent.saturation() < 30, \
        f"default bars are not the accent ({mid.name()} vs {accent.name()})"
    board.state.dock_bars = "neon"
    img, bars = bars_crop("neon-static")
    first = img.pixelColor(int(bars.left()) + 1, int(bars.center().y()) + 4)
    last = img.pixelColor(int(bars.right()) - 2, int(bars.center().y()) + 4)
    assert first.blue() > first.red() and last.red() > last.green(), \
        f"neon bars are not cyan to magenta ({first.name()}, {last.name()})"
    for motion, colours in (("circulate", ["#00e5ff"]), ("circulate", ["#21ffb2", "#00c3ff", "#9d4bff"])):
        board.state.dock_neon_colors, board.state.dock_neon_motion = colours, motion
        bars_crop(f"neon-{motion}-{len(colours)}")
    assert dock._bars_moving(), "circulating neon bars would not be redrawn"
    dock._listening = False
    bars_crop("neon-paused")
    board.state.dock_bars, board.state.dock_neon_colors = "default", cyber
    board.state.dock_neon_motion = "circulate"
    assert not dock._bars_moving()
    dock.close()
    print("PASS: the visualiser is the accent by default; Neon copies the outline's colours, "
          "spreads them still, runs them circulating (a comet that never drops a bar), fades, "
          "and dims while paused")


def check_row_animation(app, out, board):
    """A new app opens into the row - its slot widening from nothing, the icon
    growing in - and a closed one closes out where it stood, instead of the
    row jumping in one frame. Offscreen; frames saved as a strip."""
    from dataclasses import replace
    board.palette = palette.build("dark")
    board.state.dock_style = "glass"
    dock = GlassDock(board, preview=True)
    dock.setAttribute(Qt.WA_DontShowOnScreen)
    snap = fake_snapshot()
    dock._snap = snap
    dock._np = media.NowPlaying()
    dock.sync()
    dock.resize(1100, 64)
    dock.show()
    dock._animate_rows = True
    pump(app, 0.05)
    key = "app:" + shell.app_key("", r"C:\Apps\new.exe")
    rect = lambda ident: next((r for i, r, _ in dock._items if i == ident), None)
    clock_before = rect("clock").x()
    frames = []

    def change(tasks):
        dock._snap = replace(snap, tasks=tasks)
        dock._rebuild()
        dock._relayout()
        dock.invalidate()

    change(snap.tasks + [shell.Task(300, "New app", r"C:\Apps\new.exe")])
    assert rect(key) is not None and rect(key).width() <= 1, "the new app arrived at full width"
    frames.append(dock.grab())
    widths = []
    end = time.monotonic() + dockmod.ROW_MS / 1000 * 0.5
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    widths.append(rect(key).width())
    frames.append(dock.grab())
    pump(app, dockmod.ROW_MS / 1000 + 0.2)
    frames.append(dock.grab())
    plate = round(dockmod.PLATE * dock._s)
    assert 1 < widths[0] < plate, f"no in-between width halfway ({widths[0]})"
    assert rect(key).width() == plate and not dock._fading, "did not settle at full width"
    assert rect("clock").x() > clock_before, "the row did not make room"

    t0 = time.perf_counter()
    for _ in range(10):
        dock._static = None
        dock._render()
    ms = (time.perf_counter() - t0) / 10 * 1000

    change(snap.tasks)
    end = time.monotonic() + dockmod.ROW_MS / 1000 * 0.5
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    closing = rect(key)
    assert closing is not None and 1 < closing.width() < plate, "the closed app vanished at once"
    hit = dock._hit(closing.center())
    assert hit is None or hit[0] != key, "a closing app could still be clicked"
    frames.append(dock.grab())
    pump(app, dockmod.ROW_MS / 1000 + 0.2)
    assert rect(key) is None and not dock._fading, "the closed app's slot stayed"
    assert abs(rect("clock").x() - clock_before) <= 1, "the row did not close up again"
    frames.append(dock.grab())
    dock.close()

    strip = QImage(frames[0].width(), sum(f.height() for f in frames), QImage.Format_ARGB32)
    strip.fill(QColor("#202024"))
    p = QPainter(strip)
    for n, f in enumerate(frames):
        p.drawPixmap(0, n * f.height(), f)
    p.end()
    assert strip.save(str(out / "row-animation.png"))
    print(f"PASS: a new app opens into the row and a closed one closes out, in between frames "
          f"and settling exactly; closing ones can't be clicked ({ms:.1f} ms a frame)")


def check_previews(app, out):
    """Hovering an app with windows raises live previews of them after a
    moment, one card each, laid out without overlap above its button; moving
    off lets them go; a pinned app with none keeps its tooltip. The windows
    previewed are tool windows parked off every monitor, and the dock and its
    panel are never mapped: nothing reaches the screen."""
    from unittest.mock import patch
    from widgets.ui.dock_previews import WindowPreviews
    sources = []
    for i, (w, h) in enumerate(((800, 500), (400, 600))):
        src = QWidget(None, Qt.Tool | Qt.FramelessWindowHint)
        src.setAttribute(Qt.WA_ShowWithoutActivating)
        src.setGeometry(-32000 + i * 1000, -32000, w, h)
        src.show()
        sources.append(src)
    pump(app, 0.1)
    from widgets.ui import dock_previews as dp
    b = TestBoard()
    b.state.dock_replace_taskbar = False
    with patch.object(GlassDock, "_poll"), patch.object(Watcher, "start"), \
            patch.object(shell, "WindowEvents"), patch.object(shell.WorkArea, "reserve"), \
            patch.object(winapi, "fullscreen_monitor", lambda: None), \
            patch.object(shell, "maximised_on", lambda hwnds, monitor: False), \
            patch.object(WindowPreviews, "offscreen", True):
        dock = GlassDock(b)
        dock.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            b.set_dock("enabled", True)
            tasks = [shell.Task(int(s.winId()), f"Preview test {n}", r"C:\Apps\preview.exe")
                     for n, s in enumerate(sources)]
            dock._snap = Snapshot(tasks=tasks, taskbar_pins=[NOTEPAD])
            dock._rebuild()
            dock._row_key = None
            dock._place()
            app_hit = next(item for item in dock._items
                           if item[0].startswith("app:") and item[2].windows)
            pin_hit = next(item for item in dock._items
                           if item[0].startswith("app:") and not item[2].windows)
            assert dock._tip(app_hit) == "", "an app with windows still shows a tooltip"
            assert dock._tip(pin_hit), "a pinned app with no windows lost its tooltip"

            dock._hover = app_hit[0]
            dock._hover_previews(app_hit)
            pump(app, dockmod.PEEK_MS / 1000 * 0.5)
            assert not dock._previews_up(), "previews rose before the hover delay"
            # It rises: shorter than it will be, its bottom edge already in
            # place, its content partly faded in.
            end = time.monotonic() + dockmod.PEEK_MS / 1000 * 0.5 + dp.SHOW_MS / 1000 * 0.4
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.005)
            assert dock._previews_up(), "previews did not rise on hover"
            panel = dock._previews
            final = panel._target
            mid, mid_alpha = panel.geometry(), panel._alpha
            assert final.height() > mid.height() and abs(mid.bottom() - final.bottom()) <= 1 \
                and 0.0 < mid_alpha < 1.0, f"no rise: {mid} -> {final}, alpha {mid_alpha:.2f}"
            pump(app, dp.SHOW_MS / 1000 + 0.1)
            assert panel.geometry() == final and panel._alpha == 1.0, "did not settle"
            ex = shell.u.GetWindowLongPtrW(int(panel.winId()), -20)
            assert not ex & 0x80000, "the panel is layered: the compositor draws no thumbnails there"
            assert len(panel.cards) == 2 and all(c.picture is not None for c in panel.cards), \
                "a window's live thumbnail did not register"
            for c, src in zip(panel.cards, sources):
                sw, sh = c.picture.size()
                assert (sw, sh) == (src.width(), src.height()), f"source size {sw}x{sh}"
                assert abs(c.thumb.width() / c.thumb.height() - sw / sh) < 0.05, "picture stretched"
                assert c.rect.contains(c.thumb) and c.rect.contains(c.close), "spills off its card"
            a, z = panel.cards
            assert not a.rect.intersects(z.rect), "cards overlap"
            cx = dock.x() + app_hit[1].center().x()
            assert abs(panel.geometry().center().x() - cx) <= 2 or panel.x() <= 8 or \
                panel.geometry().right() >= app.primaryScreen().geometry().right() - 8, \
                "not centred over its button"
            assert panel.geometry().bottom() < dock.y() + dock._capsule.top(), "not above the dock"
            panel.grab().save(str(out / "previews.png"))

            # Neon on: an outline window behind the panel, glowing just past
            # its edge, Windows' own border off; off again, both back.
            assert panel.halo is None or not panel.halo.isVisible(), "an outline with neon off"
            b.state.dock_neon = True
            panel._frame(panel.geometry(), 1.0)
            halo = panel.halo
            m = round(dp.HALO * dock._s)
            assert halo is not None and halo.isVisible() and \
                halo.geometry() == panel.geometry().adjusted(-m, -m, m, m), "no outline round the panel"
            ring = halo.frame()
            glow = ring.pixelColor(m - 3, halo.height() // 2).alpha()
            far = ring.pixelColor(1, halo.height() // 2).alpha()
            assert glow > 40 and glow > far, f"no glow beside the panel's edge ({glow}, {far})"
            assert not panel._border, "Windows' border still drawn over the neon"
            assert not shell.u.GetWindowLongPtrW(int(panel.winId()), -20) & 0x80000, \
                "the panel turned layered"
            halo.grab().save(str(out / "previews-neon.png"))
            b.state.dock_neon = False
            panel._frame(panel.geometry(), 1.0)
            assert not halo.isVisible() and panel._border, "the outline stayed with neon off"

            # Along to another app: the panel glides there, its cards fading in.
            before = panel.geometry()
            other = shell.Task(int(sources[1].winId()), "Other", r"C:\Apps\other.exe")
            fake = Entry("other", "Other", [other])
            panel.show_for("app:other", fake, before.center().x() + 300, dock.y() + dock._capsule.top())
            end = time.monotonic() + dp.MOVE_MS / 1000 * 0.4
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.005)
            glide = panel.geometry()
            assert glide != before and glide != panel._target and panel._alpha < 1.0, \
                f"jumped instead of gliding ({before} -> {glide} -> {panel._target})"
            pump(app, dp.MOVE_MS / 1000 + 0.1)
            assert panel.geometry() == panel._target, "did not settle on the other app"
            dock._hover = app_hit[0]
            dock._show_previews()
            pump(app, dp.MOVE_MS / 1000 + 0.1)

            dock._snap = Snapshot(tasks=tasks[:1], taskbar_pins=[NOTEPAD])
            dock._rebuild()
            dock._relayout()
            dock._refresh_previews()
            assert dock._previews_up() and len(panel.cards) == 1, "a closed window kept its card"

            # Away: it folds back down, fading, and only then goes.
            top_before = panel.geometry().top()
            dock._hover = ""
            dock._hover_previews(None)
            end = time.monotonic() + dockmod.UNPEEK_MS / 1000 + dp.HIDE_MS / 1000 * 0.6
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.005)
            assert panel.isVisible() and not dock._previews_up() and \
                panel.geometry().top() > top_before, "did not fold down before going"
            pump(app, dp.HIDE_MS / 1000 + 0.15)
            assert not panel.isVisible() and not panel.cards, "previews stayed after leaving"
            dock._hover = pin_hit[0]
            dock._hover_previews(pin_hit)
            pump(app, dockmod.PEEK_MS / 1000 + 0.2)
            assert not dock._previews_up(), "a pinned app with no windows raised previews"
        finally:
            dock.close()
            for s in sources:
                s.close()
    print("PASS: hovering an app raises live previews of its windows after a moment, unlayered, "
          "one card each, unstretched and apart, above its button; they rise, glide to another "
          "app, follow a window closing and fold away; with neon on, a neon outline glows round "
          "them")


def check_music_menu(app, out):
    """A click on the music opens its menu - cover, track, progress, previous,
    play/pause, next, volume - rising like the previews; the buttons reach the
    player, the slider and wheel set the volume, the speaker mutes, and a click
    outside or on the music again closes it. The speakers and the player are
    stand-ins: the real volume and music are never touched."""
    from unittest.mock import patch
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from widgets.ui import dock_panel as dpn
    from widgets.ui.dock_music import MusicPanel

    class Speakers:
        def __init__(self):
            self.level, self.muted, self.sets = 0.5, False, []

        def read(self):
            return self.level, self.muted

        def set_level(self, level):
            self.level = level
            self.sets.append(level)
            return True

        def set_muted(self, muted):
            self.muted = muted
            return True

        def close(self):
            pass

    speakers = Speakers()
    calls = []
    clicks = {"down": False, "at": (0, 0, True)}
    art = QImage(300, 300, QImage.Format_ARGB32_Premultiplied)
    art.fill(QColor("#d94f8a"))
    playing = media.NowPlaying(title="A song with a long title that should elide cleanly",
                               artist="The artist", album="The album", source="Spotify",
                               playing=True, available=True, art=art, position=42.0,
                               duration=215.0)
    b = TestBoard()
    b.state.dock_replace_taskbar = False
    with patch.object(GlassDock, "_poll"), patch.object(Watcher, "start"), \
            patch.object(shell, "WindowEvents"), patch.object(shell.WorkArea, "reserve"), \
            patch.object(winapi, "fullscreen_monitor", lambda: None), \
            patch.object(shell, "maximised_on", lambda hwnds, monitor: False), \
            patch.object(MusicPanel, "offscreen", True), \
            patch.object(shell, "Speakers", lambda: speakers), \
            patch.object(shell, "buttons_down", lambda: clicks["down"]), \
            patch.object(shell, "cursor", lambda: clicks["at"]), \
            patch.object(media, "play_pause", lambda: calls.append("play")), \
            patch.object(media, "next_track", lambda: calls.append("next")), \
            patch.object(media, "prev_track", lambda: calls.append("prev")):
        dock = GlassDock(b)
        dock.setAttribute(Qt.WA_DontShowOnScreen)
        try:
            b.set_dock("enabled", True)
            dock._np, dock._live = playing, (playing, time.monotonic())
            music = next(item for item in dock._items if item[0] == "music")
            dock._click(*music)
            panel = dock._music
            assert panel is not None and panel.up, "the click did not open the menu"
            assert calls == [], "the click still played or paused the track"
            pump(app, dpn.SHOW_MS / 1000 * 0.4)
            assert panel.geometry().height() < panel._target.height(), "the menu did not rise"
            pump(app, dpn.SHOW_MS / 1000 + 0.1)
            assert panel.geometry() == panel._target and panel._alpha == 1.0
            assert panel.geometry().bottom() < dock.y() + dock._capsule.top(), "not above the dock"
            assert abs(panel.geometry().center().x() - (dock.x() + music[1].center().x())) <= 2 or \
                panel.x() <= 8, "not over the music"
            content = QRectF(0, 0, panel._content.width(), panel._content.height())
            rects = [panel._art_rect, panel._bar] + [panel._rects[k] for k in
                                                     ("prev", "play", "next", "mute", "volume")]
            for n, r in enumerate(rects):
                assert content.contains(r), f"control {n} spills out of the menu"
                assert all(not r.intersects(o) for o in rects[n + 1:]), f"control {n} overlaps"

            def centre(name):
                c = panel._rects[name].center()
                return QPoint(round(c.x() + panel._offset.x()), round(c.y() + panel._offset.y()))
            QTest.mouseClick(panel, Qt.LeftButton, Qt.NoModifier, centre("play"))
            assert calls == ["play"] and not panel._playing(playing), \
                "play/pause did not reach the player, or the button did not flip at once"
            QTest.mouseClick(panel, Qt.LeftButton, Qt.NoModifier, centre("next"))
            QTest.mouseClick(panel, Qt.LeftButton, Qt.NoModifier, centre("prev"))
            assert calls == ["play", "next", "prev"], calls
            track = panel._rects["volume"]
            y = round(track.center().y() + panel._offset.y())
            QTest.mousePress(panel, Qt.LeftButton, Qt.NoModifier,
                             QPoint(round(track.left() + track.width() * 0.2), y))
            QTest.mouseMove(panel, QPoint(round(track.left() + track.width() * 0.8), y))
            QTest.mouseRelease(panel, Qt.LeftButton, Qt.NoModifier,
                               QPoint(round(track.left() + track.width() * 0.8), y))
            assert abs(speakers.sets[0] - 0.2) < 0.03 and abs(speakers.level - 0.8) < 0.03, \
                f"the slider did not set the volume ({speakers.sets})"
            panel.set_volume(speakers.level + 0.02)
            assert abs(speakers.level - 0.82) < 0.03
            QTest.mouseClick(panel, Qt.LeftButton, Qt.NoModifier, centre("mute"))
            assert speakers.muted and panel._volume[1], "the speaker did not mute"
            QTest.mouseClick(panel, Qt.LeftButton, Qt.NoModifier, centre("mute"))
            assert not speakers.muted

            for theme in ("dark", "light"):
                b.palette = palette.build(theme)
                dock._pal = None
                panel._override = None
                for name, np in (("playing", playing),
                                 ("paused", media.NowPlaying(title="Paused song", artist="Someone",
                                                             source="Apple Music", available=True,
                                                             position=10.0, duration=180.0)),
                                 ("idle", media.NowPlaying())):
                    dock._np, dock._live = np, (np, time.monotonic())
                    panel.refresh()
                    pump(app, dpn.MOVE_MS / 1000 + 0.1)
                    panel.grab().save(str(out / f"music-{theme}-{name}.png"))
            b.palette = palette.build("dark")
            calls.clear()
            QTest.mouseClick(panel, Qt.LeftButton, Qt.NoModifier, centre("play"))
            assert calls == [], "the buttons acted with nothing playing"

            b.state.dock_neon = True
            panel._frame(panel.geometry(), 1.0)
            assert panel.halo is not None and panel.halo.isVisible(), "no neon round the menu"
            b.state.dock_neon = False
            panel._frame(panel.geometry(), 1.0)

            # A click elsewhere on the screen closes it; one inside does not.
            g = panel.geometry()
            clicks["at"], clicks["down"] = (g.center().x(), g.center().y(), True), True
            pump(app, 0.1)
            clicks["down"] = False
            pump(app, 0.1)
            assert panel.up, "a click inside closed the menu"
            clicks["at"], clicks["down"] = (g.center().x(), g.top() - 200, True), True
            pump(app, 0.1)
            clicks["down"] = False
            assert not panel.up, "a click outside did not close the menu"
            pump(app, dpn.HIDE_MS / 1000 + 0.15)
            assert not panel.isVisible()
            dock._click(*music)
            assert panel.up
            pump(app, 0.05)
            dock._click(*music)
            assert not panel.up, "a second click on the music did not close it"
        finally:
            dock.close()
    print("PASS: the music opens a menu that rises above it - cover, track, progress, previous, "
          "play/pause (flipping at once), next, volume slider, wheel and mute - in both themes, "
          "with neon when on; a click outside or on the music again closes it")


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


def check_start_host():
    """Only Start's and search's host processes count as Start."""
    from unittest.mock import patch
    paths = {11: r"C:\Windows\SystemApps\Microsoft.Windows.StartMenuExperienceHost_x\StartMenuExperienceHost.exe",
             12: r"C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_x\SearchHost.exe",
             13: NOTEPAD}

    def pid_of(hwnd, out):
        out._obj.value = hwnd - 100
        return 1
    shell._start_pids.clear()
    with patch.object(shell.u, "GetWindowThreadProcessId", pid_of), \
            patch.object(shell, "process_path", lambda pid: paths.get(pid, "")):
        assert shell.is_start(111) and shell.is_start(112), "Start or search not recognised"
        assert not shell.is_start(113) and not shell.is_start(0), "an app taken for Start"
    shell._start_pids.clear()
    print("PASS: Start and search are told apart from apps by their host process")


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
    full, maxed, start = [None], [False], [False]
    pointer = [(g.center().x(), g.center().y(), True)]
    with patch.object(GlassDock, "_poll"), patch.object(Watcher, "start"), \
            patch.object(shell, "WindowEvents"), patch.object(shell.WorkArea, "reserve"), \
            patch.object(winapi, "fullscreen_monitor", lambda: full[0]), \
            patch.object(shell, "maximised_on", lambda hwnds, monitor: maxed[0]), \
            patch.object(shell, "cursor", lambda: pointer[0]), \
            patch.object(shell, "is_start", lambda hwnd: start[0]):
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
            start[0] = True
            pump(app, 0.5)
            assert dock.isVisible() and dock.y() == up_y, "Start did not bring it up"
            pump(app, 1.0)
            assert dock.isVisible(), "went down while Start was still open"
            start[0] = False
            pump(app, 1.2)
            assert not dock.isVisible(), "stayed up after Start closed"
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
          "edge or opening Start brings it up, leaving or closing Start lets it down, a "
          "captured pointer never does, and it returns afterwards")


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
    check_neon_bars(out, board)
    check_row_animation(app, out, board)
    check_rows(board)
    check_native()
    check_start_host()
    check_watcher(app)
    check_store()
    check_guard(app)
    check_positions()
    check_listening()
    check_fullscreen(app)
    check_rehide(app)
    check_previews(app, out)
    check_music_menu(app, out)
    if args.windows:
        check_windows(app, board, out)
    if args.live:
        check_live(app, board, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
