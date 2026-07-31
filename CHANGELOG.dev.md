# Developer changelog

Internal / harness changes that are not user-facing product notes.
See `CHANGELOG.md` for mount/UI behavior users care about.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers match `VERSION` / SemVer with the public changelog.

## [0.1.3] - 2026-07-30

### Added

- pytest suite: `tests/test_session.py`, `test_deps.py`, `test_disks.py`.
- Ruff mccabe complexity (`C90`, max 12).
- `hooks/check_file_size.py` on pre-commit and pre-push (soft 600 / hard **800**).
- CI: pytest + coverage (`fail_under=70`, omit gui/runner/`__main__`), `pip-audit`.
- SonarCloud Automatic Analysis config via `.sonarcloud.properties` (no CI token;
  enable Automatic Analysis in the SonarCloud project UI after importing the repo).
- Dev extras: `pytest`, `pytest-cov`, `pip-audit`.

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
