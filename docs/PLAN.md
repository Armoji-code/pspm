# PSPM plan

PSPM is a desktop app (Windows and Linux first) that opens when a PSP is plugged in over USB and manages everything on it.

## Architecture

```
src/pspm/
  defaults.toml   every value that may change: folder layout, features on/off, colors, timings
  config.py       defaults.toml + the user's config.toml, merged
  device.py       finds PSPs (any mounted drive with a PSP/ folder), no UI
  files.py        file operations, no UI
  <feature>.py    each feature's logic, no UI (tagger.py, saves.py, backup.py ...)
  features/       one UI page per feature, registered by id; outside packages can add more
  platform/       one module per OS: linux.py, windows.py (macos.py later)
  ui/             Qt shell, Nothing-style, dark or light from config
```

Rules that keep it changeable:
- **Logic never imports Qt.** A future phone app reuses `device.py`, `files.py` and every feature's logic and only replaces `ui/` and `features/`.
- **OS code only lives in `platform/`.** Adding macOS means one new module with the same functions plus one CI matrix entry.
- **Values live in config, not code.** PSP folders, enabled features, poll speed, theme colors, and (later) download URLs for outside tools.
- **Features are plugins.** A feature is `id`, `title`, `page(cfg)`. It is enabled by listing its id in `app.features`, and other packages can register one through the `pspm.features` entry point.
- **Outside tools are downloaded, not bundled**, where licences allow (ARK, pop-fe, RemoteJoyLite). Their URLs and versions go in config, so updating one doesn't need a code change.

PSP detection: psutil checks mounted drives every 2 seconds. This works on every OS and also catches card readers. USB IDs are not used because they differ by model and the PSP Go has no confirmed ID.

## Tooling

| Tool | Use |
|------|-----|
| Python 3.11+, PySide6 (Qt) | app and UI |
| pytest, ruff | tests and lint |
| Docker | `docker run` gives the same headless checks as CI on any machine |
| GitHub Actions | tests on Windows and Linux, then PyInstaller builds both apps as downloadable artifacts. Tag `v*` later turns these into releases |
| git | trunk-based: short branches, PR into `main`, CI must be green |

## Features and how each will work

| # | Feature | Approach | Risk |
|---|---------|----------|------|
| 1 | File manager | **Built (phase 1).** Folder per category from config: games, isos, plugins, themes, saves, music, photos, video. Add, list, delete (asks first). | low |
| 2 | Diagnostics | **Built (phase 3).** Card: free space, filesystem check (`chkdsk` / `fsck.vfat -n`), and a fake-capacity test written in Python (write seeded data, read back). Battery, firmware and model can't be read over USB, so we ship a tiny PSP homebrew app that writes them to `PSP/COMMON/pspm_info.txt` for PSPM to read next time it's plugged in. | medium, needs PSP SDK build |
| 3 | Theme maker | Phase A: install and preview PTF/CTF, auto-set the CXMB plugin needed for CTF. Phase B: ARK launcher themes (`THEME.ARK`, open format, Python tool exists). Phase C: PTF/CTF editor. No open-source PTF/CTF builder exists, so C means reverse-engineering the format. | high for C |
| 4 | MP3 tagger | **Built (phase 2).** Edit tags in a table, FIND TAGS fills them online, SAVE writes, SORT moves into album/artist/genre folders. AcoustID not added yet. mutagen for tags (writes ID3v2.3, which the PSP reads best), MusicBrainz + Cover Art Archive for names and covers (free, 1 request/s), optional AcoustID fingerprints for untagged files (free key). Covers resized to 500 px baseline JPEG. Sort by album, artist or genre into `MUSIC/`. | low |
| 5 | PS1 converter | Call pop-fe (Python, LGPL) or PSXPackager (MIT, .NET CLI) as a subprocess. We never ship BIOS or games. | low |
| 6 | Streaming | PSP to PC: RemoteJoyLite (Kethen fork, works with ARK, Windows native, Linux via Wine, needs a libusb driver). PC to PSP: no maintained tool exists (PSPDisp is dead), so this would be new work. While streaming, USB storage is off, so PSPM has to switch modes. | high |
| 7 | Cloud backup | **Built (phase 2).** Mirrors the chosen folders (SAVEDATA by default) to `PSPM/` on plug-in, uploading only files whose size or mtime changed; never deletes in the cloud. Providers subclass `backup.Provider` (`pspm.cloud` entry point). Each user registers their own app (README), since PSPM ships no keys. Google Drive (`drive.file` scope, sign in in the browser) and Dropbox (PKCE, no secret) behind one small provider interface. Backs up saves first, then anything picked. | medium |
| 8 | PPSSPP saves | **Built (phase 2).** **Change from the ask:** a symlink to the PSP breaks the moment it's unplugged (PPSSPP sees an empty folder and may write saves in the wrong place). Instead PSPM syncs `SAVEDATA` both ways each time the PSP is plugged in, keeps a snapshot before overwriting, and asks when both sides changed. Each save folder is compared by content hash against the last sync, so the side that changed wins; if both changed it asks, suggesting the newest. Deletes are never synced. Finds PPSSPP via per-OS paths in config (Windows Documents, Linux, Flatpak); portable installs pick the folder once. | medium |
| 9 | Auto softmod | Guided install that copies the CFW files to the stick. Hard blocks: firmware must be 6.60/6.61 (from the info app in #2 or user confirmation), battery at least 75% with charger, no Strider2 units, hashes checked after copy, explicit "I understand" before anything. Permanent cIPL is a separate, opt-in step with its own warning. Both ARK-5 (via FasterARK) and ARK-4 final (v4.20.69 r206) are offered; the user picks in the app, ARK-5 is the default. CFW zips downloaded from the official GitHub releases (GPLv3), never Sony files. | **brick risk** |

Research notes: ARK-4's final release was in May 2026 and the repo was archived in August 2026. Work moved to ARK-5, installed with FasterARK (github.com/PSP-Arkfive). This came from the research pass, and I could not open those repos from here to double check.

## Roadmap

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Core, config, PSP detection, open on plug-in (login watcher), file manager, Docker, CI for Windows + Linux | scaffolded, 5 tests pass, Linux app build checked |
| 2 | MP3 tagger, PPSSPP save sync, cloud backup | built |
| 3 | Diagnostics (+ PSP info app), PS1 converter, themes A + B | diagnostics built |
| 4 | Softmod with safety checks (ARK-5 via FasterARK and ARK-4 final, user picks in the app), PSP to PC streaming | |
| 5 | Theme editor (C), PC to PSP streaming, macOS, mobile shell | later |

Each phase is one PR per feature so CI checks each separately.
