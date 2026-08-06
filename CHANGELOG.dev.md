# Developer changelog

Internal / harness changes that are not user-facing product notes.
See `CHANGELOG.md` for mount/UI behavior users care about.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers match `VERSION` / SemVer with the public changelog.

## [0.4.1] - 2026-08-05

### Fixed

- GUI `_raise_root_window` pulses `-topmost` on first launch; covered in
  `test_gui.py`.
- FAT/ExFAT `-m 700` (not `077`); `test_fs_mount` asserts the mode. `077` is a
  permission *mode* on `mount_msdos`, not a umask — owner had no access.

### Changed

- `run.sh` exits non-zero unless `id -u` is 0; static test covers the guard.
  README / AGENTS document `sudo ./run.sh` as the supported launcher.

## [0.4.0] - 2026-08-05

### Added

- `fs_probe.py` / `fat_mount.py` and `tests/test_fs_mount.py` for post-decrypt
  FAT/ExFAT routing. Archived root-deps installer plan under `plans/archive/`.

## [Unreleased]

### Security

- Completed a static audit of the privileged mount workflow. The timestamped,
  gitignored report is under `tmp/`; it identifies five issues for follow-up.

### Changed

- Extracted privileged staging/session policy from `runner.py` and tightened
  the source-file hard cap from 800 to 750 lines.

## [0.3.1] - 2026-08-05

### Added

- `scripts/install-root-deps.sh` and `tests/test_install_script_static.py`
  (static, macOS-independent assertions on the installer). The installer is
  covered by the manual macOS matrix, not unit tests (it runs as root).

### Notes

- Pinned upstream commit SHAs the installer builds:
  - dislocker (`master`): `38dab03175cb5798d625375154e716665201bae1`
  - ntfs-3g (`2026.7.7` / edge lineage):
    `d327833ec1d5eb1358b6f2c37139f10a3460944d`
    (not `master` tip — that branch lacks Darwin xattr `position` and fails
    against macFUSE fuse2; matches Homebrew `ntfs-3g-mac`)
- dislocker `master` requires the fuse3 API; macFUSE ships it (and libfuse2)
  in `/usr/local/lib` since 4.10, so the script seeds
  `PKG_CONFIG_PATH=/usr/local/lib/pkgconfig` and gates on
  `pkg-config --exists fuse3`.
- dislocker sets its own install RPATH, so the script passes no
  `-DCMAKE_INSTALL_RPATH`. Plan: `plans/2026-08-05-root-deps-install.md`.
