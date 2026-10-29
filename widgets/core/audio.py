"""
audio.py — what the speakers are actually doing, for the visualiser.

Reads the sound *leaving* this machine and turns it into a handful of band
levels. Nothing is recorded, nothing is written to disk, and nothing leaves the
process: a block of samples is analysed and thrown away roughly forty times a
second.

It listens only while a track is actually playing. What Windows offers is the
output mix - everything the speakers are doing at once, with no way to ask for
one app's share of it - so a meter that ran continuously was metering voice
calls, notifications and game audio and calling the result music. The media
widget opens the tap when a track starts and closes it when it stops; see
:func:`listen`.

Two ways in, tried in order, because neither is available everywhere:

**WASAPI loopback** (needs ``PyAudioWPatch``) opens the output endpoint as if it
were a microphone, which is the only way to get at the actual samples on Windows
- the audio path runs player → mixer → device and never passes through us. With
those samples an FFT gives a genuine spectrum, so the bars mean something
rather than just "loud": the kick and bass on the left, the hi-hats on the
right, and each bar jumping on the hits in its own part of the range.

**The Core Audio peak meter** needs nothing installed at all - it is a COM
interface every Windows machine has - but it reports one number, the current
output level. Real, and it tracks the music's dynamics, but it cannot know
*where* in the spectrum that level is. The bands are shaped from it so the bars
still move plausibly.

If neither works the tap reports ``off`` and the caller falls back to an
animation. That matters for a widget: a visualiser that fails should look calm,
not broken.

Everything happens on a worker thread. Audio callbacks and COM calls must never
run on Qt's GUI thread - blocking there freezes every widget on the board.

    python -m widgets.core.audio     watch the bars in the terminal
"""

from __future__ import annotations

import ctypes
import math
import threading
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

BANDS = 5

# Bass on the left to hi-hats on the right, log-spaced and set where a drum kit
# and a bass line actually sit rather than by arithmetic: kick and sub-bass;
# the bass line and the body of the kick; snare body, vocals and guitars; the
# snare's crack and claps; hi-hats, cymbals and shakers. Even spacing puts four
# of five bars in the mud, because energy in music is not spread evenly.
BAND_EDGES = (30.0, 100.0, 300.0, 1500.0, 5000.0, 16000.0)

BLOCK = 1024              # read size: ~21 ms at 48 kHz, ~47 readings a second
WINDOW = 2048             # the bass bands are read over the last two blocks: one
                          # block is 47 Hz a bin, which leaves the kick one bin
LONG_BELOW = 300.0        # bands under this use the long window; the rest use one
                          # block, which keeps a hi-hat's tick sharp
DEVICE_CHECK = 2.0        # seconds between "is the default output still this?"
RETRY_MIN = 0.5           # first pause after a source fails to open
RETRY_MAX = 8.0           # and the ceiling it doubles up to

# Turning band loudness into bar heights (Shaper). Each band is measured against
# its own recent range, not one gain shared by all five: a hi-hat carries a
# fraction of a kick's energy, so on a shared scale the treble bar barely moves
# and the bass bar sits pinned. And it is the *change* that reads as music -
# five bars that track loudness faithfully hover, because most music is about
# equally loud from one moment to the next. So a band that jumps is shown as a
# hit, at full height, whatever its level.
SILENT_DB = -78.0         # band power under this is silence, not quiet music
FLOOR_FALL = 0.30         # a band's floor drops straight to a quiet moment...
FLOOR_RISE = 0.015        # ...and climbs back in about a second, so a held note
                          # sinks and leaves the bar to the beats
PEAK_FALL = 0.012         # its peak jumps to a new high and sinks over ~2 s
MIN_SPAN_DB = 10.0        # never stretch less than this over a bar's height
HIT_DB = 6.0              # a rise this much sharper than usual is a full hit...
HIT_NEAR_DB = 24.0        # ...if it lands within this of the band's recent peak:
                          # a click leaking into a quiet band is not a hit there
