"""
tray.py
=======
The tray icon: add widgets, switch theme, change layer, quit. Also hosts the
global hotkey, because ``RegisterHotKey`` needs a window handle to deliver
``WM_HOTKEY`` to and the tray owns a hidden one for exactly this purpose.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .core import startup, winapi
from .core.board import Board, available
from .core.palette import ACCENTS


def _icon(accent: str = "#3b82f6") -> QIcon:
    """Four rounded tiles - the universal 'widgets' mark.

    Prefers the generated multi-size .ico so the tray, the taskbar and the
    shortcut all show the identical mark; falls back to painting it.
    """
    from .core import startup as _startup
    ico = _startup.icon_path()
    if ico:
        return QIcon(ico)
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    boxes = [(6, 6, 24, 24), (34, 6, 24, 24), (6, 34, 24, 24), (34, 34, 24, 24)]
    for i, (x, y, w, h) in enumerate(boxes):
        p.setBrush(QColor(accent) if i in (0, 3) else QColor(160, 172, 190, 220))
        p.drawRoundedRect(QRectF(x, y, w, h), 7, 7)
    p.end()
    return QIcon(pm)


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Turns WM_HOTKEY into a Python callback."""

    def __init__(self, hotkey_id: int, callback) -> None:
        super().__init__()
        self.hotkey_id = hotkey_id
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        try:
            if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                import ctypes
                from ctypes import wintypes

                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == winapi.WM_HOTKEY and msg.wParam == self.hotkey_id:
                    self.callback()
        except Exception:
            pass
        return False, 0


