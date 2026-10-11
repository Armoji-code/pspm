import os
import shlex
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
