from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import files
from ..ui.fmt import size


class FilesPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.dev = None
        self.cats = QListWidget()
        self.cats.addItems(cfg["device"]["folders"].keys())
        self.cats.currentTextChanged.connect(self.refresh)
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["NAME", "SIZE"])
        self.items.setRootIsDecorated(False)
        self.items.setColumnWidth(0, 420)
        self.status = QLabel()
        self.status.setObjectName("label")
        add, rm = QPushButton("ADD FILES"), QPushButton("DELETE")
        rm.setObjectName("danger")
        add.clicked.connect(self.add)
        rm.clicked.connect(self.delete)

        right = QVBoxLayout()
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

    def set_device(self, dev):
        self.dev = dev
        self.refresh()

    def refresh(self, *_):
        self.items.clear()
        cat = self.cats.currentItem() and self.cats.currentItem().text()
        if not (self.dev and cat):
            self.status.setText("[NO PSP CONNECTED]")
            return
        for e in files.list_items(self.dev, cat):
            QTreeWidgetItem(self.items, [e.name + ("/" if e.is_dir else ""), size(e.size)])
        self.status.setText(str(self.dev.path(cat)).upper())

    def _run(self, fn, *args):
        try:
            fn(self.dev, self.cats.currentItem().text(), *args)
        except (OSError, ValueError) as e:
            self.status.setText(f"[ERROR: {e}]")
            return
        self.refresh()

    def add(self):
        if self.dev:
            paths, _ = QFileDialog.getOpenFileNames(self, "Add to PSP")
            if paths:
                self._run(files.copy_in, [Path(p) for p in paths])

    def delete(self):
        sel = self.items.currentItem()
        if not (self.dev and sel):
            return
        name = sel.text(0).rstrip("/")
        if QMessageBox.question(self, "Delete", f"Delete {name} from the PSP? This cannot be undone.") == QMessageBox.Yes:
            self._run(files.remove, name)


class _Feature:
    id, title = "files", "FILES"

    def page(self, cfg):
        return FilesPage(cfg)


FEATURE = _Feature()
