"""Find PSPs mounted as USB drives. Pure Python, no UI."""
import shutil
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class Device:
    root: Path
    folders: dict  # label -> relative path, from config

    def path(self, label: str) -> Path:
        return self.root / self.folders[label]

    def usage(self):
        return shutil.disk_usage(self.root)


def find(cfg: dict) -> list[Device]:
    dev = cfg["device"]
    roots = []
    for part in psutil.disk_partitions(all=False):
        try:
            if (Path(part.mountpoint) / dev["marker"]).is_dir():
                roots.append(Path(part.mountpoint))
        except OSError:  # empty card readers / unready drives raise on Windows
            continue
    return [Device(r, dev["folders"]) for r in roots]
