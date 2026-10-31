"""A floating primary-display dock, using the board's material and music backends."""
from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QFileInfo, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import QApplication, QColorDialog, QFileDialog, QFileIconProvider, QMenu, QWidget

from ..core import audio, dock_shell as shell, media, palette, typography, winapi
from ..core.card import squircle
from ..core.store import _log
from .theme import glyph, paint_shadow


class GlassDock(QWidget):
    """A dock has no activation on click, so app buttons can toggle their window."""
    def __init__(self, board, *, preview=False):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.board = board
        self.preview = preview
        self.setWindowTitle("PC Widgets Glass Dock")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.lease = shell.TaskbarLease()
        self._reservation_window = QWidget(None, Qt.Tool | Qt.FramelessWindowHint)
        self._workarea = shell.WorkArea(int(self._reservation_window.winId()))
        self._icons = {}
        self._provider = QFileIconProvider()
        self._tasks = []
        self._hits = []
        self._hover = ""
        self._pressed = ""
        self._reveal_until = 0.0
        self._running = False
        self._stop = None
        self._poller = None
        self._tap = None
        self._np = media.NowPlaying()
        self._levels = (0.0,) * 5
        self._frame = QTimer(self)
        self._frame.setInterval(20)
        self._frame.setTimerType(Qt.PreciseTimer)
        self._frame.timeout.connect(self._animate)
        self._watch = QTimer(self)
        self._watch.setInterval(1000)
        self._watch.timeout.connect(self._refresh)
        self._material = None
        self._material_key = None
        board.prefs_changed.connect(self.sync)
        board.glass_changed.connect(self.invalidate)
        board.displays_changed.connect(self._refresh)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.sync()

    def invalidate(self):
        self._material_key = None
        self.update()

    def sync(self):
        if self.preview:
            self.resize(1200, 88)
            self.invalidate()
            return
        if not self.board.state.dock_enabled:
            self.shutdown()
            return
        if not self._running:
            self._running = True
            self._stop = threading.Event()
            if os.environ.get("PCWIDGETS_NO_AUDIO") != "1":
                self._tap = audio.AudioTap()
                self._tap.start()
            stop = self._stop
            # Keep each worker's stop token: a quick off/on must never revive
            # an older worker when a slow WinRT read eventually finishes.
            self._poller = threading.Thread(target=self._poll, args=(stop,), daemon=True,
                                            name="dock-media")
            self._poller.start()
            self._watch.start()
        self.invalidate()
        self._refresh()

    def _poll(self, stop):
        source = media.MediaSource()
        while not stop.is_set():
            try:
                result = source.poll()
                if not stop.is_set():
                    self._np = result
            except Exception as exc:
                _log(f"dock: media read failed ({exc!r})")
            stop.wait(3.0 if source.backend == "helper" else 0.5)

    def shutdown(self):
        self._running = False
        self._watch.stop()
        self._frame.stop()
        self.hide()
        self._workarea.release()
        self.lease.restore()
        if self._stop:
            self._stop.set()
        if self._tap:
            tap, self._tap = self._tap, None
            # Audio shutdown may wait for the device: don't block the app UI.
            threading.Thread(target=tap.stop, daemon=True).start()

    def _refresh(self):
        if self.preview or not self._running:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            self.hide()
            self._workarea.release()
            self.lease.restore()
            return
        g = screen.geometry()
        fullscreen = winapi.fullscreen_monitor()
        on_primary = fullscreen and QRectF(*(
            fullscreen[0], fullscreen[1], fullscreen[2] - fullscreen[0],
            fullscreen[3] - fullscreen[1])).intersects(QRectF(g))
        if on_primary or time.monotonic() < self._reveal_until:
            self.hide()
            self._frame.stop()
            self.lease.reveal()
            return
        if self.board.state.dock_replace_taskbar:
            if not self.lease.hide():
                # Fail open: the dock can live above the native taskbar.
                self.lease.restore()
        else:
            self.lease.restore()
        scale = max(1.0, screen.logicalDotsPerInch() / 96.0)
        width = min(g.width() - 24, int(1320 * scale))
        height = int(88 * scale)
        if self.lease.hwnd:
            self._workarea.reserve((g.x(), g.y(), g.right() + 1, g.bottom() + 1),
                                   height, self.lease.hwnd)
        else:
            self._workarea.release()
        bottom = g.bottom() if self.lease.hwnd else screen.availableGeometry().bottom()
        self.setGeometry(g.x() + (g.width() - width) // 2, bottom - height + 1, width, height)
        fresh = shell.tasks()
        # Keep app order stable as z-order changes on activation.
        by_id = {t.hwnd: t for t in fresh}
        old_ids = {t.hwnd for t in self._tasks}
        self._tasks = [by_id[t.hwnd] for t in self._tasks if t.hwnd in by_id]
        self._tasks += [t for t in fresh if t.hwnd not in old_ids]
        self.show()
        self._frame.start()
        self.update()

    def _animate(self):
        levels = self._tap.levels() if self._tap else (0.0,) * 5
        if levels != self._levels:
            self._levels = levels
            # Only the music section changes at audio rate. Qt clips repaint.
            for rect, key, _, _ in self._hits:
                if key == "music":
                    self.update(rect.toAlignedRect())
                    break

    def _entries(self):
        pins = self.board.state.dock_pins
        entries = [("start", "Start", "gallery", lambda: shell.shortcut())]
        claimed = set()
        for path in pins:
            match = next((t for t in self._tasks if t.path and
                          os.path.normcase(t.path) == os.path.normcase(path)), None)
            if match:
                claimed.add(match.hwnd)
            entries.append(("pin:" + path, os.path.splitext(os.path.basename(path))[0], path,
                            (lambda h=match.hwnd: shell.activate(h)) if match else
                            (lambda p=path: self._launch(p))))
        for task in self._tasks:
            if task.hwnd not in claimed:
                entries.append((f"task:{task.hwnd}", task.title, task.path,
                                lambda h=task.hwnd: shell.activate(h)))
        return entries

    def _launch(self, path):
        try:
            os.startfile(path)
        except OSError as exc:
            _log(f"dock: launcher failed ({exc!r})")
            self.setToolTip("Couldn't open this app. Right-click to remove or replace the pin.")

    def _icon(self, path):
        if path not in self._icons:
            if len(self._icons) > 128:
                self._icons.clear()
            self._icons[path] = self._provider.icon(QFileInfo(path)) if path else QIcon()
        return self._icons[path]

    def paintEvent(self, event):
        from PySide6.QtGui import QImage
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        scale = self.height() / 88.0
        p.scale(scale, scale)
        w = self.width() / scale
        surface = QRectF(8, 8, w - 16, 66)
        pal, state = self.board.palette, self.board.state
        colour = QColor(state.dock_color) if state.dock_color else QColor(pal.card)
        if not colour.isValid():
            colour = QColor(pal.card)
        if state.dock_color and state.dock_style == "solid":
            brightness = (0.2126 * colour.redF() + 0.7152 * colour.greenF() +
                          0.0722 * colour.blueF())
            pal = palette.build("light" if brightness > 0.55 else "dark", state.accent)
        key = (self.size(), self.pos(), state.dock_style, colour.name(), state.glass_tint, pal)
        if key != self._material_key:
            self._material = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
            self._material.fill(Qt.transparent)
            q = QPainter(self._material)
            q.setRenderHint(QPainter.Antialiasing)
            q.scale(scale, scale)
            paint_shadow(q, surface, 24, blur=12, alpha=90, dy=3)
            path = squircle(surface, 24)
            q.setClipPath(path)
            if state.dock_style == "glass":
                q.save()
                q.scale(1 / scale, 1 / scale)
                self.board.glass.paint_into(q, QRectF(0, 0, self.width(), self.height()),
                                            self.x(), self.y())
                q.restore()
                colour.setAlpha(max(70, min(225, state.glass_tint)))
            q.fillPath(path, colour)
            q.setClipping(False)
            q.setBrush(Qt.NoBrush)
            q.setPen(QPen(QColor(255, 255, 255, 35) if pal.dark else QColor(0, 0, 0, 25), 1))
            q.drawPath(path)
            q.end()
            self._material_key = key
        p.save()
        p.scale(1 / scale, 1 / scale)
        p.drawImage(0, 0, self._material)
        p.restore()
        self._hits = []

        def hit(rect, key, tip, action):
            actual = QRectF(rect.x() * scale, rect.y() * scale,
                            rect.width() * scale, rect.height() * scale)
            self._hits.append((actual, key, tip, action))
            if self._hover == key:
                p.fillPath(squircle(rect, 13), QColor(255, 255, 255, 24) if pal.dark
                           else QColor(0, 0, 0, 15))

        # Compact layouts omit the artist before they crowd out system controls.
        music_w = 248 if w >= 1000 else 166 if w >= 760 else 82
        battery = winapi.battery()
        quick_w = (104 if battery.present else 64) if w >= 1000 else 48
        system_w = 142 + quick_w if w >= 760 else 130
        music_x = 18 if state.dock_music_side == "left" else w - system_w - music_w - 26
        music_rect = QRectF(music_x, 15, music_w, 52)
        hit(music_rect, "music", "Play / pause · right-click for music controls", media.play_pause)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.accent))
        for i, level in enumerate(self._levels):
            h = 5 + max(0, min(1, level)) * 29
            p.drawRoundedRect(QRectF(music_x + 12 + i * 9, 41 - h / 2, 5, h), 2.5, 2.5)
        if music_w > 82:
            p.setFont(typography.font(10, QFont.DemiBold))
            p.setPen(QColor(pal.text))
            title = self._np.title or "Nothing playing"
            tw = music_w - 75
            p.drawText(QRectF(music_x + 68, 22, tw, 20), Qt.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(title, Qt.ElideRight, int(tw)))
            p.setFont(typography.font(8))
            p.setPen(QColor(pal.text_dim))
            sub = self._np.artist or "Spotify / Apple Music"
            p.drawText(QRectF(music_x + 68, 42, tw, 16), Qt.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(sub, Qt.ElideRight, int(tw)))

        left = music_x + music_w + 14 if state.dock_music_side == "left" else 22
        right = w - system_w - 28 if state.dock_music_side == "left" else music_x - 14
        entries = self._entries()
        capacity = max(1, int((right - left) // 48))
        if len(entries) > capacity:
            entries = entries[:max(0, capacity - 1)] + [
                ("overflow", "More apps", "more", self._overflow)]
        group_w = len(entries) * 48
        x = max(left, min((w - group_w) / 2, right - group_w))
        foreground = shell.u.GetForegroundWindow() if not self.preview else 0
        for key, title, icon, action in entries:
            rect = QRectF(x, 17, 44, 48)
            hit(rect, key, title, action)
            if key == "start":
                glyph(p, "gallery", QRectF(x + 11, 27, 24, 24), pal.accent)
            elif key == "overflow":
                p.setPen(QColor(pal.text))
                p.drawText(rect, Qt.AlignCenter, "•••")
            else:
                ico = self._icon(icon)
                if ico.isNull():
                    glyph(p, "desktop", QRectF(x + 10, 26, 26, 26), pal.text)
                else:
                    ico.paint(p, int(x + 8), 25, 28, 28)
                active = key == f"task:{foreground}" or any(
                    t.hwnd == foreground and key == "pin:" + t.path for t in self._tasks)
                running = key.startswith("task:") or any(key == "pin:" + t.path for t in self._tasks)
                if running:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(pal.accent if active else pal.text_dim))
                    p.drawRoundedRect(QRectF(x + (14 if active else 19), 60,
                                             16 if active else 6, 3), 1.5, 1.5)
            x += 48

        sx = w - system_w - 18
        p.setPen(QPen(QColor(pal.text_faint), 1))
        p.drawLine(QPoint(int(sx - 9), 27), QPoint(int(sx - 9), 55))
        tray = QRectF(sx, 18, 30, 46)
        hit(tray, "tray", "Show Windows taskbar and all tray icons for 12 seconds", self.reveal_taskbar)
        p.setPen(QPen(QColor(pal.text), 1.6))
        p.drawLine(QPoint(int(sx + 9), 43), QPoint(int(sx + 15), 37))
        p.drawLine(QPoint(int(sx + 15), 37), QPoint(int(sx + 21), 43))
        if system_w > 130:
            quick = QRectF(sx + 32, 18, quick_w, 46)
            hit(quick, "quick", "Quick settings · Wi-Fi, volume and battery", lambda: shell.shortcut(0x41))
            p.setFont(QFont("Segoe MDL2 Assets", 11))
            p.setPen(QColor(pal.text))
            p.drawText(QRectF(sx + 34, 26, 23, 30), Qt.AlignCenter, "\ue701")
            p.drawText(QRectF(sx + 57, 26, 23, 30), Qt.AlignCenter, "\ue767")
            if battery.present and quick_w >= 104:
                p.setFont(typography.font(8))
                p.drawText(QRectF(sx + 82, 26, 49, 30), Qt.AlignCenter,
                           f"{battery.percent}%" + ("+" if battery.charging else ""))
        clock_x = sx + (34 + quick_w if system_w > 130 else 32)
        clock_rect = QRectF(clock_x, 17, 100, 48)
        hit(clock_rect, "clock", "Notifications and calendar", lambda: shell.shortcut(0x4E))
        p.setPen(QColor(pal.text))
        p.setFont(typography.font(10, QFont.DemiBold))
        p.drawText(QRectF(clock_x, 22, 96, 20), Qt.AlignCenter, time.strftime("%H:%M"))
        p.setFont(typography.font(8))
        p.setPen(QColor(pal.text_dim))
        p.drawText(QRectF(clock_x, 42, 96, 16), Qt.AlignCenter, time.strftime("%b %d"))
        p.end()

    def _hit(self, pos):
        return next((h for h in self._hits if h[0].contains(pos)), None)

    def mouseMoveEvent(self, event):
        hit = self._hit(event.position())
        key = hit[1] if hit else ""
        if self._hover != key:
            self._hover = key
            self.setToolTip(hit[2] if hit else "Glass Dock · right-click to customise")
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        self._hover = ""
        self.update()

    def mousePressEvent(self, event):
        hit = self._hit(event.position())
        self._pressed = hit[1] if hit and event.button() == Qt.LeftButton else ""

    def mouseReleaseEvent(self, event):
        hit = self._hit(event.position())
        if event.button() == Qt.LeftButton and hit and hit[1] == self._pressed:
            hit[3]()
        self._pressed = ""

    def reveal_taskbar(self):
        self._reveal_until = time.monotonic() + 12
        self._refresh()

    def _overflow(self):
        menu = QMenu(self)
        for _, title, _, action in self._entries():
            menu.addAction(title, action)
        menu.exec(self.mapToGlobal(QPoint(self.width() // 2, 0)))

    def add_pin(self):
        path, _ = QFileDialog.getOpenFileName(self, "Pin an app", "",
                                             "Applications (*.exe *.lnk *.url)")
        if path and path not in self.board.state.dock_pins:
            self.board.set_dock("pins", self.board.state.dock_pins + [path])

    def choose_color(self):
        colour = QColorDialog.getColor(QColor(self.board.state.dock_color or self.board.palette.card),
                                       self, "Dock colour")
        if colour.isValid():
            self.board.set_dock("color", colour.name())

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Pin an app…", self.add_pin)
        hit = self._hit(event.pos())
        if hit and hit[1].startswith("pin:"):
            path = hit[1][4:]
            menu.addAction("Unpin this app", lambda: self.board.set_dock(
                "pins", [p for p in self.board.state.dock_pins if p != path]))
        if hit and hit[1].startswith("task:"):
            task = next((t for t in self._tasks if hit[1] == f"task:{t.hwnd}"), None)
            if task and task.path and task.path not in self.board.state.dock_pins:
                menu.addAction("Pin this app", lambda: self.board.set_dock(
                    "pins", self.board.state.dock_pins + [task.path]))
        menu.addSeparator()
        menu.addAction("Previous track", media.prev_track)
        menu.addAction("Play / pause", media.play_pause)
        menu.addAction("Next track", media.next_track)
        menu.addSeparator()
        for key, label in (("glass", "Frosted glass"), ("solid", "Solid colour")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.board.state.dock_style == key)
            action.triggered.connect(lambda _=False, v=key: self.board.set_dock("style", v))
        menu.addAction("Choose colour…", self.choose_color)
        menu.addAction("Use theme colour", lambda: self.board.set_dock("color", ""))
        side = "right" if self.board.state.dock_music_side == "left" else "left"
        menu.addAction(f"Move music to the {side}", lambda: self.board.set_dock("music_side", side))
        menu.addSeparator()
        menu.addAction("Show Windows taskbar", self.reveal_taskbar)
        menu.addAction("Turn off Glass Dock", lambda: self.board.set_dock("enabled", False))
        menu.exec(event.globalPos())

    def closeEvent(self, event):
        self.shutdown()
        self._reservation_window.close()
        super().closeEvent(event)
