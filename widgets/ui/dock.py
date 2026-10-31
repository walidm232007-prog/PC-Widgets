"""Glass Dock: a floating dock on the main display, in the board's own glass.

Start and the apps sit in the middle, music on one side and Windows' own
controls on the right. Everything native - which windows are open, which app
each belongs to, its icon and name, the network and the speakers - is read by
:class:`Watcher` on its own thread and handed over as one :class:`Snapshot`,
so painting never waits on another process. That is a lesson, not a style:
icons read during paint called into COM, COM pumped messages, and the pump
re-entered paint.
"""
from __future__ import annotations

import itertools
import os
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QColorDialog, QFileDialog, QMenu, QWidget

from ..core import audio, dock_shell as shell, media, palette, typography, winapi
from ..core.card import squircle
from ..core.store import _log, report
from .theme import paint_shadow

# Sizes at 100% scaling; all of them are multiplied by the display's scale.
WINDOW_H = 64       # the window: the capsule, the gap under it and room for its shadow
TOP, BAR_H = 4, 52  # the capsule inside it, floating 8 px above the screen's edge
SIDE = 16           # transparent room left and right, for the shadow
RADIUS = 18
PAD = 4             # inside the capsule, before the first plate
PLATE = 44          # a button's hover plate, and the pitch of the app row
ICON = 32           # Windows' own app icon size, so icons come out sharp, not resampled
LOGO = 23           # the Start logo: two 11 px tiles and a 1 px gap at 100%
SEP = 17            # a hairline and the room either side of it
MUSIC_WIDE, MUSIC_NARROW = 216, 80
BARS = 5            # the visualiser's bands, 3 px wide and 2 px apart
ICON_FONT = "Segoe Fluent Icons"    # Windows 11's own symbols; MDL2 on Windows 10
NETWORK_GLYPH = {"ethernet": "", "wifi": "", "cellular": "",
                 "offline": ""}
NETWORK_NAME = {"ethernet": "Ethernet", "wifi": "Wi-Fi", "cellular": "Cellular",
                "offline": "No internet"}


def paint_windows_logo(p: QPainter, centre: QPointF, size: float) -> None:
    """The Windows 11 logo, as Windows' own shell draws it.

    From the logo that ships with the taskbar (Icons\\WindowsLogo.svg in the
    MicrosoftWindows.Client.Core system app): two-by-two tiles with a gap of a
    eleventh of a tile, only the outer corners rounded, and one gradient from
    #4DD2FF at the top left to Windows blue, #0078D4, three quarters of the way
    to the bottom right. Tiles and gap are snapped to whole pixels, or the gap
    smears at taskbar sizes. It is Microsoft's mark, used for the one thing it
    means here: this button opens Windows' Start.
    """
    gap = max(1, round(size / 23))
    tile = max(4, round((size - gap) / 2))
    span = 2 * tile + gap
    x0, y0 = round(centre.x() - span / 2), round(centre.y() - span / 2)
    unit = span / 11.5                      # the SVG's logo spans 11.5 units
    rad = 0.5 * unit
    path = QPainterPath()
    for col, row in ((0, 0), (1, 0), (0, 1), (1, 1)):
        x, y = x0 + col * (tile + gap), y0 + row * (tile + gap)
        path.addPath(_tile(x, y, tile, rad, col, row))
    grad = QLinearGradient(x0 + 1.74259 * unit, y0, x0 + 9.23502 * unit, y0 + 11.5 * unit)
    grad.setColorAt(0.0, QColor("#4dd2ff"))
    grad.setColorAt(0.75, QColor("#0078d4"))
    grad.setColorAt(1.0, QColor("#0078d4"))
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawPath(path)
    p.restore()


def _tile(x: float, y: float, s: float, r: float, col: int, row: int) -> QPainterPath:
    """One tile of the logo, rounded only at the logo's own outer corner."""
    path = QPainterPath()
    if (col, row) == (0, 0):
        path.moveTo(x + r, y)
        path.lineTo(x + s, y)
        path.lineTo(x + s, y + s)
        path.lineTo(x, y + s)
        path.lineTo(x, y + r)
        path.arcTo(QRectF(x, y, 2 * r, 2 * r), 180, -90)
    elif (col, row) == (1, 0):
        path.moveTo(x, y)
        path.lineTo(x + s - r, y)
        path.arcTo(QRectF(x + s - 2 * r, y, 2 * r, 2 * r), 90, -90)
        path.lineTo(x + s, y + s)
        path.lineTo(x, y + s)
    elif (col, row) == (0, 1):
        path.moveTo(x, y)
        path.lineTo(x + s, y)
        path.lineTo(x + s, y + s)
        path.lineTo(x + r, y + s)
        path.arcTo(QRectF(x, y + s - 2 * r, 2 * r, 2 * r), 270, -90)
    else:
        path.moveTo(x, y)
        path.lineTo(x + s, y)
        path.lineTo(x + s, y + s - r)
        path.arcTo(QRectF(x + s - 2 * r, y + s - 2 * r, 2 * r, 2 * r), 0, -90)
        path.lineTo(x, y + s)
    path.closeSubpath()
    return path


# --------------------------------------------------------------------------- #
#  Reading the desktop, off the GUI thread
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    """One reading of the desktop, handed from the watcher to the dock."""
    tasks: list = field(default_factory=list)       # shell.Task, front first
    taskbar_pins: list = field(default_factory=list)    # the taskbar's own, in its order
    pins: dict = field(default_factory=dict)        # pin target -> (key, aumid, program)
    names: dict = field(default_factory=dict)       # app key -> what Start calls it
    launch: dict = field(default_factory=dict)     # app key -> what opens another one
    icons: dict = field(default_factory=dict)       # app key or pin target -> QImage
    network: str = "ethernet"
    speakers: Optional[tuple] = None                # (level 0..1, muted)
    battery: object = None
    source: object = None                           # the watcher that read it

    def signature(self):
        bat = self.battery
        return ([(t.hwnd, t.title, t.app, t.minimised) for t in self.tasks], self.taskbar_pins,
                sorted(self.pins.items()), sorted(self.names.items()),
                sorted((k, v.cacheKey() if v is not None else 0)
                       for k, v in self.icons.items()),
                self.network, self.speakers,
                (bat.present, bat.percent, bat.charging) if bat else None)


