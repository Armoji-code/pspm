import subprocess
import tempfile
import winreg
from pathlib import Path

from . import launch_command

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "PSPM watcher"


def autostart_install() -> str:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(launch_command("watch")))
    return rf"HKCU\{KEY}\{NAME}"


def autostart_remove() -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        try:
            winreg.DeleteValue(k, NAME)
        except FileNotFoundError:
            pass


def mount_psps() -> None:
    pass  # Windows gives every USB drive a letter by itself


def fs_check(device: str) -> tuple[int, str]:
    # chkdsk needs admin: run it elevated (UAC prompt) and collect its output through a file.
    drive = device.rstrip("\\")  # "E:\\" -> "E:"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "chkdsk.txt"
        ps = (f"$p = Start-Process cmd -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
              f"-ArgumentList '/c chkdsk {drive} > \"{out}\" 2>&1'; exit $p.ExitCode")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, errors="replace", check=False)
        return r.returncode, out.read_text(errors="replace") if out.exists() else r.stderr


def stream_ready(rule: Path) -> None:
    pass  # the libusb-win32 driver is checked by deps ([deps.tools] in the config)
