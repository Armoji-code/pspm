"""Diagnostics: PSP info file, memory stick report, filesystem check, speed and fake-capacity tests. Pure Python, no UI."""
import fnmatch
import os
import random
import shutil
import time
from pathlib import Path

import psutil

from . import platform
from .device import Device

BLOCK = 1 << 20
MODELS = {"01g": "PSP-1000", "02g": "PSP-2000", "03g": "PSP-3000", "04g": "PSP-3000", "05g": "PSP Go",
          "07g": "PSP-3000", "09g": "PSP-3000", "11g": "PSP-E1000 (Street)"}
HEN = {"0x400": "HEN D / SE-C", "0x700": "M33", "0x800": "M33", "0x1000": "TN / Adrenaline", "0x1003": "PRO"}
LANGUAGES = ["Japanese", "English", "French", "Spanish", "German", "Italian", "Dutch", "Portuguese", "Russian",
             "Korean", "Chinese (traditional)", "Chinese (simplified)"]
TOOLKIT = {"0": "retail", "1": "testing tool", "2": "development tool"}


def install_app(dev: Device, cfg: dict, cat: str = "") -> Path:
    """PSPM's PSP app; its INFO mode writes the report read_info reads."""
    from . import cast  # pulls in Pillow; only needed here

    return cast.install_app(dev, cfg, cat)


def read_info(dev: Device, cfg: dict) -> dict:
    """key=value lines written by the PSP app's INFO mode, plus 'written' (mtime). Empty if it never ran."""
    p = dev.root / cfg["diag"]["info_file"]
    if not p.is_file():
        return {}
    info = dict(line.strip().split("=", 1) for line in p.read_text(errors="replace").splitlines() if "=" in line)
    info["written"] = p.stat().st_mtime
    if (m := info.get("model")) in MODELS:
        info["model"] = f"{MODELS[m]} ({m})"
        info["ram"] = "32 MB" if m == "01g" else "64 MB"
    if (h := info.get("cfw_hen")) in HEN:
        info["cfw_hen"] = f"{HEN[h]} ({h})"
    if (v := info.get("cfw_se", "")).startswith("0x"):
        # shortcut: assumes ARK's major.minor.micro byte layout; the raw value stays visible for other CFWs.
        n = int(v, 16)
        info["cfw_se"] = f"{n >> 24}.{n >> 16 & 255}.{n >> 8 & 255} ({v})"
    if (lang := info.get("language", "")).isdigit() and int(lang) < len(LANGUAGES):
        info["language"] = LANGUAGES[int(lang)]
    if (t := info.get("toolkit")) in TOOLKIT:
        info["toolkit"] = TOOLKIT[t]
    return info


def _plugins(text: str) -> tuple[int, list[str]]:
    """(enabled, all names) from an ARK "where, path, on" or PRO "path 1" plugin list."""
    names, on = [], 0
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace(",", " ").split()]
        path = next((p for p in parts if ":" in p or "/" in p), parts[0])
        names.append(path.replace("\\", "/").rsplit("/", 1)[-1])
        on += parts[-1].lower() in ("on", "1", "true", "enabled")
    return on, names


def stick(dev: Device, cfg: dict) -> dict:
    """What PSPM can tell about the memory stick from the PC side. Walks every file once."""
    c = cfg["diag"]
    root = os.path.normcase(str(dev.root))
    part = next((p for p in psutil.disk_partitions() if os.path.normcase(p.mountpoint) == root), None)
    out = {}
    if part:
        out["filesystem"] = f"{part.fstype or '?'} on {part.device}"
    if hasattr(os, "statvfs"):
        out["cluster size"] = f"{os.statvfs(dev.root).f_bsize // 1024} KB"
    sizes = {label: [0, 0] for label in dev.folders}  # label -> [files, bytes]
    prefixes = {label: Path(rel).parts for label, rel in dev.folders.items()}
    files = total = junk = 0
    biggest = (0, "")
    for top, _, names in os.walk(dev.root):
        rel = Path(top).relative_to(dev.root).parts
        for n in names:
            try:
                b = os.stat(os.path.join(top, n)).st_size
            except OSError:
                continue
            files, total = files + 1, total + b
            junk += any(fnmatch.fnmatch(n, j) for j in c["junk"])
            biggest = max(biggest, (b, "/".join((*rel, n))))
            for label, pre in prefixes.items():
                if tuple(p.upper() for p in rel[: len(pre)]) == tuple(p.upper() for p in pre):
                    sizes[label][0] += 1
                    sizes[label][1] += b
    out["files"] = f"{files} files · {total / 2**30:.2f} GB"
    for label, (n, b) in sizes.items():
        out[label] = f"{n} files · {b / 2**20:.0f} MB"
    if biggest[1]:
        out["biggest file"] = f"{biggest[1]} · {biggest[0] / 2**20:.0f} MB"
    out["junk files"] = str(junk)
    cfws = [name for rel, name in c["cfw_folders"].items() if (dev.root / rel).is_dir()]
    out["cfw on stick"] = ", ".join(cfws) or "none found"
    for rel in c["plugin_lists"]:
        p = dev.root / rel
        if p.is_file():
            on, names = _plugins(p.read_text(errors="replace"))
            out[f"plugins ({p.name.lower()})"] = f"{on} on of {len(names)}: {', '.join(names)}" if names else "empty"
    return out


def speed(dev: Device, cfg: dict) -> tuple[float, float]:
    """(write, read) MB/s for a test file. Reading the largest existing file avoids the PC's cache of what was just written."""
    mb = cfg["diag"]["speed_test_mb"]
    p = dev.root / "PSPM_SPEED.BIN"  # not in test_folder, which may hold a pending capacity test
    data = random.randbytes(BLOCK)
    t = time.monotonic()
    try:
        with open(p, "wb") as f:
            f.writelines(data for _ in range(mb))
            f.flush()
            os.fsync(f.fileno())
        write = mb / (time.monotonic() - t)
    finally:
        p.unlink(missing_ok=True)
    big = max((f for f in dev.root.rglob("*") if f.is_file()), key=lambda f: f.stat().st_size, default=None)
    if big is None:
        return write, 0.0
    t, n = time.monotonic(), 0
    with open(big, "rb") as f:
        while n < mb * BLOCK and (chunk := f.read(BLOCK)):
            n += len(chunk)
    return write, n / 2**20 / (time.monotonic() - t)


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
