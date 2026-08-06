"""
FAT / ExFAT mount helpers for dislocker-ui.

Overall purpose:
  Mount a decrypted BitLocker image whose inner filesystem is MS-DOS FAT or
  ExFAT using the system ``mount_msdos`` / ``mount_exfat`` helpers (not
  ntfs-3g).

Inputs:
  MountRequest-like object, raw disk node, mountpoint, optional uid/gid, log.

Outputs:
  Side-effect mount; returns False for ``used_ntfs3g`` session flag.

Requirements:
  macOS ``/sbin/mount_msdos`` and ``/sbin/mount_exfat``; standard library.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from dislocker_ui.ntfs_mount import resolve_mount_owner

LogFn = Callable[[str], None]

_MOUNT_MSDOS = "/sbin/mount_msdos"
_MOUNT_EXFAT = "/sbin/mount_exfat"


class _MountReq(Protocol):
    """Structural shape of the request fields mount_fat needs."""

    readonly: bool


def mount_fat(
    req: _MountReq,
    raw_disk: str,
    mountpoint: Path,
    log: LogFn,
    *,
    kind: str,
    uid: int | None = None,
    gid: int | None = None,
) -> bool:
    """
    Mount *raw_disk* as FAT (msdos) or ExFAT at *mountpoint*.

    *kind* must be ``msdos`` or ``exfat``. Always returns False (ntfs-3g not
    used) so callers can store it on ``MountSession.used_ntfs3g``.
    """
    from dislocker_ui.runner import _run

    helper = _helper_for_kind(kind)
    owner_uid, owner_gid = resolve_mount_owner(uid, gid, log=log)
    mode = "read-only" if req.readonly else "read/write"
    label = "FAT" if kind == "msdos" else "ExFAT"
    log(f"Mounting {label} {mode} via {helper} at {mountpoint}…")
    mountpoint.mkdir(parents=True, exist_ok=True)

    cmd = [helper, "-u", str(owner_uid), "-g", str(owner_gid), "-m", "077"]
    if req.readonly:
        cmd.extend(["-o", "rdonly"])
    cmd.extend([raw_disk, str(mountpoint)])
    _run(cmd, log)
    return False


def _helper_for_kind(kind: str) -> str:
    """Return the absolute mount helper path for *kind*."""
    from dislocker_ui.runner import RunnerError

    if kind == "msdos":
        return _MOUNT_MSDOS
    if kind == "exfat":
        return _MOUNT_EXFAT
    raise RunnerError(f"Unsupported non-NTFS filesystem kind: {kind}")
