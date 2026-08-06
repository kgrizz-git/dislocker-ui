"""
Static assertions on ``run.sh``.

Overall purpose:
  Guard the supported launcher invariant: mounts need an already-root process
  on macOS (TCC blocks the unprivileged osascript path), so ``run.sh`` must
  refuse non-root invocation.

Requirements:
  pytest. Reads the script as text; never executes it.
"""

from __future__ import annotations

import re
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "run.sh"


def test_run_sh_exists_and_has_shebang() -> None:
    assert _SCRIPT.is_file()
    assert _SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")


def test_run_sh_requires_root() -> None:
    body = _SCRIPT.read_text(encoding="utf-8")
    assert 'id -u"' in body or "id -u)" in body
    assert re.search(r'\$\(id -u\)"\s+-ne\s+0', body) or re.search(
        r'\[ "\$\(id -u\)" -ne 0 \]', body
    )
    assert "sudo" in body
    assert "exit 1" in body