FLUX_AVG = 0.06           # how fast "usual" adapts, so busy music isn't all hits
CURVE = 1.6               # pushes middling levels down, so the hits stand out
# Hits go straight up; the fall is quicker the higher the band, so the bass
# thumps and the hats tick - and every bar is down again before the next beat.
FALL = (0.24, 0.28, 0.32, 0.40, 0.48)

# Ballistics for the peak meter, which has one level to share between the bars
# rather than a spectrum. Quick to rise, slower to fall - a bar that decays as
# fast as it climbs reads as noise rather than as sound. Both are faster than a
# real VU meter would use: this is five bars a centimetre tall, and honest
# ballistics at that size just look like they are barely moving.
ATTACK = 0.80             # snap up onto a transient
RELEASE = 0.22            # and come down in time for the next one

# Expansion around a mid-point. Music mostly sits in a narrow loudness band, so
# a linear meter hovers near the middle and reads as inert; pushing the distance
# from PIVOT out by CONTRAST is what makes a kick drum look like a kick drum.
PIVOT = 0.42
CONTRAST = 1.55

# How fast the auto-gain forgets a loud passage. Slow enough not to pump on
# every beat, quick enough that a quiet section is using the full height within
# a couple of seconds rather than a couple of minutes.
GAIN_FALL = 0.012


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


class Analyser:
    """Blocks of samples in, the loudness of each band out, in dB.

    Two windows, because bass and treble want opposite things. The bass bands
    are read over the last ``WINDOW`` samples - one block leaves the kick's
    30-100 Hz a single FFT bin wide - and the rest over the newest block alone,
    since a long window smears a hi-hat's tick into the gap before the next one.
    A new reading arrives every block either way.
    """

    def __init__(self, rate: int) -> None:
        import numpy as np
        self.np = np
        self.parts = []                 # (samples, window, scale, slice per band)
        for size in (WINDOW, BLOCK):
            window = np.hanning(size).astype(np.float32)
            freqs = np.fft.rfftfreq(size, 1.0 / rate)
            mine = [(i, np.where((freqs >= lo) & (freqs < hi))[0])
                    for i, (lo, hi) in enumerate(zip(BAND_EDGES, BAND_EDGES[1:]))
                    if (hi <= LONG_BELOW) == (size == WINDOW)]
            # Scaled so a full-scale sine reads about 0 dB in either window.
            self.parts.append((size, window, float(window.sum()) ** 2 / 2.0, mine))
        self.buf = np.zeros(WINDOW, dtype=np.float32)

    def feed(self, block) -> List[float]:
        np = self.np
        n = min(len(block), WINDOW)
        self.buf = np.concatenate((self.buf[n:], np.asarray(block[-n:], np.float32)))
        out = [-120.0] * BANDS
        for size, window, scale, mine in self.parts:
            power = np.abs(np.fft.rfft(self.buf[-size:] * window)) ** 2 / scale
            for i, idx in mine:
                if idx.size:
                    out[i] = 10.0 * math.log10(float(power[idx].sum()) + 1e-12)
        return out


