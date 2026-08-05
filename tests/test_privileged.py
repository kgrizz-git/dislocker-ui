"""Regression coverage for the privileged request boundary."""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.privileged import (
    EXIT_RUNNER,
    EXIT_VALIDATION,
    _format_unexpected,
    _load_and_unlink_request,
    _open_root_log,
    _request_entry_name,
    _validate_request,
    _ValidationError,
)
from dislocker_ui.runner import RunnerError


def _payload() -> dict[str, object]:
    return {
        "action": "mount",
        "uid": os.getuid(),
        "gid": os.getgid(),
        "volume": "/dev/disk2s1",
        "method": "user_password",
        "secret": "pw",
        "readonly": True,
        "volume_label": "BitLocker USB",
    }


def _request(base: Path, payload: dict[str, object]) -> Path:
    path = base / "dislocker-ui-req-test_123.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_request_descriptor_loader_accepts_private_regular_file(tmp_path: Path) -> None:
    """A 0600, user-owned request is read then its exact entry is removed."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    request = _request(base, _payload())
    name = _request_entry_name(request, base)
    assert _load_and_unlink_request(name, base, os.getuid())["action"] == "mount"
    assert not request.exists()


def test_request_descriptor_loader_rejects_symlink(tmp_path: Path) -> None:
    """O_NOFOLLOW stops a symlink replacement before it is read."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    real = _request(base, _payload())
    link = base / "dislocker-ui-req-link123.json"
    link.symlink_to(real)
    name = _request_entry_name(link, base)
    uid = os.getuid()
    with pytest.raises(_ValidationError, match="cannot read request"):
        _load_and_unlink_request(name, base, uid)


@pytest.mark.parametrize("value", ["relative.json", "/tmp/request.json"])
def test_request_entry_name_rejects_escape(value: str, tmp_path: Path) -> None:
    """The privileged child accepts one generated entry directly below its base."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    request = Path(value)
    with pytest.raises(_ValidationError):
        _request_entry_name(request, base)


def test_request_rejects_old_root_authority_fields() -> None:
    """Session, log, and dependency paths are never accepted from the request."""
    payload = _payload()
    payload["session_path"] = "/etc/passwd"
    uid = os.getuid()
    with pytest.raises(_ValidationError, match="fields"):
        _validate_request(payload, expected_action="mount", expected_uid=uid)


@pytest.mark.parametrize(
    "volume", ["/tmp/image.dmg", "/dev/rdisk2", "/dev/disk2/../x", "/dev/disk2s1 "]
)
def test_request_rejects_nonphysical_volume(volume: str) -> None:
    payload = _payload()
    payload["volume"] = volume
    uid = os.getuid()
    with pytest.raises(_ValidationError, match="physical"):
        _validate_request(payload, expected_action="mount", expected_uid=uid)


@pytest.mark.parametrize("label", ["../outside", "/Volumes/outside", "..", "bad/name", ""])
def test_request_rejects_unsafe_volume_label(label: str) -> None:
    payload = _payload()
    payload["volume_label"] = label
    uid = os.getuid()
    with pytest.raises(_ValidationError, match="label"):
        _validate_request(payload, expected_action="mount", expected_uid=uid)


def test_request_rejects_truthy_nonboolean_readonly() -> None:
    payload = _payload()
    payload["readonly"] = "true"
    uid = os.getuid()
    with pytest.raises(_ValidationError, match="boolean"):
        _validate_request(payload, expected_action="mount", expected_uid=uid)


def test_validated_gid_rejects_group_not_assigned_to_user() -> None:
    """The root child will not grant state access to an arbitrary group."""
    from dislocker_ui.privileged import _validated_gid

    account = SimpleNamespace(pw_name="test-user", pw_gid=20)
    with (
        patch("dislocker_ui.privileged.pwd.getpwuid", return_value=account),
        patch("dislocker_ui.privileged.os.getgrouplist", return_value=[20, 80]),
    ):
        assert _validated_gid(501, 80) == 80
        with pytest.raises(_ValidationError, match="not assigned"):
            _validated_gid(501, 999)


def test_main_rejects_unassigned_gid_before_state_creation(tmp_path: Path) -> None:
    """A forged request group fails before root-owned paths are created."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    payload = _payload()
    payload["gid"] = 999
    request = _request(base, payload)
    account = SimpleNamespace(pw_name="test-user", pw_gid=20)
    with (
        patch("dislocker_ui.privileged._user_base", return_value=base),
        patch("dislocker_ui.privileged.pwd.getpwuid", return_value=account),
        patch("dislocker_ui.privileged.os.getgrouplist", return_value=[20]),
        patch("dislocker_ui.privileged.ensure_root_state_dir") as ensure,
    ):
        from dislocker_ui.privileged import main

        assert (
            main(["mount", "--uid", str(os.getuid()), "--request", str(request)]) == EXIT_VALIDATION
        )
    ensure.assert_not_called()


