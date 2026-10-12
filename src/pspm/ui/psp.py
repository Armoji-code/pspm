"""A PSP drawn in outline: click a button or the stick to pick what it does on the PC; pressed buttons light up."""
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..controls import BUTTONS

W, H = 600, 250  # drawing units; scaled to the widget
# control -> (x, y, w, h); round ones are drawn as circles
SPOTS = {"l": (44, 8, 110, 20), "r": (446, 8, 110, 20),
         "up": (84, 62, 26, 26), "down": (84, 118, 26, 26), "left": (56, 90, 26, 26), "right": (112, 90, 26, 26),
         "stick": (76, 158, 42, 42),
         "triangle": (490, 62, 26, 26), "cross": (490, 118, 26, 26), "square": (462, 90, 26, 26),
         "circle": (518, 90, 26, 26), "select": (436, 186, 50, 16), "start": (500, 186, 50, 16)}
ROUND = {"stick", "triangle", "cross", "square", "circle"}
SYMBOLS = {"triangle": "△", "cross": "✕", "square": "□", "circle": "○", "up": "▲", "down": "▼", "left": "◀",
           "right": "▶", "l": "L", "r": "R", "select": "SELECT", "start": "START", "stick": ""}


class PspView(QWidget):
    picked = Signal(str, QPoint)  # a control name from SPOTS, where it was clicked on screen

    def __init__(self, colors: dict):
        super().__init__()
        self.c, self.pressed, self.stick, self.hover = colors, 0, (128, 128), None
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
        c = {k: QColor(v) for k, v in self.c.items()}
        p.setPen(QPen(c["border"], 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(10, 28, 580, 190), 95, 95)  # body
        p.drawRoundedRect(QRectF(160, 46, 280, 154), 4, 4)  # screen
        p.setFont(QFont("Space Mono", 9))
        p.setPen(c["text_disabled"])
        p.drawText(QRectF(160, 46, 280, 154), Qt.AlignCenter, "CLICK A BUTTON\nTO CHOOSE WHAT IT DOES")
        for name, r in SPOTS.items():
            rect = QRectF(*r)
            on = bool(self.pressed & BUTTONS.get(name, 0))
            p.setPen(QPen(c["accent"] if on else c["display"] if name == self.hover else c["text_secondary"], 1.5))
            p.setBrush(c["accent"] if on else Qt.NoBrush)
            if name in ROUND:
                p.drawEllipse(rect)
            else:
                p.drawRoundedRect(rect, 4, 4)
            p.setPen(c["bg"] if on else c["text"])
            p.drawText(rect, Qt.AlignCenter, SYMBOLS[name])
        x, y = self.stick  # where the PSP's stick is: a dot inside its circle
        p.setPen(Qt.NoPen)
        p.setBrush(c["accent"] if max(abs(x - 128), abs(y - 128)) > 24 else c["text_secondary"])
        p.drawEllipse(QPointF(97 + (x - 128) / 10, 179 + (y - 128) / 10), 4, 4)
