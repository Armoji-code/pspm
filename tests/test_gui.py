import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from pspm import config
from pspm.ui import app


def test_window_builds_without_psp(tmp_path, monkeypatch):
    monkeypatch.setattr(app.device, "find", lambda cfg: [])
    QApplication.instance() or QApplication([])
    w = app.Main(config.load(tmp_path / "x"))
    assert w.hero.text() == "NO PSP" and w.nav.count() == 3


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


def test_saves_page_auto_syncs_on_plug_in(tmp_path):
    from pspm import device, saves
    from pspm.features.saves import SavesPage

    cfg = config.load(tmp_path / "x")
    (tmp_path / "psp" / "PSP" / "SAVEDATA" / "ULUS1").mkdir(parents=True)
    (tmp_path / "psp" / "PSP" / "SAVEDATA" / "ULUS1" / "DATA.BIN").write_bytes(b"save")
    saves.save_state({"ppsspp": str(tmp_path / "pc"), "last": {}}, tmp_path / "s.json")
    app = QApplication.instance() or QApplication([])
    p = SavesPage(cfg, tmp_path / "s.json", tmp_path / "bk")
    p.set_device(device.Device(tmp_path / "psp", cfg["device"]["folders"]))
    for _ in range(2):  # check, then the sync it starts
        p.job.wait()
        app.processEvents()
    assert (tmp_path / "pc" / "ULUS1" / "DATA.BIN").read_bytes() == b"save"
    assert p.status.text() == "SYNCED 1 SAVES"
