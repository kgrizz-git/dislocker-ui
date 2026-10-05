"""Fail-closed coverage for the elevation writable-module scan."""

from __future__ import annotations

import os
import shlex
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


def test_scan_skips_vanished_non_symlink_module(tmp_path: Path) -> None:
    """A regular .py deleted mid-scan cannot hide a module; it is skipped."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    mod = pkg / "mod.py"
    mod.write_text("", encoding="utf-8")
    real_stat = Path.stat

    def _stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if str(self) == str(mod):
            raise FileNotFoundError(2, "No such file or directory", str(mod))
        return real_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    with patch("pathlib.Path.stat", _stat):
        _assert_no_writable_py_files(pkg)


def test_scan_hints_quote_paths_with_spaces(tmp_path: Path) -> None:
    """Ownership hints shell-quote paths so they stay copy-pasteable."""
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "dir with space" / "pkg"
    pkg.mkdir(parents=True)
    bad = pkg / "mod.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o664)
    with pytest.raises(RunnerError, match="writable") as excinfo:
        _assert_no_writable_py_files(pkg)
    assert shlex.quote(str(bad)) in str(excinfo.value)


def test_assert_no_writable_py_files_ok(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ok = pkg / "mod.py"
    ok.write_text("", encoding="utf-8")
    ok.chmod(0o644)
    _assert_no_writable_py_files(pkg)


def test_assert_no_writable_py_files_group_writable(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    bad = pkg / "mod.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o664)
    with pytest.raises(RunnerError, match="writable"):
        _assert_no_writable_py_files(pkg)


def test_assert_no_writable_py_files_world_writable(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    bad = pkg / "mod.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o646)
    with pytest.raises(RunnerError, match="writable"):
        _assert_no_writable_py_files(pkg)


def test_assert_no_writable_py_files_skips_non_py(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    non_py = pkg / "data.txt"
    non_py.write_text("", encoding="utf-8")
    non_py.chmod(0o666)
    _assert_no_writable_py_files(pkg)


def test_assert_no_writable_py_files_error_includes_path(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    bad = pkg / "mod.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o664)
    with pytest.raises(RunnerError, match=str(bad)):
        _assert_no_writable_py_files(pkg)


def test_assert_no_writable_py_files_scans_subdirectories(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "src"
    sub = root / "dislocker_ui"
    sub.mkdir(parents=True)
    bad = sub / "mod.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o664)
    with pytest.raises(RunnerError, match="writable"):
        _assert_no_writable_py_files(root)


def test_assert_no_writable_py_files_unreadable_dir_raises(tmp_path: Path) -> None:
    """An unreadable subdirectory fails the scan instead of being skipped."""
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "pkg"
    locked = pkg / "locked"
    locked.mkdir(parents=True)
    real_scandir = os.scandir

    def _scandir(path: object, *args: object, **kwargs: object) -> object:
        if str(path) == str(locked):
            raise PermissionError(13, "Permission denied", str(locked))
        return real_scandir(path, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch("os.scandir", side_effect=_scandir),
        pytest.raises(RunnerError, match="locked") as excinfo,
    ):
        _assert_no_writable_py_files(pkg)
    assert "chown" in str(excinfo.value)


def test_assert_no_writable_py_files_stat_error_raises(tmp_path: Path) -> None:
    """Unstattable files and dirs fail the scan instead of being skipped."""
    from dislocker_ui.elevate import _check_dir_writable, _check_files_writable

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    mod = pkg / "mod.py"
    mod.write_text("", encoding="utf-8")
    real_stat = Path.stat

    def _stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if str(self) == str(mod):
            raise OSError("cannot stat")
        return real_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch("pathlib.Path.stat", _stat),
        pytest.raises(RunnerError, match=r"mod\.py") as excinfo,
    ):
        _check_files_writable(pkg, ["mod.py"])
    assert "chown" in str(excinfo.value)
    with (
        patch("pathlib.Path.stat", side_effect=OSError("cannot stat")),
        pytest.raises(RunnerError, match="pkg"),
    ):
        _check_dir_writable(pkg, tmp_path)


def test_assert_no_writable_py_files_pruned_dirs_skipped_even_unreadable(
    tmp_path: Path,
) -> None:
    """Pruned cache dirs are never descended into, unreadable or not."""
    from dislocker_ui.elevate import _assert_no_writable_py_files

    pkg = tmp_path / "pkg"
    cache = pkg / ".git"
    cache.mkdir(parents=True)
    real_scandir = os.scandir

    def _scandir(path: object, *args: object, **kwargs: object) -> object:
        if str(path) == str(cache):
            raise AssertionError("pruned dir must not be scanned")
        return real_scandir(path, *args, **kwargs)  # type: ignore[arg-type]

    with patch("os.scandir", side_effect=_scandir):
        _assert_no_writable_py_files(pkg)


@pytest.mark.parametrize("dirname", ["site-packages", "dist-packages"])
def test_assert_no_writable_py_files_skips_root_owned_system_managed(
    tmp_path: Path, dirname: str
) -> None:
    """Root-owned site-packages / dist-packages is exempt from the scan."""
    from unittest.mock import patch

    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / dirname
    root.mkdir()
    bad = root / "mod.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o664)
    with patch("dislocker_ui.elevate._is_system_managed_install", return_value=True):
        _assert_no_writable_py_files(root)


@pytest.mark.parametrize("dirname", ["site-packages", "dist-packages"])
def test_assert_no_writable_py_files_scans_user_owned_system_named_dir(
    tmp_path: Path, dirname: str
) -> None:
    """User-owned directory merely named site-packages is NOT exempt."""
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / dirname
    root.mkdir()
    bad = root / "mod.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o664)
    with pytest.raises(RunnerError, match="writable"):
        _assert_no_writable_py_files(root)


def test_assert_no_writable_py_files_rejects_writable_directory(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "src"
    sub = root / "pkg"
    sub.mkdir(parents=True)
    sub.chmod(0o775)
    ok = sub / "mod.py"
    ok.write_text("", encoding="utf-8")
    ok.chmod(0o644)
    with pytest.raises(RunnerError, match=r"directory.*writable"):
        _assert_no_writable_py_files(root)


def test_assert_no_writable_py_files_rejects_writable_pyc(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "pkg"
    root.mkdir()
    bad = root / "mod.pyc"
    bad.write_bytes(b"\x00")
    bad.chmod(0o664)
    with pytest.raises(RunnerError, match="writable"):
        _assert_no_writable_py_files(root)


def test_assert_no_writable_py_files_rejects_writable_pycache(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "pkg"
    cache = root / "__pycache__"
    cache.mkdir(parents=True)
    cache.chmod(0o775)
    with pytest.raises(RunnerError, match="writable"):
        _assert_no_writable_py_files(root)


def test_assert_no_writable_py_files_rejects_writable_so(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "pkg"
    root.mkdir()
    bad = root / "native.so"
    bad.write_bytes(b"\x00")
    bad.chmod(0o664)
    with pytest.raises(RunnerError, match="writable"):
        _assert_no_writable_py_files(root)


def test_assert_no_writable_py_files_refuses_broken_symlink(tmp_path: Path) -> None:
    """A broken symlink (target deleted) is refused with a removal hint."""
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "pkg"
    root.mkdir()
    broken = root / "broken.py"
    broken.symlink_to(tmp_path / "no-such-file")
    ok = root / "mod.py"
    ok.write_text("", encoding="utf-8")
    ok.chmod(0o644)
    with pytest.raises(RunnerError, match="broken") as excinfo:
        _assert_no_writable_py_files(root)
    assert "Remove the broken entry" in str(excinfo.value)


@pytest.mark.parametrize(
    "dirname",
    [
        "dislocker_ui.egg-info",
        "build",
        "dist",
        ".pytest_cache",
        ".git",
        "tmp",
    ],
)
def test_assert_no_writable_py_files_skips_non_code_dirs(tmp_path: Path, dirname: str) -> None:
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "src"
    d = root / dirname
    d.mkdir(parents=True)
    d.chmod(0o775)
    ok = root / "mod.py"
    ok.write_text("", encoding="utf-8")
    ok.chmod(0o644)
    _assert_no_writable_py_files(root)


def test_assert_no_writable_py_files_prunes_excluded_dir_descendants(
    tmp_path: Path,
) -> None:
    """Writable files inside an excluded directory (.git) are not checked."""
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "src"
    git_dir = root / ".git" / "objects"
    git_dir.mkdir(parents=True)
    bad = git_dir / "bad.py"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o664)
    ok = root / "mod.py"
    ok.write_text("", encoding="utf-8")
    ok.chmod(0o644)
    _assert_no_writable_py_files(root)
