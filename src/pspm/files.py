"""File manager operations on a Device. Pure Python, no UI.

`sub` and `name` are paths relative to the category folder, e.g. "CAT_GBA" or "CAT_GBA/Pokemon".
"""
import json
import shutil
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError
from platformdirs import user_data_path

from . import theme
from .device import Device

CATEGORIES = user_data_path("pspm") / "categories.json"  # page -> CAT_ folder it installs into
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
SECTOR = 2048


@dataclass(frozen=True)
class Entry:
    name: str
    size: int | None         # None for folders: adding one up means walking it, see size_of()
    is_dir: bool
    title: str = ""          # from PARAM.SFO (saves, EBOOT.PBP, ISO/CSO), else ""


def size_of(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size


def sfo(data: bytes) -> dict:
    """Keys and values of a PARAM.SFO: strings and ints."""
    if data[:4] != b"\0PSF":
        return {}
    keys, vals, n = struct.unpack_from("<III", data, 8)
    out = {}
    for i in range(n):
        k, fmt, size, _, at = struct.unpack_from("<HHIII", data, 20 + 16 * i)
        key = data[keys + k:data.index(b"\0", keys + k)].decode(errors="replace")
        raw = data[vals + at:vals + at + size]
        out[key] = struct.unpack("<I", raw)[0] if fmt == 0x0404 else raw.rstrip(b"\0").decode(errors="replace")
    return out


def _title(info: dict) -> str:
    game, save = str(info.get("TITLE", "")).strip(), str(info.get("SAVEDATA_TITLE", "")).strip()
    return f"{game} · {save}" if game and save and save != game else game


def _pbp(p: Path) -> tuple[dict, bytes]:
    """PARAM.SFO and ICON0.PNG of an EBOOT.PBP; they come first, so only the start of the file is read."""
    with p.open("rb") as f:
        head = f.read(40)
        if head[:4] != b"\0PBP":
            return {}, b""
        at = struct.unpack_from("<3I", head, 8)
        f.seek(at[0])
        return sfo(f.read(at[1] - at[0])), f.read(at[2] - at[1])


def _cso(f):
    """Reader of a CSO (compressed ISO, v1): read(offset, n) -> bytes of the ISO inside."""
    _, _, _, bs, _, align = struct.unpack_from("<4sIQIBB", f.read(24))

    def read(off, n):
        out = b""
        for b in range(off // bs, (off + n - 1) // bs + 1):
            f.seek(24 + 4 * b)
            a, z = struct.unpack("<II", f.read(8))
            start = (a & 0x7FFFFFFF) << align
            f.seek(start)
            raw = f.read(((z & 0x7FFFFFFF) << align) - start)
            out += raw[:bs] if a & 0x80000000 else zlib.decompressobj(-15).decompress(raw)
        return out[off % bs:off % bs + n]
    return read


def _iso_file(read, *path: str) -> bytes:
    """A file inside an ISO 9660 image, by its folder names and name."""
    pvd = read(16 * SECTOR, SECTOR)
    if pvd[1:6] != b"CD001":
        return b""
    rec = pvd[156:190]  # root folder's record
    for part in path:
        lba, size = struct.unpack_from("<I4xI", rec, 2)
        d, i, rec = read(lba * SECTOR, size), 0, None
        while i < len(d):
            if not d[i]:  # records don't cross sectors; the rest of this one is padding
                i = (i // SECTOR + 1) * SECTOR
                continue
            if d[i + 33:i + 33 + d[i + 32]].split(b";")[0].decode(errors="replace").upper() == part:
                rec = d[i:i + d[i]]
                break
            i += d[i]
        if rec is None:
            return b""
    lba, size = struct.unpack_from("<I4xI", rec, 2)
    return read(lba * SECTOR, size)


def _iso(p: Path, name: str) -> bytes:
    """PSP_GAME/<name> inside an ISO or CSO."""
    with p.open("rb") as f:
        if p.suffix.lower() == ".cso":
            read = _cso(f)
        else:
            def read(off, n):
                f.seek(off)
                return f.read(n)
        return _iso_file(read, "PSP_GAME", name)


def _info(p: Path) -> dict:
    """PARAM.SFO of a save/game folder, EBOOT.PBP or ISO/CSO: small reads, so titles come with the listing."""
    ext = p.suffix.lower()
    if p.is_dir():
        if (p / "PARAM.SFO").is_file():
            return sfo((p / "PARAM.SFO").read_bytes())
        return _pbp(p / "EBOOT.PBP")[0] if (p / "EBOOT.PBP").is_file() else {}
    if ext == ".pbp":
        return _pbp(p)[0]
    if ext in (".iso", ".cso"):
        return sfo(_iso(p, "PARAM.SFO"))
    return {}


def thumb(p: Path) -> str | bytes:
    """Thumbnail of a folder or file, from what's already in it: an image file's path, image data, or ""."""
    ext = p.suffix.lower()
    if p.is_dir():
        if (p / "PARAM.SFO").is_file():
            icon = p / "ICON0.PNG"
            return str(icon) if icon.is_file() else ""
        if (p / "EBOOT.PBP").is_file():
            return _pbp(p / "EBOOT.PBP")[1]
        pics = sorted(c for c in p.iterdir() if c.suffix.lower() in IMAGES and c.is_file())  # album covers, ICON0.PNG
        return str(pics[0]) if pics else ""
    if ext in IMAGES:
        return str(p)
    if ext == ".pbp":
        return _pbp(p)[1]
    if ext in (".iso", ".cso"):
        return _iso(p, "ICON0.PNG")
    if ext == ".mp3":
        try:
            art = ID3(p).getall("APIC")
        except ID3NoHeaderError:  # untagged
            return ""
        return art[0].data if art else b""
    if ext in (".ptf", ".ctf"):
        typ, data = theme.read_ptf(p.read_bytes())[1].get((0, 1), (0, b""))  # the theme's 300x170 preview
        return data if typ in theme.FORMATS else b""
    thm = next((t for t in (p.with_suffix(".THM"), p.with_suffix(".thm")) if t.is_file()), None)  # PSP video thumbnail
    return str(thm) if thm else ""


def _entry(p: Path) -> Entry:
    try:
        info = _info(p)
    except Exception:  # noqa: BLE001 - a broken save/ISO shouldn't hide the item, it just gets no title
        info = {}
    is_dir = p.is_dir()
    return Entry(p.name, None if is_dir else p.stat().st_size, is_dir, _title(info))


def _inside(dev: Device, label: str, name) -> Path:
    base = dev.path(label).resolve()
    target = (base / name).resolve()
    if base not in target.parents:  # trust boundary: names come from the UI / filesystem
        raise ValueError(f"refusing path outside {label}: {name}")
    return target


def _dir(dev: Device, label: str, sub: str) -> Path:
    return _inside(dev, label, sub) if sub else dev.path(label)


def tabs(dev: Device, label: str, prefix: str) -> list[str]:
    """Top folders of a category shown as tabs: those starting with prefix ("" = every folder)."""
    d = dev.path(label)
    return sorted((p.name for p in d.iterdir() if p.is_dir() and p.name.startswith(prefix)),
                  key=str.lower) if d.is_dir() else []


def categories(dev: Device, cfg: dict) -> list[str]:
    """CAT_ folders in the games folder: where an app or game can be installed."""
    return tabs(dev, "games", cfg["files"]["tabs"]["games"])


def find_app(dev: Device, name: str) -> Path | None:
    """An app's folder in the games folder or one of its category folders."""
    games = dev.path("games")
    return next((p for p in [games / name, *games.glob(f"*/{name}")] if p.is_dir()), None)


def app_dir(dev: Device, name: str, cat: str = "") -> Path:
    """games/<cat>/<name> ("" = no category), moving an older copy there from another category so there's one."""
    dest = dev.path("games") / cat / name
    old = find_app(dev, name)
    if old and old != dest and not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(old, dest)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def chosen(page: str, path: Path = CATEGORIES) -> str:
    return json.loads(path.read_text()).get(page, "") if path.exists() else ""


def choose(page: str, cat: str, path: Path = CATEGORIES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(old | {page: cat}, indent=1))


def list_items(dev: Device, label: str, sub: str = "", skip=()) -> list[Entry]:
    """Names, titles and file sizes only; thumbnails (thumb) and folder sizes (size_of) are for the rows on screen."""
    d = _dir(dev, label, sub)
    if not d.is_dir():
        return []
    found = (_entry(p) for p in d.iterdir() if p.name not in skip)
    return sorted(found, key=lambda e: ((e.title or e.name).lower(), e.name.lower()))


def route(dev: Device, labels: list[str], src: Path, types: dict) -> str:
    """Which of a view's folders a dropped file goes to: the one it's already in, else the first whose types list
    its extension (ISOs into ISO), else the view's own."""
    src = Path(src).resolve()
    for label in labels:
        if dev.path(label).resolve() in src.parents:
            return label
    return next((lb for lb in labels if src.suffix.lower() in types.get(lb, ())), labels[0])


def copy_in(dev: Device, label: str, sources: list[Path], sub: str = "", move: bool = False) -> None:
    dest = _dir(dev, label, sub)
    dest.mkdir(parents=True, exist_ok=True)
    for src in map(Path, sources):
        if move and src.resolve().parent == dest.resolve():  # dropped where it already is
            continue
        target = _inside(dev, label, Path(sub, src.name))
        if target.exists():
            raise FileExistsError(target)
        if move:
            shutil.move(src, target)
        else:
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, target)


def remove(dev: Device, label: str, name: str) -> None:
    target = _inside(dev, label, name)
    shutil.rmtree(target) if target.is_dir() else target.unlink()
