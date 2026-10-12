"""PC screen to the PSP: PSPM Screen (psp/pspm_screen) runs on the PSP and shows the frames this sends over the
USB cable. Like PSP to PC streaming, it works while the PSP is out of USB mode. No UI."""
import time
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from . import files as fm
from . import platform, tools
from .device import Device

W, H = 480, 272
HEADER = b"PSPMSCR1".ljust(512, b"\0")  # one USB packet before each frame, so the PSP can find the next one
APP = files("pspm").joinpath("psp_app/screen")  # prebuilt from psp/pspm_screen; rebuild when its C files change

# PSP 5650 pixels are little-endian R | G << 5 | B << 11: low byte from R and G, high byte from G and B.
_LO_R, _LO_G = [v >> 3 for v in range(256)], [(v >> 2 & 7) << 5 for v in range(256)]
_HI_G, _HI_B = [v >> 5 for v in range(256)], [v >> 3 << 3 for v in range(256)]


def install_app(dev: Device, cfg: dict, cat: str = "") -> Path:
    dest = fm.app_dir(dev, cfg["cast"]["app"], cat)
    for name in ("EBOOT.PBP", "pspm_usb.prx"):
        (dest / name).write_bytes(APP.joinpath(name).read_bytes())
    return dest


def installed(dev: Device, cfg: dict) -> bool:
    found = fm.find_app(dev, cfg["cast"]["app"])
    return bool(found) and (found / "pspm_usb.prx").is_file()


def frame(img: Image.Image) -> bytes:
    """Header + the image fitted into 480x272 (black bars), in the PSP's 16-bit format."""
    r, g, b = ImageOps.pad(img.convert("RGB"), (W, H), Image.Resampling.BILINEAR).split()
    lo = ImageChops.add(r.point(_LO_R), g.point(_LO_G))  # the bits don't overlap, so add is or
    hi = ImageChops.add(g.point(_HI_G), b.point(_HI_B))
    return HEADER + Image.merge("LA", (lo, hi)).tobytes()


def open_psp(cfg: dict, password: str | None = None):
    """The PSP running PSPM Screen, over libusb. On Linux the first call allows the PSP's USB (one password)."""
    import usb.core  # pyusb; imported here so the rest of PSPM never needs libusb

    c = cfg["cast"]
    rule = tools.ROOT / "60-psp-b-usb.rules"
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text(c["udev_rule"] + "\n")
    platform.current().stream_ready(rule, password)
    try:
        psp = usb.core.find(idVendor=c["vendor"], idProduct=c["product"], bcdDevice=c["bcd_device"])
    except usb.core.NoBackendError:
        raise OSError("libusb not found: install it from the SETUP page") from None
    if psp is None:
        raise OSError("PSP not found: start PSPM SCREEN on the PSP with the cable in")
    psp.set_configuration()
    return psp


def send(cfg: dict, stop, fps=lambda n: None, password: str | None = None) -> None:
    """Capture, convert and send frames until stop() is true; fps(frames sent in the last second) once a second."""
    c = cfg["cast"]
    psp, grab = open_psp(cfg, password), platform.current().capture
    gap, sent, t0 = 1 / c["max_fps"], 0, time.monotonic()
    while not stop():
        t = time.monotonic()
        psp.write(c["endpoint"], frame(grab(c)), c["timeout_ms"])
        sent += 1
        if t - t0 >= 1:
            fps(sent)
            sent, t0 = 0, t
        time.sleep(max(0.0, gap - (time.monotonic() - t)))
