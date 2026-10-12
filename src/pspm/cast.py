"""PC screen to the PSP: PSPM Screen (psp/pspm_screen) runs on the PSP and shows the frames this sends over the
USB cable. Like PSP to PC streaming, it works while the PSP is out of USB mode. No UI."""
import io
import struct
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageOps

from . import controls, platform, stream
from . import files as fm
from .device import Device

W, H = 480, 272
MAX_JPEG = 256 * 1024  # PSPM Screen's frame buffer
APP = files("pspm").joinpath("psp_app/screen")  # prebuilt from psp/pspm_screen; rebuild when its C files change


def install_app(dev: Device, cfg: dict, cat: str = "") -> Path:
    dest = fm.app_dir(dev, cfg["cast"]["app"], cat)
    for name in ("EBOOT.PBP", "pspm_usb.prx"):
        (dest / name).write_bytes(APP.joinpath(name).read_bytes())
    return dest


def installed(dev: Device, cfg: dict) -> bool:
    found = fm.find_app(dev, cfg["cast"]["app"])
    return bool(found) and (found / "pspm_usb.prx").is_file()


def frame(img: Image.Image, quality: int) -> bytes:
    """The image fitted into 480x272 (black bars) as a baseline 4:2:0 JPEG, which the PSP decodes in hardware,
    after a one-packet header ("PSPMSCR2", size) the PSP finds the next frame by. Padded to whole USB packets."""
    pic = ImageOps.pad(img.convert("RGB"), (W, H), Image.Resampling.BILINEAR)
    for q in (quality, 50, 25):  # a very busy screen at high quality can outgrow the PSP's buffer
        out = io.BytesIO()
        pic.save(out, "JPEG", quality=q, subsampling=2)
        data = out.getvalue()
        if len(data) <= MAX_JPEG:
            break
    return (b"PSPMSCR2" + struct.pack("<I", len(data))).ljust(512, b"\0") + data.ljust(-(-len(data) // 512) * 512, b"\0")


def open_psp(cfg: dict, password: str | None = None):
    """The PSP running PSPM Screen, over libusb. On Linux the first call allows the PSP's USB (one password)."""
    import usb.core  # pyusb; imported here so the rest of PSPM never needs libusb

    c = cfg["cast"]
    platform.current().stream_ready(stream.usb_rule(cfg), password)
    try:
        psp = usb.core.find(idVendor=c["vendor"], idProduct=c["product"], bcdDevice=c["bcd_device"])
    except usb.core.NoBackendError:
        raise OSError("libusb not found: install it from the SETUP page") from None
    if psp is None:
        raise OSError("PSP not found: start PSPM SCREEN on the PSP with the cable in")
    psp.set_configuration()
    return psp


def _pad(psp, c: dict, mapper: controls.Mapper, stop, pressed) -> None:
    """Read the PSP's controls until stop(); every packet goes to the mapper and pressed(buttons, x, y)."""
    import usb.core

    try:
        while not stop():
            try:
                state = controls.parse(bytes(psp.read(c["pad_endpoint"], 512, 200)))
            except usb.core.USBTimeoutError:
                continue
            if state:
                mapper.update(*state)
                pressed(*state)
    except usb.core.USBError:
        pass  # cable out: the frame loop reports it
    finally:
        mapper.release()


def send(cfg: dict, stop, fps=lambda n, ms: None, password: str | None = None, m: dict | None = None,
         pressed=lambda buttons, lx, ly: None) -> None:
    """Send the screen until stop() is true, and turn the PSP's controls into PC input with mapping m (see
    controls.load). fps(frames sent, average capture ms) once a second."""
    c, os_ = cfg["cast"], platform.current()
    psp = open_psp(cfg, password)
    out, ended = os_.Input("PSPM PSP"), threading.Event()
    mapper = controls.Mapper(m or controls.load(cfg), out, c)
    pad = threading.Thread(target=_pad, args=(psp, c, mapper, lambda: stop() or ended.is_set(), pressed), daemon=True)
    pad.start()
    took = []

    def grab():
        t = time.monotonic()
        img = os_.capture(c)
        took.append(time.monotonic() - t)
        return img

    gap, t0 = 1 / c["max_fps"], time.monotonic()
    try:
        # several captures in flight: grim takes ~200 ms a frame, so one at a time caps at ~5 FPS
        with ThreadPoolExecutor(c["captures"]) as pool:
            todo = deque(pool.submit(grab) for _ in range(c["captures"]))
            while not stop():
                t = time.monotonic()
                img = todo.popleft().result()
                todo.append(pool.submit(grab))
                psp.write(c["endpoint"], frame(img, c["quality"]), c["timeout_ms"])
                if t - t0 >= 1:
                    fps(len(took), round(1000 * sum(took) / max(1, len(took))))
                    took.clear()
                    t0 = t
                time.sleep(max(0.0, gap - (time.monotonic() - t)))
            for f in todo:
                f.result()
    finally:
        ended.set()
        pad.join()
        out.close()
