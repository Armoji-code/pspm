import json
import struct
import zlib

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


def _sfo(**vals):
    keys = b"".join(k.encode() + b"\0" for k in vals)
    data = b"".join(v.encode().ljust(32, b"\0") for v in vals.values())
    index, ko = b"", 0
    for i, (k, v) in enumerate(vals.items()):
        index += struct.pack("<HHIII", ko, 0x0204, len(v) + 1, 32, 32 * i)
        ko += len(k) + 1
    head = 20 + len(index)
    return b"\0PSF" + struct.pack("<IIII", 0x101, head, head + len(keys), len(vals)) + index + keys + data


def test_cat_tabs_subfolders_and_save_titles(dev, tmp_path):
    games = dev.path("games")
    (games / "CAT_GBA" / "gpSP").mkdir(parents=True)
    (games / "CAT_GBA" / "gpSP" / "EBOOT.PBP").write_bytes(b"x")
    (games / "Loose").mkdir()
    assert files.tabs(dev, "games", "CAT_") == ["CAT_GBA"]
    assert [e.name for e in files.list_items(dev, "games", skip=["CAT_GBA"])] == ["Loose"]
    assert [e.name for e in files.list_items(dev, "games", "CAT_GBA/gpSP")] == ["EBOOT.PBP"]
    src = tmp_path / "a.txt"
    src.write_text("a")
    files.copy_in(dev, "games", [src], "CAT_GBA")
    files.remove(dev, "games", "CAT_GBA/a.txt")
    with pytest.raises(ValueError):
        files.list_items(dev, "games", "CAT_GBA/../..")

    save = dev.path("saves") / "ULUS10041DATA00"
    save.mkdir(parents=True)
    (save / "PARAM.SFO").write_bytes(_sfo(TITLE="Lumines", SAVEDATA_TITLE="Slot 1"))
    (save / "ICON0.PNG").write_bytes(b"png")
    e = files.list_items(dev, "saves")[0]
    assert (e.name, e.title, files.thumb(save)) == ("ULUS10041DATA00", "Lumines · Slot 1", str(save / "ICON0.PNG"))


def _iso(files_: dict) -> bytes:
    """A tiny ISO 9660 image with PSP_GAME/<name> for each file, as UMD dumps have."""
    def rec(name, lba, size, folder=False):
        n = name.encode()
        r = struct.pack("<BBI4sI4s7sBxxI", 0, 0, lba, lba.to_bytes(4, "big"), size, size.to_bytes(4, "big"), bytes(7),
                        2 if folder else 0, 0) + bytes([len(n)]) + n
        r += bytes(len(r) % 2)
        return bytes([len(r)]) + r[1:]
    img = bytearray(20 * 2048)
    pvd = b"\x01CD001\x01".ljust(156, b"\0") + rec("\0", 18, 2048, True)
    img[16 * 2048:16 * 2048 + len(pvd)] = pvd
    root = rec("PSP_GAME", 19, 2048, True)
    img[18 * 2048:18 * 2048 + len(root)] = root
    body = b"".join(rec(k + ";1", 20 + i, len(v)) for i, (k, v) in enumerate(files_.items()))
    img[19 * 2048:19 * 2048 + len(body)] = body
    for v in files_.values():
        img += v.ljust(2048, b"\0")
    return bytes(img)


def _cso(iso: bytes) -> bytes:
    blocks = [iso[i:i + 2048] for i in range(0, len(iso), 2048)]
    data, index = b"", []
    head = 24 + 4 * (len(blocks) + 1)
    for b in blocks:
        z = zlib.compressobj(9, zlib.DEFLATED, -15)
        z = z.compress(b) + z.flush()
        index.append(head + len(data) | (0 if len(z) < len(b) else 0x80000000))
        data += z if len(z) < len(b) else b
    index.append(head + len(data))
    return struct.pack("<4sIQIBB2x", b"CISO", 24, len(iso), 2048, 1, 0) + struct.pack(f"<{len(index)}I", *index) + data


def test_titles_and_thumbnails(dev, tmp_path):
    from mutagen.id3 import APIC, ID3

    sfo_b, icon = _sfo(TITLE="Patapon"), b"\x89PNG icon"
    games = dev.path("games")
    (games / "Mini").mkdir()
    pbp = b"\0PBP" + struct.pack("<I8I", 0x10000, *[40, 40 + len(sfo_b)] + [40 + len(sfo_b) + len(icon)] * 6)
    (games / "Mini" / "EBOOT.PBP").write_bytes(pbp + sfo_b + icon)
    isos = dev.path("isos")
    isos.mkdir()
    iso = _iso({"PARAM.SFO": sfo_b, "ICON0.PNG": icon})
    (isos / "a.iso").write_bytes(iso)
    (isos / "b.cso").write_bytes(_cso(iso))
    music = dev.path("music")
    music.mkdir()
    (music / "Album").mkdir()
    (music / "Album" / "cover.jpg").write_bytes(b"jpg")
    tags = ID3()
    tags.add(APIC(encoding=1, mime="image/jpeg", type=3, desc="Cover", data=b"art"))
    tags.save(music / "s.mp3")
    (music / "plain.mp3").write_bytes(b"\xff\xfb")
    look = lambda label: [(e.name, e.title, files.thumb(dev.path(label) / e.name)) for e in files.list_items(dev, label)]
    assert look("games") == [("Mini", "Patapon", icon)]
    assert look("isos") == [("a.iso", "Patapon", icon), ("b.cso", "Patapon", icon)]
    assert look("music") == [("Album", "", str(music / "Album" / "cover.jpg")), ("plain.mp3", "", ""),
                             ("s.mp3", "", b"art")]


def test_route_and_move(dev, tmp_path):
    types = {"isos": [".iso", ".cso"]}
    (dev.path("isos") / "CAT_X").mkdir(parents=True)
    (tmp_path / "g.ISO").write_bytes(b"x")
    assert files.route(dev, ["games", "isos"], tmp_path / "g.ISO", types) == "isos"
    assert files.route(dev, ["games", "isos"], tmp_path / "hb", types) == "games"
    files.copy_in(dev, "isos", [tmp_path / "g.ISO"])
    moved = dev.path("isos") / "g.ISO"
    assert files.route(dev, ["games", "isos"], moved, {}) == "isos"
    files.copy_in(dev, "isos", [moved], "", move=True)  # dropped where it is: nothing to do
    files.copy_in(dev, "isos", [moved], "CAT_X", move=True)
    assert not moved.exists() and (dev.path("isos") / "CAT_X" / "g.ISO").is_file()
