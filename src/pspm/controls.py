"""PSP controls on the PC: PSPM Screen sends the PSP's buttons and stick back over USB, and this turns them into
keys, mouse or gamepad input through the OS layer (platform.<os>.Input). No UI."""
import json
import struct
from pathlib import Path

from platformdirs import user_config_path

FILE = user_config_path("pspm") / "psp_controls.json"
BUTTONS = {"select": 0x1, "start": 0x8, "up": 0x10, "right": 0x20, "down": 0x40, "left": 0x80, "l": 0x100, "r": 0x200,
           "triangle": 0x1000, "circle": 0x2000, "cross": 0x4000, "square": 0x8000}  # PSP_CTRL_* bits
KEYS = [*"abcdefghijklmnopqrstuvwxyz0123456789", "space", "enter", "esc", "tab", "backspace", "shift", "ctrl", "alt",
        "up", "down", "left", "right", *(f"f{i}" for i in range(1, 13))]
MOUSE = ["mouse_left", "mouse_right", "mouse_middle"]
PAD = ["pad_a", "pad_b", "pad_x", "pad_y", "pad_lb", "pad_rb", "pad_select", "pad_start", "pad_up", "pad_down",
       "pad_left", "pad_right"]
STICK = ["pad", "mouse", "arrows", "wasd", "none"]
_STICK_KEYS = {"arrows": ("left", "right", "up", "down"), "wasd": ("a", "d", "w", "s")}


def load(cfg: dict, path: Path = FILE) -> dict:
    """{"buttons": {psp button: target}, "stick": mode}: config defaults, then what was picked on the page."""
    saved = json.loads(path.read_text()) if path.exists() else {}
    return {"buttons": {**cfg["cast"]["buttons"], **saved.get("buttons", {})},
            "stick": saved.get("stick", cfg["cast"]["stick"])}


def save(m: dict, path: Path = FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m, indent=1))


def parse(packet: bytes) -> tuple[int, int, int] | None:
    """(buttons, stick x, stick y) from PSPM Screen's "PSPMPAD1" packet; the stick is 0-255, 128 in the middle."""
    return struct.unpack_from("<IBB", packet, 8) if len(packet) >= 14 and packet[:8] == b"PSPMPAD1" else None


class Mapper:
    """Turns each PSP state into key(target, down) / stick(x, y) / move(dx, dy) calls on `out`. Reads the mapping
    `m` on every update, so changes on the page apply at once."""

    def __init__(self, m: dict, out, c: dict):
        self.m, self.out, self.dead, self.speed, self.held = m, out, c["dead_zone"], c["mouse_speed"], set()

    def update(self, buttons: int, lx: int, ly: int) -> None:
        want = {self.m["buttons"].get(n, "none") for n, bit in BUTTONS.items() if buttons & bit} - {"none"}
        x, y, mode, d = lx - 128, ly - 128, self.m["stick"], self.dead
        if mode in _STICK_KEYS:
            left, right, up, down = _STICK_KEYS[mode]
            want |= {k for k, on in ((left, x < -d), (right, x > d), (up, y < -d), (down, y > d)) if on}
        for t in want - self.held:
            self.out.key(t, True)
        for t in self.held - want:
            self.out.key(t, False)
        self.held = want
        if mode == "pad":
            self.out.stick(x, y)
        elif mode == "mouse" and max(abs(x), abs(y)) > d:
            self.out.move(round(x * self.speed / 128), round(y * self.speed / 128))

    def release(self) -> None:
        self.update(0, 128, 128)
