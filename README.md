# dislocker-ui

Simple macOS GUI frontend for [dislocker](https://github.com/Aorimn/dislocker).
It does **not** fork or reimplement BitLocker crypto — it shells out to an
installed `dislocker-fuse` and then attaches/mounts the resulting NTFS image.

**Version:** see `VERSION` (currently 0.2.0).

Security reports: [`SECURITY.md`](SECURITY.md). Contributing / Issues:
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## What it does

1. Unlock a BitLocker volume with a user password, recovery password, or `.bek` file.
2. Mount the decrypted NTFS image under `/Volumes` via **ntfs-3g** (read-only by default).
3. Unmount cleanly (NTFS → detach raw disk → unmount FUSE).

On modern macOS the GUI is unprivileged; Mount/Unmount request **administrator
privileges** (macOS password dialog) so the full pipeline can open `/dev/disk*`
and mount under `/Volumes`. Cancel and timeout have distinct messages. Unmount
of an elevated session asks for admin again (two prompts per cycle).

## Requirements

| Dependency | Required? | Role |
|------------|-----------|------|
| macOS | Yes | Target platform |
| Python 3.10+ with tkinter | Yes | GUI |
| [dislocker](https://github.com/Aorimn/dislocker) | Yes | BitLocker unlock (`dislocker-fuse`) |
| **macFUSE** | Yes | Required for dislocker + ntfs-3g options used here (`allow_other` / `local` / uid). **FUSE-T is not validated in 0.2.0.** |
| **`ntfs-3g`** | **Yes** | RO and RW NTFS mounts (kernel `mount_ntfs` is missing on recent macOS) |

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
- Mounts need administrator authorization (macOS dialog). Running
  `sudo ./run.sh` remains a power-user escape hatch (already-root path skips
  osascript). Elevating from a user-writable checkout is no stronger than that.

### 4. This app

```bash
cd /path/to/dislocker-ui
chmod +x run.sh   # once
```

No Python packages beyond the stdlib (tkinter) are required.

## Usage

1. Plug in / attach the BitLocker disk.
2. Launch the UI:

```bash
cd /path/to/dislocker-ui
./run.sh
```

Or:

```bash
cd /path/to/dislocker-ui
PYTHONPATH=src python3 -m dislocker_ui
```

3. Select a volume (or type `/dev/diskXsY`).
4. Choose unlock method: user password, recovery password, or `.bek` file.
5. Leave **Read-only** checked unless you need writes (ntfs-3g required either way).
6. Click **Mount**, complete the macOS administrator prompt, then open the path
   under `/Volumes` (shown in the dialog / status line).
7. When finished, click **Unmount** (second admin prompt if the session was
   elevated) before ejecting the disk or shutting down.

Already-root / `sudo ./run.sh` skips the osascript dialog and mounts in-process.

## Read vs write

| Goal | What you need |
|------|----------------|
| Browse files (read) | dislocker + macFUSE + **ntfs-3g** (`-o ro`) |
| Edit/copy onto the volume (write) | same + uncheck Read-only |

- **Dislocker** handles BitLocker. With `-r` (this app’s default) the FUSE layer
  is read-only as well.
- **ntfs-3g** is required for both RO and RW on modern macOS (no `mount_ntfs`).
- Mount options include `umask=077` with `allow_other` so other local accounts
  cannot read the decrypted volume.

## Safety notes

- Default mount mode is **read-only**.
- Passwords / recovery keys are passed to `dislocker-fuse` as CLI arguments
  (visible briefly in process listings / `ps`). Prefer a private machine and
  unmount when finished. Secrets are **not** placed in AppleScript; they travel
  briefly in a mode-0600 request file during elevation.
- Always use **Unmount** in the app before ejecting the disk or sleeping the Mac.
- Elevation does not harden a world-writable source tree — treat
  `sudo ./run.sh` and elevating this checkout similarly.

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
