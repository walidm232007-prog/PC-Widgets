"""
Now Playing — a glass media player.

Modelled on the reference: album art filling the card behind frosted glass, the
title over it, a slim progress bar with elapsed and remaining, and round
transport buttons straddling the bottom edge.

Those buttons sitting *on* the edge is the detail that makes the look, and it
works here for free: the card already floats inside an 18px transparent gutter
that exists so its corners can antialias and its shadow has somewhere to fall.
The buttons overhang into that gutter, so they break the outline exactly the way
the reference does without the window needing to grow.

Transport is real - the media keys are global, so the buttons drive whichever
app currently holds media focus. See :mod:`widgets.core.media` for what each
backend can and cannot supply.
"""

from __future__ import annotations

import math
import threading
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
)

from ..core import audio, media
from ..core.card import MARGIN, PAD, RADIUS, Fader, WidgetCard, squircle
from ..core.glass import _box_blur as glass_blur
from ..core.store import _log

# --------------------------------------------------------------------------- #
#  The visualiser
# --------------------------------------------------------------------------- #
# Bars in the top right, the way iOS marks a playing track - driven by the sound
# actually leaving the machine. :mod:`widgets.core.audio` taps the output and
# reports five band levels; see there for how, and for what happens on a machine
# that cannot manage it.
#
# The sines below are the last resort, for when no tap is available at all. They
# are two detuned waves per bar, which is what stops a synthetic visualiser
# looking like a row of metronomes.
VIZ_BARS = 5
VIZ_SPEED = (5.1, 6.7, 4.3, 7.3, 5.9)      # deliberately not multiples
VIZ_PHASE = (0.0, 1.7, 3.1, 4.6, 2.2)
VIZ_RESTING = (0.30, 0.46, 0.62, 0.40, 0.26)   # where they sit when paused

# Polling. The session is read on its own thread, on its own clock - the tick
# rate is for animation and is far too fast to ask the media API anything.
POLL_EVERY = 0.5
# Reading via a separate process is ~320ms a call, nearly all of it interpreter
# startup. Every 3s keeps it near 10% of one core; twice a second would cost
# most of a core to show the same thing.
POLL_HELPER = 3.0
WATCH_EVERY = 5.0        # how often the GUI thread checks the poller is healthy
STALE_AFTER = 20.0       # a poller alive but silent this long is worth logging


