import io
import json
import os
import threading
import urllib.parse
import urllib.request

from pspm import backup, config


class Fake(backup.Provider):
    def __init__(self, pcfg=None, token=None):
        self.got = {}

    def put(self, path, f, size):
        self.got[path] = f.read()
        assert len(self.got[path]) == size


def test_run_uploads_only_new_or_changed(tmp_path):
    save = tmp_path / "PSP" / "SAVEDATA"
    (save / "ULUS1").mkdir(parents=True)
    (save / "ULUS1" / "DATA.BIN").write_bytes(b"a")
    (save / "ULUS2.pspm-tmp").mkdir()  # a save sync mid-copy
    (save / "ULUS2.pspm-tmp" / "DATA.BIN").write_bytes(b"x")
    done, p, said = {}, Fake(), []
    assert backup.run(p, backup.scan(tmp_path, [save, tmp_path / "MUSIC"]), done, "PSPM", progress=said.append) == 1
    assert said == ["1/1 PSP/SAVEDATA/ULUS1/DATA.BIN"]
    assert p.got == {"PSPM/PSP/SAVEDATA/ULUS1/DATA.BIN": b"a"}
    assert backup.run(p, backup.scan(tmp_path, [save]), done, "PSPM") == 0
    (save / "ULUS1" / "DATA.BIN").write_bytes(b"bb")
    p.got.clear()
    assert backup.run(p, backup.scan(tmp_path, [save]), done, "PSPM") == 1 and p.got[next(iter(p.got))] == b"bb"


def test_dropbox_splits_big_files(monkeypatch):
    calls = []
    monkeypatch.setattr(backup, "_http", lambda url, data=None, headers=None, *a, **k: calls.append(
        (url.rsplit("/files/", 1)[1], json.loads(headers["Dropbox-API-Arg"]), data)) or {"session_id": "S"})
    d = backup.Dropbox.__new__(backup.Dropbox)
    d.cfg, d.auth = {"content_url": "C", "chunk_mb": 1}, {}
    data = os.urandom(5 << 19)  # 2.5 MB with 1 MB chunks
    d.put("PSPM/x.iso", io.BytesIO(data), len(data))
    assert [c[0] for c in calls] == ["upload_session/start", "upload_session/append_v2", "upload_session/finish"]
    assert b"".join(c[2] for c in calls) == data
    assert calls[-1][1] == {"cursor": {"session_id": "S", "offset": 2 << 20},
                            "commit": {"path": "/PSPM/x.iso", "mode": "overwrite", "mute": True}}


def test_login_checks_state_and_uses_pkce(tmp_path, monkeypatch):
    cfg = config.load(tmp_path / "x")["backup"] | {"redirect_port": 0}  # 0 = any free port
    sent = {}

    def browser(url):  # play the browser in a thread, as the real one is another process
        threading.Thread(target=visit, args=(url,)).start()

    def visit(url):  # a stray request first, then the real redirect
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        sent.update(q)
        base = q["redirect_uri"]
        urllib.request.urlopen(base + "?code=evil&state=wrong").read()
        urllib.request.urlopen(base + "?" + urllib.parse.urlencode({"code": "C", "state": q["state"]})).read()

    monkeypatch.setattr(backup.webbrowser, "open", browser)
    monkeypatch.setattr(backup, "_form", lambda url, f: sent.update(token=f) or {"refresh_token": "R"})
    pcfg = {"client_id": "ID", "auth_url": "https://auth", "token_url": "https://tok"}
    assert backup.login(backup.Dropbox, pcfg, cfg) == "R"
    assert sent["token_access_type"] == "offline" and sent["code_challenge_method"] == "S256"
    assert sent["token"]["code"] == "C" and len(sent["token"]["code_verifier"]) > 43


