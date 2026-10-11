# PSPM

Manager and companion app for the Sony PSP. Plug in a PSP (USB mode) and PSPM opens and shows its memory stick.

Status: phase 1 (core, PSP detection, auto-open, file manager). See [docs/PLAN.md](docs/PLAN.md).

## Run

```sh
pip install -e ".[dev]"
pspm                  # open the app
pspm autostart on     # start the watcher at login; it opens PSPM when a PSP is plugged in
pspm autostart off
pspm config           # where your config.toml override lives
```

Fonts (optional, the UI falls back without them): install Space Grotesk, Space Mono and Doto from Google Fonts.

## Change things without touching code

Everything that varies lives in `src/pspm/defaults.toml`: PSP folder layout, which features show, poll interval, light/dark colors.
Override any key in your own `config.toml` (path from `pspm config`), for example:

```toml
[app]
theme = "light"

[device.folders]
emulators = "PSP/GAME/EMU"
```

## Layout

| Path | What |
|------|------|
| `src/pspm/config.py`, `defaults.toml` | settings, merged with the user's file |
| `src/pspm/device.py`, `files.py` | PSP detection and file ops, no UI (reusable by a future mobile app) |
| `src/pspm/platform/` | one module per OS (`linux.py`, `windows.py`); add `macos.py` with the same functions |
| `src/pspm/features/` | one module per feature; registered in `features/__init__.py` or via the `pspm.features` entry point |
| `src/pspm/ui/` | Qt desktop shell (Nothing-style) |

## Dev

```sh
pytest -q && ruff check .                  # locally
docker build -t pspm . && docker run --rm pspm   # same checks in a clean Linux box
```

CI (`.github/workflows/ci.yml`) tests on Windows and Linux, then builds a PyInstaller app for each.
