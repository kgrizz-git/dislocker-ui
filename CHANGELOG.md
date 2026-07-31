# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Developer / harness-only notes live in [`CHANGELOG.dev.md`](CHANGELOG.dev.md).

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
