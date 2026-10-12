"""PS1 disc images to PSP EBOOT.PBP, using PSXPackager (MIT, downloaded on first use). No UI."""
import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageOps

from . import platform, tools

IMAGES = ["*.cue", "*.bin", "*.img", "*.iso", "*.chd", "*.m3u", "*.zip", "*.7z"]
ALIGN = 0x10000
# PBP resources PSPM can set: name -> (index in the PBP, PNG size or None for raw SND0.AT3)
RES = {"icon0": (1, (144, 80)), "pic0": (3, (310, 180)), "pic1": (4, (480, 272)), "snd0": (5, None)}
ICON = RES["icon0"][1]
BOOT = re.compile(rb"BOOT\s*=\s*cdrom:\\?\\?([A-Z]{4})_(\d{3})\.(\d{2})", re.IGNORECASE)  # SYSTEM.CNF names the exe


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
        return sfo_title(_head(f)[1][0])


def _sfo_entry(sfo: bytes, key: bytes) -> tuple[int, int, int, int] | None:
    """(index, data start, used size, max size) of a PARAM.SFO key."""
    keys, data, n = struct.unpack_from("<III", sfo, 8)
    for i in range(n):
        k, _, size, room, at = struct.unpack_from("<HHIII", sfo, 20 + 16 * i)
        if sfo[keys + k:sfo.index(b"\0", keys + k)] == key:
            return i, data + at, size, room
    return None


def sfo_title(sfo: bytes) -> str:
    """TITLE from a PARAM.SFO."""
    e = _sfo_entry(sfo, b"TITLE")
    return sfo[e[1]:e[1] + e[2]].rstrip(b"\0").decode(errors="replace") if e else ""


def sfo_set_title(sfo: bytes, name: str) -> bytes:
    """PARAM.SFO with TITLE replaced in place, cut to the room the SFO has for it (128 bytes from PSXPackager)."""
    i, start, _, room = _sfo_entry(sfo, b"TITLE")
    v = name.encode()[:room - 1].decode(errors="ignore").encode() + b"\0"  # don't cut a UTF-8 character in half
    out = bytearray(sfo)
    out[start:start + room] = v.ljust(room, b"\0")
    struct.pack_into("<I", out, 20 + 16 * i + 4, len(v))
    return bytes(out)