class MediaWidget(WidgetCard):
    kind = "media"
    title = "Now Playing"
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 40
    # The transport row is centred on the card's bottom edge, so half of the
    # play button lives outside the card. Without reserved height the window
    # clipped it in half.
    extra_bottom = 20.0

    # Ticks 25 times a second for the hover animation, but only repaints when
    # something changed. Frosting three discs costs about 4ms - fine for a
    # second of animation, a tenth of a core if it ran all day for nothing.
    repaint_on_tick = False

    # The poll runs on a worker thread and comes back through this. It has to:
    # awaiting a WinRT async operation on the GUI thread deadlocks, because the
    # completion callback needs a message pump that Qt's event loop is already
    # occupying. It only bit once album art appeared - a track with no thumbnail
    # awaits nothing, so the bug hid until there was a stream to read.
    data_ready = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.source = media.MediaSource(self.config.get("player", "auto"))
        self.np = media.NowPlaying()
        # The poller and the watchdog that keeps an eye on it.
        self._poller = None
        self._stop_poll = threading.Event()
        self._last_result = 0.0
        self._last_watch = 0.0
        self._spread = 0.0          # 0 = stacked and overlapping, 1 = separated
        self.data_ready.connect(self._on_data)
        self._buttons: List[Tuple[str, QPointF, float]] = []
        # Where the glass goes, worked out during the content pass and frosted
        # afterwards, once there is something behind it to refract.
        self._discs: List[Tuple[str, QPointF, float, float]] = []
        self._buf: Optional[QImage] = None
        self._pressed: Optional[str] = None
        self._hover_btn: Optional[str] = None
        # Visualiser. `_dirty` is what keeps the animation cheap: a bar frame
        # restores the strip behind the bars from `_viz_backing` and redraws
        # them, instead of recomposing the whole card and re-frosting three
        # discs. Anything that actually changes the card sets `_dirty` so the
        # next paint takes the full path.
        self._viz_phase = 0.0
        self._viz_rect: Optional[QRectF] = None
        self._viz_backing: Optional[QImage] = None
        self._dirty = True
        # The tap reads what the speakers are playing. Opt-out lives in the
        # right-click menu and persists, because listening to the output - even
        # only to measure it - should be something you can switch off.
        # One tap serves the whole process - see widgets/core/audio.py.
        self._tap: Optional[audio.AudioTap] = None
        self._tap_wanted = bool(self.config.get("visualiser", True))
        self._tap_on = False         # is the tap open on this widget's behalf
        # Cover art cross-fade. The outgoing image is kept until the ramp ends,
        # so a track change dissolves rather than cutting.
        self._art_id: Optional[tuple] = None
        self._art_shown: Optional[QImage] = None
        self._art_prev: Optional[QImage] = None
        self._art_fade = Fader(self, fps=30)
        self._art_fade.finish()          # nothing to fade into on the first paint
        self._scaled: dict = {}          # cover-filled covers, keyed by image+size
        self._muted = False
        self._pulse = 0.0
        self._liked: set[str] = self._load_liked()

    # ------------------------------------------------------------------ #
    def _liked_path(self) -> str:
        import os
        from ..core.store import _dir
        return os.path.join(_dir(), "liked.txt")

    def _load_liked(self) -> set:
        import os
        try:
            with open(self._liked_path(), encoding="utf-8") as fh:
                return {ln.strip() for ln in fh if ln.strip()}
        except Exception:
            return set()

    def _save_liked(self) -> None:
        import os
        try:
            os.makedirs(os.path.dirname(self._liked_path()), exist_ok=True)
            with open(self._liked_path(), "w", encoding="utf-8") as fh:
                fh.write("\n".join(sorted(self._liked)) + "\n")
        except Exception:
            pass

    def _track_key(self) -> str:
        np = self.np
        return f"{np.artist} — {np.title}".strip(" —") if np.title else ""

    @property
    def _is_liked(self) -> bool:
        key = self._track_key()
        return bool(key) and key in self._liked

    # ------------------------------------------------------------------ #
    def _animate(self) -> None:
        """Ease the transport row open and closed."""
        target = 1.0 if self._hover else 0.0
        # Critically damped-ish: quick to open, unhurried to close.
        rate = 0.30 if target > self._spread else 0.16
        before = self._spread
        self._spread += (target - self._spread) * rate
        if abs(self._spread - target) < 0.002:
            self._spread = target
        if abs(self._spread - before) > 0.001:
            self._dirty = True          # the discs moved; recompose properly
            self.update()

    def _repaint(self) -> None:
        """Recompose the whole card, not just a visualiser frame."""
        self._dirty = True
        self.update()

    def _on_data(self) -> None:
        """A poll came back: check the cover and the tap, then repaint."""
        self._check_artwork()
        self._sync_tap()
        self._repaint()

    def _sync_tap(self) -> None:
        """Listen to the output only while a track is actually playing.

        The loopback tap hears the whole machine. Windows mixes every app's
        audio before it reaches the endpoint and offers no way to pull one app's
        share back out, so a tap that runs continuously is metering a voice
        call, a notification or a game and presenting it as the track - which is
        exactly what happened during a Discord call: the bars danced to a
        friend's voice.

        Tying it to the session fixes the meaning and the cost together. The
        card only ever shows Spotify or Apple Music (see
        :data:`widgets.core.media.MUSIC_APPS`), so when it says a track is
        playing the mix really is mostly that track; and when it is not, the
        1.4% of a core the tap costs is simply not spent. That is most of the
        day.
        """
        want = self._tap_wanted and self.np.playing and self.np.available
        if want == self._tap_on:
            return
        self._tap_on = want
        audio.listen(want)

    def on_start(self) -> None:
        if self._tap_wanted:
            self._tap = audio.acquire()
        self._start_poller()

    def closeEvent(self, ev) -> None:
        self._stop_poll.set()
        if self._tap is not None:
            self._tap = None
            if self._tap_on:
                self._tap_on = False
                audio.listen(False)
            audio.release()
        super().closeEvent(ev)

    # ------------------------------------------------------------------ #
    def _start_poller(self) -> None:
        """One long-lived thread that reads the session, forever.

        This used to spawn a fresh thread per poll, and that shape is what made
        the widget go blind for days at a time. ``on_tick`` set a ``_polling``
        latch *before* starting the thread and the thread cleared it at the end,
        with no ``finally`` - so if the thread failed to start, or died before
        that line, the latch stayed set and the widget never polled again for
        the life of the process. Nothing was logged either, because
        ``WidgetCard._tick`` swallows exceptions to keep a flaky data source
        from taking the board down. The card sat on "Nothing playing" while
        music played, and only relaunching fixed it - until the next time.

        A thread that starts once cannot fail to start on the ten-thousandth
        poll, and there is no latch left to get stuck. It also stops creating
        about 170,000 threads a day to ask the same question.
        """
        import threading
        import time as _t

        if self._poller is not None and self._poller.is_alive():
            return
        # Staleness is measured from when the poller started, not from zero.
        # Left at zero it reads as "no reading since the machine booted", so a
        # widget that had been alive for two seconds reported being stale for
        # three days and the watchdog cried wolf in the log every time.
        self._last_result = self._last_watch = _t.monotonic()
        self._stop_poll.clear()
        self._poller = threading.Thread(target=self._poll_loop, daemon=True,
                                        name="media-poll")
        try:
            self._poller.start()
        except RuntimeError as exc:
            # Out of threads, which a Windows login can genuinely be. Leave it
            # unstarted; the next tick tries again rather than giving up.
            self._poller = None
            _log(f"media: could not start the poll thread ({exc!r})")

    def _poll_loop(self) -> None:
        import time as _t

        first = True
        sig = None
        backend = ""                 # kept as a local so py-spy can read it
        wait = POLL_EVERY
        while not self._stop_poll.wait(0.0 if first else wait):
            first = False
            try:
                got = self.source.poll()
            except Exception as exc:
                _log(f"media: poll failed ({exc!r})")
                continue
            # Which backend produced this, in plain sight. Both backends can
            # report source="Apple Music" with nothing playing, so from outside
            # the process the two were indistinguishable - which cost a day of
            # inferring what could have simply been stated.
            if self.source.backend != backend:
                backend = self.source.backend
                # Reading through a separate process costs a few hundred
                # milliseconds a call, so slow down rather than spend a core
                # spawning interpreters. Twice a second is for a cheap read.
                wait = POLL_HELPER if backend == "helper" else POLL_EVERY
                _log(f"media: reading via {backend} "
                     f"(winrt importable: {self.source.winrt_ok})")
            self.np = got
            self._last_result = _t.monotonic()
            # Only wake the GUI thread when the card would actually differ.
            # Every signal costs a full recompose - artwork, scrim, text and
            # three frosted discs, about 5.6ms - and firing twice a second for
            # a paused track that has not changed spent that for nothing. The
            # position is included at one-second resolution, so the progress
            # bar still moves while something is playing.
            now_sig = (got.title, got.artist, got.source, got.playing,
                       got.available, got.art is not None,
                       int(got.position) if got.position is not None else -1)
            if now_sig == sig:
                continue
            sig = now_sig
            self.data_ready.emit()

    def on_tick(self) -> None:
        import time as _t

        self._pulse += self.refresh_ms / 1000.0
        self._animate()

        # Ask for the cheap repaint path whenever the bars have something to
        # show - which is exactly when a track is playing, and never otherwise.
        # It used to also animate on any sound the tap could hear, session or
        # no session; that is what let a voice call drive them.
        if self._viz_rect is not None:
            self._viz_phase += self.refresh_ms / 1000.0
            if self.np.playing and self.np.available and not self._dirty:
                self.update()

        # Watch the poller rather than trust it. If it dies or wedges, this is
        # the only thing that will notice - and last time nothing did, which is
        # why the card stayed blank for days with an empty log.
        now = _t.monotonic()
        if now - self._last_watch < WATCH_EVERY:
            return
        self._last_watch = now
        alive = self._poller is not None and self._poller.is_alive()
        stale = now - self._last_result
        if not alive:
            _log("media: poll thread is not running; restarting it")
            self._start_poller()
        elif stale > STALE_AFTER:
            # Alive but not producing: a blocked call inside the media API.
            # Say so once per window instead of silently showing nothing.
            _log(f"media: no reading for {stale:.0f}s - the poll thread is "
                 f"alive but blocked in {self.source.backend}")
            self._last_result = now          # log once per STALE_AFTER, not per tick

    def _check_artwork(self) -> None:
        """Start a cross-fade when the cover changes.

        Identity is the track, plus whether a cover exists yet - never the image
        object. The backend decodes the thumbnail afresh on every poll, so a new
        ``QImage`` (and a new ``cacheKey``) arrives twice a second for a track
        that has not changed. Keying on that restarted the fade endlessly, and
        because every fade frame forces a full recompose it pinned the board at
        60% of a core. Identity has to mean "a different sleeve", not "a
        different object".

        The `has cover` half matters because metadata and artwork do not arrive
        together: the title lands first and the thumbnail follows a poll or two
        later, which should fade in rather than appear.

        Called from the data signal rather than from paint - starting an
        animation while painting it is the other way to make a fade restart
        every frame and never finish.
        """
        art = self.np.art
        has = art is not None and not art.isNull()
        ident = (self._track_key(), has)
        if ident == self._art_id:
            # Same sleeve, freshly decoded: keep the newest copy, no fade.
            if has:
                self._art_shown = art
            return

        had = self._art_id is not None
        self._art_id = ident
        self._art_prev = self._art_shown
        self._art_shown = art if has else None
        if had and (self._art_prev is not None or has):
            self._art_fade.start(420.0)
        else:
            self._art_fade.finish()      # first cover: nothing to dissolve from

    def context_actions(self, menu) -> None:
        from PySide6.QtGui import QAction, QActionGroup

        # Which player the card follows. "Automatic" takes whichever is
        # playing; naming one pins the card to it, so the other left open in
        # the background can't take the card over mid-track.
        player = menu.addMenu("Player")
        group = QActionGroup(player)
        group.setExclusive(True)
        for key, label in media.PLAYER_CHOICES:
            act = QAction(label, player, checkable=True)
            act.setChecked(self.source.prefer == key)
            act.triggered.connect(lambda _c=False, k=key: self._set_player(k))
            group.addAction(act)
            player.addAction(act)

        # Say which backend is live, and say so loudly when it is the degraded
        # one - this failing quietly is what left the card blind for days.
        if self.source.degraded:
            state = "Reading window titles - media API failed, retrying"
        elif self.source.backend == "winrt":
            state = "Reading the Windows media session"
        else:
            state = "Reading window titles (no media API on this machine)"
        a = QAction(state, menu)
        a.setEnabled(False)
        menu.addAction(a)

        viz = QAction("Audio visualiser", menu, checkable=True)
        viz.setChecked(self._tap_wanted)
        viz.toggled.connect(self._set_visualiser)
        menu.addAction(viz)

        # Say plainly what it is doing and where the sound is coming from.
        if not self._tap_wanted:
            described = "Not listening"
        elif not self._tap_on:
            described = "Idle - listens only while a track plays"
        else:
            described = {
                "spectrum": f"Reading {self._tap.detail if self._tap else ''}",
                "level":    "Output level only - no spectrum on this machine",
                "off":      "No audio source; bars are animated",
            }[self._tap.kind if self._tap else "off"]
        note = QAction(described, menu)
        note.setEnabled(False)
        menu.addAction(note)

    def _pinned_label(self) -> str:
        """The chosen player's name, or "" when following whichever plays."""
        for key, label in media.PLAYER_CHOICES:
            if key == self.source.prefer:
                return "" if key == "auto" else label
        return ""

    def _set_player(self, key: str) -> None:
        """Follow one player, or whichever is playing."""
        if key == self.source.prefer:
            return
        self.source.set_preference(key)
        self.config["player"] = key
        # The old track is gone as of set_preference; clear what was drawn from
        # it too, or the cover cross-fades out of a sleeve that is no longer
        # anybody's.
        self.np = media.NowPlaying()
        self._on_data()                  # closes the tap and repaints now
        self.changed.emit()              # persist the choice

    def _set_visualiser(self, on: bool) -> None:
        self._tap_wanted = bool(on)
        self.config["visualiser"] = self._tap_wanted
        if on and self._tap is None:
            self._tap = audio.acquire()
        elif not on and self._tap is not None:
            self._tap = None
            if self._tap_on:
                self._tap_on = False
                audio.listen(False)
            audio.release()
        self._sync_tap()             # switching it on mid-track starts it now
        self.changed.emit()          # persist the choice
        self._repaint()

    # ------------------------------------------------------------------ #
    #  Painting
    # ------------------------------------------------------------------ #
    def paint_content(self, p: QPainter, r: QRectF) -> None:
        pal = self.palette_
        np = self.np
        card = self.card_rect()

        small = self.size_class == "small"
        btn_r = 23.0 if not small else 17.0
        body = QRectF(r.left(), r.top(), r.width(), r.height())

        self._paint_artwork(p, card, np)

        # --- title ------------------------------------------------------ #
        title = np.title or ("Nothing playing" if not np.available else "—")
        # Name the pinned player when there is nothing to show, or "Nothing
        # playing" under a card pinned to Spotify while Apple Music is going
        # looks like a fault rather than the setting it is.
        subtitle = np.artist or np.source or self._pinned_label()
        if not np.available and not self.source.use_winrt:
            # The card cannot read this player and should say so. "Nothing
            # playing" while music is audibly playing is the single most
            # misleading thing this widget has ever shown, and it showed it for
            # days.
            subtitle = "can't read player - retrying"

        # The visualiser owns the top-right corner, as on iOS, so the heart moves
        # left of it and the title gives up the room for both.
        if small:
            self._viz_rect = None
        else:
            bar_w = 2.6
            span = bar_w * (VIZ_BARS * 2 - 1)
            self._viz_rect = QRectF(body.right() - span, body.top() + 6.0,
                                    span, 18.0)

        tw = body.width() - (76 if not small else 0)
        self.text(p, QRectF(body.left(), body.top(), tw, 20),
                  self._elide(p, title, tw, 12.5, QFont.DemiBold),
                  12.5, pal.text if np.available else pal.text_dim,
                  QFont.DemiBold)
        if subtitle and not small:
            self.text(p, QRectF(body.left(), body.top() + 18, tw, 16),
                      self._elide(p, subtitle, tw, 9.5), 9.5, pal.text_dim)

        # --- progress --------------------------------------------------- #
        # Sits just above the card's lower padding, clear of the transport row
        # that straddles the edge below it.
        if not small:
            self._paint_progress(p, QRectF(body.left(), body.bottom() - 24,
                                           body.width(), 24), np)

        # --- controls --------------------------------------------------- #
        # Only their geometry, registered here and frosted in paintEvent once the
        # buffer holds everything they have to refract. Hit radii are the resting
        # ones, so a disc that shrank under the finger can't slip out from
        # under it.
        self._discs = []
        if not small:
            like = QPointF(body.right() - 44, body.top() + 13)
            self._discs.append(("like", like, 14.0, 14.0))
        self._discs.extend(self._transport(card, body, btn_r, small))
        self._buttons = [(n, c, hit) for n, c, _rad, hit in self._discs]

    # ------------------------------------------------------------------ #
    def _transport(self, card: QRectF, body: QRectF, btn_r: float,
                   small: bool) -> List[Tuple[str, QPointF, float, float]]:
        """Where the three discs sit, back to front.

        Centred on the card's bottom edge - the reference's defining detail.
        ``extra_bottom`` reserves the window height that overhang needs.
        """
        cy = card.bottom() if not small else body.bottom() + 4
        cx = card.center().x()
        side_r = btn_r * 0.70
        t = self._spread

        # Resting state overlaps the discs, the way the reference does; hovering
        # slides them apart and grows them slightly.
        #
        # About a third of each side disc sits behind the play glass at rest.
        # Overlapping at all was no good while the discs were opaque - it simply
        # ate the arrows - but frosted glass refracts them instead, which is the
        # whole effect: the button underneath goes soft rather than disappearing.
        # A third is the balance point. Deeper and the inner arrow diffuses away
        # to nothing, which loses the overlap as well as the arrow.
        closed = btn_r + side_r * 0.30
        opened = btn_r + side_r + 9.0
        gap = closed + (opened - closed) * t
        grow = 1.0 + 0.07 * t

        # Back to front: the outer two first, or their hairlines would cut
        # across the play button.
        out: List[Tuple[str, QPointF, float, float]] = []
        for name, dx, rad in (("prev", -gap, side_r), ("next", gap, side_r),
                              ("play", 0.0, btn_r)):
            rest = rad * grow
            # A press sinks the disc slightly. Brightening alone is easy to miss
            # on artwork that is already bright.
            drawn = rest * (0.93 if self._pressed == name else 1.0)
            out.append((name, QPointF(cx + dx, cy), drawn, rest))
        return out

    def _paint_artwork(self, p: QPainter, card: QRectF, np) -> None:
        """Art behind the glass, or a tinted stand-in derived from the track."""
        clip = squircle(card, RADIUS)
        p.save()
        p.setClipPath(clip)

        # Cross-fade on a track change: the outgoing sleeve falls as the new one
        # rises. Both are drawn cover-filled at the same crop, so the dissolve
        # happens in place rather than sliding.
        t = self._art_fade.value
        art = self._art_shown if self._art_id is not None else np.art
        prev = self._art_prev if self._art_fade.active else None

        def cover(image, alpha: float) -> None:
            if image is None or image.isNull() or alpha <= 0.004:
                return
            # Covers arrive at full size - 1200x1200 is normal - and smooth-
            # scaling one down to a 348px card is most of what a repaint costs.
            # The result only depends on the image and the card, so it is worth
            # keeping: without this the fade paid for it twice a frame.
            key = (image.cacheKey(), int(card.width()), int(card.height()))
            scaled = self._scaled.get(key)
            if scaled is None:
                scaled = image.scaled(int(card.width()), int(card.height()),
                                      Qt.KeepAspectRatioByExpanding,
                                      Qt.SmoothTransformation)
                if len(self._scaled) > 4:
                    self._scaled.clear()      # only ever two are live at once
                self._scaled[key] = scaled
            sx = max(0, (scaled.width() - int(card.width())) // 2)
            sy = max(0, (scaled.height() - int(card.height())) // 2)
            p.setOpacity(0.55 * alpha)
            p.drawImage(card, scaled,
                        QRectF(sx, sy, card.width(), card.height()))
            p.setOpacity(1.0)

        if art is not None and not art.isNull():
            # The tint sits underneath during a fade *from* nothing, so the new
            # sleeve rises out of the placeholder instead of out of a hole.
            if prev is None and t < 1.0:
                self._paint_tint(p, card, np)
            cover(prev, 1.0 - t)
            cover(art, t if self._art_fade.active else 1.0)
        elif prev is not None:
            # Fading *to* no artwork: the tint is already there to land on.
            self._paint_tint(p, card, np)
            cover(prev, 1.0 - t)
        else:
            self._paint_tint(p, card, np, note=True)

        # Scrim, so the type stays legible over any picture.
        scrim = QLinearGradient(card.topLeft(), card.bottomLeft())
        scrim.setColorAt(0.0, QColor(0, 0, 0, 96))
        scrim.setColorAt(0.45, QColor(0, 0, 0, 40))
        scrim.setColorAt(1.0, QColor(0, 0, 0, 120))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(scrim))
        p.drawRect(card)
        p.restore()

        # A bright hairline around the glass. The other cards deliberately have
        # no border, but the reference's outlined frame is most of what makes it
        # read as a pane of glass rather than a panel, so this one gets it.
        #
        # It runs straight under the transport row and that is now fine: the
        # discs frost it along with everything else, so it passes behind them
        # softened instead of reading as a scar across each button. It used to be
        # clipped to leave gaps, which was only ever a workaround for glass that
        # did not actually refract.
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 92), 1.3))
        p.drawPath(clip)

    def _paint_tint(self, p: QPainter, card: QRectF, np, note: bool = False) -> None:
        """The stand-in when there is no artwork - also the floor a fade lands on."""
        r_, g_, b_ = np.tint()
        grad = QLinearGradient(card.topLeft(), card.bottomRight())
        grad.setColorAt(0.0, QColor(r_, g_, b_, 92 if np.available else 34))
        grad.setColorAt(1.0, QColor(b_, r_, g_, 40 if np.available else 14))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRect(card)
        if note and np.available:
            self._draw_note(p, card, QColor(255, 255, 255, 30))

    def _paint_progress(self, p: QPainter, area: QRectF, np) -> None:
        frac = np.progress
        h = 4.0
        bar = QRectF(area.left(), area.top(), area.width(), h)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 74))
        p.drawRoundedRect(bar, h / 2, h / 2)

        if frac is not None:
            p.setBrush(QColor(255, 255, 255, 245))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(),
                                     max(h, bar.width() * frac), h), h / 2, h / 2)

        row = QRectF(area.left(), area.top() + h + 5, area.width(), 15)
        if frac is None:
            # No position data from this backend - say so rather than show 0:00.
            self.text(p, row, "position unavailable", 8.5,
                      QColor(255, 255, 255, 132).name(QColor.HexArgb))
            return

        # White at reduced alpha, not the palette grey: these sit on artwork, so
        # they have to hold up against whatever is behind them.
        soft = "#c8ffffff"
        self.text(p, row, media.fmt(np.position), 9, soft, QFont.Medium)
        self.text(p, row, media.fmt((np.duration or 0) - (np.position or 0), True),
                  9, soft, QFont.Medium, align=Qt.AlignRight | Qt.AlignVCenter)

    # ------------------------------------------------------------------ #
    #  Controls
    # ------------------------------------------------------------------ #
    def paintEvent(self, ev) -> None:
        """Render into an offscreen buffer, then hand it to the window.

        The transport discs are real frosted glass: each one blurs the pixels
        already underneath it. That means those pixels have to be *readable*, and
        a QWidget's own painter cannot be read back - so the card paints itself
        into an image first, the discs are frosted into that image back to front,
        and the finished thing is blitted out in one go.
        """
        stale = (self._buf is None or self._buf.size() != self.size()
                 or self._dirty or self._art_fade.active)

        if stale:
            if self._buf is None or self._buf.size() != self.size():
                self._buf = QImage(max(1, self.width()), max(1, self.height()),
                                   QImage.Format_ARGB32_Premultiplied)
            self._buf.fill(Qt.transparent)

            bp = QPainter(self._buf)
            # Full opacity into the buffer, faded once on the way out, so a
            # translucent board doesn't make the glass denser than the card.
            self.paint_card(bp, 1.0)
            bp.end()

            for name, centre, rad, _hit in self._discs:
                self._frost_disc(self._buf, name, centre, rad)

            # Keep a clean copy of what sits behind the bars, so an animation
            # frame can put it back without redoing any of the above.
            self._viz_backing = (self._buf.copy(self._viz_rect.toAlignedRect())
                                 if self._viz_rect is not None else None)
            self._dirty = False

        elif self._viz_backing is not None and self._viz_rect is not None:
            # Animation frame: erase the last bars by restoring the strip, then
            # draw the new ones. Costs a small blit instead of three blurs.
            rp = QPainter(self._buf)
            rp.setCompositionMode(QPainter.CompositionMode_Source)
            rp.drawImage(self._viz_rect.toAlignedRect().topLeft(),
                         self._viz_backing)
            rp.end()

        vp = QPainter(self._buf)
        vp.setRenderHint(QPainter.Antialiasing, True)
        self._draw_viz(vp)
        vp.end()

        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(self.opacity_)
        p.drawImage(0, 0, self._buf)
        p.end()

    def _frost_disc(self, buf: QImage, name: str, c: QPointF,
                    rad: float) -> None:
        """Composite one disc as frosted glass, refracting whatever is behind it.

        Everything already in the buffer under the circle is copied out, blurred,
        and painted back through the circle - so the disc frosts the artwork, the
        progress bar, the card's own bright edge, and, where they overlap, the
        disc beneath it. That last one is the point: at rest the three discs sit
        on top of each other, and the ones underneath should go soft behind the
        glass rather than being hidden by an opaque pill.

        Outside the card the buffer is transparent - that is the window's shadow
        gutter, where the desktop shows through - so the blurred wallpaper goes
        down first and the buffer composites over it. The play button overhangs
        the card's bottom edge, and without that its lower half would have
        nothing to frost.
        """
        pad = 3
        box = QRect(int(c.x() - rad) - pad, int(c.y() - rad) - pad,
                    int(rad * 2) + pad * 2 + 2, int(rad * 2) + pad * 2 + 2)
        box = box.intersected(buf.rect())
        if box.isEmpty():
            return

        rect = QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2)
        hover = self._hover_btn == name
        pressed = self._pressed == name

        # The heart is a bare mark until you go for it; a permanent pane of glass
        # around a 10px glyph weighs more than the glyph.
        if name == "like" and not (hover or pressed):
            hp = QPainter(buf)
            hp.setRenderHint(QPainter.Antialiasing, True)
            self._draw_heart(hp, c, filled=self._is_liked)
            hp.end()
            return

        # --- a short contact shadow, laid down before the glass reads the
        #     pixels, because an object's shadow really is behind it ---------- #
        sp = QPainter(buf)
        sp.setRenderHint(QPainter.Antialiasing, True)
        sp.setPen(Qt.NoPen)
        for grow, dy, alpha in ((2.6, 1.5, 13), (1.2, 0.8, 15)):
            sp.setBrush(QColor(0, 0, 0, alpha))
            sp.drawEllipse(rect.adjusted(-grow, -grow + dy, grow, grow + dy))
        sp.end()

        # --- what is behind ---------------------------------------------- #
        behind = QImage(box.size(), QImage.Format_ARGB32_Premultiplied)
        behind.fill(Qt.transparent)
        pp = QPainter(behind)
        pp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        laid = False
        if self.glass is not None:
            laid = self.glass.paint_into(pp, behind.rect(),
                                         self.x() + box.x(), self.y() + box.y())
        if not laid:
            # Frosted glass off, or no wallpaper: give the overhang something
            # opaque so the blur has no transparent pixels to smear.
            fallback = QColor(self.palette_.card)
            fallback.setAlpha(255)
            pp.fillRect(behind.rect(), fallback)
        pp.drawImage(0, 0, buf.copy(box))
        pp.end()

        frost = self._frost(behind)

        # --- the glass itself -------------------------------------------- #
        dp = QPainter(buf)
        dp.setRenderHint(QPainter.Antialiasing, True)
        dp.setRenderHint(QPainter.SmoothPixmapTransform, True)

        circle = QPainterPath()
        circle.addEllipse(rect)
        dp.save()
        dp.setClipPath(circle)
        dp.drawImage(box.topLeft(), frost)

        # Frost scatters light as well as scattering the image, so it lifts what
        # it covers - a blurred copy on its own comes out as dark as the artwork
        # and the disc reads as a hole rather than as glass. Still a wash and not
        # a fill: a heavy one is what made these look moulded.
        lift = (10 if hover else 0) + (16 if pressed else 0)
        wash = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        wash.setColorAt(0.0, QColor(255, 255, 255, 46 + lift))
        wash.setColorAt(1.0, QColor(255, 255, 255, 26 + lift))
        dp.setPen(Qt.NoPen)
        dp.setBrush(QBrush(wash))
        dp.drawRect(rect)

        # The lit top edge of a real pane, a hairline inside the rim rather than
        # a gradient washing down the face - that broad sheen was most of what
        # read as moulded plastic.
        edge = QRectF(rect.adjusted(1.6, 1.6, -1.6, -1.6))
        dp.setBrush(Qt.NoBrush)
        dp.setPen(QPen(QColor(255, 255, 255, 60 if hover else 44), 1.2))
        dp.drawArc(edge, 28 * 16, 124 * 16)
        dp.restore()

        # A thin bright edge all the way round - the tell that a pane of glass
        # has a thickness. Inset by half the stroke so it stays inside the disc
        # and cannot fringe against the disc below.
        rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        rim.setColorAt(0.0, QColor(255, 255, 255, 168 if hover else 142))
        rim.setColorAt(0.5, QColor(255, 255, 255, 96 if hover else 80))
        rim.setColorAt(1.0, QColor(255, 255, 255, 132 if hover else 112))
        dp.setBrush(Qt.NoBrush)
        dp.setPen(QPen(QBrush(rim), 1.0))
        dp.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # --- the mark, which sits on the glass, not behind it -------------- #
        if name == "play":
            self._draw_play(dp, c, rad, self.np.playing)
        elif name == "like":
            self._draw_heart(dp, c, filled=self._is_liked)
        else:
            self._draw_skip(dp, c, rad, forward=name == "next")
        dp.end()

    def _draw_viz(self, p: QPainter) -> None:
        """The bars, top right - iOS's mark for "this is the track playing".

        Bars grow from a centre line rather than a floor, which is what makes a
        row of five short strokes read as a level meter instead of a bar chart.
        Paused freezes them at a fixed resting shape and dims them, the way iOS
        does; a frozen-but-bright meter looks broken rather than paused.
        """
        rect = self._viz_rect
        np = self.np
        if rect is None or not np.available:
            return

        # The bars belong to the track on the card, so the track is what decides
        # whether they exist and whether they move. They used to appear for any
        # sound the tap could hear, which read well in theory and in practice
        # meant a friend on a call was driving the music widget's meter.
        tap = self._tap
        bands = (tap.levels() if (tap is not None and tap.kind != "off"
                                  and np.playing) else None)
        heard = bands is not None and max(bands, default=0.0) > 0.015
        live = np.playing

        bw = rect.width() / (VIZ_BARS * 2 - 1)      # bars and gaps equal width
        cy = rect.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 236 if live else 130))

        for i in range(VIZ_BARS):
            if heard:
                # The real thing. A floor keeps a silent band as a dot rather
                # than as a gap, so five bars stay legible as one object.
                level = 0.14 + 0.86 * bands[i]
            elif live:
                t = self._viz_phase
                a = 0.5 + 0.5 * math.sin(t * VIZ_SPEED[i] + VIZ_PHASE[i])
                b = 0.5 + 0.5 * math.sin(t * VIZ_SPEED[i] * 1.7
                                         + VIZ_PHASE[i] * 2.1)
                level = 0.24 + 0.76 * (0.65 * a + 0.35 * b)
            else:
                level = VIZ_RESTING[i]
            h = max(bw, rect.height() * level)
            x = rect.left() + i * bw * 2
            p.drawRoundedRect(QRectF(x, cy - h / 2, bw, h), bw / 2, bw / 2)

    @staticmethod
    def _frost(patch: QImage) -> QImage:
        """Blur a patch by the same recipe as the wallpaper: heavy downscale, box
        passes at that small size, smooth upscale.

        Tuned so what is underneath stays *recognisable* - a skip arrow behind
        the play button should read as a skip arrow gone soft. Blur it harder and
        the overlap turns to fog, which loses the effect entirely.
        """
        w, h = patch.width(), patch.height()
        small = patch.scaled(max(4, w // 4), max(4, h // 4),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        small = glass_blur(small, 1, 2)
        return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def _glyph(self, p: QPainter, path: QPainterPath, alpha: int = 244,
               soften: float = 1.4) -> None:
        """Fill a glyph and round its corners.

        Stroking the outline with a round join is the whole trick. At twenty-odd
        pixels a mathematically sharp triangle tip reads as a spike, and that is
        the single thing that made the old transport look crude; every one of
        Apple's play and skip marks has visibly rounded vertices. Winding fill
        is required because the skip glyph is two overlapping triangles, and the
        default odd-even rule would punch a hole through the overlap.
        """
        path.setFillRule(Qt.WindingFill)
        col = QColor(255, 255, 255, alpha)
        p.setBrush(col)
        p.setPen(QPen(col, soften, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)

    def _draw_play(self, p: QPainter, c: QPointF, rad: float,
                   playing: bool) -> None:
        if playing:
            bw = rad * 0.175
            h = rad * 0.76
            gap = rad * 0.15
            path = QPainterPath()
            for side in (-1, 1):
                path.addRoundedRect(
                    QRectF(c.x() + side * (gap + bw / 2) - bw / 2,
                           c.y() - h / 2, bw, h), bw * 0.46, bw * 0.46)
            self._glyph(p, path, 246, 0.8)
        else:
            # Nudged right: a triangle carries its mass behind the tip, so one
            # centred on the geometry looks like it is sliding off to the left.
            s = rad * 0.44
            cx = c.x() + rad * 0.05
            path = QPainterPath()
            path.moveTo(cx - s * 0.60, c.y() - s * 0.95)
            path.lineTo(cx + s * 0.88, c.y())
            path.lineTo(cx - s * 0.60, c.y() + s * 0.95)
            path.closeSubpath()
            self._glyph(p, path, 246, rad * 0.075)

    def _draw_skip(self, p: QPainter, c: QPointF, rad: float,
                   forward: bool) -> None:
        """Two triangles, tip to flat edge - SF Symbols' forward.fill.

        The old mark was one triangle plus a bar, and the bar needed a fudge
        offset in one direction only, so prev and next were not mirror images of
        each other. Two triangles need no fudge and read instantly.
        """
        d = 1.0 if forward else -1.0
        s = rad * 0.36
        w = s * 0.60                    # half-width of one triangle
        path = QPainterPath()
        for i in (-0.5, 0.5):
            ox = d * i * w * 1.9        # the second's flat edge meets the first's tip
            path.moveTo(c.x() + ox - d * w, c.y() - s * 0.86)
            path.lineTo(c.x() + ox + d * w, c.y())
            path.lineTo(c.x() + ox - d * w, c.y() + s * 0.86)
            path.closeSubpath()
        self._glyph(p, path, 236, rad * 0.085)

    def _draw_heart(self, p: QPainter, c: QPointF, filled: bool) -> None:
        h = QPainterPath()
        s = 5.0
        h.moveTo(c.x(), c.y() + s * 0.92)
        h.cubicTo(c.x() - s * 1.5, c.y() - s * 0.10,
                  c.x() - s * 0.98, c.y() - s * 1.32, c.x(), c.y() - s * 0.42)
        h.cubicTo(c.x() + s * 0.98, c.y() - s * 1.32,
                  c.x() + s * 1.5, c.y() - s * 0.10, c.x(), c.y() + s * 0.92)
        h.closeSubpath()
        if filled:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ff5a6e"))
            p.drawPath(h)
        else:
            # Outline only, with a whisper of shadow so it survives light art.
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 48), 2.6,
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(h)
            p.setPen(QPen(QColor(255, 255, 255, 236), 1.4,
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(h)

    def _draw_speaker(self, p: QPainter, c: QPointF, rad: float,
                      muted: bool) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 226))
        body = QPainterPath()
        body.moveTo(c.x() - 4.5, c.y() - 2.2)
        body.lineTo(c.x() - 1.6, c.y() - 2.2)
        body.lineTo(c.x() + 1.8, c.y() - 5.2)
        body.lineTo(c.x() + 1.8, c.y() + 5.2)
        body.lineTo(c.x() - 1.6, c.y() + 2.2)
        body.lineTo(c.x() - 4.5, c.y() + 2.2)
        body.closeSubpath()
        p.drawPath(body)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 226), 1.3, Qt.SolidLine, Qt.RoundCap))
        if muted:
            p.drawLine(QPointF(c.x() + 3.4, c.y() - 3.4),
                       QPointF(c.x() + 7.0, c.y() + 3.4))
            p.drawLine(QPointF(c.x() + 7.0, c.y() - 3.4),
                       QPointF(c.x() + 3.4, c.y() + 3.4))
        else:
            p.drawArc(QRectF(c.x() + 0.6, c.y() - 4.4, 6.4, 8.8), -70 * 16, 140 * 16)

    def _draw_note(self, p: QPainter, card: QRectF, colour: QColor) -> None:
        """A large soft music note, for when there is no artwork."""
        p.setPen(Qt.NoPen)
        p.setBrush(colour)
        cx = card.right() - card.width() * 0.19
        cy = card.center().y()
        s = min(card.width(), card.height()) * 0.20
        p.drawEllipse(QRectF(cx - s * 0.75, cy + s * 0.25, s * 0.72, s * 0.55))
        p.drawEllipse(QRectF(cx + s * 0.15, cy - s * 0.05, s * 0.72, s * 0.55))
        p.setPen(QPen(colour, s * 0.15, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - s * 0.06, cy + s * 0.5),
                   QPointF(cx - s * 0.06, cy - s * 0.75))
        p.drawLine(QPointF(cx + s * 0.84, cy + s * 0.22),
                   QPointF(cx + s * 0.84, cy - s * 1.0))
        p.drawLine(QPointF(cx - s * 0.06, cy - s * 0.75),
                   QPointF(cx + s * 0.84, cy - s * 1.0))

    # ------------------------------------------------------------------ #
    def _elide(self, p: QPainter, text: str, width: float,
               size: float, weight: int = QFont.Normal) -> str:
        p.setFont(self.font(size, weight))
        fm = p.fontMetrics()
        if fm.horizontalAdvance(text) <= width:
            return text
        return fm.elidedText(text, Qt.ElideRight, int(width))

    # ------------------------------------------------------------------ #
    #  Interaction
    # ------------------------------------------------------------------ #
    def _hit(self, pos) -> Optional[str]:
        # Reversed, i.e. topmost first. While the discs overlap, the play button
        # is drawn last and must also win the click in the shared area.
        for name, centre, rad in reversed(self._buttons):
            # A little generous: these are small targets on a floating card.
            if (pos.x() - centre.x()) ** 2 + (pos.y() - centre.y()) ** 2 <= (rad + 3) ** 2:
                return name
        return None

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            hit = self._hit(ev.position())
            if hit:
                # A button press must not also start a drag.
                self._pressed = hit
                self._repaint()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        # Per-button hover. ``_hover_btn`` existed but nothing ever set it, so
        # the discs were inert until clicked - the one thing that made them feel
        # like decoration rather than controls.
        hit = None if self._drag_from is not None else self._hit(ev.position())
        if hit != self._hover_btn:
            self._hover_btn = hit
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self._repaint()
        super().mouseMoveEvent(ev)

    def enterEvent(self, ev) -> None:
        self._dirty = True          # the card draws a hover ring
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover_btn = None
        self._pressed = None
        self._dirty = True
        self.unsetCursor()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._pressed:
            name, self._pressed = self._pressed, None
            if self._hit(ev.position()) == name:
                self._activate(name)
            self._repaint()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def _activate(self, name: str) -> None:
        if name == "play":
            media.play_pause()
            # Reflect it immediately; the backend won't catch up for a poll or
            # two and a control that looks inert feels broken.
            self.np.playing = not self.np.playing
        elif name == "next":
            media.next_track()
        elif name == "prev":
            media.prev_track()
        elif name == "like":
            key = self._track_key()
            if key:
                self._liked.discard(key) if key in self._liked else self._liked.add(key)
                self._save_liked()
        self._repaint()
