# AGENTS.md

Last reviewed: 2026-07-31

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
| `deps.py` | Locate binaries; ntfs-3g required for NTFS only (FAT/ExFAT use system helpers) |
| `disks.py` | List candidate disk devices (`diskutil`) |
| `fs_probe.py` | Classify decrypted image (NTFS / FAT / ExFAT) |
| `fat_mount.py` | mount_msdos / mount_exfat for BitLocker To Go |
| `session.py` | Persist last mount session for clean unmount |
| `runner.py` | Mount/unmount facade (elevate or in-process) |
| `elevate.py` | osascript admin prompt + request file protocol |
| `privileged.py` | Root child: validate request, run pipeline |
| `ntfs_mount.py` | ntfs-3g options / ownership helpers |
| `gui.py` | tkinter UI |
| `__main__.py` | Entry point |

Prefer extending these modules over adding a second parallel flow.

## Plans, TO_DO, and changelogs

| File / folder | Purpose |
|---------------|---------|
| [`TO_DO.md`](TO_DO.md) | **Open work only.** Delete items when done; do not keep completed checkboxes. |
| [`plans/`](plans/) | Active implementation plans (tracked). Index: [`plans/README.md`](plans/README.md). |
| [`plans/archive/`](plans/archive/) | Finished or abandoned plans (history). |
| [`CHANGELOG.md`](CHANGELOG.md) | User-visible product changes; bump `VERSION` (semver) with it. |
| [`CHANGELOG.dev.md`](CHANGELOG.dev.md) | Harness / CI / docs-only notes (same version lineage). |
| `.context/` | Gitignored scratch only — not a substitute for tracked plans. |

Workflow for agents:

1. Multi-step work → write/update a plan under `plans/` and mirror open items in `TO_DO.md`.
2. Finish an item → **remove it from `TO_DO.md`** (and check it off in the plan if useful).
3. Finish a whole plan → move `plans/<name>.md` to `plans/archive/` (prefer `YYYY-MM-DD-<slug>.md`), clear its TO_DO section, update the right changelog.
4. Never commit secrets, recovery keys, or machine-specific home paths in plans or TO_DO.

## Conventions

- Python 3.10+, stdlib only for runtime unless the user explicitly adds a dependency.
  Optional `[dev]` tools (`ruff`, `pre-commit`) may be installed via `pyproject.toml`.
- Keep UI thin; put subprocess and path logic in non-GUI modules.
- Default mounts to **read-only**; only offer RW when `ntfs-3g` is detected.
- Do not log passwords or recovery keys.
- Update `VERSION` + `CHANGELOG.md` for user-visible changes (semver).
- Put harness / CI / docs-only notes in `CHANGELOG.dev.md`.
- License: **GPL-3.0-or-later** (see `LICENSE`). Do not reintroduce “personal use
  only” license language. Dislocker / FUSE / ntfs-3g stay separate dependencies.
- Put throwaway files under `tmp/` (gitignored). Put one-off test scripts under `tests/`.
- Put disposable agent scratch under `.context/` (gitignored); tracked plans belong in `plans/`.

## Commands

```bash
sudo ./run.sh
# or: sudo env PYTHONPATH=src python3 -m dislocker_ui
python3 -m compileall -q src
ruff check src tests hooks
ruff format --check src tests hooks
pytest --cov --cov-report=term-missing
python3 hooks/check_absolute_paths.py
python3 hooks/check_file_size.py
pip-audit
# Optional local Semgrep (matches CI rulesets):
#   docker run --rm -v "$PWD:/src" -w /src semgrep/semgrep \
#     semgrep scan --config p/owasp-top-ten --config p/python --error --metrics=off
```

Optional local hooks (developer machine):

```bash
pip install -e ".[dev]"   # or: pip install pre-commit ruff pytest pytest-cov pip-audit
pre-commit install -t pre-commit -t pre-push
pre-commit run --all-files
```

Never commit machine-specific home paths (literal `/Users/<you>/…`,
`/home/<you>/…`, or sensitive tilde paths under Desktop/Documents/Downloads).
Prefer `$HOME`, `Path.home()`, or repo-relative paths.
Suppress a single line with `absolute-path-allow`, or list a file in
`.absolute-paths-allowlist`.

Source files soft-cap at **600** lines and hard-cap at **750**
(`hooks/check_file_size.py`). Cyclomatic complexity soft-gated by Ruff `C901`
(max 12). Coverage fail-under is **80%** (omit `runner` / `__main__`; GUI covered).
CI runs GUI tests under Xvfb (`xvfb-run`) on Linux.

SonarCloud: prefer **Automatic Analysis** (GitHub App; no `SONAR_TOKEN`).
Optional scope tweaks live in `.sonarcloud.properties`. Do not add a CI-based
Sonar scan while Automatic Analysis is on.

CodeRabbit automatic PR reviews are **off** (`.coderabbit.yaml`). Request a
review with `@coderabbitai review` when wanted.

Public contribution posture: Issues welcome; PRs may be limited to collaborators
(see `CONTRIBUTING.md` / `SECURITY.md`). Do not file public security issues.

## Security / privileges

Mounting BitLocker volumes needs elevated rights to open `/dev/disk*`. On Darwin
launch with ``sudo ./run.sh`` so the GUI is already root and runs the mount
pipeline in-process (macOS TCC blocks the unprivileged osascript-elevated path
from opening removable disks). Do not weaken trust checks by copying volume
contents into world-readable temp files unless the user asks for a decrypt-to-file
feature. Never commit passwords, recovery keys, `.bek` files, or session dumps
with secrets. BitLocker secrets may still appear briefly on `dislocker-fuse`
argv (documented residual risk).

## Paseo tools for subagent reviews

When the user requests reviews with specific models (e.g., "opencode longcat 2.0 free" or "opencode mimo v2.5 free"), check if paseo is available first:

```bash
# Check if paseo is available
if command -v paseo &> /dev/null; then
    echo "paseo is available"
else
    echo "paseo is not available, use standard run_subagent tool"
fi
```

If paseo is available, use the paseo CLI instead of the standard `run_subagent` tool:

```bash
# List available providers
paseo provider ls

# List models for a specific provider
paseo provider models opencode

# Create an agent with a specific model
# Note: Use --mode plan for read-only reviews (the default mode is not read-only).
# Write-enabled modes allow file edits and should only be used against trusted checkouts with explicit approval.
paseo run --provider opencode --model opencode/longcat-2.0-free --mode plan "<review prompt>"

# Check agent logs after completion
paseo logs <agent-id>
```

Common opencode models:
- `opencode/longcat-2.0-free` - LongCat 2.0 Free (good for reviews)
- `opencode/mimo-v2.5` - MiMo V2.5 (good for reviews)
- `opencode/mimo-v2.5-pro` - MiMo V2.5 Pro (enhanced version)

If paseo is not available (e.g., in a cloned environment without paseo installed), fall back to the standard `run_subagent` tool with `subagent_explore` or `subagent_general` profiles, noting that specific model selection is not available through the standard tool.

The paseo CLI provides access to many more models and providers than the standard `run_subagent` tool, which only supports the `subagent_explore` and `subagent_general` profiles with default models.
