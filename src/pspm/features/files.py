from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import files
from ..ui.fmt import size


class FilesPage(QWidget):
    UP = ".."

    def __init__(self, cfg):
        super().__init__()
        self.dev, self.fcfg, self.sub = None, cfg["files"], ""
        self.cats = QListWidget()
        self.cats.addItems(cfg["device"]["folders"].keys())
        self.cats.currentTextChanged.connect(self.open_cat)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(self.open_tab)
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["NAME", "SIZE"])
        self.items.setRootIsDecorated(False)
        self.items.setColumnWidth(0, 420)
        self.items.setIconSize(QSize(72, 40))  # half of a 144x80 ICON0
        self.items.itemDoubleClicked.connect(self.enter)
        self.status = QLabel()
        self.status.setObjectName("label")
        self.status.setMinimumWidth(1)  # long paths get cut instead of squeezing the category list
        add, rm = QPushButton("ADD FILES"), QPushButton("DELETE")
        rm.setObjectName("danger")
        add.clicked.connect(self.add)
        rm.clicked.connect(self.delete)

        right = QVBoxLayout()
        right.addWidget(self.tabs)
        right.addWidget(self.items)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        bar.addWidget(add)
        bar.addWidget(rm)
        right.addLayout(bar)
        row = QHBoxLayout(self)
        row.addWidget(self.cats, 1)
        row.addLayout(right, 4)
        self.cats.setCurrentRow(0)

    def cat(self):
        return self.cats.currentItem() and self.cats.currentItem().text()

    def set_device(self, dev):
        self.dev = dev
        self.open_cat()

    def open_cat(self, *_):
        """Rebuild the tabs for the category: a plain tab, then one per tab folder."""
        prefix = self.fcfg["tabs"].get(self.cat())
        found = files.tabs(self.dev, self.cat(), prefix) if self.dev and prefix is not None else []
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for name in [""] + found:
            self.tabs.setTabData(self.tabs.addTab(name[len(prefix):] if name else self.fcfg["plain_tab"]), name)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.tabs.setVisible(bool(found))
        self.open_tab()

    def open_tab(self, *_):
        self.sub = self.tabs.tabData(self.tabs.currentIndex()) or ""
        self.refresh()

    def enter(self, item):
        name = item.data(0, Qt.UserRole)
        if name == self.UP:
            up = Path(self.sub).parent.as_posix()
            self.sub = "" if up == "." else up
        elif name.endswith("/"):
            self.sub = (Path(self.sub) / name.rstrip("/")).as_posix()
        else:
            return
        self.refresh()

    def refresh(self, *_):
        self.items.clear()
        cat = self.cat()
        if not (self.dev and cat):
            self.status.setText("[NO PSP CONNECTED]")
            return
        top = self.tabs.tabData(self.tabs.currentIndex()) or ""
        if self.sub != top:
            QTreeWidgetItem(self.items, [self.UP + "/", ""]).setData(0, Qt.UserRole, self.UP)
        hide = () if self.sub else [self.tabs.tabData(i) for i in range(1, self.tabs.count())]
        for e in files.list_items(self.dev, cat, self.sub, hide):
            it = QTreeWidgetItem(self.items, [(e.title or e.name) + ("/" if e.is_dir else ""), size(e.size)])
            it.setData(0, Qt.UserRole, e.name + ("/" if e.is_dir else ""))
            if e.title:
                it.setToolTip(0, e.name)
            if e.icon:
                it.setIcon(0, QIcon(e.icon))
        self.status.setText((self.dev.path(cat) / self.sub).relative_to(self.dev.root).as_posix().upper())

    def _run(self, fn, *args):
        try:
            fn(self.dev, self.cat(), *args)
        except (OSError, ValueError) as e:
            self.status.setText(f"[ERROR: {e}]")
            return
        self.refresh()

    def add(self):
        if self.dev:
            paths, _ = QFileDialog.getOpenFileNames(self, "Add to PSP")
            if paths:
                self._run(files.copy_in, [Path(p) for p in paths], self.sub)

    def delete(self):
        sel = self.items.currentItem()
        if not (self.dev and sel) or sel.data(0, Qt.UserRole) == self.UP:
            return
        name = sel.data(0, Qt.UserRole).rstrip("/")
        ask = f"Delete {sel.text(0).rstrip('/')} from the PSP? This cannot be undone."
        if QMessageBox.question(self, "Delete", ask) == QMessageBox.Yes:
            self._run(files.remove, (Path(self.sub) / name).as_posix())


class _Feature:
    id, title = "files", "FILES"

    def page(self, cfg):
        return FilesPage(cfg)


FEATURE = _Feature()
