import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import diag
from ..ui.fmt import size
from .saves import _Job


class DiagPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.dev, self.job, self.prog = cfg, None, None, None
        self.stopping = threading.Event()
        self.grid = QGridLayout()
        self.out = QPlainTextEdit(readOnly=True)
        self.status = QLabel()
        self.status.setObjectName("label")
        self.test = QPushButton()
        self.test.clicked.connect(self.capacity)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("INSTALL INFO APP", self.install), ("CHECK FILESYSTEM", self.check)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addWidget(self.test)
        stop = QPushButton("STOP")
        stop.setObjectName("danger")
        stop.clicked.connect(self.stopping.set)
        bar.addWidget(stop)
        col = QVBoxLayout(self)
        col.addLayout(self.grid)
        col.addWidget(self.out, 1)
        col.addLayout(bar)
        self.timer = QTimer(self, interval=500, timeout=self._tick)
        self.set_device(None)

    def _run(self, fn, then, msg):
        if self.job and self.job.isRunning():
            return
        self.stopping.clear()
        self.prog = None
        self.status.setText(msg)
        self.job = _Job(fn, self)
        self.job.done.connect(then)
        self.job.failed.connect(lambda e: self.status.setText(f"[ERROR: {e}]"))
        self.job.finished.connect(self.timer.stop)
        self.job.start()

    def _tick(self):
        if self.prog:
            done, total, verb = self.prog
            self.status.setText(f"{verb} {done} / {total} MB · STOP TO CANCEL")

    def set_device(self, dev):
        self.dev = dev
        while self.grid.count():
            self.grid.takeAt(0).widget().deleteLater()
        if not dev:
            self.status.setText("[NO PSP CONNECTED]")
            self.test.setText("TEST CAPACITY")
            return
        u = dev.usage()
        rows = [("SPACE", f"{size(u.used)} USED · {size(u.free)} FREE · {size(u.total)} TOTAL")]
        info = diag.read_info(dev, self.cfg)
        if info:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(info.pop("written")))
            rows += [(k.replace("_", " ").upper(), v.upper()) for k, v in info.items()] + [("READ ON PSP", when)]
        else:
            rows.append(("PSP INFO", "INSTALL THE INFO APP, RUN IT ON THE PSP, THEN PLUG IT IN AGAIN"))
        for r, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setObjectName("label")
            self.grid.addWidget(key, r, 0)
            self.grid.addWidget(QLabel(v), r, 1)
        pending = diag.test_dir(dev, self.cfg).is_dir()
        self.test.setText("VERIFY CAPACITY" if pending else "TEST CAPACITY")
        self.status.setText("[TEST DATA WRITTEN · PRESS VERIFY CAPACITY]" if pending else "READY")

    def install(self):
        if not self.dev:
            return
        try:
            dest = diag.install_app(self.dev, self.cfg)
        except OSError as e:
            self.status.setText(f"[ERROR: {e}]".upper())
            return
        self.status.setText(f"INSTALLED TO {dest} · RUN PSPM INFO FROM THE GAME MENU".upper())

    def check(self):
        if self.dev:
            self._run(lambda: diag.fs_check(self.dev), self._checked, "CHECKING FILESYSTEM (READ ONLY)…")

    def _checked(self, res):
        code, text = res
        self.out.setPlainText(text)
        self.status.setText("FILESYSTEM CLEAN" if code == 0 else f"[CHECK REPORTED PROBLEMS OR FAILED · CODE {code}]")

    def capacity(self):
        if not self.dev:
            return
        folder = diag.test_dir(self.dev, self.cfg)
        prog = lambda verb: lambda d, t: setattr(self, "prog", (d, t, verb))
        if folder.is_dir():
            self.timer.start()
            self._run(lambda: diag.verify(folder, prog("VERIFYING"), self.stopping.is_set),
                      lambda r: self._verified(folder, *r), "VERIFYING…")
            return
        c = self.cfg["diag"]
        free = self.dev.usage().free - c["test_reserve_mb"] * diag.BLOCK
        if free <= 0 or QMessageBox.question(
            self, "Test capacity",
            f"Fill the {size(free)} of free space with test data, then check it reads back?\n\n"
            "This can take hours. On a fake card, writing past its real size can damage files "
            "already on it, so back them up first.") != QMessageBox.Yes:
            return
        self.timer.start()
        self._run(lambda: diag.fill(folder, free, c["test_file_mb"], prog("WRITING"), self.stopping.is_set),
                  self._filled, "WRITING…")

    def _filled(self, n):
        if self.stopping.is_set():
            diag.clean(diag.test_dir(self.dev, self.cfg))
            return self.set_device(self.dev)
        self.set_device(self.dev)
        # Replugging empties the PC's disk cache, so VERIFY reads the card and not memory.
        self.status.setText(f"WROTE {size(n)} · UNPLUG THE PSP, PLUG IT BACK IN, THEN PRESS VERIFY CAPACITY")

    def _verified(self, folder, good, bad):
        stopped = self.stopping.is_set()
        if not stopped:
            diag.clean(folder)
        self.set_device(self.dev)
        if stopped:
            self.status.setText("VERIFY STOPPED · TEST DATA KEPT")
        elif bad:
            self.status.setText(f"[FAKE OR FAILING CARD: {size(bad)} BAD, ONLY {size(good)} READ BACK RIGHT]")
        else:
            self.status.setText(f"CAPACITY OK · {size(good)} VERIFIED")


class _Feature:
    id, title = "diag", "DIAGNOSTICS"

    def page(self, cfg):
        return DiagPage(cfg)


FEATURE = _Feature()