def test_drive_creates_folders_then_uploads(monkeypatch):
    calls = []

    def http(url, data=None, headers=None, method=None, timeout=0):
        calls.append((method or ("POST" if data else "GET"), url.split("?")[0]))
        return {"files": []} if "q=" in url else {"id": f"id{len(calls)}"}

    resp = io.BytesIO()
    resp.headers = {"Location": "https://upload/session"}

    monkeypatch.setattr(backup, "_http", http)
    monkeypatch.setattr(backup.urllib.request, "urlopen", lambda req, timeout: calls.append(
        (req.get_method(), req.full_url.split("?")[0], json.loads(req.data))) or resp)
    g = backup.GoogleDrive.__new__(backup.GoogleDrive)
    g.cfg, g.auth, g.dirs = {"api_url": "https://A", "upload_url": "https://U"}, {}, {}
    g.put("PSPM/SAVE/DATA.BIN", io.BytesIO(b"x"), 1)
    assert calls == [
        ("GET", "https://A/files"), ("POST", "https://A/files"),  # PSPM folder: not found, created
        ("GET", "https://A/files"), ("POST", "https://A/files"),  # SAVE folder
        ("GET", "https://A/files"),  # the file itself: not there yet
        ("POST", "https://U/files"),  # small: name and content in one request
    ]
    calls.clear()
    g.SMALL = 0  # big files go up resumable
    g.put("PSPM/SAVE/BIG.BIN", io.BytesIO(b"x"), 1)
    assert calls[1:] == [("POST", "https://U/files", {"name": "BIG.BIN", "parents": ["id4"]}), ("PUT", "https://upload/session")]


class Cloud(Fake):
    def files(self, folder):
        return {rel[len(folder) + 1:]: (len(data), rel) for rel, data in self.got.items() if rel.startswith(folder + "/")}

    def get(self, ref, f):
        f.write(self.got[ref])


def test_restore_writes_changed_files_and_keeps_the_old_ones(tmp_path):
    psp, old = tmp_path / "psp", tmp_path / "old"
    (psp / "PSP" / "SAVEDATA" / "A").mkdir(parents=True)
    (psp / "PSP" / "SAVEDATA" / "A" / "DATA.BIN").write_bytes(b"new")
    (psp / "PSP" / "SAVEDATA" / "A" / "SAME.BIN").write_bytes(b"same")
    c = Cloud()
    c.got = {"PSPM/PSP/SAVEDATA/A/DATA.BIN": b"cloud", "PSPM/PSP/SAVEDATA/A/SAME.BIN": b"same",
             "PSPM/PSP/SAVEDATA/B/DATA.BIN": b"b", "Other/x": b"no"}
    done = {}
    assert backup.restore(c, psp, "PSPM", done, old) == 2
    assert (psp / "PSP/SAVEDATA/A/DATA.BIN").read_bytes() == b"cloud" and (psp / "PSP/SAVEDATA/B/DATA.BIN").exists()
    assert [p.read_bytes() for p in old.rglob("*.BIN")] == [b"new"] and not list(psp.rglob("*.pspm-tmp"))
    assert backup.pending(backup.scan(psp, [psp / "PSP"]), done) == []  # restored files don't upload again
    c.got = {"PSPM/../evil": b"x"}
    try:
        backup.restore(c, psp, "PSPM", done, old)
        raise AssertionError("restored outside the stick")
    except OSError:
        assert not (tmp_path / "evil").exists()


def test_drive_lists_a_folder_tree(monkeypatch):
    pages = {
        "F": [{"files": [{"id": "s", "name": "SAVE", "mimeType": backup.GoogleDrive.FOLDER}], "nextPageToken": "2"},
              {"files": [{"id": "r", "name": "README", "mimeType": "text/plain", "size": "3"}]}],
        "s": [{"files": [{"id": "d", "name": "DATA.BIN", "mimeType": "application/octet-stream", "size": "7"}]}],
    }
    monkeypatch.setattr(backup, "_http", lambda url, headers=None: pages[
        dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["q"].split("'")[1]].pop(0))
    g = backup.GoogleDrive.__new__(backup.GoogleDrive)
    g.cfg, g.auth, g.dirs = {"api_url": "https://A"}, {}, {"PSPM": "F"}
    assert g.files("PSPM") == {"README": (3, "r"), "SAVE/DATA.BIN": (7, "d")}
