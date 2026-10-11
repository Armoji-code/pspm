import io
import json

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
