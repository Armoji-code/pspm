from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

from .. import ps1
from .saves import _Job


class Ps1Page(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.dev, self.job, self.icon = cfg, None, None, None
        self.discs = QListWidget()
        self.status = QLabel()
        self.status.setObjectName("label")
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("ADD DISCS", self.add), ("ICON", self.pick_icon), ("CLEAR", self.clear),
                         ("CONVERT", self.convert)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        col = QVBoxLayout(self)
        col.addWidget(self.discs, 1)
        col.addLayout(bar)
        self.set_device(None)

    def set_device(self, dev):
        self.dev = dev
        if not (self.job and self.job.isRunning()):
            self._ready()

    def _ready(self):
        if not self.dev:
            self.status.setText("[NO PSP CONNECTED]")
        elif not self.discs.count():
            self.status.setText("ADD ONE GAME'S DISCS, IN PLAY ORDER")
        else:
            icon = f" · ICON {Path(self.icon).name}" if self.icon else ""
            self.status.setText(f"{self.discs.count()} DISC(S){icon}".upper())

    def add(self):
        got, _ = QFileDialog.getOpenFileNames(self, "PS1 disc images", "", f"PS1 discs ({' '.join(ps1.IMAGES)})")
        self.discs.addItems(sorted(got))  # "Disc 1", "Disc 2"... sort into play order
        self._ready()

    def pick_icon(self):
        self.icon = QFileDialog.getOpenFileName(self, "Icon image", "", "Images (*.png *.jpg *.jpeg)")[0] or None
        self._ready()

    def clear(self):
        self.discs.clear()
        self.icon = None
        self._ready()

    def convert(self):
        if not (self.dev and self.discs.count()) or (self.job and self.job.isRunning()):
            return
        discs = [Path(self.discs.item(i).text()) for i in range(self.discs.count())]
        icon, games = self.icon and Path(self.icon), self.dev.path("games")
        self.status.setText("CONVERTING… (THE FIRST TIME ALSO DOWNLOADS PSXPACKAGER)")
        self.job = _Job(lambda: ps1.convert(discs, games, self.cfg, icon), self)
        self.job.done.connect(self._done)
        self.job.failed.connect(lambda e: self.status.setText(f"[ERROR: {e}]".upper()))
        self.job.start()

    def _done(self, dest):
        self.clear()
        name = ps1.title(dest / "EBOOT.PBP") or dest.name
        self.status.setText(f"INSTALLED {name} TO {self.cfg['device']['folders']['games']}/{dest.name}".upper())


class _Feature:
    id, title = "ps1", "PS1 CONVERTER"

    def page(self, cfg):
        return Ps1Page(cfg)


FEATURE = _Feature()
