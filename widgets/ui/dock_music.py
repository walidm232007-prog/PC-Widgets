"""The music menu: click the dock's music and it rises above it.

The cover, the title, artist and player; how far through the track it is;
then one row of previous, play or pause, next and the volume. It is the dock's
own capsule - its squircle corners, shadow, glass or matte fill and neon,
drawn by the dock's own code - see-through like the dock, so it fades in as it
slides up, and out as it sinks (see dock_panel). The first version was an
opaque panel with Windows' rounder, smaller corners, like the window previews,
which need one; this needs nothing of the sort, so it matches the dock.

The volume is Windows' own - the default output's, as the slider in quick
settings sets it - not the player's, so it is the same number the dock's
speaker shows and the volume keys change.

The menu never takes the focus, like the dock, so a game or a document keeps
the keyboard. It learns of a click outside it by watching the mouse buttons
while it is open, 30 times a second; nothing watches once it closes.

The track's position comes from the player at most once a second; between
readings it is counted on locally while playing, so the bar moves smoothly.
Play and pause show the new state at once, before the player confirms it.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainterPath, QPixmap

from ..core import dock_shell as shell, media, typography
from ..core.card import squircle
from .dock import RADIUS, NeonRing, paint_material
from .dock_panel import DockPanel

# At 100% scaling. MARGIN is the clear room round the capsule for its shadow
# and the neon's glow, as the dock keeps round its own.
WIDTH, PAD, ART, MARGIN = 264, 12, 48, 16
PLAY, SKIP, MUTE = 32, 28, 26   # the round buttons' diameters
NOTE_GLYPH = ""           # Segoe Fluent Icons' MusicNote
MUTE_GLYPH = ""
VOLUME_GLYPHS = ("", "", "", "")   # none, low, medium, high
WHEEL_STEP = 0.02


class MusicPanel(DockPanel):
    TITLE = "PC Widgets Glass Dock music"
    TRANSLUCENT = True

    def __init__(self, dock):
        super().__init__(dock)
        self._anchor = (0, 0)
        self._hover = self._pressed = ""
        self._rects = {}              # control -> rect, in content coordinates
        self._capsule = self._art_rect = self._bar = self._times = QRectF()
        self._neon = NeonRing()
        self._art = {}                # (cacheKey, px) -> QPixmap: two at most, mid-fade
        self._speakers = None         # made on first use, on this thread
        self._volume = None           # (level, muted) as last read or set
        self._dragging = False
        self._override = None         # (playing, until): shown before the player confirms
        self._was_down = False
        self._reads = 0
        self._fonts = None
        self._tick = QTimer(self)     # the bar and the volume, while open
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._on_tick)
        self._watch = QTimer(self)    # a click anywhere else closes it
        self._watch.setInterval(30)
        self._watch.timeout.connect(self._on_watch)

    # -- showing ---------------------------------------------------------- #
    def open(self, cx, top):
        """Above the dock's music, whose centre is at x = cx, the dock's top at y = top."""
        self._anchor = (cx, top)
        self._volume = self._read_volume()
        self._was_down = shell.buttons_down()
        self._tick.start()
        self._watch.start()
        self.present()

    def refresh(self):
        """A new track, or the player started or stopped: the menu follows."""
        if self.up:
            self.present()

    def release(self):
        self._tick.stop()
        self._watch.stop()
        self._hover = self._pressed = ""
        self._dragging = False

    def _track(self):
        """(NowPlaying, its position now): the latest reading, counted on."""
        live = getattr(self.dock, "_live", None)
        np, when = live if live is not None else (self.dock._np, time.monotonic())
        position = np.position
        if position is not None and self._playing(np):
            position += time.monotonic() - when
        if position is not None and np.duration:
            position = min(position, np.duration)
        return np, position

    def _playing(self, np):
        if self._override is not None:
            playing, until = self._override
            if np.playing == playing or time.monotonic() > until:
                self._override = None
            else:
                return playing
        return np.playing

    def layout(self) -> QRect:
        """The capsule, MARGIN in from the window's edges: the cover and the
        track; the progress bar, when the player gives a length; then one row
        of previous, play or pause, next, the speaker and the volume."""
        s = self.dock._s
        np, _ = self._track()
        m, w = round(MARGIN * s), round(WIDTH * s)
        left, right = m + round(PAD * s), m + w - round(PAD * s)
        self._art_rect = QRectF(left, m + round(PAD * s), round(ART * s), round(ART * s))
        y = self._art_rect.bottom() + 10 * s
        if np.available and np.duration:
            self._bar = QRectF(left, round(y), right - left, round(3 * s))
            self._times = QRectF(left, round(y + 6 * s), right - left, round(12 * s))
            y += 24 * s
        else:
            self._bar = self._times = QRectF()
        cy = y + PLAY / 2 * s

        def disc(cx, d):
            return QRectF(round(cx - d / 2 * s), round(cy - d / 2 * s), round(d * s), round(d * s))
        self._rects = {"prev": disc(left + 14 * s, SKIP), "play": disc(left + 50 * s, PLAY),
                       "next": disc(left + 86 * s, SKIP), "mute": disc(left + 120 * s, MUTE)}
        self._rects["percent"] = QRectF(right - 30 * s, cy - 7 * s, 30 * s, 14 * s)
        track = left + 138 * s
        self._rects["volume"] = QRectF(track, cy - 1.5 * s, right - 36 * s - track, 3 * s)
        h = round(cy + (PLAY / 2 + PAD) * s) - m
        self._capsule = QRectF(m, m, w, h)
        self._content = QSize(w + 2 * m, h + 2 * m)
        cx, top = self._anchor
        g = self.screen_rect()
        x = max(g.x() + round(8 * s) - m,
                min(round(cx - w / 2) - m, g.x() + g.width() - w - round(8 * s) - m))
        # The capsule sits 8 px over the dock's, as the previews do; the
        # margin round it is clear, and may overlap the dock's.
        return QRect(x, round(top - 8 * s - h) - m, w + 2 * m, h + 2 * m)

    # -- the volume ------------------------------------------------------- #
    def _speaker(self):
        if self._speakers is None:
            shell.com_thread()          # the GUI thread's apartment, if Qt hasn't joined one
            self._speakers = shell.Speakers()
        return self._speakers

    def _read_volume(self):
        try:
            return self._speaker().read()
        except OSError:
            return None

    def set_volume(self, level):
        level = max(0.0, min(1.0, level))
        try:
            speakers = self._speaker()
            speakers.set_level(level)
            if self._volume is not None and self._volume[1] and level > 0:
                speakers.set_muted(False)   # dragging up unmutes, as quick settings does
            self._volume = (level, False if level > 0 else bool(self._volume and self._volume[1]))
        except OSError:
            pass
        self.update()

    def toggle_mute(self):
        if self._volume is None:
            return
        level, muted = self._volume
        try:
            self._speaker().set_muted(not muted)
            self._volume = (level, not muted)
        except OSError:
            pass
        self.update()

    def capsule_on_screen(self) -> QRectF:
        """The menu itself, without the clear margin round it."""
        return self._capsule.translated(self.x() + self._offset.x(), self.y() + self._offset.y())

    def _volume_at(self, x):
        track = self._rects["volume"]
        return (x - track.left()) / max(1.0, track.width())

    # -- the controls ----------------------------------------------------- #
    def act(self, control):
        np, _ = self._track()
        if control == "mute":
            self.toggle_mute()
        elif not np.available:
            return                      # nothing to control
        elif control == "play":
            self._override = (not self._playing(np), time.monotonic() + 2.5)
            media.play_pause()
        elif control == "prev":
            media.prev_track()
        elif control == "next":
            media.next_track()
        self.update()

    def _on_tick(self):
        self._reads += 1
        if self._reads % 4 == 0 and not self._dragging:
            self._volume = self._read_volume()   # the volume keys or quick settings changed it
        self.update()

    def _on_watch(self):
        down = shell.buttons_down()
        if down and not self._was_down:
            x, y, _ = shell.cursor()
            if not self.capsule_on_screen().contains(QPointF(x, y)) and \
                    not self.dock.geometry().contains(x, y):
                self.dock.close_music()     # the dock handles its own clicks
        self._was_down = down

    # -- painting --------------------------------------------------------- #
    def _font(self, key):
        if self._fonts is None:
            from .dock import _symbols
            s = self.dock._s
            self._fonts = {"title": typography.font(10, QFont.DemiBold),
                           "sub": typography.font(8.5), "small": typography.font(7.5),
                           "glyph": _symbols(14 * s), "note": _symbols(20 * s)}
            for font in self._fonts.values():
                # Greyscale, as the dock draws: ClearType put red and blue
                # edges on the speaker symbol.
                font.setStyleStrategy(QFont.NoSubpixelAntialias)
        return self._fonts[key]

    def paint_content(self, p, pal, s):
        np, position = self._track()
        playing = self._playing(np)
        dock, state = self.dock, self.dock.board.state
        tint = dock._palette()[1]
        window = QRectF(-self._offset.x(), -self._offset.y(), self.width(), self.height())
        # Its glass from where it rests, not from each step of its rise, as the
        # dock's comes from where it rests up: bent once, not every frame.
        rest = self.rest()
        paint_material(p, self._capsule, s, pal, dock.board, tint, window, rest.x(), rest.y(),
                       owner=self, source=dock._through)
        if state.dock_neon:
            p.drawImage(0, 0, self._neon.frame(
                self._content, self._capsule, RADIUS * s, s, state.dock_neon_colors,
                state.dock_neon_motion, state.dock_neon_strength, time.monotonic()))
        self._paint_art(p, pal, s, np)
        x = self._art_rect.right() + 10 * s
        room = self._capsule.right() - PAD * s - x
        lines = [("title", pal.text, np.title if np.available else "Nothing playing"),
                 ("sub", pal.text_dim, (np.artist or "") if np.available
                  else "Play something in Spotify or Apple Music"),
                 ("small", pal.text_faint, " · ".join(v for v in (np.source, np.album) if v)
                  if np.available else "")]
        # The three lines, centred against the cover.
        heights = [QFontMetricsF(self._font(key)).height() for key, _, _ in lines]
        y = self._art_rect.center().y() - (sum(heights) + 2 * s * (len(lines) - 1)) / 2
        for (key, colour, text), height in zip(lines, heights):
            font = self._font(key)
            if text:
                p.setFont(font)
                p.setPen(QColor(colour))
                p.drawText(QRectF(x, y, room, height), Qt.AlignLeft | Qt.AlignVCenter,
                           QFontMetricsF(font).elidedText(text, Qt.ElideRight, room))
            y += height + 2 * s
        if not self._bar.isEmpty():
            self._paint_bar(p, pal, s, self._bar, np.progress if position is None
                            else position / np.duration, QColor(pal.accent))
            p.setFont(self._font("small"))
            p.setPen(QColor(pal.text_faint))
            p.drawText(self._times, Qt.AlignLeft | Qt.AlignVCenter, media.fmt(position))
            left = None if position is None else np.duration - position
            p.drawText(self._times, Qt.AlignRight | Qt.AlignVCenter, media.fmt(left, negative=True))
        enabled = np.available
        for name in ("prev", "play", "next"):
            r = self._rects[name]
            ink = QColor(pal.text if enabled else pal.text_faint)
            if name == "play":
                disc = QColor(pal.text) if enabled else QColor(pal.text_faint)
                if enabled and self._hover == "play":
                    disc.setAlphaF(0.85)
                p.setPen(Qt.NoPen)
                p.setBrush(disc)
                d = r.adjusted(2 * s, 2 * s, -2 * s, -2 * s) if self._pressed == "play" else r
                p.drawEllipse(d)
                bg = QColor(tint)
                bg.setAlpha(255)
                _play_glyph(p, r.center(), 12 * s, playing, bg)
                continue
            if enabled and self._hover == name:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 26) if pal.dark else QColor(0, 0, 0, 14))
                p.drawEllipse(r)
            size = (9.5 if self._pressed == name else 11) * s
            _skip_glyph(p, r.center(), size, name == "next", ink)
        level, muted = self._volume if self._volume is not None else (0.0, False)
        mute = self._rects["mute"]
        if self._hover == "mute":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 26) if pal.dark else QColor(0, 0, 0, 14))
            p.drawEllipse(mute)
        p.setFont(self._font("glyph"))
        p.setPen(QColor(pal.text if self._volume is not None else pal.text_faint))
        glyph = MUTE_GLYPH if muted or self._volume is None else \
            VOLUME_GLYPHS[0 if level <= 0 else 1 if level < 0.34 else 2 if level < 0.67 else 3]
        p.drawText(mute, Qt.AlignCenter, glyph)
        track = self._rects["volume"]
        fill = QColor(pal.text_faint) if muted else QColor(pal.accent)
        self._paint_bar(p, pal, s, track, level if self._volume is not None else 0.0, fill)
        if self._volume is not None:
            knob = QPointF(track.left() + track.width() * level, track.center().y())
            r = (6.5 if self._dragging or self._hover == "volume" else 5) * s
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawEllipse(knob + QPointF(0, 1 * s), r, r)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(knob, r, r)
        p.setFont(self._font("small"))
        p.setPen(QColor(pal.text_dim))
        p.drawText(self._rects["percent"], Qt.AlignRight | Qt.AlignVCenter,
                   "" if self._volume is None else "Muted" if muted else f"{round(level * 100)}%")

    def _paint_art(self, p, pal, s, np):
        """The cover, dissolving into the next as the dock's does - they share
        the one fade, so the two change together."""
        r = self._art_rect
        shape = squircle(r, 8 * s)
        px = round(r.width())
        cover, prev, t = self.dock.covers(np)
        pm, old = self._cover(cover, px), self._cover(prev, px)
        if pm is None or (old is None and t < 1.0):
            # The track's colour: no cover, or what one fades in from and out to.
            p.fillPath(shape, QColor(*np.tint()) if np.available else
                       QColor(255, 255, 255, 30) if pal.dark else QColor(0, 0, 0, 16))
        # As the dock's: the outgoing cover solid under the incoming one.
        for picture, alpha in ((old, 1.0 if pm is not None else 1.0 - t), (pm, t)):
            if picture is not None and alpha > 0.004:
                p.save()
                p.setClipPath(shape)
                p.setOpacity(p.opacity() * alpha)
                p.drawPixmap(r, picture, QRectF(picture.rect()))
                p.restore()
        if pm is None and old is None:
            p.setFont(self._font("note"))
            p.setPen(QColor(255, 255, 255, 235) if np.available else QColor(pal.text_dim))
            p.drawText(r, Qt.AlignCenter, NOTE_GLYPH)

    def _cover(self, art, px):
        if art is None or art.isNull():
            return None
        key = (art.cacheKey(), px)
        if key not in self._art:
            side = min(art.width(), art.height())
            square = art.copy((art.width() - side) // 2, (art.height() - side) // 2, side, side)
            if len(self._art) > 4:
                self._art.clear()       # only two are live at once, mid-fade
            self._art[key] = QPixmap.fromImage(
                square.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        return self._art[key]

    @staticmethod
    def _paint_bar(p, pal, s, rect, fraction, fill):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40) if pal.dark else QColor(0, 0, 0, 28))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if fraction is None:
            return
        done = QRectF(rect.x(), rect.y(), rect.width() * max(0.0, min(1.0, fraction)), rect.height())
        if done.width() > 0:
            p.setBrush(fill)
            p.drawRoundedRect(done, rect.height() / 2, rect.height() / 2)

    # -- input ------------------------------------------------------------ #
    def _at(self, pos):
        if self._leaving:
            return ""
        pos = self.local(pos)
        s = self.dock._s
        for name in ("prev", "play", "next", "mute"):
            r = self._rects.get(name)
            if r is not None and r.contains(pos):
                return name
        track = self._rects.get("volume")
        if track is not None and track.adjusted(-8 * s, -12 * s, 8 * s, 12 * s).contains(pos):
            return "volume"
        return ""

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.set_volume(self._volume_at(self.local(event.position()).x()))
            return
        hit = self._at(event.position())
        if hit != self._hover:
            self._hover = hit
            self.update()

    def leaveEvent(self, event):
        if not self._dragging:
            self._hover = ""
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        hit = self._at(event.position())
        if hit == "volume":
            self._dragging = True
            self.set_volume(self._volume_at(self.local(event.position()).x()))
        else:
            self._pressed = hit
            self.update()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.update()
            return
        hit, pressed = self._at(event.position()), self._pressed
        self._pressed = ""
        self.update()
        if hit and hit == pressed and event.button() == Qt.LeftButton:
            self.act(hit)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if steps and self._volume is not None:
            self.set_volume(self._volume[0] + steps * WHEEL_STEP)

    def closeEvent(self, event):
        if self._speakers is not None:
            self._speakers.close()
            self._speakers = None
        super().closeEvent(event)


def _play_glyph(p, centre, size, playing, colour):
    """Pause while playing, else play - what a press does next."""
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(colour)
    x, y = centre.x(), centre.y()
    if playing:
        w, gap = size * 0.3, size * 0.24
        for dx in (-gap / 2 - w, gap / 2):
            p.drawRoundedRect(QRectF(x + dx, y - size / 2, w, size), w / 3, w / 3)
    else:
        tri = QPainterPath(QPointF(x - size * 0.32, y - size / 2))
        tri.lineTo(QPointF(x + size * 0.52, y))
        tri.lineTo(QPointF(x - size * 0.32, y + size / 2))
        tri.closeSubpath()
        p.drawPath(tri)
    p.restore()


def _skip_glyph(p, centre, size, forward, colour):
    """Next: a triangle into a bar; previous, the same turned round."""
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(colour)
    x, y, h = centre.x(), centre.y(), size
    d = 1 if forward else -1
    tri = QPainterPath(QPointF(x - d * h * 0.45, y - h / 2))
    tri.lineTo(QPointF(x + d * h * 0.3, y))
    tri.lineTo(QPointF(x - d * h * 0.45, y + h / 2))
    tri.closeSubpath()
    p.drawPath(tri)
    bw = h * 0.16
    bx = x + h * 0.32 if forward else x - h * 0.32 - bw   # the bar on the far side
    p.drawRoundedRect(QRectF(bx, y - h / 2, bw, h), bw / 3, bw / 3)
    p.restore()