def picture(data: bytes, size: tuple[int, int], fill: bool = False) -> bytes:
    """Any image as a PNG of exactly `size`: fitted on transparency, or cropped to fill it (backgrounds)."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    if fill:
        img = ImageOps.fit(img, size, Image.LANCZOS)
    img.thumbnail(size)
    png = Image.new("RGBA", size)
    png.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    buf = io.BytesIO()
    png.save(buf, "PNG")
    return buf.getvalue()


def serial(disc: Path) -> str:
    """Game ID like SLUS-00594 from the disc's SYSTEM.CNF, or "" (compressed images can't be read this way)."""
    kind = disc.suffix.lower()
    if kind in (".cue", ".m3u") and disc.is_file():  # first data track / first disc
        line = r'^\s*FILE\s+"?(.+?)"?\s+\w+\s*$' if kind == ".cue" else r"^\s*(\S.*?)\s*$"
        m = re.search(line, disc.read_text(errors="replace"), re.MULTILINE)
        return serial(disc.parent / m[1]) if m else ""
    if kind not in (".bin", ".img", ".iso") or not disc.is_file():
        return ""
    with open(disc, "rb") as f:
        m = BOOT.search(f.read(32 << 20))  # shortcut: SYSTEM.CNF sits in the first sectors on every disc seen so far
    return f"{m[1].decode().upper()}-{m[2].decode()}{m[3].decode()}" if m else ""


def _get(url: str, cfg: dict, hops: int = 3) -> bytes | None:
    """URL contents, None if it isn't there. Follows git symlinks, which raw GitHub serves as the target's name."""
    req = urllib.request.Request(url, headers={"User-Agent": "PSPM"})
    try:
        with urllib.request.urlopen(req, timeout=cfg["ps1"]["online"]["timeout_seconds"]) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if hops and url.endswith(".png") and data.endswith(b".png") and len(data) < 512:
        return _get(urllib.parse.urljoin(url, urllib.parse.quote(data.decode().strip())), cfg, hops - 1)
    return data


def lookup(game_id: str, cfg: dict) -> dict:
    """Title and artwork for a game ID from the sources in [ps1.online]; missing pieces are left out."""
    on = cfg["ps1"]["online"]
    sid = urllib.parse.quote(game_id.strip().upper())
    title = _get(on["title"].format(serial=sid), cfg)
    if title is None:
        raise LookupError(f"{game_id} isn't in the game list")
    name = (_get(on["name"].format(serial=sid), cfg) or title).decode().strip()
    meta = {"title": re.sub(r"\s*\(.*", "", title.decode().strip())}  # drop "(Disc 1) (Rev 1)"
    file = urllib.parse.quote(re.sub(r'[&*/:`<>?\\|"]', "_", name))  # libretro-thumbnails file naming
    for res in ("icon0", "pic0", "pic1"):
        if on.get(res) and (data := _get(on[res].format(name=file, serial=sid), cfg)):
            meta[res] = data
    return meta


def _header(f, meta: dict) -> tuple[bytes, int, int]:
    """New PBP header up to DATA.PSAR, its DATA.PSAR offset and the old one. meta as in write()."""
    f.seek(0)
    magic = f.read(8)  # "\0PBP" + version
    offs, parts = _head(f)
    if meta.get("title"):
        parts[0] = sfo_set_title(parts[0], meta["title"])
    for res, (i, size) in RES.items():
        if (data := meta.get(res)) is not None:
            parts[i] = picture(data, size, res == "pic1") if data and size else data
    parts[6] = parts[6].rstrip(b"\0")  # padding up to DATA.PSAR, written back below
    new = [offs[0]]
    for p in parts:
        new.append(new[-1] + len(p))
    psar = offs[7] if new[7] <= offs[7] else -(-new[7] // ALIGN) * ALIGN  # keep DATA.PSAR aligned as built
    head = (magic + struct.pack("<8I", *new[:7], psar)).ljust(offs[0], b"\0") + b"".join(parts)
    return head.ljust(psar, b"\0"), psar, offs[7]


def write(pbp: Path, dest: Path, meta: dict | None = None) -> None:
    """Copy pbp to dest, setting what `meta` has: title (str), icon0/pic0/pic1 (any image bytes) and snd0 (AT3 bytes).
    b"" empties a resource, None or a missing key keeps it. DATA.PSAR keeps its aligned offset."""
    with open(pbp, "rb") as f, open(dest, "wb") as out:
        head, _, old = _header(f, meta or {})
        out.write(head)
        f.seek(old)
        shutil.copyfileobj(f, out, 1 << 20)


def edit(pbp: Path, meta: dict) -> None:
    """Change an installed PBP like write(). Only the header is rewritten when it still fits before DATA.PSAR;
    otherwise the PBP is rebuilt next to it and swapped in, so the game is never left half written."""
    with open(pbp, "r+b") as f:
        head, psar, old = _header(f, meta)
        if psar == old:
            f.seek(0)
            f.write(head)
            return
    tmp = pbp.with_name(pbp.name + ".pspm")
    write(pbp, tmp, meta)
    os.replace(tmp, pbp)


def read(pbp: Path) -> dict:
    """Title and the resources a PBP has, in write()'s form."""
    with open(pbp, "rb") as f:
        parts = _head(f)[1]
    return {"title": sfo_title(parts[0])} | {res: parts[i] for res, (i, _) in RES.items() if parts[i]}


def installed(games: Path) -> list[Path]:
    """EBOOT.PBP of every converted PS1 game in the games folder and its CAT_ folders."""
    found = []
    for pbp in sorted([*games.glob("*/EBOOT.PBP"), *games.glob("CAT_*/*/EBOOT.PBP")]):
        try:
            with open(pbp, "rb") as f:
                f.seek(36)  # DATA.PSAR offset, the last of the header's eight
                f.seek(struct.unpack("<I", f.read(4))[0])
                if f.read(10) in (b"PSISOIMG00", b"PSTITLEIMG"):  # one disc / multi-disc PS1 data
                    found.append(pbp)
        except (OSError, struct.error):
            pass  # not a PBP, or unreadable: not ours to edit
    return found


def convert(images: list[Path], games: Path, cfg: dict, meta: dict | None = None, exe: Path | None = None) -> Path:
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
        write(out, dest / "EBOOT.PBP", meta)
        return dest
