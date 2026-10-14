"""PSP controls on the PC: PSPM's PSP app sends the PSP's buttons and stick back over USB, and this turns them into
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
MAX_PROFILES = 6
SWITCH = "select"  # the PSP button that steps to the next profile (on release); it maps to nothing itself
ZOOM = "start"  # held SELECT + this steps the zoom; then SELECT doesn't switch profiles
# CONTROLLER mode (PPSSPP): every PSP button is a gamepad button in its usual place, SELECT included, whatever profiles
# say; SELECT neither switches profiles nor zooms there.
CONTROLLER = {"name": "CONTROLLER", "stick": "pad", "buttons": {
    "cross": "pad_a", "circle": "pad_b", "square": "pad_x", "triangle": "pad_y", "l": "pad_lb", "r": "pad_rb",
    "select": "pad_select", "start": "pad_start", "up": "pad_up", "down": "pad_down", "left": "pad_left",
    "right": "pad_right"}}


def load(cfg: dict, path: Path = FILE) -> dict:
    """{"active": index, "profiles": [{"name", "buttons": {psp button: target}, "stick": mode}]}: what was saved
    on the page, else the profiles in config ([[cast.profiles]])."""
    saved = json.loads(path.read_text()) if path.exists() else {}
    if not saved.get("profiles"):  # nothing saved yet, or the single mapping from before profiles
        saved = {"active": 0, "profiles": cfg["cast"]["profiles"]}
    profiles = [{"name": p["name"], "buttons": {**p.get("buttons", {})}, "stick": p.get("stick", "none")}
                for p in saved["profiles"][:MAX_PROFILES]]
    return {"active": min(saved.get("active", 0), len(profiles) - 1), "profiles": profiles}


def current(m: dict) -> dict:
    return m["profiles"][m["active"]]


def save(m: dict, path: Path = FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m, indent=1))


def parse(packet: bytes) -> tuple[int, int, int] | None:
    """(buttons, stick x, stick y) from the PSP app's "PSPMPAD1" packet; the stick is 0-255, 128 in the middle."""
    return struct.unpack_from("<IBB", packet, 8) if len(packet) >= 14 and packet[:8] == b"PSPMPAD1" else None


class Mapper:
    """Turns each PSP state into key(target, down) / stick(x, y) / move(dx, dy) calls on `out`, with the active
    profile of `m` (see load). Reads `m` on every update, so changes on the page apply at once. Letting go of
    SELECT steps to the next profile and calls switched(name); SELECT + START calls zoom() instead. With
    `fixed` set to a profile, that one is used as is and SELECT is a plain button."""

    def __init__(self, m: dict, out, c: dict, switched=lambda name: None, zoom=lambda: None):
        self.m, self.out, self.dead, self.speed, self.held = m, out, c["dead_zone"], c["mouse_speed"], set()
        self.switched, self.zoom, self.was, self.combo, self.fixed = switched, zoom, 0, False, None

    def update(self, buttons: int, lx: int, ly: int) -> None:
        if self.fixed:
            return self._apply(self.fixed, buttons, lx, ly)
        sel, start = BUTTONS[SWITCH], BUTTONS[ZOOM]
        if buttons & sel and buttons & ~self.was & start:
            self.combo = True
            self.zoom()
        elif self.was & ~buttons & sel:
            if not self.combo:
                self.m["active"] = (self.m["active"] + 1) % len(self.m["profiles"])
                self.switched(current(self.m)["name"])
            self.combo = False
        self.was = buttons
        if buttons & sel:
            buttons &= ~start  # START with SELECT is the zoom, not its own key
        self._apply(current(self.m), buttons & ~sel, lx, ly)

    def _apply(self, p: dict, buttons: int, lx: int, ly: int) -> None:
        want = {p["buttons"].get(n, "none") for n, bit in BUTTONS.items() if buttons & bit} - {"none"}
        x, y, mode, d = lx - 128, ly - 128, p["stick"], self.dead
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
