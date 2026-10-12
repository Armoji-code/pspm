"""Links the plugged-in PSP's game folders into PPSSPP, so its games play straight off the PSP. No UI.

Unlike saves, nothing is copied: PPSSPP only reads games, so a link does no harm when the PSP is unplugged.
PSPM still removes the links on unplug, so PPSSPP's browser never shows a folder that leads nowhere.
"""
import os
from pathlib import Path

from . import platform


def link_dir(savedata: Path, cfg: dict) -> Path:
    """Folder in PPSSPP's memory stick root (next to its PSP folder) that holds the links."""
    return savedata.parent.parent / cfg["saves"]["games_folder"]


def _ours(p: Path) -> bool:
    try:
        os.readlink(p)  # symlink or Windows junction; a real folder raises
        return True
    except OSError:
        return False


def unlink(folder: Path) -> None:
    if folder.is_dir():
        for p in folder.iterdir():
            if _ours(p):
                platform.current().unlink_dir(p)


def link(dev, folder: Path, labels: list[str]) -> list[str]:
    """Point one link per [device.folders] label at the PSP; returns the labels linked."""
    unlink(folder)
    folder.mkdir(parents=True, exist_ok=True)
    done = []
    for label in labels:
        target, p = dev.path(label), folder / label
        if target.is_dir() and not p.exists():  # a real folder of the user's is left alone
            platform.current().link_dir(target, p)
            done.append(label)
    return done
