from dataclasses import replace
from pathlib import Path
from urllib.error import URLError

from PySide6.QtCore import Qt, QThread, Signal
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

from .. import tagger

COLS = ["artist", "title", "album", "genre"]


class _Finder(QThread):
    found = Signal(int, object, object)  # row, Track | None, cover bytes | None
    failed = Signal(str)

    def __init__(self, cfg, tracks, parent):
        super().__init__(parent)  # parented so a dropped lookup isn't destroyed mid-run
        self.look, self.tracks = tagger.Lookup(cfg), tracks

    def run(self):
        for i, t in enumerate(self.tracks):
            if self.isInterruptionRequested():
                return
            try:
                m = self.look.find(t)
                self.found.emit(i, m, self.look.cover(m.release_id) if m and m.release_id and not t.has_cover else None)
            except (URLError, OSError, ValueError) as e:
                self.failed.emit(tagger.friendly(e))
                return


class TaggerPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.dev, self.folder, self.tracks = cfg, None, None, []
        self.covers, self.dirty, self.finder = {}, set(), None
        self.table = QTableWidget(0, len(COLS) + 2)
        self.table.setHorizontalHeaderLabels(["FILE", *(c.upper() for c in COLS), "COVER"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(lambda it: self.dirty.add(it.row()))
        self.status = QLabel()
        self.status.setObjectName("label")
        self.sort_by = QComboBox()
        self.sort_by.addItems(["album", "artist", "genre"])
        self.sort_by.setCurrentText(cfg["tagger"]["sort_by"])
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("FOLDER", self.pick), ("FIND TAGS", self.find), ("SAVE", self.save)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addWidget(self.sort_by)
        sort = QPushButton("SORT")
        sort.clicked.connect(self.sort)
        bar.addWidget(sort)
        col = QVBoxLayout(self)
        col.addWidget(self.table)
        col.addLayout(bar)
        self.load(None)

    def set_device(self, dev):
        self.dev = dev
        if dev and not self.folder:
            self.load(dev.path("music"))

    def pick(self):
        d = QFileDialog.getExistingDirectory(self, "Music folder", str(self.folder or ""))
        if d:
            self.load(Path(d))

    def load(self, folder):
        if self.finder:  # results from a running lookup would land on the wrong rows
            self.finder.found.disconnect()
            self.finder.finished.disconnect()
            self.finder.requestInterruption()
            self.finder = None
        self.folder = folder if folder and folder.is_dir() else None
        self.tracks = tagger.scan(self.folder) if self.folder else []
        self.covers, self.dirty = {}, set()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.tracks))
        for r, t in enumerate(self.tracks):
            self._row(r, t)
        self.table.blockSignals(False)
        self.status.setText(f"{len(self.tracks)} SONGS  ·  {self.folder}".upper() if self.folder else "[PICK A FOLDER]")

    def _row(self, r, t, cover=None):
        name = QTableWidgetItem(str(t.path.relative_to(self.folder)))
        name.setFlags(name.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 0, name)
        for c, f in enumerate(COLS, 1):
            self.table.setItem(r, c, QTableWidgetItem(getattr(t, f)))
        has = QTableWidgetItem("NEW" if cover else "YES" if t.has_cover else "—")
        has.setFlags(has.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, len(COLS) + 1, has)

    def _track(self, r):
        return replace(self.tracks[r], **{f: self.table.item(r, c).text().strip() for c, f in enumerate(COLS, 1)})

    def find(self):
        if not self.tracks or (self.finder and self.finder.isRunning()):
            return
        self.finder = _Finder(self.cfg, [self._track(r) for r in range(len(self.tracks))], self)
        self.finder.found.connect(self._found)
        self.finder.failed.connect(lambda e: self.status.setText(f"[LOOKUP STOPPED: {e}]"))
        self.finder.finished.connect(self._done)
        self.finder.start()

    def _found(self, r, match, cover):
        self.status.setText(f"LOOKING UP {r + 1}/{len(self.tracks)}")
        if not match:
            return
        if cover:
            self.covers[r] = cover
        self._row(r, match, cover)  # itemChanged marks the row dirty, so SAVE picks it up

    def _done(self):
        if not self.status.text().startswith("["):  # keep an error on screen
            self.status.setText(f"LOOKUP DONE  ·  {len(self.dirty)} SONGS TO SAVE")

    def save(self):
        try:
            for r in sorted(self.dirty):
                tagger.write(self._track(r), self.covers.get(r))
        except OSError as e:
            self.status.setText(f"[ERROR: {e}]")
            return
        n = len(self.dirty)
        self.load(self.folder)
        self.status.setText(f"SAVED {n} SONGS")

    def sort(self):
        if self.dirty:
            self.status.setText("[SAVE FIRST]")
            return
        try:
            n = len(tagger.sort_into(self.tracks, self.folder, self.sort_by.currentText()))
        except OSError as e:
            self.status.setText(f"[ERROR: {e}]")
            return
        self.load(self.folder)
        self.status.setText(f"MOVED {n} SONGS")


class _Feature:
    id, title = "tagger", "MP3 TAGGER"

    def page(self, cfg):
        return TaggerPage(cfg)


FEATURE = _Feature()
