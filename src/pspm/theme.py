"""PSP themes: take apart PTF and CTF files into editable PNGs, and build them again. No UI.

PTF is the PSP's own theme format (firmware 6.20 and later, no custom firmware needed). After a 256-byte header come
categories 0-7 (offsets at 0x100); each category and each item has a 32-byte header and items are zlib-compressed.
Category 0 is the preview (16x16 icon, 300x170 picture, background colour number), 1 the 480x272 wallpaper, 2-4 the
XMB icons as GIM images. Layout checked against PTFs from PTF Studio and github.com/Kpavel325-404/PSP-PTF-Themes.

CTF is the CXMB plugin's format (custom firmware): the PTF's categories 0-1, then flash0 files (RCO, ...) that CXMB
swaps in for the PSP's own, then a table naming them. Taken from CXMB's source (github.com/PSP-Archive/CXMB, ctf.c),
which builds the same file on the PSP.
"""
import hashlib
import io
import json
import re
import shutil
import struct
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from platformdirs import user_data_path

from .device import Device

WORK = user_data_path("pspm") / "themes"  # one folder per theme being edited

PTF_MAGIC = b"\0PTF\0\x01\0\0"
GIM = 5
FORMATS = {0: "PNG", 1: "JPEG", 2: "TIFF", 3: "GIF", 4: "BMP"}  # PTF item types Pillow reads and writes
MODE = (0, 2)  # 4-byte background colour number, not an image
CTF_DIFF = ("paf.prx", "common_gui.prx", "vshmain.prx")  # CXMB keeps these as patches against the PSP's own copy
CTF_MAX_FILE = 0x200000  # CXMB skips bigger files

# A blank theme's items: (category, item) -> (type, width, height). Same layout as the 6.20-schema PTFs above.
SLOTS = {(0, 0): (GIM, 16, 16), (0, 1): (4, 300, 170), (1, 0): (4, 480, 272),
         **{(2, i): (GIM, 64, 48) for i in range(9)},
         **{(3, i): (GIM, 48, 48) if i % 2 == 0 else (GIM, 64, 64) for i in range(2, 60)},  # odd: highlighted
         **{(4, i): (GIM, 32, 32) if i % 2 == 0 else (GIM, 48, 48) for i in range(4)}}


# --- GIM, Sony's texture format -------------------------------------------------------------------------------------

BITS = {0: 16, 1: 16, 2: 16, 3: 32, 4: 4, 5: 8}  # RGBA5650, 5551, 4444, 8888, 4-bit index, 8-bit index


def _swizzle(buf: bytes, pitch: int, rows: int, back: bool = False) -> bytes:
    """PSP 'faster' pixel order: 16-byte x 8-row tiles, stored one after another."""
    out, i = bytearray(len(buf)), 0
    for by in range(0, rows, 8):
        for bx in range(0, pitch, 16):
            for y in range(by, by + 8):
                o = y * pitch + bx
                if back:
                    out[o:o + 16] = buf[i:i + 16]
                else:
                    out[i:i + 16] = buf[o:o + 16]
                i += 16
    return bytes(out)


