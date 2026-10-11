import os

from pspm import saves


def put(root, name, data, mtime=None):
    p = root / name / "DATA.BIN"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))


def sync(psp, pc, last, bk, pick=None):
    items = saves.plan(psp, pc, last)
    for i in items:
        if i.conflict and pick:
            i.action = pick
    saves.apply(items, psp, pc, last, bk)
    return items


def test_two_way_sync_with_backup_and_conflict(tmp_path):
    psp, pc, bk, last = tmp_path / "psp", tmp_path / "pc", tmp_path / "bk", {}
    put(psp, "A", b"psp-a")
    put(pc, "B", b"pc-b")
    put(psp, "C", b"same")
    put(pc, "C", b"same")
    items = {i.name: i for i in sync(psp, pc, last, bk)}
    assert (items["A"].action, items["B"].action, items["C"].action) == (saves.TO_PC, saves.TO_PSP, saves.SAME)
    assert (pc / "A" / "DATA.BIN").read_bytes() == b"psp-a" and (psp / "B" / "DATA.BIN").read_bytes() == b"pc-b"

    # Played on the PSP only: PSP wins, old PC copy is backed up.
    put(psp, "A", b"psp-a2")
    [a] = [i for i in saves.plan(psp, pc, last) if i.action != saves.SAME]
    assert a.action == saves.TO_PC and not a.conflict
    saves.apply([a], psp, pc, last, bk)
    assert (pc / "A" / "DATA.BIN").read_bytes() == b"psp-a2"
    assert [p.read_bytes() for p in bk.rglob("DATA.BIN")] == [b"psp-a"]

    # Played on both: conflict, newest suggested, nothing changes until resolved.
    put(psp, "A", b"psp-a3", mtime=1000)
    put(pc, "A", b"pc-a3", mtime=2000)
    [a] = [i for i in saves.plan(psp, pc, last) if i.action != saves.SAME]
    assert a.conflict and a.action == saves.TO_PSP
    a.action = saves.SKIP
    saves.apply([a], psp, pc, last, bk)
    assert (psp / "A" / "DATA.BIN").read_bytes() == b"psp-a3"
    assert saves.plan(psp, pc, last)[0].conflict  # still unresolved


def test_deletes_are_not_synced(tmp_path):
    psp, pc, bk, last = tmp_path / "psp", tmp_path / "pc", tmp_path / "bk", {}
    put(psp, "A", b"x")
    sync(psp, pc, last, bk)
    (pc / "A" / "DATA.BIN").unlink()
    (pc / "A").rmdir()
    sync(psp, pc, last, bk)
    assert (psp / "A" / "DATA.BIN").exists() and (pc / "A" / "DATA.BIN").exists()


def test_ppsspp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(saves.platform, "name", lambda: "linux")
    cfg = {"saves": {"ppsspp_paths": {"linux": [str(tmp_path / "nope"), str(tmp_path / "PSP")]}}}
    assert saves.ppsspp_dir(cfg, {}) is None
    (tmp_path / "PSP").mkdir()
    assert saves.ppsspp_dir(cfg, {}) == tmp_path / "PSP" / "SAVEDATA"
    assert saves.ppsspp_dir(cfg, {"ppsspp": "/picked"}) == saves.Path("/picked")
