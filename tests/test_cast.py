import io
import random
import struct

import pytest
from PIL import Image

from pspm import cast, config, device, platform


def test_frame_is_header_then_letterboxed_jpeg():
    data = cast.frame(Image.new("RGB", (48, 48), (255, 128, 8)), 80)  # square: black bars left and right
    size = struct.unpack_from("<I", data, 8)[0]
    assert data[:8] == b"PSPMSCR2" and data[12] == 0 and len(data) % 512 == 0 and 512 + size <= len(data) < 1024 + size
    pic = Image.open(io.BytesIO(data[512:512 + size]))
    assert pic.size == (480, 272) and pic.format == "JPEG" and not pic.info.get("progressive")
    assert pic.getpixel((0, 0)) == (0, 0, 0) and abs(pic.getpixel((240, 136))[0] - 255) < 8


def test_busy_frame_drops_quality_to_fit_the_psp():
    noise = Image.frombytes("RGB", (480, 272), random.Random(1).randbytes(480 * 272 * 3))
    assert struct.unpack_from("<I", cast.frame(noise, 100), 8)[0] <= cast.MAX_JPEG


def test_install_app_copies_eboot_and_driver(tmp_path):
    cfg = config.load(tmp_path / "x")
    dev = device.Device(tmp_path, cfg["device"]["folders"])
    assert not cast.installed(dev, cfg)
    dest = cast.install_app(dev, cfg)
    assert cast.installed(dev, cfg) and (dest / "EBOOT.PBP").read_bytes()[:4] == b"\0PBP"


def test_send_writes_frames_and_maps_psp_buttons_until_stopped(tmp_path, monkeypatch):
    cfg = config.load(tmp_path / "x")
    cfg["cast"]["max_fps"] = 1000
    sent, did, seen = [], [], []
    pad = [b"PSPMPAD1" + struct.pack("<IBB", 0x4000, 128, 128), b"PSPMPAD1" + struct.pack("<IBB", 0, 128, 128)]

    class Psp:
        def write(self, ep, data, timeout):
            sent.append((ep, data[:8]))

        def read(self, ep, size, timeout):
            import usb.core

            if not pad:
                raise usb.core.USBTimeoutError("timeout")
            return pad.pop(0)

    class Input:
        def __init__(self, name):
            pass

        def key(self, t, down):
            did.append((t, down))

        def stick(self, x, y):
            pass

        def close(self):
            did.append("closed")

    monkeypatch.setattr(cast, "open_psp", lambda cfg, pw: Psp())
    monkeypatch.setattr(platform.current(), "capture", lambda c: Image.new("RGB", (640, 480)), raising=False)
    monkeypatch.setattr(platform.current(), "Input", Input)
    m = {"active": 0, "profiles": [{"name": "A", "buttons": {"cross": "space"}, "stick": "pad"}]}
    cast.send(cfg, lambda: len(sent) >= 3 and not pad, pressed=lambda *s: seen.append(s), m=m)
    assert sent[:3] == [(cfg["cast"]["endpoint"], b"PSPMSCR2")] * 3
    assert did == [("space", True), ("space", False), "closed"] and seen == [(0x4000, 128, 128), (0, 128, 128)]


def test_open_says_how_to_start_the_psp_app(tmp_path, monkeypatch):
    import usb.core

    cfg = config.load(tmp_path / "x")
    monkeypatch.setattr(cast.stream.tools, "ROOT", tmp_path)
    monkeypatch.setattr(platform.current(), "stream_ready", lambda rule, pw: None)
    monkeypatch.setattr(usb.core, "find", lambda **kw: None)
    with pytest.raises(OSError, match="PSPM SCREEN"):
        cast.open_psp(cfg)
    assert "01c9" in (tmp_path / "60-psp-b-usb.rules").read_text() and "uinput" in (tmp_path / "60-psp-b-usb.rules").read_text()


def test_install_app_into_category_moves_old_copy(tmp_path):
    cfg = config.load(tmp_path / "x")
    dev = device.Device(tmp_path, cfg["device"]["folders"])
    cast.install_app(dev, cfg)
    dest = cast.install_app(dev, cfg, "CAT_Tools")
    assert dest == dev.path("games") / "CAT_Tools" / "PSPM_SCREEN" and cast.installed(dev, cfg)
    assert not (dev.path("games") / "PSPM_SCREEN").exists()


