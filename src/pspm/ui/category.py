from PySide6.QtWidgets import QComboBox, QInputDialog

from .. import files


class CategoryPicker(QComboBox):
    """The CAT_ folder of the games folder a page installs into. Each page remembers its own pick."""

    def __init__(self, cfg, page):
        super().__init__()
        self.cfg, self.page, self.dev = cfg, page, None
        self.setToolTip("CAT_ FOLDER TO INSTALL INTO")
        self.activated.connect(self._picked)
        self.set_device(None)

    def folder(self) -> str:
        return self.currentData() or ""

    def set_device(self, dev):
        self.dev = dev
        keep = files.chosen(self.page)
        found = {*(files.categories(dev, self.cfg) if dev else []), *([keep] if keep else [])}
        self.clear()
        self.addItem("NO CATEGORY", "")
        for cat in sorted(found, key=str.lower):
            self.addItem(cat, cat)
        self.addItem("NEW CATEGORY…")  # no data: asks for a name
        self.setCurrentIndex(max(self.findData(keep), 0))

    def _picked(self, i):
        cat = self.itemData(i)
        if cat is None:
            name, ok = QInputDialog.getText(self, "Category", "New category folder, e.g. Tools (becomes CAT_Tools):")
            name, prefix = name.strip().strip("/\\"), self.cfg["files"]["tabs"]["games"]
            if not (ok and name):
                return self.set_device(self.dev)
            cat = name if name.startswith(prefix) else prefix + name
        files.choose(self.page, cat)
        self.set_device(self.dev)
