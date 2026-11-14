"""
welcome.py
==========
The first thing a fresh install shows: every screen dims behind its own
wallpaper, "Welcome" comes into focus on the main one, then Start Setup and
Skip Setup. FIRST-RUN.md has the whole design and why it is a full-screen
moment rather than a dialog.

One window per screen, each painting that screen's wallpaper - the blurred copy
the cards' frosted glass already keeps - under a dark veil, faded in with the
window's own opacity so a 4K screen isn't repainted every frame. The main
screen's window carries the words and the buttons, and is the one in Alt+Tab
and the taskbar, as "PC Widgets Setup"; the others are plain veils.

It is built to never get in the way:

- it waits while a full-screen game has the foreground, and opens when it
  leaves;
- it is topmost only while it has the focus, so Alt+Tab takes you to another
  window and back again, and the taskbar is there while you're away;
- Esc is Skip Setup and Enter is Start Setup, and a click or Enter during the
  intro jumps to its end;
- Windows' animation effects switched off make every fade a cut, and
  transparency effects switched off make the veil a plain dark screen;
- the veil darkens a bright wallpaper further, so white text over it stays at
  7:1 or better whatever the picture.

Start Setup leads into the steps - your town, your widgets, the dock - on the
same veil (setup_steps.py). Skip puts up the five starter widgets. "Set up
again" in Settings opens the veil straight onto the steps.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject, QPointF,
                            QRect, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import QAbstractButton, QGraphicsBlurEffect, QWidget

from ..core import store, winapi
from ..core.palette import contrast
from . import theme

DIM = 0.65                      # black over the blurred wallpaper: a third of its light left
DIM_MAX = 0.88
PLAIN = QColor("#1c1c1e")       # the veil when Windows' transparency effects are off
WHITE_TEXT_RATIO = 7.0

# The intro, in seconds from the moment the veil starts to appear.
VEIL_IN = (0.0, 0.30)
TITLE_IN = (0.45, 1.25)
BUTTONS_IN = (1.45, 1.80)
LIFT_MS = 450
LEAVE_MS = 300                  # the welcome's words going as the first step comes
FILL_MS = 180                   # a button easing between outline and fill
GAME_POLL_MS = 2000


def welcome_due(state: store.BoardState, open_window: bool) -> bool:
    """Decide, at startup, whether this launch shows the welcome, and set the
    board up for it.

    A fresh install opened the ordinary way gets the welcome over a desktop with
    no widgets yet: the widgets are what it ends by revealing. A fresh install
    started by the Startup shortcut at sign-in never pops up a full-screen
    welcome; it gets the starter widgets, and the welcome waits for the first
    time the window is opened.
    """
    if state.setup != "pending":
        return False
    if open_window:
        state.placements = []
        return True
    if not state.placements:
        # A welcome left unfinished last time saved an empty board.
        state.placements = store.first_run_board(winapi.work_area()).placements
    return False


def dim_for(image: Optional[QImage], area: QRect) -> float:
    """How dark the veil has to be for white text to keep 7:1 over the
    brightest part of ``area`` - DIM for most wallpapers, more for a bright one.
    Sampled from a small copy: the blurred picture has no detail to miss."""
    if image is None or image.isNull():
        return 1.0
    piece = image.copy(area).scaled(96, 54, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    brightest = QColor(0, 0, 0)
    best = -1.0
    for y in range(piece.height()):
        for x in range(piece.width()):
            c = piece.pixelColor(x, y)
            v = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
            if v > best:
                best, brightest = v, c
    a = DIM
    while a < DIM_MAX:
        k = 1.0 - a
        under = QColor(round(brightest.red() * k), round(brightest.green() * k),
                       round(brightest.blue() * k))
        if contrast("#ffffff", under.name()) >= WHITE_TEXT_RATIO:
            break
        a += 0.02
    return min(a, DIM_MAX)


def _ease(t: float, span) -> float:
    """Progress through one part of the intro, eased out."""
    a, b = span
    if t <= a:
        return 0.0
    if t >= b:
        return 1.0
    x = (t - a) / (b - a)
    return 1.0 - (1.0 - x) ** 3


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t),
                  round(a.alpha() + (b.alpha() - a.alpha()) * t))


class _Fade:
    """A 0..1 value eased towards a target, for a control's hover state.

    Stepped on the frame clock (core/frameclock) rather than Qt's 60 Hz
    animation timer, so on a fast monitor the fade is drawn at its refresh
    rate - the same reason the dock moved onto it. A fade turned round half way
    takes half as long, so a quick pass over a button doesn't lag behind the
    pointer. With Windows' animation effects off it jumps.
    """

    def __init__(self, widget: QWidget, ms: int, motion: bool) -> None:
        self.widget, self.ms, self.motion = widget, ms, motion
        self.t = 0.0
        self.target = 0.0
        self._tween = None

    def to(self, target: float) -> None:
        target = float(target)
        if target == self.target and (self._tween is None or self.t == target
                                      or self._tween.state() == QAbstractAnimation.Running):
            return
        self.target = target
        if not self.motion:
            self.t = target
            self.widget.update()
            return
        if self._tween is None:
            from ..core.frameclock import Tween
            self._tween = Tween(self.widget)
            self._tween.setEasingCurve(QEasingCurve.OutCubic)
            self._tween.valueChanged.connect(self._step)
        self._tween.stop()
        self._tween.setStartValue(self.t)
        self._tween.setEndValue(target)
        self._tween.setDuration(max(1.0, self.ms * abs(target - self.t)))
        self._tween.start()

    def _step(self, v: float) -> None:
        self.t = float(v)
        self.widget.update()


# --------------------------------------------------------------------------- #
#  The words and the buttons
# --------------------------------------------------------------------------- #
class _Title(QWidget):
    """"Welcome", coming into focus as it fades in."""

    def __init__(self, scale: float, parent=None) -> None:
        super().__init__(parent)
        self.scale = scale
        self.font_ = theme.font(10, QFont.DemiBold)
        self.font_.setPixelSize(round(132 * scale))
        self.progress = 1.0
        self.blur = QGraphicsBlurEffect(self)
        self.blur.setBlurHints(QGraphicsBlurEffect.QualityHint)
        self.blur.setEnabled(False)
        self.setGraphicsEffect(self.blur)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def size_for(self) -> tuple:
        fm = QFontMetricsF(self.font_)
        return (round(fm.horizontalAdvance("Welcome") + 80 * self.scale),
                round(fm.height() + 40 * self.scale))

    def set_progress(self, t: float) -> None:
        self.progress = t
        self.blur.setEnabled(t < 0.999)
        self.blur.setBlurRadius((1.0 - t) * 22 * self.scale)
        self.update()

    def paintEvent(self, _ev) -> None:
        if self.progress <= 0.001:
            return
        p = theme.hints(QPainter(self))
        p.setFont(self.font_)
        ink = QColor(255, 255, 255)
        ink.setAlphaF(self.progress)
        p.setPen(ink)
        p.drawText(QRectF(self.rect()), Qt.AlignCenter, "Welcome")
        p.end()


class _StartButton(QAbstractButton):
    """An outline that fills white under the pointer, and with keyboard focus
    so someone using Tab can see where they are. The fill fades in and out
    rather than switching (FILL_MS), and the label goes from white to dark
    with it."""

    INK_OUT = QColor("#ffffff")
    INK_IN = QColor("#1d1d1f")

    def __init__(self, scale: float, motion: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.scale = scale
        self.setText("Start Setup")
        self.setAccessibleName("Start Setup")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.font_ = theme.font(12.75, QFont.DemiBold)
        self.hover = False
        self.alpha = 1.0
        self.fill = _Fade(self, FILL_MS, motion)
        fm = QFontMetricsF(self.font_)
        self.setFixedSize(round(fm.horizontalAdvance(self.text()) + 68 * scale), round(52 * scale))

    def filled(self) -> bool:
        return self.hover or self.hasFocus() or self.isDown()

    def _settle(self) -> None:
        self.fill.to(1.0 if self.filled() else 0.0)

    def set_hover(self, on: bool) -> None:
        self.hover = on
        self._settle()

    def enterEvent(self, _ev) -> None:
        self.set_hover(True)

    def leaveEvent(self, _ev) -> None:
        self.set_hover(False)

    def focusInEvent(self, ev) -> None:
        super().focusInEvent(ev)
        self._settle()

    def focusOutEvent(self, ev) -> None:
        super().focusOutEvent(ev)
        self._settle()

    def paintEvent(self, _ev) -> None:
        if self.alpha <= 0.001:
            return
        f = self.fill.t
        p = theme.hints(QPainter(self))
        p.setOpacity(self.alpha)
        line = 2.0 * self.scale
        r = QRectF(self.rect()).adjusted(line / 2, line / 2, -line / 2, -line / 2)
        path = theme.rounded(r, r.height() / 2)
        if f > 0.001:
            p.fillPath(path, QColor(255, 255, 255, round(255 * f)))
        p.setPen(QPen(_mix(QColor(255, 255, 255, 230), QColor(255, 255, 255, 255), f), line))
        p.drawPath(path)
        p.setPen(_mix(self.INK_OUT, self.INK_IN, f))
        p.setFont(self.font_)
        p.drawText(r, Qt.AlignCenter, self.text())
        p.end()


class _SkipButton(QAbstractButton):
    """Quiet text that brightens under the pointer, with the same fade as
    Start Setup, and is underlined while it has keyboard focus."""

    def __init__(self, scale: float, motion: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.scale = scale
        self.setText("Skip Setup")
        self.setAccessibleName("Skip Setup")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.font_ = theme.font(11.25, QFont.Medium)
        self.hover = False
        self.alpha = 1.0
        self.lit = _Fade(self, FILL_MS, motion)
        fm = QFontMetricsF(self.font_)
        self.setFixedSize(round(fm.horizontalAdvance(self.text()) + 32 * scale), round(36 * scale))

    def _settle(self) -> None:
        self.lit.to(1.0 if (self.hover or self.hasFocus()) else 0.0)

    def set_hover(self, on: bool) -> None:
        self.hover = on
        self._settle()

    def enterEvent(self, _ev) -> None:
        self.set_hover(True)

    def leaveEvent(self, _ev) -> None:
        self.set_hover(False)

    def focusInEvent(self, ev) -> None:
        super().focusInEvent(ev)
        self._settle()

    def focusOutEvent(self, ev) -> None:
        super().focusOutEvent(ev)
        self._settle()

    def paintEvent(self, _ev) -> None:
        if self.alpha <= 0.001:
            return
        p = theme.hints(QPainter(self))
        p.setOpacity(self.alpha)
        p.setFont(self.font_)
        ink = QColor(255, 255, 255, round(204 + 51 * self.lit.t))
        p.setPen(ink)
        p.drawText(QRectF(self.rect()), Qt.AlignCenter, self.text())
        if self.hasFocus():
            fm = QFontMetricsF(self.font_)
            w = fm.horizontalAdvance(self.text())
            y = self.height() / 2 + fm.ascent() / 2 + 3 * self.scale
            p.setPen(QPen(ink, 1.5 * self.scale))
            p.drawLine(QPointF((self.width() - w) / 2, y), QPointF((self.width() + w) / 2, y))
        p.end()


# --------------------------------------------------------------------------- #
#  One screen
# --------------------------------------------------------------------------- #
class Stage(QWidget):
    """One screen's veil. The main screen's also holds the welcome itself."""

    def __init__(self, welcome: "Welcome", screen, main: bool) -> None:
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        flags |= Qt.Window if main else Qt.Tool
        super().__init__(None, flags)
        self.welcome = welcome
        self.main = main
        self.setWindowTitle("PC Widgets Setup")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        geo = screen.geometry()
        self.setGeometry(geo)
        self.scale = max(1.0, screen.logicalDotsPerInch() / 96.0)
        self.backdrop = welcome.backdrop_for(geo)
        text_area = QRect(geo.width() // 4, geo.height() // 4, geo.width() // 2, geo.height() // 2)
        self.dim = dim_for(self.backdrop, text_area)
        if welcome.offscreen:
            self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self._bg: Optional[QPixmap] = None      # the backdrop under its veil, drawn once
        self.show_mark = main
        self.steps = None
        self._leave = None

        self.title = self.start_btn = self.skip_btn = None
        if main:
            self.setAccessibleName("Welcome to PC Widgets")
            self.title = _Title(self.scale, self)
            self.start_btn = _StartButton(self.scale, welcome.motion, self)
            self.skip_btn = _SkipButton(self.scale, welcome.motion, self)
            self.start_btn.clicked.connect(welcome.start)
            self.skip_btn.clicked.connect(welcome.skip)
            # Keyboard focus starts on the screen, not the button: a focused
            # Start Setup is drawn filled, and it should open as an outline.
            # Enter still starts and Esc still skips; Tab reaches the buttons.
            self.setFocusPolicy(Qt.StrongFocus)
            self._lay_out()

    def _lay_out(self) -> None:
        s = self.scale
        tw, th = self.title.size_for()
        total = th + 40 * s + self.start_btn.height() + 14 * s + self.skip_btn.height()
        top = (self.height() - total) / 2 - 20 * s
        self.title.setGeometry(round((self.width() - tw) / 2), round(top), tw, th)
        y = top + th + 40 * s
        self.start_btn.move(round((self.width() - self.start_btn.width()) / 2), round(y))
        y += self.start_btn.height() + 14 * s
        self.skip_btn.move(round((self.width() - self.skip_btn.width()) / 2), round(y))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self.main:
            self._lay_out()

    def set_intro(self, title: float, buttons: float) -> None:
        if not self.main or self.steps is not None:
            return
        self.title.set_progress(title)
        for b in (self.start_btn, self.skip_btn):
            b.alpha = buttons
            b.setEnabled(buttons > 0.5)
            b.update()

    def open_steps(self, again: bool = False) -> None:
        """The welcome gives way to the steps: "Welcome" blurs away as it came,
        the buttons fade, and the first step comes in over the same veil."""
        from .setup_steps import Steps
        self.steps = Steps(self.welcome, self, again=again)
        self.steps.show()
        self.steps.raise_()
        self.show_mark = False
        words = [w for w in (self.title, self.start_btn, self.skip_btn) if w is not None]
        for b in (self.start_btn, self.skip_btn):
            if b is not None:
                b.setEnabled(False)

        def fade(t: float) -> None:
            if self.title is not None:
                self.title.set_progress(1.0 - t)
            for b in (self.start_btn, self.skip_btn):
                if b is not None:
                    b.alpha = 1.0 - t
                    b.update()

        def gone() -> None:
            fade(1.0)
            for w in words:
                w.hide()
            self._leave = None

        if words and self.welcome.motion and not again:
            from ..core.frameclock import Tween
            self._leave = Tween(self)
            self._leave.setStartValue(0.0)
            self._leave.setEndValue(1.0)
            self._leave.setDuration(LEAVE_MS)
            self._leave.valueChanged.connect(fade)
            self._leave.finished.connect(gone)
            self._leave.start()
            # The first step comes in as "Welcome" is nearly gone, not over it.
            QTimer.singleShot(round(LEAVE_MS * 0.6), self._begin_steps)
        else:
            gone()
            self.steps.begin()
        self.update()

    def _begin_steps(self) -> None:
        if self.steps is not None and self.steps.index < 0:
            self.steps.begin()

    def _background(self) -> QPixmap:
        """The blurred wallpaper and its veil, drawn once at the screen's size:
        the steps repaint parts of the screen many times a second, and redoing
        a full-screen scale and fill for each would be most of the work."""
        if self._bg is None or self._bg.size() != self.size():
            pm = QPixmap(self.size())
            p = QPainter(pm)
            r = self.rect()
            if self.backdrop is None:
                p.fillRect(r, PLAIN)
            else:
                p.drawImage(r, self.backdrop)
                p.fillRect(r, QColor(0, 0, 0, round(255 * self.dim)))
            p.end()
            self._bg = pm
        return self._bg

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._background())
        if self.main and self.show_mark:
            self._paint_mark(p)
        p.end()

    def _paint_mark(self, p: QPainter) -> None:
        """The app's mark and name at the foot of the screen, so a full-screen
        window that appeared on its own says whose it is."""
        s = self.scale
        f = theme.font(10.5, QFont.DemiBold)
        fm = QFontMetricsF(f)
        name = "PC Widgets"
        mark = 18 * s
        gap = 10 * s
        w = mark + gap + fm.horizontalAdvance(name)
        x = (self.width() - w) / 2
        y = self.height() - 34 * s - mark
        p.setRenderHint(QPainter.Antialiasing, True)
        tile = mark * 25 / 64
        for dx, dy in ((5, 5), (34, 5), (5, 34), (34, 34)):
            q = QRectF(x + mark * dx / 64, y + mark * dy / 64, tile, tile)
            p.fillPath(theme.rounded(q, tile * 0.3), QColor("#ffffff"))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 204))
        p.drawText(QRectF(x + mark + gap, y - 4 * s, w, mark + 8 * s),
                   Qt.AlignLeft | Qt.AlignVCenter, name)

    # -- input ----------------------------------------------------------- #
    def keyPressEvent(self, ev) -> None:
        if self.steps is not None:
            self.steps.keyPressEvent(ev)
            return
        key = ev.key()
        if key == Qt.Key_Escape:
            self.welcome.skip()
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            if self.main and self.skip_btn is not None and self.skip_btn.hasFocus():
                self.welcome.skip()
            else:
                self.welcome.start()
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if not self.main:
            self.welcome.focus_main()       # a click on another screen comes back here
        elif self.welcome.state == "intro":
            self.welcome.finish_intro()
        super().mousePressEvent(ev)

    def changeEvent(self, ev) -> None:
        if ev.type() == QEvent.ActivationChange:
            self.welcome.activation_changed()
        super().changeEvent(ev)


# --------------------------------------------------------------------------- #
#  The whole welcome
# --------------------------------------------------------------------------- #
class Welcome(QObject):
    """Opens the stages, runs the intro and the steps, and ends with the
    widgets in place.

    ``state``: waiting (for a game to leave) -> intro -> ready -> steps ->
    leaving -> closed. ``again`` is "Set up again" from Settings: the veil
    opens straight onto the steps, and their Skip becomes Cancel. ``motion``
    and ``glass`` default to Windows' animation and transparency settings, and
    ``threaded`` to searching and fetching off the GUI thread; the selftest
    passes all three.
    """

    finished = Signal(str)          # "done", "skipped" or "cancelled"

    def __init__(self, board, offscreen: bool = False, motion: Optional[bool] = None,
                 glass: Optional[bool] = None, again: bool = False,
                 threaded: bool = True) -> None:
        super().__init__()
        self.board = board
        self.offscreen = offscreen
        self.motion = winapi.animations_enabled() if motion is None else motion
        self.glass = winapi.transparency_enabled() if glass is None else glass
        self.again = again
        self.threaded = threaded
        self.stages: List[Stage] = []
        self.main: Optional[Stage] = None
        self.state = "closed"
        self._front = True
        self._intro = None
        self._lift = None
        self._game = QTimer(self)
        self._game.setInterval(GAME_POLL_MS)
        self._game.timeout.connect(self._retry)

    # -- opening ---------------------------------------------------------- #
    def open(self) -> None:
        """Show it now, or as soon as no full-screen game has the foreground."""
        if self.state not in ("closed", "waiting"):
            self.bring_up()
            return
        if winapi.foreground_is_fullscreen():
            self.state = "waiting"
            self._game.start()
            return
        self._game.stop()
        self._show()

    def _retry(self) -> None:
        if self.state == "waiting":
            self.open()

    def backdrop_for(self, geo: QRect) -> Optional[QImage]:
        """That screen's wallpaper, softened further than the cards need it:
        their blur is tuned for a 168 px tile, and behind a whole screen it would
        still show the picture's shapes."""
        if not self.glass:
            return None
        g = getattr(self.board, "glass", None)
        if g is None or g.image is None:
            return None
        ox, oy = g.origin
        crop = g.image.copy(QRect(geo.x() - ox, geo.y() - oy, geo.width(), geo.height()))
        if crop.isNull():
            return None
        small = crop.scaled(max(1, crop.width() // 12), max(1, crop.height() // 12),
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return small.scaled(crop.width(), crop.height(), Qt.IgnoreAspectRatio,
                            Qt.SmoothTransformation)

    def _show(self) -> None:
        primary = QGuiApplication.primaryScreen()
        lead = primary.name() if primary is not None else ""
        screens = QGuiApplication.screens()
        self.stages = [Stage(self, sc, sc.name() == lead) for sc in screens]
        self.main = next((st for st in self.stages if st.main), None)
        if self.main is None:           # no primary reported: the first screen leads
            self.stages[0].deleteLater()
            self.stages[0] = Stage(self, screens[0], True)
            self.main = self.stages[0]
        for st in self.stages:
            st.setWindowOpacity(0.0 if self.motion else 1.0)
            st.show()
        self.focus_main()
        if self.again:
            # Set up again: the veil comes in and the steps are already there.
            self.state = "steps"
            self.main.open_steps(again=True)
            end = VEIL_IN[1]
        else:
            self.state = "intro"
            end = BUTTONS_IN[1]
        if not self.motion:
            self._apply(end)
            self._intro_done()
            return
        self._apply(0.0)
        from ..core.frameclock import Tween
        self._intro = Tween(self)
        self._intro.setStartValue(0.0)
        self._intro.setEndValue(end)
        self._intro.setDuration(end * 1000)
        self._intro.valueChanged.connect(self._apply)
        self._intro.finished.connect(self._intro_done)
        self._intro.start()

    def _apply(self, t: float) -> None:
        veil = _ease(t, VEIL_IN)
        for st in self.stages:
            st.setWindowOpacity(veil)
        if self.main is not None:
            self.main.set_intro(_ease(t, TITLE_IN), _ease(t, BUTTONS_IN))

    def _intro_done(self) -> None:
        if self.state == "intro":
            self.state = "ready"

    def finish_intro(self) -> None:
        """Jump to the end of the intro: a click or Enter while it plays."""
        if self.state != "intro":
            return
        if self._intro is not None:
            self._intro.stop()
        self._apply(BUTTONS_IN[1])
        self.state = "ready"

    # -- focus and stacking ----------------------------------------------- #
    def focus_main(self) -> None:
        if self.main is None:
            return
        if self.offscreen:
            # Never activate an offscreen veil: it is still a real window the
            # size of the screen, and as the foreground window it reads as a
            # full-screen game to everything that checks for one.
            self.main.setFocus(Qt.OtherFocusReason)
            return
        for st in self.stages:
            st.raise_()
        self.main.raise_()
        self.main.activateWindow()
        self.main.setFocus(Qt.ActiveWindowFocusReason)

    def bring_up(self) -> None:
        """Another launch, or the tray, asked for the window while it's open."""
        if self.state in ("intro", "ready", "steps"):
            self._set_front(True)
            self.focus_main()

    def activation_changed(self) -> None:
        QTimer.singleShot(0, self._settle_front)

    def _settle_front(self) -> None:
        if self.state not in ("intro", "ready", "steps"):
            return
        self._set_front(any(st.isActiveWindow() for st in self.stages))

    def _set_front(self, front: bool) -> None:
        """Topmost only while the welcome has the focus: away, it sits behind
        whatever you switched to, and the taskbar shows again."""
        if front == self._front:
            return
        self._front = front
        if self.offscreen:
            return
        for st in self.stages:
            winapi.bring_to_front(int(st.winId()), front)

    # -- the steps, and ending ---------------------------------------------- #
    def start(self) -> None:
        if self.state == "intro":
            self.finish_intro()
            return
        if self.state != "ready" or self.main is None:
            return
        self.state = "steps"
        self.main.open_steps()

    def skip(self) -> None:
        """Skip Setup, Esc, or a step's Skip: whatever has been chosen so far,
        and the starter board's choices for the rest. On a second run the same
        button is Cancel, and nothing changes."""
        if self.state not in ("intro", "ready", "steps"):
            return
        if self.again:
            self._end("cancelled", None)
            return
        steps = self.main.steps if self.main is not None else None
        if steps is not None:
            self._end("skipped", steps.choices, steps.start)
            return
        from .setup_steps import Choices
        starter = Choices.from_board(self.board)
        self._end("skipped", starter, starter.copy())

    def finish(self) -> None:
        """Done, on the last screen: every choice made."""
        steps = self.main.steps if self.main is not None else None
        if self.state == "steps" and steps is not None:
            self._end("done", steps.choices, steps.start)

    def _end(self, how: str, choices, start=None) -> None:
        self.state = "leaving"
        if self._intro is not None:
            self._intro.stop()
        board = self.board
        if choices is not None:
            from .setup_steps import apply
            apply(board, choices, start if start is not None else choices)
        board.state.setup = "done"
        board.persist(force=True)
        if not self.motion:
            self._closed(how)
            return
        from ..core.frameclock import Tween
        self._lift = Tween(self)
        self._lift.setStartValue(1.0)
        self._lift.setEndValue(0.0)
        self._lift.setDuration(LIFT_MS)
        self._lift.valueChanged.connect(
            lambda v: [st.setWindowOpacity(v) for st in self.stages])
        self._lift.finished.connect(lambda: self._closed(how))
        self._lift.start()

    def _closed(self, how: str) -> None:
        for st in self.stages:
            if st.steps is not None:
                st.steps.release()
            st.hide()
            st.deleteLater()
        self.stages = []
        self.main = None
        self.state = "closed"
        self.finished.emit(how)