class Watcher(QObject):
    """Reads the desktop for the dock: every second, and soon after a window
    comes, goes or takes the foreground.

    Window lists, app identities, icons, names, the network and the speakers
    are all native calls, and some can block - a hung window answering
    WM_GETICON, the shell drawing a Store app's artwork - so none of them run
    on the GUI thread. A snapshot goes out only when something changed.
    """
    ready = Signal(object)

    def __init__(self, own_aumid: str = ""):
        super().__init__()
        self.own = own_aumid
        self.pins: list = []
        self.px = ICON
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._icons: dict = {}      # (key, px) -> QImage or None
        self._tried: dict = {}      # (key, px) -> when that picture was last asked for
        self._names: dict = {}
        self._launch: dict = {}
        self._resolved: dict = {}   # pin target -> (key, aumid, program)
        self._slow = (0.0, "ethernet", None)    # network and speakers, once a second
        self._last = None
        self._error = ""
        self._thread = threading.Thread(target=self._run, daemon=True, name="dock-watch")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        com = shell.com_thread()
        speakers = shell.Speakers() if com else None
        try:
            while not self._stop.is_set():
                try:
                    snap = self.read(speakers)
                    signature = snap.signature()
                    if signature != self._last and not self._stop.is_set():
                        self._last = signature
                        self.ready.emit(snap)
                    self._error = ""
                except Exception as exc:
                    if repr(exc) != self._error:        # once, not every second
                        self._error = repr(exc)
                        _log(f"dock: reading the desktop failed ({exc!r})")
                self._wake.wait(1.0)
                self._wake.clear()
        finally:
            if speakers is not None:
                speakers.close()
            if com:
                shell.com_done()

    def read(self, speakers=None) -> Snapshot:
        tasks = shell.tasks(self.own)
        now = time.monotonic()
        if now - self._slow[0] >= 1.0:
            self._slow = (now, shell.network(), speakers.read() if speakers else None)
        px = self.px
        taskbar = shell.taskbar_pins()
        snap = Snapshot(tasks=tasks, taskbar_pins=taskbar, network=self._slow[1],
                        speakers=self._slow[2], battery=winapi.battery(), source=self)
        for target in taskbar + [p for p in self.pins if p not in taskbar]:
            if target not in self._resolved:
                self._resolved[target] = resolve_pin(target)
            key, aumid, program = snap.pins[target] = self._resolved[target]
            snap.names[key] = self._name(key, aumid, program, target)
            snap.launch[key] = target
            snap.icons[target] = self._picture(target, px, aumid=aumid, file=target)
        me = os.getpid()
        fronts = {}
        for t in tasks:
            fronts.setdefault(t.app, t)
        for key, t in fronts.items():
            if t.pid == me:
                continue            # the dock paints the board's own icon itself
            snap.names[key] = self._name(key, t.aumid, t.path, "", t.title)
            if self._launch.get(key):
                snap.launch[key] = self._launch[key]
            snap.icons[key] = self._picture(key, px, aumid=t.aumid, hwnd=t.hwnd, file=t.path)
        return snap

    def _name(self, key, aumid, program, target="", title=""):
        if key not in self._names:
            name = launch = ""
            if target:
                name = shell.display_name(target)
            if not name and aumid:
                name = shell.display_name("shell:AppsFolder\\" + aumid)
                launch = "shell:AppsFolder\\" + aumid if name else ""
            if not name and program:
                name = shell.display_name(program)
            if not launch and program.lower().endswith(".exe") and \
                    os.path.basename(program).lower() != "applicationframehost.exe":
                launch = program
            self._names[key], self._launch[key] = name, launch
        return self._names[key] or title

    def _picture(self, key, px, aumid="", hwnd=0, file=""):
        """An app's picture at ``px``: its Start artwork, else the window's own
        icon when it is big enough, else the program's. A missing one is asked
        for again after ten seconds - some apps set their icon a moment after
        the window appears."""
        slot = (key, px)
        now = time.monotonic()
        if slot in self._icons and (self._icons[slot] is not None or
                                    now - self._tried[slot] < 10.0):
            return self._icons[slot]
        img = shell.shell_image("shell:AppsFolder\\" + aumid, px) if aumid else None
        small = None
        if img is None and hwnd:
            small = shell.window_icon(hwnd)
            if small is not None and small.width() >= px * 0.75:
                img, small = small, None
        if img is None and file and not file.lower().endswith("applicationframehost.exe"):
            img = shell.shell_image(file, px)
        if img is None:
            img = small
        if len(self._icons) > 256:
            self._icons.clear()
            self._tried.clear()
        self._icons[slot], self._tried[slot] = img, now
        return img


def resolve_pin(target: str):
    """(app key, app ID, program) for a pinned target, so a pin and the app's
    running windows share one button. Opens shortcuts: worker thread only."""
    if not target.lower().endswith(".lnk"):
        return resolve_guess(target)
    from PySide6.QtCore import QFileInfo
    aumid = shell.file_aumid(target)
    found = QFileInfo(target).symLinkTarget()
    program = os.path.normpath(found) if found else ""
    if not program and aumid.lower() == "microsoft.windows.explorer":
        # The File Explorer pin points into the shell namespace, not at a
        # program, and folder windows declare no app ID of their own.
        program = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
    return shell.app_key(aumid, program) or "pin:" + target.lower(), aumid, program


def _owns(aumid: str, program: str, t) -> bool:
    """Whether a pin's app owns a window. App IDs decide when both have one -
    a Chrome web app runs chrome.exe but is its own app."""
    if aumid and t.aumid:
        return aumid.lower() == t.aumid.lower()
    return bool(program) and os.path.normcase(program) == os.path.normcase(t.path)


@dataclass
class Entry:
    """One app button: its pin, its windows, or both."""
    key: str
    name: str
    windows: list                 # shell.Task, front first
    pin: str = ""                 # the saved pin target, when pinned
    icon: str = ""                # whose picture in Snapshot.icons
    own: bool = False             # the PC Widgets app window itself


