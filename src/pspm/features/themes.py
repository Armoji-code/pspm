from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import theme

GROUPS = {"0": "PREVIEW", "1": "WALLPAPER", "2": "MENU", "3": "ITEM", "4": "SETTINGS"}


class ThemesPage(QWidget):
    def __init__(self, cfg, work=theme.WORK):
        super().__init__()
        self.cfg, self.dev, self.work, self.folder = cfg, None, work, None
        self.wall = QLabel(alignment=Qt.AlignCenter)
        self.wall.setFixedSize(240, 136)
        self.items = QListWidget(viewMode=QListWidget.IconMode, iconSize=QSize(48, 48),
                                 gridSize=QSize(110, 84), resizeMode=QListWidget.Adjust)
        self.items.itemDoubleClicked.connect(self.pick_image)  # a lambda on self here kept the page alive past Qt
        self.name = QLineEdit(placeholderText="THEME NAME")
        self.mode = QSpinBox(minimum=0, maximum=12, prefix="COLOUR ")
        self.status = QLabel()
        self.status.setObjectName("label")
        top = QHBoxLayout()
        side = QVBoxLayout()
        for w in (self.wall, self.name, self.mode):
            side.addWidget(w)
        side.addStretch(1)
        top.addLayout(side)
        top.addWidget(self.items, 1)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("NEW", self.new), ("OPEN THEME", self.open), ("IMAGE", self.pick_image),
                         ("FOLDER", self.show_folder), ("BUILD", self.build)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        col = QVBoxLayout(self)
        col.addLayout(top, 1)
        col.addLayout(bar)
        self._say("NEW STARTS BLANK · OPEN THEME TAKES A PTF OR CTF APART TO USE AS A TEMPLATE")

    def set_device(self, dev):
        self.dev = dev

    def _say(self, text):
        self.status.setText(text.upper())

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - a bad file or full disk shows in the status line
            self._say(f"[ERROR: {e or type(e).__name__}]")

    def _free(self, stem):
        n, path = 1, self.work / stem
        while path.exists():
            n, path = n + 1, self.work / f"{stem}-{n}"
        return path

    def load(self, folder: Path):
        self.folder = folder
        m = theme.meta(folder)
        self.name.setText(m["name"])
        self.mode.setValue(int(m["background_mode"]))
        self.items.clear()
        for key, (_, w, h) in m["items"].items():
            png = folder / f"{key}.png"
            item = QListWidgetItem(QIcon(str(png)) if png.exists() else QIcon(), f"{GROUPS.get(key[0], key[0])} {key}")
            item.setData(Qt.UserRole, key)
            item.setToolTip(f"{w}x{h}")
            self.items.addItem(item)
        wall = folder / "1-00.png"
        if not wall.exists():
            wall = folder / "0-01.png"
        self.wall.setPixmap(QPixmap(str(wall)).scaled(self.wall.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        files = len(theme.flash0(folder))
        self._say(f"EDITING {folder.name} · {files} FLASH0 FILE(S) FOR CTF · DOUBLE-CLICK AN IMAGE TO REPLACE IT")

    def new(self):
        self._try(lambda: self.load(theme.new(self._free("my-theme"), "My theme")))

    def open(self):
        start = str(self.dev.path("themes")) if self.dev else ""
        src = QFileDialog.getOpenFileName(self, "PSP theme", start, "PSP themes (*.ptf *.ctf)")[0]
        if src:
            self._try(lambda: self.load(theme.unpack(Path(src), self._free(Path(src).stem))))

    def pick_image(self, *_):
        item = self.items.currentItem()
        if not (self.folder and item):
            return
        src = QFileDialog.getOpenFileName(self, "Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")[0]
        if src:
            self._try(lambda: (theme.replace(self.folder, item.data(Qt.UserRole), Path(src)), self.load(self.folder)))

    def show_folder(self):
        if self.folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))

    def build(self):
        if self.folder:
            self._try(self._build)

    def _build(self):
        name = self.name.text().strip() or self.folder.name
        theme.update(self.folder, name=name, file_name=name, background_mode=self.mode.value())
        done = theme.build(self.folder, self.folder / "build", self.cfg["theme_maker"]["ctf_magic"])
        names = " + ".join(f.name for f in done)
        if self.dev:
            dest = theme.install(done, self.dev)
            self._say(f"COPIED {names} TO {dest.relative_to(self.dev.root).as_posix()} · SET IT IN THEME SETTINGS")
        else:
            self._say(f"BUILT {names} IN {self.folder / 'build'} · PLUG IN THE PSP AND BUILD AGAIN TO COPY IT")


class _Feature:
    id, title = "themes", "THEMES"

    def page(self, cfg):
        return ThemesPage(cfg)


FEATURE = _Feature()
