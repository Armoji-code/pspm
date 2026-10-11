import struct

import pytest

from pspm import config, device, softmod


def _setup(tmp_path):
    cfg = config.load(tmp_path / "x")
    home = tmp_path / "ark"
    for src in cfg["softmod"]["ark4"]["copy"]:
        (home / src).mkdir(parents=True)
        (home / src / "EBOOT.PBP").write_bytes(src.encode())
    (home / "PSP" / "ARK_cIPL").mkdir(parents=True)
    (home / "PSP" / "ARK_cIPL" / "EBOOT.PBP").write_bytes(b"flash")
    (tmp_path / "psp" / "PSP").mkdir(parents=True)
    return cfg, device.Device(tmp_path / "psp", cfg["device"]["folders"]), home


def test_install_copies_only_listed_folders_and_backs_up(tmp_path):
    cfg, dev, home = _setup(tmp_path)
    todo = softmod.files(cfg, "ark4", home)
    assert {rel for _, rel in todo} == {"PSP/SAVEDATA/ARK_01234/EBOOT.PBP", "PSP/GAME/ARK_Loader/EBOOT.PBP"}
    assert softmod.install(dev, cfg, "ark4", todo, tmp_path / "bk") is None
    (dev.root / "PSP/GAME/ARK_Loader/EBOOT.PBP").write_bytes(b"mine")
    (dev.root / "PSP/GAME/ARK_Loader/OLD.PRX").write_bytes(b"other version")
    assert softmod.existing(dev, cfg, "ark4") == ["PSP/SAVEDATA/ARK_01234", "PSP/GAME/ARK_Loader"]
    kept = softmod.install(dev, cfg, "ark4", todo, tmp_path / "bk")
    assert (kept / "PSP/GAME/ARK_Loader/EBOOT.PBP").read_bytes() == b"mine"
    assert (dev.root / "PSP/GAME/ARK_Loader/EBOOT.PBP").read_bytes() == b"ARK_Loader"
    assert not (dev.root / "PSP/GAME/ARK_Loader/OLD.PRX").exists() and (kept / "PSP/GAME/ARK_Loader/OLD.PRX").exists()
    assert not list(dev.root.rglob("ARK_cIPL"))
    softmod.install(dev, cfg, "ark4", softmod.files(cfg, "ark4", home, cipl=True), tmp_path / "bk", cipl=True)
    assert (dev.root / "PSP/GAME/ARK_cIPL/EBOOT.PBP").read_bytes() == b"flash"


def test_problems_block_wrong_firmware_and_full_stick(tmp_path, monkeypatch):
    cfg, dev, home = _setup(tmp_path)
    todo = softmod.files(cfg, "ark4", home)
    assert softmod.problems(dev, cfg, todo, {}) == []
    assert softmod.problems(dev, cfg, todo, {"firmware": "6.61"}) == []
    assert "6.39" in softmod.problems(dev, cfg, todo, {"firmware": "6.39"})[0]
    assert "40%" in softmod.problems(dev, cfg, todo, {"battery": "40%", "charger": "not connected"})[0]
    assert softmod.problems(dev, cfg, todo, {"battery": "40%", "charger": "connected"}) == []
    assert softmod.problems(dev, cfg, todo, {"battery": "none"}) == []
    monkeypatch.setattr(device.Device, "usage", lambda self: type("U", (), {"free": 0})())
    assert "free" in softmod.problems(dev, cfg, todo, {})[0]


def test_missing_folder_in_download_is_an_error(tmp_path):
    cfg, _, home = _setup(tmp_path)
    with pytest.raises(FileNotFoundError):
        softmod.files(cfg, "ark5", home)


def _sfo(title: bytes) -> bytes:
    keys, data = 20 + 16, 20 + 16 + 8
    return (b"\0PSF" + struct.pack("<IIII", 0x101, keys, data, 1) + struct.pack("<HHIII", 0, 0x204, len(title) + 1, 32, 0)
            + b"TITLE\0\0\0" + title.ljust(32, b"\0"))


def test_installed_reads_ark_from_its_save(tmp_path):
    cfg, dev, _ = _setup(tmp_path)
    assert softmod.installed(dev) is None
    sfo = dev.root / "PSP/SAVEDATA/ARK_01234/PARAM.SFO"
    sfo.parent.mkdir(parents=True)
    sfo.write_bytes(_sfo(b"ARK-4"))
    assert softmod.installed(dev) == "ARK-4"
    sfo.write_bytes(b"junk")
    assert softmod.installed(dev) == "ARK"
    assert softmod.folders(cfg, "update") == {"PSP/GAME/UPDATE": "PSP/GAME/UPDATE"}
