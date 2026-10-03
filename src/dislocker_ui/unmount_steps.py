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
  Per-step lists of error strings (empty means the step succeeded);
  side-effect umount/detach/rmdir calls. Raised RunnerError on
  subprocess failure when check is requested.

Requirements:
  umount, hdiutil, diskutil paths from DepsStatus; standard library only.
"""

from __future__ import annotations

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


def _unmount_ntfs(deps: DepsStatus, session: MountSession, log: LogFn) -> list[str]:
    """Unmount the decrypted volume mountpoint, forcing via diskutil if needed."""
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


def _remove_empty_ntfs_dir(ntfs_mount: str) -> list[str]:
    """Remove an empty /Volumes mount-point directory left after unmount."""
    ntfs_path = Path(ntfs_mount)
    if not ntfs_path.is_dir():
        return []
    try:
        if not any(ntfs_path.iterdir()):
            ntfs_path.rmdir()
        elif ntfs_path.exists():
            return [f"NTFS mountpoint is not empty: {ntfs_path}"]
    except OSError as exc:
        return [f"Could not remove NTFS mountpoint {ntfs_path}: {exc}"]
    return []
