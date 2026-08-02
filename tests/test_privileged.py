"""
Unit tests for the privileged child module.

Overall purpose:
  Cover request schema validation, deps round-trip (no discover_deps),
  O_NOFOLLOW log open, and exit-code mapping via main() helpers.

Requirements:
  pytest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.privileged import (
    EXIT_OK,
    EXIT_RUNNER,
    EXIT_UNEXPECTED,
    EXIT_VALIDATION,
    _deps_from_payload,
    _format_unexpected,
    _open_log,
    _validate_request,
    main,
)
from dislocker_ui.runner import RunnerError


def _bin() -> str:
    return str(Path("/bin/sh").resolve())


def _deps_dict() -> dict[str, str]:
    b = _bin()
    return {
        "dislocker_fuse": b,
        "hdiutil": b,
        "diskutil": b,
        "umount": b,
        "ntfs3g": b,
    }


def _mount_payload(tmp_path: Path, *, uid: int | None = None) -> dict:
    log_path = tmp_path / "elev.log"
    log_path.write_text("", encoding="utf-8")
    os.chmod(log_path, 0o600)
    return {
        "action": "mount",
        "volume": "/dev/disk2s1",
        "method": "user_password",
        "secret": "pw",
        "readonly": True,
        "volume_label": "DislockerUI",
        "session_path": str(tmp_path / "active_session.json"),
        "log_path": str(log_path),
        "uid": os.getuid() if uid is None else uid,
        "gid": os.getgid(),
        "deps": _deps_dict(),
    }


def test_deps_round_trip_no_discover() -> None:
    """Serialized deps become DepsStatus without calling discover_deps."""
    with patch(
        "dislocker_ui.privileged.DepsStatus",
        wraps=__import__("dislocker_ui.deps", fromlist=["DepsStatus"]).DepsStatus,
    ) as _:
        status = _deps_from_payload(_deps_dict())
    assert status.ntfs3g == _bin()
    assert status.core_ok is True


def test_validate_request_ok(tmp_path: Path) -> None:
    """A complete mount payload passes validation."""
    _validate_request(_mount_payload(tmp_path), expected_action="mount")


def test_validate_rejects_relative_session(tmp_path: Path) -> None:
    """session_path must be absolute."""
    payload = _mount_payload(tmp_path)
    payload["session_path"] = "relative.json"
    with pytest.raises(Exception, match="absolute"):
        _validate_request(payload, expected_action="mount")


def test_validate_bek_rejects_tilde(tmp_path: Path) -> None:
    """Child must not accept BEK paths containing ~."""
    payload = _mount_payload(tmp_path)
    payload["method"] = "bek_file"
    payload["secret"] = str(tmp_path / "~not-home.bek")
    # Absolute but contains ~ character
    with pytest.raises(Exception, match="~"):
        _validate_request(payload, expected_action="mount")


def test_open_log_rejects_symlink(tmp_path: Path) -> None:
    """O_NOFOLLOW rejects a symlink log_path."""
    real = tmp_path / "real.log"
    real.write_text("", encoding="utf-8")
    os.chmod(real, 0o600)
    link = tmp_path / "link.log"
    link.symlink_to(real)
    uid = os.getuid()
    with pytest.raises(Exception, match="log"):
        _open_log(link, owner_uid=uid)


def test_open_log_accepts_owner_mode(tmp_path: Path) -> None:
    """Regular 0600 log owned by request uid opens for append."""
    log_path = tmp_path / "ok.log"
    log_path.write_text("start\n", encoding="utf-8")
    os.chmod(log_path, 0o600)
    fp = _open_log(log_path, owner_uid=os.getuid())
    fp.write("more\n")
    fp.close()
    assert "more" in log_path.read_text(encoding="utf-8")


def test_main_validation_exit_2(tmp_path: Path) -> None:
    """Invalid request JSON yields exit code 2."""
    req = tmp_path / "bad.json"
    req.write_text("{not-json", encoding="utf-8")
    assert main(["mount", "--uid", str(os.getuid()), "--request", str(req)]) == EXIT_VALIDATION


def test_main_mount_success(tmp_path: Path) -> None:
    """Successful mount path returns 0 and calls mount_volume with elevated."""
    payload = _mount_payload(tmp_path)
    req = tmp_path / "req.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    session = MagicMock(ntfs_mount="/Volumes/X")
    with (
        patch("dislocker_ui.privileged.mount_volume", return_value=session) as mount,
        patch("dislocker_ui.privileged._chown_session"),
        patch("dislocker_ui.privileged.os.chdir"),
        patch("dislocker_ui.privileged._user_base", return_value=tmp_path.resolve()),
    ):
        code = main(["mount", "--uid", str(os.getuid()), "--request", str(req)])
    assert code == EXIT_OK
    assert not req.exists()  # unlinked after read
    mount.assert_called_once()
    assert mount.call_args.kwargs["elevated"] is True
    assert mount.call_args.kwargs["uid"] == os.getuid()


def test_main_runner_error_exit_3(tmp_path: Path) -> None:
    """RunnerError maps to exit 3."""
    payload = _mount_payload(tmp_path)
    req = tmp_path / "req.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    with (
        patch(
            "dislocker_ui.privileged.mount_volume",
            side_effect=RunnerError("boom"),
        ),
        patch("dislocker_ui.privileged.os.chdir"),
        patch("dislocker_ui.privileged._user_base", return_value=tmp_path.resolve()),
    ):
        assert main(["mount", "--uid", str(os.getuid()), "--request", str(req)]) == EXIT_RUNNER


def test_main_unexpected_exit_4(tmp_path: Path) -> None:
    """Unexpected exceptions map to exit 4."""
    payload = _mount_payload(tmp_path)
    req = tmp_path / "req.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    with (
        patch(
            "dislocker_ui.privileged.mount_volume",
            side_effect=RuntimeError("surprise"),
        ),
        patch("dislocker_ui.privileged.os.chdir"),
        patch("dislocker_ui.privileged._user_base", return_value=tmp_path.resolve()),
    ):
        assert main(["mount", "--uid", str(os.getuid()), "--request", str(req)]) == EXIT_UNEXPECTED


def test_main_never_calls_discover_deps(tmp_path: Path) -> None:
    """Privileged mount must not rediscover deps."""
    payload = _mount_payload(tmp_path)
    req = tmp_path / "req.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    with (
        patch("dislocker_ui.privileged.mount_volume", return_value=MagicMock(ntfs_mount="x")),
        patch("dislocker_ui.privileged._chown_session"),
        patch("dislocker_ui.privileged.os.chdir"),
        patch("dislocker_ui.privileged._user_base", return_value=tmp_path.resolve()),
        patch("dislocker_ui.deps.discover_deps") as discover,
    ):
        assert main(["mount", "--uid", str(os.getuid()), "--request", str(req)]) == EXIT_OK
    discover.assert_not_called()


def test_confine_under_base_accepts_and_rejects(tmp_path: Path) -> None:
    """Paths inside the base resolve; paths escaping it raise."""
    from dislocker_ui.privileged import _confine_under_base, _ValidationError

    base = tmp_path.resolve()
    inside = _confine_under_base(str(tmp_path / "active_session.json"), base, label="s")
    assert inside == (base / "active_session.json")
    with pytest.raises(_ValidationError, match="escapes"):
        _confine_under_base("/etc/passwd", base, label="s")


def test_main_rejects_session_path_escaping_base(tmp_path: Path) -> None:
    """A session_path outside the user's base dir is rejected (exit 2)."""
    payload = _mount_payload(tmp_path)
    payload["session_path"] = "/etc/dislocker-evil.json"
    req = tmp_path / "req.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    with (
        patch("dislocker_ui.privileged.os.chdir"),
        patch("dislocker_ui.privileged._user_base", return_value=tmp_path.resolve()),
    ):
        assert main(["mount", "--uid", str(os.getuid()), "--request", str(req)]) == EXIT_VALIDATION


