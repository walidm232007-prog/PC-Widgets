"""A panel that rises out of the dock: what the window previews and the music
menu are built on.

The panel is opaque: the board's frosted wallpaper painted edge to edge, like
the dock's glass, with Windows 11's own rounded corners, border and shadow
round it. The window previews have to be - the compositor never draws a
thumbnail into a layered window, and Qt makes every see-through frameless
window layered - and the music menu matches them.

Being opaque, it can't fade as a whole. It rises instead: it unfolds upward
from the dock, its bottom edge still, while what is in it fades in. Shown for
something else, it glides there; dismissed, it folds back down and only then
hides. With the dock's neon outline on, a see-through window just behind it
carries the same neon round its edge, where an opaque panel can't draw a glow.

A panel with nothing the compositor draws into it can be see-through instead
(TRANSLUCENT): then it is the dock's own capsule - its squircle corners,
shadow, glass and neon, drawn by the dock's code - and it really fades,
sliding up a little as it does, rather than unfolding. The music menu is.

A subclass lays out its content (``layout`` returns where the panel goes),
paints it (``paint_content``, in the content's own coordinates, already
faded; a see-through one paints its own capsule), and may place anything the
compositor draws (``place_extras``).
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from ..core import dock_shell as shell
from ..core.frameclock import Ticker, Tween     # the main display's refresh, as the dock
from .dock import NEON_FPS, NeonRing

CORNER = 8                      # Windows 11's rounding of a popup, at 100%
HALO = 14                       # room round the panel for a neon glow, at 100%
SHOW_MS, MOVE_MS, HIDE_MS = 190, 170, 130
RISE = 10                       # how far a see-through panel slides up as it fades in


def _lerp(a: QRect, b: QRect, t: float) -> QRect:
    return QRect(round(a.x() + (b.x() - a.x()) * t), round(a.y() + (b.y() - a.y()) * t),
                 round(a.width() + (b.width() - a.width()) * t),
                 round(a.height() + (b.height() - a.height()) * t))


def _folded(r: QRect) -> QRect:
    """Where the panel rises from and folds back to: its lower half, the
    bottom edge where it always is, just above the dock."""
    return QRect(r.x(), r.y() + r.height() // 2, r.width(), r.height() - r.height() // 2)


class NeonHalo(QWidget):
    """The neon outline round a panel, in a see-through window just behind
    it: the tube runs along the panel's edge and its bloom shines outward."""

    def __init__(self, panel):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.panel = panel
        self.setWindowTitle(panel.windowTitle() + " outline")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if panel.offscreen:
            self.setAttribute(Qt.WA_DontShowOnScreen)
        self.ring = NeonRing()
        self.alpha = 1.0
        self.timer = Ticker(self.update, max_fps=NEON_FPS)

    def frame(self):
        dock = self.panel.dock
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


