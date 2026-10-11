import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from pspm import config
from pspm.ui import app


def test_window_builds_without_psp(tmp_path, monkeypatch):
    monkeypatch.setattr(app.device, "find", lambda cfg: [])
    QApplication.instance() or QApplication([])
    w = app.Main(config.load(tmp_path / "x"))
    assert w.hero.text() == "NO PSP" and w.nav.count() == 2


def test_tagger_page_edit_save_sort(tmp_path):
    from pspm import tagger
    from pspm.features.tagger import TaggerPage

    (tmp_path / "Queen - Song.mp3").write_bytes((b"\xff\xfb\x90\x00" + b"\x00" * 413) * 10)  # silent MP3 frames
    QApplication.instance() or QApplication([])
    p = TaggerPage(config.load(tmp_path / "x"))
    p.load(tmp_path)
    assert p.table.item(0, 1).text() == "Queen"
    p.table.item(0, 3).setText("Album")
    p.save()
    assert tagger.read(tmp_path / "Queen - Song.mp3").album == "Album"
    p.sort()
    assert (tmp_path / "Album" / "Queen - Song.mp3").exists()
