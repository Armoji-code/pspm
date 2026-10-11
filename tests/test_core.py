import json

import pytest

from pspm import config, device, files


@pytest.fixture
def dev(tmp_path):
    (tmp_path / "PSP" / "GAME").mkdir(parents=True)
    return device.Device(tmp_path, config.load(tmp_path / "none.toml")["device"]["folders"])


def test_user_config_overrides_defaults(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[app]\ntheme = "light"\n[device.folders]\nmusic = "MUSIC2"\n')
    cfg = config.load(f)
    assert cfg["app"]["theme"] == "light"
    assert cfg["device"]["folders"]["music"] == "MUSIC2"
    assert cfg["device"]["folders"]["games"] == "PSP/GAME"  # untouched keys survive


def test_find_detects_marker(tmp_path, monkeypatch):
    (tmp_path / "psp" / "PSP").mkdir(parents=True)
    (tmp_path / "usb").mkdir()
    parts = [type("P", (), {"mountpoint": str(tmp_path / n)}) for n in ("psp", "usb")]
    monkeypatch.setattr(device.psutil, "disk_partitions", lambda all: parts)
    monkeypatch.setattr(device.platform, "current", lambda: type("OS", (), {"mount_psps": staticmethod(lambda: None)}))
    assert [d.root for d in device.find(config.load(tmp_path / "x"))] == [tmp_path / "psp"]


def test_copy_list_remove(dev, tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"x" * 10)
    files.copy_in(dev, "music", [src])
    assert files.list_items(dev, "music") == [files.Entry("song.mp3", 10, False)]
    with pytest.raises(FileExistsError):
        files.copy_in(dev, "music", [src])
    files.remove(dev, "music", "song.mp3")
    assert files.list_items(dev, "music") == []


def test_remove_refuses_escape(dev):
    with pytest.raises(ValueError):
        files.remove(dev, "games", "../../etc")


def test_mount_psps_mounts_once_per_plug(monkeypatch):
    from pspm.platform import linux

    disks = [{"path": "/dev/sda", "pkname": None, "model": '"PSP" Type A', "mountpoint": None, "fstype": None},
             {"path": "/dev/sda1", "pkname": "sda", "model": None, "mountpoint": None, "fstype": "vfat"},
             {"path": "/dev/sdb1", "pkname": "sdb", "model": None, "mountpoint": None, "fstype": "vfat"}]
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return type("R", (), {"stdout": json.dumps({"blockdevices": disks})})()

    monkeypatch.setattr(linux.subprocess, "run", run)
    monkeypatch.setattr(linux.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(linux, "_tried", set())
    linux.mount_psps()
    linux.mount_psps()  # the user unmounted it again: leave it alone
    assert [c for c in calls if c[0] == "udisksctl"] == [["udisksctl", "mount", "--no-user-interaction", "-b",
                                                          "/dev/sda1"]]