class DockPanel(QWidget):
    offscreen = False               # the selftest's: never map the panel or its outline
    TITLE = "PC Widgets Glass Dock panel"
    TRANSLUCENT = False             # the dock's own capsule, see-through, instead of opaque

    def __init__(self, dock):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.dock = dock
        self.setWindowTitle(self.TITLE)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if self.TRANSLUCENT:
            self.setAttribute(Qt.WA_TranslucentBackground)
        if self.offscreen:
            self.setAttribute(Qt.WA_DontShowOnScreen)
        self.setMouseTracking(True)
        self.halo = None
        self._neon_timer = Ticker(self.update, max_fps=NEON_FPS)  # a see-through panel's own neon
        self._rounded = False
        self._border = True           # Windows' hairline; off while neon draws the edge
        self._content = QSize()       # the size the content was laid out for
        self._target = QRect()        # where the panel is going
        self._alpha = 0.0             # its content: 0 faded out, 1 shown
        self._offset = QPointF()      # the content's origin in the window as it is now
        self._leaving = False
        self._from = self._to = None  # (geometry, alpha) the animation runs between
        self._anim = Tween(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._settled)

    # -- for subclasses --------------------------------------------------- #
    def layout(self) -> QRect:
        raise NotImplementedError

    def paint_content(self, p, pal, s):
        pass

    def place_extras(self):
        pass

    def release(self):
        pass

    def screen_rect(self) -> QRect:
        screen = QApplication.screenAt(self.dock.geometry().center()) or QApplication.primaryScreen()
        return screen.geometry()

    def local(self, pos):
        """A point in the window as content coordinates."""
        return pos - self._offset

    # -- showing ---------------------------------------------------------- #
    @property
    def up(self) -> bool:
        """Showing, or on its way up - not folding away."""
        return self.isVisible() and not self._leaving

    def present(self, fresh=False):
        """Lay out and show: rise out of the dock, or glide to the new place -
        new content (fresh) fading in on the way - or just repaint."""
        if not self._rounded and not self.TRANSLUCENT:
            shell.round_corners(int(self.winId()))
            self._rounded = True
        final = self.layout()
        if not self.isVisible():
            start = self._below(final)
            self._leaving = False
            self._frame(start, 0.0)
            self.show()
            self._run(start, 0.0, final, 1.0, SHOW_MS, QEasingCurve.OutCubic)
        elif self._leaving or final != self._target or fresh:
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
            self._run(self.geometry(), self._alpha, self._below(self.geometry()), 0.0, HIDE_MS,
                      QEasingCurve.InCubic)

    def _below(self, r: QRect) -> QRect:
        """Where it rises from and sinks back to: unfolded from its lower half
        when opaque; a little lower, fading, when see-through."""
        if self.TRANSLUCENT:
            return r.translated(0, round(RISE * self.dock._s))
        return _folded(r)

    def _reset(self):
        self._anim.stop()
        self._neon_timer.stop()
        self.hide()
        if self.halo is not None:
            self.halo.timer.stop()
            self.halo.hide()
        self.release()
        self._leaving, self._alpha = False, 0.0

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
        """The panel at one moment: its window, its content in it - centred
        across, held to its bottom edge - and anything drawn over it."""
        self._alpha = alpha
        self._offset = QPointF(round((geometry.width() - self._content.width()) / 2),
                               geometry.height() - self._content.height())
        if self.geometry() != geometry:
            self.setGeometry(geometry)
        self.place_extras()
        self._sync_halo(geometry)
        self.update()

    def _sync_halo(self, geometry):
        state = self.dock.board.state
        neon = state.dock_neon
        if self.TRANSLUCENT:
            # Its neon is its own, drawn round its capsule as the dock's is.
            moving = neon and state.dock_neon_motion != "static"
            if moving and not self._neon_timer.isActive():
                self._neon_timer.start()
            elif not moving:
                self._neon_timer.stop()
            return
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

    # -- painting --------------------------------------------------------- #
    def paintEvent(self, event):
        dock, s = self.dock, self.dock._s
        pal, tint = dock._palette()
        state = dock.board.state
        if self.TRANSLUCENT:
            # Drawn on a straight-alpha image, then faded onto the window, as
            # the dock draws itself: on a premultiplied one Qt renders text
            # with ClearType, and every letter came out with red and blue edges.
            img = QImage(self.size(), QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            dpm = round(self.logicalDpiX() / 0.0254)
            img.setDotsPerMeterX(dpm)
            img.setDotsPerMeterY(dpm)
            q = QPainter(img)
            q.setRenderHint(QPainter.Antialiasing)
            q.setRenderHint(QPainter.SmoothPixmapTransform)
            q.translate(self._offset)
            self.paint_content(q, pal, s)
            q.end()
            p = QPainter(self)
            p.setOpacity(self._alpha)
            p.drawImage(0, 0, img)
            p.end()
            return
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
        self.paint_content(p, pal, s)
        p.end()

    def closeEvent(self, event):
        self._anim.stop()
        self.release()
        if self.halo is not None:
            self.halo.close()
        super().closeEvent(event)
