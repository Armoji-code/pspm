from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QMimeData, QObject, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from .saves import _Job


def _image(src, box: QSize):
    """Image file or data, decoded straight to thumbnail size (JPEG art skips most of the work), or None."""
    buf = None
    if isinstance(src, bytes):
        buf = QBuffer()
        buf.setData(QByteArray(src))
    r = QImageReader(buf) if buf else QImageReader(src)
    r.setAutoTransform(True)
    if r.size().isValid():
        r.setScaledSize(r.size().scaled(box, Qt.KeepAspectRatio))
    img = r.read()
    return None if img.isNull() else img


class Thumbs(QObject):
    """Thumbnails and folder sizes, read and scaled off the UI thread for the rows asked for, cached by path+mtime."""

    ready = Signal(str, object, object)  # path, QImage or None, folder size or None

    def __init__(self, box: QSize, parent):
        super().__init__(parent)
        self.box, self.cache, self.pool = box, {}, QThreadPool(self)
        self.pool.setMaxThreadCount(2)  # one memory stick: more readers only make it seek more

    def want(self, paths):
        self.pool.clear()  # rows queued earlier have scrolled away
        for p in paths:
            self.pool.start(lambda p=p: self._load(p))

    def _load(self, p: Path):
        try:
            key = (p, p.stat().st_mtime_ns)
            got = self.cache.get(key)
            if not got:
                if len(self.cache) > 3000:  # shortcut: drop everything when full, an LRU if people browse huge sticks
                    self.cache.clear()
                src = files.thumb(p)
                got = self.cache[key] = (src and _image(src, self.box), files.size_of(p) if p.is_dir() else None)
        except Exception:  # noqa: BLE001 - unplugged mid-read or a broken file: the row just keeps no thumbnail
            return
        self.ready.emit(str(p), *got)


def _accept(e):
    e.acceptProposedAction() if e.mimeData().hasUrls() else e.ignore()


class _Tree(QTreeWidget):
    """Drag items out to the PC's file manager (copies); drop files in, or items onto a folder (moves)."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def supportedDropActions(self):
        return Qt.CopyAction  # a "move" out would make Qt drop the rows; the files stay either way

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, items):
        m = QMimeData()
        m.setUrls([QUrl.fromLocalFile(str(p)) for p in map(self.page.path_of, items) if p])
        return m

    dragEnterEvent = dragMoveEvent = lambda self, e: _accept(e)

    def dropEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        data = it and it.data(0, Qt.UserRole)
        self.page.drop(e, data[1] if data and data[2] else self.page.sub)


class _Tabs(QTabBar):
    """Drop onto a tab to put files (or move items) into that category folder."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setAcceptDrops(True)

    dragEnterEvent = dragMoveEvent = lambda self, e: _accept(e)

    def dropEvent(self, e):
        i = self.tabAt(e.position().toPoint())
        if i >= 0:
            self.page.drop(e, self.tabData(i) or "")


