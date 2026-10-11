"""MP3 tags, online lookup (MusicBrainz + Cover Art Archive) and sorting. No UI."""
import io
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TCON, TIT2, TPE1, ID3NoHeaderError
from PIL import Image

FIELDS = {"artist": TPE1, "title": TIT2, "album": TALB, "genre": TCON}


@dataclass(frozen=True)
class Track:
    path: Path
    artist: str = ""
    title: str = ""
    album: str = ""
    genre: str = ""
    has_cover: bool = False
    release_id: str = ""  # MusicBrainz release, set by lookup(), used to fetch the cover


def _id3(path: Path) -> ID3:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3()


def read(path: Path) -> Track:
    tags = _id3(path)
    vals = {k: str(tags[f.__name__].text[0]) if f.__name__ in tags else "" for k, f in FIELDS.items()}
    if not (vals["artist"] or vals["title"]):  # untagged: try "Artist - Title.mp3"
        a, sep, t = path.stem.partition(" - ")
        vals["artist"], vals["title"] = (a, t) if sep else ("", path.stem)
    return Track(path, **vals, has_cover=bool(tags.getall("APIC")))


def scan(folder: Path) -> list[Track]:
    return [read(p) for p in sorted(folder.rglob("*")) if p.suffix.lower() == ".mp3"]


class Lookup:
    """MusicBrainz client. Keeps to the API's 1 request/second rule across calls."""

    def __init__(self, cfg: dict):
        self.cfg = cfg["tagger"]
        self._last = 0.0

    def _get(self, url: str) -> bytes:
        time.sleep(max(0.0, self._last + self.cfg["request_gap_seconds"] - time.monotonic()))
        self._last = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": self.cfg["user_agent"]})
        with urllib.request.urlopen(req, timeout=self.cfg["timeout_seconds"]) as r:
            return r.read()

    def find(self, t: Track) -> Track | None:
        """Best match for t with corrected artist/title/album/genre, or None below min_score."""
        q = f'recording:"{_esc(t.title)}"' + (f' AND artist:"{_esc(t.artist)}"' if t.artist else "")
        url = f"{self.cfg['musicbrainz_url']}/recording?" + urllib.parse.urlencode({"query": q, "fmt": "json", "limit": 5})
        for rec in json.loads(self._get(url)).get("recordings", []):
            if rec.get("score", 0) < self.cfg["min_score"]:
                break
            rel = _pick_release(rec.get("releases", []))
            tags = sorted(rec.get("tags", []), key=lambda g: -g.get("count", 0))
            return replace(
                t,
                artist="".join(c["name"] + c.get("joinphrase", "") for c in rec["artist-credit"]),
                title=rec["title"],
                album=rel["title"] if rel else t.album,
                genre=tags[0]["name"].title() if tags else t.genre,
                release_id=rel["id"] if rel else "",
            )
        return None

    def cover(self, release_id: str) -> bytes | None:
        """Front cover as a PSP-safe JPEG (baseline, max cover_size px), or None if the release has none."""
        try:
            raw = self._get(f"{self.cfg['coverart_url']}/release/{release_id}/front-500")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        return psp_jpeg(raw, self.cfg["cover_size"])


def _esc(s: str) -> str:
    return re.sub(r'(["\\])', r"\\\1", s)  # inside a quoted Lucene phrase only " and \ are special


def _pick_release(releases: list) -> dict | None:
    # Prefer an official album over singles and compilations.
    def rank(r):
        rg = r.get("release-group", {})
        return (r.get("status") != "Official", rg.get("primary-type") != "Album", bool(rg.get("secondary-types")))

    return min(releases, key=rank) if releases else None


def psp_jpeg(data: bytes, size: int) -> bytes:
    # The PSP can't show progressive JPEGs, so always re-encode as baseline.
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((size, size))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, progressive=False)
    return out.getvalue()


def write(t: Track, cover: bytes | None = None) -> None:
    """Save t's fields (and cover) into its file as ID3v2.3, the version the PSP reads best."""
    tags = _id3(t.path)
    for k, frame in FIELDS.items():
        if getattr(t, k):
            tags.setall(frame.__name__, [frame(encoding=1, text=getattr(t, k))])
    if cover:
        tags.setall("APIC", [APIC(encoding=1, mime="image/jpeg", type=3, desc="Cover", data=cover)])
    tags.save(t.path, v2_version=3)


def _safe(name: str) -> str:
    # FAT32 (the memory stick) rejects these characters and trailing dots/spaces.
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ") or "Unknown"


def sort_into(tracks: list[Track], root: Path, by: str) -> list[Path]:
    """Move each track to root/<album|artist|genre>/<file>. Skips files whose target exists. Returns new paths."""
    moved = []
    for t in tracks:
        dest = root / _safe(getattr(t, by) or "Unknown") / t.path.name
        if dest == t.path or dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        moved.append(Path(shutil.move(t.path, dest)))  # move, not rename: may cross drives
    return moved
