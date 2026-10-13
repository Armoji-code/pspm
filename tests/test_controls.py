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
    p = {"name": "A", "buttons": {"cross": "space", "circle": "none", "l": "mouse_left"}, "stick": "none"}
    out = Out()
    mp = controls.Mapper({"active": 0, "profiles": [p]}, out, cfg["cast"])
    steps = []
    for buttons in (controls.BUTTONS["cross"] | controls.BUTTONS["circle"], controls.BUTTONS["cross"] |
                    controls.BUTTONS["l"], controls.BUTTONS["cross"], 0):
        if buttons == controls.BUTTONS["cross"]:
            p["buttons"]["cross"] = "enter"  # picked on the page while held: the old key lets go
        mp.update(buttons, 128, 128)
        steps.append(set(out.log))
        out.log.clear()
    assert steps == [{("space", True)}, {("mouse_left", True)}, {("enter", True), ("space", False),
                                                                  ("mouse_left", False)}, {("enter", False)}]


def test_stick_modes(tmp_path):
    cfg = config.load(tmp_path / "x")
    m, out = {"name": "A", "buttons": {}, "stick": "wasd"}, Out()
    mp = controls.Mapper({"active": 0, "profiles": [m]}, out, cfg["cast"])
    mp.update(0, 0, 140)  # far left, inside the dead zone up/down
    m["stick"] = "mouse"
    mp.update(0, 255, 128)
    m["stick"] = "pad"
    mp.update(0, 0, 255)
    assert out.log == [("a", True), ("a", False), ("move", 12, 0), ("stick", -128, 127)]


def test_profiles_default_save_and_parse(tmp_path):
    cfg = config.load(tmp_path / "x")
    m = controls.load(cfg, tmp_path / "c.json")
    assert [p["name"] for p in m["profiles"]] == ["DESKTOP", "GAMING"] and m["active"] == 0
    desk = controls.current(m)
    assert desk["stick"] == "mouse" and desk["buttons"]["l"] == "mouse_left" and desk["buttons"]["cross"] == "enter"
    m["active"] = 1
    controls.current(m)["buttons"]["cross"] = "space"
    controls.save(m, tmp_path / "c.json")
    again = controls.load(cfg, tmp_path / "c.json")
    assert again["active"] == 1 and controls.current(again)["buttons"]["cross"] == "space"
    (tmp_path / "old.json").write_text('{"buttons": {"cross": "space"}, "stick": "pad"}')  # before profiles
    assert len(controls.load(cfg, tmp_path / "old.json")["profiles"]) == 2
    assert controls.parse(b"PSPMPAD1" + bytes([0, 0x40, 0, 0, 10, 250, 0, 0])) == (0x4000, 10, 250)
    assert controls.parse(b"PSPMSCR2" + bytes(8)) is None
    targets = set(controls.KEYS + controls.MOUSE + controls.PAD)
    for p in cfg["cast"]["profiles"]:
        assert set(p["buttons"]) <= set(controls.BUTTONS) - {controls.SWITCH} and set(p["buttons"].values()) <= targets


def test_select_steps_through_profiles(tmp_path):
    cfg = config.load(tmp_path / "x")
    m, out, names = controls.load(cfg, tmp_path / "c.json"), Out(), []
    mp = controls.Mapper(m, out, cfg["cast"], names.append)
    sel, cross = controls.BUTTONS["select"], controls.BUTTONS["cross"]
    for b in (cross, 0, sel, sel, 0, cross, 0, sel, 0):  # held SELECT counts once
        mp.update(b, 128, 128)
    assert names == ["GAMING", "DESKTOP"] and ("enter", True) in out.log and ("pad_a", True) in out.log


def test_select_start_zooms_instead_of_switching(tmp_path):
    cfg = config.load(tmp_path / "x")
    m, out, names, zooms = controls.load(cfg, tmp_path / "c.json"), Out(), [], []
    mp = controls.Mapper(m, out, cfg["cast"], names.append, lambda: zooms.append(1))
    sel, start = controls.BUTTONS["select"], controls.BUTTONS["start"]
    for b in (sel, sel | start, sel, sel | start, 0, start, 0):
        mp.update(b, 128, 128)
    assert zooms == [1, 1] and names == [] and out.log.count(("esc", True)) == 1  # only the lone START
