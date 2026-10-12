import struct

import pytest
from PIL import Image

from pspm import cast, config, device, platform


def test_frame_is_header_then_letterboxed_5650():
    data = cast.frame(Image.new("RGB", (48, 48), (255, 128, 8)))  # square: black bars left and right
    assert data[:512] == cast.HEADER and len(data) == 512 + 480 * 272 * 2 and len(data) % 512 == 0
    px = struct.unpack(f"<{480 * 272}H", data[512:])
    assert px[136 * 480 + 240] == 31 | 32 << 5 | 1 << 11 and px[0] == 0


def test_install_app_copies_eboot_and_driver(tmp_path):
    cfg = config.load(tmp_path / "x")
    dev = device.Device(tmp_path, cfg["device"]["folders"])
    assert not cast.installed(dev, cfg)
    dest = cast.install_app(dev, cfg)
    assert cast.installed(dev, cfg) and (dest / "EBOOT.PBP").read_bytes()[:4] == b"\0PBP"


def test_send_writes_frames_until_stopped(tmp_path, monkeypatch):
    cfg = config.load(tmp_path / "x")
    cfg["cast"]["max_fps"] = 1000
    sent = []

    class Psp:
        def write(self, ep, data, timeout):
            sent.append((ep, data[:8]))

    monkeypatch.setattr(cast, "open_psp", lambda cfg, pw: Psp())
    monkeypatch.setattr(platform.current(), "capture", lambda c: Image.new("RGB", (640, 480)), raising=False)
    cast.send(cfg, lambda: len(sent) >= 3)
    assert sent == [(cfg["cast"]["endpoint"], b"PSPMSCR1")] * 3


def test_open_says_how_to_start_the_psp_app(tmp_path, monkeypatch):
    import usb.core

    cfg = config.load(tmp_path / "x")
    monkeypatch.setattr(cast.tools, "ROOT", tmp_path)
    monkeypatch.setattr(platform.current(), "stream_ready", lambda rule, pw: None)
    monkeypatch.setattr(usb.core, "find", lambda **kw: None)
    with pytest.raises(OSError, match="PSPM SCREEN"):
        cast.open_psp(cfg)
    assert "01c9" in (tmp_path / "60-psp-b-usb.rules").read_text()