def test_main_fails_before_mount_when_trusted_deps_missing(tmp_path: Path) -> None:
    """A request cannot select a PATH-provided program for the root child."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    request = _request(base, _payload())
    state = tmp_path / "state"
    state.mkdir()
    with (
        patch("dislocker_ui.privileged._user_base", return_value=base),
        patch("dislocker_ui.privileged.ensure_root_state_dir", return_value=state),
        patch(
            "dislocker_ui.privileged.root_session_path", return_value=state / "active_session.json"
        ),
        patch("dislocker_ui.privileged.root_log_path", return_value=state / "operation.log"),
        patch("dislocker_ui.privileged._open_root_log", return_value=StringIO()),
        patch(
            "dislocker_ui.privileged.discover_privileged_deps",
            return_value=MagicMock(core_ok=False, missing_core=lambda: ["ntfs-3g"]),
        ),
        patch("dislocker_ui.privileged.mount_volume") as mount,
    ):
        from dislocker_ui.privileged import main

        assert main(["mount", "--uid", str(os.getuid()), "--request", str(request)]) == EXIT_RUNNER
    mount.assert_not_called()


def test_main_rejects_invalid_request_before_state_creation(tmp_path: Path) -> None:
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    payload = _payload()
    payload["volume"] = "/tmp/image.dmg"
    request = _request(base, payload)
    with (
        patch("dislocker_ui.privileged._user_base", return_value=base),
        patch("dislocker_ui.privileged.ensure_root_state_dir") as ensure,
    ):
        from dislocker_ui.privileged import main

        assert (
            main(["mount", "--uid", str(os.getuid()), "--request", str(request)]) == EXIT_VALIDATION
        )
    ensure.assert_not_called()


def test_main_mount_uses_only_root_derived_state_and_deps(tmp_path: Path) -> None:
    """A valid request mounts through the root-derived paths and trusted deps."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    request = _request(base, _payload())
    state = tmp_path / "state"
    state.mkdir()
    log = StringIO()
    deps = MagicMock(core_ok=True)
    with (
        patch("dislocker_ui.privileged._user_base", return_value=base),
        patch("dislocker_ui.privileged.ensure_root_state_dir", return_value=state),
        patch(
            "dislocker_ui.privileged.root_session_path", return_value=state / "active_session.json"
        ),
        patch("dislocker_ui.privileged.root_log_path", return_value=state / "operation.log"),
        patch("dislocker_ui.privileged._open_root_log", return_value=log),
        patch("dislocker_ui.privileged.discover_privileged_deps", return_value=deps),
        patch(
            "dislocker_ui.privileged.mount_volume",
            return_value=MagicMock(ntfs_mount="/Volumes/USB"),
        ) as mount,
        patch("dislocker_ui.privileged._verify_session_readable") as finalize,
    ):
        from dislocker_ui.privileged import EXIT_OK, main

        assert main(["mount", "--uid", str(os.getuid()), "--request", str(request)]) == EXIT_OK
    assert mount.call_args.kwargs["session_path"] == state / "active_session.json"
    assert mount.call_args.kwargs["elevated"] is True
    finalize.assert_called_once_with(state / "active_session.json", os.getgid())