def test_slow_capture_runs_in_parallel(tmp_path, monkeypatch):
    import time

    cfg = config.load(tmp_path / "x")
    cfg["cast"] |= {"max_fps": 1000, "captures": 4}
    sent, busy, most = [], [], []

    class Psp:
        def write(self, ep, data, timeout):
            sent.append(data)

        def read(self, ep, size, timeout):
            import usb.core

            raise usb.core.USBTimeoutError("timeout")

    def capture(c):
        busy.append(1)
        most.append(len(busy))
        time.sleep(0.1)  # like grim
        busy.pop()
        return Image.new("RGB", (64, 36))

    class Input:
        def __init__(self, name):
            pass

        def key(self, t, down):
            pass

        def stick(self, x, y):
            pass

        def close(self):
            pass

    monkeypatch.setattr(cast, "open_psp", lambda cfg, pw: Psp())
    monkeypatch.setattr(platform.current(), "capture", capture, raising=False)
    monkeypatch.setattr(platform.current(), "Input", Input)
    cast.send(cfg, lambda: len(sent) >= 12, m={"active": 0, "profiles": [{"name": "A", "buttons": {}, "stick": "pad"}]})
    assert len(sent) >= 12 and max(most) >= 2  # grabs overlap (how many depends on thread start-up timing)


def test_psp_preset_picks_quality(tmp_path, monkeypatch):
    cfg = config.load(tmp_path / "x")
    used, pad = [], [b"PSPMPAD1" + struct.pack("<IBBB", 0, 128, 128, 2)]

    class Psp:
        def write(self, ep, data, timeout):
            pass

        def read(self, ep, size, timeout):
            import usb.core

            if not pad:
                raise usb.core.USBTimeoutError("timeout")
            return pad.pop(0)

    class Input:
        def __init__(self, name):
            pass

        def key(self, t, down):
            pass

        def stick(self, x, y):
            pass

        def close(self):
            pass

    monkeypatch.setattr(cast, "open_psp", lambda cfg, pw: Psp())
    monkeypatch.setattr(cast, "frame", lambda img, q, note: used.append(q) or b"")
    monkeypatch.setattr(platform.current(), "capture", lambda c: Image.new("RGB", (8, 8)), raising=False)
    monkeypatch.setattr(platform.current(), "Input", Input)
    cast.send(cfg, lambda: len(used) > 5 and not pad, m={"active": 0, "profiles": [{"name": "A", "buttons": {}, "stick": "pad"}]})
    assert used[-1] == cfg["cast"]["presets"]["best_fps"]["quality"]


def test_frame_carries_a_note():
    data = cast.frame(Image.new("RGB", (8, 8)), 80, "CONTROLS: GAMING")
    assert data[12:29] == b"CONTROLS: GAMING\0"


def test_note_button_turns_on_pc_sound(tmp_path, monkeypatch):
    cfg = config.load(tmp_path / "x")
    sent, pad = [], [b"PSPMPAD1" + struct.pack("<IBBBB", 0, 128, 128, 1, 1)]

    class Psp:
        def write(self, ep, data, timeout):
            sent.append(bytes(data[:16]))

        def read(self, ep, size, timeout):
            import usb.core

            if not pad:
                raise usb.core.USBTimeoutError("timeout")
            return pad.pop(0)

    class Sound:
        def __init__(self, c, rate):
            self.rate = rate

        def read(self, n):
            return bytes(n)

        def close(self):
            sent.append("closed")

    class Input:
        def __init__(self, name):
            pass

        def key(self, t, down):
            pass

        def stick(self, x, y):
            pass

        def close(self):
            pass

    monkeypatch.setattr(cast, "open_psp", lambda cfg, pw: Psp())
    monkeypatch.setattr(platform.current(), "sound", Sound, raising=False)
    monkeypatch.setattr(platform.current(), "capture", lambda c: Image.new("RGB", (8, 8)), raising=False)
    monkeypatch.setattr(platform.current(), "Input", Input)
    cast.send(cfg, lambda: any(s[:8] == b"PSPMAUD1" for s in sent if s != "closed"),
              m={"active": 0, "profiles": [{"name": "A", "buttons": {}, "stick": "pad"}]})
    aud = next(s for s in sent if s != "closed" and s[:8] == b"PSPMAUD1")
    assert struct.unpack_from("<II", aud, 8) == (cfg["cast"]["audio_block"], 22050) and sent[-1] == "closed"
