"""
chrome.py
=========
The app window's frame: drawn, shaped like a widget, and still a Windows window.

The stock title bar is a strip of system UI laid over the design, so it goes:
the content runs to the top edge, with drawn caption buttons and a strip to
drag by. And the window takes the widgets' own outline - their continuous
corner, at their radius - which Windows can't draw: the corners it rounds are a
fixed 8 px, and plainly circular. So the window is a layered one
(``Qt.WA_TranslucentBackground``) and everything about its shape is painted
here: the body cut to the widgets' curve, and the drop shadow - which Windows
only draws for opaque windows - in a transparent margin round it.

Removing a frame the naive way - a frameless Qt window - also removes what
Windows hangs off one: Aero Snap, Win+arrow, resizing from the edges, the system
menu. That is most of the difference between a window that feels native and a
web page in a box. So the frame styles are put back and the frame is made
zero-sized instead (winapi.native_frame):

- WM_NCCALCSIZE answers "the client area is the whole window", so no frame is
  drawn. When maximised the client is clipped to the monitor's work area:
  Windows parks a maximised window's frame just off the screen, and that
  overhang would otherwise be client area cut off at the edges. (Qt sizes a
  maximised frameless window to the work area exactly instead, so a fixed
  inset would leave a gap; clipping is right either way.)
- WM_NCHITTEST answers "resize" all round the visible edge: across the shadow
  outside it - where Windows 11 keeps its own invisible resize border - a few
  pixels inside it, and over the corner's curve. A zero-sized frame has no
  resize border of its own.

Dragging hands over to Windows' own move loop (QWindow.startSystemMove), so
snapping to screen edges works exactly as it does for any other window. A
maximised or snapped window fills its space exactly, as Windows 11's own do: no
margin, no shadow, and square corners.

A window with a sidebar has it frosted, running to the window's edge with a
hairline between it and the page. Frosted the way Windows 11's Mica is: Mica
isn't see-through either - it is the desktop wallpaper, blurred and tinted, as
though the window were a pane laid over the desktop - so it is the board's own
blurred wallpaper (glass.py), the slice that lies behind the sidebar, under a
grey tint and a faint grain. That slice changes as the window moves, so the
glass is a picture of its own, a sidebar wide: rebuilt when the window moves or
the wallpaper changes, and blitted under every other repaint. DWM's own
materials can't be used for the reason the widgets can't use them: they fill
the whole window, the transparent shadow margin included.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Callable, Optional, Sequence

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QRegion
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
    """Minimise, maximise and close: Windows' line glyphs on rounded pads, set
    in from the corner far enough to sit inside its curve.

    The widget itself runs right up to the corner, so on a maximised window the
    very corner pixel of the screen still closes it, as on any other window.
    """

    W, H = 40, 32           # one pad
    INSET = 10              # from the body's top and right edges

    def __init__(self, window: QWidget, kinds: Sequence[str]) -> None:
        super().__init__(window)
        self.win = window
        self.kinds = list(kinds)
        self.setFixedSize(self.W * len(self.kinds) + self.INSET, self.H + self.INSET)
        self.setMouseTracking(True)
        self._hover = -1
        self._down = -1

    def _hit(self, x: float) -> int:
        if not 0 <= x < self.width():
            return -1
        return min(len(self.kinds) - 1, int(x // self.W))

    def pad(self, i: int) -> QRectF:
        return QRectF(i * self.W + 2, self.INSET, self.W - 4, self.H)

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        p = theme.hints(QPainter(self))
        for i, kind in enumerate(self.kinds):
            r = self.pad(i)
            hot, down = i == self._hover, i == self._down
            ink = QColor(ui.text)
            if hot and kind == "close":
                p.fillPath(theme.rounded(r, 9), QColor("#b5281b" if down else "#c42b1c"))
                ink = QColor("#ffffff")
            elif hot:
                p.fillPath(theme.rounded(r, 9), QColor(ui.control_hi if down else ui.control))
            elif not self.win.isActiveWindow():
                ink = QColor(ui.text_dim)
            p.setPen(QPen(ink, 1.0))
            p.setBrush(Qt.NoBrush)
            cx, cy = round(r.center().x()), round(r.center().y())
            p.setRenderHint(QPainter.Antialiasing, False)
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
            p.setRenderHint(QPainter.Antialiasing, True)
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
    """Mixin for a QWidget or QDialog: frameless, cut to the widgets' shape,
    still a proper Windows window.

    The subclass sets ``Qt.FramelessWindowHint`` and
    ``Qt.WA_TranslucentBackground`` before the native window exists, gives
    itself a layout, then calls :meth:`init_chrome` once built,
    :meth:`place_chrome` from ``resizeEvent``, and :meth:`paint_backdrop` from
    ``paintEvent``. The layout's margins are kept at what it set plus the
    shadow margin, so its content always lands on the visible body.
    """

    EDGE = 5                # resize band inside the visible edge, in pixels
    GRIP = 28               # how far from a corner, along either edge, resizes diagonally
    TITLE_H = 46            # the draggable strip

    def init_chrome(self, kinds: Sequence[str] = ("min", "max", "close"),
                    side: Callable[[], int] = lambda: 0,
                    glass: Callable[[], object] = lambda: None) -> None:
        """``side`` gives the sidebar's width, if the window has one, and
        ``glass`` the GlassBackdrop it is frosted with - or None, for a sidebar
        of plain grey."""
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._side = side
        self._glass = glass
        lay = self.layout()
        self._base_margins = lay.contentsMargins() if lay is not None else None
        self._margin = -1
        self._backdrop_key = None
        self._glass_key = None
        self._inner_key = None
        self.caption = CaptionButtons(self, kinds)
        self.drag = DragStrip(self)
        winapi.native_frame(int(self.winId()), theme.current().dark)
        self.sync_frame()

    # -- the visible body ------------------------------------------------ #
    def frame_margin(self) -> int:
        """The transparent margin round the body: room for the shadow, or 0
        when maximised or snapped."""
        return max(0, self._margin)

    def body_rect(self) -> QRect:
        m = self.frame_margin()
        return self.rect().adjusted(m, m, -m, -m)

    def side_rect(self) -> QRect:
        """The sidebar's column of the body, its hairline included."""
        body = self.body_rect()
        return QRect(body.left(), body.top(), int(self._side()), body.height())

    def corner(self) -> float:
        return theme.CORNER if self.frame_margin() else 0.0

    def _wanted_margin(self) -> int:
        if self.isMaximized() or self.isFullScreen():
            return 0
        if self.testAttribute(Qt.WA_WState_Created) and winapi.is_arranged(int(self.winId())):
            return 0
        return theme.WINDOW_SHADOW

    def sync_frame(self) -> None:
        """Put the margin, the layout and the caption where the window's state
        - normal, maximised, snapped - says they go."""
        m = self._wanted_margin()
        if m == self._margin:
            return
        self._margin = m
        lay = self.layout()
        if lay is not None and self._base_margins is not None:
            b = self._base_margins
            lay.setContentsMargins(b.left() + m, b.top() + m, b.right() + m, b.bottom() + m)
        self.place_chrome()
        self.update()

    def place_chrome(self) -> None:
        if not hasattr(self, "caption"):
            return
        body = self.body_rect()
        self.caption.move(body.right() + 1 - self.caption.width(), body.top())
        self.drag.setGeometry(body.left(), body.top(),
                              body.width() - self.caption.width(), self.TITLE_H)
        self.drag.raise_()
        self.caption.raise_()

    # -- painting -------------------------------------------------------- #
    def paint_backdrop(self, p: QPainter, rect: QRect) -> None:
        """The window's shape, shadow and sidebar, from cached pictures.

        Built once per size and state. Every frame of a scroll repaints the
        backdrop under the moving content, and almost all of that is plain
        window colour: that part is filled - the cheapest thing that can be
        drawn - and only what lies over an edge or a corner is copied from the
        picture, straight, with no blending. The sidebar's glass is a picture
        of its own, laid over the rest only where the repaint reaches it.
        """
        key = (self.width(), self.height(), self._margin, int(self._side()),
               theme._generation)
        if self._backdrop_key != key:
            self._backdrop = self._compose_backdrop()
            self._plain = self._plain_region()
            self._backdrop_key = key
        dirty = QRegion(rect)
        plain = dirty.intersected(self._plain)
        rest = dirty.subtracted(self._plain)
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_Source)
        if not plain.isEmpty():
            p.setClipRegion(plain)
            p.fillRect(plain.boundingRect(), QColor(theme.current().base))
        if not rest.isEmpty():
            p.setClipRegion(rest)
            box = rest.boundingRect()
            p.drawPixmap(box, self._backdrop, box)
        p.restore()
        side = self.side_rect()
        if side.width() > 0 and side.intersects(rect):
            p.drawPixmap(side.topLeft(), self._side_glass())

    def _side_glass(self) -> QPixmap:
        """The sidebar: the slice of blurred wallpaper behind it, tinted and
        grained, cut to the window's corners on the left, with the hairline on
        its right edge and the window's own outline redrawn over it.

        Keyed on where the sidebar is on screen as well as its size, since the
        slice is whatever lies behind it - and on the wallpaper, for a new one.
        """
        ui = theme.current()
        side = self.side_rect()
        body = QRectF(self.body_rect())
        radius = self.corner()
        at = self.mapToGlobal(side.topLeft())
        glass = self._glass()
        image = getattr(glass, "image", None)
        key = (side.width(), side.height(), at.x(), at.y(), radius, theme._generation,
               image.cacheKey() if image is not None else 0)
        if self._glass_key == key:
            return self._glass_pm
        w, h = max(1, side.width()), max(1, side.height())

        # Opaque first: where the window hangs off the edge of the desktop
        # there is no wallpaper to sample, and a see-through gap in a layered
        # window is a hole straight through it.
        raw = QPixmap(w, h)
        raw.fill(QColor(ui.side_solid))
        rp = QPainter(raw)
        rp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if glass is not None and glass.paint_into(rp, QRectF(0, 0, w, h), at.x(), at.y()):
            rp.fillRect(QRectF(0, 0, w, h), QColor(ui.side_glass))
            grain = theme.noise(ui.dark)
            if grain is not None:
                rp.drawTiledPixmap(0, 0, w, h, grain)
        rp.end()

        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = theme.hints(QPainter(pm))
        # A clip path is drawn aliased, so the corner is cut by filling the
        # window's own outline with the glass as a brush, which is smoothed.
        outline = body.translated(-side.left(), -side.top())
        if radius:
            p.fillPath(theme.smooth(outline, radius), QBrush(raw))
        else:
            p.drawPixmap(0, 0, raw)
        p.setPen(QPen(QColor(ui.side_line), 1.0))
        p.drawLine(QPointF(w - 0.5, 0), QPointF(w - 0.5, h))
        if radius:
            p.setPen(QPen(QColor(ui.frame), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(theme.smooth(outline.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5))
        p.end()
        self._glass_pm, self._glass_key = pm, key
        return pm

    def _plain_region(self) -> QRegion:
        """Where the backdrop is nothing but the window's colour: the body
        clear of its hairline, its corners and the sidebar."""
        body = self.body_rect()
        side = int(self._side())
        plain = QRegion(body.adjusted(side, 1, -1, -1))
        c = int(theme.CORNER * 1.6) + 2         # the corner's curve, and a little
        for x in (body.left(), body.right() + 1 - c):
            for y in (body.top(), body.bottom() + 1 - c):
                plain = plain.subtracted(QRegion(x, y, c, c))
        return plain

    def _compose_backdrop(self) -> QPixmap:
        ui = theme.current()
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        pm.fill(Qt.transparent)
        p = theme.hints(QPainter(pm))
        body = QRectF(self.body_rect())
        radius = self.corner()
        if radius:
            theme.paint_window_shadow(p, body, radius, ui.shadow)
            p.fillPath(theme.smooth(body, radius), QColor(ui.base))
        else:
            p.fillRect(body, QColor(ui.base))
        if radius:
            p.setPen(QPen(QColor(ui.frame), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(theme.smooth(body.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5))
        p.end()
        return pm

    # -- window events --------------------------------------------------- #
    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if hasattr(self, "caption"):
            self.sync_frame()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if hasattr(self, "caption"):
            self.sync_frame()
            self.place_chrome()

    def moveEvent(self, ev) -> None:
        super().moveEvent(ev)
        if hasattr(self, "caption"):
            self.sync_frame()       # snapping to an edge can move a window without resizing it
            # The glass shows what lies behind the sidebar, which a move changes.
            # Only the sidebar is repainted, and repaints asked for faster than
            # they can be drawn are merged into one.
            if self._side():
                self.update(self.side_rect())

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if hasattr(self, "caption"):
            self.sync_frame()
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
                if msg.message == WM_NCHITTEST:
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    left, top, _r, _b = winapi.window_rect(msg.hWnd)
                    code = self.edge_hit(QPointF(x - left, y - top))
                    if code is not None:
                        return True, code
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def edge_hit(self, pt: QPointF) -> Optional[int]:
        """The resize direction for a point in window coordinates, or None
        where the point is ordinary client area.

        Windows only asks about points the window actually covers - a layered
        window lets the mouse through wherever it is fully transparent - so
        everything outside the body that asks is the shadow, and resizes.
        """
        if not self.frame_margin():
            return None                 # maximised or snapped: nothing to pull
        body = QRectF(self.body_rect())
        key = (body.width(), body.height(), body.left())
        if self._inner_key != key:
            e = self.EDGE
            self._inner = theme.smooth(body.adjusted(e, e, -e, -e), theme.CORNER - e)
            self._inner_key = key
        if self._inner.contains(pt):
            return None
        dl, dr = pt.x() - body.left(), body.right() - pt.x()
        dt, db = pt.y() - body.top(), body.bottom() - pt.y()
        g = self.GRIP
        if min(dl, dr) < g and min(dt, db) < g:
            if dt < db:
                return HTTOPLEFT if dl < dr else HTTOPRIGHT
            return HTBOTTOMLEFT if dl < dr else HTBOTTOMRIGHT
        nearest = min(dl, dr, dt, db)
        if nearest == dl:
            return HTLEFT
        if nearest == dr:
            return HTRIGHT
        return HTTOP if nearest == dt else HTBOTTOM