class Shaper:
    """Band loudness in, bar heights (0..1) out.

    Each band against its own recent range: a floor that drops straight to the
    quiet moments and creeps back, and a peak that jumps to the loud ones and
    sinks. On top, a band that has just risen sharply - a kick, a snare, a
    hi-hat - is a hit and goes to full height, however loud it is. Then the bar
    falls, quicker the higher the band. See the constants above.
    """

    def __init__(self, bands: int = BANDS) -> None:
        self.bands = bands
        self.reset()

    def reset(self) -> None:
        n = self.bands
        self.floor: List[Optional[float]] = [None] * n
        self.peak: List[Optional[float]] = [None] * n
        self.prev: List[Optional[float]] = [None] * n
        self.flux = [0.0] * n
        self.out = [0.0] * n

    def feed(self, db: List[float]) -> List[float]:
        for i, d in enumerate(db[:self.bands]):
            if d < SILENT_DB:
                target = 0.0
                self.prev[i] = None          # coming back from silence is a hit
            else:
                if self.floor[i] is None:
                    self.floor[i], self.peak[i] = d - MIN_SPAN_DB, d
                f = self.floor[i]
                f += (d - f) * (FLOOR_FALL if d < f else FLOOR_RISE)
                pk = self.peak[i]
                pk = d if d > pk else pk + (d - pk) * PEAK_FALL
                self.floor[i], self.peak[i] = f, pk
                level = _clamp((d - f) / max(MIN_SPAN_DB, pk - f)) ** CURVE

                prev, self.prev[i] = self.prev[i], d
                rise = max(0.0, d - prev) if prev is not None else MIN_SPAN_DB
                self.flux[i] += (rise - self.flux[i]) * FLUX_AVG
                hit = _clamp((rise - self.flux[i]) / HIT_DB)
                hit *= _clamp((d - (pk - HIT_NEAR_DB)) / (HIT_NEAR_DB / 2.0))
                target = max(level, hit)
            o = self.out[i]
            self.out[i] = target if target > o else o + (target - o) * FALL[i]
        return list(self.out)


# --------------------------------------------------------------------------- #
#  Core Audio peak meter, through raw COM. No dependencies.
# --------------------------------------------------------------------------- #
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str) -> None:
        super().__init__()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(self))


try:
    ole32 = ctypes.WinDLL("ole32")
except Exception:                                    # pragma: no cover
    ole32 = None

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioMeterInformation = "{C02216F6-8C67-4B5B-9D00-D008E73E0064}"

CLSCTX_ALL = 0x17
E_RENDER, E_CONSOLE = 0, 0


