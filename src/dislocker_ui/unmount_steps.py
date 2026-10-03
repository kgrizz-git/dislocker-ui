"""
Unmount steps for dislocker-ui.

Overall purpose:
  Implement the individual Unmount stages (NTFS umount, raw-disk detach,
  FUSE umount, staging and mountpoint cleanup) plus the shared subprocess
  helpers they are built on. The orchestration facade (unmount_volume)
  stays in runner.py and calls these step functions.

Inputs:
  DepsStatus, MountSession, log fn.

Outputs:
  Per-step lists of error strings (empty means the step succeeded or the
  target was already in the desired end state); side-effect umount/detach
  /rmdir calls. Raised RunnerError on subprocess failure when check is
  requested.

Requirements:
  umount, hdiutil, diskutil paths from DepsStatus; standard library only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from dislocker_ui.deps import DepsStatus
from dislocker_ui.mount_policy import (
    is_privileged_fuse_path,
    remove_empty_privileged_staging_parents,
)
from dislocker_ui.session import MountSession

LogFn = Callable[[str], None]


class RunnerError(RuntimeError):
    """Raised when a mount/unmount step fails."""


def _run(
    cmd: list[str],
    log: LogFn,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, log argv, optionally raise RunnerError on failure."""
    log(" ".join(cmd))
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.stdout.strip():
        log(proc.stdout.strip())
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        if check:
            raise RunnerError(err)
        log(err)
    return proc


def _run_with_fallback(
    primary: list[str],
    fallback: list[str],
    log: LogFn,
) -> list[str]:
    """Try *primary*, then *fallback* on failure; return any error messages."""
    errors: list[str] = []
    try:
        _run(primary, log, check=True)
    except RunnerError as exc:
        errors.append(str(exc))
        try:
            _run(fallback, log, check=True)
        except RunnerError as exc2:
            errors.append(str(exc2))
    return errors


def _mounted_paths() -> set[str] | None:
    """Parse ``/sbin/mount`` output into mounted paths; None when unavailable."""
    try:
        proc = subprocess.run(["/sbin/mount"], check=False, capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    paths: set[str] = set()
    for line in proc.stdout.splitlines():
        # "<dev> on <path> (<fstype>, ...)"; our paths are app-generated, so
        # splitting on these separators is safe.
        _, on_sep, rest = line.partition(" on ")
        if not on_sep:
            continue
        pieces = rest.rsplit(" (", 1)
        if len(pieces) != 2 or not pieces[0]:
            continue
        paths.add(pieces[0])
    return paths


def _is_mounted(path: str) -> bool:
    """Return whether *path* appears in the mount table.

    The mount table (not stat) is the source of truth: a dead FUSE mount
    answers stat with ENXIO/EIO, so stat-based checks would misreport it as
    unmounted, and a wedged daemon could hang the stat outright. When the
    table itself is unavailable, assume the target is mounted.
    """
    mounted = _mounted_paths()
    if mounted is None:
        return True
    if path in mounted:
        return True
    # Tolerate /tmp vs /private/tmp style aliases by resolving the parent
    # only; the target itself is never statted.
    parent = os.path.dirname(path)
    aliased = os.path.join(os.path.realpath(parent), os.path.basename(path))
    return aliased in mounted


def _unmount_ntfs(deps: DepsStatus, session: MountSession, log: LogFn) -> list[str]:
    """Unmount the decrypted volume mountpoint, forcing via diskutil if needed."""
    if not _is_mounted(session.ntfs_mount):
        log(f"already unmounted: {session.ntfs_mount}")
        return []
    log(f"Unmounting volume at {session.ntfs_mount}…")
    return _run_with_fallback(
        [deps.umount, session.ntfs_mount],
        [deps.diskutil or "/usr/sbin/diskutil", "unmount", "force", session.ntfs_mount],
        log,
    )


def _detach_raw_disk(deps: DepsStatus, session: MountSession, log: LogFn) -> list[str]:
    """Detach the hdiutil raw disk, forcing if needed."""
    log(f"Detaching {session.raw_disk}…")
    return _run_with_fallback(
        [deps.hdiutil, "detach", session.raw_disk],
        [deps.hdiutil, "detach", "-force", session.raw_disk],
        log,
    )


def _unmount_fuse(deps: DepsStatus, session: MountSession, log: LogFn) -> list[str]:
    """Unmount the dislocker FUSE mount point."""
    if not _is_mounted(session.fuse_mount):
        log(f"already unmounted: {session.fuse_mount}")
        return []
    log(f"Unmounting FUSE at {session.fuse_mount}…")
    try:
        _run([deps.umount, session.fuse_mount], log, check=True)
        return []
    except RunnerError as exc:
        return [str(exc)]


def _remove_fuse_dir(
    fuse_mount: str, *, elevated: bool = False, session_path: Path | None = None
) -> list[str]:
    """Remove the temporary FUSE mount directory if present."""
    fuse_path = Path(fuse_mount)
    if elevated and not is_privileged_fuse_path(fuse_path, session_path):
        raise RunnerError("Refusing to remove a FUSE path outside privileged staging")
    try:
        if fuse_path.is_dir():
            shutil.rmtree(fuse_path)
    except OSError as exc:
        return [f"Could not remove FUSE staging directory {fuse_path}: {exc}"]
    if elevated:
        remove_empty_privileged_staging_parents(fuse_path, session_path)
    return []


def _remove_empty_ntfs_dir(ntfs_mount: str, log: LogFn) -> list[str]:
    """Remove an empty /Volumes mount-point directory left after unmount.

    A non-empty directory that is confirmed unmounted is left in place with
    a warning (stale contents need operator inspection); only a directory
    that is still mounted retains the session for retry.
    """
    ntfs_path = Path(ntfs_mount)
    if not ntfs_path.is_dir():
        return []
    try:
        if not any(ntfs_path.iterdir()):
            ntfs_path.rmdir()
        elif not _is_mounted(ntfs_mount):
            log(f"Warning: leaving non-empty unmounted directory {ntfs_path} for inspection")
            return []
        elif ntfs_path.exists():
            return [f"NTFS mountpoint is not empty: {ntfs_path}"]
    except OSError as exc:
        return [f"Could not remove NTFS mountpoint {ntfs_path}: {exc}"]
    return []
