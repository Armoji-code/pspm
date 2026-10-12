from pspm import device, gamelink


def test_links_follow_the_psp(tmp_path):
    cfg = {"saves": {"games_folder": "PSP_STICK"}}
    dev = device.Device(tmp_path / "stick", {"isos": "ISO", "games": "PSP/GAME", "music": "MUSIC"})
    (dev.path("isos") / "CAT_RPG").mkdir(parents=True)
    dev.path("games").mkdir(parents=True)
    folder = gamelink.link_dir(tmp_path / "ppsspp" / "PSP" / "SAVEDATA", cfg)
    assert folder == tmp_path / "ppsspp" / "PSP_STICK"

    (folder / "games").mkdir(parents=True)  # the user's own folder isn't replaced
    assert gamelink.link(dev, folder, ["isos", "games", "music"]) == ["isos"]  # music isn't on this stick
    assert (folder / "isos" / "CAT_RPG").is_dir()

    gamelink.unlink(folder)  # unplugged
    assert sorted(p.name for p in folder.iterdir()) == ["games"]
    assert (dev.path("isos") / "CAT_RPG").is_dir()  # the PSP's folder is untouched
