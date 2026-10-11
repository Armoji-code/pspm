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
