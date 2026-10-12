import subprocess

import pytest

from pspm import config, deps


def test_missing_tools_install_with_the_pcs_package_manager(tmp_path, monkeypatch):
    cfg = config.load(tmp_path / "x")
    cfg["deps"]["tools"] = {
        "wine": {"name": "Wine", "features": ["stream"], "check": {"linux": "cmd:wine", "windows": "cmd:wine"},
                 "packages": {"pacman": ["wine"], "winget": ["Wine"]}},
        "drv": {"name": "Driver", "features": ["stream"], "check": {"linux": "cmd:x", "windows": "cmd:x"},
                "url": "https://example.org"},
        "off": {"name": "Off", "features": ["nope"], "check": {"linux": "cmd:y", "windows": "cmd:y"}},
    }
    monkeypatch.setattr(deps, "found", lambda check: False)
    assert [t["name"] for t in deps.missing(cfg)] == ["Wine", "Driver"]  # Off's feature isn't enabled
    assert deps.missing(cfg, "files") == []
    ran, opened = [], []
    monkeypatch.setattr(deps.shutil, "which", lambda m: m in ("pacman", "pkexec"))
    monkeypatch.setattr(subprocess, "run", lambda c, **kw: ran.append(c) or subprocess.CompletedProcess(c, 0))
    monkeypatch.setattr(deps.webbrowser, "open", opened.append)
    assert [t["name"] for t in deps.install(cfg, deps.missing(cfg))] == ["Driver"]
    assert ran == [cfg["deps"]["managers"]["pacman"] + ["wine"]] and opened == ["https://example.org"]
    monkeypatch.setattr(deps.shutil, "which", lambda m: m == "winget")
    cmds, _ = deps.commands(cfg, deps.missing(cfg))
    assert cmds == [[a.replace("{pkg}", "Wine") for a in cfg["deps"]["managers"]["winget"]]]


def test_checks(tmp_path):
    (tmp_path / "fsck.vfat").touch()
    assert deps.found(f"path:{tmp_path}/fsck.*") and not deps.found(f"path:{tmp_path}/nope")
    assert deps.found("cmd:python3") or deps.found("cmd:python")
    assert not deps.found("cmd:pspm-no-such-program") and not deps.found("lib:pspm-no-such-lib")


def test_every_tool_has_a_check_and_a_way_to_install(tmp_path):
    cfg = config.load(tmp_path / "x")
    for t in cfg["deps"]["tools"].values():
        assert t["check"] and set(t["features"]) <= set(cfg["app"]["features"])
        assert t.get("url") or set(t["packages"]) <= set(cfg["deps"]["managers"]), t["name"]


def test_without_a_polkit_agent_it_asks_for_the_password_and_uses_sudo(tmp_path, monkeypatch):
    cfg = config.load(tmp_path / "x")
    tools = [{"name": "Wine", "packages": {"pacman": ["wine"]}}]
    monkeypatch.setattr(deps.shutil, "which", lambda m: m in ("pacman", "pkexec"))
    ran = []

    def run(c, **kw):
        ran.append((c, kw["input"]))
        no_agent = c[0] == "pkexec"
        return subprocess.CompletedProcess(c, 127 if no_agent else 0, "", "No authentication agent found." * no_agent)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(deps.NeedPassword, match=deps.NEED_PASSWORD):
        deps.install(cfg, tools)
    assert deps.install(cfg, tools, "pw") == []
    assert ran[-1] == (["sudo", "-S", "-p", "", "pacman", "-S", "--needed", "--noconfirm", "wine"], "pw\n")
