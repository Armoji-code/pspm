"""Two-way SAVEDATA sync between the PSP and PPSSPP. No UI.

Each save folder (e.g. ULUS10041DATA00) is copied whole. The content hash from the last sync
decides the direction: the side that changed since then wins. If both changed, or there is no
history yet, it's a conflict for the user, with the newest side suggested. Overwritten folders
are moved to a backup first and deletions are never synced, so no save is lost silently.
"""
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path, user_documents_path

from . import platform

STATE = user_data_path("pspm") / "saves.json"
BACKUPS = user_data_path("pspm") / "save-backups"
TO_PC, TO_PSP, SAME, SKIP = "PSP → PC", "PC → PSP", "SAME", "SKIP"
TMP = ".pspm-tmp"


@dataclass
class Item:
    name: str
    psp_hash: str  # "" when the folder is missing on that side
    pc_hash: str
    psp_time: float
    pc_time: float
    action: str
    conflict: bool = False


def digest(folder: Path) -> str:
    """Content hash; mtimes aren't used because FAT rounds them and shifts them with DST."""
    if not folder.is_dir():
        return ""
    h = hashlib.sha1()
    for p in sorted((p for p in folder.rglob("*") if p.is_file()), key=lambda p: p.relative_to(folder).as_posix()):
        data = p.read_bytes()
        h.update(f"{p.relative_to(folder).as_posix()}\0{len(data)}\0".encode() + data)
    return h.hexdigest()


def newest(folder: Path) -> float:
    return max((p.stat().st_mtime for p in folder.rglob("*") if p.is_file()), default=0.0) if folder.is_dir() else 0.0


def load_state(path: Path = STATE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"ppsspp": "", "last": {}}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


def ppsspp_dir(cfg: dict, state: dict) -> Path | None:
    """PPSSPP's SAVEDATA: the folder the user picked, else the first configured PSP folder that exists."""
    if state.get("ppsspp"):
        return Path(state["ppsspp"])
    docs = str(user_documents_path())
    for c in cfg["saves"]["ppsspp_paths"].get(platform.name(), []):
        p = Path(os.path.expandvars(c.replace("{documents}", docs))).expanduser()
        if p.is_dir():
            return p / "SAVEDATA"
    return None


def plan(psp: Path, pc: Path, last: dict) -> list[Item]:
    # shortcut: one sync history for all PSPs; a second PSP's saves show as changes (still backed up). Key by PSP if needed.
    names = sorted({p.name for d in (psp, pc) if d.is_dir() for p in d.iterdir() if p.is_dir() and TMP not in p.name})
    out = []
    for n in names:
        a, b = digest(psp / n), digest(pc / n)
        ta, tb = newest(psp / n), newest(pc / n)
        conflict = False
        if a == b:
            action = SAME
        elif not b or last.get(n) == b:  # missing on PC, or only the PSP changed
            action = TO_PC
        elif not a or last.get(n) == a:
            action = TO_PSP
        else:
            action, conflict = (TO_PC if ta >= tb else TO_PSP), True
        out.append(Item(n, a, b, ta, tb, action, conflict))
    return out


def apply(items: list[Item], psp: Path, pc: Path, last: dict, backups: Path = BACKUPS) -> Path:
    """Run each item's action and record it in `last`. Returns this run's backup folder."""
    run = backups / time.strftime("%Y%m%d-%H%M%S")
    for it in items:
        if it.action == TO_PC:
            _copy(psp / it.name, pc / it.name, run / "pc")
        elif it.action == TO_PSP:
            _copy(pc / it.name, psp / it.name, run / "psp")
        if it.action != SKIP:
            last[it.name] = it.pc_hash if it.action == TO_PSP else it.psp_hash
    return run


def _copy(src: Path, dst: Path, backup: Path) -> None:
    # Copy beside dst first, so an unplug mid-copy leaves the old save in place.
    tmp = dst.with_name(dst.name + TMP)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp)
    if dst.exists():
        backup.mkdir(parents=True, exist_ok=True)
        shutil.move(dst, backup / dst.name)
    tmp.rename(dst)
