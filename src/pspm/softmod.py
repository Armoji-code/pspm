"""ARK custom firmware: copies the official release's files to the memory stick. No UI.

PSPM only writes to the memory stick. ARK itself is installed later on the PSP, when you run the copied app
(FasterARK for ARK-5, ARK Loader for ARK-4). The permanent install (cIPL) is only copied when asked for.
"""
import hashlib
import shutil
import struct
import time
from pathlib import Path

from platformdirs import user_data_path

from . import ps1, tools
from .device import Device

BACKUPS = user_data_path("pspm") / "softmod-backups"


def tool(cfg: dict, ark: str, root: Path = tools.ROOT) -> Path:
    """Folder with the ARK release, downloaded and checked against its sha256 the first time."""
    a = cfg["softmod"][ark]
    return tools.fetch(a["url"], a["sha256"], root / f"{ark}-{a['version']}", cfg["softmod"]["timeout_seconds"])


def folders(cfg: dict, ark: str, cipl: bool = False) -> dict:
    """Folder in the download -> folder on the stick. cipl adds the permanent installer, which writes the PSP's flash."""
    a = cfg["softmod"][ark]
    return a["copy"] | (a["cipl"] if cipl else {})


def files(cfg: dict, ark: str, home: Path, cipl: bool = False) -> list[tuple[Path, str]]:
    """(file in the download, path on the stick) for everything that gets copied."""
    out = []
    for src, dest in folders(cfg, ark, cipl).items():
        found = sorted(p for p in (home / src).rglob("*") if p.is_file())
        if not found:
            raise FileNotFoundError(f"{src} is missing from the download")
        out += [(p, f"{dest}/{p.relative_to(home / src).as_posix()}") for p in found]
    return out


def problems(dev: Device, cfg: dict, todo: list[tuple[Path, str]], info: dict) -> list[str]:
    """What blocks the copy. info is the PSP app's INFO report (diag.read_info), empty when it never ran."""
    s, out = cfg["softmod"], []
    fw = info.get("firmware")
    if fw and fw not in s["firmware"]:
        out.append(f"firmware {fw} isn't supported; update the PSP to {' or '.join(s['firmware'])} first")
    battery = info.get("battery", "")
    # shortcut: the report is from when the INFO mode last ran, so this only catches a known-low battery
    if battery.endswith("%") and int(battery[:-1]) < s["min_battery"] and info.get("charger") != "connected":
        out.append(f"battery was at {battery} without the charger; charge to {s['min_battery']}% and run PSPM's INFO mode on the PSP again")
    need = sum(src.stat().st_size for src, _ in todo)
    if dev.usage().free < need:
        out.append(f"needs {need // 2**20 + 1} MB free on the memory stick")
    return out


def existing(dev: Device, cfg: dict, ark: str, cipl: bool = False) -> list[str]:
    """Folders on the stick that the copy would replace."""
    return [rel for rel in folders(cfg, ark, cipl).values() if (dev.root / rel).exists()]


def installed(dev: Device) -> str | None:
    """ARK's name from its save folder on the stick ("ARK-4", or "ARK" for ARK-5), None when ARK was never set up."""
    sfo = dev.root / "PSP/SAVEDATA/ARK_01234/PARAM.SFO"
    if not sfo.is_file():
        return None
    try:
        return ps1.sfo_title(sfo.read_bytes()) or "ARK"
    except (struct.error, ValueError):  # a damaged PARAM.SFO still means ARK was there
        return "ARK"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def install(dev: Device, cfg: dict, ark: str, todo: list[tuple[Path, str]], backups: Path = BACKUPS,
            cipl: bool = False) -> Path | None:
    """Move folders being replaced to a backup, copy, then read every copy back. Returns the backup folder, if any."""
    old = existing(dev, cfg, ark, cipl)
    keep = backups / time.strftime("%Y%m%d-%H%M%S") if old else None
    for rel in old:  # whole folders, so a different ARK version's leftovers never mix with the new files
        (keep / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(dev.root / rel, keep / rel)
    for src, rel in todo:
        (dev.root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dev.root / rel)
    bad = [rel for src, rel in todo if _sha(dev.root / rel) != _sha(src)]
    if bad:
        raise OSError(f"{len(bad)} files didn't copy right (first: {bad[0]}); check the stick, don't run ARK")
    return keep
