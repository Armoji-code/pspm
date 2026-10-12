import io
import json
from pathlib import Path

import pytest
from mutagen.id3 import ID3
from PIL import Image

from pspm import config, tagger

# Smallest valid MPEG-1 Layer III frame (silent), repeated, so mutagen sees a real MP3.
FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413


@pytest.fixture
def cfg(tmp_path):
    c = config.load(tmp_path / "none.toml")
    c["tagger"]["request_gap_seconds"] = 0
    return c


@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / "in" / "Queen - Bohemian Rhapsody.mp3"
    p.parent.mkdir()
    p.write_bytes(FRAME * 10)
    return p


def jpeg(progressive):
    out = io.BytesIO()
    Image.new("RGB", (1200, 800), "red").save(out, "JPEG", progressive=progressive)
    return out.getvalue()


def test_untagged_name_from_filename(mp3):
    t = tagger.read(mp3)
    assert (t.artist, t.title, t.has_cover) == ("Queen", "Bohemian Rhapsody", False)


def test_write_roundtrip_v23_with_cover(mp3):
    cover = tagger.psp_jpeg(jpeg(progressive=True), 500)
    img = Image.open(io.BytesIO(cover))
    assert max(img.size) == 500 and not img.info.get("progressive")
    tagger.write(tagger.Track(mp3, "Queen", "Bohemian Rhapsody", "A Night at the Opera", "Rock"), cover)
    assert ID3(mp3).version == (2, 3, 0)
    t = tagger.read(mp3)
    assert (t.album, t.genre, t.has_cover) == ("A Night at the Opera", "Rock", True)


def test_lookup_picks_official_album(cfg, mp3, monkeypatch):
    rec = {
        "score": 100,
        "title": "Bohemian Rhapsody",
        "artist-credit": [{"name": "Queen", "joinphrase": ""}],
        "tags": [{"name": "pop", "count": 1}, {"name": "rock", "count": 9}],
        "releases": [
            {"id": "s", "title": "Single", "status": "Official", "release-group": {"primary-type": "Single"}},
            {"id": "a", "title": "A Night at the Opera", "status": "Official", "release-group": {"primary-type": "Album"}},
        ],
    }
    urls = []

    def fake(url):
        urls.append(url)
        return json.dumps({"recordings": [rec]}).encode()

    look = tagger.Lookup(cfg)
    monkeypatch.setattr(look, "_get", fake)
    m = look.find(tagger.read(mp3))
    assert (m.album, m.genre, m.release_id, m.path) == ("A Night at the Opera", "Rock", "a", mp3)
    assert "Queen" in urls[0]

    rec["score"] = 50
    assert look.find(tagger.read(mp3)) is None


def test_sort_into_album_folders(tmp_path, mp3):
    tagger.write(tagger.Track(mp3, "AC/DC", "x", "Back: In Black"))
    moved = tagger.sort_into(tagger.scan(mp3.parent), tmp_path / "out", "album")
    assert moved == [tmp_path / "out" / "Back_ In Black" / mp3.name]


def _http(code, retry_after=None):
    import urllib.error
    from email.message import Message

    h = Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("u", code, "x", h, None)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_get_retries_busy_then_succeeds(cfg, monkeypatch):
    calls, sleeps = [], []

    def urlopen(req, timeout):
        calls.append(req.get_header("User-agent"))
        if len(calls) < 3:
            raise _http(503, "2" if len(calls) == 1 else None)
        return _Resp(b"ok")

    monkeypatch.setattr(tagger.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tagger.time, "sleep", sleeps.append)
    assert tagger.Lookup(cfg)._get("http://x") == b"ok"
    assert calls == [cfg["tagger"]["user_agent"]] * 3
    assert [s for s in sleeps if s] == [2.0, 2.0]  # Retry-After, then backoff 2**1


def test_get_gives_up_and_cover_is_optional(cfg, monkeypatch):
    def urlopen(req, timeout):
        raise _http(503)

    monkeypatch.setattr(tagger.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tagger.time, "sleep", lambda s: None)
    look = tagger.Lookup(cfg)
    with pytest.raises(tagger.urllib.error.HTTPError) as e:
        look._get("http://x")
    assert "busy" in tagger.friendly(e.value)
    assert look.cover(tagger.Track(Path("x.mp3"), "Queen", "Song", release_id="r")) is None  # never stops the lookup
    assert "connection" in tagger.friendly(tagger.urllib.error.URLError("dns"))


def test_cover_falls_back_to_itunes(cfg, mp3, monkeypatch):
    urls = []

    def get(self, url):
        urls.append(url)
        if "coverartarchive" in url:
            raise _http(404)
        if "itunes" in url:
            return json.dumps({"results": [{"artworkUrl100": "http://art/100x100bb.jpg"}]}).encode()
        return jpeg(progressive=False)

    monkeypatch.setattr(tagger.Lookup, "_get", get)
    cover = tagger.Lookup(cfg).cover(tagger.Track(mp3, "Queen", "Bohemian Rhapsody", release_id="r"))
    assert urls[-1] == "http://art/600x600bb.jpg" and max(Image.open(io.BytesIO(cover)).size) == 500
    tagger.write(tagger.read(mp3), cover)
    assert tagger.cover_of(mp3) == cover
