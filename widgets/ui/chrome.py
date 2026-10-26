"""
chrome.py
=========
The app window's frame: a custom title area that is still a Windows window.

The stock title bar is a strip of system UI laid over the design, and it can't
take a material, so it goes: the content runs to the top edge, with drawn
caption buttons and a strip to drag by. Removing a frame the naive way - a
frameless Qt window - also removes everything Windows hangs off one: the drop
shadow, the rounded corners, Aero Snap, Win+arrow and the minimise and maximise
animations. That is most of the difference between a window that feels native
and a web page in a box. So the frame styles are put back and the frame is made
zero-sized instead (winapi.native_frame):

- WM_NCCALCSIZE answers "the client area is the whole window", so no frame is
  drawn. When maximised the client is clipped to the monitor's work area:
  Windows parks a maximised window's frame just off the screen, and that
  overhang would otherwise be client area cut off at the edges. (Qt sizes a
  maximised frameless window to the work area exactly instead, so a fixed
  inset would leave a gap; clipping is right either way.)
- WM_NCHITTEST answers "resize" in a thin band around the edge, since a
  zero-sized frame has no resize border of its own.

Dragging hands over to Windows' own move loop (QWindow.startSystemMove), so
snapping to screen edges works exactly as it does for any other window.

The backdrop is the frosted material: the blurred wallpaper - the same one the
widgets use - under a heavy tint, with the sidebar's tint lighter so more of
the wallpaper shows through it, and a faint grain over both.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Callable, Sequence

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..core import winapi
from . import theme

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT = 10, 11, 12, 13
HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 14, 15, 16, 17


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wt.RECT * 3), ("lppos", ctypes.c_void_p)]


class CaptionButtons(QWidget):
    """Minimise, maximise and close, drawn the way Windows 11 draws them."""

    W, H = 46, 38

    def __init__(self, window: QWidget, kinds: Sequence[str]) -> None:
        super().__init__(window)
        self.win = window
        self.kinds = list(kinds)
        self.setFixedSize(self.W * len(self.kinds), self.H)
        self.setMouseTracking(True)
        self._hover = -1
        self._down = -1

    def _hit(self, x: float) -> int:
        i = int(x // self.W)
        return i if 0 <= i < len(self.kinds) and 0 <= x < self.width() else -1

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        for i, kind in enumerate(self.kinds):
            r = QRectF(i * self.W, 0, self.W, self.H)
            hot, down = i == self._hover, i == self._down
            ink = QColor(ui.text)
            if hot and kind == "close":
                p.fillRect(r, QColor("#b5281b" if down else "#c42b1c"))
                ink = QColor("#ffffff")
            elif hot:
                p.fillRect(r, QColor(ui.control if down else ui.control_hi))
            elif not self.win.isActiveWindow():
                ink = QColor(ui.text_dim)
            p.setPen(QPen(ink, 1.0))
            p.setBrush(Qt.NoBrush)
            cx, cy = round(r.center().x()), round(r.center().y())
            if kind == "min":
                p.drawLine(cx - 5, cy, cx + 5, cy)
            elif kind == "max":
                if self.win.isMaximized():
                    p.drawRect(cx - 5, cy - 3, 8, 8)
                    p.drawLine(cx - 3, cy - 5, cx + 5, cy - 5)
                    p.drawLine(cx + 5, cy - 5, cx + 5, cy + 3)
                else:
                    p.drawRect(cx - 5, cy - 5, 10, 10)
            elif kind == "close":
                p.setRenderHint(QPainter.Antialiasing, True)
                p.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
                p.drawLine(QPointF(cx + 5, cy - 5), QPointF(cx - 5, cy + 5))
                p.setRenderHint(QPainter.Antialiasing, False)
        p.end()

    def mouseMoveEvent(self, ev) -> None:
        i = self._hit(ev.position().x())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _ev) -> None:
        self._hover = -1
        self._down = -1
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._down = self._hit(ev.position().x())
            self.update()

    def mouseReleaseEvent(self, ev) -> None:
        down, self._down = self._down, -1
        self.update()
        if ev.button() != Qt.LeftButton or down < 0 or down != self._hit(ev.position().x()):
            return
        kind = self.kinds[down]
        if kind == "min":
            self.win.showMinimized()
        elif kind == "max":
            toggle_maximised(self.win)
        elif kind == "close":
            self.win.close()


def toggle_maximised(win: QWidget) -> None:
    if win.isMaximized():
        win.showNormal()
    else:
        win.showMaximized()


class DragStrip(QWidget):
    """The empty top band that moves the window, and maximises it on a
    double-click, as a title bar would."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.win = window

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            handle = self.win.windowHandle()
            if handle is not None:
                handle.startSystemMove()
            ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            toggle_maximised(self.win)
            ev.accept()


