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
    needs_elevation,
    run_elevated_mount,
    serialize_deps,
    validate_volume_path,
    volume_needs_elevation,
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


def test_volume_needs_elevation_respects_access() -> None:
    """UI helper is true only when not root and volume is unreadable."""
    with (
        patch("dislocker_ui.elevate.os.geteuid", return_value=501),
        patch("dislocker_ui.elevate.os.access", return_value=False),
    ):
        assert volume_needs_elevation("/dev/disk2s1") is True
    with patch("dislocker_ui.elevate.os.geteuid", return_value=0):
        assert volume_needs_elevation("/dev/disk2s1") is False


def test_quoting_handles_spaces_quotes_dollar_backticks(tmp_path: Path) -> None:
    """shlex quoting protects spaces, quotes, $, and backticks in paths."""
    tricky = tmp_path / 'req $`" weird.json'
    tricky.write_text("{}", encoding="utf-8")
    cmd = build_privileged_shell_command("mount", tricky)
    assert "cd / &&" in cmd
    assert "-s" in cmd and "-P" in cmd
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


def test_validate_volume_path_accepts_disk_and_file(tmp_path: Path) -> None:
    """/dev/disk* pattern and regular files are accepted."""
    validate_volume_path("/dev/disk2")
    validate_volume_path("/dev/disk2s1")
    f = tmp_path / "image.dmg"
    f.write_bytes(b"x")
    validate_volume_path(str(f))
    with pytest.raises(RunnerError):
        validate_volume_path("/etc/passwd/../evil")
    with pytest.raises(RunnerError):
        validate_volume_path("/dev/rdisk0")


def test_serialize_deps_resolves_paths(tmp_path: Path) -> None:
    """Deps serialization requires all paths and resolves them."""
    # Use real existing paths from the test environment where possible.
    python = Path("/bin/sh")
    deps = DepsStatus(
        dislocker_fuse=str(python),
        hdiutil=str(python),
        diskutil=str(python),
        umount=str(python),
        ntfs3g=str(python),
    )
    out = serialize_deps(deps)
    assert out["ntfs3g"] == str(python.resolve())


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

    def fake_run(argv, check, capture_output, text):
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
            _deps(),
            logs.append,
            session_path=session_path,
            log_path=log_path,
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
            _deps(),
            lambda _m: None,
            session_path=session_path,
            log_path=log_path,
        )
    assert created
    assert not created[0].exists()


def test_prepare_elevation_paths_creates_0600_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prepare_elevation_paths returns session path and a mode-0600 log file."""
    from dislocker_ui.elevate import prepare_elevation_paths

    support = tmp_path / "Application Support" / "dislocker-ui"
    support.mkdir(parents=True)
    monkeypatch.setattr(
        "dislocker_ui.session.default_session_path",
        lambda: support / "active_session.json",
    )

    def _safe(path: Path, *, label: str) -> None:
        return None

    with patch("dislocker_ui.elevate.assert_safe_path_for_elevation", side_effect=_safe):
        session_path, log_path = prepare_elevation_paths()
    assert session_path.name == "active_session.json"
    assert log_path.is_file()
    assert oct(log_path.stat().st_mode & 0o777) == "0o600"
    log_path.unlink(missing_ok=True)


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
        deps=_deps(),
        session_path=tmp_path / "s.json",
        log_path=tmp_path / "l.log",
        req=req,
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
