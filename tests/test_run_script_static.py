"""
Static assertions on ``run.sh``.

Overall purpose:
  Guard the supported launcher invariant: mounts need an already-root process
  on macOS (TCC blocks the unprivileged osascript path), so ``run.sh`` must
  refuse non-root invocation. Also document the checkout-as-root ownership
  model and refuse world/group-writable trees.

Requirements:
  pytest, subprocess with bash. Reads the script as text; executes only the
  portable ``find`` expressions (BSD + GNU compatible flags) against temp
  trees — never executes run.sh itself.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "run.sh"


def test_run_sh_exists_and_has_shebang() -> None:
    """run.sh is present with a bash shebang."""
    assert _SCRIPT.is_file()
    assert _SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")


def test_run_sh_requires_root() -> None:
    """run.sh exits non-zero unless id -u is 0."""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert 'id -u"' in body or "id -u)" in body
    assert re.search(r'\$\(id -u\)"\s+-ne\s+0', body) or re.search(
        r'\[ "\$\(id -u\)" -ne 0 \]', body
    )
    assert "sudo" in body
    assert "exit 1" in body


def test_run_sh_refuses_group_world_writable_tree() -> None:
    """Before exec, refuse group-/world-writable launcher or src paths."""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert "_check_not_group_world_writable" in body
    assert "stat -f '%OLp'" in body
    assert "& 022" in body
    assert "Ownership model" in body
    assert "-maxdepth" not in body


def test_run_sh_scans_src_recursively_for_writable_modules() -> None:
    """Root launch must catch a writable .py nested under src/ (not just src/)."""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert 'find "$ROOT/src"' in body
    assert "*.py" in body
    assert "-perm -020" in body
    assert "-perm -002" in body


def test_run_sh_symlink_and_filter_hardening() -> None:
    """Symlink dirs/importable names refused; self-filter is fixed-string."""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert "-type l" in body
    # Fixed-string grep: a regex metacharacter in $ROOT must not discard findings.
    assert "grep -v -xF" in body
    # Symlinked dirs are refused (never descended into); importable link names
    # are refused outright (target mode bits prove nothing: attacker-owned
    # 0644 or replaceable via a writable parent).
    assert '-d "$_link"' in body
    assert "*.py | *.pyc | *.so" in body
    assert "stat -L" not in body
    # Unresolvable entries fail closed instead of skipping a split filename.
    assert "refusing unreadable path" in body


def test_run_sh_drops_cwd_before_exec() -> None:
    """`python3 -m` puts cwd on sys.path ahead of PYTHONPATH; run.sh must cd."""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert 'cd "$ROOT"' in body
    # The cd must precede the root exec it protects.
    assert body.index('cd "$ROOT"') < body.index("exec python3 -m dislocker_ui")


def test_run_sh_gates_top_level_importable_files() -> None:
    """$ROOT/*.py is importable via cwd; gate it outside the src/ scan."""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert '"$ROOT"/*.py' in body
    assert '"$ROOT"/*.pyc' in body
    assert '"$ROOT"/*.so' in body
    assert '[ -L "$_top" ]' in body
    assert "non-plain-file Python module" in body


# Verbatim mirror of the run.sh recursive-find expression (run.sh scans for
# writable modules). Flags used here (-prune, -o, -type, -name, -perm -020)
# are BSD + GNU compatible. Keep in sync with run.sh when it changes.
_FIND_MODULES = (
    'find "{root}/src" '
    r"\( -name '.git' -o -name '.hg' -o -name '.pytest_cache' -o -name '.tox' "
    r"-o -name '.eggs' -o -name 'build' -o -name 'dist' -o -name 'tmp' "
    r"-o -name '*.egg-info' \) -prune "
    r"-o \( -type d \( -perm -020 -o -perm -002 \) -print \) "
    r"-o \( -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.so' \) "
    r"\( -perm -020 -o -perm -002 \) -print \)"
)
_FIND_LINKS = (
    'find "{root}/src" '
    r"\( -name '.git' -o -name '.hg' -o -name '.pytest_cache' -o -name '.tox' "
    r"-o -name '.eggs' -o -name 'build' -o -name 'dist' -o -name 'tmp' "
    r"-o -name '*.egg-info' \) -prune -o \( -type l -print \)"
)


def test_run_sh_find_flags_nested_writable_module(tmp_path: Path) -> None:
    """Behavioral: the scan expression flags a writable .py under 755 dirs."""
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    bad = src / "evil.py"
    bad.write_text("x", encoding="utf-8")
    bad.chmod(0o664)
    good = src / "good.py"
    good.write_text("x", encoding="utf-8")
    good.chmod(0o644)
    proc = subprocess.run(
        ["bash", "-c", _FIND_MODULES.format(root=tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "evil.py" in proc.stdout
    assert "good.py" not in proc.stdout


def test_run_sh_find_lists_symlinks_for_classification(tmp_path: Path) -> None:
    """Behavioral: the link scan surfaces symlinks for the run.sh loop."""
    src = tmp_path / "src"
    src.mkdir()
    target = tmp_path / "target.py"
    target.write_text("x", encoding="utf-8")
    target.chmod(0o644)
    link = src / "mod.py"
    link.symlink_to(target)
    proc = subprocess.run(
        ["bash", "-c", _FIND_LINKS.format(root=tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "mod.py" in proc.stdout
