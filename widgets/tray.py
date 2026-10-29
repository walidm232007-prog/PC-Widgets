"""
tray.py
=======
The tray icon: click it for the app window, right-click for the quick menu -
add widgets, switch presets, switch theme, change layer, quit. Also hosts the
global hotkey, because ``RegisterHotKey`` needs a window handle to deliver
``WM_HOTKEY`` to and the tray owns a hidden one for exactly this purpose. The
same hidden window is
how a second launch of the app reaches this one (see :mod:`main`).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, Qt
from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .core import displays, mark, startup, winapi
from .core.board import Board, available
from .core.palette import ACCENTS


def _icon() -> QIcon:
    """The mark: four white tiles (core/mark.py).

    Prefers the generated multi-size .ico so the tray, the taskbar and the
    shortcut all show the identical mark; falls back to painting it.
    """
    ico = startup.icon_path()
    if ico:
        return QIcon(ico)
    return QIcon(mark.pixmap(64))


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Turns WM_HOTKEY - and a second launch's knock - into Python callbacks."""

    def __init__(self, hotkey_id: int, callback, show_msg: int = 0,
                 on_show=None) -> None:
        super().__init__()
        self.hotkey_id = hotkey_id
        self.callback = callback
        self.show_msg = show_msg
        self.on_show = on_show

    def nativeEventFilter(self, event_type, message):
        try:
            if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                import ctypes
                from ctypes import wintypes

                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == winapi.WM_HOTKEY and msg.wParam == self.hotkey_id:
                    self.callback()
                elif self.show_msg and msg.message == self.show_msg and self.on_show:
                    self.on_show()
                    return True, 0
        except Exception:
            pass
        return False, 0


