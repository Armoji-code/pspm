# PSPM

Manager and companion app for the Sony PSP. Plug in a PSP (USB mode) and PSPM opens and shows its memory stick.

Status: phase 1 (core, PSP detection, auto-open, file manager) and phase 2 (MP3 tagger, PPSSPP save sync, cloud backup), diagnostics from phase 3. See [docs/PLAN.md](docs/PLAN.md).

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
