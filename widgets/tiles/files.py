"""
Files - a small file browser that lives on the desktop.

Not a launcher that hands off to Explorer: folders open *inside* the card, with
history, scrolling and a filter. Explorer is still one click away in the
right-click menu for the times you want the real thing.

Two data sources. The home view comes from **Quick Access**, which is what File
Explorer shows when it opens and is far better than the obvious source: on this
machine ``%APPDATA%\\Microsoft\\Windows\\Recent`` holds 655 shortcuts, most of
them protocol activations (``ms-gamingoverlay``, ``ms-screenclip``) whose targets
resolve to nothing at all. Inside a folder it is a plain ``os.scandir``.

Both run on one long-lived worker thread. A directory listing is usually a
couple of milliseconds, but "usually" is not good enough on the GUI thread - a
network path that has gone away blocks for the full timeout, and that would
freeze every card on the board, not just this one.

This tile is also the board's one exception to fixed sizes. Everything else is
designed for three known shapes, which is what keeps a mixed board looking
composed; a file list has no natural height, so this one can be dragged by the
corner to whatever fits the gap. See :meth:`_grip`.
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen

from ..core.card import Fader, WidgetCard, pixel_size

REFRESH = 90.0            # seconds between Quick Access re-reads
MAX_ENTRIES = 4000        # a folder bigger than this is a filesystem, not a list
ROW_H = 22.0
MIN_W, MIN_H = 260, 200   # smallest the window may be dragged to
GRIP = 16.0               # corner hit area for resizing

# Transitions. Two, because entering a folder from the home view and going one
# level deeper are different moves: the first is a change of mode, the second is
# travel along a path. Giving both the same animation would say they were the
# same thing.
FADE_MS = 240.0           # home -> a folder
SLIDE_MS = 300.0          # folder -> folder, in or out

QA_CLSID = "{679F85CB-0220-4080-B29B-5540CC05AAB6}"

FALLBACK_PLACES = ("Desktop", "Downloads", "Documents", "Pictures")

# Extension -> glyph family. Drawn, not loaded: the board has no image assets
# and this tile is not going to be the one that introduces them.
KINDS = {
    "img": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"},
    "aud": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aiff", ".flp", ".bin"},
    "vid": {".mp4", ".mov", ".mkv", ".avi", ".webm"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".json", ".c", ".cpp",
             ".rs", ".go", ".java", ".sh", ".ps1", ".bat", ".qml"},
    "zip": {".zip", ".rar", ".7z", ".tar", ".gz", ".mcpack", ".jar"},
}


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    for name, exts in KINDS.items():
        if ext in exts:
            return name
    return "doc"


def _shell_snapshot() -> Tuple[List[str], List[str]]:
    """Quick Access, split into folders and files. Slow; never on the GUI thread."""
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$sh = New-Object -ComObject Shell.Application;"
        "$qa = $sh.Namespace('shell:::" + QA_CLSID + "');"
        "foreach ($i in $qa.Items()) {"
        "  if ($i.IsFolder) { 'D|' + $i.Path } else { 'F|' + $i.Path } }"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, text=True, timeout=20,
            creationflags=0x08000000)
    except Exception:
        return [], []

    dirs: List[str] = []
    files: List[str] = []
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if len(line) < 3 or line[1] != "|":
            continue
        tag, path = line[0], line[2:]
        # Quick Access keeps entries for files that are gone. Offering one is a
        # dead click, so it is dropped here rather than failing on use.
        if not os.path.exists(path):
            continue
        (dirs if tag == "D" else files).append(path)
    return dirs, files


def _fallback_places() -> List[str]:
    home = os.path.expanduser("~")
    return [p for p in (os.path.join(home, f) for f in FALLBACK_PLACES)
            if os.path.isdir(p)]


class FilesWidget(WidgetCard):
    kind = "files"
    title = "Files"
    supported_sizes = ("small", "medium", "large")
    wants_focus = True        # the filter box has to be typeable
    refresh_ms = 0
    repaint_on_tick = False

    data_ready = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.places: List[str] = _fallback_places()
        self.recent: List[str] = []
        self.entries: List[Tuple[str, bool]] = []     # (path, is_dir)
        self.cwd: Optional[str] = None                # None = the home view
        self.error = ""
        self.query = ""
        self._searching = False       # only true after clicking the search bar
        self._scroll = 0.0
        # The transition plays when the *listing* arrives, not when the click
        # lands - the read is on a worker thread, and animating before the data
        # is back would slide the old folder in and then swap it.
        self._anim = Fader(self, fps=60)
        self._anim.finish()
        self._anim_kind = ""          # "fade" | "slide"
        self._anim_dir = 1.0          # +1 entering from the right, -1 from the left
        self._pending: Optional[Tuple[str, float]] = None
        self._hot: Optional[str] = None
        self._rows: List[Tuple[QRectF, str, bool]] = []
        self._chrome: List[Tuple[QRectF, str]] = []
        self._history: List[str] = []
        self._resizing = None
        self._pressed = None      # (path, tag) while a row is held
        self._press_at = None
        self._size0 = (self.width(), self.height())
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._want: Optional[str] = None
        self.data_ready.connect(self._on_data)

        # Free size, remembered per card. Falls back to the size class, so a
        # board saved before this existed keeps the shape it already had.
        w, h = self.config.get("w"), self.config.get("h")
        if isinstance(w, int) and isinstance(h, int):
            self.resize(max(MIN_W, w), max(MIN_H, h))

    # ------------------------------------------------------------------ #
    #  The worker
    # ------------------------------------------------------------------ #
    def on_start(self) -> None:
        self._start_reader()

    def closeEvent(self, ev) -> None:
        self._stop.set()
        self._wake.set()
        super().closeEvent(ev)

    def _start_reader(self) -> None:
        """One long-lived thread, not one per read.

        The media tile learned this the hard way: a thread spawned per poll
        needs a latch, and a latch set before a fallible operation is how that
        widget stopped updating for a week with nothing in any log.
        """
        if self._reader is not None and self._reader.is_alive():
            return
        self._stop.clear()
        self._reader = threading.Thread(target=self._loop, daemon=True,
                                        name="files-read")
        try:
            self._reader.start()
        except RuntimeError:
            self._reader = None

    def _loop(self) -> None:
        self._refresh_qa()
        while not self._stop.is_set():
            woke = self._wake.wait(REFRESH)
            if self._stop.is_set():
                break
            self._wake.clear()
            want = self._want
            if want is not None:
                self._want = None
                self._list_dir(want)
            elif not woke:
                self._refresh_qa()

    def _refresh_qa(self) -> None:
        dirs, files = _shell_snapshot()
        if dirs or files:
            self.places = dirs or _fallback_places()
            self.recent = files
            self.data_ready.emit()

    def _list_dir(self, path: str) -> None:
        rows: List[Tuple[str, bool, str]] = []
        try:
            with os.scandir(path) as it:
                for i, e in enumerate(it):
                    if i >= MAX_ENTRIES:
                        break
                    try:
                        is_dir = e.is_dir()
                    except OSError:
                        is_dir = False      # a link we are not allowed to follow
                    rows.append((e.path, is_dir, e.name))
            rows.sort(key=lambda r: (not r[1], r[2].lower()))
            self.entries = [(p, d) for p, d, _ in rows]
            self.error = ""
        except Exception as exc:
            # Permission denied, or the drive went away. Say which, on the card,
            # rather than showing an empty folder that looks the same as a real
            # empty folder.
            self.entries = []
            self.error = type(exc).__name__
        self.cwd = path
        self._scroll = 0.0
        self.data_ready.emit()

    def _on_data(self) -> None:
        if self._pending is not None:
            kind, direction = self._pending
            self._pending = None
            self._begin(kind, direction)
        self.update()

    # ------------------------------------------------------------------ #
    #  Navigation
    # ------------------------------------------------------------------ #
    def _go(self, path: Optional[str], remember: bool = True,
            back: bool = False) -> None:
        if remember and self.cwd:
            self._history.append(self.cwd)
        self.query = ""
        self._searching = False
        self._scroll = 0.0

        if path is None:
            # Back out to the home view: a fade, the same move as arriving.
            self.cwd = None
            self.entries = []
            self.error = ""
            self._pending = None
            self._begin("fade", 1.0)
            self.update()
            return

        # From home it is a change of mode, so fade. From one folder to another
        # it is travel, so slide - out to the left going deeper, in from the
        # left coming back.
        self._pending = (("fade", 1.0) if self.cwd is None
                         else ("slide", -1.0 if back else 1.0))
        self._want = path
        self._wake.set()

    def _begin(self, kind: str, direction: float) -> None:
        self._anim_kind = kind
        self._anim_dir = direction
        self._anim.start(FADE_MS if kind == "fade" else SLIDE_MS)

    def _back(self) -> None:
        if self._history:
            self._go(self._history.pop(), remember=False, back=True)
        else:
            self._go(None, remember=False)

    def _up(self) -> None:
        if not self.cwd:
            return
        parent = os.path.dirname(self.cwd.rstrip("\\/"))
        # A drive root's parent is itself, so "up" from C:\ means going home
        # rather than sitting there doing nothing.
        self._go(parent if parent and parent != self.cwd else None, back=True)

    # ------------------------------------------------------------------ #
    def context_actions(self, menu) -> None:
        from PySide6.QtGui import QAction
        where = self.cwd or "Quick Access"
        note = QAction(os.path.basename(where.rstrip("\\/")) or where, menu)
        note.setEnabled(False)
        menu.addAction(note)

        if self.cwd:
            act = QAction("Open in File Explorer", menu)
            act.triggered.connect(lambda: self._explorer(self.cwd))
            menu.addAction(act)
            home = QAction("Back to Quick Access", menu)
            home.triggered.connect(lambda: self._go(None, remember=False))
            menu.addAction(home)

        refresh = QAction("Refresh", menu)
        refresh.triggered.connect(self._reread)
        menu.addAction(refresh)

        reset = QAction("Reset size", menu)
        reset.triggered.connect(self._reset_size)
        menu.addAction(reset)

    def _reread(self) -> None:
        if self.cwd:
            self._want = self.cwd
        self._wake.set()

    def _reset_size(self) -> None:
        self.config.pop("w", None)
        self.config.pop("h", None)
        self.resize(*pixel_size(self.size_class, self.extra_bottom))
        self.changed.emit()
        self.update()

    def set_size_class(self, size: str) -> None:
        # Picking a preset from the menu is an explicit instruction, so it
        # overrides a hand-dragged size rather than being silently ignored.
        self.config.pop("w", None)
        self.config.pop("h", None)
        super().set_size_class(size)

    # ------------------------------------------------------------------ #
    #  Painting
    # ------------------------------------------------------------------ #
    def paint_content(self, p, r: QRectF) -> None:
        self._rows = []
        self._chrome = []
        tight = self.width() < 300 and self.height() < 260

        y = self._header(p, r, tight)
        body = QRectF(r.left(), y, r.width(), max(0.0, r.bottom() - y))

        # The header does not move. Only the contents transition, which is what
        # makes it read as the folder changing rather than the whole card
        # lurching - and it keeps the back chevron under the pointer, so two
        # levels can be climbed without waiting for an animation.
        moving = self._anim.active and not self._searching
        if moving:
            p.save()
            p.setClipRect(body)          # nothing may spill past the card edge
            t = self._anim.value
            if self._anim_kind == "slide":
                p.translate((1.0 - t) * body.width() * self._anim_dir, 0.0)
                # A touch of fade with the slide. Pure translation reads as a
                # panel being shoved; the two together read as one view
                # replacing another.
                p.setOpacity(min(1.0, 0.35 + t * 0.65))
            else:
                p.setOpacity(t)

        if self._searching:
            self._results(p, body)
        elif self.cwd is not None:
            self._listing(p, body)
        else:
            self._home(p, body, tight)

        if moving:
            p.restore()
        self._grip(p)

    def _header(self, p, r: QRectF, tight: bool) -> float:
        """Where you are, how to get back, and the search field.

        Searching takes the card over completely - the bar grows, everything
        else stops being drawn, and results appear underneath as you type. The
        alternative is a filter box perched above a list that is quietly lying
        about how much is in it, which is the thing every file manager gets
        wrong. Escape puts the browser back.
        """
        pal = self.palette_
        top = r.top()

        if self._searching:
            box = QRectF(r.left(), top, r.width(), 30)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
            p.setBrush(QColor(0, 0, 0, 64))
            p.drawRoundedRect(box, 9, 9)
            self._magnifier(p, QPointF(box.left() + 16, box.center().y()),
                            QColor(pal.text))
            shown = self.query or "Search"
            self.text(p, QRectF(box.left() + 30, box.top(), box.width() - 40,
                                box.height()),
                      self._elide(p, shown, box.width() - 46, 11),
                      11, pal.text if self.query else pal.text_dim)
            if self.query:
                p.setFont(self.font(11))
                caret = box.left() + 30 + p.fontMetrics().horizontalAdvance(
                    self._elide(p, self.query, box.width() - 46, 11)) + 2
                p.setPen(QPen(QColor(pal.text), 1.4))
                p.drawLine(QPointF(caret, box.center().y() - 7),
                           QPointF(caret, box.center().y() + 7))
            self._chrome.append((box, "search"))
            return top + 38

        if self.cwd:
            box = QRectF(r.left(), top - 2, 16, 16)
            self._chevron(p, box.center(), QColor(pal.text))
            self._chrome.append((box.adjusted(-4, -4, 6, 4), "back"))
            name = os.path.basename(self.cwd.rstrip("\\/")) or self.cwd
            self.text(p, QRectF(r.left() + 22, top - 4, r.width() - 26, 18),
                      self._elide(p, name, r.width() - 30, 10.5), 10.5,
                      pal.text, QFont.DemiBold)
        else:
            self.label(p, QRectF(r.left(), top, r.width(), 12), "Files")

        y = top + 20
        if tight:
            return y

        box = QRectF(r.left(), y, r.width(), 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 22 if self.query else 14))
        p.drawRoundedRect(box, 6, 6)
        self._magnifier(p, QPointF(box.left() + 11, box.center().y()),
                        QColor(pal.text_dim))
        self.text(p, QRectF(box.left() + 22, box.top(), box.width() - 28,
                            box.height()),
                  self._elide(p, self.query or "Search", box.width() - 30, 9.5),
                  9.5, pal.text if self.query else pal.text_dim)
        self._chrome.append((box, "search"))
        return y + 26

    # ------------------------------------------------------------------ #
    def _home(self, p, r: QRectF, tight: bool) -> None:
        pal = self.palette_
        q = self.query.lower()
        y = r.top()

        places = [x for x in self.places
                  if not q or q in os.path.basename(x.rstrip("\\/")).lower()]
        # Columns follow the width now that the card is resizable, rather than
        # being pinned to the size class.
        cols = max(2, int(r.width() // 92))
        tile_w = (r.width() - (cols - 1) * 6) / cols
        tile_h = 46.0
        max_rows = 1 if tight else max(1, int((r.height() * 0.45) // (tile_h + 6)))
        shown = places[:cols * max_rows]
        for i, path in enumerate(shown):
            box = QRectF(r.left() + (i % cols) * (tile_w + 6),
                         y + (i // cols) * (tile_h + 6), tile_w, tile_h)
            self._tile(p, box, path)
            self._rows.append((box, path, True))
        if shown:
            y += ((len(shown) + cols - 1) // cols) * (tile_h + 6) + 4

        recent = [x for x in self.recent
                  if not q or q in os.path.basename(x).lower()]
        left = r.bottom() - y
        if not recent:
            if left > 18:
                self.text(p, QRectF(r.left(), y, r.width(), 20),
                          "No matches" if q else "No recent files yet",
                          9.5, pal.text_dim)
            return
        if left > ROW_H + 16:
            self.text(p, QRectF(r.left(), y, r.width(), 14), "Recent", 9,
                      pal.text_dim, QFont.DemiBold)
            y += 16
            self._rowlist(p, QRectF(r.left(), y, r.width(), r.bottom() - y),
                          [(x, False) for x in recent])

    def _results(self, p, r: QRectF) -> None:
        """What search mode shows instead of the browser."""
        pal = self.palette_
        q = self.query.lower()
        where = (os.path.basename(self.cwd.rstrip("\\/")) or self.cwd
                 if self.cwd else "recent files and places")
        if not q:
            self.text(p, QRectF(r.left(), r.top(), r.width(), 20),
                      f"Type to search {where}", 9.5, pal.text_dim)
            return

        rows = self._matches(q)
        if not rows:
            self.text(p, QRectF(r.left(), r.top(), r.width(), 20),
                      f"Nothing in {where} matches", 9.5, pal.text_dim)
            return
        self.text(p, QRectF(r.left(), r.top(), r.width(), 14),
                  f"{len(rows)} match" + ("" if len(rows) == 1 else "es"),
                  9, pal.text_dim, QFont.DemiBold)
        self._rowlist(p, QRectF(r.left(), r.top() + 16, r.width(),
                                r.bottom() - r.top() - 16), rows)

    def _matches(self, q: str):
        """Everything in the current context whose name contains the query."""
        if self.cwd is not None:
            return [(path, is_dir) for path, is_dir in self.entries
                    if q in os.path.basename(path).lower()]
        rows = [(x, True) for x in self.places
                if q in os.path.basename(x.rstrip("\\/")).lower()]
        rows += [(x, False) for x in self.recent
                 if q in os.path.basename(x).lower()]
        return rows

    def _listing(self, p, r: QRectF) -> None:
        pal = self.palette_
        q = self.query.lower()
        rows = [(path, is_dir) for path, is_dir in self.entries
                if not q or q in os.path.basename(path).lower()]

        if self.error:
            self.text(p, QRectF(r.left(), r.top(), r.width(), 20),
                      f"Can't read this folder - {self.error}", 9.5,
                      pal.text_dim)
            return
        if not rows:
            self.text(p, QRectF(r.left(), r.top(), r.width(), 20),
                      "No matches" if q else "Empty folder", 9.5, pal.text_dim)
            return
        self._rowlist(p, r, rows)

    def _rowlist(self, p, r: QRectF, rows) -> None:
        """As many rows as fit, offset by the scroll position."""
        visible = max(0, int(r.height() // ROW_H))
        span = max(0.0, len(rows) - visible)
        self._scroll = min(max(0.0, self._scroll), span)
        start = int(self._scroll)

        for i, (path, is_dir) in enumerate(rows[start:start + visible]):
            box = QRectF(r.left(), r.top() + i * ROW_H, r.width(), ROW_H)
            self._row(p, box, path, is_dir)
            self._rows.append((box, path, is_dir))

        # A hairline scrollbar, only when there is more than fits.
        if span and visible:
            bar_h = max(18.0, r.height() * visible / len(rows))
            top = r.top() + (r.height() - bar_h) * (self._scroll / span)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 46))
            p.drawRoundedRect(QRectF(r.right() - 2.5, top, 2.5, bar_h), 1.2, 1.2)

    # ------------------------------------------------------------------ #
    def _tile(self, p, box: QRectF, path: str) -> None:
        pal = self.palette_
        hot = self._hot == path
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26 if hot else 12))
        p.drawRoundedRect(box, 8, 8)
        self._folder(p, QPointF(box.center().x(), box.top() + 15))
        name = os.path.basename(path.rstrip("\\/")) or path
        self.text(p, QRectF(box.left() + 3, box.bottom() - 18,
                            box.width() - 6, 15),
                  self._elide(p, name, box.width() - 8, 9.0), 9.0, pal.text,
                  align=Qt.AlignHCenter | Qt.AlignVCenter)

    def _row(self, p, box: QRectF, path: str, is_dir: bool) -> None:
        pal = self.palette_
        if self._hot == path:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 20))
            p.drawRoundedRect(box.adjusted(-4, 1, 4, -1), 6, 6)

        c = QPointF(box.left() + 9, box.center().y())
        if is_dir:
            self._folder(p, c, small=True)
        else:
            self._glyph(p, c, kind_of(path))

        right = 0.0
        if self.cwd is None:
            # In the home view a file's folder is the only thing telling two
            # index.html apart, so it earns the right-hand column. Inside a
            # folder every row shares it, and repeating it would be noise.
            parent = os.path.basename(os.path.dirname(path))
            p.setFont(self.font(8.5))
            right = min(p.fontMetrics().horizontalAdvance(parent) + 6,
                        box.width() * 0.36)
            self.text(p, QRectF(box.right() - right, box.top(), right,
                                box.height()),
                      self._elide(p, parent, right, 8.5), 8.5, pal.text_dim,
                      align=Qt.AlignRight | Qt.AlignVCenter)

        avail = box.width() - 22 - right - 6
        self.text(p, QRectF(box.left() + 21, box.top(), avail, box.height()),
                  self._elide(p, os.path.basename(path), avail, 9.5), 9.5,
                  pal.text)

    # -- drawn marks ----------------------------------------------------- #
    def _folder(self, p, c: QPointF, small: bool = False) -> None:
        """White, deliberately.

        The accent tinted every folder the same hue as the progress bars and
        pills elsewhere on the board, which made them read as controls rather
        than as objects.
        """
        w, h = (11.0, 8.6) if small else (13.0, 10.0)
        x, y = c.x() - w / 2, c.y() - h / 2
        path = QPainterPath()
        path.moveTo(x, y + h)
        path.lineTo(x, y + 1.5)
        path.lineTo(x + w * 0.42, y + 1.5)
        path.lineTo(x + w * 0.54, y + 3.4)
        path.lineTo(x + w, y + 3.4)
        path.lineTo(x + w, y + h)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 236))
        p.drawPath(path)

    def _chevron(self, p, c: QPointF, col: QColor) -> None:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(c.x() + 2.4, c.y() - 4.4),
                        QPointF(c.x() - 2.4, c.y()),
                        QPointF(c.x() + 2.4, c.y() + 4.4)])

    def _magnifier(self, p, c: QPointF, col: QColor) -> None:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(col, 1.3, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(QRectF(c.x() - 4.6, c.y() - 4.6, 7.2, 7.2))
        p.drawLine(QPointF(c.x() + 2.4, c.y() + 2.4),
                   QPointF(c.x() + 4.8, c.y() + 4.8))

    def _grip(self, p) -> None:
        """Three ticks in the corner - the only affordance saying "drag me".

        Hidden while positions are locked, because locking is how you say "stop
        letting me move things by accident" and a visible handle that refuses to
        work is worse than no handle.
        """
        if self.locked:
            return
        c = self.card_rect()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 64), 1.2, Qt.SolidLine, Qt.RoundCap))
        for d in (4.0, 8.0, 12.0):
            p.drawLine(QPointF(c.right() - d - 3, c.bottom() - 4),
                       QPointF(c.right() - 4, c.bottom() - d - 3))

    def _glyph(self, p, c: QPointF, kind: str) -> None:
        """One 12px mark per file family.

        Shape carries the meaning rather than colour: these sit at 12px over a
        translucent card and whatever wallpaper is behind it, where a hue alone
        would not survive.
        """
        col = QColor(self.palette_.text_dim)
        p.setPen(QPen(col, 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        x, y = c.x(), c.y()

        if kind == "img":
            p.drawRoundedRect(QRectF(x - 6, y - 5, 12, 10), 2, 2)
            p.setBrush(col)
            p.drawEllipse(QRectF(x - 3.6, y - 3.0, 2.6, 2.6))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline([QPointF(x - 5.4, y + 3.6), QPointF(x - 1.0, y - 0.6),
                            QPointF(x + 5.4, y + 4.2)])
        elif kind == "aud":
            p.drawLine(QPointF(x + 3.6, y - 5), QPointF(x + 3.6, y + 2.4))
            p.drawLine(QPointF(x - 3.4, y - 3.4), QPointF(x + 3.6, y - 5))
            p.setBrush(col)
            p.drawEllipse(QRectF(x + 0.4, y + 1.4, 3.4, 2.8))
            p.setBrush(Qt.NoBrush)
        elif kind == "vid":
            p.drawRoundedRect(QRectF(x - 6, y - 4.6, 12, 9.2), 2, 2)
            tri = QPainterPath()
            tri.moveTo(x - 1.6, y - 2.4)
            tri.lineTo(x + 3.0, y)
            tri.lineTo(x - 1.6, y + 2.4)
            tri.closeSubpath()
            p.setBrush(col)
            p.drawPath(tri)
            p.setBrush(Qt.NoBrush)
        elif kind == "code":
            p.drawPolyline([QPointF(x - 1.6, y - 4.6), QPointF(x - 5.6, y),
                            QPointF(x - 1.6, y + 4.6)])
            p.drawPolyline([QPointF(x + 1.6, y - 4.6), QPointF(x + 5.6, y),
                            QPointF(x + 1.6, y + 4.6)])
        elif kind == "zip":
            p.drawRoundedRect(QRectF(x - 4.6, y - 5, 9.2, 10), 1.8, 1.8)
            for dy in (-2.6, 0.0, 2.6):
                p.drawLine(QPointF(x - 0.6, y + dy), QPointF(x + 0.6, y + dy))
        else:
            page = QPainterPath()
            page.moveTo(x - 4.4, y - 5)
            page.lineTo(x + 1.8, y - 5)
            page.lineTo(x + 4.4, y - 2.2)
            page.lineTo(x + 4.4, y + 5)
            page.lineTo(x - 4.4, y + 5)
            page.closeSubpath()
            p.drawPath(page)
            p.drawLine(QPointF(x + 1.8, y - 5), QPointF(x + 1.8, y - 2.2))
            p.drawLine(QPointF(x + 4.4, y - 2.2), QPointF(x + 1.8, y - 2.2))

    def _elide(self, p, s: str, width: float, size: float) -> str:
        p.setFont(self.font(size))
        fm = p.fontMetrics()
        if fm.horizontalAdvance(s) <= width:
            return s
        # Middle, not right: the extension is what tells you what a file is, and
        # eliding from the right throws it away first.
        return fm.elidedText(s, Qt.ElideMiddle, int(max(10, width)))

    # ------------------------------------------------------------------ #
    #  Interaction
    # ------------------------------------------------------------------ #
    def _in_grip(self, pos) -> bool:
        if self.locked:
            return False
        c = self.card_rect()
        return pos.x() > c.right() - GRIP and pos.y() > c.bottom() - GRIP

    def _hit(self, pos):
        for box, tag in self._chrome:
            if box.contains(pos):
                return None, tag
        for box, path, is_dir in self._rows:
            if box.contains(pos):
                return path, "dir" if is_dir else "file"
        return None, ""

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self._in_grip(ev.position()):
            self._resizing = ev.globalPosition().toPoint()
            self._size0 = (self.width(), self.height())
            ev.accept()
            return

        if ev.button() == Qt.LeftButton:
            self._press_at = ev.globalPosition().toPoint()
            path, tag = self._hit(ev.position())
            if tag:
                # Claim the press so the base class never starts a drag from it.
                # WidgetCard sets _drag_from on *every* left press, so testing
                # that at release time - which is what this did - meant a click
                # on a row never registered at all.
                self._pressed = (path, tag)
                if self.wants_focus:
                    self.raise_()
                    self.activateWindow()
                ev.accept()
                return
        self._pressed = None
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._resizing is not None:
            d = ev.globalPosition().toPoint() - self._resizing
            self.resize(max(MIN_W, self._size0[0] + d.x()),
                        max(MIN_H, self._size0[1] + d.y()))
            self.update()
            ev.accept()
            return
        if self._drag_from is None:
            hot = self._hit(ev.position())[0]
            if hot != self._hot:
                self._hot = hot
                self.update()
            self.setCursor(Qt.SizeFDiagCursor if self._in_grip(ev.position())
                           else (Qt.PointingHandCursor if hot else Qt.ArrowCursor))
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:
        if self._hot is not None:
            self._hot = None
            self.update()
        self.unsetCursor()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._resizing is not None:
            self._resizing = None
            self.config["w"], self.config["h"] = self.width(), self.height()
            self.changed.emit()          # remember the shape across launches
            ev.accept()
            return

        if self._pressed is not None:
            path, tag = self._pressed
            self._pressed = None
            # Only act if the pointer is still on what was pressed, as a button
            # does - dragging off a row should cancel it.
            if self._hit(ev.position()) == (path, tag):
                if tag == "search":
                    # Typing does nothing until the bar is clicked. Filtering on
                    # whatever key happens to land, on a card that sits under
                    # the mouse all day, is how you lose your place in a folder.
                    self._searching = True
                    self.query = ""
                    self._scroll = 0.0
                elif tag == "back":
                    self._back()
                elif tag == "dir":
                    self._go(path)
                elif tag == "file":
                    self._open(path)
            self.update()
            ev.accept()
            return

        # A click on the background - not a drag - leaves search, the way
        # clicking off a Spotlight field dismisses it.
        if (self._searching and ev.button() == Qt.LeftButton
                and self._press_at is not None
                and (ev.globalPosition().toPoint()
                     - self._press_at).manhattanLength() < 5):
            self._exit_search()
            self.update()
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev) -> None:
        self._scroll = max(0.0, self._scroll - ev.angleDelta().y() / 120.0 * 3)
        self.update()
        ev.accept()

    def _exit_search(self) -> None:
        """Put the browser back exactly as it was."""
        self._searching = False
        self.query = ""
        self._scroll = 0.0

    def keyPressEvent(self, ev) -> None:
        key = ev.key()

        if self._searching:
            if key == Qt.Key_Escape:
                self._exit_search()
            elif key == Qt.Key_Backspace:
                self.query = self.query[:-1]
                self._scroll = 0.0
            elif key in (Qt.Key_Return, Qt.Key_Enter):
                if self._rows:
                    _box, path, is_dir = self._rows[0]
                    if is_dir:
                        self._go(path)
                        self._searching = False
                    else:
                        self._open(path)
            elif key == Qt.Key_Down:
                self._scroll += 1
            elif key == Qt.Key_Up:
                self._scroll = max(0.0, self._scroll - 1)
            elif ev.text() and ev.text().isprintable():
                self.query += ev.text()
                self._scroll = 0.0
            else:
                super().keyPressEvent(ev)
                return
            self.update()
            ev.accept()
            return

        # Browsing. No printable key does anything here - see the search branch
        # in mouseReleaseEvent for why.
        if key == Qt.Key_Escape:
            self._go(None, remember=False)
        elif key == Qt.Key_Backspace:
            self._up()
        elif key == Qt.Key_Up and ev.modifiers() & Qt.AltModifier:
            self._up()
        elif key == Qt.Key_Down:
            self._scroll += 1
        elif key == Qt.Key_Up:
            self._scroll = max(0.0, self._scroll - 1)
        else:
            super().keyPressEvent(ev)
            return
        self.update()
        ev.accept()

    # ------------------------------------------------------------------ #
    def _explorer(self, path: str) -> None:
        try:
            subprocess.Popen(["explorer", os.path.normpath(path)])
        except Exception:
            pass

    def _open(self, path: str) -> None:
        """Hand a file to the shell, as double-clicking it in Explorer would.

        Never raises: a file can be deleted between the listing and the click,
        and a widget that dies over that is worse than one that does nothing.
        """
        try:
            os.startfile(path)
        except Exception:
            pass
