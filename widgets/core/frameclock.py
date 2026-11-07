"""One clock for the dock's motion: the main display's own refresh.

Qt steps every QVariantAnimation from a single 16 ms timer, whatever the
monitor. On a 60 Hz panel that is close enough, but on a 200 Hz one the dock's
slide moved on roughly one frame in three or four, and not on the same ones each
time - the hitch that read as "choppy" on the way up and down. The fixed timers
beside it (the bars at 50 fps, the neon and the cover fade at 30) each beat
against the display in their own way.

So the dock steps on this instead: a thread waits for the main display's
vertical blank - D3DKMTWaitForVerticalBlankEvent, the wait Chromium's own vsync
thread uses - and each blank becomes one tick on the GUI thread, so a 200 Hz
monitor gets 200 even steps a second and a 60 Hz one gets 60. Ticks that the GUI
thread has not got to yet are never stacked up: a busy frame is skipped, not
replayed late.

Tween positions itself by the blank's time, not by counting ticks, so a dropped
frame never slows a slide down. The thread sleeps whenever nothing is
subscribed: between animations the clock costs nothing.

If the blank can't be waited on (no display, a remote session), it falls back
to sleeping one refresh period, which is still the monitor's rate.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, Signal

from widgets.core import displays


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _OPEN_FROM_HDC(ctypes.Structure):
    _fields_ = [("hDc", wt.HDC), ("hAdapter", wt.UINT), ("AdapterLuid", _LUID),
                ("VidPnSourceId", wt.UINT)]


class _WAIT_FOR_VBLANK(ctypes.Structure):
    _fields_ = [("hAdapter", wt.UINT), ("hDevice", wt.UINT), ("VidPnSourceId", wt.UINT)]


class _CLOSE_ADAPTER(ctypes.Structure):
    _fields_ = [("hAdapter", wt.UINT)]


class _DEVMODEW(ctypes.Structure):
    # Display fields only; dmDisplayFrequency is all that is read.
    _fields_ = [("dmDeviceName", wt.WCHAR * 32), ("dmSpecVersion", wt.WORD),
                ("dmDriverVersion", wt.WORD), ("dmSize", wt.WORD),
                ("dmDriverExtra", wt.WORD), ("dmFields", wt.DWORD),
                ("dmPosition", wt.POINTL), ("dmDisplayOrientation", wt.DWORD),
                ("dmDisplayFixedOutput", wt.DWORD), ("dmColor", ctypes.c_short),
                ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                ("dmFormName", wt.WCHAR * 32), ("dmLogPixels", wt.WORD),
                ("dmBitsPerPel", wt.DWORD), ("dmPelsWidth", wt.DWORD),
                ("dmPelsHeight", wt.DWORD), ("dmDisplayFlags", wt.DWORD),
                ("dmDisplayFrequency", wt.DWORD)]


_gdi32 = ctypes.WinDLL("gdi32")
_user32 = ctypes.WinDLL("user32")
_gdi32.CreateDCW.restype = wt.HDC
_gdi32.CreateDCW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
_gdi32.DeleteDC.argtypes = [wt.HDC]
_gdi32.D3DKMTOpenAdapterFromHdc.restype = ctypes.c_long
_gdi32.D3DKMTOpenAdapterFromHdc.argtypes = [ctypes.POINTER(_OPEN_FROM_HDC)]
_gdi32.D3DKMTWaitForVerticalBlankEvent.restype = ctypes.c_long
_gdi32.D3DKMTWaitForVerticalBlankEvent.argtypes = [ctypes.POINTER(_WAIT_FOR_VBLANK)]
_gdi32.D3DKMTCloseAdapter.restype = ctypes.c_long
_gdi32.D3DKMTCloseAdapter.argtypes = [ctypes.POINTER(_CLOSE_ADAPTER)]
_user32.EnumDisplaySettingsW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(_DEVMODEW)]

ENUM_CURRENT_SETTINGS = -1 & 0xFFFFFFFF


def main_display() -> str:
    """\\\\.\\DISPLAYn of the main display - where the dock lives - or ""."""
    for device, _bounds, _work, main in displays._enum():
        if main:
            return device
    return ""


def refresh_rate(device: str = "") -> int:
    """The display's refresh rate in Hz, 60 when Windows won't say."""
    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(mode)
    try:
        if _user32.EnumDisplaySettingsW(device or None, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
            hz = int(mode.dmDisplayFrequency)
            if 20 <= hz <= 1000:        # 0 and 1 mean "the hardware default"
                return hz
    except OSError:
        pass
    return 60


class _VBlank:
    """The vertical blank of one display, to wait on."""

    def __init__(self, device: str):
        self._wait = None
        hdc = _gdi32.CreateDCW("DISPLAY", device, None, None)
        if not hdc:
            return
        try:
            req = _OPEN_FROM_HDC(hDc=hdc)
            if _gdi32.D3DKMTOpenAdapterFromHdc(ctypes.byref(req)) == 0:
                self._wait = _WAIT_FOR_VBLANK(hAdapter=req.hAdapter, hDevice=0,
                                              VidPnSourceId=req.VidPnSourceId)
        finally:
            _gdi32.DeleteDC(hdc)

    @property
    def ok(self) -> bool:
        return self._wait is not None

    def wait(self) -> bool:
        """Block until the next blank (ctypes lets the GIL go meanwhile)."""
        return self._wait is not None and \
            _gdi32.D3DKMTWaitForVerticalBlankEvent(ctypes.byref(self._wait)) == 0

    def close(self) -> None:
        if self._wait is not None:
            _gdi32.D3DKMTCloseAdapter(ctypes.byref(_CLOSE_ADAPTER(self._wait.hAdapter)))
            self._wait = None


class FrameClock(QObject):
    """Ticks once per refresh of the main display while anything listens."""

    _blank = Signal(float)

    def __init__(self):
        super().__init__()
        self.hz = refresh_rate(main_display())
        self.period = 1.0 / self.hz
        self.frame_time = time.perf_counter()   # when the frame being drawn began
        self.source = "none"        # "vblank" or "sleep", once the thread has run
        self._listeners = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending = False       # a tick is queued and the GUI hasn't run it yet
        self._reopen = True
        self._thread: Optional[threading.Thread] = None
        self._blank.connect(self._dispatch)     # queued: emitted from the thread

    # -- listeners -------------------------------------------------------- #
    def add(self, listener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)
            self._wake.set()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="frame-clock")
            self._thread.start()

    def discard(self, listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)
            if not self._listeners:
                self._wake.clear()

    def displays_changed(self) -> None:
        """The main display, or its refresh rate, may have changed."""
        self._reopen = True

    # -- the thread ------------------------------------------------------- #
    def _run(self) -> None:
        vblank: Optional[_VBlank] = None
        last = 0.0
        while True:
            self._wake.wait()
            if self._reopen:
                self._reopen = False
                if vblank is not None:
                    vblank.close()
                device = main_display()
                self.hz = refresh_rate(device)
                self.period = 1.0 / self.hz
                vblank = _VBlank(device) if device else None
                self.source = "vblank" if vblank is not None and vblank.ok else "sleep"
            if not (vblank is not None and vblank.wait()):
                # No blank to wait on: sleep to the next period instead.
                if vblank is not None:
                    vblank.close()
                    vblank, self.source = None, "sleep"
                due = last + self.period
                delay = due - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            now = time.perf_counter()
            if now - last < self.period * 0.5:
                continue                # the same blank twice: wait for the next
            last = now
            if not self._pending:
                self._pending = True
                self._blank.emit(now)

    # -- the GUI thread --------------------------------------------------- #
    def _dispatch(self, now: float) -> None:
        self._pending = False
        self.frame_time = now
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener._tick(now)
            except RuntimeError:        # its widget has been deleted
                self.discard(listener)


_clock: Optional[FrameClock] = None


def clock() -> FrameClock:
    global _clock
    if _clock is None:
        _clock = FrameClock()
    return _clock


class Ticker:
    """Calls back once per displayed frame while started - a QTimer whose
    interval is the monitor's own.

    max_fps caps it to every nth frame, a whole number of frames apart so the
    pace stays even: 60 on a 200 Hz display is every third frame (67 fps), on
    144 Hz every second (72), on 60 Hz every one. For the neon, which glows the
    whole time the dock is up and drifts slowly: at 200 fps it cost about a
    quarter of a core for motion that looks the same at 60."""

    def __init__(self, callback: Callable[[], None], max_fps: Optional[float] = None):
        self._callback = callback
        self._max_fps = max_fps
        self._active = False
        self._last = 0.0

    def start(self) -> None:
        if not self._active:
            self._active = True
            self._last = 0.0
            clock().add(self)

    def stop(self) -> None:
        if self._active:
            self._active = False
            clock().discard(self)

    def isActive(self) -> bool:
        return self._active

    def _tick(self, now: float) -> None:
        if self._max_fps:
            c = clock()
            every = max(1, round(c.hz / self._max_fps))
            if now - self._last < (every - 0.5) * c.period:
                return
            self._last = now
        self._callback()


class Tween(QObject):
    """A start → end value, eased over a duration, stepped once per displayed
    frame. The parts of QVariantAnimation the dock uses, so it drops in.

    Progress comes from the blank's timestamp, so the steps are even and a
    frame the GUI thread misses is skipped over, not stretched."""

    valueChanged = Signal(float)
    finished = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._from, self._to = 0.0, 1.0
        self._ms = 250.0
        self._curve = QEasingCurve(QEasingCurve.Linear)
        self._t0: Optional[float] = None
        self._running = False
        self._value = 0.0

    def setStartValue(self, value) -> None:
        self._from = float(value)

    def setEndValue(self, value) -> None:
        self._to = float(value)

    def setDuration(self, ms) -> None:
        self._ms = max(1.0, float(ms))

    def setEasingCurve(self, curve) -> None:
        self._curve = QEasingCurve(curve)

    def state(self):
        return QAbstractAnimation.Running if self._running else QAbstractAnimation.Stopped

    def currentValue(self) -> float:
        return self._value

    def start(self) -> None:
        self._t0 = None             # set on the first tick, one frame back
        self._running = True
        clock().add(self)
        self._emit(0.0)

    def stop(self) -> None:
        if self._running:
            self._running = False
            clock().discard(self)

    def _emit(self, progress: float) -> None:
        eased = self._curve.valueForProgress(progress)
        self._value = self._from + (self._to - self._from) * eased
        self.valueChanged.emit(self._value)

    def _tick(self, now: float) -> None:
        if not self._running:
            return
        if self._t0 is None:
            self._t0 = now - clock().period
        progress = min(1.0, (now - self._t0) * 1000.0 / self._ms)
        if progress >= 1.0:
            self.stop()
            self._emit(1.0)
            self.finished.emit()
        else:
            self._emit(progress)
