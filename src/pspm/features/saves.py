import time
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon
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

from .. import gamelink, saves


class _Job(QThread):
    """Runs hashing/copying off the UI thread; a PSP over USB is slow."""

    done = Signal(object)
    failed = Signal(str)
    step = Signal(str)  # what it's on now, e.g. "3/12 ULUS10041"; emit from the worker, shown after the status

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
        self.table.setIconSize(QSize(72, 40))  # ICON0 is 144x80
        self.table.verticalHeader().setMinimumSectionSize(44)
        self.table.verticalScrollBar().valueChanged.connect(self._icons)
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
        self.job.step.connect(lambda t: self.status.setText(f"{msg} {t.upper()}"))
        self.job.start()

    @contextmanager
    def _lock(self):
        """saves.LOCK, saying so while the cloud backup page holds it (it reads every save first)."""
        if not saves.LOCK.acquire(blocking=False):
            self._say("waiting for cloud backup")
            saves.LOCK.acquire()
        try:
            yield
        finally:
            saves.LOCK.release()

    def _say(self, text):
        self.job.step.emit(text)  # called from the worker thread; the signal hops back to the UI

    def set_device(self, dev):
        self.dev = dev
        self._show([])
        self.games = self._link_games()
        if dev:
            self.check(auto=self.cfg["saves"]["auto_sync"])
        else:
            self.status.setText("[NO PSP CONNECTED]")

    def _link_games(self):
        pc = saves.ppsspp_dir(self.cfg, self.state)
        if not pc:
            return ""
        folder = gamelink.link_dir(pc, self.cfg)
        try:
            if not self.dev:
                gamelink.unlink(folder)
                return ""
            done = gamelink.link(self.dev, folder, self.cfg["saves"]["game_links"])
        except OSError as e:
            return f"  ·  [GAMES NOT LINKED: {e.strerror or e}]"
        return f"  ·  PSP GAMES IN PPSSPP: {folder}" if done else ""

    def pick(self):
        d = QFileDialog.getExistingDirectory(self, "PPSSPP SAVEDATA folder", self.state.get("ppsspp", ""))
        if d:
            with saves.LOCK:
                self.state = saves.load_state(self.state_file)
                self.state["ppsspp"] = d
                saves.save_state(self.state, self.state_file)
            self.games = self._link_games()
            self.check()

    def check(self, *_, auto=False):
        dirs = self._dirs()
        if not dirs:
            self.status.setText("[NO PSP CONNECTED]" if not self.dev else "[PPSSPP NOT FOUND · PICK ITS SAVEDATA FOLDER]")
            return
        def work():
            with self._lock():  # the cloud backup page syncs too, and writes the same state file
                self.state = saves.load_state(self.state_file)
                items = saves.plan(*dirs, saves.history(self.state, dirs[1]), self._say, self.state.setdefault("hashes", {}))
                saves.save_state(self.state, self.state_file)
                return items

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
            self.status.setText((f"{len(todo)} TO SYNC" if todo else f"IN SYNC · {len(items)} SAVES") + self.games)

    def _show(self, items):
        """Every save: the ones needing a pick first, then the rest to sync, then those already in sync."""
        self.items = sorted(items, key=lambda i: (i.action == saves.SAME, not i.conflict, i.name))
        self.table.clearContents()
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            for c, text in enumerate([it.name, side(it.psp_time, it.psp_desc), side(it.pc_time, it.pc_desc)]):
                self.table.setItem(r, c, QTableWidgetItem(text))
            if it.conflict:
                box = QComboBox()
                box.addItems([saves.TO_PC, saves.TO_PSP, saves.SKIP])
                box.setCurrentText(it.action)  # newest side suggested
                box.currentTextChanged.connect(lambda a, it=it: setattr(it, "action", a))
                self.table.setCellWidget(r, 3, box)
            else:
                self.table.setItem(r, 3, QTableWidgetItem("IN SYNC" if it.action == saves.SAME else it.action))
        self.table.resizeRowsToContents()
        self._icons()

    def _icons(self, *_):
        """ICON0 for the rows on screen only, PPSSPP's copy first: reading every one off the PSP would stall the page."""
        t, dirs = self.table, self._dirs()
        if not (dirs and t.isVisible() and t.rowCount()):
            return
        last = t.rowAt(t.viewport().height() - 1)
        for r in range(max(t.rowAt(0), 0), (t.rowCount() - 1 if last < 0 else last) + 1):
            cell = t.item(r, 0)
            if cell.data(Qt.UserRole):
                continue
            cell.setData(Qt.UserRole, True)
            png = next((p for d in reversed(dirs) if (p := d / cell.text() / "ICON0.PNG").is_file()), None)
            if png:
                cell.setIcon(QIcon(str(png)))

    def showEvent(self, e):
        super().showEvent(e)
        self._icons()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._icons()

    def sync(self):
        dirs = self._dirs()
        if not (dirs and self.items):
            return
        items = self.items
        if all(i.action == saves.SAME for i in items):
            self.status.setText(f"IN SYNC · {len(items)} SAVES" + self.games)  # nothing to copy
            return

        def work():
            with self._lock():
                self.state = saves.load_state(self.state_file)
                run = saves.apply(items, *dirs, saves.history(self.state, dirs[1]), self.backups, self._say, self.state.setdefault("hashes", {}))
                saves.save_state(self.state, self.state_file)
            return run, sum(i.action in (saves.TO_PC, saves.TO_PSP) for i in items)

        self._run(work, self._synced, "SYNCING…")

    def _synced(self, res):
        run, n = res
        for it in self.items:
            if it.action in (saves.TO_PC, saves.TO_PSP):
                it.action, it.conflict = saves.SAME, False
        self._show(self.items)
        self.status.setText(f"SYNCED {n} SAVES" + (f"  ·  OLD COPIES IN {run}" if Path(run).exists() else "") + self.games)


class _Feature:
    id, title = "saves", "PPSSPP SAVES"

    def page(self, cfg):
        return SavesPage(cfg)


FEATURE = _Feature()