def test_main_logs_manual_recovery_when_session_finalization_fails(tmp_path: Path) -> None:
    """A mounted volume remains recoverable when final permission changes fail."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    request = _request(base, _payload())
    state = tmp_path / "state"
    state.mkdir()
    log = MagicMock()
    session = MagicMock(ntfs_mount="/Volumes/USB", raw_disk="/dev/disk9")
    with (
        patch("dislocker_ui.privileged._user_base", return_value=base),
        patch("dislocker_ui.privileged.ensure_root_state_dir", return_value=state),
        patch(
            "dislocker_ui.privileged.root_session_path", return_value=state / "active_session.json"
        ),
        patch("dislocker_ui.privileged.root_log_path", return_value=state / "operation.log"),
        patch("dislocker_ui.privileged._open_root_log", return_value=log),
        patch(
            "dislocker_ui.privileged.discover_privileged_deps", return_value=MagicMock(core_ok=True)
        ),
        patch("dislocker_ui.privileged.mount_volume", return_value=session),
        patch(
            "dislocker_ui.privileged._verify_session_readable",
            side_effect=RunnerError("permission update failed"),
        ),
    ):
        from dislocker_ui.privileged import main

        assert main(["mount", "--uid", str(os.getuid()), "--request", str(request)]) == EXIT_RUNNER
    assert any(
        "Manually unmount /Volumes/USB, detach /dev/disk9" in call.args[0]
        for call in log.write.call_args_list
    )


@pytest.mark.parametrize(
    "argument",
    [
        "--user-password=top-secret",
        "--recovery-password top-secret",
        "--bekfile /private/key.bek",
    ],
)
def test_unexpected_error_redacts_all_supported_secret_argument_forms(argument: str) -> None:
    """Unexpected logs must not expose password or BEK command arguments."""
    rendered = _format_unexpected(RuntimeError(f"failed command: {argument}"))
    assert "top-secret" not in rendered
    assert "/private/key.bek" not in rendered
    assert "***" in rendered


def test_root_log_rejects_a_non_root_caller(tmp_path: Path) -> None:
    """The root-only log writer never implies support for unprivileged callers."""
    with (
        patch("dislocker_ui.privileged.os.geteuid", return_value=501),
        pytest.raises(_ValidationError, match="privileged helper"),
    ):
        _open_root_log(tmp_path / "operation.log", tmp_path, 20)


def test_main_unmount_uses_canonical_session(tmp_path: Path) -> None:
    """Unmount carries no user-selected cleanup path through the request."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    request = _request(base, {"action": "unmount", "uid": os.getuid(), "gid": os.getgid()})
    state = tmp_path / "state"
    state.mkdir()
    log = StringIO()
    with (
        patch("dislocker_ui.privileged._user_base", return_value=base),
        patch("dislocker_ui.privileged.ensure_root_state_dir", return_value=state),
        patch(
            "dislocker_ui.privileged.root_session_path", return_value=state / "active_session.json"
        ),
        patch("dislocker_ui.privileged.root_log_path", return_value=state / "operation.log"),
        patch("dislocker_ui.privileged._open_root_log", return_value=log),
        patch(
            "dislocker_ui.privileged.discover_privileged_deps", return_value=MagicMock(core_ok=True)
        ),
        patch("dislocker_ui.privileged.unmount_volume") as unmount,
    ):
        from dislocker_ui.privileged import EXIT_OK, main

        assert main(["unmount", "--uid", str(os.getuid()), "--request", str(request)]) == EXIT_OK
    assert unmount.call_args.kwargs["session_path"] == state / "active_session.json"
