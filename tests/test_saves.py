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
    (tmp_path / "PSP" / "SAVEDATA").mkdir(parents=True)
    assert saves.ppsspp_dir(cfg, {}) == tmp_path / "PSP" / "SAVEDATA"
    assert saves.ppsspp_dir(cfg, {"ppsspp": "/picked"}) == saves.Path("/picked")
    assert saves.ppsspp_dir(cfg, {"ppsspp": str(tmp_path)}) == tmp_path / "PSP" / "SAVEDATA"  # PPSSPP's folder picked
    assert saves.ppsspp_dir(cfg, {"ppsspp": str(tmp_path / "PSP")}) == tmp_path / "PSP" / "SAVEDATA"


def sfo(**kv):
    """A minimal PARAM.SFO with string values."""
    import struct
    keys = b"".join(k.encode() + b"\0" for k in kv)
    vals = b"".join(v.encode().ljust(64, b"\0") for v in kv.values())
    head = struct.pack("<4sIIII", b"\0PSF", 0x101, 20 + 16 * len(kv), 20 + 16 * len(kv) + len(keys), len(kv))
    ko, ents = 0, b""
    for i, k in enumerate(kv):
        ents += struct.pack("<HHIII", ko, 0x0204, 64, 64, 64 * i)
        ko += len(k) + 1
    return head + ents + keys + vals


def test_plan_shows_descriptions_and_apply_skips_stale_items(tmp_path):
    psp, pc, bk, last = tmp_path / "psp", tmp_path / "pc", tmp_path / "bk", {}
    put(psp, "A", b"psp")
    (psp / "A" / "PARAM.SFO").write_bytes(sfo(SAVEDATA_TITLE="Tetris", SAVEDATA_DETAIL="COMPLETION :\n 1%"))
    [a] = saves.plan(psp, pc, last)
    assert (a.psp_desc, a.pc_desc) == ("Tetris · COMPLETION : 1%", "")
    put(psp, "A", b"psp-played-since")  # changed after the plan: copying it now would act on old information
    saves.apply([a], psp, pc, last, bk)
    assert a.action == saves.SKIP and not (pc / "A").exists() and "A" not in last


def test_hash_cache_skips_unchanged_saves(tmp_path, monkeypatch):
    psp, pc, bk, last, cache = tmp_path / "psp", tmp_path / "pc", tmp_path / "bk", {}, {}
    put(psp, "A", b"x")
    saves.apply(saves.plan(psp, pc, last, cache=cache), psp, pc, last, bk, cache=cache)
    assert set(cache) == {str(psp / "A"), str(pc / "A")}  # the copy is cached too
    read, real = [], saves.Path.read_bytes
    monkeypatch.setattr(saves.Path, "read_bytes", lambda p: read.append(p) or real(p))
    assert saves.plan(psp, pc, last, cache=cache)[0].action == saves.SAME and all(p.name == "PARAM.SFO" for p in read)  # no save data re-read
    put(psp, "A", b"played")
    assert saves.plan(psp, pc, last, cache=cache)[0].action == saves.TO_PC


def test_history_is_per_ppsspp_folder(tmp_path):
    st = {"last": {"A": "h"}}
    assert saves.history(st, tmp_path / "wrong") == {}  # no folder recorded yet: no history to trust
    st["last"]["A"] = "h"
    assert saves.history(st, tmp_path / "wrong") == {"A": "h"}
    assert saves.history(st, tmp_path / "PSP" / "SAVEDATA") == {}
