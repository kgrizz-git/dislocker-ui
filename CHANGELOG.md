# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Developer / harness-only notes live in [`CHANGELOG.dev.md`](CHANGELOG.dev.md).

## [0.5.1] - 2026-08-12

### Security

- Elevation preflight now rejects group/world-writable `.py`/`.pyc`/`.so` files,
  `__pycache__`, and writable descendant directories under the PYTHONPATH
  source root, preventing a trojan-module attack when an attacker shares
  group-write on the checkout. Known non-code directories (build artifacts,
  caches, VCS metadata) are excluded to avoid false positives. Skips the
  recursive scan for pip-installed packages (site-packages) where the package
  manager controls file modes.
- When launched as root (`sudo ./run.sh`), the GUI uses fixed root-managed
  executable paths (`discover_privileged_deps`) instead of PATH-based
  discovery, closing a PATH injection vector for mount tools.

### Changed

- Root-launched GUI (`sudo ./run.sh`) now only trusts dislocker-fuse and
  ntfs-3g from root-managed locations (`/usr/local/sbin`, `/opt/local/sbin`).
  User-owned Homebrew installs remain available for the unprivileged GUI and
  the osascript elevation preflight, but are not executed as root. If tools are
  missing, the GUI shows an actionable message pointing at
  `scripts/install-root-deps.sh`.

## [0.5.0] - 2026-08-07

### Added
- Password visibility toggle button for user password field. Click "Show" to
  reveal password in clear text (default: hidden). Passwords automatically
  re-mask after 30 seconds.

## [0.4.4] - 2026-08-06

### Changed

- Without `ntfs-3g`, the GUI forces read-only (volume type is unknown until
  after decrypt). FAT/ExFAT RO mounts still work; NTFS still requires ntfs-3g.

### Fixed

- `run.sh` writable-path check uses BSD `stat -f` instead of `find -maxdepth`.

## [0.4.3] - 2026-08-06

### Changed

- Core dependency check no longer requires `ntfs-3g`. FAT/ExFAT BitLocker To Go
  volumes can mount with only `dislocker-fuse` + system mount helpers; NTFS still
  needs `ntfs-3g` and fails with a clear error when it is missing.
- `run.sh` documents the checkout-as-root ownership model and refuses
  group-/world-writable launcher/`src` paths before `exec`.

### Fixed

- Installer: explicit `--prefix` wins over a `PREFIX` env default; empty dyld
  target lists are guarded under bash 3.2 `set -u`; manifest re-check messaging
  no longer claims a full TOCTOU close.

## [0.4.2] - 2026-08-06

### Fixed

- FAT/ExFAT mount argv is validated before `subprocess` (physical `/dev/disk*`
  only, safe absolute mountpoints, bounded uid/gid) so untrusted request fields
  cannot reach OS commands.

## [0.4.1] - 2026-08-05


### Fixed

- Main window is raised to the front on launch so it is not hidden behind
  Terminal (or another launcher) on macOS.
- FAT/ExFAT mounts use `-m 700` (owner-readable mode). The previous `-m 077`
  left owner bits clear, so Finder showed a red/empty volume after a successful
  mount.

### Changed

- `run.sh` requires root (`sudo ./run.sh`). Unprivileged launches cannot open
  removable `/dev/disk*` under macOS TCC, so a non-sudo GUI could not complete
  a useful mount.

## [0.4.0] - 2026-08-05

### Added

- BitLocker To Go volumes whose inner filesystem is **FAT32** or **ExFAT** now
  mount after decrypt via system `mount_msdos` / `mount_exfat` (NTFS volumes
  still use ntfs-3g).

## [0.3.1] - 2026-08-05

### Added

- Optional root-managed install script (`scripts/install-root-deps.sh`) that
  builds `dislocker-fuse` and `ntfs-3g` from source and installs them
  root-owned into `/opt/local/sbin` (or `/usr/local/sbin`) so the elevated
  mount flow trusts them. Fully supported on Apple Silicon; on Intel it
  refuses with a clear message when Homebrew owns `/usr/local` (macFUSE's
  libfuse would then load from a user-writable directory).
- The GUI "Privileged tools unavailable" dialog now points at the script.

### Fixed

- Root installer builds dislocker against macFUSE ≥4.10 by compiling with
  `-DFUSE_DARWIN_ENABLE_EXTENSIONS=0` (vanilla FUSE3 API). Without that flag
  AppleClang rejects `getattr`/`readdir` as incompatible with
  `fuse_darwin_attr`.
- Root installer stages dislocker with `DESTDIR` instead of
  `cmake --install --prefix`. Absolute `bindir`/`libdir` install rules were
  writing `/opt/local/lib` during the unprivileged build phase.
- Root installer pins ntfs-3g to the `2026.7.7` release (Darwin
  `getxattr`/`setxattr` position API). The previous `master` tip failed to
  compile against macFUSE fuse2.
- Root installer configures ntfs-3g with `--bindir=$PREFIX/sbin` so
  `ntfs-3g` (a `rootbin_PROGRAM`) lands in the sbin path
  `discover_privileged_deps` accepts. `--sbindir` alone left it in `bin`.
- Root installer recreates versioned `libdislocker.*.dylib` symlinks and
  strips Homebrew `LC_RPATH` entries so `@rpath/libdislocker.0.7.dylib`
  resolves under `$PREFIX/lib` at mount time.

