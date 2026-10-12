from pspm import device, diag


def test_capacity_test_catches_bad_blocks(tmp_path):
    folder, never = tmp_path / "t", lambda: False
    assert diag.fill(folder, 5 * diag.BLOCK + 123, 2, lambda d, t: None, never) == 5 * diag.BLOCK
    assert sorted(f.name for f in folder.iterdir()) == ["0001.bin", "0002.bin", "0003.bin"]
    assert diag.verify(folder, lambda d, t: None, never) == (5 * diag.BLOCK, 0)

    # A fake card wraps around: the last block reads back as the first one.
    first = (folder / "0001.bin").read_bytes()[: diag.BLOCK]
    with open(folder / "0003.bin", "r+b") as f:
        f.write(first)
    assert diag.verify(folder, lambda d, t: None, never) == (4 * diag.BLOCK, diag.BLOCK)
    diag.clean(folder)
    assert not folder.exists()


def test_reads_info_written_by_psp_app(tmp_path):
    from pspm import config

    cfg = config.load(tmp_path / "x")
    dev = device.Device(tmp_path, cfg["device"]["folders"])
    assert diag.read_info(dev, cfg) == {}
    p = tmp_path / cfg["diag"]["info_file"]
    p.parent.mkdir(parents=True)
    p.write_text("model=04g\nfirmware=6.61\nbattery=87%\n")
    info = diag.read_info(dev, cfg)
    assert info["model"] == "PSP-3000 (04g)" and info["firmware"] == "6.61" and info["battery"] == "87%"


def test_installs_bundled_psp_app(tmp_path):
    from pspm import config

    cfg = config.load(tmp_path / "x")
    dest = diag.install_app(device.Device(tmp_path, cfg["device"]["folders"]), cfg)
    assert (dest / "EBOOT.PBP").read_bytes()[:4] == b"\0PBP"


def test_decodes_cfw_and_settings(tmp_path):
    from pspm import config

    cfg = config.load(tmp_path / "x")
    p = tmp_path / cfg["diag"]["info_file"]
    p.parent.mkdir(parents=True)
    p.write_text("model=01g\ncfw_hen=0x1003\ncfw_se=0x04146900\nlanguage=1\ntoolkit=0\n")
    info = diag.read_info(device.Device(tmp_path, cfg["device"]["folders"]), cfg)
    assert info["ram"] == "32 MB" and info["cfw_hen"] == "PRO (0x1003)" and info["cfw_se"] == "4.20.105 (0x04146900)"
    assert info["language"] == "English" and info["toolkit"] == "retail"


def test_stick_report(tmp_path):
    from pspm import config

    cfg = config.load(tmp_path / "x")
    (tmp_path / "PSP/GAME/HB").mkdir(parents=True)
    (tmp_path / "PSP/GAME/HB/EBOOT.PBP").write_bytes(b"x" * 2048)
    (tmp_path / "PSP/SAVEDATA/ARK_01234").mkdir(parents=True)
    (tmp_path / "ISO").mkdir()
    (tmp_path / "ISO/._game.iso").write_bytes(b"")
    (tmp_path / "SEPLUGINS").mkdir()
    (tmp_path / "SEPLUGINS/PLUGINS.TXT").write_text("game, ms0:/SEPLUGINS/cheat.prx, on\nvsh, ms0:/SEPLUGINS/cat.prx, off\n")
    r = diag.stick(device.Device(tmp_path, cfg["device"]["folders"]), cfg)
    assert r["games"].startswith("1 files") and r["isos"].startswith("1 files") and r["junk files"] == "1"
    assert r["cfw on stick"] == "ARK" and r["plugins (plugins.txt)"] == "1 on of 2: cheat.prx, cat.prx"
    assert r["biggest file"].startswith("PSP/GAME/HB/EBOOT.PBP")
    assert diag._plugins("ms0:/seplugins/a.prx 1\nms0:/seplugins/b.prx 0\n") == (1, ["a.prx", "b.prx"])


def test_speed_cleans_up(tmp_path):
    from pspm import config

    cfg = config.load(tmp_path / "x")
    cfg["diag"]["speed_test_mb"] = 2
    w, r = diag.speed(device.Device(tmp_path, cfg["device"]["folders"]), cfg)
    assert w > 0 and r == 0 and list(tmp_path.iterdir()) == []
