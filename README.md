# dislocker-ui

Simple macOS GUI frontend for [dislocker](https://github.com/Aorimn/dislocker).
It does **not** fork or reimplement BitLocker crypto — it shells out to an
installed `dislocker-fuse` and then attaches/mounts the resulting filesystem
image (NTFS via ntfs-3g, or FAT/ExFAT via system mount helpers).

**Version:** see `VERSION` (currently 0.4.0).

Security reports: [`SECURITY.md`](SECURITY.md). Contributing / Issues:
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## What it does

1. Unlock a BitLocker volume with a user password, recovery password, or `.bek` file.
2. Mount the decrypted image under `/Volumes` (read-only by default): **ntfs-3g**
   for NTFS, system **mount_msdos** / **mount_exfat** for BitLocker To Go FAT/ExFAT.
3. Unmount cleanly (volume → detach raw disk → unmount FUSE).

On modern macOS, launch with **`sudo ./run.sh`**. The GUI then runs already-root
and mounts in-process (no osascript administrator dialog). An unprivileged
GUI cannot open removable `/dev/disk*` under TCC, so non-sudo launches cannot
complete a useful mount.

Elevated GUI mounts intentionally support physical BitLocker devices
(`/dev/diskN` or `/dev/diskNsM`) only; regular image files are out of scope.

## Requirements

| Dependency | Required? | Role |
|------------|-----------|------|
| macOS | Yes | Target platform |
| Python 3.10+ with tkinter | Yes | GUI |
| [dislocker](https://github.com/Aorimn/dislocker) | Yes | BitLocker unlock (`dislocker-fuse`) |
| **macFUSE** | Yes | Required for dislocker + ntfs-3g options used here (`allow_other` / `local` / uid). **FUSE-T is not validated in 0.2.0.** |
| **`ntfs-3g`** | **Yes** (for NTFS) | RO and RW NTFS mounts (kernel `mount_ntfs` is missing on recent macOS). FAT/ExFAT BitLocker To Go uses system `mount_msdos` / `mount_exfat`. |

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

The Homebrew build below satisfies the **GUI preflight check** only; the
elevated mount flow requires root-owned binaries. Run
`scripts/install-root-deps.sh` once (section 4) to satisfy both.

Homebrew core’s `dislocker` / `ntfs-3g` formulae are awkward on modern macOS
(no bottles / FUSE disabled). Use the community macFUSE tap:

```bash
brew tap gromgit/homebrew-fuse
# Newer Homebrew may require:
#   brew trust --formula gromgit/fuse/dislocker-mac
#   brew trust --formula gromgit/fuse/ntfs-3g-mac
brew install gromgit/fuse/dislocker-mac
brew install gromgit/fuse/ntfs-3g-mac   # required for mounts on modern macOS
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

Then click **Recheck deps** in dislocker-ui (or restart it). Without `ntfs-3g`,
Mount is unavailable. Uncheck Read-only when you need writes.

Notes:

- Community taps are unsupported by Homebrew.
- The GUI's dependency check is advisory. For the elevated mount itself,
  the complete toolchain must be trusted: macOS supplies `hdiutil`
  (`/usr/bin/hdiutil`), `diskutil` (`/usr/sbin/diskutil`), and `umount`
  (`/sbin/umount`); an administrator must install `dislocker-fuse` and
  `ntfs-3g` as root-owned, non-group/world-writable executables under
  `/usr/local/sbin` or `/opt/local/sbin`. A normal user-owned Homebrew
  installation is deliberately not executed as root.
- Mounts need administrator authorization (macOS dialog). Running
  `sudo ./run.sh` remains a power-user escape hatch (already-root path skips
  osascript). Elevating from a user-writable checkout is no stronger than that.

### 4. One-time root install (optional helper)

The Homebrew path above is advisory only. To make the **elevated mount flow**
trust the toolchain, run the installer once — it builds `dislocker-fuse` and
`ntfs-3g` **from source** and installs them root-owned into `/opt/local/sbin`:

```bash
sudo scripts/install-root-deps.sh          # or: --prefix /usr/local
```

- **From source, not a copy:** a copied Homebrew binary keeps load commands
  pointing at the user-owned Homebrew prefix, so a root-trusted binary would
  load user-mutable dylibs. A `--prefix` build plus vendoring keeps the whole
  dyld closure root-managed.
- **libfuse is vendored:** macFUSE installs its libfuse into `/usr/local/lib`,
  which is often user-owned (true even on Apple Silicon). The installer copies
  libfuse root-owned into the install prefix and re-points the binaries, so no
  root-trusted binary loads a library from a user-writable directory. Works on
  both Apple Silicon and Intel.
- **macFUSE:** if macFUSE isn't installed, the script installs the cask and
  asks you to re-run. macFUSE's kernel extension is approved the first time you
  actually **mount** a FUSE volume (on Apple Silicon this can require enabling
  kernel extensions in Recovery, then a reboot) — there is no "system
  extension" to approve in Privacy & Security beforehand.
- This is optional; the manual Homebrew + tap path (section 3) remains valid
  for the GUI advisory check.

### 5. This app

```bash
cd /path/to/dislocker-ui
chmod +x run.sh   # once
```

No Python packages beyond the stdlib (tkinter) are required.

## Usage

1. Plug in / attach the BitLocker disk.
2. Launch the UI **with sudo** (required so mounts can open removable
   `/dev/disk*` under macOS TCC; an unprivileged GUI cannot complete a useful
   mount):

```bash
cd /path/to/dislocker-ui
sudo ./run.sh
```

`SUDO_UID` / `SUDO_GID` are preserved so mounted files are owned by your user,
not root. Running `python3 -m dislocker_ui` without sudo is unsupported for
real mounts.

3. Select a volume (or type `/dev/diskXsY`).
4. Choose unlock method: user password, recovery password, or `.bek` file.
5. Leave **Read-only** checked unless you need writes.
6. Click **Mount**, then open the path under `/Volumes` (shown in the dialog /
   status line).
7. When finished, click **Unmount** before ejecting the disk or shutting down.

## Read vs write

| Goal | What you need |
|------|----------------|
| Browse files (read) | dislocker + macFUSE + **ntfs-3g** (NTFS) or **mount_msdos/exfat** (FAT/ExFAT) |
| Edit/copy onto the volume (write) | same + uncheck Read-only |

- **Dislocker** handles BitLocker. With `-r` (this app’s default) the FUSE layer
  is read-only as well.
- **After decrypt**, the app probes the attached image: **NTFS** uses
  **ntfs-3g** (required on modern macOS; no `mount_ntfs`); **FAT/ExFAT**
  (BitLocker To Go) uses system `mount_msdos` / `mount_exfat`.
- NTFS mounts use `umask=077` with `allow_other`. FAT/ExFAT mounts use
  `-u/-g` from `SUDO_UID`/`SUDO_GID` and `-m 700` (owner-only mode bits).

## Safety notes

- Default mount mode is **read-only**.
- Passwords / recovery keys are passed to `dislocker-fuse` as CLI arguments
  (visible briefly in process listings / `ps`). Prefer a private machine and
  unmount when finished. Secrets are **not** placed in AppleScript; they travel
  briefly in a mode-0600 request file during elevation.
- Elevation writes only its mode-0600 request file under your
  `Library/Application Support/dislocker-ui/` folder; a killed-run request is
  swept before the next Mount/Unmount. The privileged helper derives its
  session state and diagnostics beneath root-controlled `/var/db/dislocker-ui/`.
  Prefer `sudo ./run.sh` so the GUI is already root and skips that path.
- Always use **Unmount** in the app before ejecting the disk or sleeping the Mac.
- Running the GUI as root does not harden a world-writable source tree — keep
  the checkout private.

## Layout

```
src/dislocker_ui/   # application package
tests/              # test helpers / scripts
plans/              # active implementation plans (archive/ for finished)
TO_DO.md            # open work only (remove items when done)
CHANGELOG.md        # user-visible product changes
CHANGELOG.dev.md    # harness / CI / docs-only notes
tmp/                # local scratch (gitignored)
.context/           # disposable agent scratch (gitignored)
run.sh              # launcher
pyproject.toml      # packaging + Ruff config
.pre-commit-config.yaml  # optional local hooks (incl. pre-push)
hooks/              # local policy scripts (absolute-path / line-count)
.sonarcloud.properties   # SonarCloud Automatic Analysis scope
.github/            # CI + issue/PR templates
```

## Developer checks

Runtime needs no pip packages. Optional lint/hooks:

```bash
pip install -e ".[dev]"   # ruff, pre-commit, pytest, pip-audit
ruff check src tests hooks
ruff format src tests hooks
pytest --cov --cov-report=term-missing
python3 hooks/check_absolute_paths.py
python3 hooks/check_file_size.py
pip-audit                 # prefer a clean venv, not a shared global env
pre-commit install -t pre-commit -t pre-push
pre-commit run --all-files
```

CI on GitHub runs compileall, Ruff (incl. complexity), absolute-path and
line-count checks, pytest with coverage, pip-audit, Semgrep (OWASP Top 10 +
Python rules), and gitleaks.

### SonarCloud (Automatic Analysis)

No `SONAR_TOKEN` is required for the default setup:

1. Sign in at [SonarQube Cloud](https://sonarcloud.io) with GitHub and import
   `kgrizz-git/dislocker-ui` (public / open-source plan is fine).
2. In the project: **Administration → Analysis Method → Automatic Analysis** → on.
3. Optional: keep [`.sonarcloud.properties`](.sonarcloud.properties) for source/test
   paths (already in this repo). Set path exclusions in the SonarCloud UI
   (**Administration → General Settings → Analysis Scope**); Automatic Analysis
   does not support wildcard `sonar.exclusions` in `.sonarcloud.properties`.

Do **not** also run a CI-based Sonar scan while Automatic Analysis is enabled
(SonarCloud rejects that combo). Coverage upload needs CI-based analysis later
if you want it.

## License

**dislocker-ui** is free software: you can redistribute it and/or modify it under
the terms of the [GNU General Public License](LICENSE) as published by the Free
Software Foundation, either **version 3** of the License, or (at your option)
any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

SPDX: `GPL-3.0-or-later`. Commercial use is allowed; copyleft applies when you
redistribute this program or a modified version (you must provide source under
the same license terms).

### Related tools (not covered by this license)

This project **does not** bundle [dislocker](https://github.com/Aorimn/dislocker),
macFUSE/FUSE-T, or `ntfs-3g`. Those remain separate system dependencies under
their own licenses (dislocker is typically **GPL-2.0-or-later**). Using
dislocker-ui does not grant you rights to those projects. If you redistribute a
package that includes their binaries or sources, follow *their* license terms.
