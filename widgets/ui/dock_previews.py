"""Live window previews over a dock button, as the taskbar shows them.

Hover an app with windows open and, after a moment, a panel rises above its
button with a live picture of each window: its icon and title on top, a close
button on the one under the pointer. Click a picture to bring that window
forward; close it from its button or with a middle-click.

The pictures are the compositor's own thumbnails (DwmRegisterThumbnail), the
same live images the taskbar uses, video and all - nothing is captured or
copied, and they cost this process nothing to keep up to date. They are why
the panel is opaque (see dock_panel): the compositor never draws one into a
layered window. They fade in with the rest through the compositor's own
thumbnail opacity.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF

from ..core import dock_shell as shell
from ..core.card import squircle
from .dock_panel import DockPanel, HALO, SHOW_MS, MOVE_MS, HIDE_MS  # noqa: F401 (the selftest's)

THUMB_W, THUMB_H = 200, 120     # the largest a picture gets, at 100% scaling
CARD_MIN = 150                  # a card is never narrower, so a title has room
PAD, GAP, HEAD = 8, 6, 28       # the panel's margin, between cards, a card's title row
CLOSE = 24
CLOSE_GLYPH = ""          # Segoe Fluent Icons' ChromeClose


class Card:
    __slots__ = ("hwnd", "title", "rect", "thumb", "close", "picture")

    def __init__(self, hwnd, title, picture):
        self.hwnd, self.title, self.picture = hwnd, title, picture
        self.rect = self.thumb = self.close = QRectF()


class WindowPreviews(DockPanel):
    TITLE = "PC Widgets Glass Dock previews"

    def __init__(self, dock):
        super().__init__(dock)
        self.ident = ""
        self.entry = None
        self.cards: list[Card] = []
        self._hover = None            # (hwnd, "card" | "close")
        self._pressed = None
        self._anchor = (0, 0)

    def show_for(self, ident, entry, cx, top):
        """Above the button whose centre is at x = cx and the dock's top at y =
        top, both on the screen. The same app again only refreshes titles and
        place; a different set of windows registers its pictures afresh."""
        hwnds = [t.hwnd for t in entry.windows]
        fresh = ident != self.ident or hwnds != [c.hwnd for c in self.cards]
        if fresh:
            self.release()
            hwnd = int(self.winId())
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
        self.present(fresh and self.isVisible())

    def release(self):
        for c in self.cards:
            if c.picture is not None:
                c.picture.close()
        self.cards = []
        self._hover = self._pressed = None

    def _reset(self):
        super()._reset()
        self.ident, self.entry = "", None

    def layout(self) -> QRect:
        s = self.dock._s
        cx, top = self._anchor
        g = self.screen_rect()
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

    def place_extras(self):
        for c in self.cards:
            if c.picture is not None:
                r = c.thumb.translated(self._offset)
                c.picture.place(int(r.left()), int(r.top()), int(r.right()), int(r.bottom()),
                                round(255 * self._alpha))

    def paint_content(self, p, pal, s):
        dock = self.dock
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

    # -- input ------------------------------------------------------------ #
    def _at(self, pos):
        if self._leaving:
            return None
        pos = self.local(pos)
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
