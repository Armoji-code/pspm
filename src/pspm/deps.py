"""PC programs features need (Wine, drivers...), from [deps] in the config: find what's missing, install it. No UI."""
import ctypes.util
import glob
import os
import shlex
import shutil
import subprocess
import webbrowser

from . import platform


def found(check: str) -> bool:
    kind, _, what = check.partition(":")
    if kind == "cmd":
        return bool(shutil.which(what))
    if kind == "lib":
        return bool(ctypes.util.find_library(what))
    return bool(glob.glob(os.path.expandvars(what)))


def status(cfg: dict) -> list[tuple[dict, bool]]:
    """(tool, installed) for each tool this OS needs, among enabled features."""
    osn, on = platform.name(), set(cfg["app"]["features"])
    return [(t, found(t["check"][osn])) for t in cfg["deps"]["tools"].values()
            if osn in t["check"] and on & set(t["features"])]


def missing(cfg: dict, feature: str | None = None) -> list[dict]:
    return [t for t, ok in status(cfg) if not ok and (feature is None or feature in t["features"])]


def manager(cfg: dict) -> str | None:
    return next((m for m in cfg["deps"]["managers"] if shutil.which(m)), None)


def commands(cfg: dict, tools: list[dict]) -> tuple[list[list[str]], list[dict]]:
    """Install commands for tools, and the tools this PC's package manager has no package for."""
    m = manager(cfg)
    pkgs = [p for t in tools for p in t.get("packages", {}).get(m, [])]
    cmd = cfg["deps"]["managers"].get(m, [])
    cmds = ([[a.replace("{pkg}", p) for a in cmd] for p in pkgs] if "{pkg}" in cmd else [cmd + pkgs]) if pkgs else []
    return cmds, [t for t in tools if not t.get("packages", {}).get(m)]


NEED_PASSWORD = "no password dialog on this desktop"


class NeedPassword(OSError):
    """pkexec can't ask for the password: no polkit agent runs (bare Sway, i3...) or pkexec isn't installed.
    Call install again with the password; it then goes through sudo."""

    def __init__(self):
        super().__init__(NEED_PASSWORD)


def install(cfg: dict, tools: list[dict], password: str | None = None) -> list[dict]:
    """Run the install commands (pkexec or winget asks for the password or UAC). Opens the download page of the
    tools it can't install, and returns those."""
    cmds, manual = commands(cfg, tools)
    for c in cmds:
        if c[0] == "pkexec" and (password is not None or not shutil.which("pkexec")):
            if password is None:
                raise NeedPassword
            c = ["sudo", "-S", "-p", "", *c[1:]]
        try:
            r = subprocess.run(c, capture_output=True, text=True, errors="replace", check=False,
                               input="" if password is None else password + "\n")
        except OSError as e:
            raise OSError(f"couldn't run {c[0]}; install by hand: {shlex.join(c)}") from e
        if c[0] == "pkexec" and r.returncode == 127 and "agent" in r.stderr:
            raise NeedPassword
        if r.returncode:
            raise OSError(f"{shlex.join(c)} failed: {(r.stderr or r.stdout).strip()[-300:]}")
    for t in manual:
        if t.get("url"):
            webbrowser.open(t["url"])
    return manual
