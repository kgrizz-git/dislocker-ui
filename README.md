# dislocker-ui

Simple macOS GUI frontend for [dislocker](https://github.com/Aorimn/dislocker).
It does **not** fork or reimplement BitLocker crypto — it shells out to an
installed `dislocker-fuse` and then attaches/mounts the resulting NTFS image.

**Version:** see `VERSION` (currently 0.1.0).

## What it does

1. Unlock a BitLocker volume with a user password, recovery password, or `.bek` file.
2. Mount the decrypted NTFS image under `/Volumes` (read-only by default).
3. Unmount cleanly (NTFS → detach raw disk → unmount FUSE).

## Requirements

| Dependency | Required? | Role |
|------------|-----------|------|
| macOS | Yes | Target platform |
| Python 3.10+ with tkinter | Yes | GUI |
| [dislocker](https://github.com/Aorimn/dislocker) | Yes | BitLocker unlock (`dislocker-fuse`) |
| macFUSE or FUSE-T | Yes | Needed by dislocker (and by ntfs-3g if used) |
| `ntfs-3g` | Optional | Writable NTFS mounts in Finder |

This project does **not** bundle FUSE or ntfs-3g (system extensions / installers).

## Installation

### 1. Homebrew

If needed: https://brew.sh

### 2. FUSE backend (required)

**macFUSE** is required for the Homebrew macOS formulae below. After install,
approve the system extension in **System Settings → Privacy & Security**, then
reboot if macOS asks.

```bash
brew install --cask macfuse
```

(`brew install --cask` needs an interactive Terminal for `sudo`. You can also
open the `.dmg` from the Homebrew cache and run **Install macFUSE.pkg**.)

### 3. dislocker + ntfs-3g (macOS Homebrew)

Homebrew core’s `dislocker` / `ntfs-3g` formulae are awkward on modern macOS
(no bottles / FUSE disabled). Use the community macFUSE tap:

```bash
brew tap gromgit/homebrew-fuse
# Newer Homebrew may require:
#   brew trust --formula gromgit/fuse/dislocker-mac
#   brew trust --formula gromgit/fuse/ntfs-3g-mac
brew install gromgit/fuse/dislocker-mac
brew install gromgit/fuse/ntfs-3g-mac   # optional but needed for Finder write
```

Confirm:

```bash
which dislocker-fuse
dislocker-fuse -h | head
which ntfs-3g
ntfs-3g --version
```

If `dislocker-fuse` fails with a missing `libmbedcrypto.16.dylib`, the bottle
was built against mbedtls 3.x while Homebrew linked mbedtls 4.x. Fix:

```bash
brew install mbedtls@3
ln -sf /opt/homebrew/opt/mbedtls@3/lib/libmbedcrypto.16.dylib \
  /opt/homebrew/opt/mbedtls/lib/libmbedcrypto.16.dylib
```

(That symlink can break on `brew upgrade`; re-run if dislocker stops loading.)

Then click **Recheck deps** in dislocker-ui (or restart it). Uncheck Read-only
only when `ntfs-3g` is found.

Notes:

- Community taps are unsupported by Homebrew.
- Writable mounts may still need Full Disk Access / admin rights depending on
  your macOS version.

### 4. This app

```bash
cd ~/MyCode/dislocker-ui
chmod +x run.sh   # once
```

No Python packages beyond the stdlib (tkinter) are required.

## Usage

1. Plug in / attach the BitLocker disk.
2. Launch the UI:

```bash
cd ~/MyCode/dislocker-ui
./run.sh
```

Or:

```bash
cd ~/MyCode/dislocker-ui
PYTHONPATH=src python3 -m dislocker_ui
```

3. Select a volume (or type `/dev/diskXsY`).
4. Choose unlock method: user password, recovery password, or `.bek` file.
5. Leave **Read-only** checked unless ntfs-3g is installed and you need writes.
6. Click **Mount**. When it succeeds, open the path under `/Volumes` (shown in
   the dialog / status line).
7. When finished, click **Unmount** before ejecting the disk or shutting down.

Raw-disk access often needs admin rights. If mount fails with permission errors,
run from a terminal where you can authenticate, or adjust device permissions
carefully for your use case.

## Read vs write

| Goal | What you need |
|------|----------------|
| Browse files (read) | dislocker + FUSE + stock macOS NTFS mount |
| Edit/copy onto the volume (write) | above + `ntfs-3g` (or other writable NTFS) |

- **Dislocker** handles BitLocker. With `-r` (this app’s default) the FUSE layer
  is read-only as well.
- **Stock macOS NTFS** is usually read-only in Finder even when dislocker would
  allow writes.
- Uncheck Read-only in the UI only when `ntfs-3g` is detected.

## Safety notes

- Default mount mode is **read-only**.
- Passwords are passed to `dislocker-fuse` as CLI arguments (visible briefly in
  process listings). Prefer a private machine and unmount when finished.
- Always use **Unmount** in the app before ejecting the disk or sleeping the Mac.

## Layout

```
src/dislocker_ui/   # application package
tests/              # test helpers / scripts
tmp/                # local scratch (gitignored)
run.sh              # launcher
```

## License

This frontend is for personal use. Dislocker itself is GPL-2.0-or-later; respect
its license when redistributing anything that bundles or tightly couples to it.
