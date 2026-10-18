"""
Static and behavioral assertions on ``dislocker-ui.command``.

Overall purpose:
  Guard the double-clickable launcher invariant: the wrapper stays
  unprivileged (Finder/Terminal launch context) and defers every root-side
  check to ``run.sh``, which re-validates everything once it runs as root.
  The 2026-10-17 TCC experiments (``plans/double-clickable-app.md``)
  measured that a root child spawned by an osascript admin prompt cannot
  open removable ``/dev/disk*`` nodes — even with Full Disk Access granted
  to the .app — while sudo in Terminal opens them all, so the ``.command``
  is the only viable double-click path (severed descent is the working
  hypothesis for that differential). The wrapper resolves its own real
  path first: a symlink named like the launcher plus a planted executable
  ``run.sh`` must never reach sudo.

Requirements:
  pytest, subprocess with bash. ``bash -n`` gates syntax and shellcheck
  runs when installed (CI never runs the pre-commit hook). Guard behavior
  is exercised ONLY from temp copies where ``run.sh`` is absent or a
  non-executable 0644 stub — never where a real executable ``run.sh``
  exists: CI runners have passwordless sudo and would really exec the
  root launcher. Every wrapper invocation uses a dedicated empty cwd
  (never the repo, which has an executable ``run.sh``).
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_COMMAND = _REPO / "dislocker-ui.command"
_GUARD_PREFIX = "dislocker-ui: "
_GUARD_SUFFIX = "/run.sh is missing or not executable"


def test_command_exists_with_bash_shebang() -> None:
    """The launcher is present with a bash shebang Finder/Terminal can run."""
    assert _COMMAND.is_file()
    assert _COMMAND.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")


def test_command_passes_bash_syntax_gate() -> None:
    """``bash -n`` accepts the wrapper (CI runs no pre-commit hooks)."""
    proc = subprocess.run(
        ["bash", "-n", str(_COMMAND)], check=False, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_command_passes_shellcheck() -> None:
    """Direct shellcheck gate (the hook may not tag a .command extension)."""
    proc = subprocess.run(
        ["shellcheck", "--shell=bash", str(_COMMAND)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_command_resolves_real_path_before_root() -> None:
    """Symlinks are resolved fail-closed (planted run.sh must not reach sudo)."""
    body = _COMMAND.read_text(encoding="utf-8")
    assert 'SELF="$0"' in body
    assert '/usr/bin/readlink -f "$SELF"' in body
    # Fail closed: an unresolvable path refuses to run rather than
    # falling back to $0 (which would reopen the symlink attack).
    assert "cannot resolve $SELF" in body
    assert 'ROOT="$(cd "$(/usr/bin/dirname "$SELF")" && pwd)"' in body
    assert 'exec /usr/bin/sudo "$ROOT/run.sh" "$@"' in body
    assert 'exec "$ROOT/run.sh" "$@"' in body


def _empty_cwd(tmp_path: Path) -> Path:
    """A dedicated empty cwd so a cwd-based ROOT regression stays safe."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    return cwd


def _run_wrapper_without_real_run_sh(
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run a temp copy of the wrapper in a dir with no executable run.sh."""
    copy = tmp_path / "dislocker-ui.command"
    copy.write_text(_COMMAND.read_text(encoding="utf-8"), encoding="utf-8")
    return subprocess.run(
        ["bash", str(copy)],
        cwd=_empty_cwd(tmp_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _assert_guard_message(proc: subprocess.CompletedProcess[str], tmp_path: Path) -> None:
    """The diagnostic names this temp dir — proof ROOT came from $0, not cwd."""
    stderr = proc.stderr.strip()
    assert proc.returncode == 1, stderr
    assert stderr.startswith(_GUARD_PREFIX), stderr
    assert stderr.endswith(_GUARD_SUFFIX), stderr
    assert f"{tmp_path.resolve()}/run.sh" in stderr, stderr


def test_command_guards_missing_run_sh(tmp_path: Path) -> None:
    """Behavioral: no run.sh beside the wrapper -> exit 1 with the message."""
    proc = _run_wrapper_without_real_run_sh(tmp_path)
    _assert_guard_message(proc, tmp_path)


def test_command_guards_non_executable_run_sh(tmp_path: Path) -> None:
    """Behavioral: a 0644 run.sh stub is refused exactly like a missing one."""
    copy = tmp_path / "dislocker-ui.command"
    copy.write_text(_COMMAND.read_text(encoding="utf-8"), encoding="utf-8")
    stub = tmp_path / "run.sh"
    stub.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    stub.chmod(0o644)
    proc = subprocess.run(
        ["bash", str(copy)],
        cwd=_empty_cwd(tmp_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    _assert_guard_message(proc, tmp_path)


def test_command_through_symlink_never_runs_planted_run_sh(tmp_path: Path) -> None:
    """Behavioral: a symlink to the wrapper plus a planted executable
    run.sh must not reach sudo — resolution follows the link, the guard
    fires against the real directory, and the planted script never runs."""
    real = tmp_path / "real"
    real.mkdir()
    copy = real / "dislocker-ui.command"
    copy.write_text(_COMMAND.read_text(encoding="utf-8"), encoding="utf-8")

    planted_dir = tmp_path / "planted"
    planted_dir.mkdir()
    link = planted_dir / "dislocker-ui.command"
    link.symlink_to(copy)
    marker = planted_dir / "planted-ran"
    planted = planted_dir / "run.sh"
    planted.write_text(f"#!/usr/bin/env bash\ntouch {shlex.quote(str(marker))}\n", encoding="utf-8")
    planted.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(link)],
        cwd=_empty_cwd(tmp_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    stderr = proc.stderr.strip()
    assert proc.returncode == 1, stderr
    assert f"{real.resolve()}/run.sh" in stderr, stderr
    assert not marker.exists(), "the planted run.sh ran"


def test_command_exec_bit_is_committed() -> None:
    """git records mode 100755 (Finder will not run a non-executable .command)."""
    assert _COMMAND.stat().st_mode & 0o111, "launcher not executable in the working tree"
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    proc = subprocess.run(
        ["git", "ls-files", "-s", "dislocker-ui.command"],
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.skip("not a git checkout, or the launcher is not tracked yet")
    assert proc.stdout.split()[0] == "100755", proc.stdout
