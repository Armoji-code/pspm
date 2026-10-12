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


def start(cfg: dict, home: Path | None = None, password: str | None = None) -> subprocess.Popen:
    """Open the viewer window. Raises with what's missing on this PC (driver, wine...) or why the viewer quit,
    instead of failing silently. Raises deps.NeedPassword when the USB rule needs a password and nothing can ask."""
    if gone := deps.missing(cfg, "stream"):
        raise OSError(f"install {', '.join(t['name'] for t in gone)} first, on the SETUP page")
    home = home or tool(cfg)
    platform.current().stream_ready(home / cfg["stream"]["tool"]["udev_rule"], password)
    cmd = [a.format(dir=home) for a in cfg["stream"]["run"][platform.name()]]
    log = home / "viewer.log"
    with log.open("wb") as out:
        p = subprocess.Popen(cmd, cwd=home, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    try:  # a viewer that can't start (wine, missing dll) quits within seconds; one still running is up
        code = p.wait(cfg["stream"]["check_seconds"])
    except subprocess.TimeoutExpired:
        return p
    tail = log.read_text(errors="replace").strip().splitlines()[-3:]
    raise OSError(f"viewer quit ({code}): {' / '.join(tail) or 'no output'} · full log: {log}")
