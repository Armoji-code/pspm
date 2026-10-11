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
| 5 | PS1 converter | **Built (phase 3).** PSXPackager (MIT, self-contained CLI for Linux, .NET 8 on Windows) run as a subprocess, downloaded on first use and sha256-checked. pop-fe was skipped: heavy install (OpenCV, Node, native builds) on Linux. bin/cue, img, iso, chd, archives; several discs become one multi-disc EBOOT via an .m3u. Title from PSXPackager's game DB, optional icon written into the PBP by PSPM. We never ship BIOS or games. | low |
| 6 | Streaming | **PSP to PC built (phase 4).** RemoteJoyLite (Kethen fork, release 2026-01-06), downloaded on first use and sha256-checked. INSTALL PLUGIN copies `RemoteJoyLite.prx` to `SEPLUGINS/` and turns it on in ARK's `PLUGINS.TXT` and PRO/ME's `vsh/game/pops.txt` (REMOVE takes it out again). The PSP leaves USB mode and restarts, then START VIEWER opens the viewer: native on Windows (needs the libusb-win32 driver via Zadig, PSPM checks for it), in Wine on Linux (PSPM checks for Wine and libusb 0.1 and installs the udev rule with one password prompt). PC to PSP: **not built.** No maintained tool exists (PSPDisp is dead and Windows-only), so it would be a new PSP app plus a PC capture side. | PSP to PC: medium. PC to PSP: high |
| 7 | Cloud backup | **Built (phase 2).** Mirrors the chosen folders (SAVEDATA by default) to `PSPM/` on plug-in, uploading only files whose size or mtime changed; never deletes in the cloud. Providers subclass `backup.Provider` (`pspm.cloud` entry point). Each user registers their own app (README), since PSPM ships no keys. Google Drive (`drive.file` scope, sign in in the browser) and Dropbox (PKCE, no secret) behind one small provider interface. Backs up saves first, then anything picked. | medium |
| 8 | PPSSPP saves | **Built (phase 2).** **Change from the ask:** a symlink to the PSP breaks the moment it's unplugged (PPSSPP sees an empty folder and may write saves in the wrong place). Instead PSPM syncs `SAVEDATA` both ways each time the PSP is plugged in, keeps a snapshot before overwriting, and asks when both sides changed. Each save folder is compared by content hash against the last sync, so the side that changed wins; if both changed it asks, suggesting the newest. Deletes are never synced. Finds PPSSPP via per-OS paths in config (Windows Documents, Linux, Flatpak); portable installs pick the folder once. | medium |
| 9 | Auto softmod | **Built (phase 4), copy only.** Copies ARK-5 (FasterARK lite: `PSP/GAME/FasterARK`, `PSP/SAVEDATA/ARK_01234`) or ARK-4 r206 (`ARK_01234`, `ARK_Loader`) to the stick after sha256-checking the download; blocks on firmware other than 6.60/6.61 (when PSPM Info reported it) and on too little space; moves replaced folders to a backup; reads every copy back. Battery, firmware (when unknown) and risk are confirmed by the user. cIPL is not copied. Original plan: Guided install that copies the CFW files to the stick. Hard blocks: firmware must be 6.60/6.61 (from the info app in #2 or user confirmation), battery at least 75% with charger, no Strider2 units, hashes checked after copy, explicit "I understand" before anything. Permanent cIPL is a separate, opt-in step with its own warning. Both ARK-5 (via FasterARK) and ARK-4 final (v4.20.69 r206) are offered; the user picks in the app, ARK-5 is the default. CFW zips downloaded from the official GitHub releases (GPLv3), never Sony files. | **brick risk** |

Research notes, checked 2026-10-11: ARK-4's final release (v4.20.69 r206) is from May 2026 and the repo was archived on 2026-08-02. ARK-5 (5.1.0) ships through FasterARK's single `latest` release. Neither repo has a LICENSE file, so PSPM downloads rather than bundles them.

## Roadmap

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Core, config, PSP detection, open on plug-in (login watcher), file manager, Docker, CI for Windows + Linux | scaffolded, 5 tests pass, Linux app build checked |
| 2 | MP3 tagger, PPSSPP save sync, cloud backup | built |
| 3 | Diagnostics (+ PSP info app), PS1 converter, themes A + B | diagnostics and PS1 converter built |
| 4 | Softmod with safety checks (ARK-5 via FasterARK and ARK-4 final, user picks in the app), PSP to PC streaming | built |
| 5 | Theme editor (C), PC to PSP streaming, macOS, mobile shell | later |

Each phase is one PR per feature so CI checks each separately.
