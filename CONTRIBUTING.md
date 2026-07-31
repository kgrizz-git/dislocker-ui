# Contribution guide

Thanks for your interest in **dislocker-ui** — a small macOS GUI wrapper around
an already-installed `dislocker` toolchain.

## How to contribute today

**Issues are welcome** from anyone: bug reports, feature ideas, and questions.
Use the issue templates when they fit. Never paste BitLocker passwords, recovery
keys, or `.bek` files.

**Pull requests** may be limited to collaborators while the maintainer keeps
public contribution surface area small. If you are not a collaborator, open an
Issue with a clear proposal or patch description; the maintainer can follow up
or invite a PR when ready.

Security reports: see [`SECURITY.md`](SECURITY.md) (private advisory preferred).

## Local development

Requirements: macOS, Python 3.10+, an installed `dislocker` stack if you want to
exercise mounts (the unit tests do not need a BitLocker volume).

```bash
cd /path/to/dislocker-ui
pip install -e ".[dev]"
pre-commit install -t pre-commit -t pre-push

python3 -m compileall -q src
ruff check src tests hooks
ruff format --check src tests hooks
pytest --cov --cov-report=term-missing
python3 hooks/check_absolute_paths.py
python3 hooks/check_file_size.py
```

Run the GUI:

```bash
PYTHONPATH=src python3 -m dislocker_ui
```

## Project rules (please respect)

- **Runtime stays stdlib-only** unless the maintainer explicitly agrees to a new
  dependency (`tkinter` + subprocess orchestration).
- Do **not** vendor or rewrite `dislocker` C sources, or bundle FUSE / ntfs-3g.
- Default mounts are **read-only**; RW only when `ntfs-3g` is present.
- Never commit secrets, machine home paths (`/Users/<you>/…`), or session dumps.
- Update `VERSION` + `CHANGELOG.md` / `CHANGELOG.dev.md` for user-visible or
  harness changes (semver). License is **GPL-3.0-or-later**.

More agent/maintainer conventions: [`AGENTS.md`](AGENTS.md).

## Opening a pull request (when allowed)

1. Branch from `main`.
2. Keep the change focused; match existing module boundaries (`deps`, `disks`,
   `session`, `runner`, `gui`).
3. Ensure CI hooks/tests pass locally when practical.
4. Fill out the PR template. Request `@coderabbitai review` only if you want an
   automated review (auto-review is disabled for this repo).

## Enabling broader PR access later

Before opening PRs to the public, the maintainer intends to keep:

- Branch protection on `main` with required CI checks
- Secret scanning + push protection
- CODEOWNERS / review expectations as needed
- No secrets in fixtures or sample logs

That does not guarantee security of third-party PRs; treat external patches as
untrusted until reviewed.