class Chrome:
    """Mixin for a QWidget or QDialog: frameless, still a proper Windows window,
    painted on the frosted material.

    The subclass sets ``Qt.FramelessWindowHint`` before the native window exists,
    calls :meth:`init_chrome` once built, :meth:`place_chrome` from
    ``resizeEvent``, and :meth:`paint_backdrop` from ``paintEvent``.
    """

    EDGE = 6            # resize band, in pixels
    TITLE_H = 46        # the draggable strip

    def init_chrome(self, glass: Callable[[], object] = lambda: None,
                    kinds: Sequence[str] = ("min", "max", "close"),
                    side: Callable[[], int] = lambda: 0) -> None:
        """``glass`` returns the GlassBackdrop to frost with, or None when the
        glass is switched off; ``side`` the sidebar's width, if there is one."""
        self._glass = glass
        self._side = side
        self.caption = CaptionButtons(self, kinds)
        self.drag = DragStrip(self)
        self._moved = QTimer(self)
        self._moved.setSingleShot(True)
        self._moved.timeout.connect(self.update)
        winapi.native_frame(int(self.winId()), theme.current().dark)
        self.place_chrome()

    def place_chrome(self) -> None:
        if not hasattr(self, "caption"):
            return
        self.caption.move(self.width() - self.caption.width(), 0)
        self.drag.setGeometry(0, 0, self.width() - self.caption.width(), self.TITLE_H)
        self.drag.raise_()
        self.caption.raise_()

    def paint_backdrop(self, p: QPainter) -> None:
        """The material, from a cached picture of it.

        Built once for the window's size and place on screen and blitted after
        that. Every frame of a scroll repaints the backdrop under the moving
        content, and a single opaque copy is the cheapest thing that can be
        drawn there; blending the wallpaper, two tints and the grain each time
        is not.
        """
        side = int(self._side())
        glass = self._glass()
        origin = self.mapToGlobal(QPoint(0, 0))
        stamp = (glass.image.cacheKey() if glass is not None and glass.image is not None
                 else 0)
        key = (self.width(), self.height(), origin.x(), origin.y(), side, stamp,
               theme._generation)
        if getattr(self, "_backdrop_key", None) != key:
            self._backdrop = self._compose_backdrop(side, glass, origin)
            self._backdrop_key = key
        p.drawPixmap(0, 0, self._backdrop)

    def _compose_backdrop(self, side: int, glass, origin) -> QPixmap:
        ui = theme.current()
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        p = QPainter(pm)
        r = QRectF(pm.rect())
        content = QRectF(side, 0, r.width() - side, r.height())
        sidebar = QRectF(0, 0, side, r.height())
        p.fillRect(r, QColor(ui.solid))
        drew = glass is not None and glass.paint_into(p, r, origin.x(), origin.y())
        if drew:
            p.fillRect(content, QColor(ui.tint))
            if side:
                p.fillRect(sidebar, QColor(ui.tint_side))
        elif side:
            p.fillRect(sidebar, QColor(ui.solid_side))
        grain = theme.noise(ui.dark)
        if grain is not None:
            p.drawTiledPixmap(r.toRect(), grain)
        if side:
            p.setPen(QPen(QColor(ui.line), 1.0))
            p.drawLine(QPointF(side - 0.5, 0), QPointF(side - 0.5, r.height()))
        p.end()
        return pm

    def moveEvent(self, ev) -> None:
        # The material is the wallpaper *behind* the window, so it changes as
        # the window moves - repainted once the move settles rather than on
        # every step of a drag, which would redraw the whole window 60 times a
        # second for a difference too soft to follow.
        super().moveEvent(ev)
        if hasattr(self, "_moved"):
            self._moved.start(90)

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if hasattr(self, "caption"):
            self.caption.update()           # maximise glyph, inactive dimming

    def nativeEvent(self, event_type, message):
        try:
            kind = bytes(event_type.data()) if hasattr(event_type, "data") else bytes(event_type)
            if kind == b"windows_generic_MSG":
                msg = wt.MSG.from_address(int(message))
                if msg.message == WM_NCCALCSIZE:
                    if msg.wParam and winapi.is_maximised(msg.hWnd):
                        rc = NCCALCSIZE_PARAMS.from_address(msg.lParam).rgrc[0]
                        wl, wt_, wr, wb = winapi.monitor_work_area(msg.hWnd)
                        rc.left, rc.top = max(rc.left, wl), max(rc.top, wt_)
                        rc.right, rc.bottom = min(rc.right, wr), min(rc.bottom, wb)
                    return True, 0
                if msg.message == WM_NCHITTEST and not winapi.is_maximised(msg.hWnd):
                    code = self._edge_hit(msg)
                    if code:
                        return True, code
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def _edge_hit(self, msg) -> int:
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        left, top, right, bottom = winapi.window_rect(msg.hWnd)
        e = self.EDGE
        at_l, at_r = x < left + e, x >= right - e
        at_t, at_b = y < top + e, y >= bottom - e
        if at_t and at_l:
            return HTTOPLEFT
        if at_t and at_r:
            return HTTOPRIGHT
        if at_b and at_l:
            return HTBOTTOMLEFT
        if at_b and at_r:
            return HTBOTTOMRIGHT
        if at_l:
            return HTLEFT
        if at_r:
            return HTRIGHT
        if at_t:
            return HTTOP
        if at_b:
            return HTBOTTOM
        return 0
