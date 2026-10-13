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
PRESETS = ["best_quality", "balanced", "best_fps"]  # picked in PSPM Screen's menu, sent in each pad packet
AUDIO = ["off", "on", "best"]  # the PSP's note button steps through these; [cast] audio_rates gives each a sample rate
APP = files("pspm").joinpath("psp_app/screen")  # prebuilt from psp/pspm_screen; rebuild when its C files change


def install_app(dev: Device, cfg: dict, cat: str = "") -> Path:
    dest = fm.app_dir(dev, cfg["cast"]["app"], cat)
    for name in ("EBOOT.PBP", "pspm_usb.prx"):
        (dest / name).write_bytes(APP.joinpath(name).read_bytes())
    return dest


def installed(dev: Device, cfg: dict) -> bool:
    found = fm.find_app(dev, cfg["cast"]["app"])
    return bool(found) and (found / "pspm_usb.prx").is_file()


def frame(img: Image.Image, quality: int, note: str = "") -> bytes:
    """The image fitted into 480x272 (black bars) as a baseline 4:2:0 JPEG, which the PSP decodes in hardware,
    after a one-packet header ("PSPMSCR2", size, a note the PSP prints over the picture) the PSP finds the next
    frame by. Padded to whole USB packets."""
    pic = ImageOps.pad(img.convert("RGB"), (W, H), Image.Resampling.BILINEAR)
    for q in (quality, 50, 25):  # a very busy screen at high quality can outgrow the PSP's buffer
        out = io.BytesIO()
        pic.save(out, "JPEG", quality=q, subsampling=2)
        data = out.getvalue()
        if len(data) <= MAX_JPEG:
            break
    head = b"PSPMSCR2" + struct.pack("<I", len(data)) + note.encode("ascii", "replace")[:47]
    return head.ljust(512, b"\0") + data.ljust(-(-len(data) // 512) * 512, b"\0")


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


def _pad(psp, c: dict, mapper: controls.Mapper, stop, pressed, live: dict) -> None:
    """Read the PSP's controls until stop(); every packet goes to the mapper and pressed(buttons, x, y), and
    live["preset"] follows the quality preset picked on the PSP."""
    import usb.core

    try:
        while not stop():
            try:
                packet = bytes(psp.read(c["pad_endpoint"], 512, 200))
                state = controls.parse(packet)
            except usb.core.USBTimeoutError:
                continue
            if state:
                if len(packet) > 14 and packet[14] < len(PRESETS):
                    live["preset"] = PRESETS[packet[14]]
                if len(packet) > 15 and packet[15] < len(AUDIO):
                    live["audio"] = AUDIO[packet[15]]
                mapper.update(*state)
                pressed(*state)
    except usb.core.USBError:
        pass  # cable out: the frame loop reports it
    finally:
        mapper.release()


def _sound(psp, c: dict, live: dict, lock: threading.Lock, stop) -> None:
    """Send what the PC is playing while the PSP asks for it ("PSPMAUD1", size, sample rate; then the PCM)."""
    import usb.core

    src, at, os_ = None, 0, platform.current()
    try:
        while not stop():
            rate = c["audio_rates"][AUDIO.index(live["audio"])]
            if rate != at:
                if src:
                    src.close()
                src, at = None, rate
                try:
                    src = os_.sound(c, rate) if rate else None
                    live.pop("audio_error", None)
                except (OSError, ImportError, RuntimeError) as e:  # no parec, no speakers...: shown in the status
                    live["audio_error"] = str(e) or type(e).__name__
            pcm = src.read(c["audio_block"]) if src else b""
            if not pcm:
                time.sleep(0.05)
                continue
            head = (b"PSPMAUD1" + struct.pack("<II", len(pcm), rate)).ljust(512, b"\0")
            with lock:
                psp.write(c["endpoint"], head + pcm.ljust(-(-len(pcm) // 512) * 512, b"\0"), c["timeout_ms"])
    except usb.core.USBError:
        return  # cable out: the frame loop reports it
    finally:
        if src:
            src.close()


def send(cfg: dict, stop, fps=lambda n, ms, preset: None, password: str | None = None, m: dict | None = None,
         pressed=lambda buttons, lx, ly: None, switched=lambda name: None) -> None:
    """Send the screen until stop() is true, and turn the PSP's controls into PC input with mapping m (see
    controls.load). fps(frames sent, average capture ms, "preset · audio state") once a second. The PSP picks the preset;
    [cast.presets.<name>] overrides quality, max_fps and captures. SELECT on the PSP switches the controls profile:
    switched(name) is called and the PSP shows the name for a moment."""
    c, os_ = cfg["cast"], platform.current()
    psp = open_psp(cfg, password)
    out, ended = os_.Input("PSPM PSP"), threading.Event()
    note = {"text": "", "until": 0.0}

    def switch(name):
        note.update(text=f"CONTROLS: {name}", until=time.monotonic() + 2)
        switched(name)

    mapper = controls.Mapper(m or controls.load(cfg), out, c, switch)
    live, lock, done = {"preset": "balanced", "audio": "off"}, threading.Lock(), lambda: stop() or ended.is_set()
    pad = threading.Thread(target=_pad, args=(psp, c, mapper, done, pressed, live), daemon=True)
    pad.start()
    sound = threading.Thread(target=_sound, args=(psp, c, live, lock, done), daemon=True)
    sound.start()
    took = []

    def grab():
        t = time.monotonic()
        img = os_.capture(c)
        took.append(time.monotonic() - t)
        return img

    t0 = time.monotonic()
    most = max(c["captures"], *(p.get("captures", 0) for p in c["presets"].values()))
    try:
        # several captures in flight: grim takes ~250 ms a frame, so one at a time caps at ~4 FPS
        with ThreadPoolExecutor(most) as pool:
            todo = deque([pool.submit(grab)])
            while not stop():
                t, p = time.monotonic(), c | c["presets"].get(live["preset"], {})
                while len(todo) < p["captures"]:
                    todo.append(pool.submit(grab))
                img = todo.popleft().result()
                data = frame(img, p["quality"], note["text"] if t < note["until"] else "")
                with lock:
                    psp.write(c["endpoint"], data, c["timeout_ms"])
                if t - t0 >= 1:
                    audio = f"audio error: {live['audio_error']}" if "audio_error" in live else f"audio {live['audio']}"
                    fps(len(took), round(1000 * sum(took) / max(1, len(took))), f"{live['preset']} · {audio}")
                    took.clear()
                    t0 = t
                time.sleep(max(0.0, 1 / p["max_fps"] - (time.monotonic() - t)))
            for f in todo:
                f.result()
    finally:
        ended.set()
        pad.join()
        sound.join()
        out.close()
