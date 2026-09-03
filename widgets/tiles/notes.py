"""
A sticky note you can actually type in.

The card paints itself, so this hosts a real ``QPlainTextEdit`` on top of the
painted surface rather than trying to reimplement text editing. It saves on a
short debounce after typing stops - not on every keystroke - so the disk isn't
hit once per character.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QPlainTextEdit

from ..core.card import MARGIN, PAD, WidgetCard
from ..core.store import notes_path


class NotesWidget(WidgetCard):
    kind = "notes"
    allow_image = True
    title = "Notes"
    customizable = True
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 0        # purely user-driven
    wants_focus = True    # it is a text field; it has to be typeable

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.edit = QPlainTextEdit(self)
        self.edit.setFrameStyle(0)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.edit.setPlaceholderText("Jot something…")
        self.edit.textChanged.connect(self._touched)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save)

        self.edit.setFocusPolicy(Qt.StrongFocus)
        self._load()
        self._restyle()
        self._relayout()

    def on_focus_click(self, ev) -> None:
        self.edit.setFocus(Qt.MouseFocusReason)

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        try:
            if os.path.exists(notes_path()):
                with open(notes_path(), "r", encoding="utf-8") as fh:
                    self.edit.setPlainText(fh.read())
        except Exception:
            pass

    def _touched(self) -> None:
        self._save_timer.start(600)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(notes_path()), exist_ok=True)
            tmp = notes_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(self.edit.toPlainText())
            os.replace(tmp, notes_path())
        except Exception:
            pass

    def closeEvent(self, ev):
        self._save()
        super().closeEvent(ev)

    # ------------------------------------------------------------------ #
    def _restyle(self) -> None:
        pal = self.palette_
        f = QFont("Segoe UI", 10)
        self.edit.setFont(f)
        # The one tile where the text colour is not the user's to set: notes are
        # for reading back, so they stay in contrast with the background no
        # matter what it is - the request was explicit about this. On glass
        # there is no single background colour, so the theme's own text is used.
        from ..core import appearance
        if self.solid_bg and self.config.get("bg"):
            ink = appearance.contrast_text(self.config["bg"])
        else:
            ink = pal.text
        self.edit.setStyleSheet(
            "QPlainTextEdit {"
            "  background: transparent; border: none;"
            f" color: {ink};"
            f" selection-background-color: {pal.accent};"
            "  selection-color: #ffffff;"
            "}"
        )

    def set_palette_(self, palette) -> None:
        super().set_palette_(palette)
        self._restyle()

    def on_appearance_changed(self) -> None:
        self._restyle()

    def _relayout(self) -> None:
        # Inset by the shadow gutter as well as the card padding, or the text
        # sits outside the visible card.
        left = int(MARGIN + PAD)
        top = int(MARGIN + PAD) + 20
        self.edit.setGeometry(left, top,
                              self.width() - left * 2,
                              self.height() - top - int(MARGIN + PAD))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout()

    # ------------------------------------------------------------------ #
    def paint_content(self, p, r: QRectF) -> None:
        # Only the header; the text itself is a real editor widget on top.
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Notes")
