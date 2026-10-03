"""
Tests for verified raw-disk detach (plan D2 + D2b).

Overall purpose:
  Verify unmount never detaches a device that is no longer attached as
  the session's own disk image, and the mount-failure cleanup path
  applies the same identity gate.

Requirements:
  pytest.
"""

from __future__ import annotations

import contextlib
import plistlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.runner import RunnerError, _best_effort_cleanup, unmount_volume
from dislocker_ui.session import MountSession, save_session
from dislocker_ui.unmount_steps import _attached_disk_images, _image_identity_ok

_HDIUTIL = "/bin/hdiutil"
_NTFS_MOUNT = "/Volumes/DislockerUI-stale-probe"


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil=_HDIUTIL,
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def _session(
    *,
    fuse_mount: str,
    ntfs_mount: str = _NTFS_MOUNT,
    raw_disk: str = "/dev/disk9",
) -> MountSession:
    """Build a valid non-elevated session for the detach tests."""
    return MountSession(
        volume="/dev/disk2s1",
        fuse_mount=fuse_mount,
        dislocker_file=fuse_mount + "/dislocker-file",
        raw_disk=raw_disk,
        ntfs_mount=ntfs_mount,
        readonly=True,
        used_ntfs3g=True,
        elevated=False,
    )


@contextlib.contextmanager
def _fuse_dir(*, suffix: str = "") -> Iterator[Path]:
    """Create a real tempdir dislocker-ui-* dir; remove it afterwards."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-", suffix=suffix))
    try:
        yield fuse
    finally:
        shutil.rmtree(fuse, ignore_errors=True)


def _stored_session(tmp_path: Path, session: MountSession) -> Path:
    """Write *session* to a temp session file; return its path."""
    path = tmp_path / "active_session.json"
    save_session(session, path)
    return path


def _unmount_session_file(session_path: Path, logs: list[str]) -> None:
    """Run the in-process unmount path against an explicit session file."""
    with patch("dislocker_ui.elevate.needs_elevation", return_value=False):
        unmount_volume(_deps(), logs.append, session_path=session_path)


def _image_entry(image_path: str, *devs: str) -> dict:
    """Build one hdiutil info -plist image entry."""
    return {"image-path": image_path, "system-entities": [{"dev-entry": d} for d in devs]}


def _detach_responder(
    *,
    table: str = "",
    info_images: list[dict] | None = None,
    info_rc: int = 0,
    info_error: Exception | None = None,
    detach_rc: int = 0,
    force_rc: int = 0,
) -> MagicMock:
    """Route /sbin/mount, hdiutil info -plist, and detach/force-detach."""
    mount_result = MagicMock(returncode=0, stdout=table, stderr="")
    info_result = MagicMock(
        returncode=info_rc, stdout=plistlib.dumps({"images": info_images or []}), stderr=b""
    )
    detach_result = MagicMock(returncode=detach_rc, stdout="", stderr="busy" if detach_rc else "")
    force_result = MagicMock(returncode=force_rc, stdout="", stderr="busy" if force_rc else "")

    def _fake(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:1] == ["/sbin/mount"]:
            return mount_result
        if cmd[1:3] == ["info", "-plist"]:
            if info_error is not None:
                raise info_error
            return info_result
        if cmd[2:3] == ["-force"]:
            return force_result
        return detach_result

    return MagicMock(side_effect=_fake)


def _detach_calls(run: MagicMock) -> list[list[str]]:
    """Return the detach argv lists issued through the subprocess mock."""
    return [c.args[0] for c in run.call_args_list if c.args[0][1:2] == ["detach"]]


def test_attached_disk_images_maps_every_entity() -> None:
    """Whole disks and partition nodes map to the decoded image path."""
    payload = {
        "images": [
            _image_entry("/Applications/Other.dmg", "/dev/disk5", "/dev/disk5s1"),
            _image_entry("/tmp/Kiro%20CLI.dmg", "/dev/disk6"),
            {"image-path": 42, "system-entities": []},
            {"nope": True},
        ]
    }
    run = MagicMock(returncode=0, stdout=plistlib.dumps(payload), stderr=b"")
    with patch("subprocess.run", return_value=run):
        assert _attached_disk_images(_HDIUTIL) == {
            "/dev/disk5": "/Applications/Other.dmg",
            "/dev/disk5s1": "/Applications/Other.dmg",
            "/dev/disk6": "/tmp/Kiro CLI.dmg",
        }


def test_attached_disk_images_returns_none_on_failure() -> None:
    """A failed, unrunnable, or malformed listing reports unknown state."""
    with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout=b"", stderr=b"x")):
        assert _attached_disk_images(_HDIUTIL) is None
    with patch("subprocess.run", side_effect=OSError("no hdiutil")):
        assert _attached_disk_images(_HDIUTIL) is None
    with patch(
        "subprocess.run", return_value=MagicMock(returncode=0, stdout=b"garbage", stderr=b"")
    ):
        assert _attached_disk_images(_HDIUTIL) is None
    with patch(
        "subprocess.run",
        return_value=MagicMock(
            returncode=0, stdout=plistlib.dumps(["not", "a", "dict"]), stderr=b""
        ),
    ):
        assert _attached_disk_images(_HDIUTIL) is None


def test_image_identity_ok_matches_own_image_only() -> None:
    """Identity holds for the session image, nothing else."""
    session = _session(fuse_mount="/tmp/dislocker-ui-x")
    images = {"/dev/disk9": "/tmp/dislocker-ui-x/dislocker-file"}
    assert _image_identity_ok(images, "/dev/disk9", session)
    assert not _image_identity_ok(images, "/dev/disk5", session)
    assert not _image_identity_ok(
        {"/dev/disk9": "/tmp/other/dislocker-file"}, "/dev/disk9", session
    )


def test_detach_skips_absent_device(tmp_path: Path) -> None:
    """A device that is now a physical disk (or gone) is never detached."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        logs: list[str] = []
        others = [_image_entry("/Applications/Other.dmg", "/dev/disk5")]
        run = _detach_responder(info_images=others)
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, logs)
    assert not stored.exists()
    assert _detach_calls(run) == []
    assert any("skipping detach" in line for line in logs)