def _plane(d: bytes, at: int) -> tuple[int, int, int, bytes]:
    """Image or palette block data at `at` -> format, width, height, tightly packed rows."""
    h = struct.unpack_from("<12H", d, at)
    start = struct.unpack_from("<I", d, at + 28)[0]
    fmt, order, w, ht, align, hal = h[2], h[3], h[4], h[5], h[7] or 16, h[8] or 8
    row = -(-w * BITS[fmt] // 8)
    pitch = -(-row // align) * align
    rows = -(-ht // hal) * hal if order else ht
    buf = d[at + start:at + start + pitch * rows]
    if order:
        buf = _swizzle(buf, pitch, rows, back=True)
    return fmt, w, ht, b"".join(buf[y * pitch:y * pitch + row] for y in range(ht))


def _rgba(fmt: int, raw: bytes) -> bytes:
    if fmt == 3:
        return raw
    out = bytearray()
    for (v,) in struct.iter_unpack("<H", raw):
        if fmt == 0:
            px = (v & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v >> 11) * 255 // 31, 255
        elif fmt == 1:
            px = (v & 31) * 255 // 31, (v >> 5 & 31) * 255 // 31, (v >> 10 & 31) * 255 // 31, 255 * (v >> 15)
        else:
            px = tuple((v >> s & 15) * 17 for s in (0, 4, 8, 12))
        out += bytes(px)
    return bytes(out)


def gim_decode(d: bytes) -> Image.Image:
    if not d.startswith(b"MIG.00.1PSP"):
        raise ValueError("not a GIM image")
    blocks, p = {}, 16
    while p + 16 <= len(d):
        bid, _, size, child, off = struct.unpack_from("<HHIII", d, p)
        if bid in (2, 3):  # file and picture blocks just hold the others
            p += child
            continue
        blocks.setdefault(bid, p + off)
        if not size:
            break
        p += size
    fmt, w, h, raw = _plane(d, blocks[4])
    if fmt < 4:
        return Image.frombytes("RGBA", (w, h), _rgba(fmt, raw))
    if fmt == 4:
        row = -(-w // 2)
        raw = b"".join(bytes(n for b in raw[y * row:(y + 1) * row] for n in (b & 15, b >> 4))[:w] for y in range(h))
    pfmt, _, _, praw = _plane(d, blocks[5])
    im = Image.frombytes("P", (w, h), raw)
    im.putpalette(_rgba(pfmt, praw), "RGBA")
    return im.convert("RGBA")


def _block(bid: int, body: bytes, holds: bool = False) -> bytes:
    size = 16 + len(body)
    return struct.pack("<HHIII", bid, 0, size, 16 if holds else size, 16) + body


def _image(fmt: int, order: int, w: int, h: int, level: int, px: bytes) -> bytes:
    head = struct.pack("<12H4I4H", 0x30, 0, fmt, order, w, h, BITS[fmt], 16, 8, 2, 0, 0,
                       0x30, 0x40, 0x40 + len(px), 0, level, 1, 3, 1)
    return head + struct.pack("<I12x", 0x40) + px


def gim_encode(im: Image.Image) -> bytes:
    """8-bit indexed, swizzled, RGBA palette: what PSP themes use."""
    im = im.convert("RGBA")
    w, h = im.size
    colors = im.getcolors(256)
    if colors:  # already fits a palette: keep it exact
        lut = {bytes(c): i for i, (_, c) in enumerate(colors)}
        raw = im.tobytes()
        q = Image.frombytes("P", (w, h), bytes(lut[raw[i:i + 4]] for i in range(0, len(raw), 4)))
        pal = b"".join(lut)
    else:
        q = im.quantize(256, method=Image.Quantize.FASTOCTREE)
        pal = bytes(q.getpalette("RGBA"))
    pal = pal.ljust(1024, b"\0")
    pitch, rows = -(-w // 16) * 16, -(-h // 8) * 8
    canvas = Image.new("P", (pitch, rows))
    canvas.paste(q)
    picture = _block(5, _image(3, 0, 256, 1, 2, pal)) + _block(4, _image(5, 1, w, h, 1,
                                                                         _swizzle(canvas.tobytes(), pitch, rows)))
    info = _block(0xFF, b"\0\0\0PSPM\0\0\0")
    return b"MIG.00.1PSP\0" + bytes(4) + _block(2, _block(3, picture, holds=True) + info, holds=True)


# --- PTF and CTF ----------------------------------------------------------------------------------------------------

def _str(b: bytes) -> str:
    return b.split(b"\0")[0].decode("utf-8", "replace")


def _field(s: str, n: int) -> bytes:
    return s.encode()[:n - 1].ljust(n, b"\0")


def read_ptf(d: bytes) -> tuple[dict, dict]:
    """-> header, {(category, item): (type, data)}."""
    if not d.startswith(PTF_MAGIC):
        raise ValueError("not a PSP theme (PTF/CTF)")
    head = {"name": _str(d[8:136]), "file_name": _str(d[136:184]), "psp_version": _str(d[184:192]),
            "version": _str(d[192:200])}
    items = {}
    for at in struct.unpack_from("<8I", d, 0x100):
        if not at:
            continue
        cat, n, _, p = struct.unpack_from("<HHII", d, at)
        for _ in range(n):
            idx, _, typ, comp, size, full = struct.unpack_from("<HHHHII", d, p)
            data = d[p + 32:p + 32 + size]
            items[(cat, idx)] = (typ, zlib.decompress(data) if comp == 2 and size < full else data[:full])
            p += 32 + size
    return head, items


def write_ptf(head: dict, items: dict) -> bytes:
    out = bytearray(PTF_MAGIC + _field(head["name"], 128) + _field(head["file_name"], 48)
                    + _field(head.get("psp_version") or "6.20", 8) + _field(head.get("version") or "1.0", 8)
                    + struct.pack("<I", 8))
    out += bytes(0x120 - len(out))
    for cat in sorted({c for c, _ in items}):
        struct.pack_into("<I", out, 0x100 + 4 * cat, len(out))
        body = bytearray()
        keys = sorted(i for c, i in items if c == cat)
        for idx in keys:
            typ, data = items[(cat, idx)]
            z = zlib.compress(data, 9)
            stored = z if len(z) < len(data) else data
            stored += bytes(-len(stored) % 4)
            body += struct.pack("<HHHHII16x", idx, 0, typ, 2, len(stored), len(data)) + stored
        out += struct.pack("<HHII20x", cat, len(keys), len(body), len(out) + 32) + body
    return bytes(out)


def read_ctf(d: bytes) -> tuple[dict, dict, dict, int]:
    """-> header, preview items, {flash0 path: data}, firmware magic."""
    head, items = read_ptf(d)
    head["name"] = _str(d[8:16])  # CXMB writes over the rest of the name
    magic, _, count = struct.unpack_from("<3I", d, 0x10)
    table = len(d) - 72 * count
    entries = sorted((struct.unpack_from("<64sII", d, table + 72 * i) for i in range(count)), key=lambda e: e[1])
    ends = [e[1] for e in entries[1:]] + [table]  # a patch entry's size counts records, so use where the next starts
    return head, items, {_str(name): d[start:end] for (name, start, _), end in zip(entries, ends)}, magic


def _records(patch: bytes) -> int:
    n = p = 0
    while p < len(patch):
        p += 8 + struct.unpack_from("<I", patch, p + 4)[0]
        n += 1
    return n


def write_ctf(head: dict, items: dict, files: dict, magic: int) -> bytes:
    out = bytearray(write_ptf(head, {k: v for k, v in items.items() if k[0] < 2}))
    struct.pack_into("<I", out, 0x1C, len(out))
    table = b""
    for name, data in files.items():
        if len(name) > 63 or not 0 < len(data) < CTF_MAX_FILE:
            raise ValueError(f"{name}: CXMB takes files up to 2 MB with paths up to 63 characters")
        size = _records(data) if name.rsplit("/", 1)[-1].lower() in CTF_DIFF else len(data)
        table += struct.pack("<64sII", name.encode(), len(out), size)
        out += data
    struct.pack_into("<I4sI", out, 0x10, magic, hashlib.sha1(table).digest()[:4], len(files))
    return bytes(out + table)


# --- Theme folders: theme.json + one PNG per item + flash0/ ----------------------------------------------------------

def _key(cat: int, idx: int) -> str:
    return f"{cat}-{idx:02d}"


def unpack(src: Path, dest: Path) -> Path:
    """Take a PTF or CTF apart into dest, to edit and build again."""
    d = src.read_bytes()
    if src.suffix.lower() == ".ctf":
        head, items, files, magic = read_ctf(d)
    else:
        (head, items), files, magic = read_ptf(d), {}, None
    dest.mkdir(parents=True, exist_ok=True)
    meta = {**head, "background_mode": 0, "items": {}, "ctf_magic": magic}
    for (cat, idx), (typ, data) in sorted(items.items()):
        if (cat, idx) == MODE:
            meta["background_mode"] = struct.unpack("<I", data[:4])[0]
            continue
        key = _key(cat, idx)
        try:
            im = gim_decode(data) if typ == GIM else Image.open(io.BytesIO(data))
            im.save(dest / f"{key}.png")
            meta["items"][key] = [typ, *im.size]
        except Exception:  # noqa: BLE001 - an image this can't read is kept as is and built back unchanged
            (dest / f"{key}.bin").write_bytes(data)
            meta["items"][key] = [typ, 0, 0]
    for name, data in files.items():
        f = dest / "flash0" / (name.lstrip("/") + (".diff" if name.rsplit("/", 1)[-1].lower() in CTF_DIFF else ""))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    (dest / "theme.json").write_text(json.dumps(meta, indent=1), "utf-8")
    return dest


def new(dest: Path, name: str) -> Path:
    """A blank theme to start from: dark wallpaper, a white dot for each icon."""
    dest.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "file_name": name, "background_mode": 0, "items": {}, "ctf_magic": None}
    for (cat, idx), (typ, w, h) in SLOTS.items():
        im = Image.new("RGBA", (w, h), (0, 0, 0, 255 if typ != GIM else 0))
        if typ == GIM:
            r = min(w, h) // (3 if idx % 2 or cat == 2 else 4)
            ImageDraw.Draw(im).ellipse((w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r), fill="white")
        im.save(dest / f"{_key(cat, idx)}.png")
        meta["items"][_key(cat, idx)] = [typ, w, h]
    (dest / "theme.json").write_text(json.dumps(meta, indent=1), "utf-8")
    return dest


def meta(folder: Path) -> dict:
    return json.loads((folder / "theme.json").read_text("utf-8"))


def update(folder: Path, **changes) -> None:
    (folder / "theme.json").write_text(json.dumps({**meta(folder), **changes}, indent=1), "utf-8")


def replace(folder: Path, key: str, image: Path) -> None:
    """Use any picture for an item: cropped to fill the item's size."""
    _, w, h = meta(folder)["items"][key]
    with Image.open(image) as im:
        ImageOps.fit(im.convert("RGBA"), (w, h) if w else im.size).save(folder / f"{key}.png")


def _encode(png: Path, typ: int, size: tuple[int, int]) -> bytes:
    with Image.open(png) as im:
        im = ImageOps.fit(im.convert("RGBA"), size) if size[0] and im.size != size else im.convert("RGBA")
        if typ == GIM:
            return gim_encode(im)
        buf = io.BytesIO()
        (im.convert("RGB") if typ in (1, 4) else im).save(buf, FORMATS[typ])
        return buf.getvalue()


def flash0(folder: Path) -> dict:
    """flash0 files for a CTF, as CXMB names them."""
    root = folder / "flash0"
    files = sorted(f for f in root.rglob("*") if f.is_file()) if root.is_dir() else []
    return {"/" + f.relative_to(root).as_posix().removesuffix(".diff"): f.read_bytes() for f in files}


def build(folder: Path, out: Path, ctf_magic: int) -> list[Path]:
    """Theme folder -> out/<file_name>.ptf (when it has every category) and .ctf (when it has flash0 files)."""
    m = meta(folder)
    items = {MODE: (GIM, struct.pack("<I", int(m["background_mode"])))}
    for key, (typ, w, h) in m["items"].items():
        cat, idx = map(int, key.split("-"))
        png = folder / f"{key}.png"
        items[(cat, idx)] = (typ, _encode(png, typ, (w, h)) if png.exists() else (folder / f"{key}.bin").read_bytes())
    head = {k: m.get(k, "") for k in ("name", "file_name", "psp_version", "version")}
    head["file_name"] = re.sub(r"[^\w.-]", "_", head["file_name"] or folder.name)
    files, done = flash0(folder), []
    out.mkdir(parents=True, exist_ok=True)
    if {0, 1, 2, 3, 4} <= {c for c, _ in items}:
        done.append(out / f"{head['file_name']}.ptf")
        done[-1].write_bytes(write_ptf(head, items))
    if files:
        done.append(out / f"{head['file_name']}.ctf")
        done[-1].write_bytes(write_ctf(head, items, files, m.get("ctf_magic") or ctf_magic))
    if not done:
        raise ValueError("nothing to build: a PTF needs all icon sets, a CTF needs files in flash0/")
    return done


def install(files: list[Path], dev: Device) -> Path:
    """Copy built themes to the PSP's theme folder; pick them in Settings > Theme Settings."""
    dest = dev.path("themes")
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copyfile(f, dest / f.name)
    return dest
