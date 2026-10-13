import io
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


def stream_ready(rule: Path, password: str | None = None) -> None:
    # Wine and libusb 0.1 are checked by deps ([deps.tools] in the config).
    # one password prompt (again when PSPM's rule changes), then the PSP's streaming USB is yours without root
    if not UDEV_RULE.exists() or UDEV_RULE.read_bytes() != rule.read_bytes():
        from .. import deps  # deps imports platform
        r = deps.root(["pkexec", "sh", "-c", f'cp "$1" {UDEV_RULE} && udevadm control --reload-rules && udevadm trigger',
                       "sh", str(rule)], password)
        if r.returncode:
            raise OSError(f"couldn't allow the PSP's USB: {(r.stderr or r.stdout).strip()[-300:] or r.returncode}")


def link_dir(target: Path, link: Path) -> None:
    link.symlink_to(target, target_is_directory=True)


def unlink_dir(link: Path) -> None:
    link.unlink()


def capture(c: dict):
    """The whole screen as a Pillow image: grim on Wayland (wlroots: Sway, Hyprland...), else X11."""
    from PIL import Image, ImageGrab

    if os.environ.get("WAYLAND_DISPLAY"):
        return Image.open(io.BytesIO(subprocess.run(c["wayland_capture"], capture_output=True, check=True).stdout))
    return ImageGrab.grab()


class _Proc:
    def __init__(self, cmd):
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, n: int) -> bytes:
        return self.p.stdout.read(n)

    def close(self) -> None:
        self.p.kill()
        self.p.wait()


def sound(c: dict, rate: int):
    """What the PC is playing, as 16-bit stereo PCM at `rate`: read(n) bytes, close(). parec records the default
    output's monitor on PulseAudio and PipeWire."""
    return _Proc([a.format(rate=rate) for a in c["audio_capture"]])


# PSP controls as Linux input devices through /dev/uinput (allowed by the udev rule above). Codes from linux/input.h.
_KEYS = {c: n for row, first in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)) for n, c in enumerate(row, first)}
_KEYS |= {str(i % 10): i + 1 for i in range(1, 11)} | {f"f{i}": 58 + i for i in range(1, 11)} | {
    "f11": 87, "f12": 88, "esc": 1, "backspace": 14, "tab": 15, "enter": 28, "ctrl": 29, "shift": 42, "alt": 56,
    "space": 57, "up": 103, "left": 105, "right": 106, "down": 108,
    "mouse_left": 0x110, "mouse_right": 0x111, "mouse_middle": 0x112}
_PAD = {"pad_a": 0x130, "pad_b": 0x131, "pad_y": 0x133, "pad_x": 0x134, "pad_lb": 0x136, "pad_rb": 0x137,
        "pad_select": 0x13A, "pad_start": 0x13B, "pad_up": 0x220, "pad_down": 0x221, "pad_left": 0x222,
        "pad_right": 0x223}


def _uinput(name: str, keys, axes: dict) -> int:
    """A virtual device with these key codes and axes {type: [codes]}; returns its fd."""
    import fcntl
    import struct

    try:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        raise OSError(f"can't create the PSP's controls on this PC (/dev/uinput: {e.strerror})") from None
    fcntl.ioctl(fd, 0x40045564, 1)  # UI_SET_EVBIT EV_KEY
    for k in keys:
        fcntl.ioctl(fd, 0x40045565, k)  # UI_SET_KEYBIT
    for kind, codes in axes.items():
        fcntl.ioctl(fd, 0x40045564, kind)
        for a in codes:
            fcntl.ioctl(fd, 0x40045566 if kind == 2 else 0x40045567, a)  # UI_SET_RELBIT / UI_SET_ABSBIT
            if kind == 3:  # UI_ABS_SETUP: -128..127, small flat middle
                fcntl.ioctl(fd, 0x401C5504, struct.pack("=H2x6i", a, 0, -128, 127, 0, 8, 0))
    fcntl.ioctl(fd, 0x405C5503, struct.pack("=4H80sI", 6, 0, 0, 1, name.encode(), 0))  # UI_DEV_SETUP, BUS_VIRTUAL
    fcntl.ioctl(fd, 0x5501)  # UI_DEV_CREATE
    return fd


class Input:
    """Two devices: keyboard + mouse, and a gamepad (games and Steam treat a device that is also a keyboard badly)."""

    def __init__(self, name: str):
        self.kbd = _uinput(f"{name} keyboard", _KEYS.values(), {2: [0, 1]})  # REL_X, REL_Y
        self.pad = _uinput(f"{name} gamepad", _PAD.values(), {3: [0, 1]})  # ABS_X, ABS_Y

    def _emit(self, fd: int, *events) -> None:
        import struct

        os.write(fd, b"".join(struct.pack("llHHi", 0, 0, *e) for e in (*events, (0, 0, 0))))  # + SYN_REPORT

    def key(self, target: str, down: bool) -> None:
        fd, code = (self.pad, _PAD[target]) if target in _PAD else (self.kbd, _KEYS[target])
        self._emit(fd, (1, code, int(down)))

    def stick(self, x: int, y: int) -> None:
        self._emit(self.pad, (3, 0, x), (3, 1, y))

    def move(self, dx: int, dy: int) -> None:
        self._emit(self.kbd, (2, 0, dx), (2, 1, dy))

    def close(self) -> None:
        import fcntl

        for fd in (self.kbd, self.pad):
            fcntl.ioctl(fd, 0x5502)  # UI_DEV_DESTROY
            os.close(fd)
