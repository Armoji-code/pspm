import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from pspm import config
from pspm.ui import app


def test_window_builds_without_psp(tmp_path, monkeypatch):
    monkeypatch.setattr(app.device, "find", lambda cfg: [])
    QApplication.instance() or QApplication([])
    w = app.Main(config.load(tmp_path / "x"))
    assert w.hero.text() == "NO PSP" and w.nav.count() == 1
