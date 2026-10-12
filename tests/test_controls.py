from pspm import config, controls


class Out:
    def __init__(self):
        self.log = []

    def key(self, t, down):
        self.log.append((t, down))

    def stick(self, x, y):
        self.log.append(("stick", x, y))

    def move(self, dx, dy):
        self.log.append(("move", dx, dy))


def test_buttons_press_and_release_their_targets(tmp_path):
    cfg = config.load(tmp_path / "x")
    m = {"buttons": {"cross": "space", "circle": "none", "l": "mouse_left"}, "stick": "none"}
    out = Out()
    mp = controls.Mapper(m, out, cfg["cast"])
    steps = []
    for buttons in (controls.BUTTONS["cross"] | controls.BUTTONS["circle"], controls.BUTTONS["cross"] |
                    controls.BUTTONS["l"], controls.BUTTONS["cross"], 0):
        if buttons == controls.BUTTONS["cross"]:
            m["buttons"]["cross"] = "enter"  # picked on the page while held: the old key lets go
        mp.update(buttons, 128, 128)
        steps.append(set(out.log))
        out.log.clear()
    assert steps == [{("space", True)}, {("mouse_left", True)}, {("enter", True), ("space", False),
                                                                  ("mouse_left", False)}, {("enter", False)}]


def test_stick_modes(tmp_path):
    cfg = config.load(tmp_path / "x")
    m, out = {"buttons": {}, "stick": "wasd"}, Out()
    mp = controls.Mapper(m, out, cfg["cast"])
    mp.update(0, 0, 140)  # far left, inside the dead zone up/down
    m["stick"] = "mouse"
    mp.update(0, 255, 128)
    m["stick"] = "pad"
    mp.update(0, 0, 255)
    assert out.log == [("a", True), ("a", False), ("move", 12, 0), ("stick", -128, 127)]


def test_saved_picks_override_defaults_and_packets_parse(tmp_path):
    cfg = config.load(tmp_path / "x")
    m = controls.load(cfg, tmp_path / "c.json")
    assert m["buttons"]["cross"] == "pad_a" and m["stick"] == "pad"
    m["buttons"]["cross"], m["stick"] = "space", "mouse"
    controls.save(m, tmp_path / "c.json")
    again = controls.load(cfg, tmp_path / "c.json")
    assert again["buttons"]["cross"] == "space" and again["buttons"]["circle"] == "pad_b" and again["stick"] == "mouse"
    assert controls.parse(b"PSPMPAD1" + bytes([0, 0x40, 0, 0, 10, 250, 0, 0])) == (0x4000, 10, 250)
    assert controls.parse(b"PSPMSCR2" + bytes(8)) is None
    assert set(cfg["cast"]["buttons"]) == set(controls.BUTTONS)
    targets = set(controls.KEYS + controls.MOUSE + controls.PAD)
    assert set(cfg["cast"]["buttons"].values()) <= targets
