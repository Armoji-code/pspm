"""Diagnostics: PSP info file, filesystem check, fake-capacity test. Pure Python, no UI."""
import os
import random
import shutil
from importlib.resources import files
from pathlib import Path

import psutil

from . import platform
from .device import Device

BLOCK = 1 << 20
APP = files("pspm").joinpath("psp_app/EBOOT.PBP")  # prebuilt from psp/pspm_info; rebuild it when main.c changes
MODELS = {"01g": "PSP-1000", "02g": "PSP-2000", "03g": "PSP-3000", "04g": "PSP-3000", "05g": "PSP Go",
          "07g": "PSP-3000", "09g": "PSP-3000", "11g": "PSP-E1000 (Street)"}


def install_app(dev: Device, cfg: dict) -> Path:
    dest = dev.root / cfg["diag"]["info_app_folder"]
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "EBOOT.PBP").write_bytes(APP.read_bytes())
    return dest


def read_info(dev: Device, cfg: dict) -> dict:
    """key=value lines written by the PSP info app, plus 'written' (mtime). Empty if it never ran."""
    p = dev.root / cfg["diag"]["info_file"]
    if not p.is_file():
        return {}
    info = dict(line.strip().split("=", 1) for line in p.read_text(errors="replace").splitlines() if "=" in line)
    info["written"] = p.stat().st_mtime
    if info.get("model") in MODELS:
        info["model"] = f"{MODELS[info['model']]} ({info['model']})"
    return info


def fs_check(dev: Device) -> tuple[int, str]:
    """Read-only check (fsck.vfat -n / chkdsk without /f). Returns (exit code, output); 0 means clean."""
    root = os.path.normcase(str(dev.root))
    part = next((p for p in psutil.disk_partitions() if os.path.normcase(p.mountpoint) == root), None)
    if part is None:
        raise OSError(f"can't find the drive mounted at {dev.root}")
    return platform.current().fs_check(part.device)


def _block(n: int, i: int) -> bytes:
    # Unique data per position: fake cards wrap writes around, so repeated data would read back "fine".
    return random.Random(n << 32 | i).randbytes(BLOCK)


def test_dir(dev: Device, cfg: dict) -> Path:
    return dev.root / cfg["diag"]["test_folder"]


def fill(folder: Path, free: int, file_mb: int, progress, stop) -> int:
    """Write seeded 1 MB blocks into folder until `free` bytes are used. Returns bytes written."""
    folder.mkdir(parents=True, exist_ok=True)
    total, done, n = free // BLOCK, 0, 0
    while done < total and not stop():
        n += 1
        with open(folder / f"{n:04}.bin", "wb") as f:
            for i in range(min(file_mb, total - done)):
                if stop():
                    break
                f.write(_block(n, i))
                done += 1
                progress(done, total)
            f.flush()
            os.fsync(f.fileno())
    return done * BLOCK


def verify(folder: Path, progress, stop) -> tuple[int, int]:
    """Read the test files back. Returns (good bytes, bad bytes)."""
    found = sorted(folder.glob("*.bin"))
    total, done, good, bad = sum(f.stat().st_size for f in found) // BLOCK, 0, 0, 0
    for f in found:
        with open(f, "rb") as fh:
            i = 0
            while (chunk := fh.read(BLOCK)) and not stop():
                ok = chunk == _block(int(f.stem), i)[: len(chunk)]
                good, bad = good + ok * len(chunk), bad + (not ok) * len(chunk)
                i, done = i + 1, done + 1
                progress(done, total)
    return good, bad


def clean(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
