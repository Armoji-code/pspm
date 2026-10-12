import subprocess

import pytest

from pspm import config, deps, device, platform, stream


def _setup(tmp_path):
    cfg = config.load(tmp_path / "x")
    (tmp_path / "rjl" / "psp").mkdir(parents=True)
    (tmp_path / "rjl" / "psp" / "RemoteJoyLite.prx").write_bytes(b"prx")
    (tmp_path / "psp" / "SEPLUGINS").mkdir(parents=True)
    return cfg, device.Device(tmp_path / "psp", cfg["device"]["folders"]), tmp_path / "rjl"


def test_install_enables_plugin_once_and_remove_keeps_others(tmp_path):
    cfg, dev, home = _setup(tmp_path)
    ark = tmp_path / "psp" / "SEPLUGINS" / "PLUGINS.TXT"
    ark.write_text("vsh, ms0:/SEPLUGINS/cxmb.prx, on\nvsh, ms0:/seplugins/remotejoylite.prx, off\n")
    stream.install(dev, cfg, home)
    stream.install(dev, cfg, home)
    assert stream.installed(dev, cfg) and (tmp_path / "psp" / "SEPLUGINS" / "RemoteJoyLite.prx").read_bytes() == b"prx"
    lines = ark.read_bytes().decode().split("\r\n")
    assert lines == ["vsh, ms0:/SEPLUGINS/cxmb.prx, on", *cfg["stream"]["lists"]["SEPLUGINS/PLUGINS.TXT"], ""]
    assert (tmp_path / "psp" / "SEPLUGINS" / "game.txt").read_text() == "ms0:/SEPLUGINS/RemoteJoyLite.prx 1\n"
    stream.remove(dev, cfg)
    assert not stream.installed(dev, cfg) and ark.read_text() == "vsh, ms0:/SEPLUGINS/cxmb.prx, on\n"
    assert (tmp_path / "psp" / "SEPLUGINS" / "vsh.txt").read_text() == ""


def test_start_checks_pc_then_runs_viewer(tmp_path, monkeypatch):
    cfg, _, home = _setup(tmp_path)
    seen = {}
    monkeypatch.setattr(deps, "found", lambda check: False)
    with pytest.raises(OSError, match="SETUP"):
        stream.start(cfg, home)
    monkeypatch.setattr(deps, "found", lambda check: True)
    monkeypatch.setattr(platform.current(), "stream_ready", lambda rule, pw: seen.update(rule=rule))

    class Viewer:
        def __init__(self, cmd, **kw):
            seen.update(cmd=cmd, cwd=kw["cwd"])
            kw["stdout"].write(b"err:module:import_dll Library libusb0.dll not found\n")

        def wait(self, timeout):
            if seen.get("runs"):
                raise subprocess.TimeoutExpired("viewer", timeout)
            return 1

    monkeypatch.setattr(subprocess, "Popen", Viewer)
    with pytest.raises(OSError, match="libusb0.dll not found"):  # the viewer's own error, not a silent hang
        stream.start(cfg, home)
    seen["runs"] = True
    assert isinstance(stream.start(cfg, home), Viewer)
    assert "uinput" in seen["rule"].read_text() and seen["cwd"] == home
    assert seen["cmd"][-1].startswith(str(home)) and seen["cmd"][-1].endswith((".exe", ".sh"))


def test_usb_rule_never_waits_on_a_terminal(tmp_path, monkeypatch):
    from pspm.platform import linux

    monkeypatch.setattr(linux, "UDEV_RULE", tmp_path / "60-psp-b-usb.rules")
    monkeypatch.setattr(deps.shutil, "which", lambda m: m == "pkexec")
    ran = []

    def run(c, **kw):  # pkexec run from a terminal would wait there for the password; its own session can't
        ran.append((c[0], kw["start_new_session"]))
        return subprocess.CompletedProcess(c, 127 if c[0] == "pkexec" else 0, "", "No authentication agent found.")

    monkeypatch.setattr(subprocess, "run", run)
    (tmp_path / "r.rules").write_text("rule")
    with pytest.raises(deps.NeedPassword):
        linux.stream_ready(tmp_path / "r.rules")
    linux.stream_ready(tmp_path / "r.rules", "pw")
    assert ran == [("pkexec", True), ("sudo", True)]
