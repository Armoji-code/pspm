import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import saves


class _Job(QThread):
    """Runs hashing/copying off the UI thread; a PSP over USB is slow."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa: BLE001 - network, keyring, disk: shown in the status line instead of killing the thread
            self.failed.emit(str(e) or type(e).__name__)


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "—"


def side(t, desc):
    """One side of a save: when it last changed, then the game's own description to compare progress."""
    return f"{_when(t)}\n{desc}" if desc else _when(t)


class SavesPage(QWidget):
    def __init__(self, cfg, state_file=saves.STATE, backups=saves.BACKUPS):
        super().__init__()
        self.cfg, self.dev, self.items, self.job = cfg, None, [], None
        self.state_file, self.backups = state_file, backups
        self.state = saves.load_state(state_file)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["SAVE", "PSP", "PPSSPP", "ACTION"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.status = QLabel()
        self.status.setObjectName("label")
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("PPSSPP FOLDER", self.pick), ("CHECK", self.check), ("SYNC", self.sync)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        col = QVBoxLayout(self)
        col.addWidget(self.table)
        col.addLayout(bar)
        self.set_device(None)

    def _dirs(self):
        pc = saves.ppsspp_dir(self.cfg, self.state)
        return (self.dev.path("saves"), pc) if self.dev and pc else None

    def _run(self, fn, then, msg):
        if self.job and self.job.isRunning():
            return
        self.status.setText(msg)
        self.job = _Job(fn, self)
        self.job.done.connect(then)
        self.job.failed.connect(lambda e: self.status.setText(f"[ERROR: {e}]"))
        self.job.start()

    def set_device(self, dev):
        self.dev = dev
        self._show([])
        if dev:
            self.check(auto=self.cfg["saves"]["auto_sync"])
        else:
            self.status.setText("[NO PSP CONNECTED]")

    def pick(self):
        d = QFileDialog.getExistingDirectory(self, "PPSSPP SAVEDATA folder", self.state.get("ppsspp", ""))
        if d:
            with saves.LOCK:
                self.state = saves.load_state(self.state_file)
                self.state["ppsspp"] = d
                saves.save_state(self.state, self.state_file)
            self.check()

    def check(self, *_, auto=False):
        dirs = self._dirs()
        if not dirs:
            self.status.setText("[NO PSP CONNECTED]" if not self.dev else "[PPSSPP NOT FOUND · PICK ITS SAVEDATA FOLDER]")
            return
        def work():
            with saves.LOCK:  # the cloud backup page syncs too, and writes the same state file
                self.state = saves.load_state(self.state_file)
                return saves.plan(*dirs, self.state["last"])

        self._run(work, lambda items: self._planned(items, auto), "CHECKING…")

    def _planned(self, items, auto):
        self._show(items)
        todo = [i for i in items if i.action != saves.SAME]
        conflicts = sum(i.conflict for i in items)
        if auto and todo and not conflicts:
            self.sync()
        elif conflicts:
            self.status.setText(f"[{conflicts} CHANGED ON BOTH SIDES · PICK AN ACTION, THEN SYNC]")
        else:
            self.status.setText(f"{len(todo)} TO SYNC" if todo else f"IN SYNC · {len(items)} SAVES")

    def _show(self, items):
        self.items = items
        rows = [i for i in items if i.action != saves.SAME]
        self.table.setRowCount(len(rows))
        for r, it in enumerate(rows):
            for c, text in enumerate([it.name, side(it.psp_time, it.psp_desc), side(it.pc_time, it.pc_desc)]):
                self.table.setItem(r, c, QTableWidgetItem(text))
            if it.conflict:
                box = QComboBox()
                box.addItems([saves.TO_PC, saves.TO_PSP, saves.SKIP])
                box.setCurrentText(it.action)  # newest side suggested
                box.currentTextChanged.connect(lambda a, it=it: setattr(it, "action", a))
                self.table.setCellWidget(r, 3, box)
            else:
                self.table.setItem(r, 3, QTableWidgetItem(it.action))
        self.table.resizeRowsToContents()

    def sync(self):
        dirs = self._dirs()
        if not (dirs and self.items):
            return
        items = self.items

        def work():
            with saves.LOCK:
                self.state = saves.load_state(self.state_file)
                run = saves.apply(items, *dirs, self.state["last"], self.backups)
                saves.save_state(self.state, self.state_file)
            return run, sum(i.action in (saves.TO_PC, saves.TO_PSP) for i in items)

        self._run(work, self._synced, "SYNCING…")

    def _synced(self, res):
        run, n = res
        self._show([])
        self.status.setText(f"SYNCED {n} SAVES" + (f"  ·  OLD COPIES IN {run}" if Path(run).exists() else ""))


class _Feature:
    id, title = "saves", "PPSSPP SAVES"

    def page(self, cfg):
        return SavesPage(cfg)


FEATURE = _Feature()
