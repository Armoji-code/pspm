"""PC screen to the PSP: PSPM's PSP app (psp/pspm) runs on the PSP and shows the frames this sends over the
USB cable or Wi-Fi. Like PSP to PC streaming, it works while the PSP is out of USB mode. No UI."""
import io
import select
import shutil
import socket
import struct
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from . import controls, platform, stream
from . import files as fm
from .device import Device

W, H = 480, 272
MAX_JPEG = 256 * 1024  # the PSP app's frame buffer
# picked in the PSP app's menu, sent in each pad packet; "controller": the PSP only sends its buttons (for PPSSPP,
# its memory stick shared over USB), so no frames go to it
PRESETS = ["best_quality", "balanced", "best_fps", "controller"]
GAMEPAD = "GAMING"  # the profile controller mode switches to, when there is one
AUDIO = ["off", "on", "best"]  # the PSP's note button steps through these; [cast] audio_rates gives each a sample rate
ZOOMS = [1, 2, 3]  # SELECT + START on the PSP steps through these, zoomed in where the mouse is
ARROW = [(0, 0), (0, 16), (4, 12), (7, 18), (9, 17), (6, 11), (11, 11)]  # the mouse pointer, 16 px tall
APP = files("pspm").joinpath("psp_app/pspm")  # prebuilt from psp/pspm; rebuild when its C files change


def install_app(dev: Device, cfg: dict, cat: str = "") -> Path:
    """PSPM's PSP app (screen, controller and info modes) into games/<cat>/; the older separate apps go."""
    for old in cfg["cast"]["old_apps"]:
        if found := fm.find_app(dev, old):
            shutil.rmtree(found)
    dest = fm.app_dir(dev, cfg["cast"]["app"], cat)
    for name in ("EBOOT.PBP", "pspm_usb.prx"):
        (dest / name).write_bytes(APP.joinpath(name).read_bytes())
    return dest


def installed(dev: Device, cfg: dict) -> bool:
    found = fm.find_app(dev, cfg["cast"]["app"])
    return bool(found) and (found / "pspm_usb.prx").is_file()


def view(img: Image.Image, pointer=None, zoom: int = 1) -> Image.Image:
    """The screen with the mouse pointer drawn at `pointer` (0-1 across it, or None), cropped to 1/zoom of its
    width around the pointer (or the middle)."""
    img = img.convert("RGB")
    w, h = img.size
    x, y = pointer if pointer else (0.5, 0.5)
    cw = w / zoom
    ch = min(h, cw * H / W)
    if pointer and 0 <= x <= 1 and 0 <= y <= 1:
        s, px, py = cw / W, x * w, y * h  # 16 px tall on the PSP whatever the zoom
        dots = [(px + ax * s, py + ay * s) for ax, ay in ARROW]
        ImageDraw.Draw(img).polygon(dots, fill="white", outline="black", width=max(1, round(s)))
    if zoom == 1:
        return img
    left = min(max(0, x * w - cw / 2), w - cw)
    top = min(max(0, y * h - ch / 2), h - ch)
    return img.crop((round(left), round(top), round(left + cw), round(top + ch)))


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


class Net:
    """The PSP app over Wi-Fi: the same stream as over USB, on one TCP connection, with pyusb's read/write and
    errors so the rest doesn't care which it is."""

    def __init__(self, host: str, c: dict):
        self.sock = socket.create_connection((host, c["wifi_port"]), c["timeout_ms"] / 1000)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.host, self.got = host, b""

    def write(self, _endpoint, data: bytes, _timeout=None) -> int:
        import usb.core

        try:
            self.sock.sendall(data)
        except OSError as e:
            raise usb.core.USBError(f"Wi-Fi: {e}") from None
        return len(data)

    def read(self, _endpoint, _size, timeout_ms: int) -> bytes:
        """One 16-byte pad packet."""
        import usb.core

        while len(self.got) < 16:
            if not select.select([self.sock], [], [], timeout_ms / 1000)[0]:
                raise usb.core.USBTimeoutError("timeout")
            try:
                more = self.sock.recv(4096)
            except OSError as e:
                raise usb.core.USBError(f"Wi-Fi: {e}") from None
            if not more:
                raise usb.core.USBError("Wi-Fi: the PSP closed the connection")
            self.got += more
        packet, self.got = self.got[:16], self.got[16:]
        return packet