def _vtable_call(ptr, index: int, restype, *argtypes):
    """Bind one method of a COM interface by its slot in the vtable."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


class PeakMeter:
    """The default output device's current level, 0..1.

    Three COM calls to set up and one per read. The interface is
    ``IAudioMeterInformation``; slots 3 and 4 of its vtable are ``GetPeakValue``
    and ``GetMeteringChannelCount``.
    """

    def __init__(self) -> None:
        self._meter = None
        self._device = None
        self._enum = None

    def open(self) -> bool:
        if ole32 is None:
            return False
        try:
            ole32.CoInitializeEx(None, 0)            # per-thread, apartment 0
            enum = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(GUID(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
                ctypes.byref(GUID(IID_IMMDeviceEnumerator)), ctypes.byref(enum))
            if hr != 0 or not enum:
                return False
            self._enum = enum

            device = ctypes.c_void_p()
            get_default = _vtable_call(
                enum, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p))
            if get_default(enum, E_RENDER, E_CONSOLE,
                           ctypes.byref(device)) != 0 or not device:
                return False
            self._device = device

            meter = ctypes.c_void_p()
            activate = _vtable_call(
                device, 3, ctypes.HRESULT, ctypes.c_void_p, ctypes.c_ulong,
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            if activate(device, ctypes.byref(GUID(IID_IAudioMeterInformation)),
                        CLSCTX_ALL, None, ctypes.byref(meter)) != 0 or not meter:
                return False
            self._meter = meter
            return True
        except Exception:
            return False

    def read(self) -> Optional[float]:
        if not self._meter:
            return None
        try:
            value = ctypes.c_float(0.0)
            get_peak = _vtable_call(self._meter, 3, ctypes.HRESULT,
                                    ctypes.POINTER(ctypes.c_float))
            if get_peak(self._meter, ctypes.byref(value)) != 0:
                return None
            return float(value.value)
        except Exception:
            return None

    def close(self) -> None:
        for ptr in (self._meter, self._device, self._enum):
            if ptr:
                try:
                    _vtable_call(ptr, 2, ctypes.c_ulong)(ptr)      # Release
                except Exception:
                    pass
        self._meter = self._device = self._enum = None


# --------------------------------------------------------------------------- #
#  The tap
# --------------------------------------------------------------------------- #
class AudioTap:
    """Band levels from whatever this machine can offer.

    ``levels()`` returns :data:`BANDS` floats in 0..1 and is safe to call from
    the GUI thread; it only reads a tuple the worker replaces wholesale.
    """

    def __init__(self, bands: int = BANDS) -> None:
        self.bands = bands
        self.kind = "off"            # "spectrum" | "level" | "off"
        self.detail = ""
        self._levels: Tuple[float, ...] = tuple(0.0 for _ in range(bands))
        self._smooth: List[float] = [0.0] * bands
        self._gain = 1.0
        self._shaper = Shaper(bands)        # the spectrum's; the meter uses _publish
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="audio-tap")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.5)
        self._thread = None
        self.kind = "off"
        self._levels = tuple(0.0 for _ in range(self.bands))

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def levels(self) -> Tuple[float, ...]:
        return self._levels

    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        """Try the good source, fall back, and keep trying if a device vanishes.

        The backoff is not politeness, it is the whole difference between a
        widget and a runaway. ``_spectrum_loop`` asks to be re-opened for
        anything from a genuine device change to an endpoint that will not open
        at all, and re-opening means constructing PyAudio, which enumerates
        every endpoint on the machine: **296 ms**, measured. Retrying that with
        no pause - which is what this did - burns a whole core for as long as
        the condition lasts, and the condition can easily last hours. An
        exclusive-mode grab by another app is enough to trigger it.

        So: a loop that ran for a while and then asked to re-open gets to go
        straight back, because that is a real device change and the user is
        waiting to see the bars move again. One that failed immediately is
        assumed broken and backs off, doubling to a ceiling.
        """
        wait = 0.0
        while not self._stop.is_set():
            began = time.monotonic()
            reopen = self._spectrum_loop() or self._meter_loop()
            if reopen:
                lived = time.monotonic() - began
                wait = 0.0 if lived > 1.0 else min(RETRY_MAX,
                                                   max(RETRY_MIN, wait * 2))
            else:
                self.kind = "off"
                self.detail = "no audio source"
                self._decay_to_silence()
                wait = 4.0                # a device may appear later
            if wait and self._stop.wait(wait):
                break

    def _decay_to_silence(self) -> None:
        self._smooth = [0.0] * self.bands
        self._shaper.reset()
        self._levels = tuple(0.0 for _ in range(self.bands))

    # -- source 1: real samples ----------------------------------------- #
    def _spectrum_loop(self) -> bool:
        """WASAPI loopback + FFT. Returns True if it ran and wants re-opening."""
        try:
            import numpy as np
            import pyaudiowpatch as pa
        except Exception:
            return False

        audio = stream = None
        try:
            audio = pa.PyAudio()
            info = audio.get_host_api_info_by_type(pa.paWASAPI)
            speakers = audio.get_device_info_by_index(info["defaultOutputDevice"])
            if not speakers.get("isLoopbackDevice"):
                # The output endpoint has a mirror-image loopback entry; find the
                # one that belongs to the device actually in use, so switching
                # headphones follows the audio rather than the first match.
                for candidate in audio.get_loopback_device_info_generator():
                    if speakers["name"] in candidate["name"]:
                        speakers = candidate
                        break
                else:
                    return False

            rate = int(speakers["defaultSampleRate"])
            channels = int(speakers["maxInputChannels"])
            stream = audio.open(format=pa.paFloat32, channels=channels,
                                rate=rate, input=True, frames_per_buffer=BLOCK,
                                input_device_index=speakers["index"])
            self.kind = "spectrum"
            self.detail = speakers["name"]
            analyser = Analyser(rate)
            self._shaper.reset()

            next_check = time.monotonic() + DEVICE_CHECK
            heard = 0.0                  # loudest sample since the last check

            while not self._stop.is_set():
                # Follow the default output when it changes. Plugging in a
                # headset or picking one in the volume flyout points the audio
                # somewhere else, and a stream opened on the old endpoint does
                # not error - it just goes quiet, so the bars stop while sound
                # is plainly still playing.
                #
                # Silence alone therefore proves nothing; the question is
                # whether *someone else* is getting the audio. The peak meter
                # binds to whichever endpoint is default at the moment it opens,
                # so opening a fresh one and finding it loud while this stream
                # is silent is exactly the signal - and it costs three COM calls
                # every couple of seconds instead of a 296 ms device
                # enumeration.
                #
                # This replaces comparing device names, which could not
                # distinguish the two cases at all: PortAudio's snapshot of the
                # default is taken when PyAudio is constructed and never
                # updates, so the comparison was answering a question about the
                # past.
                now = time.monotonic()
                if now >= next_check:
                    next_check = now + DEVICE_CHECK
                    if heard < 1e-4 and self._elsewhere_playing():
                        self.detail = "output moved to another device"
                        return True          # reopen on the new endpoint
                    heard = 0.0

                raw = stream.read(BLOCK, exception_on_overflow=False)
                block = np.frombuffer(raw, dtype=np.float32)
                if channels > 1:
                    block = block.reshape(-1, channels).mean(axis=1)
                if block.size < BLOCK:
                    continue
                peak = float(np.abs(block).max())
                if peak > heard:
                    heard = peak
                self._levels = tuple(self._shaper.feed(analyser.feed(block[:BLOCK])))
            return False
        except Exception:
            # A device change or an exclusive-mode grab lands here. Reporting the
            # failure and re-opening beats dying silently.
            self.detail = "loopback interrupted"
            return not self._stop.is_set()
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                if audio is not None:
                    audio.terminate()
            except Exception:
                pass

    @staticmethod
    def _elsewhere_playing() -> bool:
        """Is the *current* default output making sound?

        Asked only when this stream has heard nothing for a while, to tell
        "nothing is playing" apart from "the audio went to the headset you just
        plugged in". Opened fresh each time on purpose: the meter binds to
        whichever endpoint was default when it opened, so a long-lived one would
        go stale in exactly the case being tested for.
        """
        meter = PeakMeter()
        try:
            if not meter.open():
                return False
            return (meter.read() or 0.0) > 0.01
        except Exception:
            return False
        finally:
            meter.close()

    # -- source 2: just the level --------------------------------------- #
    def _meter_loop(self) -> bool:
        meter = PeakMeter()
        if not meter.open():
            meter.close()
            return False
        self.kind = "level"
        self.detail = "output level (no spectrum available)"
        # One number has to fill five bars. Weighting them differently and
        # nudging each with a slow wobble keeps it honest - the level is real,
        # the distribution across bands is admittedly invented.
        weights = (1.00, 0.86, 0.72, 0.60, 0.48)
        phase = 0.0
        try:
            while not self._stop.is_set():
                peak = meter.read()
                if peak is None:
                    return not self._stop.is_set()
                phase += 0.09
                shaped = []
                for i in range(self.bands):
                    wobble = 0.82 + 0.18 * math.sin(phase * (1.0 + i * 0.37) + i)
                    shaped.append(_clamp(peak * weights[i % 5] * wobble * 1.35))
                self._publish(shaped)
                self._stop.wait(0.033)
            return False
        finally:
            meter.close()

    # ------------------------------------------------------------------ #
    def _publish(self, raw: List[float]) -> None:
        """Apply auto-gain and meter ballistics, then hand over the result.

        Auto-gain matters more than it sounds: a quiet track would otherwise sit
        as five stubs and a loud one would peg every bar flat. Tracking a decaying
        maximum keeps the bars using their full height whatever the material,
        which is what makes it look alive.
        """
        loudest = max(raw) if raw else 0.0
        if loudest > self._gain:
            self._gain += (loudest - self._gain) * 0.45        # follow up fast
        else:
            self._gain += (loudest - self._gain) * GAIN_FALL   # sink between hits
        scale = 1.0 / max(0.18, self._gain)

        for i, value in enumerate(raw):
            # Expansion, not just scaling. The bars used to track loudness
            # honestly and therefore hovered around the middle, because most
            # music does: the interesting part is how far each band sits from
            # the average, so stretching around a mid-point turns a gentle sway
            # into something that visibly hits the beat.
            target = _clamp((value * scale - PIVOT) * CONTRAST + PIVOT)
            rate = ATTACK if target > self._smooth[i] else RELEASE
            self._smooth[i] += (target - self._smooth[i]) * rate
        self._levels = tuple(self._smooth)


# --------------------------------------------------------------------------- #
#  One tap per process
# --------------------------------------------------------------------------- #
# There is one set of speakers, so there should be one reader of them. Two
# widgets each opening their own PyAudio instance against the same endpoint
# segfaulted the interpreter outright - not an exception, a hard crash - so this
# is a correctness requirement rather than an economy.
_shared: Optional[AudioTap] = None
_users = 0                 # widgets holding a reference
_listeners = 0             # of those, how many want it running *right now*
_paused = False            # the board is behind a fullscreen game
_lock = threading.Lock()


def _refresh() -> None:
    """Start or stop the shared tap to match what the board currently wants.

    One place decides, from state, rather than three callers each trying to
    remember whether some other condition also applies. Caller holds the lock.
    """
    if _shared is None:
        return
    want = _listeners > 0 and not _paused and not _disabled()
    if want and not _shared.running:
        _shared.start()
    elif not want and _shared.running:
        _shared.stop()


def acquire() -> AudioTap:
    """Get the process-wide tap. Does not start it - see :func:`listen`."""
    global _shared, _users
    with _lock:
        if _shared is None:
            _shared = AudioTap()
        _users += 1
        return _shared


def release() -> None:
    """Give it back. The last one out stops the capture."""
    global _users
    with _lock:
        _users = max(0, _users - 1)
        if _users == 0 and _shared is not None:
            _shared.stop()


def listen(on: bool) -> None:
    """Say whether *this* caller currently has something to meter.

    Opening the output costs about 1.4% of a core, permanently, and until now it
    was paid from the moment the board started until it closed - to animate five
    bars that mean nothing unless music is playing. The media widget calls this
    as the track starts and stops, so the tap exists only while there is
    something to hear.

    Idempotent per caller is *not* provided: this counts, so every ``True`` must
    be matched by a ``False``. Callers track their own state and only call on a
    transition.
    """
    global _listeners
    with _lock:
        _listeners = max(0, _listeners + (1 if on else -1))
        _refresh()


def set_paused(on: bool) -> None:
    """Stop listening while nothing can see the result.

    Called when the board goes quiet behind a fullscreen game. Analysing audio
    for a meter nobody is looking at was the whole of what quiet mode still
    spent - about 2.5% of a core - and a game is exactly when that is least
    welcome.
    """
    global _paused
    with _lock:
        _paused = bool(on)
        _refresh()


def _disabled() -> bool:
    """Offscreen renders and CI have no business opening an audio device."""
    import os
    return bool(os.environ.get("PCWIDGETS_NO_AUDIO"))


def _shutdown() -> None:
    """Stop cleanly at exit; PyAudio can crash if torn down by the interpreter."""
    global _users, _listeners
    _users = _listeners = 0
    if _shared is not None:
        _shared.stop()


import atexit                                          # noqa: E402
atexit.register(_shutdown)


# --------------------------------------------------------------------------- #
def _demo() -> None:                                  # pragma: no cover
    tap = AudioTap()
    tap.start()
    print("watching the output... play something. ctrl-c to stop.\n")
    try:
        time.sleep(0.8)
        print(f"source: {tap.kind}  ({tap.detail})\n")
        while True:
            bars = "".join(
                " ▁▂▃▄▅▆▇█"[min(8, int(v * 8.99))] for v in tap.levels())
            print(f"\r  [{bars}]  {'  '.join(f'{v:.2f}' for v in tap.levels())}",
                  end="", flush=True)
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        print()
    finally:
        tap.stop()


if __name__ == "__main__":                            # pragma: no cover
    _demo()
