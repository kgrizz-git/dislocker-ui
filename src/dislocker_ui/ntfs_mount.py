"""
NTFS mount helpers for dislocker-ui.

Overall purpose:
  Build ntfs-3g option strings, resolve mount ownership (uid/gid), run the
  ntfs-3g mount, and assert post-mount ownership.

Inputs:
  DepsStatus, MountRequest, optional explicit uid/gid, mountpoint path, log fn.

Outputs:
  Option strings, owner tuples; side-effect mounts; ownership assertions.

Requirements:
  ntfs-3g binary; standard library only.
"""

from __future__ import annotations

import os
import pwd
import re
from collections.abc import Callable
from pathlib import Path

from dislocker_ui.deps import DepsStatus

LogFn = Callable[[str], None]


def assert_mount_owner(
    ntfs_mount: Path,
    *,
    uid: int | None,
    gid: int | None,
    log: LogFn,
) -> None:
    """Abort if the mountpoint is not owned by the expected uid."""
    from dislocker_ui.runner import RunnerError

    if uid is None:
        return
    try:
        st = ntfs_mount.stat()
    except OSError as exc:
        raise RunnerError(f"Could not stat mountpoint {ntfs_mount}: {exc}") from exc
    if st.st_uid != uid:
        raise RunnerError(
            f"Post-mount ownership mismatch at {ntfs_mount}: "
            f"expected uid={uid}, got uid={st.st_uid}"
        )
    if gid is not None and st.st_gid != gid:
        log(f"Warning: mountpoint gid={st.st_gid} != expected {gid}")


def resolve_mount_owner(
    uid: int | None = None,
    gid: int | None = None,
    *,
    log: LogFn | None = None,
) -> tuple[int, int]:
    """
    Resolve uid/gid for ntfs-3g ownership options.

    Explicit values (elevated parent request) win. Otherwise use SUDO_UID /
    SUDO_GID when set and valid; fall back to 0/0 with a warning (never refuse).
    """
    if uid is not None and gid is not None:
        return uid, gid

    sudo_uid = _parse_sudo_id(os.environ.get("SUDO_UID"))
    sudo_gid = _parse_sudo_id(os.environ.get("SUDO_GID"))
    if sudo_uid is not None and sudo_gid is not None and _pwd_resolves(sudo_uid):
        return sudo_uid, sudo_gid

    if log is not None:
        log(
            "Warning: mounting as uid=0/gid=0 (no SUDO_UID/SUDO_GID). "
            "You may need sudo to write to an RW mount."
        )
    return 0, 0


def _parse_sudo_id(raw: str | None) -> int | None:
    """Parse a SUDO_UID/SUDO_GID value; return None if missing or invalid."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pwd_resolves(uid: int) -> bool:
    """Return True when *uid* maps to a passwd entry."""
    try:
        pwd.getpwuid(uid)
        return True
    except KeyError:
        return False


def ntfs3g_options(
    *,
    readonly: bool,
    uid: int,
    gid: int,
    volume_label: str,
) -> str:
    """Build the comma-separated -o option string for ntfs-3g."""
    label = re.sub(r"[,=\0]", "_", volume_label) or "DislockerUI"
    parts: list[str] = []
    if readonly:
        parts.append("ro")
    parts.extend(
        [
            "allow_other",
            "local",
            f"uid={uid}",
            f"gid={gid}",
            "umask=077",
            "fmask=177",
            "dmask=077",
            f"volname={label}",
        ]
    )
    return ",".join(parts)


def mount_ntfs(
    deps: DepsStatus,
    req: object,
    raw_disk: str,
    ntfs_mount: Path,
    log: LogFn,
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> bool:
    """
    Mount the attached raw disk via ntfs-3g (always).

    *req* is a MountRequest-like object with readonly and volume_label.
    Always returns True (ntfs-3g used).
    """
    from dislocker_ui.runner import RunnerError, _run

    if not deps.ntfs3g:
        raise RunnerError("ntfs-3g is required to mount BitLocker NTFS volumes")

    owner_uid, owner_gid = resolve_mount_owner(uid, gid, log=log)
    opts = ntfs3g_options(
        readonly=bool(req.readonly),  # type: ignore[attr-defined]
        uid=owner_uid,
        gid=owner_gid,
        volume_label=str(req.volume_label),  # type: ignore[attr-defined]
    )
    mode = "read-only" if req.readonly else "read/write"  # type: ignore[attr-defined]
    log(f"Mounting NTFS {mode} via ntfs-3g at {ntfs_mount}…")
    ntfs_mount.mkdir(parents=True, exist_ok=True)
    _run([deps.ntfs3g, raw_disk, str(ntfs_mount), "-o", opts], log)
    return True
