import subprocess
import winreg

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