# --------------------------------------------------------------------------- #
#  The dock
# --------------------------------------------------------------------------- #
class GlassDock(QWidget):
    """A dock takes no activation on click, so app buttons can toggle their window."""
    track_ready = Signal(object)

    def __init__(self, board, *, preview=False):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.board = board
        self.preview = preview
        self.setWindowTitle("PC Widgets Glass Dock")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Qt shows tooltips only over the active window unless told otherwise,
        # and the dock is never active - without this not one of them appeared.
        self.setAttribute(Qt.WA_AlwaysShowToolTips)
        self.setMouseTracking(True)
        self.lease = shell.TaskbarLease()
        self._reservation = None      # the hidden app-bar window, made on first use
        self._workarea = None
        self._watcher = None
        self._events = None
        self._snap = Snapshot()
        self._entries = []
        self._order = {}              # app key -> when it first appeared: a stable row
        self._arrivals = itertools.count()
        self._items = []              # (id, plate, payload) from _relayout
        self._seps = []
        self._capsule = QRectF()
        self._bars = QRectF()
        self._music_wide = True
        self._pixmaps = {}            # picture key -> (QImage cacheKey, QPixmap)
        self._art = None              # (art id, px, QPixmap)
        self._static = None           # everything but the bars, until something changes
        self._pal = None
        self._fonts = None
        self._hover = ""
        self._pressed = ""
        self._fg = 0
        self._tray_since = 0.0        # when the taskbar was raised for its tray; 0 = not
        self._tray_timer = QTimer(self)
        self._tray_timer.setInterval(250)
        self._tray_timer.timeout.connect(self._refresh)
        self._running = False
        self._listening = False
        self._stop = None
        self._tap = None
        self._np = media.NowPlaying()
        self._levels = (0.0,) * BARS
        self._s = 1.0
        self._minute = ""
        self._frame = QTimer(self)
        self._frame.setInterval(20)
        self._frame.setTimerType(Qt.PreciseTimer)
        self._frame.timeout.connect(self._animate)
        self._watch = QTimer(self)
        self._watch.setInterval(1000)
        self._watch.timeout.connect(self._refresh)
        self._soon = QTimer(self)     # coalesces a burst of window events into one read
        self._soon.setSingleShot(True)
        self._soon.setInterval(120)
        self._soon.timeout.connect(self._poke)
        self.track_ready.connect(self._on_track)
        board.prefs_changed.connect(self.sync)
        board.glass_changed.connect(self.invalidate)
        board.displays_changed.connect(lambda: QTimer.singleShot(0, self._refresh))
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.sync()

    # -- lifecycle -------------------------------------------------------- #
    def invalidate(self):
        self._static = None
        self.update()

    def sync(self):
        if self.preview:
            self._rebuild()
            self._relayout()
            self.invalidate()
            return
        if not self.board.state.dock_enabled:
            self.shutdown()
            return
        if not self._running:
            self._start()
        self._watcher.pins = list(self.board.state.dock_pins)
        self._poke()
        self._rebuild()
        self._fonts = None
        self.invalidate()
        self._refresh()

    def _start(self):
        self._running = True
        if self._reservation is None:
            self._reservation = QWidget(None, Qt.Tool | Qt.FramelessWindowHint)
            self._workarea = shell.WorkArea(int(self._reservation.winId()))
        # One capture serves the whole process (see audio.py); it only opens
        # while a track is playing - see _listen.
        self._tap = audio.acquire()
        self._stop = threading.Event()
        # Each worker keeps its own stop token: a quick off/on must never revive
        # an older worker when a slow WinRT read eventually finishes.
        threading.Thread(target=self._poll, args=(self._stop,), daemon=True,
                         name="dock-media").start()
        self._watcher = Watcher(shell.own_aumid())
        self._watcher.px = round(ICON * self._s)
        self._watcher.pins = list(self.board.state.dock_pins)
        self._watcher.ready.connect(self._apply)
        self._watcher.start()
        try:
            self._events = shell.WindowEvents(self._window_event)
        except OSError as exc:
            _log(f"dock: window events unavailable, polling only ({exc!r})")
        self._fg = shell.foreground()
        self._watch.start()

    def shutdown(self):
        self._running = False
        self._watch.stop()
        self._frame.stop()
        self._soon.stop()
        self._tray_timer.stop()
        self._tray_since = 0.0
        self.hide()
        if self._workarea is not None:
            self._workarea.release()
        self.lease.restore()
        if self._stop:
            self._stop.set()
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        if self._events is not None:
            self._events.close()
            self._events = None
        if self._tap is not None:
            if self._listening:
                self._listening = False
                audio.listen(False)
            self._tap = None
            audio.release()
        self._levels = (0.0,) * BARS

    def closeEvent(self, event):
        self.shutdown()
        if self._reservation is not None:
            self._reservation.close()
        super().closeEvent(event)

    # -- what the worker threads hand over -------------------------------- #
    def _poll(self, stop):
        source = media.MediaSource()
        last = None
        while not stop.is_set():
            try:
                np = source.poll()
                signature = (np.title, np.artist, np.playing, np.available, id(np.art), np.source)
                if signature != last and not stop.is_set():
                    last = signature
                    self.track_ready.emit(np)
            except Exception as exc:
                _log(f"dock: media read failed ({exc!r})")
            stop.wait(3.0 if source.backend == "helper" else 1.0)

    def _on_track(self, np):
        if not self._running:
            return
        self._np = np
        self._listen(np.playing and np.available)
        self.invalidate()

    def _listen(self, want):
        """Meter the speakers only while a track plays, as Now Playing does.

        The tap hears the whole mix: left open, the bars danced to a voice call
        or a game with nothing playing, and it cost 1.4% of a core all day.
        """
        if self._tap is None or want == self._listening:
            return
        self._listening = want
        audio.listen(want)
        if want and self.isVisible():
            self._frame.start()
        elif not want:
            self._frame.stop()
            self._levels = (0.0,) * BARS

    def _apply(self, snap):
        if snap.source is not self._watcher or not self._running:
            return
        self._snap = snap
        self._rebuild()
        if self.isVisible():
            self._place()
        self.invalidate()

    def _window_event(self, event, hwnd):
        if event == shell.EVENT_FOREGROUND and hwnd != self._fg:
            self._fg = hwnd
            self.invalidate()
        if not self._soon.isActive():
            self._soon.start()

    def _poke(self):
        if self._watcher is not None:
            self._watcher.poke()

    # -- placement -------------------------------------------------------- #
    def _refresh(self):
        if self.preview or not self._running:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            self._conceal()
            self._workarea.release()
            self.lease.restore()
            return
        g = screen.geometry()
        full = winapi.fullscreen_monitor()
        if full and QRect(full[0], full[1], full[2] - full[0], full[3] - full[1]).intersects(g):
            # A game or a video fills the main display. Step aside and leave the
            # taskbar alone: the game covers it either way, and showing it used
            # to activate it - taking the focus from the game every second.
            self._conceal()
            return
        if self._tray_since:
            if self._tray_busy():
                if self.lease.hwnd:
                    self._conceal()         # the real bar takes the dock's place a while
                self.lease.reveal()
                return
            self._tray_since = 0.0
            self._tray_timer.stop()
        if self.board.state.dock_replace_taskbar:
            if not self.lease.hide():
                self.lease.restore()        # fail open: the dock floats above the bar
        else:
            self.lease.restore()
        self._place(screen)
        fg = shell.foreground()
        minute = time.strftime("%H:%M")
        if fg != self._fg or minute != self._minute:
            self._fg, self._minute = fg, minute
            self.invalidate()
        if not self.isVisible():
            self.show()
            if self._listening:
                self._frame.start()

    def _conceal(self):
        self.hide()
        self._frame.stop()

    def _place(self, screen=None):
        screen = screen or QApplication.primaryScreen()
        if screen is None or self._workarea is None:
            return
        s = max(1.0, screen.logicalDotsPerInch() / 96.0)
        if s != self._s:
            self._s, self._fonts = s, None
            if self._watcher is not None:
                self._watcher.px = round(ICON * s)
                self._watcher.poke()
        g = screen.geometry()
        monitor = (g.x(), g.y(), g.x() + g.width(), g.y() + g.height())
        width = self._relayout(g.width() - round(48 * s))
        height = round(WINDOW_H * s)
        bar = self.lease.hwnd or shell.taskbar()
        # In place of the taskbar the dock sits at the screen's edge; beside it,
        # just above it. Either way maximised windows stop at the dock's top.
        bottom = monitor[3] if self.lease.hwnd else shell.taskbar_edge(monitor, bar)
        self._workarea.reserve(monitor, bottom - height, bar)
        rect = QRect(g.x() + (g.width() - width) // 2, bottom - height, width, height)
        if self.geometry() != rect:
            self.setGeometry(rect)

    def resizeEvent(self, event):
        if self.preview:
            self._relayout()
        self.invalidate()

    def moveEvent(self, event):
        self.invalidate()           # the glass behind it moved

    # -- the row ---------------------------------------------------------- #
    def _pin_targets(self):
        """The taskbar's pins in its order, less any removed from the dock, then
        the dock's own. Pinning to the taskbar from Start shows up here too."""
        state = self.board.state
        targets = [t for t in self._snap.taskbar_pins if t not in state.dock_hidden]
        return targets + [p for p in state.dock_pins if p not in targets]

    def _rebuild(self):
        """Pins first, in their order, each with any windows of its app; then
        the other running apps in the order they opened."""
        snap = self._snap
        taken, keys, entries = set(), set(), []
        for target in self._pin_targets():
            key, aumid, program = snap.pins.get(target) or resolve_guess(target)
            if key in keys:
                continue                    # pinned on the taskbar and in the dock
            keys.add(key)
            wins = [t for t in snap.tasks if t.hwnd not in taken and _owns(aumid, program, t)]
            taken.update(t.hwnd for t in wins)
            name = snap.names.get(key) or os.path.splitext(os.path.basename(target))[0]
            entries.append(Entry(key, name, wins, pin=target, icon=target))
        groups = {}
        for t in snap.tasks:
            if t.hwnd not in taken:
                groups.setdefault(t.app, []).append(t)
        # A counter, not the clock: apps first seen together must keep the
        # order they arrived in, not re-sort by z-order on every activation.
        for key in groups:
            if key not in self._order:
                self._order[key] = next(self._arrivals)
        for key in [k for k in self._order if k not in groups]:
            del self._order[key]
        me = os.getpid()
        for key in sorted(groups, key=self._order.get):
            wins = groups[key]
            entries.append(Entry(key, snap.names.get(key) or wins[0].title, wins, icon=key,
                                 own=wins[0].pid == me))
        self._entries = entries

    def _font_set(self):
        if self._fonts is None:
            s = self._s
            self._fonts = {
                "title": typography.font(9.5, QFont.DemiBold),
                "sub": typography.font(8.5),
                "time": typography.font(9, QFont.Medium),
                "date": typography.font(8.5),
                "letter": typography.font(12, QFont.DemiBold),
                "glyph": _symbols(16 * s), "small": _symbols(12 * s), "note": _symbols(15 * s),
            }
        return self._fonts

    def _system(self):
        """The right-hand buttons and their widths, in 100% units."""
        f, s = self._font_set(), self._s
        n = 3 if self._snap.battery is not None and self._snap.battery.present else 2
        tm, dm = QFontMetricsF(f["time"]), QFontMetricsF(f["date"])
        clock = max(tm.horizontalAdvance("12:59 PM"), tm.horizontalAdvance(_time()),
                    dm.horizontalAdvance("Wed Sep 30"), dm.horizontalAdvance(_date())) / s
        return [("tray", 28), ("quick", 12 + 22 * n), ("clock", round(clock) + 20)]

    def _relayout(self, limit=None):
        """Place every button; returns the dock's natural width. Pixel edges are
        rounded from running totals, so nothing drifts at 125% or 150%."""
        s = self._s
        system = self._system()
        fixed = 2 * SIDE + 2 * PAD + 2 * SEP + sum(w for _, w in system) + 2 * (len(system) - 1)
        limit_u = (limit if limit else self.width() if self.preview else 1 << 20) / s
        entries = list(self._entries)
        music = MUSIC_WIDE
        if fixed + music + (1 + len(entries)) * PLATE > limit_u:
            music = MUSIC_NARROW            # the artist goes before any app does
        room = max(2, int((limit_u - fixed - music) // PLATE))
        row = [("start", None)] + [("app:" + e.key, e) for e in entries]
        if len(row) > room:
            row = row[:room - 1] + [("more", [e for _, e in row[room - 1:]])]
        natural = fixed + music + len(row) * PLATE
        width = round(natural * s)
        ox = max(0.0, (self.width() - width) / 2) if self.preview else 0.0
        y, h = round((TOP + (BAR_H - PLATE) / 2) * s), round(PLATE * s)
        items, seps = [], []
        cursor = SIDE + PAD

        def put(ident, w, payload=None):
            nonlocal cursor
            x0, x1 = round(ox + cursor * s), round(ox + (cursor + w) * s)
            items.append((ident, QRectF(x0, y, x1 - x0, h), payload))
            cursor += w

        def sep():
            nonlocal cursor
            seps.append(round(ox + (cursor + SEP / 2) * s))
            cursor += SEP

        if self.board.state.dock_music_side == "right":
            for ident, payload in row:
                put(ident, PLATE, payload)
            sep()
            put("music", music)
        else:
            put("music", music)
            sep()
            for ident, payload in row:
                put(ident, PLATE, payload)
        sep()
        for i, (ident, w) in enumerate(system):
            cursor += 2 if i else 0
            put(ident, w)
        self._items, self._seps, self._music_wide = items, seps, music == MUSIC_WIDE
        self._capsule = QRectF(round(ox + SIDE * s), round(TOP * s),
                               width - 2 * round(SIDE * s), round(BAR_H * s))
        m = next(r for i, r, _ in items if i == "music")
        bw = round(3 * s) * BARS + round(2 * s) * (BARS - 1)
        self._bars = QRectF(m.right() - round(10 * s) - bw, m.center().y() - 11 * s, bw, 22 * s)
        return width

    # -- painting --------------------------------------------------------- #
    def _palette(self):
        state, pal = self.board.state, self.board.palette
        colour = QColor(state.dock_color) if state.dock_color else QColor(pal.card)
        if not colour.isValid():
            colour = QColor(pal.card)
        if state.dock_color and state.dock_style == "solid":
            light = 0.2126 * colour.redF() + 0.7152 * colour.greenF() + 0.0722 * colour.blueF()
            pal = palette.build("light" if light > 0.55 else "dark", state.accent)
        return pal, colour

    def paintEvent(self, event):
        if self._static is None or self._static.size() != self.size():
            self._static = self._render()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static)
        if self._np.available and not self._bars.isEmpty():
            self._paint_bars(p)
        p.end()

    def _render(self) -> QPixmap:
        # Drawn on a straight-alpha image, then converted once. On a
        # premultiplied one Qt always renders glyphs with ClearType, whatever
        # the font asks for, and on glass every letter and symbol came out with
        # red and blue edges. Its DPI is the screen's, so points stay the size
        # they are on the cards.
        img = QImage(self.size(), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        if img.isNull() or self._capsule.isEmpty():
            return QPixmap.fromImage(img)
        dpm = round(self.logicalDpiX() / 0.0254)
        img.setDotsPerMeterX(dpm)
        img.setDotsPerMeterY(dpm)
        self._pal, tint = self._palette()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._paint_material(p, tint)
        line = QColor(255, 255, 255, 40) if self._pal.dark else QColor(0, 0, 0, 30)
        for x in self._seps:
            p.fillRect(QRectF(x, self._capsule.center().y() - 12 * self._s, 1, 24 * self._s), line)
        for ident, rect, payload in self._items:
            self._paint_item(p, ident, rect, payload)
        p.end()
        return QPixmap.fromImage(img)

    def _paint_material(self, p, tint):
        s, pal, state = self._s, self._pal, self.board.state
        cap, radius = self._capsule, RADIUS * s
        paint_shadow(p, cap, radius, blur=round(14 * s), alpha=80 if pal.dark else 50, dy=3 * s)
        path = squircle(cap, radius)
        p.save()
        p.setClipPath(path)
        if state.dock_style == "glass":
            drew = self.board.glass.paint_into(p, QRectF(0, 0, self.width(), self.height()),
                                               self.x(), self.y())
            tint.setAlpha(max(70, min(225, state.glass_tint)) if drew else 235)
        p.fillPath(path, tint)
        # Light from above: a sheen on the upper half of the glass.
        sheen = QLinearGradient(0, cap.top(), 0, cap.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 22 if pal.dark else 90))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, sheen)
        p.restore()
        # A hairline that catches that light along the top edge.
        edge = QLinearGradient(0, cap.top(), 0, cap.bottom())
        if pal.dark:
            edge.setColorAt(0.0, QColor(255, 255, 255, 70))
            edge.setColorAt(1.0, QColor(255, 255, 255, 18))
        else:
            edge.setColorAt(0.0, QColor(0, 0, 0, 22))
            edge.setColorAt(1.0, QColor(0, 0, 0, 40))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawPath(squircle(cap.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5))

    def _plate(self, p, ident, rect, active=False, radius=10):
        dark = self._pal.dark
        if self._pressed == ident:
            fill = QColor(255, 255, 255, 14) if dark else QColor(0, 0, 0, 20)
        elif self._hover == ident:
            fill = QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 14)
        elif active:
            fill = QColor(255, 255, 255, 16) if dark else QColor(0, 0, 0, 9)
        else:
            return
        p.fillPath(squircle(rect, radius * self._s), fill)

    def _paint_item(self, p, ident, rect, payload):
        s, pal = self._s, self._pal
        c = rect.center()
        if ident == "music":
            self._paint_music(p, rect)
            return
        if ident.startswith("app:"):
            active = any(t.hwnd == self._fg for t in payload.windows)
            self._plate(p, ident, rect, active)
            px = round(ICON * s)
            box = QRectF(round(c.x() - px / 2), round(c.y() - px / 2), px, px)
            if self._pressed == ident:
                box = box.adjusted(2 * s, 2 * s, -2 * s, -2 * s)    # it sinks under the click
            pm = self._picture(payload, px)
            if pm is not None:
                p.drawPixmap(box, pm, QRectF(pm.rect()))
            else:
                _letter_tile(p, box, payload.name, s, self._font_set()["letter"])
            if payload.windows:
                w = (16 if active else 6) * s
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(pal.accent) if active else
                           QColor(255, 255, 255, 140) if pal.dark else QColor(0, 0, 0, 110))
                p.drawRoundedRect(QRectF(c.x() - w / 2, c.y() + 18 * s, w, 3 * s), 1.5 * s, 1.5 * s)
            return
        self._plate(p, ident, rect)
        f = self._font_set()
        if ident == "start":
            size = LOGO * s * (0.9 if self._pressed == ident else 1.0)
            paint_windows_logo(p, c, size)
        elif ident == "more":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(pal.text))
            for dx in (-6, 0, 6):
                p.drawEllipse(QPointF(c.x() + dx * s, c.y()), 1.6 * s, 1.6 * s)
        elif ident == "tray":
            p.setFont(f["small"])
            p.setPen(QColor(pal.text))
            p.drawText(rect, Qt.AlignCenter, "")
        elif ident == "quick":
            p.setFont(f["glyph"])
            p.setPen(QColor(pal.text))
            for i, g in enumerate(self._quick_glyphs()):
                p.drawText(QRectF(rect.x() + (6 + 22 * i) * s, rect.y(), 22 * s, rect.height()),
                           Qt.AlignCenter, g)
        elif ident == "clock":
            p.setPen(QColor(pal.text))
            p.setFont(f["time"])
            p.drawText(QRectF(rect.x(), c.y() - 16 * s, rect.width(), 16 * s),
                       Qt.AlignHCenter | Qt.AlignBottom, _time())
            p.setFont(f["date"])
            p.setPen(QColor(pal.text_dim))
            p.drawText(QRectF(rect.x(), c.y() + 1 * s, rect.width(), 15 * s),
                       Qt.AlignHCenter | Qt.AlignTop, _date())

    def _quick_glyphs(self):
        snap = self._snap
        glyphs = [NETWORK_GLYPH.get(snap.network, "")]
        if snap.speakers is None:
            glyphs.append("")
        else:
            level, muted = snap.speakers
            glyphs.append("" if muted else "" if level <= 0.0 else
                          "" if level < 0.34 else "" if level < 0.67 else "")
        bat = snap.battery
        if bat is not None and bat.present:
            glyphs.append(chr(0xE850 + max(0, min(10, round(bat.percent / 10)))))
        return glyphs

    def _picture(self, entry, px):
        if entry.own:
            icon = QApplication.windowIcon()
            return None if icon.isNull() else icon.pixmap(px, px)
        img = self._snap.icons.get(entry.icon)
        if img is None:
            return None
        hit = self._pixmaps.get(entry.icon)
        if hit is not None and hit[0] == img.cacheKey() and hit[1].width() == px:
            return hit[1]
        if img.width() != px or img.height() != px:
            img = img.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm = QPixmap.fromImage(img)
        if len(self._pixmaps) > 128:
            self._pixmaps.clear()
        self._pixmaps[entry.icon] = (self._snap.icons[entry.icon].cacheKey(), pm)
        return pm

    def _paint_music(self, p, rect):
        s, pal, np = self._s, self._pal, self._np
        self._plate(p, "music", rect, radius=12)
        px = round(ICON * s)
        art = QRectF(rect.x() + round(6 * s), round(rect.center().y() - px / 2), px, px)
        shape = squircle(art, 7 * s)
        pm = self._art_pixmap(np.art, px) if np.available else None
        if pm is not None:
            p.save()
            p.setClipPath(shape)
            p.drawPixmap(art, pm, QRectF(pm.rect()))
            p.restore()
        else:
            # No cover: the track's own colour, or a quiet plate when idle.
            p.fillPath(shape, QColor(*np.tint()) if np.available else
                       QColor(255, 255, 255, 30) if pal.dark else QColor(0, 0, 0, 16))
            p.setFont(self._font_set()["note"])
            p.setPen(QColor(255, 255, 255, 235) if np.available else QColor(pal.text_dim))
            p.drawText(art, Qt.AlignCenter, "")
        if self._hover == "music" and np.available:
            p.fillPath(shape, QColor(0, 0, 0, 110))
            _play_pause(p, art.center(), 12 * s, playing=np.playing)
        if not self._music_wide:
            return
        f = self._font_set()
        x = art.right() + round(10 * s)
        tw = self._bars.left() - round(10 * s) - x if np.available else rect.right() - x - 8 * s
        c = rect.center().y()
        p.setFont(f["title"])
        p.setPen(QColor(pal.text))
        title = np.title if np.available else "Not playing"
        p.drawText(QRectF(x, c - 17 * s, tw, 16 * s), Qt.AlignLeft | Qt.AlignBottom,
                   QFontMetricsF(f["title"]).elidedText(title, Qt.ElideRight, tw))
        p.setFont(f["sub"])
        p.setPen(QColor(pal.text_dim))
        sub = (np.artist or np.source) if np.available else "Spotify or Apple Music"
        p.drawText(QRectF(x, c + 1 * s, tw, 15 * s), Qt.AlignLeft | Qt.AlignTop,
                   QFontMetricsF(f["sub"]).elidedText(sub, Qt.ElideRight, tw))

    def _art_pixmap(self, art, px):
        if art is None or art.isNull():
            return None
        if self._art is not None and self._art[0] == art.cacheKey() and self._art[1] == px:
            return self._art[2]
        side = min(art.width(), art.height())
        square = art.copy((art.width() - side) // 2, (art.height() - side) // 2, side, side)
        pm = QPixmap.fromImage(square.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        self._art = (art.cacheKey(), px, pm)
        return pm

    def _paint_bars(self, p):
        """The visualiser, redrawn alone at 50 fps over the cached dock."""
        s, pal = self._s, self._pal or self.board.palette
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.accent) if self._listening else QColor(pal.text_faint))
        bw, gap = round(3 * s), round(2 * s)
        c = self._bars.center().y()
        for i, level in enumerate(self._levels if self._listening else (0.0,) * BARS):
            h = (4 + 18 * max(0.0, min(1.0, level))) * s
            p.drawRoundedRect(QRectF(self._bars.x() + i * (bw + gap), c - h / 2, bw, h),
                              bw / 2, bw / 2)

    def _animate(self):
        levels = self._tap.levels() if self._tap else self._levels
        if levels != self._levels:
            self._levels = levels
            if not self._bars.isEmpty():
                self.update(self._bars.toAlignedRect().adjusted(-1, -1, 1, 1))

    # -- input ------------------------------------------------------------ #
    def _hit(self, pos):
        return next((item for item in self._items if item[1].contains(pos)), None)

    def mouseMoveEvent(self, event):
        hit = self._hit(event.position())
        ident = hit[0] if hit else ""
        if ident != self._hover:
            self._hover = ident
            self.setToolTip(self._tip(hit))
            self.invalidate()

    def leaveEvent(self, event):
        if self._hover or self._pressed:
            self._hover = self._pressed = ""
            self.invalidate()

    def mousePressEvent(self, event):
        hit = self._hit(event.position())
        if event.button() in (Qt.LeftButton, Qt.MiddleButton) and hit:
            self._pressed = hit[0]
            self.invalidate()

    def mouseReleaseEvent(self, event):
        hit = self._hit(event.position())
        pressed, self._pressed = self._pressed, ""
        self.invalidate()
        if not hit or hit[0] != pressed:
            return
        if event.button() == Qt.LeftButton:
            self._click(*hit)
        elif event.button() == Qt.MiddleButton and hit[0].startswith("app:"):
            target = self._launch_target(hit[2])
            if target:
                self._launch(target)              # middle-click: another window, as on the taskbar

    def wheelEvent(self, event):
        hit = self._hit(event.position())
        if hit and hit[0] == "quick" and event.angleDelta().y():
            shell.key(0xAF if event.angleDelta().y() > 0 else 0xAE)   # volume up / down
            QTimer.singleShot(150, self._poke)

    def _click(self, ident, rect, payload):
        if ident == "start":
            shell.shortcut()
        elif ident == "music":
            media.play_pause()
        elif ident == "tray":
            self.open_tray()
        elif ident == "quick":
            shell.shortcut(0x41)                  # Win+A: quick settings
        elif ident == "clock":
            shell.shortcut(0x4E)                  # Win+N: notifications and calendar
        elif ident == "more":
            menu = QMenu(self)
            for entry in payload:
                menu.addAction(entry.name, lambda e=entry: self._open(e))
            menu.exec(self.mapToGlobal(rect.topLeft().toPoint()))
        elif ident.startswith("app:"):
            self._open(payload)

    def _open(self, entry):
        """Open a pinned app; bring a running one forward; minimise it if it is
        already in front. With several windows, each click brings the next."""
        wins = entry.windows
        if not wins:
            target = self._launch_target(entry)
            if target:
                self._launch(target)
            return
        fg = shell.foreground()
        if any(t.hwnd == fg for t in wins):
            if len(wins) == 1:
                shell.minimise(fg)
            else:
                shell.activate(wins[-1].hwnd)     # the one longest behind: clicks cycle
        else:
            shell.activate(wins[0].hwnd)
        self._poke()

    def _launch_target(self, entry):
        if entry.pin:
            return entry.pin
        return self._snap.launch.get(entry.key, "")

    def _launch(self, target):
        """ShellExecute, on a thread of its own: it can sit in COM for a while,
        and the dock must not freeze under the click."""
        def run():
            com = shell.com_thread()
            try:
                os.startfile(target)
            except OSError as exc:
                _log(f"dock: could not open {os.path.basename(target)!r} ({exc!r})")
            finally:
                if com:
                    shell.com_done()
        threading.Thread(target=run, daemon=True, name="dock-launch").start()

    def _tip(self, hit):
        if not hit:
            return ""
        ident, _, payload = hit
        np = self._np
        if ident == "start":
            return "Start"
        if ident == "music":
            if not np.available:
                return "Nothing playing in Spotify or Apple Music"
            return f"{np.title} · {np.artist}" if np.artist else np.title
        if ident == "tray":
            return "Show hidden icons"
        if ident == "quick":
            parts = [NETWORK_NAME.get(self._snap.network, "Network")]
            if self._snap.speakers is not None:
                level, muted = self._snap.speakers
                parts.append("Muted" if muted else f"Volume {round(level * 100)}%")
            return " · ".join(parts)
        if ident == "clock":
            return time.strftime("%A, %B %#d, %Y")
        if ident == "more":
            return "More apps"
        if ident.startswith("app:"):
            if len(payload.windows) == 1:
                return payload.windows[0].title
            if payload.windows:
                return f"{payload.name} · {len(payload.windows)} windows"
            return payload.name
        return ""

    # -- menus ------------------------------------------------------------ #
    def contextMenuEvent(self, event):
        hit = self._hit(QPointF(event.pos()))
        if hit and hit[0] == "start":
            shell.shortcut(0x58)                  # Win+X, as Start's own right-click
            return
        menu = QMenu(self)
        if hit and hit[0].startswith("app:"):
            self._app_menu(menu, hit[2])
            self._dock_menu(menu.addMenu("Glass Dock"))
        elif hit and hit[0] == "music":
            menu.addAction("Previous track", media.prev_track)
            menu.addAction("Pause" if self._np.playing else "Play", media.play_pause)
            menu.addAction("Next track", media.next_track)
            menu.addSeparator()
            self._dock_menu(menu.addMenu("Glass Dock"))
        else:
            self._dock_menu(menu)
        menu.exec(event.globalPos())

    def _app_menu(self, menu, entry):
        for t in entry.windows:
            title = t.title if len(t.title) <= 60 else t.title[:57] + "…"
            menu.addAction(title, lambda h=t.hwnd: shell.activate(h))
        if entry.windows:
            menu.addSeparator()
        target = self._launch_target(entry)
        if target:
            menu.addAction("New window" if entry.windows else "Open", lambda: self._launch(target))
        if entry.pin:
            menu.addAction("Unpin from dock", lambda: self._unpin(entry.pin))
        elif target:
            menu.addAction("Pin to dock", lambda: self._pin(target))
        if entry.windows:
            menu.addAction("Close window" if len(entry.windows) == 1 else "Close all windows",
                           lambda: [shell.close(t.hwnd) for t in entry.windows])
        menu.addSeparator()

    def _pin(self, target):
        state = self.board.state
        if target in state.dock_hidden:
            self.board.set_dock("hidden", [p for p in state.dock_hidden if p != target])
        if target not in self._pin_targets():
            self.board.set_dock("pins", state.dock_pins + [target])

    def _unpin(self, target):
        """Off the dock only: a taskbar pin is remembered as removed, so the
        taskbar itself is never changed."""
        state = self.board.state
        if target in state.dock_pins:
            self.board.set_dock("pins", [p for p in state.dock_pins if p != target])
        if target in self._snap.taskbar_pins:
            self.board.set_dock("hidden", state.dock_hidden + [target])

    def _dock_menu(self, menu):
        state = self.board.state
        menu.addAction("Pin an app…", self.add_pin)
        if state.dock_hidden:
            menu.addAction("Show all taskbar pins", lambda: self.board.set_dock("hidden", []))
        menu.addSeparator()
        for key, label in (("glass", "Frosted glass"), ("solid", "Solid colour")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(state.dock_style == key)
            action.triggered.connect(lambda _=False, v=key: self.board.set_dock("style", v))
        menu.addAction("Choose colour…", self.choose_color)
        if state.dock_color:
            menu.addAction("Use theme colour", lambda: self.board.set_dock("color", ""))
        side = "right" if state.dock_music_side == "left" else "left"
        menu.addAction(f"Move music to the {side}", lambda: self.board.set_dock("music_side", side))
        menu.addSeparator()
        menu.addAction("Show Windows taskbar", self.reveal_taskbar)
        menu.addAction("Turn off Glass Dock", lambda: self.board.set_dock("enabled", False))

    def reveal_taskbar(self):
        """The real taskbar, in the dock's place, until it loses the focus."""
        self._tray_since = time.monotonic()
        self._tray_timer.start()
        self._refresh()
        threading.Thread(target=_raise_tray, daemon=True, name="dock-tray").start()

    def open_tray(self):
        """Windows' own hidden icons, popped up above the dock's arrow, with the
        taskbar left hidden.

        Win+B gives the hidden bar's "Show hidden icons" arrow the keyboard
        focus and Enter opens its flyout - both work while the bar is hidden,
        tried on a live desktop. Explorer then leaves the flyout wherever it is
        moved. The first version showed the taskbar in place of the dock
        instead, and an auto-hiding one appeared parked below the screen.
        """
        arrow = next((r for i, r, _ in self._items if i == "tray"), None)
        screen = QApplication.primaryScreen()
        if arrow is None or screen is None:
            return
        g = screen.geometry()
        cx = self.x() + round(arrow.center().x())
        top = self.y() + round(self._capsule.top()) - round(6 * self._s)
        threading.Thread(target=_show_overflow, daemon=True, name="dock-tray",
                         args=(cx, top, (g.x(), g.y(), g.x() + g.width(), g.y() + g.height()))
                         ).start()

    def _tray_busy(self):
        since = time.monotonic() - self._tray_since
        if since < 2.5:
            return True                     # still coming up
        if since > 120:
            return False
        if shell.overflow_window():
            return True
        fg = shell.foreground()
        return bool(fg) and fg == shell.taskbar()

    def add_pin(self):
        path, _ = QFileDialog.getOpenFileName(self, "Pin an app", "",
                                              "Applications (*.exe *.lnk *.url)")
        if path:
            self._pin(os.path.normpath(path))

    def choose_color(self):
        colour = QColorDialog.getColor(QColor(self.board.state.dock_color or self.board.palette.card),
                                       self, "Dock colour")
        if colour.isValid():
            self.board.set_dock("color", colour.name())


_tray_seen = set()


def _raise_tray():
    time.sleep(0.1)                         # the bar has just been shown
    shell.shortcut(0x42)                    # Win+B raises an auto-hiding bar too


def _show_overflow(cx, top, screen):
    """Win+B, then Enter only once UI Automation says the focus is on the
    "Show hidden icons" arrow (Windows 11 names it SystemTrayIcon). With no
    hidden icons Win+B lands on the first tray icon instead, and Enter there
    would open that app. Then the flyout is moved above the dock's arrow."""
    com = shell.com_thread()
    try:
        shell.shortcut(0x42)                # Win+B: focus the notification area
        for _ in range(8):
            time.sleep(0.1)
            name, ident, cls = shell.focused_control() or ("", "", "")
            if "hidden icons" in name.lower() or ident == "SystemTrayIcon":
                break
        else:
            shell.key(0x1B)                 # Escape: give the tray its focus back
            if (name, ident, cls) not in _tray_seen:    # what it was instead, once
                _tray_seen.add((name, ident, cls))
                report(f"dock: the tray's focus was {name!r} ({ident!r}, {cls!r}), "
                       "not the hidden-icons arrow")
            return
        shell.key(0x0D)
        for _ in range(40):
            time.sleep(0.025)
            flyout = shell.overflow_window()
            if flyout:
                shell.place_above(flyout, cx, top, screen)
                return
    finally:
        if com:
            shell.com_done()


def resolve_guess(target: str):
    """A pin the watcher has not read yet. App IDs and programs are plain text;
    a shortcut has to be opened to know its app, and that waits for the
    watcher's thread."""
    low = target.lower()
    if low.startswith("shell:appsfolder\\"):
        aumid = target[len("shell:AppsFolder\\"):]
        return shell.app_key(aumid), aumid, ""
    program = target if low.endswith(".exe") else ""
    return shell.app_key("", program) or "pin:" + low, "", program


def _time() -> str:
    return time.strftime("%#I:%M %p")


def _date() -> str:
    return time.strftime("%a %b %#d")


def _symbols(px: float) -> QFont:
    """Windows' symbol font: Segoe Fluent Icons on 11, MDL2 Assets on 10,
    which has the same code points."""
    global _SYMBOL_FAMILY
    if _SYMBOL_FAMILY is None:
        from PySide6.QtGui import QFontDatabase
        _SYMBOL_FAMILY = ICON_FONT if ICON_FONT in QFontDatabase.families() \
            else "Segoe MDL2 Assets"
    font = QFont(_SYMBOL_FAMILY)
    font.setPixelSize(round(px))
    return font


_SYMBOL_FAMILY = None


def _letter_tile(p, box, name, s, font):
    """For an app with no icon at all: its initial on a colour of its own,
    rather than a generic screen that says nothing about which app it is."""
    hue = zlib.crc32(name.encode("utf-8", "replace")) % 360
    p.fillPath(squircle(box, 8 * s), QColor.fromHsl(hue, 140, 105))
    p.setPen(QColor(255, 255, 255))
    p.setFont(font)
    p.drawText(box, Qt.AlignCenter, (name.strip()[:1] or "?").upper())


def _play_pause(p, centre, size, playing):
    """What a click on the music does next: pause while playing, else play."""
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    x, y = centre.x(), centre.y()
    if playing:
        w, gap = size * 0.28, size * 0.22
        for dx in (-gap / 2 - w, gap / 2):
            p.drawRoundedRect(QRectF(x + dx, y - size / 2, w, size), w / 3, w / 3)
    else:
        tri = QPainterPath(QPointF(x - size * 0.35, y - size / 2))
        tri.lineTo(QPointF(x + size * 0.5, y))
        tri.lineTo(QPointF(x - size * 0.35, y + size / 2))
        tri.closeSubpath()
        p.drawPath(tri)
    p.restore()
