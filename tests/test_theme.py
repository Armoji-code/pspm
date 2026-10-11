import hashlib
import struct

from PIL import Image, ImageChops

from pspm import config, device, theme


def test_gim_round_trip_keeps_every_pixel():
    im = Image.new("RGBA", (40, 20), (0, 0, 0, 0))  # not a multiple of the 16x8 tiles
    for x in range(40):
        im.putpixel((x, x % 20), (x * 6, 255 - x, 7, 128 + x))
    assert not ImageChops.difference(theme.gim_decode(theme.gim_encode(im)), im).getbbox()


def test_new_theme_builds_a_ptf_that_takes_apart_the_same(tmp_path):
    folder = theme.new(tmp_path / "t", "My theme")
    theme.replace(folder, "1-00", _photo(tmp_path))
    theme.update(folder, background_mode=7)
    [ptf] = theme.build(folder, tmp_path / "out", 0xDEAD0661)
    head, items = theme.read_ptf(ptf.read_bytes())
    assert head["name"] == "My theme" and ptf.name == "My_theme.ptf"
    assert set(items) == set(theme.SLOTS) | {theme.MODE} and items[theme.MODE] == (theme.GIM, b"\7\0\0\0")
    again = theme.unpack(ptf, tmp_path / "again")
    assert theme.meta(again)["items"] == theme.meta(folder)["items"]
    for key in ("1-00", "3-03"):
        a, b = (Image.open(f / f"{key}.png").convert("RGBA") for f in (folder, again))
        assert not ImageChops.difference(a, b).getbbox()


def test_ctf_keeps_flash0_files_and_signs_its_table(tmp_path):
    folder = theme.new(tmp_path / "t", "Dark")
    (folder / "flash0" / "vsh" / "resource").mkdir(parents=True)
    (folder / "flash0" / "vsh" / "resource" / "topmenu_plugin.rco").write_bytes(b"RCO" * 100)
    patch = struct.pack("<II", 0x10, 2) + b"ab" + struct.pack("<II", 0x40, 1) + b"c"
    (folder / "flash0" / "vsh" / "module").mkdir()
    (folder / "flash0" / "vsh" / "module" / "paf.prx.diff").write_bytes(patch)
    _, ctf = theme.build(folder, tmp_path / "out", 0xDEAD0661)
    d = ctf.read_bytes()
    _, items, files, magic = theme.read_ctf(d)
    assert magic == 0xDEAD0661 and {c for c, _ in items} == {0, 1}
    assert files == {"/vsh/module/paf.prx": patch, "/vsh/resource/topmenu_plugin.rco": b"RCO" * 100}
    table = d[-144:]
    assert d[0x14:0x18] == hashlib.sha1(table).digest()[:4] and struct.unpack_from("<I", table, 68)[0] == 2  # records
    theme.unpack(ctf, tmp_path / "back")
    assert (tmp_path / "back" / "flash0" / "vsh" / "module" / "paf.prx.diff").read_bytes() == patch
    assert theme.build(tmp_path / "back", tmp_path / "out2", 0)[0].suffix == ".ctf"  # preview only: no PTF


def test_install_copies_to_theme_folder(tmp_path):
    cfg = config.load(tmp_path / "x")
    (tmp_path / "psp" / "PSP").mkdir(parents=True)
    f = tmp_path / "a.ptf"
    f.write_bytes(b"x")
    dest = theme.install([f], device.Device(tmp_path / "psp", cfg["device"]["folders"]))
    assert (dest / "a.ptf").read_bytes() == b"x" and dest == tmp_path / "psp" / "PSP" / "THEME"


def _photo(tmp_path):
    p = tmp_path / "photo.jpg"
    Image.radial_gradient("L").convert("RGB").resize((800, 600)).save(p)
    return p
