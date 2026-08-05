"""Regression coverage for the privileged request boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.privileged import (
    EXIT_RUNNER,
    EXIT_VALIDATION,
    _load_and_unlink_request,
    _validate_request,
    _ValidationError,
)


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
    path = base / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_request_descriptor_loader_accepts_private_regular_file(tmp_path: Path) -> None:
    """A 0600, user-owned request is read then its exact entry is removed."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    request = _request(base, _payload())
    assert _load_and_unlink_request(request, base, os.getuid())["action"] == "mount"
    assert not request.exists()


def test_request_descriptor_loader_rejects_symlink(tmp_path: Path) -> None:
    """O_NOFOLLOW stops a symlink replacement before it is read."""
    base = tmp_path / "requests"
    base.mkdir(mode=0o700)
    real = _request(base, _payload())
    link = base / "request-link.json"
    link.symlink_to(real)
    with pytest.raises(_ValidationError, match="cannot read request"):
        _load_and_unlink_request(link, base, os.getuid())


def test_request_rejects_old_root_authority_fields() -> None:
    """Session, log, and dependency paths are never accepted from the request."""
    payload = _payload()
    payload["session_path"] = "/etc/passwd"
    with pytest.raises(_ValidationError, match="fields"):
        _validate_request(payload, expected_action="mount", expected_uid=os.getuid())


@pytest.mark.parametrize("volume", ["/tmp/image.dmg", "/dev/rdisk2", "/dev/disk2/../x"])
def test_request_rejects_nonphysical_volume(volume: str) -> None:
    payload = _payload()
    payload["volume"] = volume
    with pytest.raises(_ValidationError, match="physical"):
        _validate_request(payload, expected_action="mount", expected_uid=os.getuid())


@pytest.mark.parametrize("label", ["../outside", "/Volumes/outside", "..", "bad/name", ""])
def test_request_rejects_unsafe_volume_label(label: str) -> None:
    payload = _payload()
    payload["volume_label"] = label
    with pytest.raises(_ValidationError, match="label"):
        _validate_request(payload, expected_action="mount", expected_uid=os.getuid())


def test_request_rejects_truthy_nonboolean_readonly() -> None:
    payload = _payload()
    payload["readonly"] = "true"
    with pytest.raises(_ValidationError, match="boolean"):
        _validate_request(payload, expected_action="mount", expected_uid=os.getuid())


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
        patch("dislocker_ui.privileged.os.fchown"),
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
