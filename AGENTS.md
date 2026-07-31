# AGENTS.md

Last reviewed: 2026-07-30

Guidance for coding agents working on **dislocker-ui**.

## Project purpose

Personal macOS helper GUI that wraps an **already installed** `dislocker`
(`dislocker-fuse`). It orchestrates mount/unmount; it does not reimplement
BitLocker cryptography and should not vendor dislocker sources.

## Non-goals

- Forking or rewriting dislocker C code.
- Bundling macFUSE / FUSE-T / ntfs-3g inside this repo.
- Windows or Linux GUI support (macOS-first).
- Copying the full [template-repo-v1](https://github.com/kgrizz-git/template-repo-v1)
  harness (policies tree, PHI gates, inventories). Prefer slim local checks.

## Architecture

| Module | Responsibility |
|--------|----------------|
| `deps.py` | Locate binaries; report RW capability |
| `disks.py` | List candidate disk devices (`diskutil`) |
| `session.py` | Persist last mount session for clean unmount |
| `runner.py` | Run mount/unmount command sequences |
| `gui.py` | tkinter UI |
| `__main__.py` | Entry point |

Prefer extending these modules over adding a second parallel flow.

## Conventions

- Python 3.10+, stdlib only for runtime unless the user explicitly adds a dependency.
  Optional `[dev]` tools (`ruff`, `pre-commit`) may be installed via `pyproject.toml`.
- Keep UI thin; put subprocess and path logic in non-GUI modules.
- Default mounts to **read-only**; only offer RW when `ntfs-3g` is detected.
- Do not log passwords or recovery keys.
- Update `VERSION` + `CHANGELOG.md` for user-visible changes (semver).
- Put harness / CI / docs-only notes in `CHANGELOG.dev.md`.
- Put throwaway files under `tmp/` (gitignored). Put one-off test scripts under `tests/`.
- Put agent scratch (plans, notes) under `.context/` (gitignored); never commit secrets there.

## Commands

```bash
PYTHONPATH=src python3 -m dislocker_ui
python3 -m compileall -q src
ruff check src tests hooks
ruff format --check src tests hooks
python3 hooks/check_absolute_paths.py
```

Optional local hooks (developer machine):

```bash
pip install -e ".[dev]"   # or: pip install pre-commit ruff
pre-commit install -t pre-commit -t pre-push
pre-commit run --all-files
```

Never commit machine-specific home paths (literal `/Users/<you>/…`,
`/home/<you>/…`, or sensitive tilde paths under Desktop/Documents/Downloads).
Prefer `$HOME`, `Path.home()`, or repo-relative paths.
Suppress a single line with `absolute-path-allow`, or list a file in
`.absolute-paths-allowlist`.

## Security / privileges

Mounting BitLocker volumes usually needs elevated rights to open `/dev/disk*`.
Do not weaken that by copying volume contents into world-readable temp files
unless the user asks for a decrypt-to-file feature.
Never commit passwords, recovery keys, `.bek` files, or session dumps with secrets.
