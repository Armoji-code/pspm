import os
import shlex
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
