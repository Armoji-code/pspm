import _winapi
import os
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


def stream_ready(rule: Path, password: str | None = None) -> None:
    pass  # the libusb-win32 driver is checked by deps ([deps.tools] in the config)


def link_dir(target: Path, link: Path) -> None:
    _winapi.CreateJunction(str(target), str(link))  # a junction, unlike a symlink, needs no admin or developer mode


def unlink_dir(link: Path) -> None:
    os.rmdir(link)  # removes the junction only, never the PSP folder behind it


def capture(c: dict):
    from PIL import ImageGrab

    return ImageGrab.grab()


# PSP controls as keyboard and mouse input through SendInput. Scan codes, so games that read DirectInput see them.
_VK = {c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"} | {f"f{i}": 0x6F + i for i in range(1, 13)} | {
    "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "backspace": 0x08, "shift": 0x10, "ctrl": 0x11,
    "alt": 0x12, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
_MOUSE = {"mouse_left": (0x2, 0x4), "mouse_right": (0x8, 0x10), "mouse_middle": (0x20, 0x40)}  # (down, up) flags


def _send(kind: int, **fields) -> None:
    import ctypes
    from ctypes import wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    i = INPUT(type=kind)
    for k, v in fields.items():
        setattr(i.mi if kind == 0 else i.ki, k, v)
    ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(i))


class Input:
    """shortcut: keyboard and mouse only; a gamepad on Windows needs a virtual-controller driver, so pad_* do nothing."""

    def __init__(self, name: str):
        pass

    def key(self, target: str, down: bool) -> None:
        import ctypes

        if target in _MOUSE:
            _send(0, dwFlags=_MOUSE[target][not down])
        elif target in _VK:
            vk = _VK[target]
            flags = 0x8 | (0x1 if vk in (0x25, 0x26, 0x27, 0x28) else 0) | (0 if down else 0x2)  # SCANCODE, EXTENDED, UP
            _send(1, wScan=ctypes.windll.user32.MapVirtualKeyW(vk, 0), dwFlags=flags)

    def stick(self, x: int, y: int) -> None:
        pass

    def move(self, dx: int, dy: int) -> None:
        _send(0, dx=dx, dy=dy, dwFlags=0x1)  # MOUSEEVENTF_MOVE

    def close(self) -> None:
        pass
