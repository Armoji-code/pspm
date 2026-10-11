"""OS-specific bits live here, one module per OS. Add macos.py etc. with the same functions."""
import importlib
import sys

_NAMES = {"win32": "windows", "linux": "linux"}


def name() -> str:
    """OS key used for per-OS config values and module names."""
    n = _NAMES.get(sys.platform)
    if n is None:
        raise NotImplementedError(f"PSPM has no platform layer for {sys.platform} yet")
    return n


def current():
    return importlib.import_module(f"{__name__}.{name()}")


def launch_command(*args: str) -> list[str]:
    """Command that starts PSPM, frozen (PyInstaller) or from source."""
    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "pspm"]
    return base + list(args)
