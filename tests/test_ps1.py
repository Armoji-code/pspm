import hashlib
import io
import struct
import subprocess
import urllib.error
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from pspm import config, platform, ps1, tools


def _pbp(title=b"Metal Gear Solid"):
    """Minimal PBP: PARAM.SFO with TITLE, small icon, DATA.PSP, then DATA.PSAR at 0x10000 like PSXPackager."""
    keys, data = b"TITLE\0", title + b"\0"
    sfo = b"\0PSF" + struct.pack("<IIII", 0x101, 20 + 16, 20 + 16 + 8, 1)
    sfo += struct.pack("<HHIII", 0, 0x204, len(data), 128, 0) + keys.ljust(8, b"\0") + data.ljust(128, b"\0")
    parts = [sfo, b"ICON", b"", b"", b"", b"", b"PSP!"]
    offs = [40]
    for p in parts:
        offs.append(offs[-1] + len(p))
    head = b"\0PBP" + struct.pack("<I8I", 0x10000, *offs[:7], 0x10000)
    return (head + b"".join(parts)).ljust(0x10000, b"\0") + b"PSISOIMG0000disc"


def _png(color, size=(300, 300)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_write_sets_metadata_and_keeps_data(tmp_path):
    (tmp_path / "in.pbp").write_bytes(_pbp())
    meta = {"title": "Metal Gear Solid: Special Edition", "icon0": _png("red"), "pic1": _png("blue", (640, 480)),
            "snd0": b"RIFFat3"}
    ps1.write(tmp_path / "in.pbp", tmp_path / "out.pbp", meta)
    out = (tmp_path / "out.pbp").read_bytes()
    offs = struct.unpack_from("<8I", out, 8)
    assert out[:8] == _pbp()[:8] and offs[7] == 0x10000 and out[offs[7]:] == b"PSISOIMG0000disc"
    assert out[offs[6]:offs[6] + 4] == b"PSP!" and ps1.title(tmp_path / "out.pbp") == meta["title"]
    icon = Image.open(io.BytesIO(out[offs[1]:offs[2]]))
    assert icon.size == ps1.ICON and icon.getpixel((72, 40))[:3] == (255, 0, 0) and icon.getpixel((0, 0))[3] == 0
    back = Image.open(io.BytesIO(out[offs[4]:offs[5]]))
    assert back.size == (480, 272) and back.getpixel((0, 0)) == (0, 0, 255, 255)  # cropped to fill, no borders
    assert out[offs[5]:offs[6]] == b"RIFFat3" and offs[3] == offs[4]  # no PIC0 given: stays empty


def test_title_is_cut_to_the_sfo_room():
    sfo = ps1.sfo_set_title(_pbp()[40:40 + 36 + 8 + 128], "é" * 100)
    assert ps1.sfo_title(sfo) == "é" * 63


def test_serial_from_cue(tmp_path):
    (tmp_path / "Game (Track 1).bin").write_bytes(b"\0" * 5000 + b"BOOT = cdrom:\\SLUS_005.94;1\r\nTCB = 4")
    (tmp_path / "game.cue").write_text('FILE "Game (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    assert ps1.serial(tmp_path / "game.cue") == "SLUS-00594"
    assert ps1.serial(tmp_path / "game.chd") == ""


def test_lookup_follows_symlinks_and_skips_missing(tmp_path, monkeypatch):
    files = {"title.txt": b"Metal Gear Solid (Disc 1) (Rev 1)\n", "release_name.txt": b"Metal Gear Solid (USA) (Disc 1)",
             "Named_Boxarts/Metal%20Gear%20Solid%20%28USA%29%20%28Disc%201%29.png": b"Metal Gear Solid (USA).png",
             "Named_Boxarts/Metal%20Gear%20Solid%20%28USA%29.png": b"\x89PNGbox"}

    def fake(req, timeout):
        key = next((k for k in files if req.full_url.endswith(k)), None)
        if key is None:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
        return io.BytesIO(files[key])

    monkeypatch.setattr(ps1.urllib.request, "urlopen", fake)
    meta = ps1.lookup("slus-00594", config.load(tmp_path / "x"))
    assert meta == {"title": "Metal Gear Solid", "icon0": b"\x89PNGbox"}
    with pytest.raises(LookupError):
        ps1.lookup("SLUS-99999", {"ps1": {"online": {**config.load(tmp_path / "x")["ps1"]["online"],
                                                    "title": "https://x/none.txt"}}})


def test_convert_installs_multi_disc_set(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        src, out = cmd[cmd.index("-i") + 1], cmd[cmd.index("-o") + 1]
        seen["m3u"] = Path(src).read_text().splitlines()
        (Path(out) / "SLUS00594.pbp").write_bytes(_pbp())
        return subprocess.CompletedProcess(cmd, 0, "Writing Disc 2 - 100%", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    discs = [tmp_path / "d1.cue", tmp_path / "d2.cue"]
    dest = ps1.convert(discs, tmp_path / "GAME", config.load(tmp_path / "x"), exe=tmp_path / "tool")
    assert seen["m3u"] == [str(d.resolve()) for d in discs]
    assert dest == tmp_path / "GAME" / "SLUS00594" and ps1.title(dest / "EBOOT.PBP") == "Metal Gear Solid"


def test_convert_reports_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, "Processing x.iso\nVolume is not ISO-9660\nProcessing completed: 00h 00m 00s\n", ""))
    with pytest.raises(RuntimeError, match="not ISO-9660"):
        ps1.convert([tmp_path / "x.iso"], tmp_path / "GAME", config.load(tmp_path / "x"), exe=tmp_path / "tool")


def test_tool_download_checks_sha256(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bin/psxpackager", "#!/bin/sh\n")
    cfg = config.load(tmp_path / "x")
    t = cfg["ps1"]["tool"][platform.name()] = {"url": "https://x/t.zip", "exe": "bin/psxpackager", "sha256": "0"}
    monkeypatch.setattr(tools.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(buf.getvalue()))
    with pytest.raises(OSError, match="checksum"):
        ps1.tool(cfg, tmp_path)
    t["sha256"] = hashlib.sha256(buf.getvalue()).hexdigest()
    exe = ps1.tool(cfg, tmp_path)
    assert exe.read_text() == "#!/bin/sh\n" and ps1.tool(cfg, tmp_path) == exe


def test_edit_installed_game_in_place_and_by_rebuild(tmp_path):
    game = tmp_path / "GAME" / "CAT_PS1" / "SLUS00594"
    game.mkdir(parents=True)
    (game / "EBOOT.PBP").write_bytes(_pbp())
    (tmp_path / "GAME" / "HOMEBREW").mkdir()
    (tmp_path / "GAME" / "HOMEBREW" / "EBOOT.PBP").write_bytes(_pbp()[:0x10000] + b"not a PS1 game")
    pbp, = ps1.installed(tmp_path / "GAME")
    assert pbp == game / "EBOOT.PBP" and ps1.read(pbp) == {"title": "Metal Gear Solid", "icon0": b"ICON"}
    ps1.edit(pbp, {"title": "MGS", "icon0": b"", "snd0": b"RIFFat3"})  # small: header rewritten in place
    assert ps1.read(pbp) == {"title": "MGS", "snd0": b"RIFFat3"} and pbp.read_bytes()[0x10000:] == b"PSISOIMG0000disc"
    ps1.edit(pbp, {"pic1": _png("blue", (640, 480)), "snd0": b"x" * 0x10000})  # too big: DATA.PSAR moves
    out = pbp.read_bytes()
    assert struct.unpack_from("<I", out, 36)[0] == 0x20000 and out[0x20000:] == b"PSISOIMG0000disc"
    assert set(ps1.read(pbp)) == {"title", "pic1", "snd0"} and not list(game.glob("*.pspm"))
