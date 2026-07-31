"""
Unit tests for hooks/check_file_size.py.

Overall purpose:
  Cover override parsing, soft/hard thresholds for source vs docs, ignore
  matching, and oversized-byte handling without relying on the real git tree.

Requirements:
  pytest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_checker() -> ModuleType:
    """Load the hook module by path (hooks/ is not a Python package)."""
    path = Path(__file__).resolve().parents[1] / "hooks" / "check_file_size.py"
    spec = importlib.util.spec_from_file_location("check_file_size", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checker() -> ModuleType:
    """Provide a freshly loaded check_file_size module."""
    return _load_checker()


def test_ignored_paths(checker: ModuleType) -> None:
    """Scratch, venv, and LICENSE paths are ignored."""
    assert checker._ignored("tmp/foo.py") is True
    assert checker._ignored(".context/notes.md") is True
    assert checker._ignored(".venv/lib/x.py") is True
    assert checker._ignored("LICENSE") is True
    assert checker._ignored("src/dislocker_ui/gui.py") is False


def test_override_cap(checker: ModuleType, tmp_path: Path) -> None:
    """policy:file-size allow=N is read from the first lines of a file."""
    path = tmp_path / "big.py"
    path.write_text(
        "# policy:file-size allow=900 reason=test\n" + ("x = 1\n" * 10),
        encoding="utf-8",
    )
    assert checker._override_cap(path) == 900


def test_check_file_source_soft_and_hard(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source files warn above soft and error above hard."""
    monkeypatch.setattr(checker, "SOFT_LINE_CAP", 5)
    monkeypatch.setattr(checker, "HARD_LINE_CAP", 10)

    soft = tmp_path / "soft.py"
    soft.write_text("\n".join(f"x{i}=1" for i in range(7)) + "\n", encoding="utf-8")
    errs, warns = checker._check_file(soft, "soft.py")
    assert errs == []
    assert any("soft cap" in w for w in warns)

    hard = tmp_path / "hard.py"
    hard.write_text("\n".join(f"x{i}=1" for i in range(12)) + "\n", encoding="utf-8")
    errs, warns = checker._check_file(hard, "hard.py")
    assert any("hard cap" in e for e in errs)
    assert warns == []


def test_check_file_toml_uses_source_caps(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.toml` files use source caps, not documentation caps."""
    monkeypatch.setattr(checker, "SOFT_LINE_CAP", 3)
    monkeypatch.setattr(checker, "HARD_LINE_CAP", 5)
    monkeypatch.setattr(checker, "DOC_SOFT_LINE_CAP", 100)
    monkeypatch.setattr(checker, "DOC_HARD_LINE_CAP", 200)

    path = tmp_path / "pyproject.toml"
    path.write_text("\n".join(f"k{i}=1" for i in range(6)) + "\n", encoding="utf-8")
    errs, _warns = checker._check_file(path, "pyproject.toml")
    assert any("hard cap 5" in e for e in errs)


def test_check_file_doc_caps(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Markdown uses documentation soft/hard caps."""
    monkeypatch.setattr(checker, "DOC_SOFT_LINE_CAP", 4)
    monkeypatch.setattr(checker, "DOC_HARD_LINE_CAP", 6)

    path = tmp_path / "notes.md"
    path.write_text("\n".join(f"# h{i}" for i in range(5)) + "\n", encoding="utf-8")
    errs, warns = checker._check_file(path, "notes.md")
    assert errs == []
    assert any("soft cap" in w for w in warns)


def test_check_file_max_bytes(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files over POLICY_MAX_BYTES are hard errors."""
    monkeypatch.setattr(checker, "MAX_BYTES", 20)
    path = tmp_path / "blob.py"
    path.write_text("x" * 50, encoding="utf-8")
    errs, warns = checker._check_file(path, "blob.py")
    assert any("bytes exceeds max" in e for e in errs)
    assert warns == []


def test_override_raises_hard_cap(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allow=N raises the hard cap; soft cap still warns below that ceiling."""
    monkeypatch.setattr(checker, "SOFT_LINE_CAP", 2)
    monkeypatch.setattr(checker, "HARD_LINE_CAP", 3)
    path = tmp_path / "ok.py"
    path.write_text(
        "# policy:file-size allow=20 reason=fixture\n"
        + "\n".join(f"x{i}=1" for i in range(10))
        + "\n",
        encoding="utf-8",
    )
    errs, warns = checker._check_file(path, "ok.py")
    assert errs == []
    assert any("soft cap" in w for w in warns)
