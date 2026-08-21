"""
Files - the folders and documents you actually use, on the desktop.

Windows already keeps this list. Quick Access is what File Explorer shows when
it opens, and it is a much better source than the obvious one: ``%APPDATA%\\
Microsoft\\Windows\\Recent`` holds 655 shortcuts on this machine, most of them
protocol activations - ``ms-gamingoverlay``, ``ms-screenclip`` - whose targets
resolve to nothing at all. Quick Access is the same history already de-junked by
the shell, with real paths.

It costs about a second to enumerate through COM, so it is read on its own
thread and cached. That is not a micro-optimisation: a second of blocking on the
GUI thread freezes every card on the board.
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen

from ..core.card import WidgetCard

REFRESH = 90.0           # seconds between shell enumerations
QA_CLSID = "{679F85CB-0220-4080-B29B-5540CC05AAB6}"

# Offered when the shell has nothing to say - a new profile has no Quick Access
# history, and an empty card reads as broken rather than as new.
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
    """Quick Access, split into folders and files. Slow; never on the GUI thread.

    One PowerShell process, not one per item. ``-ExecutionPolicy Bypass`` is
    deliberate - this runs a literal command rather than a file, and a
    restricted policy would otherwise leave the tile silently empty.
    """
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
            creationflags=0x08000000)          # CREATE_NO_WINDOW
    except Exception:
        return [], []

    dirs: List[str] = []
    files: List[str] = []
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if len(line) < 3 or line[1] != "|":
            continue
        tag, path = line[0], line[2:]
        # Quick Access keeps entries for deleted files. Offering one is a dead
        # click, so history that no longer exists is dropped here.
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
    refresh_ms = 0            # nothing to animate; the reader drives repaints
    repaint_on_tick = False

    data_ready = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.places: List[str] = _fallback_places()
        self.recent: List[str] = []
        self._rows: List[Tuple[QRectF, str, bool]] = []   # rect, path, is_dir
        self._hot: Optional[str] = None
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.data_ready.connect(self._on_data)

    # ------------------------------------------------------------------ #
    def on_start(self) -> None:
        self._start_reader()

    def closeEvent(self, ev) -> None:
        self._stop.set()
        super().closeEvent(ev)

    def _start_reader(self) -> None:
        """One long-lived thread, not one per refresh.

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
            self._reader = None       # out of threads; try again on the next tick

    def _loop(self) -> None:
        first = True
        while not self._stop.wait(0.0 if first else REFRESH):
            first = False
            self._refresh_once()

    def _refresh_once(self) -> None:
        dirs, files = _shell_snapshot()
        if dirs or files:
            self.places = dirs or _fallback_places()
            self.recent = files
            self.data_ready.emit()

    def _on_data(self) -> None:
        self.update()

    # ------------------------------------------------------------------ #
    def context_actions(self, menu) -> None:
        from PySide6.QtGui import QAction
        note = QAction(f"{len(self.recent)} recent, {len(self.places)} places",
                       menu)
        note.setEnabled(False)
        menu.addAction(note)

        refresh = QAction("Refresh now", menu)
        refresh.triggered.connect(
            lambda: threading.Thread(target=self._refresh_once,
                                     daemon=True).start())
        menu.addAction(refresh)

        home = QAction("Open File Explorer", menu)
        home.triggered.connect(lambda: self._open(os.path.expanduser("~"), True))
        menu.addAction(home)

    # ------------------------------------------------------------------ #
    #  Painting
    # ------------------------------------------------------------------ #
    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        self._rows = []
        small = self.size_class == "small"
        large = self.size_class == "large"

        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Files")
        y = r.top() + 20

        # --- places, as a grid of folder tiles ---------------------------- #
        # The three sizes answer different questions, rather than being the
        # same card with less of it: small is a launcher, medium is "what was I
        # just working on", large is both. Medium showed one recent file when it
        # carried the grid too, which is the worst of each.
        #
        # Icon above the name, not beside it. Four columns across a 384px card
        # leaves about 54px of text per tile, which rendered "Downloads" as
        # "D...s" - a label that was present and told you nothing.
        cols = 2 if small else 4
        tile_w = (r.width() - (cols - 1) * 6) / cols
        tile_h = 46.0
        medium = self.size_class == "medium"
        shown = [] if medium else self.places[:8 if large else 4]
        for i, path in enumerate(shown):
            box = QRectF(r.left() + (i % cols) * (tile_w + 6),
                         y + (i // cols) * (tile_h + 6), tile_w, tile_h)
            self._tile(p, box, path)
            self._rows.append((box, path, True))
        if shown:
            y += ((len(shown) + cols - 1) // cols) * (tile_h + 6) + 4

        if small:
            return

        # --- recent files -------------------------------------------------- #
        row_h = 22.0
        if self.recent and r.bottom() - y > row_h + 16:
            self.text(p, QRectF(r.left(), y, r.width(), 14), "Recent", 9,
                      pal.text_dim, QFont.DemiBold)
            y += 16
            for path in self.recent[:max(0, int((r.bottom() - y) // row_h))]:
                box = QRectF(r.left(), y, r.width(), row_h)
                self._row(p, box, path)
                self._rows.append((box, path, os.path.isdir(path)))
                y += row_h
        elif not self.recent:
            self.text(p, QRectF(r.left(), y, r.width(), 20),
                      "No recent files yet", 9.5, pal.text_dim)

    # ------------------------------------------------------------------ #
    def _tile(self, p, box: QRectF, path: str) -> None:
        pal = self.palette_
        hot = self._hot == path
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 26 if hot else 12))
        p.drawRoundedRect(box, 8, 8)

        self._folder(p, QPointF(box.center().x(), box.top() + 15),
                     QColor(pal.accent))
        name = os.path.basename(path.rstrip("\\/")) or path
        self.text(p, QRectF(box.left() + 3, box.bottom() - 18,
                            box.width() - 6, 15),
                  self._elide(p, name, box.width() - 8, 9.0), 9.0,
                  pal.text, align=Qt.AlignHCenter | Qt.AlignVCenter)

    def _row(self, p, box: QRectF, path: str) -> None:
        pal = self.palette_
        if self._hot == path:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 20))
            p.drawRoundedRect(box.adjusted(-4, 1, 4, -1), 6, 6)

        self._glyph(p, QPointF(box.left() + 9, box.center().y()), kind_of(path))

        # The folder sits right, dimmer. Two files called index.html are only
        # told apart by where they live, so the location has to be on the row -
        # but it must not compete with the name for attention.
        parent = os.path.basename(os.path.dirname(path))
        p.setFont(self.font(8.5))
        w_parent = min(p.fontMetrics().horizontalAdvance(parent) + 6,
                       box.width() * 0.36)
        self.text(p, QRectF(box.right() - w_parent, box.top(), w_parent,
                            box.height()),
                  self._elide(p, parent, w_parent, 8.5), 8.5, pal.text_dim,
                  align=Qt.AlignRight | Qt.AlignVCenter)

        avail = box.width() - 22 - w_parent - 6
        self.text(p, QRectF(box.left() + 21, box.top(), avail, box.height()),
                  self._elide(p, os.path.basename(path), avail, 9.5),
                  9.5, pal.text)

    # -- drawn marks ----------------------------------------------------- #
    def _folder(self, p, c: QPointF, col: QColor) -> None:
        w, h = 13.0, 10.0
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
        p.setBrush(col)
        p.drawPath(path)

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
    def _hit(self, pos) -> Optional[Tuple[str, bool]]:
        for box, path, is_dir in self._rows:
            if box.contains(pos):
                return path, is_dir
        return None

    def mouseMoveEvent(self, ev) -> None:
        hit = None if self._drag_from is not None else self._hit(ev.position())
        name = hit[0] if hit else None
        if name != self._hot:
            self._hot = name
            self.setCursor(Qt.PointingHandCursor if name else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:
        if self._hot is not None:
            self._hot = None
            self.update()
        self.unsetCursor()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self._drag_from is None:
            hit = self._hit(ev.position())
            if hit:
                self._open(hit[0], hit[1])
                ev.accept()
                return
        super().mouseReleaseEvent(ev)

    def _open(self, path: str, is_dir: bool) -> None:
        """Hand it to the shell, exactly as double-clicking it in Explorer would.

        Never raises: a file can be deleted between the shell listing it and the
        click landing, and a widget that dies over that is worse than one that
        quietly does nothing.
        """
        try:
            if is_dir:
                subprocess.Popen(["explorer", os.path.normpath(path)])
            else:
                os.startfile(path)          # the user's own file, on their click
        except Exception:
            pass
