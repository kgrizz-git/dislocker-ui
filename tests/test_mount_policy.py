"""Unit tests for policy shared by the unprivileged and root mount paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from dislocker_ui.mount_policy import (
    elevated_request_error,
    elevated_session_error,
    is_physical_volume,
    is_privileged_fuse_path,
    is_safe_volume_label,
    privileged_staging_dir,
    remove_empty_privileged_staging_parents,
)
from dislocker_ui.session import MountSession


@pytest.mark.parametrize("value", ["/dev/disk2", "/dev/disk12s3"])
def test_physical_volume_accepts_diskutil_device_selectors(value: str) -> None:
    assert is_physical_volume(value)


@pytest.mark.parametrize("value", ["/dev/rdisk2", "/dev/disk2s", 2])
def test_physical_volume_rejects_other_values(value: object) -> None:
    assert not is_physical_volume(value)


@pytest.mark.parametrize("value", ["BitLocker USB", "X_1.0"])
def test_safe_volume_label_accepts_direct_volume_children(value: str) -> None:
    assert is_safe_volume_label(value)


@pytest.mark.parametrize("value", [".", "..", "bad/name", "", None])
def test_safe_volume_label_rejects_paths_and_non_strings(value: object) -> None:
    assert not is_safe_volume_label(value)


@pytest.mark.parametrize(
    ("volume", "readonly", "label", "message"),
    [
        ("/tmp/image.dmg", True, "USB", "physical"),
        ("/dev/disk2", "true", "USB", "boolean"),
        ("/dev/disk2", True, "../USB", "filesystem-safe"),
    ],
)
def test_elevated_request_error_rejects_each_untrusted_field(
    volume: object, readonly: object, label: object, message: str
) -> None:
    assert message in (elevated_request_error(volume, readonly, label) or "")


def test_elevated_request_error_accepts_safe_physical_request() -> None:
    assert elevated_request_error("/dev/disk2s1", False, "BitLocker USB") is None


def _session(*, fuse_mount: Path, ntfs_mount: Path) -> MountSession:
    return MountSession(
        volume="/dev/disk2s1",
        fuse_mount=str(fuse_mount),
        dislocker_file=str(fuse_mount / "dislocker-file"),
        raw_disk="/dev/disk2",
        ntfs_mount=str(ntfs_mount),
        readonly=True,
        used_ntfs3g=True,
        elevated=True,
    )


def test_privileged_fuse_path_requires_direct_non_symlink_child(tmp_path: Path) -> None:
    session_path = tmp_path / "501" / "active_session.json"
    staging = privileged_staging_dir(session_path, 501)
    staging.mkdir(parents=True)
    fuse_path = staging / "session-test"
    fuse_path.mkdir()
    assert is_privileged_fuse_path(fuse_path, session_path)
    assert not is_privileged_fuse_path(staging / "other", session_path)
    assert not is_privileged_fuse_path(fuse_path, None)


def test_elevated_session_error_accepts_only_derived_safe_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_path = tmp_path / "501" / "active_session.json"
    staging = privileged_staging_dir(session_path, 501)
    fuse_path = staging / "session-test"
    fuse_path.mkdir(parents=True)
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    monkeypatch.setattr("dislocker_ui.mount_policy.VOLUMES_ROOT", volumes)
    session = _session(fuse_mount=fuse_path, ntfs_mount=volumes / "USB")
    assert elevated_session_error(session, session_path) is None
    assert "staging" in (elevated_session_error(session, None) or "")


def test_elevated_session_error_rejects_invalid_device(tmp_path: Path) -> None:
    session_path = tmp_path / "501" / "active_session.json"
    session = _session(fuse_mount=tmp_path / "session", ntfs_mount=Path("/Volumes/USB"))
    session.volume = "/tmp/image.dmg"
    assert "invalid device" in (elevated_session_error(session, session_path) or "")


def test_remove_staging_parents_ignores_non_staging_path(tmp_path: Path) -> None:
    session_path = tmp_path / "501" / "active_session.json"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    remove_empty_privileged_staging_parents(unrelated, session_path)
    assert unrelated.exists()
