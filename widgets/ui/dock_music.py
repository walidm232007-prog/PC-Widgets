"""The music menu: click the dock's music and it rises above it.

The cover, the title, artist and player; how far through the track it is;
previous, play or pause, and next; and the volume. It rises, glides and folds
like the window previews, with the dock's neon round it when that is on (see
dock_panel).

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
from .dock_panel import DockPanel

WIDTH, PAD, ART = 304, 14, 76   # at 100% scaling
PLAY, SKIP = 48, 40             # the round buttons' diameters
NOTE_GLYPH = ""           # Segoe Fluent Icons' MusicNote
MUTE_GLYPH = ""
VOLUME_GLYPHS = ("", "", "", "")   # none, low, medium, high
WHEEL_STEP = 0.02


class MusicPanel(DockPanel):
    TITLE = "PC Widgets Glass Dock music"

    def __init__(self, dock):
        super().__init__(dock)
        self._anchor = (0, 0)
        self._hover = self._pressed = ""
        self._rects = {}              # control -> rect, in content coordinates
        self._art_rect = self._bar = self._times = QRectF()
        self._art = None              # (cacheKey, px, QPixmap)
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
        s = self.dock._s
        np, _ = self._track()
        w = round(WIDTH * s)
        self._art_rect = QRectF(round(PAD * s), round(PAD * s), round(ART * s), round(ART * s))
        y = (PAD + ART + 16) * s
        if np.available and np.duration:
            self._bar = QRectF(round(PAD * s), round(y), w - round(2 * PAD * s), round(4 * s))
            self._times = QRectF(self._bar.x(), round(y + 8 * s), self._bar.width(), round(14 * s))
            y += 30 * s
        else:
            self._bar = self._times = QRectF()
        cy = y + PLAY / 2 * s
        self._rects = {
            "prev": QRectF(w / 2 - 64 * s - SKIP / 2 * s, cy - SKIP / 2 * s, SKIP * s, SKIP * s),
            "play": QRectF(w / 2 - PLAY / 2 * s, cy - PLAY / 2 * s, PLAY * s, PLAY * s),
            "next": QRectF(w / 2 + 64 * s - SKIP / 2 * s, cy - SKIP / 2 * s, SKIP * s, SKIP * s),
        }
        vy = cy + (PLAY / 2 + 26) * s
        self._rects["mute"] = QRectF(PAD * s, vy - 16 * s, 32 * s, 32 * s)
        self._rects["volume"] = QRectF((PAD + 40) * s, vy - 2 * s, w - (2 * PAD + 84) * s, 4 * s)
        self._rects["percent"] = QRectF(w - (PAD + 40) * s, vy - 8 * s, 40 * s, 16 * s)
        h = round(vy + (16 + PAD) * s)
        self._content = QSize(w, h)
        cx, top = self._anchor
        g = self.screen_rect()
        x = max(g.x() + round(8 * s), min(round(cx - w / 2), g.x() + g.width() - w - round(8 * s)))
        return QRect(x, round(top - 8 * s - h), w, h)

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
            if not self.geometry().contains(x, y) and not self.dock.geometry().contains(x, y):
                self.dock.close_music()     # the dock handles its own clicks
        self._was_down = down

    # -- painting --------------------------------------------------------- #
    def _font(self, key):
        if self._fonts is None:
            from .dock import _symbols
            s = self.dock._s
            self._fonts = {"title": typography.font(11, QFont.DemiBold),
                           "sub": typography.font(9), "small": typography.font(8),
                           "glyph": _symbols(16 * s), "note": _symbols(28 * s)}
            for font in self._fonts.values():
                # Greyscale, as the dock draws: ClearType put red and blue
                # edges on the speaker symbol.
                font.setStyleStrategy(QFont.NoSubpixelAntialias)
        return self._fonts[key]

    def paint_content(self, p, pal, s):
        np, position = self._track()
        playing = self._playing(np)
        self._paint_art(p, pal, s, np)
        x = self._art_rect.right() + 12 * s
        room = self._content.width() - x - PAD * s
        lines = [("title", pal.text, np.title if np.available else "Nothing playing"),
                 ("sub", pal.text_dim, (np.artist or "") if np.available
                  else "Play something in Spotify or Apple Music"),
                 ("small", pal.text_faint, " · ".join(v for v in (np.source, np.album) if v)
                  if np.available else "")]
        y = self._art_rect.top() + 8 * s
        for key, colour, text in lines:
            font = self._font(key)
            fm = QFontMetricsF(font)
            if text:
                p.setFont(font)
                p.setPen(QColor(colour))
                p.drawText(QRectF(x, y, room, fm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                           fm.elidedText(text, Qt.ElideRight, room))
            y += fm.height() + 2 * s
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
                bg = QColor(self.dock._palette()[1])
                bg.setAlpha(255)
                _play_glyph(p, r.center(), 16 * s, playing, bg)
                continue
            if enabled and self._hover == name:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 26) if pal.dark else QColor(0, 0, 0, 14))
                p.drawEllipse(r)
            size = (12 if self._pressed == name else 14) * s
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
            r = (8 if self._dragging or self._hover == "volume" else 6) * s
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
        r = self._art_rect
        shape = squircle(r, 10 * s)
        px = round(r.width())
        art = np.art if np.available else None
        if art is not None and not art.isNull():
            if self._art is None or self._art[0] != art.cacheKey() or self._art[1] != px:
                side = min(art.width(), art.height())
                square = art.copy((art.width() - side) // 2, (art.height() - side) // 2, side, side)
                self._art = (art.cacheKey(), px, QPixmap.fromImage(
                    square.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)))
            p.save()
            p.setClipPath(shape)
            p.drawPixmap(r, self._art[2], QRectF(self._art[2].rect()))
            p.restore()
            return
        p.fillPath(shape, QColor(*np.tint()) if np.available else
                   QColor(255, 255, 255, 30) if pal.dark else QColor(0, 0, 0, 16))
        p.setFont(self._font("note"))
        p.setPen(QColor(255, 255, 255, 235) if np.available else QColor(pal.text_dim))
        p.drawText(r, Qt.AlignCenter, NOTE_GLYPH)

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
