"""A silver PSP-1000 (psp.svg, traced from the image the user picked): click a button or the stick to pick what it does on the PC; pressed buttons light up."""
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ..controls import BUTTONS

SVG = Path(__file__).with_name("psp.svg")
W, H = 600, 270  # the SVG's units; scaled to the widget
# control -> (x, y, w, h) over the drawing; round ones light up as circles
SPOTS = {"l": (38, 8, 68, 22), "r": (494, 8, 68, 22),
         "up": (60, 81, 28, 30), "down": (60, 135, 28, 30), "left": (32, 109, 30, 28), "right": (86, 109, 30, 28),
         "stick": (55, 184, 38, 38),
         "triangle": (514, 75, 30, 30), "cross": (514, 141, 30, 30), "square": (481, 108, 30, 30),
         "circle": (547, 108, 30, 30), "select": (412, 239, 32, 16), "start": (450, 239, 32, 16)}
ROUND = {"stick", "triangle", "cross", "square", "circle"}
SYMBOLS = {"triangle": "△", "cross": "✕", "square": "□", "circle": "○", "up": "▲", "down": "▼", "left": "◀",
           "right": "▶", "l": "L", "r": "R", "select": "SELECT", "start": "START", "stick": ""}


class PspView(QWidget):
    picked = Signal(str, QPoint)  # a control name from SPOTS, where it was clicked on screen

    def __init__(self, colors: dict):
        super().__init__()
        self.c, self.pressed, self.stick, self.hover = colors, 0, (128, 128), None
        self.svg = QSvgRenderer(str(SVG), self)
        self.setMouseTracking(True)
        self.setMinimumSize(W * 2 // 3, H * 2 // 3)
        self.setCursor(Qt.PointingHandCursor)

    def set_state(self, buttons: int, lx: int, ly: int) -> None:
        if (buttons, (lx, ly)) != (self.pressed, self.stick):
            self.pressed, self.stick = buttons, (lx, ly)
            self.update()

    def _scale(self) -> tuple[float, float, float]:
        s = min(self.width() / W, self.height() / H)
        return s, (self.width() - W * s) / 2, (self.height() - H * s) / 2

    def _spot_at(self, pos) -> str | None:
        s, ox, oy = self._scale()
        p = QPointF((pos.x() - ox) / s, (pos.y() - oy) / s)
        return next((n for n, r in SPOTS.items() if QRectF(*r).adjusted(-4, -4, 4, 4).contains(p)), None)

    def mouseMoveEvent(self, e):
        spot = self._spot_at(e.position())
        if spot != self.hover:
            self.hover = spot
            self.update()

    def mousePressEvent(self, e):
        if spot := self._spot_at(e.position()):
            self.picked.emit(spot, e.globalPosition().toPoint())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s, ox, oy = self._scale()
        p.translate(ox, oy)
        p.scale(s, s)
        self.svg.render(p, QRectF(0, 0, W, H))
        c = {k: QColor(v) for k, v in self.c.items()}
        p.setFont(QFont("Space Mono", 9))
        p.setPen(QColor("#d8d8d8"))
        p.drawText(QRectF(137, 30, 328, 190), Qt.AlignCenter, "CLICK A BUTTON\nTO CHOOSE WHAT IT DOES")
        for name, r in SPOTS.items():
            on, hover = bool(self.pressed & BUTTONS.get(name, 0)), name == self.hover
            if not (on or hover):
                continue
            fill = QColor(c["accent"])
            fill.setAlpha(170)
            p.setPen(QPen(c["accent"] if on else QColor("#ffffff"), 1.5))
            p.setBrush(fill if on else Qt.NoBrush)
            if name in ROUND:
                p.drawEllipse(QRectF(*r))
            else:
                p.drawRoundedRect(QRectF(*r), 5, 5)
        x, y = self.stick  # where the PSP's stick is: a dot on the nub
        if max(abs(x - 128), abs(y - 128)) > 24:
            p.setPen(Qt.NoPen)
            p.setBrush(c["accent"])
            p.drawEllipse(QPointF(74 + (x - 128) / 10, 203 + (y - 128) / 10), 4, 4)
