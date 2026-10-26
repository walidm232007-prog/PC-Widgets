"""
cropper.py
==========
Move and scale a background photo inside its card.

The editor shows the whole photo with the card cut out of it: everything outside
the card's shape is dimmed, and inside it is the real card - the photo exactly as
the card will paint it, with the card's own text, scrim and corners on top.
Placing a photo so the time doesn't land across someone's face needs the time to
be there while you do it; a bare crop rectangle would leave that to guesswork.

Every change shows on the desktop widget as it is made, so with the editor moved
aside the real thing can be checked too. Cancel puts everything back.

Dialogs here are never parented to a card. A card in the desktop layer is pushed
to the bottom of the z-order every second and a half, and Windows keeps owned
windows above their owner by moving them *with* it - so a dialog owned by a card
would sink behind every other window shortly after it opened.
"""

from __future__ import annotations

import math
import os
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from ..core import imagefit, startup, winapi
from ..core.card import RADIUS, squircle
from ..core.imagefit import Crop
from . import theme

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
SLIDER_STEPS = 1000
WHEEL_STEP = 1.0015          # per eighth of a degree: ~1.2x per mouse-wheel notch


class CropCanvas(QWidget):
    """The photo behind a card-shaped window: drag to move it, scroll to zoom."""

    changed = Signal()

    def __init__(self, card, image: QImage, crop: Crop, parent=None) -> None:
        super().__init__(parent)
        self.card = card
        self.image = image
        self.crop = crop
        self._drag: Optional[QPointF] = None
        self._view: Optional[QImage] = None
        self._view_key = None
        # The thirds grid shows while the photo is moving and fades from view
        # shortly after - a guide while positioning, clutter otherwise.
        self._moving = False
        self._still = QTimer(self)
        self._still.setSingleShot(True)
        self._still.timeout.connect(self._stop_moving)
        # The card inside the frame is live - a clock ticks - so keep it fresh.
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.update)
        self._tick.start(1000)
        self.setMinimumSize(560, 330)
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.crop = imagefit.normalise(*self.dims(), crop)

    # -- geometry -------------------------------------------------------- #
    def dims(self):
        r = self.card.card_rect()
        return self.image.width(), self.image.height(), r.width(), r.height()

    def frame(self):
        """Where the card sits in the canvas, and how much it is enlarged."""
        _iw, _ih, cw, ch = self.dims()
        k = min((self.width() - 120) / cw, (self.height() - 84) / ch, 2.2)
        k = max(0.2, k)
        fw, fh = cw * k, ch * k
        return QRectF((self.width() - fw) / 2, (self.height() - fh) / 2, fw, fh), k

    def _card_point(self, pos: QPointF):
        frame, k = self.frame()
        return ((pos.x() - frame.left()) / k, (pos.y() - frame.top()) / k)

    # -- changes --------------------------------------------------------- #
    def set_crop(self, crop: Crop) -> None:
        self.crop = imagefit.normalise(*self.dims(), crop)
        self.update()
        self.changed.emit()

    def set_image(self, image: QImage) -> None:
        self.image = image
        self._view = self._view_key = None
        self.set_crop(Crop())

    def zoom_to(self, zoom: float, about=None) -> None:
        self.set_crop(imagefit.zoomed(*self.dims(), self.crop, zoom, about))

    def nudge(self, dx: float, dy: float) -> None:
        self._moved()
        self.set_crop(imagefit.moved(*self.dims(), self.crop, dx, dy))

    def _moved(self) -> None:
        self._moving = True
        self._still.start(650)

    def _stop_moving(self) -> None:
        self._moving = False
        self.update()

    # -- painting -------------------------------------------------------- #
    def _view_for(self, w: int, h: int) -> QImage:
        """The photo at the size it is shown, so panning costs nothing.

        Only ever a reduction: when the photo is shown larger than it is, the
        painter scales the original up directly, which is both cheaper and no
        worse than any copy made here.
        """
        if w >= self.image.width() or h >= self.image.height():
            return self.image
        if self._view_key != (w, h):
            self._view = self.image.scaled(w, h, Qt.IgnoreAspectRatio,
                                           Qt.SmoothTransformation)
            self._view_key = (w, h)
        return self._view

    def paintEvent(self, _ev) -> None:
        ui = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.fillRect(self.rect(), QColor(ui.sidebar))

        frame, k = self.frame()
        iw, ih, cw, ch = self.dims()
        left, top, w, h = imagefit.place(iw, ih, cw, ch, self.crop)
        vw, vh = max(1, round(w * k)), max(1, round(h * k))
        p.drawImage(QRectF(round(frame.left() + left * k),
                           round(frame.top() + top * k), vw, vh),
                    self._view_for(vw, vh))

        # Dim everything the card will not show.
        card_path = squircle(frame, RADIUS * k)
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        p.fillPath(outside.subtracted(card_path),
                   QColor(0, 0, 0, 150 if ui.dark else 125))

        # The real card at this scale: photo, scrim, text and all.
        r = self.card.card_rect()
        p.save()
        p.translate(frame.topLeft())
        p.scale(k, k)
        p.translate(-r.left(), -r.top())
        try:
            self.card.paint_card(p, opacity=1.0, shadow=False)
        except RuntimeError:
            pass                    # the card was removed while this was open
        p.restore()

        p.setBrush(Qt.NoBrush)
        if self._moving:
            p.save()
            p.setClipPath(card_path)
            p.setPen(QPen(QColor(255, 255, 255, 95), 1.0))
            for i in (1, 2):
                x = frame.left() + frame.width() * i / 3.0
                y = frame.top() + frame.height() * i / 3.0
                p.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
                p.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))
            p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 175), 1.25))
        p.drawPath(card_path)
        p.end()

    # -- input ----------------------------------------------------------- #
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._drag = ev.position()
            self.setCursor(Qt.ClosedHandCursor)
            self._moved()
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is None:
            return
        _frame, k = self.frame()
        d = ev.position() - self._drag
        self._drag = ev.position()
        self.nudge(d.x() / k, d.y() / k)

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, _ev) -> None:
        self._moved()
        self.set_crop(Crop())

    def wheelEvent(self, ev) -> None:
        delta = ev.angleDelta().y() or ev.pixelDelta().y()
        if not delta:
            return
        self._moved()
        self.zoom_to(self.crop.zoom * WHEEL_STEP ** delta,
                     self._card_point(ev.position()))
        ev.accept()

    def keyPressEvent(self, ev) -> None:
        step = 20.0 if ev.modifiers() & Qt.ShiftModifier else 4.0
        move = {Qt.Key_Left: (-step, 0.0), Qt.Key_Right: (step, 0.0),
                Qt.Key_Up: (0.0, -step), Qt.Key_Down: (0.0, step)}.get(ev.key())
        if move is not None:
            self.nudge(*move)
        elif ev.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_to(self.crop.zoom * 1.1)
        elif ev.key() == Qt.Key_Minus:
            self.zoom_to(self.crop.zoom / 1.1)
        elif ev.key() == Qt.Key_0:
            self.set_crop(Crop())
        else:
            super().keyPressEvent(ev)


