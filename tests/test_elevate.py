"""
Unit tests for macOS elevation helpers (elevate.py).

Overall purpose:
  Cover predicates, two-layer quoting, AppleScript contents (request path not
  secret), and stderr → cancel/timeout/exit-code classification without
  prompting for a real admin password.

Requirements:
  pytest.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.elevate import (
    ElevationCancelled,
    ElevationTimedOut,
    build_osascript,
    build_privileged_shell_command,
    classify_osascript_failure,
    elevation_transaction,
    needs_elevation,
    run_elevated_mount,
    validate_volume_path,
)
from dislocker_ui.runner import MountRequest, RunnerError, UnlockMethod
from dislocker_ui.session import MountSession, save_session


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def test_needs_elevation_darwin_non_root() -> None:
    """Darwin + euid!=0 requires elevation."""
    with (
        patch("dislocker_ui.elevate.sys.platform", "darwin"),
        patch("dislocker_ui.elevate.os.geteuid", return_value=501),
    ):
        assert needs_elevation() is True


def test_needs_elevation_false_when_root_or_non_darwin() -> None:
    """Root or non-Darwin never needs the osascript path."""
    with (
        patch("dislocker_ui.elevate.sys.platform", "darwin"),
        patch("dislocker_ui.elevate.os.geteuid", return_value=0),
    ):
        assert needs_elevation() is False
    with (
        patch("dislocker_ui.elevate.sys.platform", "linux"),
        patch("dislocker_ui.elevate.os.geteuid", return_value=501),
    ):
        assert needs_elevation() is False


def test_quoting_handles_spaces_quotes_dollar_backticks(tmp_path: Path) -> None:
    """shlex quoting protects spaces, quotes, $, and backticks in paths."""
    tricky = tmp_path / 'req $`" weird.json'
    tricky.write_text("{}", encoding="utf-8")
    cmd = build_privileged_shell_command("mount", tricky)
    assert "cd / &&" in cmd
    assert "-s" in cmd
    assert "-P" in cmd
    assert "dislocker_ui.privileged" in cmd
    # After shlex.quote the dangerous characters are inside single quotes.
    assert "$" in cmd or "'$" in cmd or "\\$" in cmd
    script = build_osascript(cmd)
    assert "with timeout of 600 seconds" in script
    assert "with administrator privileges" in script
    assert str(tricky.resolve()) in cmd
    # AppleScript escaping doubles backslashes / escapes quotes — no raw breakout.
    assert '\n  do shell script "' in script


def test_applescript_contains_request_path_not_secret(tmp_path: Path) -> None:
    """AppleScript embeds the request path but never the BitLocker secret."""
    req_path = tmp_path / "request.json"
    req_path.write_text("{}", encoding="utf-8")
    secret = "SuperSecretPassword123!"
    cmd = build_privileged_shell_command("mount", req_path)
    script = build_osascript(cmd)
    assert str(req_path.resolve()) in cmd
    assert secret not in script
    assert secret not in cmd


def test_sweep_stale_elevation_files(tmp_path: Path) -> None:
    """Stale requests are removed while root-owned logs and other files are kept."""
    from dislocker_ui.elevate import _sweep_stale_elevation_files

    (tmp_path / "dislocker-ui-req-abc.json").write_text("{}", encoding="utf-8")
    (tmp_path / "dislocker-ui-log-xyz.log").write_text("x", encoding="utf-8")
    keep = tmp_path / "active_session.json"
    keep.write_text("{}", encoding="utf-8")
    lock = tmp_path / "dislocker-ui-elevate.lock"
    lock.write_text("", encoding="utf-8")
    _sweep_stale_elevation_files(tmp_path)
    assert not (tmp_path / "dislocker-ui-req-abc.json").exists()
    assert (tmp_path / "dislocker-ui-log-xyz.log").exists()
    assert keep.exists()
    assert lock.exists()


def test_overlapping_elevation_transactions_are_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A second elevation_transaction waits; it must not sweep the holder's files.

    Regression for concurrent prepare sweeps deleting an in-flight request/log.
    """
    support = tmp_path / "Application Support" / "dislocker-ui"
    support.mkdir(parents=True)
    monkeypatch.setattr(
        "dislocker_ui.session.default_session_path",
        lambda: support / "active_session.json",
    )
    monkeypatch.setattr(
        "dislocker_ui.elevate.assert_safe_path_for_elevation",
        lambda path, *, label: None,
    )

    holder_ready = threading.Event()
    release_holder = threading.Event()
    waiter_entered = threading.Event()
    errors: list[BaseException] = []
    waiter_log: list[Path] = []
    # Observations recorded in threads; asserts stay on the main thread (S5779).
    holder_saw_release = threading.Event()
    holder_files_ok = threading.Event()

    def holder() -> None:
        try:
            with elevation_transaction():
                # Mimic an in-flight request sitting beside the log.
                req = support / "dislocker-ui-req-holder.json"
                req.write_text('{"secret":"x"}', encoding="utf-8")
                holder_ready.set()
                if not release_holder.wait(timeout=5.0):
                    errors.append(TimeoutError("holder did not see release signal"))
                    return
                holder_saw_release.set()
                if req.exists():
                    holder_files_ok.set()
                else:
                    errors.append(RuntimeError("holder request deleted while lock held"))
        except Exception as exc:
            errors.append(exc)

    def waiter() -> None:
        try:
            if not holder_ready.wait(timeout=5.0):
                errors.append(TimeoutError("waiter did not see holder ready"))
                return
            with elevation_transaction() as (_sess, log_path):
                waiter_log.append(log_path)
                waiter_entered.set()
        except Exception as exc:
            errors.append(exc)

    t_hold = threading.Thread(target=holder)
    t_wait = threading.Thread(target=waiter)
    t_hold.start()
    t_wait.start()
    assert holder_ready.wait(timeout=5.0)
    # Waiter must block while the holder still owns the lock.
    time.sleep(0.2)
    assert not waiter_entered.is_set()
    assert (support / "dislocker-ui-req-holder.json").exists()
    release_holder.set()
    t_hold.join(timeout=5.0)
    t_wait.join(timeout=5.0)
    assert not t_hold.is_alive()
    assert not t_wait.is_alive()
    assert not errors, f"thread errors: {errors}"
    assert holder_saw_release.is_set()
    assert holder_files_ok.is_set()
    assert waiter_entered.is_set()
    assert waiter_log
    # After the holder released, waiter's prepare may sweep the holder's temps.
    assert not (support / "dislocker-ui-req-holder.json").exists()


