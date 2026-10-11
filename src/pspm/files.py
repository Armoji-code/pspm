"""File manager operations on a Device. Pure Python, no UI.

`sub` and `name` are paths relative to the category folder, e.g. "CAT_GBA" or "CAT_GBA/Pokemon".
"""
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

from .device import Device


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    is_dir: bool
    title: str = ""  # from the folder's PARAM.SFO (save games), else ""
    icon: str = ""   # path of the folder's ICON0.PNG, else ""


def _size(p: Path) -> int:
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


def _entry(p: Path) -> Entry:
    title = icon = ""
    if p.is_dir() and (p / "PARAM.SFO").is_file():
        try:
            info = sfo((p / "PARAM.SFO").read_bytes())
        except (struct.error, ValueError):  # a broken save shouldn't hide the folder
            info = {}
        game, save = str(info.get("TITLE", "")).strip(), str(info.get("SAVEDATA_TITLE", "")).strip()
        title = f"{game} · {save}" if game and save and save != game else game
        icon = str(p / "ICON0.PNG") if (p / "ICON0.PNG").is_file() else ""
    return Entry(p.name, _size(p), p.is_dir(), title, icon)


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


def list_items(dev: Device, label: str, sub: str = "", skip=()) -> list[Entry]:
    d = _dir(dev, label, sub)
    if not d.is_dir():
        return []
    found = (_entry(p) for p in d.iterdir() if p.name not in skip)
    return sorted(found, key=lambda e: (e.title or e.name).lower())


def copy_in(dev: Device, label: str, sources: list[Path], sub: str = "") -> None:
    _dir(dev, label, sub).mkdir(parents=True, exist_ok=True)
    for src in map(Path, sources):
        target = _inside(dev, label, Path(sub, src.name))
        if target.exists():
            raise FileExistsError(target)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, target)


def remove(dev: Device, label: str, name: str) -> None:
    target = _inside(dev, label, name)
    shutil.rmtree(target) if target.is_dir() else target.unlink()
