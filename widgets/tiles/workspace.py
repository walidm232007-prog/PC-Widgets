"""
The Workspace widget: a building site with one bay per coding agent.

Each agent gets a bay - a lit alcove with a workbench, its avatar, and the
structure it is putting up. A bay is warm and lit while that agent is working
and dark and quiet when it isn't, so the widget answers "is anything happening,
and who's doing it" from across the room without reading a word.

State comes from the shared bus (:mod:`widgets.core.agents`); avatars come from
the registry (:mod:`widgets.core.avatars`). Neither knows about the other, which
is what lets a second agent appear by writing one JSON file and registering one
class.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainterPath, QPen, QRadialGradient,
)

from ..core import avatars
from ..core.agents import AgentBus, AgentState
from ..core.card import WidgetCard


def _short(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


class WorkspaceWidget(WidgetCard):
    kind = "workspace"
    title = "Workspace"
    customizable = True
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 90            # animation, not data; the bus poll is throttled

    # The bays only move when an agent is working - hammering, sparks, a lamp
    # fading down after a turn ends. The rest of the time the picture is
    # identical frame to frame, and repainting it eleven times a second was the
    # single most expensive thing on the board: 6% of a core, for a still image.
    repaint_on_tick = False

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.bus = AgentBus()
        self.states: list[AgentState] = self.bus.poll()
        self.phase = 0.0
        self._last_poll = 0.0
        self._sparks: list[list] = []
        self._last_counts: dict[str, int] = {}
        self._signature: tuple = ()

    # ------------------------------------------------------------------ #
    def on_tick(self) -> None:
        now = time.monotonic()
        self.phase += self.refresh_ms / 1000.0

        # The bus is files on disk; there is no need to stat it 11 times a
        # second just because the animation runs at that rate.
        if now - self._last_poll > 0.5:
            self._last_poll = now
            self.states = self.bus.poll()
            for st in self.states:
                seen = self._last_counts.get(st.agent)
                if seen is not None and st.count > seen and st.active:
                    self._emit_sparks(st.agent)
                self._last_counts[st.agent] = st.count

        for s in self._sparks:
            s[0] += s[2] * 0.09
            s[1] += s[3] * 0.09
            s[3] += 1.6
            s[4] -= 0.09
        self._sparks = [s for s in self._sparks if s[4] > 0]

        # Repaint only when the picture would differ: someone is working, sparks
        # are still in the air, or a bay is inside its post-turn fade. Otherwise
        # follow the bus - a status change is the only thing that can alter a
        # sleeping workspace.
        moving = (self._sparks
                  or any(s.active or s.just_finished for s in self.states))
        signature = tuple((s.agent, s.active, s.event, s.tool, s.count,
                           s.seen, s.just_finished) for s in self.states)
        if moving or signature != self._signature:
            self._signature = signature
            self.update()

    def _emit_sparks(self, agent: str) -> None:
        """A visible tick each time an agent completes a tool call."""
        import random
        for _ in range(7):
            angle = random.uniform(-2.7, -0.4)
            speed = random.uniform(2.0, 6.5)
            self._sparks.append([
                0.0, 0.0,
                math.cos(angle) * speed, math.sin(angle) * speed,
                random.uniform(0.35, 0.7), agent,
            ])

    # ------------------------------------------------------------------ #
    def paint_content(self, p, r: QRectF) -> None:
        pal = self.palette_
        states = self.states or self.bus.poll()

        active = [s for s in states if s.active]
        if self.size_class == "small":
            self._paint_small(p, r, states, active)
            return

        header = f"Workspace · {len(active)} working" if active else "Workspace · idle"
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), header)

        top = r.top() + 22
        bays = QRectF(r.left(), top, r.width(), r.bottom() - top)
        gap = 10.0
        n = max(1, len(states))
        bw = (bays.width() - gap * (n - 1)) / n

        for i, st in enumerate(states):
            self._paint_bay(p, QRectF(bays.left() + i * (bw + gap), bays.top(),
                                      bw, bays.height()), st)

    # ------------------------------------------------------------------ #
    def _paint_small(self, p, r: QRectF, states, active) -> None:
        """Small: whoever is working, big. Nobody working, the site is shut."""
        pal = self.palette_
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Workspace")

        who = active[0] if active else None
        stage = QRectF(r.left(), r.top() + 18, r.width(), r.height() - 40)
        self._paint_floor(p, stage, lit=bool(who),
                          tint=avatars.for_agent(who.agent).tint if who else None)

        av = avatars.for_agent(who.agent if who else "codex")
        unit = min(stage.height() / 3.1, stage.width() / 2.6)
        av.paint(p, QPointF(stage.center().x(), stage.bottom() - 4), unit,
                 self.phase, working=bool(who), dim=1.0 if who else 0.5)

        if who:
            self._paint_sparks(p, QPointF(stage.center().x() + unit * 0.75,
                                          stage.bottom() - unit * 1.5), unit)
            line = who.display + (f" · {who.tool}" if who.tool else "")
            colour = pal.text
        else:
            line = "no agents working"
            colour = pal.text_dim
        self.text(p, QRectF(r.left(), r.bottom() - 20, r.width(), 20),
                  line, 10, colour, QFont.DemiBold,
                  align=Qt.AlignHCenter | Qt.AlignVCenter)

    # ------------------------------------------------------------------ #
    def _paint_bay(self, p, bay: QRectF, st: AgentState) -> None:
        pal = self.palette_
        av = avatars.for_agent(st.agent)
        lit = st.active
        dim = 1.0 if lit else (0.72 if st.just_finished else 0.45)
        large = self.size_class == "large"

        # --- alcove ---------------------------------------------------- #
        shell = QPainterPath()
        shell.addRoundedRect(bay, 14, 14)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 12 if lit else 6))
        p.drawPath(shell)

        p.save()
        p.setClipPath(shell)

        if lit:
            # A warm pool of light in the agent's own colour: the fastest read
            # of "this bay is the one that's busy".
            glow = QRadialGradient(bay.center().x(), bay.bottom() - 6,
                                   bay.width() * 0.85)
            c = QColor(av.tint)
            glow.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 92))
            glow.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
            p.setBrush(QBrush(glow))
            p.drawRect(bay)

        floor_y = bay.bottom() - (26 if large else 20)
        self._paint_floor(p, QRectF(bay.left(), bay.top(),
                                    bay.width(), floor_y - bay.top()),
                          lit=lit, tint=av.tint if lit else None)

        # A hanging work lamp. It fills the headroom a tall bay would otherwise
        # waste, and its cone is the clearest possible statement of which bay
        # the work is happening in.
        if large:
            self._paint_lamp(p, bay, floor_y, av, lit)

        # --- what they've built this turn ------------------------------ #
        self._paint_stack(p, bay, floor_y, st, av, dim)

        # --- the figure ------------------------------------------------- #
        head_room = floor_y - bay.top() - (16 if large else 12)
        unit = max(6.0, min(head_room / 3.0, bay.width() / (2.5 if large else 3.0)))
        feet = QPointF(bay.center().x() - bay.width() * 0.14, floor_y)
        av.paint(p, feet, unit, self.phase, working=lit, dim=dim)

        if lit:
            self._paint_sparks(p, QPointF(feet.x() + unit * 0.78,
                                          feet.y() - unit * 1.45), unit)

        p.restore()

        # --- caption ---------------------------------------------------- #
        name_y = bay.bottom() - (24 if large else 19)
        self.text(p, QRectF(bay.left() + 8, name_y, bay.width() - 16, 16),
                  st.display, 10 if large else 9, pal.text if lit else pal.text_dim,
                  QFont.DemiBold)

        detail = self._detail(st, av)
        self.text(p, QRectF(bay.left() + 8, name_y + (12 if large else 9),
                            bay.width() - 16, 14),
                  detail, 8 if large else 7.5,
                  pal.text_dim if lit else pal.text_faint)

        # A live dot, so "working" is legible even at a glance.
        if lit:
            pulse = 0.55 + 0.45 * math.sin(self.phase * 3.2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(av.tint))
            p.drawEllipse(QRectF(bay.right() - 15, bay.top() + 9, 6, 6))
            halo = QColor(av.tint)
            halo.setAlphaF(0.32 * pulse)
            p.setBrush(halo)
            p.drawEllipse(QRectF(bay.right() - 19, bay.top() + 5, 14, 14))

    def _detail(self, st: AgentState, av) -> str:
        if st.active:
            bits = [st.tool or st.event or "working"]
            if st.count:
                bits.append(f"{st.count} step{'s' if st.count != 1 else ''}")
            if st.elapsed:
                bits.append(_short(st.elapsed))
            return "  ·  ".join(bits)
        if st.just_finished:
            return f"done · {st.count} steps"
        if not st.seen:
            return "not connected"
        return "idle"

    # ------------------------------------------------------------------ #
    def _paint_floor(self, p, area: QRectF, lit: bool, tint=None) -> None:
        """A blueprint grid fading upward - the 'site' the bays stand on."""
        pal = self.palette_
        base = QColor(tint) if (lit and tint) else QColor(pal.text)
        step = max(8.0, area.height() / 5.0)

        p.setBrush(Qt.NoBrush)
        y = area.bottom()
        i = 0
        while y > area.top():
            fade = max(0.0, 1.0 - (area.bottom() - y) / max(1.0, area.height()))
            a = int((34 if lit else 16) * fade)
            if a > 1:
                p.setPen(QPen(QColor(base.red(), base.green(), base.blue(), a), 1.0))
                p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            y -= step
            i += 1

        p.setPen(QPen(QColor(base.red(), base.green(), base.blue(),
                             90 if lit else 40), 1.4))
        p.drawLine(QPointF(area.left(), area.bottom()),
                   QPointF(area.right(), area.bottom()))

    def _paint_lamp(self, p, bay: QRectF, floor_y: float, av, lit: bool) -> None:
        pal = self.palette_
        cx = bay.center().x()
        shade_y = bay.top() + 26.0

        # Flex and shade
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(pal.text_faint), 1.3))
        p.drawLine(QPointF(cx, bay.top()), QPointF(cx, shade_y - 5))

        shade = QPainterPath()
        shade.moveTo(cx - 13, shade_y + 6)
        shade.lineTo(cx - 5, shade_y - 5)
        shade.lineTo(cx + 5, shade_y - 5)
        shade.lineTo(cx + 13, shade_y + 6)
        shade.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.text_dim if lit else pal.text_faint))
        p.drawPath(shade)

        if not lit:
            return

        # Bulb and cone
        c = QColor(av.tint)
        p.setBrush(QColor(255, 246, 232, 230))
        p.drawEllipse(QRectF(cx - 3.5, shade_y + 4, 7, 7))

        cone = QPainterPath()
        cone.moveTo(cx - 11, shade_y + 7)
        cone.lineTo(cx - bay.width() * 0.40, floor_y)
        cone.lineTo(cx + bay.width() * 0.40, floor_y)
        cone.lineTo(cx + 11, shade_y + 7)
        cone.closeSubpath()
        # Warm white, not the agent tint: a saturated cone over an already-warm
        # bay reads as a shadow rather than as light.
        grad = QLinearGradient(cx, shade_y + 7, cx, floor_y)
        grad.setColorAt(0.0, QColor(255, 243, 224, 74))
        grad.setColorAt(0.55, QColor(255, 238, 214, 30))
        grad.setColorAt(1.0, QColor(255, 236, 210, 0))
        p.setBrush(QBrush(grad))
        p.drawPath(cone)

    def _paint_stack(self, p, bay: QRectF, floor_y: float,
                     st: AgentState, av, dim: float) -> None:
        """Blocks stacked on the bench, one per tool call this turn."""
        if st.count <= 0:
            return
        large = self.size_class == "large"
        bw = 9.0 if large else 7.0
        bh = 5.5 if large else 4.5
        per_row = 4
        x0 = bay.right() - 14 - per_row * bw

        p.setPen(Qt.NoPen)
        shown = min(st.count, per_row * (5 if large else 3))
        for i in range(shown):
            row, col = divmod(i, per_row)
            jog = (bw / 2.0) if row % 2 else 0.0
            x = x0 + col * bw + jog * 0.4
            y = floor_y - 3 - (row + 1) * bh
            c = QColor(av.tint)
            c.setAlphaF((0.85 if i % 2 else 0.62) * dim)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, y, bw - 1.4, bh - 1.4), 1.6, 1.6)

        if st.count > shown:
            self.text(p, QRectF(x0 - 26, floor_y - 16, 24, 14),
                      f"+{st.count - shown}", 7.5, self.palette_.text_faint,
                      align=Qt.AlignRight | Qt.AlignVCenter)

    def _paint_sparks(self, p, origin: QPointF, unit: float) -> None:
        p.setPen(Qt.NoPen)
        for x, y, _vx, _vy, life, agent in self._sparks:
            c = QColor(avatars.for_agent(agent).accent)
            c.setAlphaF(max(0.0, min(1.0, life)))
            p.setBrush(c)
            rad = max(0.7, unit * 0.055 * life)
            p.drawEllipse(QPointF(origin.x() + x, origin.y() + y), rad, rad)
