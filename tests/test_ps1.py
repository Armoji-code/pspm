import hashlib
import io
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from pspm import config, platform, ps1


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


def test_write_replaces_icon_and_keeps_data(tmp_path):
    (tmp_path / "in.pbp").write_bytes(_pbp())
    Image.new("RGB", (300, 300), "red").save(tmp_path / "cover.png")
    ps1.write(tmp_path / "in.pbp", tmp_path / "out.pbp", tmp_path / "cover.png")
    out = (tmp_path / "out.pbp").read_bytes()
    offs = struct.unpack_from("<8I", out, 8)
    assert out[:8] == _pbp()[:8] and offs[7] == 0x10000 and out[offs[7]:] == b"PSISOIMG0000disc"
    assert out[offs[6]:offs[6] + 4] == b"PSP!" and ps1.title(tmp_path / "out.pbp") == "Metal Gear Solid"
    icon = Image.open(io.BytesIO(out[offs[1]:offs[2]]))
    assert icon.size == ps1.ICON and icon.getpixel((72, 40))[:3] == (255, 0, 0) and icon.getpixel((0, 0))[3] == 0


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
    monkeypatch.setattr(ps1.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(buf.getvalue()))
    with pytest.raises(OSError, match="checksum"):
        ps1.tool(cfg, tmp_path)
    t["sha256"] = hashlib.sha256(buf.getvalue()).hexdigest()
    exe = ps1.tool(cfg, tmp_path)
    assert exe.read_text() == "#!/bin/sh\n" and ps1.tool(cfg, tmp_path) == exe
