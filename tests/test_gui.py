import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from pspm import config
from pspm.ui import app


def test_window_builds_without_psp(tmp_path, monkeypatch):
    monkeypatch.setattr(app.device, "find", lambda cfg: [])
    QApplication.instance() or QApplication([])
    w = app.Main(config.load(tmp_path / "x"))
    assert w.hero.text() == "NO PSP" and w.nav.count() == 9


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


def test_backup_page_backs_up_on_plug_in(tmp_path, monkeypatch):
    from pspm import backup, device
    from pspm.features.backup import BackupPage

    got = {}

    class Fake(backup.Provider):
        title = "FAKE"

        def __init__(self, pcfg, token):
            assert token == "T"

        def put(self, path, f, size):
            got[path] = f.read()

    monkeypatch.setitem(backup.BUILTIN, "fake", Fake)
    monkeypatch.setattr(backup, "token", lambda pid: "T")
    cfg = config.load(tmp_path / "x")
    cfg["backup"]["provider"] = "fake"
    (tmp_path / "psp" / "PSP" / "SAVEDATA" / "ULUS1").mkdir(parents=True)
    (tmp_path / "psp" / "PSP" / "SAVEDATA" / "ULUS1" / "DATA.BIN").write_bytes(b"save")
    app = QApplication.instance() or QApplication([])
    p = BackupPage(cfg, tmp_path / "b.json")
    p.set_device(device.Device(tmp_path / "psp", cfg["device"]["folders"]))
    p.job.wait()
    app.processEvents()
    assert got == {"PSPM/PSP/SAVEDATA/ULUS1/DATA.BIN": b"save"} and p.status.text() == "BACKED UP 1 FILES"


def test_diag_page_shows_info_and_pending_test(tmp_path):
    from pspm import device
    from pspm.features.diag import DiagPage

    cfg = config.load(tmp_path / "x")
    (tmp_path / "PSP" / "COMMON").mkdir(parents=True)
    (tmp_path / "PSP" / "COMMON" / "pspm_info.txt").write_text("firmware=6.61\n")
    QApplication.instance() or QApplication([])
    p = DiagPage(cfg)
    p.set_device(device.Device(tmp_path, cfg["device"]["folders"]))
    shown = [p.grid.itemAt(i).widget().text() for i in range(p.grid.count())]
    assert "FIRMWARE" in shown and "6.61" in shown and p.test.text() == "TEST CAPACITY"
    (tmp_path / "PSPM_TEST").mkdir()
    p.set_device(device.Device(tmp_path, cfg["device"]["folders"]))
    assert p.test.text() == "VERIFY CAPACITY"


def test_ps1_page_converts_and_reports(tmp_path, monkeypatch):
    from pspm import device, ps1
    from pspm.features.ps1 import Ps1Page

    got = {}

    def fake(discs, games, cfg, icon):
        got.update(discs=discs, games=games)
        return games / "SLUS00594"

    monkeypatch.setattr(ps1, "convert", fake)
    monkeypatch.setattr(ps1, "title", lambda pbp: "Metal Gear Solid")
    cfg = config.load(tmp_path / "x")
    app = QApplication.instance() or QApplication([])
    p = Ps1Page(cfg)
    p.set_device(device.Device(tmp_path, cfg["device"]["folders"]))
    p.discs.addItems(["d1.cue", "d2.cue"])
    p.convert()
    p.job.wait()
    app.processEvents()
    assert got == {"discs": [Path("d1.cue"), Path("d2.cue")], "games": tmp_path / "PSP" / "GAME"}
    assert p.status.text() == "INSTALLED METAL GEAR SOLID TO PSP/GAME/SLUS00594" and not p.discs.count()


def test_stream_page_installs_plugin(tmp_path, monkeypatch):
    from pspm import device, stream
    from pspm.features.stream import StreamPage

    (tmp_path / "rjl" / "psp").mkdir(parents=True)
    (tmp_path / "rjl" / "psp" / "RemoteJoyLite.prx").write_bytes(b"prx")
    monkeypatch.setattr(stream, "tool", lambda cfg: tmp_path / "rjl")
    cfg = config.load(tmp_path / "x")
    app = QApplication.instance() or QApplication([])
    p = StreamPage(cfg)
    p.set_device(device.Device(tmp_path / "psp", cfg["device"]["folders"]))
    assert p.status.text() == "PLUGIN NOT INSTALLED"
    p.install()
    p.job.wait()
    app.processEvents()
    assert (tmp_path / "psp" / "SEPLUGINS" / "RemoteJoyLite.prx").read_bytes() == b"prx"
    assert p.status.text().startswith("PLUGIN INSTALLED")


