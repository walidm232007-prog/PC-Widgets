"""
The Comms widget: what the agents are saying to each other.

The Workspace widget next door shows *who is working*. This shows *what they
said* - the running conversation between the coding agents in this folder, newest
last, the way a chat reads.

Everything comes from :mod:`agents.agentmail`, which is a directory of small JSON
files. Nothing is shared-writable, so this widget only ever reads; it cannot
disturb a conversation by watching it.

Each agent's colour is taken from its avatar in the registry, so a message here
and a lit bay over there are recognisably the same agent without either widget
knowing about the other.
"""

from __future__ import annotations

import os
import sys
import time
from typing import List

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont, QPainterPath, QPen

from ..core import avatars
from ..core.card import WidgetCard

# The mailbox lives with the other agent-facing code, outside the widgets
# package - it has to be runnable as a bare script by an agent that knows
# nothing about this app.
_AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agents")
if _AGENTS_DIR not in sys.path:
    sys.path.append(_AGENTS_DIR)

try:
    import agentmail as mb
except Exception:                                   # pragma: no cover
    mb = None

# A colour per kind, so the log is skimmable without reading it.
KIND_COLOUR = {
    "ask":     "#ffd60a",
    "answer":  "#32d74b",
    "claim":   "#ff9f0a",
    "release": "#8e8e93",
    "blocked": "#ff453a",
    "done":    "#30d158",
    "note":    "#64d2ff",
}


class CommsWidget(WidgetCard):
    kind = "comms"
    title = "Comms"
    supported_sizes = ("medium", "large")
    refresh_ms = 2000          # a directory listing; there is no hurry

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.messages: List = []
        self.claims: dict = {}
        self._sig = None

    # ------------------------------------------------------------------ #
    def on_tick(self) -> None:
        if mb is None:
            return
        try:
            msgs = mb.load(limit=24)
            claims = mb.claims()
        except Exception:
            return
        # Repaint only when the conversation actually moved.
        sig = (tuple(m.id for m in msgs), tuple(sorted(claims)))
        if sig != self._sig:
            self._sig = sig
            self.messages = msgs
            self.claims = claims
            self.update()

    def context_actions(self, menu) -> None:
        if mb is None:
            return
        where = QAction(f"Mailbox: {mb.mail_dir()}", menu)
        where.setEnabled(False)
        menu.addAction(where)
        opn = QAction("Open mailbox folder", menu)
        opn.triggered.connect(self._open)
        menu.addAction(opn)

    def _open(self) -> None:
        try:
            os.startfile(mb.mail_dir())
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def _tint(self, agent: str) -> str:
        try:
            return avatars.for_agent(agent).tint
        except Exception:
            return self.palette_.text_dim

    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        large = self.size_class == "large"

        self.label(p, QRectF(r.left(), r.top(), r.width(), 14), "Comms")

        if mb is None:
            self.text(p, r, "mailbox unavailable", 10, pal.text_dim,
                      align=Qt.AlignCenter)
            return

        # Claim strip: who is holding what, right now. Small, but it is the one
        # thing worth knowing before touching a file.
        head = QRectF(r.left(), r.top() + 18, r.width(), 16)
        if self.claims:
            holders = {}
            for path, (who, _ts, _why) in self.claims.items():
                holders.setdefault(who, []).append(os.path.basename(path))
            bits = [f"{who} holds {', '.join(sorted(f)[:2])}"
                    + (f" +{len(f) - 2}" if len(f) > 2 else "")
                    for who, f in sorted(holders.items())]
            self.text(p, head, self._elide(p, "  ·  ".join(bits), r.width(), 9),
                      9, pal.warn)
        elif self.messages:
            self.text(p, head, "nothing claimed", 9, pal.text_faint)

        body = QRectF(r.left(), r.top() + 38, r.width(), r.bottom() - r.top() - 38)

        if not self.messages:
            self.text(p, body,
                      "No messages yet.\n"
                      "Agents talk here with agents/msg.py",
                      10, pal.text_dim, align=Qt.AlignCenter)
            return

        # Newest last and clipped to the bottom, like a chat: the most recent
        # exchange is the one worth seeing, so it gets the guaranteed space.
        rows = []
        for m in reversed(self.messages):
            h = self._height(p, m, body.width(), large)
            if sum(x[1] for x in rows) + h > body.height():
                break
            rows.append((m, h))
        rows.reverse()

        y = body.bottom() - sum(h for _m, h in rows)
        for m, h in rows:
            self._row(p, QRectF(body.left(), y, body.width(), h), m, large)
            y += h

    # ------------------------------------------------------------------ #
    def _height(self, p, m, width: float, large: bool) -> float:
        h = 17.0                                   # sender line
        if m.subject:
            h += 14.0
        if large and m.body:
            h += 12.0 * min(2, len(m.body.splitlines()))
        return h + 7.0

    def _row(self, p, rect: QRectF, m, large: bool) -> None:
        pal = self.palette_
        tint = QColor(self._tint(m.sender))
        kind = QColor(KIND_COLOUR.get(m.kind, "#64d2ff"))

        # A coloured spine per sender, so who-said-what is legible at a glance
        # even when the text is too small to read.
        p.setPen(Qt.NoPen)
        p.setBrush(tint)
        p.drawRoundedRect(QRectF(rect.left(), rect.top() + 2, 3.0,
                                 rect.height() - 8), 1.5, 1.5)

        left = rect.left() + 10
        w = rect.width() - 10

        # sender + kind pill + time
        self.text(p, QRectF(left, rect.top(), w * 0.5, 15),
                  m.sender, 9.5, tint.name(), QFont.DemiBold)

        pill_w = self._pill(p, QRectF(left + 52, rect.top() + 2, 60, 12),
                            m.kind, kind)
        self.text(p, QRectF(left, rect.top(), w, 15), m.when(), 8.5,
                  pal.text_faint, align=Qt.AlignRight | Qt.AlignVCenter)

        y = rect.top() + 15
        if m.subject:
            self.text(p, QRectF(left, y, w, 14),
                      self._elide(p, m.subject, w, 10), 10, pal.text)
            y += 14
        if large and m.body:
            for line in m.body.splitlines()[:2]:
                self.text(p, QRectF(left, y, w, 12),
                          self._elide(p, line, w, 8.5), 8.5, pal.text_dim)
                y += 12

    def _pill(self, p, rect: QRectF, text: str, colour: QColor) -> float:
        p.setFont(self.font(7.5, QFont.DemiBold))
        w = p.fontMetrics().horizontalAdvance(text) + 12
        box = QRectF(rect.left(), rect.top(), w, rect.height())
        fill = QColor(colour)
        fill.setAlpha(46)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(box, box.height() / 2, box.height() / 2)
        p.setPen(colour)
        p.drawText(box, Qt.AlignCenter, text)
        return w

    def _elide(self, p, text: str, width: float, size: float) -> str:
        p.setFont(self.font(size))
        fm = p.fontMetrics()
        return (text if fm.horizontalAdvance(text) <= width
                else fm.elidedText(text, Qt.ElideRight, int(max(10, width))))
