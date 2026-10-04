"""Fail-closed coverage for the elevation writable-module scan."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from dislocker_ui.elevate import _assert_no_writable_py_files
from dislocker_ui.runner import RunnerError


def _tree(tmp_path: Path) -> Path:
    """Build a small private source tree with one package."""
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "README.txt").write_text("docs")
    for path in (src, pkg):
        path.chmod(0o755)
    return src


def test_scan_rejects_directory_walk_failed_to_classify(tmp_path: Path) -> None:
    """A directory filed under filenames (is_dir failed) blocks elevation."""
    src = _tree(tmp_path)
    hidden = src / "pkg" / "hidden"
    hidden.mkdir(mode=0o755)
    real_walk = os.walk

    def _walk(top: str, **kwargs: object):
        for dirpath, dirnames, filenames in real_walk(top, **kwargs):
            if "hidden" in dirnames:
                dirnames.remove("hidden")
                filenames.append("hidden")
            yield dirpath, dirnames, filenames

    with (
        patch("dislocker_ui.elevate.os.walk", _walk),
        pytest.raises(RunnerError, match="directory was not scanned"),
    ):
        _assert_no_writable_py_files(src)


def test_scan_rejects_unstatable_non_module_entry(tmp_path: Path) -> None:
    """An lstat failure on a non-module entry fails closed."""
    src = _tree(tmp_path)
    real_lstat = os.lstat

    def _lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if str(path).endswith("README.txt"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch("dislocker_ui.elevate.os.lstat", _lstat),
        pytest.raises(RunnerError, match="cannot inspect"),
    ):
        _assert_no_writable_py_files(src)


def test_scan_accepts_plain_non_module_files(tmp_path: Path) -> None:
    """Ordinary non-module files do not block elevation."""
    _assert_no_writable_py_files(_tree(tmp_path))
