"""
avatars.py
==========
The little figures that stand in the workspace bays.

Each avatar is drawn procedurally - no image assets - so it can breathe, blink,
lean into its work and be tinted by its own brand colour at any size.

**Adding one (Codex, read this):** subclass :class:`Avatar`, implement the three
body parts, and register it under your agent id. The rig handles posture,
breathing, the tool swing and the idle slump, so an avatar only has to describe
what it *looks* like, not how it moves:

    @register
    class MyAvatar(Avatar):
        agent = "myagent"
        tint = "#10a37f"
        def draw_body(self, p, u): ...
        def draw_head(self, p, u): ...
        def draw_emblem(self, p, u): ...

Everything is drawn in a unit space: the origin is between the figure's feet,
``-1 <= x <= 1`` across its shoulders, and ``y`` runs from ``0`` at the floor to
``-2.6`` at the top of the head. ``u`` is the pixels-per-unit scale, so a stroke
of ``0.08 * u`` stays proportional at every widget size.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Type

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen,
)

REGISTRY: Dict[str, Type["Avatar"]] = {}


def register(cls: Type["Avatar"]) -> Type["Avatar"]:
    REGISTRY[cls.agent] = cls
    return cls


def for_agent(agent: str) -> "Avatar":
    cls = REGISTRY.get(agent.lower(), PlaceholderAvatar)
    return cls()


# --------------------------------------------------------------------------- #
class Avatar:
    """Base rig: posture, breathing, tool swing. Subclasses only draw."""

    agent = "generic"
    tint = "#8a93a5"
    accent = "#ffffff"
    HEAD_TOP = -2.55

    # Shared arm rig. The tool is drawn in the *same* frame as the raised arm
    # and offset to its tip, so it is attached by construction - deriving a
    # hand position separately is how you end up with a hammer floating beside
    # a hand instead of in it.
    ARM_PIVOT = (0.50, -1.30)
    ARM_LEN = 0.54
    ARM_REST = 26.0
    ARM_RAISE = -74.0
    ARM_SWING = 34.0

    def arm_angle(self, side: int) -> float:
        """Rotation for one arm, in degrees, including the working swing."""
        base = side * self.ARM_REST
        if self.working and side > 0:
            return base + self.ARM_RAISE + self.ARM_SWING * self.swing()
        return base

    # ------------------------------------------------------------------ #
    def paint(self, p: QPainter, feet: QPointF, unit: float,
              phase: float, working: bool, dim: float = 1.0) -> None:
        """Draw the figure with its feet at ``feet``, ``unit`` px per unit."""
        p.save()
        p.translate(feet)
        p.scale(unit, unit)

        # Breathing, and a forward lean while working. Idle figures settle and
        # slump a little, which is most of what reads as "not doing anything".
        breathe = math.sin(phase * 1.7) * (0.018 if working else 0.010)
        lean = 0.10 if working else -0.02
        slump = 0.0 if working else 0.06

        p.translate(0.0, breathe + slump)
        p.shear(-lean * 0.30, 0.0)

        self.opacity = dim
        self.working = working
        self.phase = phase

        self.draw_shadow(p)
        self.draw_body(p, 1.0)
        self.draw_head(p, 1.0)
        self.draw_emblem(p, 1.0)

        p.restore()

    # -- helpers for subclasses ------------------------------------------ #
    def col(self, hex_or_col, alpha: float = 1.0) -> QColor:
        c = QColor(hex_or_col)
        c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha * self.opacity)))
        return c

    def swing(self) -> float:
        """0 at the top of the stroke, 1 at the strike."""
        return (1.0 - math.cos(self.phase * 5.0)) / 2.0

    # -- parts ------------------------------------------------------------ #
    def draw_shadow(self, p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(self.col("#000000", 0.30))
        p.drawEllipse(QRectF(-0.62, -0.10, 1.24, 0.20))

    def draw_body(self, p: QPainter, u: float) -> None:
        raise NotImplementedError

    def draw_head(self, p: QPainter, u: float) -> None:
        raise NotImplementedError

    def draw_emblem(self, p: QPainter, u: float) -> None:
        """The mark on the chest. Optional."""

    def draw_arm(self, p: QPainter, side: int, colour) -> None:
        """One arm, plus the tool if this is the working hand."""
        p.save()
        p.translate(*[self.ARM_PIVOT[0] * side, self.ARM_PIVOT[1]])
        p.rotate(self.arm_angle(side))

        p.setPen(QPen(self.col("#20242c", 0.55), 0.06,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(self.col(colour))
        p.drawRoundedRect(QRectF(-0.11, -0.09, 0.22, self.ARM_LEN + 0.09),
                          0.11, 0.11)

        if self.working and side > 0:
            # Still in the arm's frame: translate to the hand and keep going.
            p.translate(0.0, self.ARM_LEN)
            self.draw_tool(p, 1.0)
        p.restore()

    def draw_tool(self, p: QPainter, u: float) -> None:
        """Drawn at the hand, pointing along the arm. Default is a mallet."""
        p.setPen(QPen(self.col("#20242c", 0.9), 0.06,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(self.col("#a9713f"))
        p.drawRoundedRect(QRectF(-0.045, -0.06, 0.09, 0.46), 0.045, 0.045)
        p.setBrush(self.col("#c8d2de"))
        p.drawRoundedRect(QRectF(-0.15, 0.34, 0.30, 0.17), 0.05, 0.05)


# --------------------------------------------------------------------------- #
@register
class ClaudeAvatar(Avatar):
    """Warm clay figure with a soft monolith head and a spark on its chest.

    The spark is the point of it: it turns slowly and brightens while working,
    so from across the room you can tell the bay is live without reading a word.
    """

    agent = "claude"
    tint = "#d97757"          # Anthropic clay
    accent = "#ffd9c7"

    def draw_body(self, p: QPainter, u: float) -> None:
        grad = QLinearGradient(0.0, -1.9, 0.0, 0.0)
        grad.setColorAt(0.0, self.col(QColor(self.tint).lighter(118)))
        grad.setColorAt(1.0, self.col(QColor(self.tint).darker(112)))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(self.col("#5c2c1c", 0.85), 0.07,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

        torso = QPainterPath()
        torso.moveTo(-0.50, 0.0)
        torso.cubicTo(-0.60, -0.60, -0.56, -1.35, -0.44, -1.62)
        torso.cubicTo(-0.20, -1.80, 0.20, -1.80, 0.44, -1.62)
        torso.cubicTo(0.56, -1.35, 0.60, -0.60, 0.50, 0.0)
        torso.closeSubpath()
        p.drawPath(torso)

        # Stubby arms, one raised with the tool in it while working.
        for side in (-1, 1):
            self.draw_arm(p, side, QColor(self.tint).darker(106))

        # Feet
        p.setBrush(self.col("#5c2c1c", 0.9))
        p.setPen(Qt.NoPen)
        for side in (-1, 1):
            p.drawRoundedRect(QRectF(side * 0.30 - 0.20, -0.16, 0.40, 0.18),
                              0.08, 0.08)

    def draw_head(self, p: QPainter, u: float) -> None:
        grad = QLinearGradient(0.0, -2.55, 0.0, -1.70)
        grad.setColorAt(0.0, self.col(QColor(self.tint).lighter(126)))
        grad.setColorAt(1.0, self.col(self.tint))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(self.col("#5c2c1c", 0.85), 0.07,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawRoundedRect(QRectF(-0.52, -2.52, 1.04, 0.86), 0.34, 0.34)

        # Eyes: open and forward when working, narrowed to rests when idle.
        p.setPen(Qt.NoPen)
        p.setBrush(self.col("#3a1a10"))
        for side in (-1, 1):
            x = side * 0.20
            if self.working:
                p.drawEllipse(QRectF(x - 0.085, -2.24, 0.17, 0.19))
            else:
                p.drawRoundedRect(QRectF(x - 0.10, -2.14, 0.20, 0.055),
                                  0.03, 0.03)

    def draw_emblem(self, p: QPainter, u: float) -> None:
        """Claude's spark: tapered rays around a bright centre."""
        p.save()
        p.translate(0.0, -1.13)
        glow = 1.0 if self.working else 0.45
        p.rotate((self.phase * 22.0) % 360.0 if self.working else 12.0)

        rays = 6
        p.setPen(Qt.NoPen)
        for i in range(rays):
            p.save()
            p.rotate(i * (360.0 / rays))
            ray = QPainterPath()
            ray.moveTo(-0.036, 0.0)
            ray.quadTo(-0.014, -0.13, 0.0, -0.23)
            ray.quadTo(0.014, -0.13, 0.036, 0.0)
            ray.closeSubpath()
            p.setBrush(self.col(self.accent, glow * 0.85))
            p.drawPath(ray)
            p.restore()

        p.setBrush(self.col("#fff4ec", glow * 0.9))
        p.drawEllipse(QRectF(-0.048, -0.048, 0.096, 0.096))
        p.restore()


