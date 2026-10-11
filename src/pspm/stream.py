"""PSP screen to the PC with RemoteJoyLite (github.com/Kethen/RemoteJoyLite), downloaded on first use. No UI.

A plugin on the PSP sends the screen over the USB cable to a viewer on the PC. The PSP's USB can't be a drive
and a stream at once, so the plugin is installed in USB mode and streams after the PSP leaves it.
"""
import subprocess
from pathlib import Path

from . import deps, platform, tools
from .device import Device


def tool(cfg: dict, root: Path = tools.ROOT) -> Path:
    """Folder with RemoteJoyLite's release, downloaded and checked against its sha256 the first time."""
    t = cfg["stream"]["tool"]
    return tools.fetch(t["url"], t["sha256"], root / f"remotejoylite-{t['version']}", t["timeout_seconds"])


def _lists(dev: Device, cfg: dict, add: bool) -> None:
    """Drop every line naming the plugin from the plugin lists, then (add) append the lines that load it."""
    s = cfg["stream"]
    name = Path(s["plugin"]).name.casefold()
    for rel, lines in s["lists"].items():
        f = dev.root / rel
        old = f.read_text(errors="replace").splitlines() if f.exists() else []
        new = [x for x in old if name not in x.casefold()] + (lines if add else [])
        if new != old:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("".join(x + "\r\n" for x in new), newline="")


def installed(dev: Device, cfg: dict) -> bool:
    return (dev.root / cfg["stream"]["plugin"]).is_file()


def install(dev: Device, cfg: dict, home: Path | None = None) -> None:
    """Copy the plugin to the stick and turn it on in ARK's and PRO/ME's plugin lists."""
    dest = dev.root / cfg["stream"]["plugin"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(((home or tool(cfg)) / cfg["stream"]["tool"]["prx"]).read_bytes())
    _lists(dev, cfg, add=True)


def remove(dev: Device, cfg: dict) -> None:
    _lists(dev, cfg, add=False)
    (dev.root / cfg["stream"]["plugin"]).unlink(missing_ok=True)


def start(cfg: dict, home: Path | None = None) -> subprocess.Popen:
    """Open the viewer window. Raises with what's missing on this PC (driver, wine...) instead of failing silently."""
    if gone := deps.missing(cfg, "stream"):
        raise OSError(f"install {', '.join(t['name'] for t in gone)} first, on the SETUP page")
    home = home or tool(cfg)
    platform.current().stream_ready(home / cfg["stream"]["tool"]["udev_rule"])
    cmd = [a.format(dir=home) for a in cfg["stream"]["run"][platform.name()]]
    return subprocess.Popen(cmd, cwd=home, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