class WidgetTray(QSystemTrayIcon):
    def __init__(self, board: Board, app) -> None:
        super().__init__(_icon())
        self.board = board
        self.app = app
        self._hk_window: QWidget | None = None
        self._hk_filter: _HotkeyFilter | None = None
        self._hk_id = 0
        self._window = None
        self._menu: QMenu | None = None

        self.setToolTip("PC Widgets — click to open · Ctrl+Alt+W to peek")
        self.activated.connect(self._activated)
        # Settings can change from the app window too; keep the ticks honest.
        board.prefs_changed.connect(self.rebuild_menu)
        board.presets_changed.connect(self.rebuild_menu)
        self.rebuild_menu()
        self.show()

    def _activated(self, reason) -> None:
        # A click opens the app, the way every other tray icon behaves; peeking
        # is still on the hotkey, the menu and a middle click.
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()
        elif reason == QSystemTrayIcon.MiddleClick:
            self.board.toggle_peek()

    def show_window(self, page: str | None = None) -> None:
        """Open the app window, building it the first time it is asked for -
        a board started with Windows may never need it."""
        if self._window is None:
            from .ui.app import MainWindow
            self._window = MainWindow(self.board, self)
        self._window.bring_up(page)

    # ------------------------------------------------------------------ #
    def rebuild_menu(self) -> None:
        m = QMenu()

        open_app = QAction("Open PC Widgets", m)
        f = open_app.font()
        f.setBold(True)
        open_app.setFont(f)
        open_app.triggered.connect(lambda: self.show_window())
        m.addAction(open_app)
        m.addSeparator()

        add = m.addMenu("Add widget")
        for cls in available():
            sub = add.addMenu(cls.title)
            for size in cls.supported_sizes:
                a = QAction(size.capitalize(), sub)
                a.triggered.connect(
                    lambda _c, k=cls.kind, s=size: self._add(k, s))
                sub.addAction(a)

        # Filled as it opens: whether a preset is the one in use depends on
        # where every card is, and that changes without the menu hearing of it.
        presets = m.addMenu("Presets")
        presets.aboutToShow.connect(lambda menu=presets: self._fill_presets(menu))
        self._fill_presets(presets)

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

        start_menu = QAction("Add to Start menu", m)
        start_menu.triggered.connect(self._start_menu)
        m.addAction(start_menu)

        folder = QAction("Open widgets folder", m)
        folder.triggered.connect(self._open_folder)
        m.addAction(folder)

        m.addSeparator()
        quit_a = QAction("Quit", m)
        quit_a.triggered.connect(self._quit)
        m.addAction(quit_a)

        # The old menu may be the one whose action is running right now, so it
        # is retired with deleteLater rather than dropped on the spot.
        old, self._menu = self._menu, m
        self.setContextMenu(m)
        if old is not None:
            old.deleteLater()

    def _fill_presets(self, menu: QMenu) -> None:
        menu.clear()
        board = self.board
        for preset in board.state.presets:
            a = QAction(preset.name, menu, checkable=True)
            a.setChecked(board.preset_in_use(preset))
            a.triggered.connect(lambda _c, p=preset: self._apply_preset(p))
            menu.addAction(a)
        if not board.state.presets:
            menu.addAction("No presets yet").setEnabled(False)
        menu.addSeparator()
        save = QAction("Save layout as a preset", menu)
        save.setEnabled(bool(board.cards))
        save.triggered.connect(self._save_preset)
        menu.addAction(save)
        manage = QAction("Manage presets…", menu)
        manage.triggered.connect(lambda: self.show_window("presets"))
        menu.addAction(manage)

    # ------------------------------------------------------------------ #
    def _add(self, kind: str, size: str) -> None:
        # On the display the pointer is on: that is where the person picking
        # from the menu is looking.
        at = QCursor.pos()
        here = displays.at(at.x(), at.y(), self.board.screens)
        card = self.board.add(kind, size, display=here.key)
        if card is not None:
            self.board.spotlight(card)

    def _apply_preset(self, preset) -> None:
        if self.board.apply_preset(preset) is None:
            return
        n = len(self.board.cards)
        self.showMessage("PC Widgets",
                         f"“{preset.name}” is on your desktop — "
                         f"{n} widget{'s' if n != 1 else ''}.", self.icon(), 3000)

    def _save_preset(self) -> None:
        from .ui.app import waiting_note
        preset = self.board.save_preset()
        if preset is None:
            return
        note = waiting_note(self.board)
        self.showMessage("PC Widgets",
                         f"Saved as “{preset.name}”. Rename it in the app, "
                         "under Presets." + (f" {note}" if note else ""),
                         self.icon(), 4000)

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

    def _start_menu(self) -> None:
        path = startup.create_start_menu_shortcut()
        self.showMessage(
            "PC Widgets",
            "PC Widgets is in your Start menu." if path
            else "Couldn't add it to the Start menu.",
            self.icon(), 4000)

    def _open_folder(self) -> None:
        import os
        os.startfile(os.path.dirname(startup.app_link()))

    def _set_theme(self, key: str) -> None:
        self.board.set_theme(key)

    def _set_accent(self, key: str) -> None:
        self.board.set_accent(key)

    def _quit(self) -> None:
        self.remove_hotkey()
        if self._window is not None:
            self._window.hide()
        self.board.close_all()
        self.hide()
        QCoreApplication.instance().quit()

    # ------------------------------------------------------------------ #
    def install_hotkey(self, hotkey_id: int, mods: int, vk: int) -> bool:
        """RegisterHotKey needs a real HWND, so keep a hidden one alive.

        The window is titled so a second launch can find it with FindWindow,
        and the filter goes in whether or not the hotkey registers - another
        app holding Ctrl+Alt+W must not also stop the launcher opening the app.
        """
        self._hk_window = QWidget()
        self._hk_window.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self._hk_window.setWindowTitle(winapi.CONTROL_TITLE)
        self._hk_window.resize(1, 1)
        self._hk_window.show()
        self._hk_window.hide()

        self._hk_id = hotkey_id
        ok = winapi.register_hotkey(int(self._hk_window.winId()), hotkey_id, mods, vk)
        self._hk_filter = _HotkeyFilter(
            hotkey_id, self.board.toggle_peek,
            winapi.register_message(winapi.SHOW_MESSAGE), self.show_window)
        self.app.installNativeEventFilter(self._hk_filter)
        return ok

    def remove_hotkey(self) -> None:
        if self._hk_window is not None and self._hk_id:
            winapi.unregister_hotkey(int(self._hk_window.winId()), self._hk_id)
        if self._hk_filter is not None:
            self.app.removeNativeEventFilter(self._hk_filter)
