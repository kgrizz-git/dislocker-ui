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

import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from dislocker_ui.mount_policy import PHYSICAL_VOLUME_RE
from dislocker_ui.ntfs_mount import resolve_mount_owner

LogFn = Callable[[str], None]

_MOUNT_MSDOS = "/sbin/mount_msdos"
_MOUNT_EXFAT = "/sbin/mount_exfat"
_MAX_POSIX_ID = 0x7FFFFFFF
# Absolute path with safe path characters only (no shell metacharacters).
_SAFE_ABS_PATH_RE = re.compile(r"^(/[A-Za-z0-9._-]+)+$")


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

    ``-m 700`` is a *permission mode* (chmod-style), not a umask: ``077`` would
    leave the owner with no bits and Finder shows a red/empty volume.

    Disk, mountpoint, and uid/gid are validated/sanitized before building argv
    so only allow-listed values reach ``subprocess`` (Sonar pythonsecurity:S8705).
    """
    from dislocker_ui.runner import _run

    helper = _helper_for_kind(kind)
    safe_disk = _require_physical_disk(raw_disk)
    safe_mount = _require_safe_mountpoint(mountpoint)
    owner_uid, owner_gid = resolve_mount_owner(uid, gid, log=log)
    safe_uid = _require_posix_id(owner_uid, "uid")
    safe_gid = _require_posix_id(owner_gid, "gid")

    mode = "read-only" if req.readonly else "read/write"
    label = "FAT" if kind == "msdos" else "ExFAT"
    log(f"Mounting {label} {mode} via {helper} at {safe_mount}…")
    safe_mount.mkdir(parents=True, exist_ok=True)

    # -m is max permission bits (like 700), NOT umask (ntfs-3g's umask=077).
    # All argv tails below are freshly built from sanitized locals only.
    cmd = [helper, "-u", safe_uid, "-g", safe_gid, "-m", "700"]
    if req.readonly:
        cmd.extend(["-o", "rdonly"])
    cmd.extend([safe_disk, str(safe_mount)])
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


def _require_physical_disk(raw_disk: str) -> str:
    """Return *raw_disk* only when it is a physical ``/dev/diskN[sM]`` path."""
    from dislocker_ui.runner import RunnerError

    if not isinstance(raw_disk, str):
        raise RunnerError(f"Refusing FAT mount for non-physical disk path: {raw_disk!r}")
    match = PHYSICAL_VOLUME_RE.fullmatch(raw_disk)
    if match is None:
        raise RunnerError(f"Refusing FAT mount for non-physical disk path: {raw_disk!r}")
    # Matched substring is treated as sanitized by static taint analysis.
    return match.group(0)


def _require_safe_mountpoint(mountpoint: Path) -> Path:
    """
    Return an absolute mountpoint whose path contains only safe characters.

    Production mounts are under ``/Volumes``; tests may use a temp directory.
    Reject relative paths, ``..`` segments, and shell-metacharacter-bearing
    names before the path reaches ``subprocess``.
    """
    from dislocker_ui.runner import RunnerError

    if not isinstance(mountpoint, Path):
        raise RunnerError(f"Invalid FAT mountpoint type: {type(mountpoint)!r}")
    try:
        resolved = mountpoint.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RunnerError(f"Invalid FAT mountpoint: {mountpoint}") from exc
    if not resolved.is_absolute():
        raise RunnerError(f"FAT mountpoint must be absolute: {resolved}")
    text = resolved.as_posix()
    match = _SAFE_ABS_PATH_RE.fullmatch(text)
    if match is None:
        raise RunnerError(f"FAT mountpoint has unsafe characters: {text!r}")
    return Path(match.group(0))


def _require_posix_id(value: int, label: str) -> str:
    """Return a decimal string for a non-negative POSIX uid/gid."""
    from dislocker_ui.runner import RunnerError

    if not isinstance(value, int) or isinstance(value, bool):
        raise RunnerError(f"Invalid mount {label}: {value!r}")
    if value < 0 or value > _MAX_POSIX_ID:
        raise RunnerError(f"Mount {label} out of range: {value}")
    # Rebuild from a bounded int so the argv string is not a tainted reference.
    return f"{int(value):d}"
