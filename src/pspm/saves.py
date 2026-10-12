"""Two-way SAVEDATA sync between the PSP and PPSSPP. No UI.

Each save folder (e.g. ULUS10041DATA00) is copied whole. The content hash from the last sync
decides the direction: the side that changed since then wins. If both changed, or there is no
history yet, it's a conflict for the user, with the newest side suggested. Overwritten folders
are moved to a backup first and deletions are never synced, so no save is lost silently.
Plan, apply and the state file are shared by the PPSSPP SAVES and CLOUD BACKUP pages: hold LOCK.
"""
import hashlib
import json
import os
import shutil
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path, user_documents_path

from . import platform
from .files import sfo

STATE = user_data_path("pspm") / "saves.json"
BACKUPS = user_data_path("pspm") / "save-backups"
TO_PC, TO_PSP, SAME, SKIP = "PSP → PC", "PC → PSP", "SAME", "SKIP"
TMP = ".pspm-tmp"
LOCK = threading.Lock()


@dataclass
class Item:
    name: str
    psp_hash: str  # "" when the folder is missing on that side
    pc_hash: str
    psp_time: float
    pc_time: float
    action: str
    conflict: bool = False
    psp_desc: str = ""  # the game's own text from PARAM.SFO, e.g. "COMPLETION : 1%"
    pc_desc: str = ""


def _files(folder: Path) -> list[tuple[str, Path, os.stat_result]]:
    return sorted((p.relative_to(folder).as_posix(), p, p.stat()) for p in folder.rglob("*") if p.is_file())


def _sig(files) -> str:
    return hashlib.sha1(repr([(r, s.st_size, s.st_mtime_ns) for r, _, s in files]).encode()).hexdigest()


def digest(folder: Path, cache: dict | None = None) -> str:
    """Content hash. Mtimes alone can't say two sides match (FAT rounds them and shifts them with DST), but the
    same file list, sizes and mtimes as last time means the same content, so `cache` skips re-reading over USB."""
    if not folder.is_dir():
        return ""
    files = _files(folder)
    sig, hit = _sig(files), (cache or {}).get(str(folder))
    if hit and hit[0] == sig:
        return hit[1]
    h = hashlib.sha1()
    for rel, p, _ in files:
        data = p.read_bytes()
        h.update(f"{rel}\0{len(data)}\0".encode() + data)
    if cache is not None:
        cache[str(folder)] = [sig, h.hexdigest()]
    return h.hexdigest()


def describe(folder: Path) -> str:
    """The save's title and detail text, as the PSP's save menu shows them."""
    try:
        info = sfo((folder / "PARAM.SFO").read_bytes())
    except (OSError, struct.error, ValueError):  # missing or broken: just no description
        return ""
    return " · ".join(" ".join(str(info.get(k, "")).split()) for k in ("SAVEDATA_TITLE", "SAVEDATA_DETAIL") if info.get(k))


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


def plan(psp: Path, pc: Path, last: dict, cache: dict | None = None) -> list[Item]:
    """`cache` (state["hashes"]) is pruned to the folders seen now."""
    seen = {}
    # shortcut: one sync history for all PSPs; a second PSP's saves show as changes (still backed up). Key by PSP if needed.
    names = sorted({p.name for d in (psp, pc) if d.is_dir() for p in d.iterdir() if p.is_dir() and TMP not in p.name})
    out = []
    for n in names:
        a, b = digest(psp / n, cache), digest(pc / n, cache)
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
        out.append(Item(n, a, b, ta, tb, action, conflict, describe(psp / n), describe(pc / n)))
        if cache is not None:
            seen.update({k: cache[k] for k in (str(psp / n), str(pc / n)) if k in cache})
    if cache is not None:
        cache.clear()
        cache.update(seen)
    return out


def apply(items: list[Item], psp: Path, pc: Path, last: dict, backups: Path = BACKUPS, cache: dict | None = None) -> Path:
    """Run each item's action and record it in `last`. Returns this run's backup folder.
    An item whose folders changed since it was planned is set to SKIP: its plan is stale."""
    run = backups / time.strftime("%Y%m%d-%H%M%S")
    for it in items:
        if it.action in (TO_PC, TO_PSP) and (digest(psp / it.name, cache), digest(pc / it.name, cache)) != (
            it.psp_hash, it.pc_hash
        ):
            it.action = SKIP
        if it.action == TO_PC:
            _copy(psp / it.name, pc / it.name, run / "pc", it.psp_hash, cache)
        elif it.action == TO_PSP:
            _copy(pc / it.name, psp / it.name, run / "psp", it.pc_hash, cache)
        if it.action != SKIP:
            last[it.name] = it.pc_hash if it.action == TO_PSP else it.psp_hash
    return run


def _copy(src: Path, dst: Path, backup: Path, h: str, cache: dict | None) -> None:
    # Copy beside dst first, so an unplug mid-copy leaves the old save in place.
    tmp = dst.with_name(dst.name + TMP)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp)
    if dst.exists():
        backup.mkdir(parents=True, exist_ok=True)
        shutil.move(dst, backup / dst.name)
    tmp.rename(dst)
    if cache is not None:  # the copy has src's content, so the next check needn't read it back
        cache[str(dst)] = [_sig(_files(dst)), h]
