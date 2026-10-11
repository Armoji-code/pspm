# PSPM

Manager and companion app for the Sony PSP. Plug in a PSP (USB mode) and PSPM opens and shows its memory stick.

Status: phase 1 (core, PSP detection, auto-open, file manager) and phase 2 (MP3 tagger, PPSSPP save sync, cloud backup), diagnostics and the PS1 converter from phase 3, PSP to PC streaming and the ARK softmod from phase 4. See [docs/PLAN.md](docs/PLAN.md).

## Run

```sh
pip install -e ".[dev]"
pspm                  # open the app
pspm autostart on     # start the watcher at login; it opens PSPM when a PSP is plugged in
pspm autostart off
pspm config           # where your config.toml override lives
```

Fonts (optional, the UI falls back without them): install Space Grotesk, Space Mono and Doto from Google Fonts.

## PPSSPP saves

Each time the PSP is plugged in, PSPM syncs `PSP/SAVEDATA` with PPSSPP's both ways, so you can carry on where you left off on either.
The side that changed since the last sync wins. If a save changed on both, PSPM asks which to keep (newest is suggested).
Every overwritten save is moved to a backup folder first, and deleting a save on one side never deletes it on the other.
If PPSSPP isn't found (portable install, custom memstick), press PPSSPP FOLDER and pick its `PSP/SAVEDATA`.

## Cloud backup

Each time the PSP is plugged in, PSPM uploads new and changed files from `PSP/SAVEDATA` to a `PSPM` folder in your Dropbox or Google Drive, keeping the stick's layout.
Nothing is ever deleted in the cloud, and both clouds keep older versions of overwritten files. Back up more folders with `folders = ["saves", "music", "photos"]` under `[backup]` in your `config.toml`.
Sign in once from the CLOUD BACKUP page; it happens in your browser and the token is stored in your OS keyring (Windows Credential Manager, or GNOME Keyring / KWallet on Linux).

PSPM has no shared app key, so you register your own app once (free, about five minutes) and put its id in your `config.toml`:

**Dropbox**
1. Go to https://www.dropbox.com/developers/apps, Create app, choose Scoped access and App folder, and give it any name.
2. On the Permissions tab tick `files.content.write` and press Submit.
3. On the Settings tab add the redirect URI `http://127.0.0.1:53682/`, and copy the App key.
4. In `config.toml`:
   ```toml
   [backup.dropbox]
   client_id = "your app key"
   ```

**Google Drive**
1. At https://console.cloud.google.com create a project and enable the Google Drive API.
2. Set up the OAuth consent screen (External) and set its publishing status to In production. Testing mode makes you sign in again every 7 days. PSPM only asks for the `drive.file` scope (its own files), which needs no Google review.
3. Under Credentials, create an OAuth client ID of type Desktop app and copy its id and secret.
4. In `config.toml`:
   ```toml
   [backup]
   provider = "gdrive"

   [backup.gdrive]
   client_id = "....apps.googleusercontent.com"
   client_secret = "..."
   ```

Other clouds can be added by another package: subclass `pspm.backup.Provider` and register it under the `pspm.cloud` entry point.

## Diagnostics

- **Space**: used, free and total on the memory stick.
- **Check filesystem**: a read-only check (`fsck.vfat -n` on Linux, `chkdsk` on Windows). Both need admin rights, so you get a password or UAC prompt. Nothing is repaired.
- **Test capacity**: finds fake memory sticks. PSPM fills the free space with test data; you unplug and replug the PSP, then press VERIFY CAPACITY to read it back, and the test data is deleted. It takes a while on a big stick. On a fake card, writing past its real size can damage files already on it, so back up first.
- **PSP info**: battery, firmware and model can't be read over USB. Press INSTALL INFO APP, run *PSPM Info* from the PSP's Game menu (needs custom firmware), and the next time you plug in PSPM shows them. The app's source is in `psp/pspm_info` and CI builds it with the PSPDEV toolchain.

