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

Lint, test, and hook commands are under [Developer checks](#developer-checks).

Run the GUI (unprivileged runs open the UI but cannot complete a real
mount; use `sudo ./run.sh` for that):

```bash
PYTHONPATH=src python3 -m dislocker_ui
```

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
assets/             # app icon (README + launcher Finder icon)
dislocker-ui.command  # double-clickable launcher (Finder → Terminal → sudo)
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
python3 -m compileall -q src
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
