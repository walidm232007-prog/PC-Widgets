"""Live window previews over a dock button, as the taskbar shows them.

Hover an app with windows open and, after a moment, a panel rises above its
button with a live picture of each window: its icon and title on top, a close
button on the one under the pointer. Click a picture to bring that window
forward; close it from its button or with a middle-click.

The pictures are the compositor's own thumbnails (DwmRegisterThumbnail), the
same live images the taskbar uses, video and all - nothing is captured or
copied, and they cost this process nothing to keep up to date. The compositor
never draws one into a layered window, and Qt makes every see-through
frameless window layered, so this panel is opaque: the board's frosted
wallpaper painted edge to edge, like the dock's glass, with Windows 11's own
rounded corners, border and shadow round it.

Being opaque, it can't fade as a whole. It rises instead: it unfolds upward
from the dock, its bottom edge still, while what is in it fades in - the
pictures through the compositor's own opacity. Moving to another app glides
it there; leaving folds it back down. With the dock's neon outline on, a
see-through window just behind it carries the same neon round its edge,
where an opaque panel can't draw a glow.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QTimer, \
    QVariantAnimation
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from ..core import dock_shell as shell
from ..core.card import squircle
from .dock import NeonRing

THUMB_W, THUMB_H = 200, 120     # the largest a picture gets, at 100% scaling
CARD_MIN = 150                  # a card is never narrower, so a title has room
PAD, GAP, HEAD = 8, 6, 28       # the panel's margin, between cards, a card's title row
CLOSE = 24
CLOSE_GLYPH = ""          # Segoe Fluent Icons' ChromeClose
CORNER = 8                      # Windows 11's rounding of a popup, at 100%
HALO = 14                       # room round the panel for a neon glow, at 100%
SHOW_MS, MOVE_MS, HIDE_MS = 190, 170, 130


class Card:
    __slots__ = ("hwnd", "title", "rect", "thumb", "close", "picture")

    def __init__(self, hwnd, title, picture):
        self.hwnd, self.title, self.picture = hwnd, title, picture
        self.rect = self.thumb = self.close = QRectF()


def _lerp(a: QRect, b: QRect, t: float) -> QRect:
    return QRect(round(a.x() + (b.x() - a.x()) * t), round(a.y() + (b.y() - a.y()) * t),
                 round(a.width() + (b.width() - a.width()) * t),
                 round(a.height() + (b.height() - a.height()) * t))


def _folded(r: QRect) -> QRect:
    """Where the panel rises from and folds back to: its lower half, the
    bottom edge where it always is, just above the dock."""
    return QRect(r.x(), r.y() + r.height() // 2, r.width(), r.height() - r.height() // 2)


class NeonHalo(QWidget):
    """The neon outline round the panel, in a see-through window just behind
    it: the tube runs along the panel's edge and its bloom shines outward."""

    def __init__(self, previews):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.previews = previews
        self.setWindowTitle("PC Widgets Glass Dock preview outline")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if previews.offscreen:
            self.setAttribute(Qt.WA_DontShowOnScreen)
        self.ring = NeonRing()
        self.alpha = 1.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.update)

    def frame(self):
        dock = self.previews.dock
        state, s = dock.board.state, dock._s
        m = round(HALO * s)
        # Just outside the panel, so the tube's core shows beside its edge.
        edge = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m).adjusted(-1, -1, 1, 1)
        return self.ring.frame(self.size(), edge, CORNER * s + 1, s, state.dock_neon_colors,
                               state.dock_neon_motion, state.dock_neon_strength, time.monotonic(),
                               rounded=True)

    def paintEvent(self, event):
        img = self.frame()
        p = QPainter(self)
        p.setOpacity(self.alpha)
        p.drawImage(0, 0, img)
        p.end()


