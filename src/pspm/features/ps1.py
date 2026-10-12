from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import ps1
from .saves import _Job

SLOTS = {"icon0": "ICON", "pic0": "INFO PICTURE", "pic1": "BACKGROUND", "snd0": "MUSIC (AT3)"}
IMAGES = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"


class Ps1Page(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.dev, self.job, self.meta = cfg, None, None, {}
        self.discs = QListWidget()
        self.game_id = QLineEdit(placeholderText="GAME ID, E.G. SLUS-00594")
        self.title = QLineEdit(placeholderText="TITLE ON THE XMB")
        self.status = QLabel()
        self.status.setObjectName("label")
        find = QPushButton("FIND ONLINE")
        find.clicked.connect(self.find)
        side = QGridLayout()
        side.addWidget(self.game_id, 0, 0, 1, 2)
        side.addWidget(find, 0, 2, 1, 2)
        side.addWidget(self.title, 1, 0, 1, 4)
        self.shown = {}
        for row, (res, name) in enumerate(SLOTS.items(), 2):
            self.shown[res] = QLabel(alignment=Qt.AlignCenter)
            self.shown[res].setFixedSize(144, 80)
            pick, clear = QPushButton("PICK"), QPushButton("CLEAR")
            pick.clicked.connect(lambda _=False, r=res: self.pick(r))
            clear.clicked.connect(lambda _=False, r=res: self.set_res(r, None))
            tag = QLabel(name)
            tag.setObjectName("label")
            for col, w in enumerate((self.shown[res], tag, pick, clear)):
                side.addWidget(w, row, col)
        side.setRowStretch(len(SLOTS) + 2, 1)
        top = QHBoxLayout()
        top.addWidget(self.discs, 1)
        top.addLayout(side)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("ADD DISCS", self.add), ("CLEAR", self.clear), ("CONVERT", self.convert)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        col = QVBoxLayout(self)
        col.addLayout(top, 1)
        col.addLayout(bar)
        self.clear()

    def set_device(self, dev):
        self.dev = dev
        if not self._busy():
            self._ready()

    def _busy(self):
        return self.job and self.job.isRunning()

    def _ready(self):
        if not self.dev:
            self.status.setText("[NO PSP CONNECTED]")
        elif not self.discs.count():
            self.status.setText("ADD ONE GAME'S DISCS, IN PLAY ORDER")
        else:
            self.status.setText(f"{self.discs.count()} DISC(S) · EDIT ANYTHING ON THE RIGHT, THEN CONVERT")

    def _run(self, text, fn, done):
        self.status.setText(text)
        self.job = _Job(fn, self)
        self.job.done.connect(done)
        self.job.failed.connect(lambda e: self.status.setText(f"[ERROR: {e}]".upper()))
        self.job.start()

    def set_res(self, res, data):
        self.meta[res] = data
        label = self.shown[res]
        if res == "snd0":
            label.setText(f"{len(data) // 1024} KB" if data else "—")
            return
        pix = QPixmap()
        if data and pix.loadFromData(data):
            label.setPixmap(pix.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            label.setPixmap(QPixmap())
            label.setText("—")

    def pick(self, res):
        kind = "XMB music (*.at3)" if res == "snd0" else IMAGES
        src = QFileDialog.getOpenFileName(self, SLOTS[res].title(), "", kind)[0]
        if src:
            self.set_res(res, Path(src).read_bytes())

    def add(self):
        got, _ = QFileDialog.getOpenFileNames(self, "PS1 disc images", "", f"PS1 discs ({' '.join(ps1.IMAGES)})")
        if not got:
            return
        first = not self.discs.count()
        self.discs.addItems(sorted(got))  # "Disc 1", "Disc 2"... sort into play order
        self._ready()
        if first and not self._busy():
            gid = ps1.serial(Path(self.discs.item(0).text()))
            self.game_id.setText(gid)
            if gid:
                self.find()

    def find(self):
        gid = self.game_id.text().strip()
        if gid and not self._busy():
            self._run(f"LOOKING UP {gid}…".upper(), lambda: ps1.lookup(gid, self.cfg), self._found)

    def _found(self, meta):
        self.title.setText(meta["title"])
        for res in SLOTS:
            if res in meta:
                self.set_res(res, meta[res])
        missing = [SLOTS[r] for r in ("icon0", "pic0", "pic1") if r not in meta]
        none = f" · NONE ONLINE: {', '.join(missing)}" if missing else ""
        self.status.setText(f"FOUND {meta['title']}{none}".upper())

    def clear(self):
        self.discs.clear()
        self.game_id.clear()
        self.title.clear()
        for res in SLOTS:
            self.set_res(res, None)
        self._ready()

    def convert(self):
        if not (self.dev and self.discs.count()) or self._busy():
            return
        discs = [Path(self.discs.item(i).text()) for i in range(self.discs.count())]
        meta = {k: v for k, v in self.meta.items() if v} | {"title": self.title.text().strip()}
        games = self.dev.path("games")
        self._run("CONVERTING… (THE FIRST TIME ALSO DOWNLOADS PSXPACKAGER)",
                  lambda: ps1.convert(discs, games, self.cfg, meta), self._done)

    def _done(self, dest):
        self.clear()
        name = ps1.title(dest / "EBOOT.PBP") or dest.name
        self.status.setText(f"INSTALLED {name} TO {self.cfg['device']['folders']['games']}/{dest.name}".upper())


class _Feature:
    id, title = "ps1", "PS1 CONVERTER"

    def page(self, cfg):
        return Ps1Page(cfg)


FEATURE = _Feature()
