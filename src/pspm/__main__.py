"""pspm [gui|watch|autostart on|off|config]"""
import argparse
import subprocess
import time

from pspm import config, device, platform


def watch(cfg) -> None:
    """Open the app when a PSP appears. Runs at login via `pspm autostart on`."""
    seen, gui = set(), None
    while True:
        roots = {d.root for d in device.find(cfg)}
        if roots - seen and cfg["app"]["open_on_connect"] and (gui is None or gui.poll() is not None):
            gui = subprocess.Popen(platform.launch_command("gui"))
        seen = roots
        time.sleep(cfg["app"]["poll_seconds"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pspm")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("gui")
    sub.add_parser("watch")
    sub.add_parser("config")
    sub.add_parser("autostart").add_argument("state", choices=["on", "off"])
    a = p.parse_args(argv)
    cfg = config.load()

    if a.cmd == "watch":
        watch(cfg)
    elif a.cmd == "config":
        print(config.USER_FILE)
    elif a.cmd == "autostart":
        osl = platform.current()
        print(osl.autostart_install() if a.state == "on" else osl.autostart_remove() or "removed")
    else:
        from pspm.ui.app import run  # Qt only loads for the GUI
        return run(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