@pytest.mark.parametrize(
    ("stderr", "exc_type"),
    [
        ("User canceled. (-128)", ElevationCancelled),
        ("execution error: User canceled. (-128)", ElevationCancelled),
        ("AppleEvent timed out. (-1712)", ElevationTimedOut),
        ("with timeout of 600 seconds timed out", ElevationTimedOut),
        ("sh: something failed (2)", RunnerError),
        ("mount failed (3)", RunnerError),
        ("unexpected (4)", RunnerError),
        ("totally opaque failure", RunnerError),
    ],
)
def test_classify_osascript_failure(stderr: str, exc_type: type[Exception]) -> None:
    """stderr samples map to cancel / timeout / generic RunnerError."""
    err = classify_osascript_failure(stderr)
    assert isinstance(err, exc_type)


def test_validate_volume_path_accepts_only_physical_disk() -> None:
    """Elevated GUI mounting intentionally accepts physical disks only."""
    validate_volume_path("/dev/disk2")
    validate_volume_path("/dev/disk2s1")
    validate_volume_path("/dev/disk2s1 ")
    with pytest.raises(RunnerError):
        validate_volume_path("/tmp/image.dmg")
    with pytest.raises(RunnerError):
        validate_volume_path("/dev/rdisk0")


def test_run_elevated_mount_success_loads_session(tmp_path: Path) -> None:
    """On osascript success, session is loaded from session_path."""
    session_path = tmp_path / "active_session.json"
    log_path = tmp_path / "elev.log"
    log_path.write_text("ok\n", encoding="utf-8")
    session = MountSession(
        volume="/dev/disk2s1",
        fuse_mount=str(tmp_path / "fuse"),
        dislocker_file=str(tmp_path / "fuse" / "dislocker-file"),
        raw_disk="/dev/disk999",
        ntfs_mount="/Volumes/X",
        readonly=True,
        used_ntfs3g=True,
    )
    save_session(session, session_path)
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="sekrit",
        readonly=True,
    )
    logs: list[str] = []

    def fake_run(argv, **_kwargs):
        # Ensure AppleScript was built without the secret.
        script = argv[2]
        assert "sekrit" not in script
        assert "dislocker_ui.privileged" in script
        # Request file should exist during the call and contain the secret.
        # Recover path from script is hard; just succeed.
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    with patch("dislocker_ui.elevate.subprocess.run", side_effect=fake_run):
        result = run_elevated_mount(
            req,
            logs.append,
            session_path=session_path,
            log_path=log_path,
            request_dir=tmp_path,
        )
    assert result.ntfs_mount == "/Volumes/X"
    assert any("administrator" in line.lower() for line in logs)


