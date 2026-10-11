"""File manager operations on a Device. Pure Python, no UI."""
import shutil
from dataclasses import dataclass
from pathlib import Path

from .device import Device


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    is_dir: bool


def _size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size


def list_items(dev: Device, label: str) -> list[Entry]:
    d = dev.path(label)
    if not d.is_dir():
        return []
    return sorted((Entry(p.name, _size(p), p.is_dir()) for p in d.iterdir()), key=lambda e: e.name.lower())


def _inside(dev: Device, label: str, name: str) -> Path:
    base = dev.path(label).resolve()
    target = (base / name).resolve()
    if target.parent != base:  # trust boundary: names come from the UI / filesystem
        raise ValueError(f"refusing path outside {label}: {name}")
    return target


def copy_in(dev: Device, label: str, sources: list[Path]) -> None:
    dest = dev.path(label)
    dest.mkdir(parents=True, exist_ok=True)
    for src in map(Path, sources):
        target = _inside(dev, label, src.name)
        if target.exists():
            raise FileExistsError(target)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, target)


def remove(dev: Device, label: str, name: str) -> None:
    target = _inside(dev, label, name)
    shutil.rmtree(target) if target.is_dir() else target.unlink()