class WidgetTray(QSystemTrayIcon):
    def __init__(self, board: Board, app) -> None:
        super().__init__(_icon(board.palette.accent))
        self.board = board
        self.app = app
        self._hk_window: QWidget | None = None
        self._hk_filter: _HotkeyFilter | None = None
        self._hk_id = 0

        self.setToolTip("PC Widgets — Ctrl+Alt+W to peek")
        self.activated.connect(
            lambda r: self.board.toggle_peek()
            if r == QSystemTrayIcon.Trigger else None
        )
        self.rebuild_menu()
        self.show()

    # ------------------------------------------------------------------ #
    def rebuild_menu(self) -> None:
        m = QMenu()

        add = m.addMenu("Add widget")
        for cls in available():
            sub = add.addMenu(cls.title)
            for size in cls.supported_sizes:
                a = QAction(size.capitalize(), sub)
                a.triggered.connect(
                    lambda _c, k=cls.kind, s=size: self._add(k, s))
                sub.addAction(a)

        m.addSeparator()

        peek = QAction("Bring to front", m)
        peek.triggered.connect(self.board.toggle_peek)
        m.addAction(peek)

        layer = m.addMenu("Layer")
        grp = QActionGroup(layer)
        for key, label in (("desktop", "On the desktop (behind windows)"),
                           ("normal", "Floating"),
                           ("top", "Always on top")):
            a = QAction(label, layer, checkable=True)
            a.setChecked(self.board.state.layer == key)
            a.setActionGroup(grp)
            a.triggered.connect(lambda _c, k=key: self.board.set_layer(k))
            layer.addAction(a)

        theme = m.addMenu("Theme")
        tg = QActionGroup(theme)
        for key in ("dark", "light"):
            a = QAction(key.capitalize(), theme, checkable=True)
            a.setChecked(self.board.state.theme == key)
            a.setActionGroup(tg)
            a.triggered.connect(lambda _c, k=key: self._set_theme(k))
            theme.addAction(a)

        accent = m.addMenu("Accent")
        ag = QActionGroup(accent)
        for key in ACCENTS:
            a = QAction(key.capitalize(), accent, checkable=True)
            a.setChecked(self.board.state.accent == key)
            a.setActionGroup(ag)
            a.triggered.connect(lambda _c, k=key: self._set_accent(k))
            accent.addAction(a)

        op = m.addMenu("Opacity")
        for pct in (100, 90, 80, 65, 50):
            a = QAction(f"{pct}%", op)
            a.triggered.connect(lambda _c, v=pct: self.board.set_opacity(v / 100))
            op.addAction(a)

        glass = QAction("Frosted glass", m, checkable=True)
        glass.setChecked(self.board.state.glass)
        glass.toggled.connect(self.board.set_glass)
        m.addAction(glass)

        frost = m.addMenu("Glass strength")
        fg = QActionGroup(frost)
        for label, alpha in (("Clearer", 78), ("Balanced", 112), ("Frostier", 158)):
            a = QAction(label, frost, checkable=True)
            a.setChecked(abs(self.board.state.glass_tint - alpha) < 12)
            a.setActionGroup(fg)
            a.triggered.connect(lambda _c, v=alpha: self.board.set_glass_tint(v))
            frost.addAction(a)

        snap = QAction("Snap to grid", m, checkable=True)
        snap.setChecked(self.board.state.snap)
        snap.toggled.connect(self.board.set_snap)
        m.addAction(snap)

        lock = QAction("Lock positions", m, checkable=True)
        lock.setChecked(self.board.state.locked)
        lock.toggled.connect(self.board.set_locked)
        m.addAction(lock)

        save_now = QAction("Save layout now", m)
        save_now.triggered.connect(self._save_layout)
        m.addAction(save_now)

        m.addSeparator()

        boot = QAction("Start with Windows", m, checkable=True)
        boot.setChecked(startup.is_enabled())
        boot.toggled.connect(self._set_startup)
        m.addAction(boot)

        shortcut = QAction("Add desktop shortcut", m)
        shortcut.triggered.connect(self._desktop_shortcut)
        m.addAction(shortcut)

        folder = QAction("Open widgets folder", m)
        folder.triggered.connect(self._open_folder)
        m.addAction(folder)

        m.addSeparator()
        quit_a = QAction("Quit", m)
        quit_a.triggered.connect(self._quit)
        m.addAction(quit_a)

        self.setContextMenu(m)

    # ------------------------------------------------------------------ #
    def _add(self, kind: str, size: str) -> None:
        self.board.add(kind, size)

    def _set_startup(self, on: bool) -> None:
        ok = startup.set_enabled(on)
        self.showMessage(
            "PC Widgets",
            ("Will start with Windows." if on else "Won't start with Windows.")
            if ok else "Couldn't update the Startup folder.",
            self.icon(), 4000)
        self.rebuild_menu()

    def _save_layout(self) -> None:
        """Write the arrangement on demand, and say so either way.

        The board saves itself, but a silent autosave gives you no way to check
        before shutting the machine down.
        """
        from .core import store
        ok = self.board.persist(force=True)
        self.showMessage(
            "PC Widgets",
            f"Layout saved — {len(self.board.cards)} widgets. "
            "It will come back exactly like this."
            if ok else f"Couldn't save the layout. See {store.log_path()}",
            self.icon(), 4000)

    def _desktop_shortcut(self) -> None:
        path = startup.create_desktop_shortcut()
        self.showMessage(
            "PC Widgets",
            f"Shortcut created: {path}" if path
            else "Couldn't create the desktop shortcut.",
            self.icon(), 4000)

    def _open_folder(self) -> None:
        import os
        os.startfile(os.path.dirname(startup.app_link()))

    def _set_theme(self, key: str) -> None:
        self.board.set_theme(key)
        self.setIcon(_icon(self.board.palette.accent))

    def _set_accent(self, key: str) -> None:
        self.board.set_accent(key)
        self.setIcon(_icon(self.board.palette.accent))

    def _quit(self) -> None:
        self.remove_hotkey()
        self.board.close_all()
        self.hide()
        QCoreApplication.instance().quit()

    # ------------------------------------------------------------------ #
    def install_hotkey(self, hotkey_id: int, mods: int, vk: int) -> bool:
        """RegisterHotKey needs a real HWND, so keep a hidden one alive."""
        self._hk_window = QWidget()
        self._hk_window.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self._hk_window.resize(1, 1)
        self._hk_window.show()
        self._hk_window.hide()

        self._hk_id = hotkey_id
        ok = winapi.register_hotkey(int(self._hk_window.winId()), hotkey_id, mods, vk)
        if ok:
            self._hk_filter = _HotkeyFilter(hotkey_id, self.board.toggle_peek)
            self.app.installNativeEventFilter(self._hk_filter)
        return ok

    def remove_hotkey(self) -> None:
        if self._hk_window is not None and self._hk_id:
            winapi.unregister_hotkey(int(self._hk_window.winId()), self._hk_id)
        if self._hk_filter is not None:
            self.app.removeNativeEventFilter(self._hk_filter)
