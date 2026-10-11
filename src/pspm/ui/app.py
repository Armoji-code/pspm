import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import device, features
from .fmt import size

# Nothing-style sheet; colors come from config [themes.*]. Fonts fall back if not installed.
QSS = """
* {{ font-family: "Space Grotesk", "DM Sans", sans-serif; font-size: 14px; color: {text}; }}
QMainWindow, QWidget {{ background: {bg}; }}
QListWidget, QTreeWidget, QTableWidget {{ background: {bg}; border: 1px solid {border}; outline: 0; gridline-color: {border}; }}
QListWidget::item {{ padding: 8px; font-family: "Space Mono", monospace; color: {text_secondary}; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {surface}; color: {display}; }}
QHeaderView::section {{ background: {bg}; color: {text_disabled}; border: 0; border-bottom: 1px solid {border};
    font-family: "Space Mono", monospace; font-size: 11px; padding: 6px; }}
QPushButton, QComboBox, QLineEdit, QSpinBox {{ font-family: "Space Mono", monospace; font-size: 11px; padding: 8px 18px;
    border: 1px solid {border}; border-radius: 16px; background: {bg}; }}
QPushButton:hover, QComboBox:hover, QLineEdit:focus, QSpinBox:focus {{ border-color: {text}; }}
QTabBar::tab {{ font-family: "Space Mono", monospace; font-size: 11px; color: {text_secondary}; background: {bg};
    padding: 6px 14px; border: 0; border-bottom: 2px solid {bg}; text-transform: uppercase; }}
QTabBar::tab:selected {{ color: {display}; border-bottom-color: {display}; }}
QPushButton#danger {{ color: {accent}; }}
QLabel#label {{ font-family: "Space Mono", monospace; font-size: 11px; color: {text_secondary}; }}
QLabel#hero {{ font-family: "Doto", "Space Mono", monospace; font-size: 48px; color: {display}; }}
"""


class Main(QMainWindow):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("PSPM")
        self.resize(1100, 700)
        self.dev = False  # not None, so the first poll always renders

        self.hero = QLabel()
        self.hero.setObjectName("hero")
        self.meta = QLabel()
        self.meta.setObjectName("label")
        self.nav = QListWidget()
        self.nav.setFixedWidth(180)
        self.stack = QStackedWidget()
        self.pages = []
        for f in features.enabled(cfg):
            self.nav.addItem(f.title)
            self.pages.append(f.page(cfg))
            self.stack.addWidget(self.pages[-1])
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body = QHBoxLayout()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root = QVBoxLayout()
        root.setContentsMargins(32, 24, 32, 24)
        root.addWidget(self.hero)
        root.addWidget(self.meta)
        root.addSpacing(24)
        root.addLayout(body, 1)
        w = QWidget()
        w.setLayout(root)
        self.setCentralWidget(w)

        self.timer = QTimer(self, interval=int(cfg["app"]["poll_seconds"] * 1000), timeout=self.poll)
        self.timer.start()
        self.poll()

    def poll(self):
        found = device.find(self.cfg)
        dev = found[0] if found else None  # shortcut: first PSP only, add a picker if people plug in two
        if dev == self.dev:
            return
        self.dev = dev
        if dev:
            u = dev.usage()
            self.hero.setText(f"{size(u.free)} FREE")
            self.meta.setText(f"PSP CONNECTED  ·  {dev.root}  ·  {size(u.total)} TOTAL".upper())
        else:
            self.hero.setText("NO PSP")
            self.meta.setText("PLUG IN YOUR PSP AND ENABLE USB MODE")
        for p in self.pages:
            p.set_device(dev)


def run(cfg) -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS.format(**cfg["themes"][cfg["app"]["theme"]]))
    win = Main(cfg)
    win.show()
    return app.exec()
