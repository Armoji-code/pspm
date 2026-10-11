import ctypes.util
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


def fs_check(device: str) -> tuple[int, str]:
    # pkexec asks for the password in a desktop dialog; reading the raw partition needs root.
    r = subprocess.run(["pkexec", "fsck.vfat", "-n", device], capture_output=True, text=True, errors="replace",
                       check=False)
    return r.returncode, r.stdout + r.stderr


UDEV_RULE = Path("/etc/udev/rules.d/60-psp-b-usb.rules")


def stream_ready(rule: Path) -> None:
    # The viewer is a Windows app run in Wine; it reaches the PSP through the host's libusb 0.1.
    if not shutil.which("wine"):
        raise OSError("install Wine to run the viewer")
    if not ctypes.util.find_library("usb-0.1"):
        raise OSError("install libusb 0.1 (libusb-0.1-4 on Debian/Ubuntu, libusb-0_1-4 on openSUSE)")
    if not UDEV_RULE.exists():  # one password prompt, then the PSP's streaming USB is yours without root
        subprocess.run(["pkexec", "sh", "-c", f'cp "$1" {UDEV_RULE} && udevadm control --reload-rules && udevadm trigger',
                        "sh", str(rule)], check=True)
