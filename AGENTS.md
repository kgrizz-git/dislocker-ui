# AGENTS.md

Guidance for coding agents working on **dislocker-ui**.

## Project purpose

Personal macOS helper GUI that wraps an **already installed** `dislocker`
(`dislocker-fuse`). It orchestrates mount/unmount; it does not reimplement
BitLocker cryptography and should not vendor dislocker sources.

## Non-goals

- Forking or rewriting dislocker C code.
- Bundling macFUSE / FUSE-T / ntfs-3g inside this repo.
- Windows or Linux GUI support (macOS-first).

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

- Python 3.10+, stdlib only unless the user explicitly adds a dependency.
- Keep UI thin; put subprocess and path logic in non-GUI modules.
- Default mounts to **read-only**; only offer RW when `ntfs-3g` is detected.
- Do not log passwords or recovery keys.
- Update `VERSION` + `CHANGELOG.md` for user-visible changes (semver).
- Put throwaway files under `tmp/` (gitignored). Put one-off test scripts under `tests/`.

## Commands

```bash
PYTHONPATH=src python3 -m dislocker_ui
python3 -m compileall -q src
```

## Security / privileges

Mounting BitLocker volumes usually needs elevated rights to open `/dev/disk*`.
Do not weaken that by copying volume contents into world-readable temp files
unless the user asks for a decrypt-to-file feature.