def test_softmod_page_needs_boxes_then_copies(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from pspm import device, softmod
    from pspm.features.softmod import SoftmodPage

    cfg = config.load(tmp_path / "x")
    for src in cfg["softmod"]["ark5"]["copy"] | cfg["softmod"]["ark5"]["cipl"]:
        (tmp_path / "ark" / src).mkdir(parents=True)
        (tmp_path / "ark" / src / "EBOOT.PBP").write_bytes(b"ark")
    monkeypatch.setattr(softmod, "tool", lambda cfg, ark: tmp_path / "ark")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]) or QMessageBox.Yes)
    (tmp_path / "psp" / "PSP" / "COMMON").mkdir(parents=True)
    (tmp_path / "psp" / "PSP" / "COMMON" / "pspm_info.txt").write_text("firmware=6.61\n")
    app = QApplication.instance() or QApplication([])
    p = SoftmodPage(cfg, tmp_path / "bk")
    p.set_device(device.Device(tmp_path / "psp", cfg["device"]["folders"]))
    assert p.ark.currentData() == "ark5" and p.fw.isHidden() and "6.61" in p.known.text()
    p.install()
    assert p.status.text() == "[TICK EVERY BOX ABOVE FIRST]" and not p.job
    p.battery.setChecked(True)
    p.risk.setChecked(True)
    assert not p.cipl.isChecked()
    p.cipl.setChecked(True)
    p.install()
    for _ in range(2):  # check, then the copy it starts
        p.job.wait()
        app.processEvents()
    assert (tmp_path / "psp" / "PSP" / "GAME" / "FasterARK" / "EBOOT.PBP").read_bytes() == b"ark"
    assert p.status.text().startswith("COPIED AND VERIFIED") and "flash" in warned[0]
    assert (tmp_path / "psp" / "PSP" / "GAME" / "CustomIPL" / "EBOOT.PBP").exists()


def test_themes_page_new_and_build_copies_to_psp(tmp_path):
    from pspm import device
    from pspm.features.themes import ThemesPage

    cfg = config.load(tmp_path / "x")
    (tmp_path / "psp" / "PSP").mkdir(parents=True)
    QApplication.instance() or QApplication([])
    p = ThemesPage(cfg, work=tmp_path / "work")
    p.new()
    assert p.items.count() == 74 and p.folder == tmp_path / "work" / "my-theme"
    p.name.setText("Night")
    p.set_device(device.Device(tmp_path / "psp", cfg["device"]["folders"]))
    p.build()
    assert (tmp_path / "psp" / "PSP" / "THEME" / "Night.ptf").is_file(), p.status.text()


def test_files_page_tabs_and_double_click(tmp_path):
    from pspm import device
    from pspm.features.files import FilesPage

    cfg = config.load(tmp_path / "x")
    (tmp_path / "PSP" / "GAME" / "CAT_PS1" / "MGS").mkdir(parents=True)
    (tmp_path / "PSP" / "GAME" / "Loose").mkdir()
    QApplication.instance() or QApplication([])
    p = FilesPage(cfg)
    p.set_device(device.Device(tmp_path, cfg["device"]["folders"]))
    names = lambda: [p.items.topLevelItem(i).text(0) for i in range(p.items.topLevelItemCount())]
    assert [p.tabs.tabText(i) for i in range(p.tabs.count())] == ["uncategorized", "PS1"] and names() == ["Loose/"]
    p.tabs.setCurrentIndex(1)
    assert names() == ["MGS/"]
    p.enter(p.items.topLevelItem(0))
    assert names() == ["../"] and p.sub == "CAT_PS1/MGS"
    p.enter(p.items.topLevelItem(0))
    assert names() == ["MGS/"]
