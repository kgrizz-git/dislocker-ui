"""
Static assertions on ``run.sh``.

Overall purpose:
  Guard the supported launcher invariant: mounts need an already-root process
  on macOS (TCC blocks the unprivileged osascript path), so ``run.sh`` must
  refuse non-root invocation. Also document the checkout-as-root ownership
  model and refuse world/group-writable trees.

Requirements:
  pytest. Reads the script as text; never executes it.
"""

from __future__ import annotations

import re
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