def test_detach_skips_different_image(tmp_path: Path) -> None:
    """A device attached as someone else's image is never detached."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        other = _image_entry(str(Path("/tmp") / "other-dislocker" / "dislocker-file"), "/dev/disk9")
        run = _detach_responder(info_images=[other])
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert _detach_calls(run) == []


def test_detach_runs_on_match(tmp_path: Path) -> None:
    """The verified device detaches via the primary command."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        entry = _image_entry(str(fuse / "dislocker-file"), "/dev/disk9")
        run = _detach_responder(info_images=[entry])
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert _detach_calls(run) == [[_HDIUTIL, "detach", "/dev/disk9"]]


def test_detach_falls_back_to_force(tmp_path: Path) -> None:
    """A failed primary detach retries with force; success clears state."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        entry = _image_entry(str(fuse / "dislocker-file"), "/dev/disk9")
        run = _detach_responder(info_images=[entry], detach_rc=1, force_rc=0)
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert _detach_calls(run) == [
        [_HDIUTIL, "detach", "/dev/disk9"],
        [_HDIUTIL, "detach", "-force", "/dev/disk9"],
    ]


def test_detach_matches_tmpdir_alias(tmp_path: Path) -> None:
    """A symlinked tempdir spelling of the same image still matches."""
    tmp = Path(tempfile.gettempdir())
    link = tmp / "dislocker-ui-alias-probe"
    if link.exists() or link.is_symlink():
        pytest.skip("alias probe path already exists")
    link.symlink_to(tmp, target_is_directory=True)
    try:
        with _fuse_dir() as fuse:
            stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
            entry = _image_entry(str(link / fuse.name / "dislocker-file"), "/dev/disk9")
            run = _detach_responder(info_images=[entry])
            with patch("dislocker_ui.unmount_steps.subprocess.run", run):
                _unmount_session_file(stored, [])
        assert not stored.exists()
        assert _detach_calls(run) == [[_HDIUTIL, "detach", "/dev/disk9"]]
    finally:
        link.unlink(missing_ok=True)


def test_detach_matches_percent_encoded_space(tmp_path: Path) -> None:
    """A %20-encoded image path matches a session path with a space."""
    with _fuse_dir(suffix=" with space") as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        encoded = str(fuse / "dislocker-file").replace(" ", "%20")
        run = _detach_responder(info_images=[_image_entry(encoded, "/dev/disk9")])
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert _detach_calls(run) == [[_HDIUTIL, "detach", "/dev/disk9"]]


def test_detach_does_not_decode_session_percent(tmp_path: Path) -> None:
    """A literal %20 in the session path is compared literally."""
    with _fuse_dir(suffix="-100%20x") as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        # hdiutil encodes the literal "%" as %25; decoding once must recover it.
        encoded = str(fuse / "dislocker-file").replace("100%20x", "100%2520x")
        run = _detach_responder(info_images=[_image_entry(encoded, "/dev/disk9")])
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert _detach_calls(run) == [[_HDIUTIL, "detach", "/dev/disk9"]]


def test_detach_matches_partition_node(tmp_path: Path) -> None:
    """A /dev/diskNsM session node is found among the mapped entities."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse), raw_disk="/dev/disk9s2"))
        entry = _image_entry(str(fuse / "dislocker-file"), "/dev/disk9", "/dev/disk9s2")
        run = _detach_responder(info_images=[entry])
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert _detach_calls(run) == [[_HDIUTIL, "detach", "/dev/disk9s2"]]


