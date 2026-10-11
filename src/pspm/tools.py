"""Outside tools: downloaded on first use from a fixed URL and checked against a pinned sha256. No UI."""
import hashlib
import io
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from platformdirs import user_data_path

ROOT = user_data_path("pspm") / "tools"


def fetch(url: str, sha256: str, home: Path, timeout: float) -> Path:
    """Unpack the archive at url into home, unless it's already there. Refuses a download that doesn't match."""
    if home.is_dir():
        return home
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise OSError(f"{url.rsplit('/', 1)[-1]} doesn't match its checksum; not using it")
    home.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=home.parent))
    if url.endswith(".zip"):
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(tmp, filter="data")
    tmp.rename(home)  # only a complete unpack ends up at `home`
    return home