- Real-hardware findings (macFUSE 5.3.3, Apple Silicon):
  - macFUSE is a kext, not a System Extension — `systemextensionsctl` never
    lists it. Detection uses `macfuse_installed()` (bundle dir / pkg-config),
    not kext loadedness; kext approval is a first-mount concern.
  - macFUSE ships libfuse into a user-owned `/usr/local/lib` even on Apple
    Silicon, so phase 2 vendors libfuse root-owned into `$PREFIX/lib`
    (`install_name_tool` + ad-hoc `codesign`) to keep the dyld closure
    root-managed. Only `MFMount.framework` (root-owned under
    `/Library/Filesystems/macfuse.fs`) is left in place.
  - First end-to-end install failed compiling `dislocker-fuse.c` against
    fuse3 3.18.2 (`fuse_darwin_attr` vs `struct stat`). Fixed by passing
    `-DCMAKE_C_FLAGS=-DFUSE_DARWIN_ENABLE_EXTENSIONS=0` (macFUSE#1064;
    same approach as nixpkgs). Static test asserts the flag.
  - After that, stage-install failed creating `/opt/local/lib` as the
    unprivileged user: dislocker bakes absolute `libdir`/`bindir` into
    `install()` rules, so `cmake --install --prefix $STAGEDIR$PREFIX` does
    not remap them. Switched to `DESTDIR=$STAGEDIR cmake --install` (same
    as ntfs-3g; matches dislocker's own `$ENV{DESTDIR}` symlink install).
  - ntfs-3g at master tip then failed on Darwin fuse2 xattr signatures
    (`getxattr`/`setxattr` need `uint32_t position`). Bumped pin to
    `2026.7.7` (`d327833…`), which has the Darwin wrappers.
  - Build then succeeded through vendor + otool, but
    `discover_privileged_deps().core_ok` failed missing `ntfs-3g`:
    with `--exec-prefix` set, ntfs-3g installs via `rootbindir=$(bindir)`
    (default `$PREFIX/bin`). Added `--bindir=$PREFIX/sbin`.
  - First successful install still failed at mount: dyld could not load
    `@rpath/libdislocker.0.7.dylib` because phase2's `find -type f` skipped
    cmake's versioned dylib symlinks, and `verify_dyld_closure` ignored
    `@rpath` / `LC_RPATH`. Added `install_staged_lib_symlinks`,
    `sanitize_rpaths` (drop Homebrew mbedtls rpath), and tightened the
    otool gate to require `@rpath` names under `$PREFIX/lib` and only
    `$PREFIX/lib` as LC_RPATH.

## [0.2.0] - 2026-07-31

### Added

- Elevation modules and tests: `elevate.py`, `privileged.py`, `ntfs_mount.py`,
  plus focused suites (`test_elevate`, `test_privileged`, `test_runner_elevation`,
  `test_runner_ntfs`).
- Archived plan: `plans/archive/2026-07-31-macos-elevation.md`.

### Changed

- Coverage includes elevate/privileged/ntfs_mount (runner still omitted).
- `.gitignore` ignores `.superpowers/` SDD scratch.
- Elevation uses `elevation_transaction()` (per-user flock + thread lock) around
  prepare+run; overlapping-elevation regression covered in `test_elevate`.
- Overlapping-elevation test records thread observations without assert-in-try
  (Sonar `python:S5779` / reliability gate).

## [0.1.6] - 2026-07-31

### Added

- `tests/test_gui.py`: headless tkinter coverage for deps banner, RW gating,
  unlock-method / BEK browse UI, volume resolution, Mount/Unmount success and
  error paths, busy guards, and `run_app` startup.

### Changed

- Coverage `fail_under` **70 → 80**; stop omitting `gui.py` (still omit
  `runner.py` and `__main__.py`).
- CI pytest step runs under `xvfb-run` so headless Linux can exercise tkinter.
- Keep 0.1.6 notes in this file only (no user-facing `CHANGELOG.md` section).
- Parametrize Mount/Unmount error-path GUI tests (`RunnerError` vs unexpected).

## [0.1.5] - 2026-07-31

### Added

- `SECURITY.md`, `CONTRIBUTING.md`, `.github/CODEOWNERS`, `.github/dependabot.yml`
  (pip + GitHub Actions weekly, `cooldown.default-days: 7`), issue template
  `config.yml` with security / contributing contact links.

## [0.1.4] - 2026-07-31

### Added

- `.coderabbit.yaml` with `reviews.auto_review.enabled: false` (manual
  `@coderabbitai review` still works).

### Changed

- `runner.py`: FUSE temp dirs via `tempfile.mkdtemp()` (no `dir="/tmp"`);
  extract mount validation / NTFS mount / unmount helpers for Sonar S3776.

## [0.1.3] - 2026-07-30

### Added

- pytest suite: `tests/test_session.py`, `tests/test_deps.py`, `tests/test_disks.py`.
- Ruff mccabe complexity (`C90`, max 12).
- `hooks/check_file_size.py` on pre-commit and pre-push (soft 600 / hard **800**).
- CI: pytest + coverage (`fail_under=70`, omit gui/runner/`__main__`), `pip-audit`,
  Semgrep (`p/owasp-top-ten` + `p/python`); `.semgrepignore` for scratch/caches.
- SonarCloud Automatic Analysis config via `.sonarcloud.properties` (no CI token;
  enable Automatic Analysis in the SonarCloud project UI after importing the repo).
- Dev extras: `pytest`, `pytest-cov`, `pip-audit`.
- Unit tests for `hooks/check_file_size.py`.

### Changed

- Pin `actions/checkout` and `actions/setup-python` to full commit SHAs in CI
  (Semgrep `github-actions-mutable-action-tag`).
- Pin the Semgrep CI container image by digest (`semgrep/semgrep@sha256:…` /
  1.170.0).

### Fixed

- CodeRabbit follow-ups: `persist-credentials: false` on CI checkouts; drop
  unsupported Automatic Analysis wildcard exclusions; `.toml` uses source
  line-caps; changelog test paths.
- Split composite assert in `tests/test_check_file_size.py` (Sonar `python:S9073`).

## [0.1.2] - 2026-07-30

### Changed

- Project license set to GPL-3.0-or-later (`LICENSE`, README, `pyproject.toml`,
  package header). Replaced vague “personal use” wording.

### Fixed

- Add `tests/.gitkeep` so the empty tests tree exists in CI for Ruff path args.
- `pyproject.toml`: PEP 639 SPDX `license` string + `license-files`, require
  `setuptools>=77` (CodeRabbit nit).

## [0.1.1] - 2026-07-30

### Added

- Slim repo harness from template-repo-v1 ideas: richer `.gitignore`, `.context/` scratch convention.
- `.pre-commit-config.yaml` with gitleaks, pre-commit-hooks hygiene, shellcheck, Ruff,
  and absolute home-path checks on **pre-commit** and **pre-push**.
- `hooks/check_absolute_paths.py` (+ `.absolute-paths-allowlist`) to block `/Users/<name>`,
  `/home/<name>`, Windows user profiles, and sensitive `~/…` paths.
- `pyproject.toml` with packaging metadata and Ruff config; optional `[dev]` extras (`ruff`, `pre-commit`).
- GitHub Actions CI: `compileall` + Ruff lint/format + absolute-path check + gitleaks binary scan.
- PR and issue templates under `.github/`.
- Dual-track developer changelog (`CHANGELOG.dev.md`).

### Fixed

- GUI: bind exception text into `after()` callbacks so error dialogs work after the
  `except` block clears the exception name (Python 3 semantics).

### Notes

- Runtime remains Python stdlib only; pre-commit and Ruff are optional developer tooling.
- PHI/medical gates, CodeGuard dumps, and full policy/inventory trees were intentionally not copied.