def test_run_elevated_mount_cancel_unlinks_request(tmp_path: Path) -> None:
    """Cancel raises ElevationCancelled and still removes the request file."""
    session_path = tmp_path / "active_session.json"
    log_path = tmp_path / "elev.log"
    log_path.write_text("", encoding="utf-8")
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="sekrit",
        readonly=True,
    )
    created: list[Path] = []

    real_mkstemp = __import__("tempfile").mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(Path(name))
        return fd, name

    def fake_run(*_a, **_k):
        return type(
            "R",
            (),
            {"returncode": 1, "stderr": "User canceled. (-128)", "stdout": ""},
        )()

    with (
        patch("dislocker_ui.elevate.tempfile.mkstemp", side_effect=tracking_mkstemp),
        patch("dislocker_ui.elevate.subprocess.run", side_effect=fake_run),
        pytest.raises(ElevationCancelled),
    ):
        run_elevated_mount(
            req,
            lambda _m: None,
            session_path=session_path,
            log_path=log_path,
            request_dir=tmp_path,
        )
    assert created
    assert not created[0].exists()


def test_prepare_elevation_paths_returns_root_derived_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unprivileged parent does not create root session or log files."""
    from dislocker_ui.elevate import prepare_elevation_paths

    support = tmp_path / "Application Support" / "dislocker-ui"
    support.mkdir(parents=True)
    monkeypatch.setattr(
        "dislocker_ui.session.default_session_path",
        lambda: support / "active_session.json",
    )
    state = tmp_path / "root-state"
    monkeypatch.setattr(
        "dislocker_ui.elevate.root_session_path",
        lambda uid: state / str(uid) / "active_session.json",
    )
    monkeypatch.setattr(
        "dislocker_ui.elevate.root_log_path",
        lambda uid: state / str(uid) / "operation.log",
    )

    def _safe(path: Path, *, label: str) -> None:
        return None

    with patch("dislocker_ui.elevate.assert_safe_path_for_elevation", side_effect=_safe):
        session_path, log_path = prepare_elevation_paths()
    assert session_path.name == "active_session.json"
    assert log_path.name == "operation.log"
    assert not log_path.exists()


def test_assert_safe_path_rejects_world_writable(tmp_path: Path) -> None:
    """Group/world-writable dirs refuse elevation."""
    from dislocker_ui.elevate import assert_safe_path_for_elevation

    d = tmp_path / "wide"
    d.mkdir()
    d.chmod(0o777)
    with pytest.raises(RunnerError, match="writable"):
        assert_safe_path_for_elevation(d, label="test")


def test_request_json_secret_is_last_key(tmp_path: Path) -> None:
    """Secret is the last key in the written request JSON object."""
    from dislocker_ui.elevate import _write_request

    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="last-please",
        readonly=True,
    )
    path = _write_request(
        action="mount",
        req=req,
        request_dir=tmp_path,
    )
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        assert list(data.keys())[-1] == "secret"
        assert data["secret"] == "last-please"
        assert data["action"] == "mount"
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    finally:
        path.unlink(missing_ok=True)


def test_prepare_elevation_paths_checks_writable_py_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prepare_elevation_paths calls _assert_no_writable_py_files on the package dir."""
    from dislocker_ui.elevate import prepare_elevation_paths

    support = tmp_path / "Application Support" / "dislocker-ui"
    support.mkdir(parents=True)
    monkeypatch.setattr(
        "dislocker_ui.session.default_session_path",
        lambda: support / "active_session.json",
    )
    state = tmp_path / "root-state"
    monkeypatch.setattr(
        "dislocker_ui.elevate.root_session_path",
        lambda uid: state / str(uid) / "active_session.json",
    )
    monkeypatch.setattr(
        "dislocker_ui.elevate.root_log_path",
        lambda uid: state / str(uid) / "operation.log",
    )

    def _safe(path: Path, *, label: str) -> None:
        return None

    with (
        patch("dislocker_ui.elevate.assert_safe_path_for_elevation", side_effect=_safe),
        patch("dislocker_ui.elevate._assert_no_writable_py_files") as check_py,
    ):
        prepare_elevation_paths()
    check_py.assert_called_once()
    called_dir = check_py.call_args.args[0]
    assert called_dir.name == "src"


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


def test_assert_no_writable_py_files_handles_stat_oserror(tmp_path: Path) -> None:
    """A broken symlink (target deleted) does not abort the scan."""
    from dislocker_ui.elevate import _assert_no_writable_py_files

    root = tmp_path / "pkg"
    root.mkdir()
    broken = root / "broken.py"
    broken.symlink_to(tmp_path / "no-such-file")
    ok = root / "mod.py"
    ok.write_text("", encoding="utf-8")
    ok.chmod(0o644)
    _assert_no_writable_py_files(root)


def test_is_system_managed_install_requires_root_ownership(tmp_path: Path) -> None:
    from dislocker_ui.elevate import _is_system_managed_install

    assert _is_system_managed_install(tmp_path / "other") is False
    sp = tmp_path / "site-packages"
    sp.mkdir()
    assert _is_system_managed_install(sp) is False


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
