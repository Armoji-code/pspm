"""Cloud backup of PSP folders (Google Drive, Dropbox, or any registered provider). No UI.

The chosen stick folders are mirrored to <remote_folder>/ in the cloud with the stick's layout
(e.g. PSPM/PSP/SAVEDATA/ULUS10041DATA00/DATA.BIN). Only files whose size or mtime changed since
the last backup are uploaded. Nothing is deleted in the cloud; overwritten files stay in the
provider's own version history. Sign-in is OAuth in the browser with PKCE; the refresh token is
kept in the OS keyring, never in config.
"""
import base64
import hashlib
import http.server
import json
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from importlib.metadata import entry_points
from pathlib import Path
from typing import ClassVar

import keyring
import keyring.errors
from platformdirs import user_data_path

from .saves import TMP

STATE = user_data_path("pspm") / "backup.json"
KEYRING = "pspm"


def _http(url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data, headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body else {}


def _form(url, fields):
    return _http(url, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})


class Provider:
    """A cloud. Subclass, then register under the "pspm.cloud" entry point to add one.

    `pcfg` is its config table [backup.<id>]: client_id, (client_secret), auth_url, token_url, ...
    """

    title = ""
    auth_extra: ClassVar[dict] = {}  # extra authorize-URL params

    def __init__(self, pcfg: dict, refresh_token: str):
        self.cfg = pcfg
        tok = _form(pcfg["token_url"], {"grant_type": "refresh_token", "refresh_token": refresh_token, **_client(pcfg)})
        self.auth = {"Authorization": f"Bearer {tok['access_token']}"}

    def put(self, path: str, f, size: int) -> None:
        """Upload file object `f` to `path` (slash separated), replacing what's there."""
        raise NotImplementedError


class Dropbox(Provider):
    title = "DROPBOX"
    auth_extra: ClassVar[dict] = {"token_access_type": "offline"}

    def _up(self, endpoint, arg, data):
        h = {**self.auth, "Content-Type": "application/octet-stream", "Dropbox-API-Arg": json.dumps(arg)}
        return _http(f"{self.cfg['content_url']}/files/{endpoint}", data, h)

    def put(self, path, f, size):
        commit = {"path": f"/{path}", "mode": "overwrite", "mute": True}
        chunk = self.cfg["chunk_mb"] << 20
        if size <= chunk:
            return self._up("upload", commit, f.read())
        # Dropbox caps one request at 150 MB, so big files (ISOs) go up in a session.
        sid = self._up("upload_session/start", {}, f.read(chunk))["session_id"]
        off = chunk
        while size - off > chunk:
            self._up("upload_session/append_v2", {"cursor": {"session_id": sid, "offset": off}}, f.read(chunk))
            off += chunk
        self._up("upload_session/finish", {"cursor": {"session_id": sid, "offset": off}, "commit": commit}, f.read())