def find_psp(c: dict) -> str | None:
    """The address of a PSP running PSPM's app on Wi-Fi: [cast] wifi_host, else whoever answers a broadcast."""
    if c.get("wifi_host"):
        return c["wifi_host"]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.5)
        end = time.monotonic() + c["find_seconds"]
        while time.monotonic() < end:
            try:
                s.sendto(b"PSPMFIND", ("255.255.255.255", c["find_port"]))
                while True:
                    data, (host, _port) = s.recvfrom(16)
                    if data == b"PSPMHERE":
                        return host
            except TimeoutError:
                continue
            except OSError:  # no network
                return None
    return None


def open_psp(cfg: dict, password: str | None = None):
    """The PSP running PSPM's app: over libusb when the cable is in, else over Wi-Fi. On Linux the first call
    allows the PSP's USB (one password)."""
    import usb.core  # pyusb; imported here so the rest of PSPM never needs libusb

    c = cfg["cast"]
    platform.current().stream_ready(stream.usb_rule(cfg), password)
    no_libusb = False
    try:
        psp = usb.core.find(idVendor=c["vendor"], idProduct=c["product"], bcdDevice=c["bcd_device"])
    except usb.core.NoBackendError:
        psp, no_libusb = None, True
    if psp is not None:
        psp.set_configuration()
        return psp
    host = find_psp(c)
    if host:
        try:
            return Net(host, c)
        except OSError as e:
            raise OSError(f"PSP found at {host} but could not connect: {e}") from None
    if no_libusb:
        raise OSError("libusb not found: install it from the SETUP page (Wi-Fi: no PSP answered either)")
    raise OSError("PSP not found: start PSPM on the PSP (cable in, or Wi-Fi picked in its menu). "
                  "If it shows a Wi-Fi address, a firewall may block the search: set [cast] wifi_host to it")


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
                    if live["preset"] == "controller" and not live.get("gamepad"):  # once: PPSSPP wants a gamepad
                        live["gamepad"] = True
                        names = [q["name"] for q in mapper.m["profiles"]]
                        if GAMEPAD in names and controls.current(mapper.m)["name"] != GAMEPAD:
                            mapper.m["active"] = names.index(GAMEPAD)
                            mapper.switched(GAMEPAD)
                if len(packet) > 15 and packet[15] < len(AUDIO):
                    live["audio"] = AUDIO[packet[15]]
                mapper.update(*state)
                pressed(*state)
    except usb.core.USBError:
        pass  # cable out: the frame loop reports it
    finally:
        mapper.release()


def _sound(psp, c: dict, live: dict, lock: threading.Lock, stop, say=lambda text: None) -> None:
    """Send what the PC is playing while the PSP asks for it ("PSPMAUD1", size, sample rate; then the PCM).
    A failure goes to live["audio_error"] and say() (shown on the PSP)."""
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
                    say(f"AUDIO ERROR: {live['audio_error']}")
            try:
                pcm = src.read(c["audio_block"]) if src else b""
            except (OSError, RuntimeError) as e:  # the recorder stopped: tried again when the PSP switches sound
                live["audio_error"] = str(e) or type(e).__name__
                say(f"AUDIO ERROR: {live['audio_error']}")
                src.close()
                src, pcm = None, b""
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
    zoom = {"at": 0}
    where = getattr(out, "pointer", lambda: None)  # an OS layer that can't tell draws no pointer

    def say(text, seconds=2):
        note.update(text=text, until=time.monotonic() + seconds)

    def switch(name):
        say(f"CONTROLS: {name}")
        switched(name)

    def step_zoom():
        zoom["at"] = (zoom["at"] + 1) % len(ZOOMS)
        say(f"ZOOM {ZOOMS[zoom['at']]}X")

    mapper = controls.Mapper(m or controls.load(cfg), out, c, switch, step_zoom)
    live, lock, done = {"preset": "balanced", "audio": "off"}, threading.Lock(), lambda: stop() or ended.is_set()
    pad = threading.Thread(target=_pad, args=(psp, c, mapper, done, pressed, live), daemon=True)
    pad.start()
    sound = threading.Thread(target=_sound, args=(psp, c, live, lock, done, lambda t: say(t, 5)), daemon=True)
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
                if live["preset"] == "controller":
                    if not pad.is_alive():
                        break  # the PSP went away
                    if not live.get("told"):
                        live["told"] = True
                        fps(0, 0, "controller")
                    time.sleep(0.1)
                    continue
                t, p = time.monotonic(), c | c["presets"].get(live["preset"], {})
                while len(todo) < p["captures"]:
                    todo.append(pool.submit(grab))
                img = todo.popleft().result()
                data = frame(view(img, where(), ZOOMS[zoom["at"]]), p["quality"], note["text"] if t < note["until"] else "")
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