class WindowPreviews(QWidget):
    offscreen = False               # the selftest's: never map the panel or its outline

    def __init__(self, dock):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.dock = dock
        self.setWindowTitle("PC Widgets Glass Dock previews")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if self.offscreen:
            self.setAttribute(Qt.WA_DontShowOnScreen)
        self.setMouseTracking(True)
        self.ident = ""
        self.entry = None
        self.cards: list[Card] = []
        self.halo = None
        self._hover = None            # (hwnd, "card" | "close")
        self._pressed = None
        self._anchor = (0, 0)
        self._rounded = False
        self._border = True           # Windows' hairline; off while neon draws the edge
        self._content = QSize()       # the size the cards were laid out for
        self._target = QRect()        # where the panel is going
        self._alpha = 0.0             # what is in it: 0 faded out, 1 shown
        self._offset = QPointF()      # the cards' origin in the window as it is now
        self._leaving = False
        self._from = self._to = None  # (geometry, alpha) the animation runs between
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._settled)

    @property
    def up(self) -> bool:
        """Showing, or on its way up - not folding away."""
        return self.isVisible() and not self._leaving

    # -- showing ---------------------------------------------------------- #
    def show_for(self, ident, entry, cx, top):
        """Above the button whose centre is at x = cx and the dock's top at y =
        top, both on the screen. The same app again only refreshes titles and
        place; a different set of windows registers its pictures afresh."""
        hwnds = [t.hwnd for t in entry.windows]
        hwnd = int(self.winId())
        if not self._rounded:
            shell.round_corners(hwnd)
            self._rounded = True
        fresh = ident != self.ident or hwnds != [c.hwnd for c in self.cards]
        if fresh:
            self.release()
            for t in entry.windows:
                try:
                    picture = shell.Thumbnail(hwnd, t.hwnd)
                except OSError:
                    picture = None
                self.cards.append(Card(t.hwnd, t.title, picture))
        else:
            titles = {t.hwnd: t.title for t in entry.windows}
            for c in self.cards:
                c.title = titles.get(c.hwnd, c.title)
        self.ident, self.entry, self._anchor = ident, entry, (cx, top)
        final = self._layout()
        if not self.isVisible():
            start = _folded(final)          # rise out of the dock
            self._leaving = False
            self._frame(start, 0.0)
            self.show()
            self._run(start, 0.0, final, 1.0, SHOW_MS, QEasingCurve.OutCubic)
        elif self._leaving or final != self._target or fresh:
            # Back up from wherever it is; or along to another app, whose
            # cards fade in as the panel glides over to it.
            self._leaving = False
            alpha = min(self._alpha, 0.35) if fresh else self._alpha
            self._run(self.geometry(), alpha, final, 1.0, MOVE_MS, QEasingCurve.OutCubic)
        else:
            self._frame(self.geometry(), self._alpha)
        self.raise_()

    def dismiss(self):
        """Fold back down into the dock, fading, then go."""
        if not self.isVisible():
            self._reset()
        elif not self._leaving:
            self._leaving = True
            self._run(self.geometry(), self._alpha, _folded(self.geometry()), 0.0, HIDE_MS,
                      QEasingCurve.InCubic)

    def release(self):
        for c in self.cards:
            if c.picture is not None:
                c.picture.close()
        self.cards = []
        self._hover = self._pressed = None

    def _reset(self):
        self._anim.stop()
        self.hide()
        if self.halo is not None:
            self.halo.timer.stop()
            self.halo.hide()
        self.release()
        self.ident, self.entry, self._leaving, self._alpha = "", None, False, 0.0

    def _run(self, g0, a0, g1, a1, ms, curve):
        self._anim.stop()
        self._from, self._to, self._target = (g0, a0), (g1, a1), g1
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(curve)
        self._anim.start()

    def _step(self, t):
        if self._from is None:
            return
        (g0, a0), (g1, a1) = self._from, self._to
        self._frame(_lerp(g0, g1, t), a0 + (a1 - a0) * t)

    def _settled(self):
        if self._leaving:
            self._reset()

    def _frame(self, geometry, alpha):
        """The panel at one moment: its window, its cards in it - centred
        across, held to its bottom edge - and its pictures and outline."""
        self._alpha = alpha
        self._offset = QPointF(round((geometry.width() - self._content.width()) / 2),
                               geometry.height() - self._content.height())
        if self.geometry() != geometry:
            self.setGeometry(geometry)
        for c in self.cards:
            if c.picture is not None:
                r = c.thumb.translated(self._offset)
                c.picture.place(int(r.left()), int(r.top()), int(r.right()), int(r.bottom()),
                                round(255 * alpha))
        self._sync_halo(geometry)
        self.update()

    def _sync_halo(self, geometry):
        state = self.dock.board.state
        neon = state.dock_neon
        if neon == self._border:
            self._border = not neon
            shell.window_border(int(self.winId()), self._border)
        if not neon:
            if self.halo is not None and self.halo.isVisible():
                self.halo.timer.stop()
                self.halo.hide()
            return
        if self.halo is None:
            self.halo = NeonHalo(self)
        m = round(HALO * self.dock._s)
        self.halo.setGeometry(geometry.adjusted(-m, -m, m, m))
        self.halo.alpha = self._alpha
        if not self.halo.isVisible():
            self.halo.show()
            self.raise_()                   # the panel stays in front of its outline
        moving = state.dock_neon_motion != "static"
        if moving and not self.halo.timer.isActive():
            self.halo.timer.start()
        elif not moving:
            self.halo.timer.stop()
        self.halo.update()

    def _layout(self) -> QRect:
        """Lays the cards out and returns where the panel goes when settled."""
        s = self.dock._s
        cx, top = self._anchor
        screen = QApplication.screenAt(self.dock.geometry().center()) or QApplication.primaryScreen()
        g = screen.geometry()
        sizes = [(c.picture.size() if c.picture else (0, 0)) for c in self.cards]
        sizes = [(w, h) if w > 0 and h > 0 else (16, 10) for w, h in sizes]
        # Shrink every picture alike until the row fits on the screen.
        fit = 1.0
        while True:
            tw, th = THUMB_W * s * fit, THUMB_H * s * fit
            fitted = [(w * min(tw / w, th / h), h * min(tw / w, th / h)) for w, h in sizes]
            widths = [max(round(w), round(CARD_MIN * s * fit)) for w, _ in fitted]
            total = round(2 * PAD * s + sum(widths) + GAP * s * (len(widths) - 1))
            if total <= g.width() - round(16 * s) or fit < 0.35:
                break
            fit *= 0.85
        height = round((2 * PAD + HEAD) * s + th)
        x = round(cx - total / 2)
        x = max(g.x() + round(8 * s), min(x, g.x() + g.width() - total - round(8 * s)))
        y = round(top - 8 * s - height)
        left = PAD * s
        for c, w, (pw, ph) in zip(self.cards, widths, fitted):
            c.rect = QRectF(round(left), round(PAD * s / 2), w, height - round(PAD * s))
            box = QRectF(round(left), round((PAD + HEAD) * s), w, round(th))
            c.thumb = QRectF(round(box.center().x() - pw / 2), round(box.center().y() - ph / 2),
                             round(pw), round(ph))
            # The title row runs from y = PAD to PAD + HEAD; the button sits at its right end.
            c.close = QRectF(round(left + w - (CLOSE + 2) * s), round((PAD + (HEAD - CLOSE) / 2) * s),
                             round(CLOSE * s), round(CLOSE * s))
            left += w + GAP * s
        self._content = QSize(total, height)
        return QRect(x, y, total, height)

    # -- painting --------------------------------------------------------- #
    def paintEvent(self, event):
        dock, s = self.dock, self.dock._s
        pal, tint = dock._palette()
        state = dock.board.state
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        whole = QRectF(self.rect())
        drew = state.dock_style == "glass" and \
            dock.board.glass.paint_into(p, whole, self.x(), self.y())
        tint.setAlpha(max(70, min(225, state.glass_tint)) if drew else 255)
        p.fillRect(whole, tint)
        p.translate(self._offset)
        p.setOpacity(self._alpha)
        fonts = dock._font_set()
        fm = QFontMetricsF(fonts["sub"])
        icon = dock._picture(self.entry, round(32 * s)) if self.entry else None
        fg = shell.foreground()
        for c in self.cards:
            hovered = self._hover is not None and self._hover[0] == c.hwnd
            if hovered or c.hwnd == fg:
                fill = (QColor(255, 255, 255, 26 if hovered else 12) if pal.dark else
                        QColor(0, 0, 0, 14 if hovered else 7))
                p.fillPath(squircle(c.rect, 8 * s), fill)
            head = QRectF(c.rect.x() + 6 * s, PAD * s, c.rect.width() - 12 * s, HEAD * s)
            ix = head.x()
            if icon is not None:
                p.drawPixmap(QRectF(ix, head.center().y() - 8 * s, 16 * s, 16 * s), icon,
                             QRectF(icon.rect()))
                ix += 22 * s
            room = head.right() - ix - (CLOSE * s if hovered else 0)
            p.setFont(fonts["sub"])
            p.setPen(QColor(pal.text))
            p.drawText(QRectF(ix, head.y(), room, head.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(c.title, Qt.ElideRight, room))
            if hovered:
                on_close = self._hover[1] == "close"
                if on_close:
                    p.fillPath(squircle(c.close, 6 * s), QColor("#c42b1c"))
                p.setFont(fonts["small"])
                p.setPen(QColor("#ffffff") if on_close else QColor(pal.text_dim))
                p.drawText(c.close, Qt.AlignCenter, CLOSE_GLYPH)
            # Under the compositor's picture: shows only where there is none -
            # a window that refused one - or round a picture still arriving.
            p.fillPath(squircle(c.thumb, 4 * s),
                       QColor(255, 255, 255, 14) if pal.dark else QColor(0, 0, 0, 10))
            if c.picture is None and icon is not None:
                px = 32 * s
                p.drawPixmap(QRectF(c.thumb.center().x() - px / 2, c.thumb.center().y() - px / 2,
                                    px, px), icon, QRectF(icon.rect()))
        p.end()

    # -- input ------------------------------------------------------------ #
    def _at(self, pos):
        if self._leaving:
            return None
        pos = pos - self._offset
        for c in self.cards:
            if c.rect.contains(pos):
                return c.hwnd, "close" if c.close.contains(pos) else "card"
        return None

    def mouseMoveEvent(self, event):
        hit = self._at(event.position())
        if hit != self._hover:
            self._hover = hit
            self.update()

    def enterEvent(self, event):
        self.dock._keep_previews()

    def leaveEvent(self, event):
        self._hover = self._pressed = None
        self.update()
        self.dock._previews_left()

    def mousePressEvent(self, event):
        self._pressed = self._at(event.position())

    def mouseReleaseEvent(self, event):
        hit, pressed = self._at(event.position()), self._pressed
        self._pressed = None
        if hit is None or hit != pressed:
            return
        hwnd, part = hit
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and part == "close"):
            shell.close(hwnd)                   # the dock's next read drops its card
        elif event.button() == Qt.LeftButton:
            self.dock.hide_previews()
            shell.activate(hwnd)
            self.dock._poke()

    def closeEvent(self, event):
        self._anim.stop()
        self.release()
        if self.halo is not None:
            self.halo.close()
        super().closeEvent(event)