class CropDialog(QDialog):
    """The editor window. Accept keeps the photo where it is; reject restores."""

    def __init__(self, card, path: str, image: QImage, parent=None) -> None:
        super().__init__(parent)
        self.card = card
        self.path = path
        self._saved = {k: card.config.get(k) for k in ("image", "crop")}
        self._gone = False
        card.destroyed.connect(self._card_gone)

        ui = theme.current()
        self.setObjectName("Dialog")
        self.setWindowTitle(f"Background photo — {card.title}")
        ico = startup.icon_path()
        if ico:
            self.setWindowIcon(QIcon(ico))
        self.setPalette(theme.qpalette(ui))
        self.setStyleSheet(theme.stylesheet(ui))
        self.setFont(theme.font(10))
        self.resize(780, 620)

        start = (imagefit.from_config(card.config)
                 if card.config.get("image") == path else Crop())
        self.canvas = CropCanvas(card, image, start)
        self.canvas.changed.connect(self._changed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)

        title = QLabel("Position your photo")
        title.setFont(theme.font(17, QFont.Bold))
        sub = QLabel(f"{card.title} · {card.size_class.capitalize()}")
        sub.setProperty("tone", "dim")
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(title)
        head.addWidget(sub)
        lay.addLayout(head)
        lay.addWidget(self.canvas, 1)
        hint = QLabel("Drag to move  ·  Scroll to zoom  ·  Double-click to reset")
        hint.setProperty("tone", "faint")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(10)
        smaller = QPushButton("−")
        larger = QPushButton("+")
        for b in (smaller, larger):
            b.setProperty("kind", "flat")
            b.setFixedWidth(34)
            b.setFont(theme.font(13, QFont.DemiBold))
        smaller.setToolTip("Zoom out")
        larger.setToolTip("Zoom in")
        smaller.clicked.connect(lambda: self.canvas.zoom_to(self.canvas.crop.zoom / 1.15))
        larger.clicked.connect(lambda: self.canvas.zoom_to(self.canvas.crop.zoom * 1.15))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(0, SLIDER_STEPS)
        self.zoom.valueChanged.connect(self._slid)
        fit = QPushButton("Fit whole photo")
        fit.setToolTip("Show all of it, with the card's colour around it")
        fit.clicked.connect(self._fit)
        fill = QPushButton("Fill card")
        fill.setToolTip("The smallest size that leaves no gaps")
        fill.clicked.connect(self._fill)
        zoom_row.addWidget(smaller)
        zoom_row.addWidget(self.zoom, 1)
        zoom_row.addWidget(larger)
        zoom_row.addSpacing(12)
        zoom_row.addWidget(fit)
        zoom_row.addWidget(fill)
        lay.addLayout(zoom_row)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        other = QPushButton("Choose another photo…")
        other.clicked.connect(self._choose_other)
        remove = QPushButton("Remove photo")
        remove.setProperty("kind", "danger")
        remove.clicked.connect(self._remove)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        done = QPushButton("Done")
        done.setProperty("kind", "primary")
        done.setDefault(True)
        done.setMinimumWidth(96)
        done.clicked.connect(self.accept)
        foot.addWidget(other)
        foot.addWidget(remove)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(done)
        lay.addLayout(foot)

        self._syncing = False
        self._changed()
        self.canvas.setFocus()

    # ------------------------------------------------------------------ #
    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        ui = theme.current()
        winapi.style_titlebar(int(self.winId()), ui.dark, ui.window, ui.text)

    def _card_gone(self, *_a) -> None:
        self._gone = True
        QDialog.reject(self)

    def _bounds(self):
        iw, ih, cw, ch = self.canvas.dims()
        return imagefit.zoom_range(iw, ih, cw, ch)

    def _changed(self) -> None:
        """Show the change on the desktop card, and keep the slider in step."""
        if self._gone:
            return
        self.card.config["image"] = self.path
        saved = imagefit.to_config(self.canvas.crop)
        if saved is None:
            self.card.config.pop("crop", None)
        else:
            self.card.config["crop"] = saved
        self.card.update()

        lo, hi = self._bounds()
        z = max(lo, min(hi, self.canvas.crop.zoom))
        v = 0 if hi <= lo else round(SLIDER_STEPS * math.log(z / lo) / math.log(hi / lo))
        self._syncing = True
        self.zoom.setValue(v)
        self._syncing = False

    def _slid(self, v: int) -> None:
        if self._syncing:
            return
        lo, hi = self._bounds()
        self.canvas._moved()
        self.canvas.zoom_to(lo * (hi / lo) ** (v / SLIDER_STEPS))

    def _fit(self) -> None:
        lo, _hi = self._bounds()
        self.canvas.set_crop(Crop(0.5, 0.5, lo))

    def _fill(self) -> None:
        self.canvas.zoom_to(1.0)

    def _choose_other(self) -> None:
        path = ask_for_photo(self, os.path.dirname(self.path))
        if not path:
            return
        img = imagefit.load(path)
        if img is None:
            _cannot_open(self, path)
            return
        self.path = path
        self.canvas.set_image(img)

    def _remove(self) -> None:
        if not self._gone:
            self.card.set_image(None)
        self.done(QDialog.Accepted)

    def accept(self) -> None:
        if not self._gone:
            self.card.set_image(self.path, imagefit.to_config(self.canvas.crop))
        super().accept()

    def reject(self) -> None:
        if not self._gone:
            for key, value in self._saved.items():
                if value is None:
                    self.card.config.pop(key, None)
                else:
                    self.card.config[key] = value
            self.card.update()
        super().reject()


