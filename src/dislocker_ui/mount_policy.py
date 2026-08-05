"""Shared validation policy for privileged mount inputs and generated paths."""

from __future__ import annotations

import contextlib
import os
import re
import stat
import tempfile
from pathlib import Path

from dislocker_ui.session import MountSession

VOLUMES_ROOT = Path("/Volumes")
PHYSICAL_VOLUME_RE = re.compile(r"^/dev/disk\d+(s\d+)?$")
VOLUME_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


def is_physical_volume(value: object) -> bool:
    """Return whether *value* is a permitted physical macOS disk selector."""
    return isinstance(value, str) and bool(PHYSICAL_VOLUME_RE.fullmatch(value))


def is_safe_volume_label(value: object) -> bool:
    """Return whether *value* can name a direct child of ``/Volumes``."""
    return (
        isinstance(value, str)
        and bool(VOLUME_LABEL_RE.fullmatch(value))
        and value not in {".", ".."}
    )


def privileged_staging_dir(session_path: Path, uid: int) -> Path:
    """Return the root-owned staging directory for one user's session state."""
    return session_path.parent.parent / "staging" / str(uid)


def elevated_request_error(volume: object, readonly: object, label: object) -> str | None:
    """Return an elevated-request validation error, if any."""
    if not is_physical_volume(volume):
        return "Elevated mounts require a physical /dev/diskN or /dev/diskNsM device"
    if type(readonly) is not bool:
        return "readonly must be boolean"
    if not is_safe_volume_label(label):
        return "Volume label must be a short filesystem-safe display name"
    return None


def allocate_privileged_fuse_path(session_path: Path, uid: int) -> Path:
    """Create a private root-helper FUSE directory below canonical state."""
    staging = privileged_staging_dir(session_path, uid)
    _ensure_private_staging_dir(staging.parent)
    _ensure_private_staging_dir(staging)
    return Path(tempfile.mkdtemp(prefix="session-", dir=str(staging)))


def is_privileged_fuse_path(path: Path, session_path: Path | None) -> bool:
    """Return whether *path* is a direct, non-symlink staging child."""
    if session_path is None:
        return False
    try:
        staging = privileged_staging_dir(session_path, int(session_path.parent.name))
        return path.parent == staging and path.name.startswith("session-") and not path.is_symlink()
    except (OSError, ValueError):
        return False


def remove_empty_privileged_staging_parents(fuse_path: Path, session_path: Path | None) -> None:
    """Best-effort removal of now-empty, derived staging directories."""
    if session_path is None:
        return
    try:
        staging = privileged_staging_dir(session_path, int(session_path.parent.name))
    except ValueError:
        return
    if fuse_path.parent != staging:
        return
    with contextlib.suppress(OSError):
        staging.rmdir()
    with contextlib.suppress(OSError):
        staging.parent.rmdir()


def elevated_session_error(session: MountSession, session_path: Path | None) -> str | None:
    """Return an elevated-session cleanup validation error, if any."""
    if not is_physical_volume(session.volume) or not is_physical_volume(session.raw_disk):
        return "Privileged session has an invalid device selector"
    if not is_privileged_fuse_path(Path(session.fuse_mount), session_path):
        return "Privileged session FUSE path is outside application staging"
    ntfs = Path(session.ntfs_mount)
    if ntfs.parent != VOLUMES_ROOT or ntfs.name in {"", ".", ".."}:
        return "Privileged session NTFS mountpoint is unsafe"
    return None


def _ensure_private_staging_dir(path: Path) -> None:
    """Create and verify one private directory for the privileged helper."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise RuntimeError("Privileged staging directory ownership or type is unsafe")
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- root-helper staging must not be readable by other users
    os.chmod(path, 0o700)
