import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from . import launch_command

ENTRY = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "pspm-watch.desktop"


def autostart_install() -> Path:
    ENTRY.parent.mkdir(parents=True, exist_ok=True)
    ENTRY.write_text(
        "[Desktop Entry]\nType=Application\nName=PSPM watcher\n"
        f"Exec={shlex.join(launch_command('watch'))}\nX-GNOME-Autostart-enabled=true\n"
    )
    return ENTRY


def autostart_remove() -> None:
    ENTRY.unlink(missing_ok=True)


_tried: set[str] = set()  # partitions mounted once per plug-in, so a manual unmount sticks


def mount_psps() -> None:
    """Mount PSP partitions the desktop left unmounted (no automount); udisks needs no root for USB drives."""
    try:
        out = subprocess.run(["lsblk", "-Jl", "-o", "PATH,PKNAME,MODEL,MOUNTPOINT,FSTYPE"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return
    devs = json.loads(out)["blockdevices"]
    psps = {d["path"].rsplit("/", 1)[-1] for d in devs if "PSP" in (d["model"] or "")}
    parts = {d["path"]: d["mountpoint"] for d in devs if d["pkname"] in psps and d["fstype"]}
    _tried.intersection_update(parts)  # unplugged ones get a fresh try next time
    for path, mountpoint in parts.items():
        if path not in _tried and not mountpoint and shutil.which("udisksctl"):
            _tried.add(path)
            subprocess.run(["udisksctl", "mount", "--no-user-interaction", "-b", path], capture_output=True,
                           check=False)


def fs_check(device: str) -> tuple[int, str]:
    # pkexec asks for the password in a desktop dialog; reading the raw partition needs root.
    r = subprocess.run(["pkexec", "fsck.vfat", "-n", device], capture_output=True, text=True, errors="replace",
                       check=False)
    return r.returncode, r.stdout + r.stderr


UDEV_RULE = Path("/etc/udev/rules.d/60-psp-b-usb.rules")


def stream_ready(rule: Path) -> None:
    # Wine and libusb 0.1 are checked by deps ([deps.tools] in the config).
    if not UDEV_RULE.exists():  # one password prompt, then the PSP's streaming USB is yours without root
        subprocess.run(["pkexec", "sh", "-c", f'cp "$1" {UDEV_RULE} && udevadm control --reload-rules && udevadm trigger',
                        "sh", str(rule)], check=True)