# --------------------------------------------------------------------------- #
def ask_for_photo(parent=None, start: str = "") -> str:
    if not start or not os.path.isdir(start):
        pictures = os.path.join(os.path.expanduser("~"), "Pictures")
        start = pictures if os.path.isdir(pictures) else ""
    path, _f = QFileDialog.getOpenFileName(parent, "Choose a background photo",
                                           start, IMAGE_FILTER)
    return path


def _cannot_open(parent, path: str) -> None:
    QMessageBox.warning(parent, "PC Widgets",
                        f"That picture couldn't be opened:\n{path}")


def edit(card, parent=None, image_path: Optional[str] = None) -> bool:
    """Position ``card``'s photo. With ``image_path``, start from that photo -
    one just chosen - instead of the card's current one. True if kept."""
    path = image_path or card.config.get("image")
    if not path:
        return False
    img = imagefit.load(path)
    if img is None:
        _cannot_open(parent, path)
        return False
    dlg = CropDialog(card, path, img, parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    kept = dlg.exec() == QDialog.Accepted
    dlg.deleteLater()
    return kept


def choose(card, parent=None) -> bool:
    """Pick a photo for ``card``, then position it. Nothing changes on cancel."""
    current = card.config.get("image") or ""
    path = ask_for_photo(parent, os.path.dirname(current) if current else "")
    if not path:
        return False
    return edit(card, parent, image_path=path)