# --------------------------------------------------------------------------- #
@register
class CodexAvatar(Avatar):
    """Placeholder so the bay is never empty.

    Codex: replace this class with your own. Keep ``agent = "codex"`` and the
    registry picks it up with no other changes. The rig gives you posture,
    breathing, the tool swing and the idle slump for free - see the module
    docstring for the unit space.
    """

    agent = "codex"
    tint = "#5667e8"          # indigo — distinct from Claude's warm clay
    accent = "#a7fff2"        # sea-glass light in the visor and core
    placeholder = False

    def draw_body(self, p: QPainter, u: float) -> None:
        grad = QLinearGradient(0.0, -1.80, 0.0, 0.0)
        grad.setColorAt(0.0, self.col(QColor(self.tint).lighter(124)))
        grad.setColorAt(1.0, self.col(QColor(self.tint).darker(126)))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(self.col("#18234a", 0.90), 0.07,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

        # An old field coat, cut into a pointed tabard rather than a block.
        coat = QPainterPath()
        coat.moveTo(-0.52, 0.0)
        coat.lineTo(-0.46, -1.48)
        coat.quadTo(-0.33, -1.73, 0.0, -1.77)
        coat.quadTo(0.33, -1.73, 0.46, -1.48)
        coat.lineTo(0.52, 0.0)
        coat.lineTo(0.18, -0.10)
        coat.lineTo(0.0, 0.10)
        coat.lineTo(-0.18, -0.10)
        coat.closeSubpath()
        p.drawPath(coat)

        p.setPen(QPen(self.col(self.accent, 0.62), 0.045,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline([QPointF(-0.31, -1.58), QPointF(0.0, -1.38),
                        QPointF(0.31, -1.58)])

        for side in (-1, 1):
            self.draw_arm(p, side, QColor(self.tint).darker(112))

        p.setPen(Qt.NoPen)
        p.setBrush(self.col("#18234a", 0.94))
        for side in (-1, 1):
            p.drawRoundedRect(QRectF(side * 0.30 - 0.20, -0.15, 0.40, 0.17),
                              0.08, 0.08)

    def draw_head(self, p: QPainter, u: float) -> None:
        grad = QLinearGradient(0.0, -2.56, 0.0, -1.66)
        grad.setColorAt(0.0, self.col(QColor(self.tint).lighter(140)))
        grad.setColorAt(1.0, self.col(QColor(self.tint).darker(106)))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(self.col("#18234a", 0.92), 0.07,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawRoundedRect(QRectF(-0.54, -2.54, 1.08, 0.89), 0.30, 0.30)

        visor = QPainterPath()
        visor.moveTo(-0.38, -2.24)
        visor.quadTo(0.0, -2.38, 0.38, -2.24)
        visor.lineTo(0.31, -2.04)
        visor.quadTo(0.0, -1.95, -0.31, -2.04)
        visor.closeSubpath()
        p.setPen(QPen(self.col("#172651", 0.80), 0.045,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(self.col("#173a68", 0.96))
        p.drawPath(visor)

        p.setPen(Qt.NoPen)
        p.setBrush(self.col(self.accent, 1.0 if self.working else 0.62))
        for side in (-1, 1):
            x = side * 0.17
            if self.working:
                p.drawEllipse(QRectF(x - 0.065, -2.20, 0.13, 0.10))
            else:
                p.drawRoundedRect(QRectF(x - 0.075, -2.15, 0.15, 0.040),
                                  0.02, 0.02)

    def draw_emblem(self, p: QPainter, u: float) -> None:
        """A dim ``< >`` — a stand-in mark, waiting for a real one."""
        p.save()
        p.translate(0.0, -1.08)
        glow = (0.80 + 0.20 * math.sin(self.phase * 4.0)
                if self.working else 0.34)
        nodes = [
            QPointF(0.0, -0.22), QPointF(0.19, -0.11),
            QPointF(0.19, 0.11), QPointF(0.0, 0.22),
            QPointF(-0.19, 0.11), QPointF(-0.19, -0.11),
        ]
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self.col(self.accent, glow * 0.72), 0.050,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(nodes + [nodes[0]])
        p.drawLine(nodes[0], nodes[3])
        p.drawLine(nodes[1], nodes[4])
        p.drawLine(nodes[2], nodes[5])
        p.setPen(Qt.NoPen)
        p.setBrush(self.col(self.accent, glow))
        for node in nodes:
            p.drawEllipse(QRectF(node.x() - 0.043, node.y() - 0.043,
                                 0.086, 0.086))
        p.setBrush(self.col("#f2fffd", glow))
        p.drawEllipse(QRectF(-0.055, -0.055, 0.110, 0.110))
        p.restore()

    def draw_tool(self, p: QPainter, u: float) -> None:
        """A small tuning fork for sending a clean signal into the work."""
        p.setPen(QPen(self.col("#172651", 0.90), 0.055,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(self.col("#6c7af0"))
        p.drawRoundedRect(QRectF(-0.048, -0.07, 0.096, 0.38), 0.045, 0.045)
        p.setPen(QPen(self.col(self.accent, 0.96), 0.060,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(0.0, 0.27), QPointF(0.0, 0.47))
        p.drawLine(QPointF(0.0, 0.47), QPointF(-0.13, 0.47))
        p.drawLine(QPointF(-0.13, 0.47), QPointF(-0.13, 0.62))
        p.drawLine(QPointF(0.0, 0.47), QPointF(0.13, 0.47))
        p.drawLine(QPointF(0.13, 0.47), QPointF(0.13, 0.62))


class PlaceholderAvatar(CodexAvatar):
    """Used for any agent that reports in without a registered avatar."""

    agent = "generic"
    tint = "#6f7787"
