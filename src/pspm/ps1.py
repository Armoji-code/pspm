"""PS1 disc images to PSP EBOOT.PBP, using PSXPackager (MIT, downloaded on first use). No UI."""
import io
import shutil
import struct
import subprocess
import tempfile
from itertools import pairwise
from pathlib import Path

from PIL import Image

from . import platform, tools

IMAGES = ["*.cue", "*.bin", "*.img", "*.iso", "*.chd", "*.m3u", "*.zip", "*.7z"]
ICON = (144, 80)  # ICON0.PNG size the XMB shows
ALIGN = 0x10000


def tool(cfg: dict, root: Path = tools.ROOT) -> Path:
    """Path to PSXPackager for this OS, downloaded and checked against its sha256 the first time."""
    t = cfg["ps1"]["tool"][platform.name()]
    home = root / f"psxpackager-{cfg['ps1']['version']}"
    exe = home / t["exe"]
    if not exe.is_file():
        tools.fetch(t["url"], t["sha256"], home, cfg["ps1"]["timeout_seconds"])
        exe.chmod(0o755)
    return exe


def _head(f) -> tuple[list[int], list[bytes]]:
    """PBP offsets and the files before DATA.PSAR: PARAM.SFO, ICON0, ICON1, PIC0, PIC1, SND0, DATA.PSP."""
    f.seek(8)
    offs = list(struct.unpack("<8I", f.read(32)))
    f.seek(offs[0])
    return offs, [f.read(b - a) for a, b in pairwise(offs)]


def title(pbp: Path) -> str:
    """TITLE from the PBP's PARAM.SFO."""
    with open(pbp, "rb") as f:
        sfo = _head(f)[1][0]
    keys, data, n = struct.unpack_from("<III", sfo, 8)
    for i in range(n):
        k, _, size, _, at = struct.unpack_from("<HHIII", sfo, 20 + 16 * i)
        if sfo[keys + k:sfo.index(b"\0", keys + k)] == b"TITLE":
            return sfo[data + at:data + at + size].rstrip(b"\0").decode(errors="replace")
    return ""


def write(pbp: Path, dest: Path, icon: Path | None = None) -> None:
    """Copy pbp to dest, replacing ICON0 with `icon` fitted into 144x80 on a transparent background."""
    with open(pbp, "rb") as f, open(dest, "wb") as out:
        magic = f.read(8)  # "\0PBP" + version
        offs, parts = _head(f)
        if icon:
            img = Image.open(icon).convert("RGBA")
            img.thumbnail(ICON)
            png = Image.new("RGBA", ICON)
            png.paste(img, ((ICON[0] - img.width) // 2, (ICON[1] - img.height) // 2))
            buf = io.BytesIO()
            png.save(buf, "PNG")
            parts[1] = buf.getvalue()
        parts[6] = parts[6].rstrip(b"\0")  # padding up to DATA.PSAR, written back below
        new = [offs[0]]
        for p in parts:
            new.append(new[-1] + len(p))
        psar = offs[7] if new[7] <= offs[7] else -(-new[7] // ALIGN) * ALIGN  # keep DATA.PSAR aligned as built
        out.write((magic + struct.pack("<8I", *new[:7], psar)).ljust(offs[0], b"\0"))
        out.write(b"".join(parts).ljust(psar - offs[0], b"\0"))
        f.seek(offs[7])
        shutil.copyfileobj(f, out, 1 << 20)


def convert(images: list[Path], games: Path, cfg: dict, icon: Path | None = None, exe: Path | None = None) -> Path:
    """Build one EBOOT.PBP from the discs (in play order) into games/<GAME ID>/. Returns that folder."""
    with tempfile.TemporaryDirectory() as tmp:
        src = images[0]
        if len(images) > 1:  # PSXPackager makes a multi-disc PBP from an .m3u playlist
            src = Path(tmp) / "discs.m3u"
            src.write_text("\n".join(str(p.resolve()) for p in images) + "\n")
        r = subprocess.run([str(exe or tool(cfg)), "-i", str(src), "-o", tmp, "-f", "%MAINGAMEID%", "-x",
                            "-l", str(cfg["ps1"]["compression"])],
                           capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        out = next(Path(tmp).glob("*.[pP][bB][pP]"), None)
        if r.returncode or not out:
            log = [x for x in (r.stdout + r.stderr).splitlines() if x.strip() and "Processing" not in x]
            raise RuntimeError(log[-1] if log else f"PSXPackager failed with code {r.returncode}")
        dest = games / out.stem.upper()
        dest.mkdir(parents=True, exist_ok=True)
        write(out, dest / "EBOOT.PBP", icon)
        return dest