## PS1 converter

Turns a PS1 game into an `EBOOT.PBP` in `PSP/GAME/<game ID>/`, for the PSP's built-in PS1 emulator (needs custom firmware). Press ADD DISCS and pick one game's images: `.cue` (with its `.bin`), `.img`, `.iso`, `.chd`, `.m3u` or a `.zip`/`.7z`. Several discs become one multi-disc EBOOT, in file name order. The title comes from the disc's game ID; ICON sets an optional icon from any picture.

The conversion is done by [PSXPackager](https://github.com/RupertAvery/PSXPackager) (MIT), downloaded on first use from its GitHub release and checked against the sha256 in `defaults.toml`. On Windows it needs the .NET 8 runtime. PSPM ships no Sony files, BIOS or games.

## Streaming

Shows the PSP's screen in a window on the PC, live over the USB cable; your keyboard or gamepad controls the PSP. Needs custom firmware.

1. With the PSP in USB mode, open STREAMING and press INSTALL PLUGIN. It copies `RemoteJoyLite.prx` to `SEPLUGINS/` and turns it on for ARK (`PLUGINS.TXT`) and PRO/ME (`vsh.txt`, `game.txt`, `pops.txt`).
2. Leave USB mode and restart the PSP, keeping the cable in. The PSP's USB can't be a drive and a stream at the same time.
3. Press START VIEWER.

- **Windows**, first time: with the plugin running, open [Zadig](https://zadig.akeo.ie), pick *PSP Type B* and install the libusb-win32 driver. PSPM tells you if it's missing.
- **Linux**: install Wine and libusb 0.1 (`libusb-0.1-4` on Debian/Ubuntu). The first start asks for your password once to add a udev rule so the viewer can open the PSP's USB.
- On 2000/3000/Go/Street models, RemoteJoyLite's author recommends turning off the CFW's extended memory, ISO cache and memory stick speedup.
- REMOVE PLUGIN takes it out of every list again.

The plugin and viewer are [RemoteJoyLite](https://github.com/Kethen/RemoteJoyLite) (Kethen's fork), downloaded on first use from its GitHub release and checked against the sha256 in `defaults.toml`. Streaming the PC's screen to the PSP isn't offered: no maintained tool does it.

## Softmod (ARK custom firmware)

Copies the official ARK files to the memory stick so you can install ARK on the PSP. PSPM never writes the PSP's flash; that happens on the PSP when you run the installer.

**Supported:** every PSP-1000, 2000, 3000, Go and Street (E1000) on official firmware 6.60 or 6.61.
**Not supported:** other firmware (update to 6.61 with Sony's updater first), PS Vita, development units. Already on ARK-4: use ARK's own updater. On PRO or ME with a permanent cIPL: follow ARK's DC10 guide instead.

1. Pick ARK-5 (default, via [FasterARK](https://github.com/PSP-Arkfive/FasterARK)) or ARK-4's final release ([v4.20.69 r206](https://github.com/PSP-Archive/ARK-4), archived August 2026).
2. Tick the boxes: firmware (skipped when the PSPM Info app already reported it), battery at 75% or more with the charger in, and the risk.
3. Press COPY ARK. PSPM checks the firmware and free space, shows what it will copy, moves any ARK folders already there to a backup, copies, and reads every file back.
4. On the PSP, leave USB mode, keep the charger in, and run FasterARK (ARK-5) or ARK Loader (ARK-4) from the Game menu.

ARK then runs until a full power-off, after which you run the same app again. Making it permanent needs ARK's cIPL installer (`CustomIPL` / `ARK_cIPL`), which writes the PSP's flash; PSPM doesn't copy it.
Both releases are downloaded on first use from GitHub and checked against the sha256 in `defaults.toml`. FasterARK reuses one `latest` tag, so its next release will be refused until the url and sha256 there are updated.

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
