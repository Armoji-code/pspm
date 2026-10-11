"""Config = packaged defaults.toml deep-merged with the user's config.toml."""
import tomllib
from importlib.resources import files
from pathlib import Path

from platformdirs import user_config_path

USER_FILE = user_config_path("pspm") / "config.toml"


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(user_file: Path = USER_FILE) -> dict:
    cfg = tomllib.loads(files("pspm").joinpath("defaults.toml").read_text())
    if user_file.exists():
        cfg = merge(cfg, tomllib.loads(user_file.read_text()))
    return cfg