class FilesPage(QWidget):
    UP = None  # item data of the "../" row; others hold (label, path in label, is_dir)

    def __init__(self, cfg):
        super().__init__()
        self.dev, self.fcfg, self.sub, self.job = None, cfg["files"], "", None
        self.cats = QListWidget()
        self.cats.addItems(cfg["device"]["folders"].keys())
        self.cats.currentTextChanged.connect(self.open_cat)
        self.tabs = _Tabs(self)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(self.open_tab)
        self.items = _Tree(self)
        self.items.setHeaderLabels(["NAME", "SIZE"])
        self.items.setRootIsDecorated(False)
        self.items.setColumnWidth(0, 420)
        self.items.setIconSize(QSize(90, 50))  # ICON0 is 144x80
        self.items.itemDoubleClicked.connect(self.enter)
        self.rows, self.thumbs = {}, Thumbs(self.items.iconSize(), self)
        self.thumbs.ready.connect(self.show_thumb)
        self.later = QTimer(self, singleShot=True, interval=50, timeout=self.load_visible)  # once scrolling settles
        self.items.verticalScrollBar().valueChanged.connect(self.later.start)
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

    def labels(self):
        """The category's own folder, then the folders merged into it."""
        return [self.cat(), *self.fcfg.get("merge", {}).get(self.cat(), [])]

    def set_device(self, dev):
        self.dev = dev
        self.open_cat()

    def open_cat(self, *_):
        """Rebuild the tabs for the category: a plain tab, then one per tab folder in any of its folders."""
        prefix = self.fcfg["tabs"].get(self.cat())
        found = sorted({t for lb in self.labels() for t in files.tabs(self.dev, lb, prefix)}, key=str.lower) \
            if self.dev and prefix is not None else []
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
        data = item.data(0, Qt.UserRole)
        if data is self.UP:
            up = Path(self.sub).parent.as_posix()
            self.sub = "" if up == "." else up
        elif data[2]:
            self.sub = data[1]
        else:
            return
        self.refresh()

    def path_of(self, item):
        data = item.data(0, Qt.UserRole)
        return data and self.dev and self.dev.path(data[0]) / data[1]

    def refresh(self, *_):
        self.items.clear()
        self.rows = {}
        cat = self.cat()
        if not (self.dev and cat):
            self.status.setText("[NO PSP CONNECTED]")
            return
        top = self.tabs.tabData(self.tabs.currentIndex()) or ""
        if self.sub != top:
            QTreeWidgetItem(self.items, ["../", ""]).setData(0, Qt.UserRole, self.UP)
        hide = () if self.sub else [self.tabs.tabData(i) for i in range(1, self.tabs.count())]
        found = [(lb, e) for lb in self.labels() for e in files.list_items(self.dev, lb, self.sub, hide)]
        for lb, e in sorted(found, key=lambda f: ((f[1].title or f[1].name).lower(), f[1].name.lower())):
            it = QTreeWidgetItem(self.items, [(e.title or e.name) + ("/" if e.is_dir else ""),
                                              "" if e.size is None else size(e.size)])
            it.setData(0, Qt.UserRole, (lb, (Path(self.sub) / e.name).as_posix(), e.is_dir))
            self.rows[str(self.path_of(it))] = it
            if e.title:
                it.setToolTip(0, e.name)
        self.later.start()
        self.status.setText((self.dev.path(cat) / self.sub).relative_to(self.dev.root).as_posix().upper())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.later.start()

    def load_visible(self):
        """Ask for the thumbnails of the rows on screen and one screen below."""
        view, it, want = self.items.viewport(), self.items.itemAt(0, 0), []
        while it and self.items.visualItemRect(it).top() < 2 * view.height():
            if (p := self.path_of(it)) and not it.data(1, Qt.UserRole):
                want.append(p)
            it = self.items.itemBelow(it)
        self.thumbs.want(want)

    def show_thumb(self, path, img, folder_size):
        if not (it := self.rows.get(path)):
            return  # from a folder no longer shown
        it.setData(1, Qt.UserRole, True)  # loaded: scrolling past again asks for nothing
        if img:
            it.setIcon(0, QIcon(QPixmap.fromImage(img)))
        if folder_size is not None:
            it.setText(1, size(folder_size))

    def _work(self, fn, then=None):
        """Copies and deletes run off the UI thread: big ISOs over USB take a while."""
        if self.job and self.job.isRunning():
            self.status.setText("[BUSY: WAIT FOR THE LAST COPY TO FINISH]")
            return
        self.status.setText("[WORKING…]")
        self.job = _Job(fn, self)
        self.job.done.connect(self.refresh)
        self.job.failed.connect(lambda e: (self.refresh(), self.status.setText(f"[ERROR: {e}]")))
        self.job.start()

    def place(self, paths, sub, move=False):
        """Copy (or move, for items from this list) into sub, each into the folder its type belongs in."""
        groups = {}
        for p in paths:
            groups.setdefault(files.route(self.dev, self.labels(), p, self.fcfg.get("types", {})), []).append(p)
        self._work(lambda: [files.copy_in(self.dev, lb, ps, sub, move) for lb, ps in groups.items()])

    def drop(self, e, sub):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        if self.dev and paths:
            self.place(paths, sub, move=e.source() is self.items)

    def add(self):
        if self.dev:
            paths, _ = QFileDialog.getOpenFileNames(self, "Add to PSP")
            if paths:
                self.place([Path(p) for p in paths], self.sub)

    def delete(self):
        sel = [it.data(0, Qt.UserRole) for it in self.items.selectedItems()]
        sel = [d for d in sel if d is not self.UP]
        if not (self.dev and sel):
            return
        what = Path(sel[0][1]).name if len(sel) == 1 else f"{len(sel)} items"
        if QMessageBox.question(self, "Delete", f"Delete {what} from the PSP? This cannot be undone.") == QMessageBox.Yes:
            self._work(lambda: [files.remove(self.dev, lb, name) for lb, name, _ in sel])


class _Feature:
    id, title = "files", "FILES"

    def page(self, cfg):
        return FilesPage(cfg)


FEATURE = _Feature()