def test_assert_safe_base_accepts_and_rejects(tmp_path: Path) -> None:
    """A user-owned dir passes; a symlink or world-writable base is refused."""
    from dislocker_ui.privileged import _assert_safe_base, _ValidationError

    _assert_safe_base(tmp_path, os.getuid())  # owned, not a symlink, 0700-ish

    link = tmp_path / "linkdir"
    link.symlink_to(tmp_path)
    with pytest.raises(_ValidationError, match="symlink"):
        _assert_safe_base(link, os.getuid())

    wide = tmp_path / "wide"
    wide.mkdir()
    os.chmod(wide, 0o777)
    with pytest.raises(_ValidationError, match="writable"):
        _assert_safe_base(wide, os.getuid())


def test_chown_failure_still_reports_success(tmp_path: Path) -> None:
    """A chown failure after a good mount warns but keeps exit 0."""
    payload = _mount_payload(tmp_path)
    req = tmp_path / "req.json"
    req.write_text(json.dumps(payload), encoding="utf-8")
    with (
        patch("dislocker_ui.privileged.mount_volume", return_value=MagicMock(ntfs_mount="/V/X")),
        patch("dislocker_ui.privileged._chown_session", side_effect=RunnerError("denied")),
        patch("dislocker_ui.privileged.os.chdir"),
        patch("dislocker_ui.privileged._user_base", return_value=tmp_path.resolve()),
    ):
        assert main(["mount", "--uid", str(os.getuid()), "--request", str(req)]) == EXIT_OK


def test_validate_mount_fields_rejects_bad_volume(tmp_path: Path) -> None:
    """The child independently rejects a volume that is not a disk or file."""
    payload = _mount_payload(tmp_path)
    payload["volume"] = "/dev/rdisk0"
    with pytest.raises(Exception, match="Refusing to elevate for volume"):
        _validate_request(payload, expected_action="mount")


def test_load_and_unlink_request_rejects_symlink(tmp_path: Path) -> None:
    """A symlinked request path is refused before it is read."""
    from dislocker_ui.privileged import _load_and_unlink_request, _ValidationError

    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(_ValidationError, match="symlink"):
        _load_and_unlink_request(link)


def test_format_unexpected_redacts_secret() -> None:
    """A traceback carrying a password argv is fully scrubbed, not just prefixed."""
    try:
        raise RuntimeError("boom while running --user-password=hunter2 --recovery-password=ABC-123")
    except RuntimeError as exc:
        out = _format_unexpected(exc)
    assert "hunter2" not in out
    assert "ABC-123" not in out
    assert "--user-password=***" in out
    assert "--recovery-password=***" in out