class GoogleDrive(Provider):
    title = "GOOGLE DRIVE"
    # drive.file: PSPM only sees files it created, not the rest of the user's Drive.
    auth_extra: ClassVar[dict] = {"scope": "https://www.googleapis.com/auth/drive.file", "access_type": "offline", "prompt": "consent"}
    FOLDER = "application/vnd.google-apps.folder"

    def __init__(self, pcfg, refresh_token):
        super().__init__(pcfg, refresh_token)
        self.dirs = {}

    def _find(self, name, parent, folder=False):
        # Looked up every time rather than cached across runs, so files moved or deleted in Drive just get re-created.
        name = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name = '{name}' and '{parent}' in parents and trashed = false"
        if folder:
            q += f" and mimeType = '{self.FOLDER}'"
        url = f"{self.cfg['api_url']}/files?" + urllib.parse.urlencode({"q": q, "fields": "files(id)"})
        found = _http(url, headers=self.auth)["files"]
        return found[0]["id"] if found else None

    def _folder(self, parts):
        pid = "root"
        for i, name in enumerate(parts):
            key = "/".join(parts[: i + 1])
            if key not in self.dirs:
                meta = json.dumps({"name": name, "mimeType": self.FOLDER, "parents": [pid]}).encode()
                self.dirs[key] = self._find(name, pid, True) or _http(
                    f"{self.cfg['api_url']}/files?fields=id", meta, {**self.auth, "Content-Type": "application/json"}
                )["id"]
            pid = self.dirs[key]
        return pid

    def put(self, path, f, size):
        *parts, name = path.split("/")
        parent = self._folder(parts)
        fid = self._find(name, parent)
        meta = {} if fid else {"name": name, "parents": [parent]}
        url = f"{self.cfg['upload_url']}/files" + (f"/{fid}" if fid else "") + "?uploadType=resumable"
        h = {**self.auth, "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(size)}
        req = urllib.request.Request(url, json.dumps(meta).encode(), h, method="PATCH" if fid else "POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            session = r.headers["Location"]
        _http(session, f, {"Content-Length": str(size)}, "PUT", timeout=600)


BUILTIN = {"gdrive": GoogleDrive, "dropbox": Dropbox}


def providers() -> dict:
    found = {ep.name: ep.load() for ep in entry_points(group="pspm.cloud")}
    return {**found, **BUILTIN}


def _client(pcfg):
    return {"client_id": pcfg["client_id"], **({"client_secret": pcfg["client_secret"]} if pcfg.get("client_secret") else {})}


def login(cls, pcfg: dict, cfg: dict) -> str:
    """Sign in in the browser (OAuth code flow + PKCE on a loopback port). Returns the refresh token."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state") == [state]:  # ignore anything that isn't our redirect
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"PSPM: done, you can close this tab.")

        def log_message(self, *_):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", cfg["redirect_port"]), Handler)
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    params = {
        "client_id": pcfg["client_id"], "redirect_uri": redirect, "response_type": "code", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", **cls.auth_extra,
    }
    with srv:
        srv.timeout = 1
        webbrowser.open(pcfg["auth_url"] + "?" + urllib.parse.urlencode(params))
        end = time.monotonic() + cfg["login_timeout_seconds"]
        while not got and time.monotonic() < end:
            srv.handle_request()
    if "code" not in got:
        raise OSError(f"sign-in failed: {got.get('error', 'timed out')}")
    tok = _form(pcfg["token_url"], {
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect,
        "code_verifier": verifier, **_client(pcfg),
    })
    return tok["refresh_token"]


def token(pid: str) -> str | None:
    try:
        return keyring.get_password(KEYRING, pid)
    except keyring.errors.KeyringError:  # no keyring on this system: treated as signed out; signing in shows the error
        return None


def set_token(pid: str, value: str | None) -> None:
    if value:
        keyring.set_password(KEYRING, pid, value)
    elif token(pid):
        keyring.delete_password(KEYRING, pid)


def load_state(path: Path = STATE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


def scan(root: Path, folders: list[Path]) -> dict[str, Path]:
    """Path relative to the stick root (posix) -> file, for every file under `folders`."""
    return {
        p.relative_to(root).as_posix(): p
        for d in folders if d.is_dir() for p in d.rglob("*")
        if p.is_file() and TMP not in p.as_posix()  # skip half-copied saves from a running sync
    }


def _sig(p: Path) -> list:
    # shortcut: size + whole-second mtime, not a hash, so big folders over USB stay fast; a DST shift re-uploads once.
    s = p.stat()
    return [s.st_size, int(s.st_mtime)]


def pending(files: dict[str, Path], done: dict) -> list[str]:
    return [rel for rel, p in sorted(files.items()) if done.get(rel) != _sig(p)]


def run(provider: Provider, files: dict[str, Path], done: dict, remote: str, save=lambda: None) -> int:
    """Upload every new or changed file; `done` (rel -> signature) is updated and saved after each one,
    so an unplug mid-backup keeps what already went up."""
    todo = pending(files, done)
    for rel in todo:
        p = files[rel]
        sig = _sig(p)
        with p.open("rb") as f:
            provider.put(f"{remote}/{rel}", f, sig[0])
        done[rel] = sig
        save()
    return len(todo)
