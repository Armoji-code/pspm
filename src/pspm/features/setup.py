from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import deps, features
from .saves import _Job


class SetupPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.job = cfg, None
        self.titles = {f.id: f.title for f in features.enabled(cfg)}
        self.red = QColor(cfg["themes"][cfg["app"]["theme"]]["accent"])
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["PROGRAM", "NEEDED FOR", "STATUS"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.status = QLabel(wordWrap=True)
        self.status.setObjectName("label")
        self.btn = QPushButton("INSTALL MISSING")
        self.btn.clicked.connect(self.install)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        bar.addWidget(self.btn)
        col = QVBoxLayout(self)
        col.addWidget(QLabel("PROGRAMS THIS PC NEEDS FOR SOME FEATURES. PSPM INSTALLS THEM WITH YOUR PACKAGE MANAGER.",
                             wordWrap=True))
        col.addWidget(self.table, 1)
        col.addLayout(bar)
        self.refresh()

    def refresh(self):
        rows = deps.status(self.cfg)
        self.table.setRowCount(len(rows))
        for i, (t, ok) in enumerate(rows):
            for j, text in enumerate([t["name"], ", ".join(self.titles.get(f, f) for f in t["features"]),
                                      "INSTALLED" if ok else "MISSING"]):
                self.table.setItem(i, j, QTableWidgetItem(text))
            if not ok:
                self.table.item(i, 2).setForeground(self.red)
        self.table.resizeColumnsToContents()
        n = sum(not ok for _, ok in rows)
        self.btn.setEnabled(bool(n))
        self.status.setText(f"{n} MISSING" if n else "EVERYTHING INSTALLED")

    def set_device(self, dev):
        pass  # about this PC, not the PSP

    def install(self):
        if self.job and self.job.isRunning():
            return
        tools = deps.missing(self.cfg)
        cmds, manual = deps.commands(self.cfg, tools)
        text = "\n".join(" ".join(c) for c in cmds)
        if manual:
            text += "\n\nNot in your package manager, opens its download page:\n" + "\n".join(
                f"{t['name']}" + (f" ({t['note']})" if t.get("note") else "") for t in manual)
        if QMessageBox.question(self, "Install", f"Run this? It asks for your password.\n\n{text}") != QMessageBox.Yes:
            return
        self.status.setText("INSTALLING…")
        self.btn.setEnabled(False)
        self.job = _Job(lambda: deps.install(self.cfg, tools), self)
        self.job.done.connect(self._done)
        self.job.failed.connect(lambda e: (self.refresh(), self.status.setText(f"[ERROR: {e}]".upper())))
        self.job.start()

    def _done(self, manual):
        self.refresh()
        if manual:
            self.status.setText("FINISH IN THE PAGE THAT OPENED: " + ", ".join(t["name"] for t in manual).upper())


class _Feature:
    id, title = "setup", "SETUP"

    def page(self, cfg):
        return SetupPage(cfg)


FEATURE = _Feature()