def test_detach_errors_when_info_unavailable(tmp_path: Path) -> None:
    """An unreadable image list retains state and detaches nothing."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        logs: list[str] = []
        run = _detach_responder(info_rc=1)
        with (
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            pytest.raises(RunnerError, match="Could not verify"),
        ):
            _unmount_session_file(stored, logs)
        assert stored.exists()
        assert _detach_calls(run) == []
        run2 = _detach_responder(info_error=OSError("no hdiutil"))
        with (
            patch("dislocker_ui.unmount_steps.subprocess.run", run2),
            pytest.raises(RunnerError, match="Could not verify"),
        ):
            _unmount_session_file(stored, logs)
        assert stored.exists()
        assert _detach_calls(run2) == []


def test_best_effort_cleanup_skips_unverified_detach(tmp_path: Path) -> None:
    """Cleanup warns loudly and leaves an unverified disk attached."""
    with _fuse_dir() as fuse:
        marker = tmp_path / "active_session.json"
        marker.write_text("{}")
        logs: list[str] = []
        others = [_image_entry("/Applications/Other.dmg", "/dev/disk5")]
        run = _detach_responder(info_images=others)
        with patch("dislocker_ui.runner.subprocess.run", run):
            _best_effort_cleanup(
                fuse, tmp_path / "Nope", "/dev/disk9", logs.append, session_path=marker
            )
    assert not marker.exists()
    assert _detach_calls(run) == []
    assert any("left attached" in line for line in logs)


def test_best_effort_cleanup_detaches_verified_image(tmp_path: Path) -> None:
    """Cleanup force-detaches a disk verified as our image."""
    with _fuse_dir() as fuse:
        marker = tmp_path / "active_session.json"
        marker.write_text("{}")
        logs: list[str] = []
        entry = _image_entry(str(fuse / "dislocker-file"), "/dev/disk9")
        run = _detach_responder(info_images=[entry])
        with patch("dislocker_ui.runner.subprocess.run", run):
            _best_effort_cleanup(
                fuse, tmp_path / "Nope", "/dev/disk9", logs.append, session_path=marker
            )
    assert not marker.exists()
    assert _detach_calls(run) == [["/usr/bin/hdiutil", "detach", "-force", "/dev/disk9"]]
