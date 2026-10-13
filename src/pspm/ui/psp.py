"""A PSP-2000 outline (psp.svg, the trace the user picked) in the theme's display color: click a button or the stick to pick what it does on the PC; pressed buttons light up."""
import re
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ..controls import BUTTONS

SVG = Path(__file__).with_name("psp.svg")
W, H = 566, 234  # the SVG's viewBox; scaled to the widget
PARTS = {"up": 5, "left": 8, "right": 9, "down": 10, "stick": 14, "triangle": 4, "square": 6, "circle": 7,
         "cross": 11, "select": 17, "start": 18}  # control -> its outline in psp.svg's path, counted from 0


def _outlines() -> list[QPainterPath]:
    """psp.svg's potrace path (M/m, c, l, z) as one QPainterPath per closed outline, in drawing units."""
    d = re.search(r' d="([^"]*)"', SVG.read_text()).group(1)
    toks = iter(re.findall(r"[MmcClLzZ]|-?\d+", d))
    at = lambda x, y: QPointF(int(x) / 40 - 137, 615 - int(y) / 40 - 101)  # the SVG's transforms, then viewBox
    out, x, y, cmd = [], 0, 0, None
    for t in toks:
        if t.isalpha():
            if t in "zZ":
                continue
            cmd = t
            t = next(toks)
        if cmd in "Mm":
            x, y = (int(t), int(next(toks))) if cmd == "M" else (x + int(t), y + int(next(toks)))
            out.append(QPainterPath(at(x, y)))
            cmd = "l"
        elif cmd == "c":
            pts = [(x + int(t), y + int(next(toks)))] + [(x + int(next(toks)), y + int(next(toks))) for _ in range(2)]
            out[-1].cubicTo(*(at(*q) for q in pts))
            x, y = pts[2]
        else:
            x, y = x + int(t), y + int(next(toks))
            out[-1].lineTo(at(x, y))
    return out


def _shapes() -> dict[str, QPainterPath]:
    parts = _outlines()
    shapes = {n: parts[i] for n, i in PARTS.items()}
    for n, x in (("l", 23), ("r", 468)):  # the outline has no shoulder buttons: a tab along the top edge
        shapes[n] = QPainterPath()
        shapes[n].addRoundedRect(QRectF(x, 0, 75, 16), 6, 6)
    return shapes


SYMBOLS = {"triangle": "△", "cross": "✕", "square": "□", "circle": "○", "up": "▲", "down": "▼", "left": "◀",
           "right": "▶", "l": "L", "r": "R", "select": "SELECT", "start": "START", "stick": ""}


class PspView(QWidget):
    picked = Signal(str, QPoint)  # a control name from PARTS, "l" or "r", where it was clicked on screen

    def __init__(self, colors: dict):
        super().__init__()
        self.c, self.pressed, self.stick, self.hover = colors, 0, (128, 128), None
        self.shapes = _shapes()
        self.svg = QSvgRenderer(QByteArray(SVG.read_bytes().replace(b"COLOR", colors["display"].encode())), self)
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
        return next((n for n, path in self.shapes.items() if path.boundingRect().adjusted(-3, -3, 3, 3).contains(p)), None)

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
        p.setPen(c["text_secondary"])
        p.drawText(QRectF(121, 16, 326, 183), Qt.AlignCenter, "CLICK A BUTTON\nTO CHOOSE WHAT IT DOES")
        for name, path in self.shapes.items():
            on, hover = bool(self.pressed & BUTTONS.get(name, 0)), name == self.hover
            if not (on or hover):
                continue
            fill = QColor(c["accent"])
            fill.setAlpha(170)
            p.setPen(QPen(c["accent"], 1.5))
            p.setBrush(fill if on else Qt.NoBrush)
            p.drawPath(path)
        x, y = self.stick  # where the PSP's stick is: a dot on the nub
        if max(abs(x - 128), abs(y - 128)) > 24:
            p.setPen(Qt.NoPen)
            p.setBrush(c["accent"])
            p.drawEllipse(QPointF(67.5 + (x - 128) / 10, 176.5 + (y - 128) / 10), 4, 4)