## [0.3.0] - 2026-08-05

### Security

- Hardened the elevated macOS mount flow: root now derives canonical session
  state, diagnostics, and executable paths instead of accepting them from the
  unprivileged request file.
- Elevated GUI mounting accepts physical `/dev/diskN` and `/dev/diskNsM`
  devices only. Regular disk-image files are no longer supported in that flow.
- Added descriptor-safe request reads, strict request/label validation, and
  constrained privileged cleanup. Incomplete unmounts retain state for retry.

### Changed

- The privileged helper runs only root-managed `dislocker-fuse` and `ntfs-3g`
  from `/usr/local/sbin` or `/opt/local/sbin`, plus fixed macOS system tools.
  User-owned Homebrew paths remain useful for GUI preflight but are not executed
  as root.
- The GUI now shows elevated-session status from the canonical root-owned state
  file and reports a missing trusted toolchain before requesting administrator
  authorization.

## [0.2.0] - 2026-07-31

### Added

- macOS administrator elevation for the full mount/unmount pipeline via
  `osascript` (`do shell script … with administrator privileges`), keeping the
  tkinter GUI unprivileged.
- Privileged child module (`dislocker_ui.privileged`) with a mode-0600 request
  file protocol (secrets never in AppleScript).
- Distinct GUI dialogs for authorization cancel vs timeout.

### Changed

- **`ntfs-3g` is required** for both read-only and read/write mounts on modern
  macOS (kernel `mount_ntfs` is unavailable). Stock `mount -t ntfs` path removed.
- NTFS mounts use `allow_other,local,uid=,gid=,umask=077` (plus fmask/dmask) so
  Finder can access elevated mounts without world-readable decrypted data.
- Unmount of an elevated session prompts for admin privileges again (two prompts
  per mount/unmount cycle).

### Security

- Elevation preflight refuses symlink or group/world-writable `src/` /
  Application Support directories.
- Request/log files are mode 0600; log open uses `O_NOFOLLOW` + `fstat` checks.
- Elevation prepare+run is serialized with a per-user lock so a concurrent
  mount/unmount cannot delete another transaction's request or log file while
  an authorization dialog is pending.
- Residual risk documented: BitLocker secrets still appear briefly on
  `dislocker-fuse` argv (visible to `ps`). Elevating from a user-writable checkout
  is no stronger than `sudo ./run.sh`.

## [0.1.6] - 2026-07-31

No user-facing changes; internal version alignment for the elevation track.
Developer/harness details live in [`CHANGELOG.dev.md`](CHANGELOG.dev.md).

## [0.1.5] - 2026-07-31

### Added

- Public-repo meta: `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS`, Dependabot
  version updates, and issue-template contact links for private security reports.

## [0.1.4] - 2026-07-31

### Changed

- FUSE staging directories are created under the process temp dir instead of
  world-writable `/tmp` (Sonar `python:S5443`).

### Fixed

- Reduced cognitive complexity of mount/unmount orchestration helpers.

## [0.1.3] - 2026-07-30

### Added

- Unit tests for `session`, `deps`, and `disks` (pytest).
- Coverage gate on core modules (GUI/runner omitted for now); CI `pip-audit` and
  Semgrep (OWASP Top 10 + Python) on the `[dev]` install / scan set; file
  line-count and complexity (Ruff C901) checks.
- SonarCloud Automatic Analysis support (`.sonarcloud.properties`).

### Changed

- Source file hard line-count cap is **800** (soft warning remains 600).
- CI GitHub Actions pinned to full commit SHAs (checkout / setup-python).
- Semgrep CI container pinned by digest (`1.170.0`).

## [0.1.2] - 2026-07-30

### Changed

- Licensed the project as **GPL-3.0-or-later** (see `LICENSE`). README clarifies
  commercial use + copyleft, and that dislocker / FUSE / ntfs-3g remain separate
  dependencies under their own licenses.

### Fixed

- Track `tests/.gitkeep` so CI `ruff check src tests hooks` does not fail on a
  missing `tests/` directory.

## [0.1.1] - 2026-07-30

### Fixed

- Mount/Unmount error dialogs no longer risk a blank/failed message when showing
  exceptions from a background thread (Tk `after` lambdas now bind the message).

### Changed

- Repository hygiene (CI, pre-commit, Ruff, templates). See `CHANGELOG.dev.md`.

## [0.1.0] - 2026-07-30

### Added

- Initial macOS Python/tkinter frontend for installed `dislocker`.
- Mount / Unmount flow: BitLocker unlock via FUSE, attach raw NTFS image, mount filesystem.
- Unlock methods: user password, recovery password, `.bek` file.
- Read-only mounts by default; optional read/write when `ntfs-3g` is available.
- Dependency detection for `dislocker-fuse`, `hdiutil`, and optional `ntfs-3g`.
- Session state so Unmount can reverse the last successful Mount.
- README install guide for macFUSE and gromgit `dislocker-mac` / `ntfs-3g-mac` (plus mbedtls@3 symlink note when needed).

### Notes

- Does not bundle `dislocker`, macFUSE/FUSE-T, or `ntfs-3g`; those remain system dependencies.
- Writable Finder access on macOS requires a writable NTFS stack (`ntfs-3g` + FUSE), not dislocker alone.
