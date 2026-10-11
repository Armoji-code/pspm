"""Feature registry. A feature is any object with:

    id: str               key used in config `app.features`
    title: str            sidebar label
    page(cfg) -> QWidget  the widget must have set_device(Device | None)

Built-ins are listed below; other packages add features via the
"pspm.features" entry point without touching this repo.
"""
from importlib import import_module
from importlib.metadata import entry_points

BUILTIN = {
    "files": "pspm.features.files:FEATURE",
    "tagger": "pspm.features.tagger:FEATURE",
    "saves": "pspm.features.saves:FEATURE",
    "backup": "pspm.features.backup:FEATURE",
    "diag": "pspm.features.diag:FEATURE",
    "ps1": "pspm.features.ps1:FEATURE",
    "stream": "pspm.features.stream:FEATURE",
    "softmod": "pspm.features.softmod:FEATURE",
    "themes": "pspm.features.themes:FEATURE",
}


def _load(ref: str):
    mod, _, attr = ref.partition(":")
    return getattr(import_module(mod), attr)


def enabled(cfg: dict) -> list:
    found = {ep.name: ep.value for ep in entry_points(group="pspm.features")}
    found.update(BUILTIN)
    return [_load(found[fid]) for fid in cfg["app"]["features"] if fid in found]
