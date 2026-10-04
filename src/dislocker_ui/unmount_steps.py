"""
Unmount steps for dislocker-ui.

Overall purpose:
  Implement the individual Unmount stages (NTFS umount, raw-disk detach,
  FUSE umount, staging and mountpoint cleanup) plus the shared subprocess
  helpers they are built on. This module is the shared step and identity
  layer: mount-table liveness, disk-image identity, and the boolean
  helpers `session_targets_gone` / `session_looks_stale` used by mount
  validation and the GUI. The orchestration facades (mount/unmount_volume)
  stay in runner.py and call these step functions.

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
import plistlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

from dislocker_ui.deps import DepsStatus
from dislocker_ui.mount_policy import (
    is_privileged_fuse_path,
    remove_empty_privileged_staging_parents,
    session_paths_error,
)
from dislocker_ui.session import MountSession

HDIUTIL = "/usr/bin/hdiutil"

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
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- argv list, no shell; executables come from trusted deps
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
    """Try *primary*, then *fallback* on failure; return any error messages.

    A fallback that succeeds clears the primary error (kept in the log);
    only failures are returned.
    """
    try:
        _run(primary, log, check=True)
    except RunnerError as exc:
        log(f"Primary command failed ({exc}); trying fallback…")
        try:
            _run(fallback, log, check=True)
        except RunnerError as exc2:
            return [str(exc), str(exc2)]
    return []


def _mount_table() -> dict[str, str] | None:
    """Parse ``/sbin/mount`` into {mountpoint: device}; None when unavailable."""
    try:
        proc = subprocess.run(
            ["/sbin/mount"], check=False, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    table: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        # "<dev> on <path> (<fstype>, ...)"; our paths are app-generated, so
        # splitting on these separators is safe.
        dev, on_sep, rest = line.partition(" on ")
        if not on_sep:
            continue
        pieces = rest.rsplit(" (", 1)
        if len(pieces) != 2 or not pieces[0]:
            continue
        table[pieces[0]] = dev
    return table


def _mounted_paths() -> set[str] | None:
    """Return the mounted paths; None when the table is unavailable."""
    table = _mount_table()
    return set(table) if table is not None else None


def _is_mounted(path: str) -> bool:
    """Return whether *path* appears in the mount table.

    The mount table (not stat) is the source of truth: a dead FUSE mount
    answers stat with ENXIO/EIO, so stat-based checks would misreport it as
    unmounted, and a wedged daemon could hang the stat outright. When the
    table itself is unavailable, assume the target is mounted.
    """
    return _is_mounted_in(_mount_table(), path)


def _is_mounted_in(table: dict[str, str] | None, path: str) -> bool:
    """Membership test against an already-read table; None means mounted."""
    if table is None:
        return True
    if path in table:
        return True
    # Tolerate /tmp vs /private/tmp style aliases by resolving the parent
    # only; the target itself is never statted.
    parent = os.path.dirname(path)
    aliased = os.path.join(os.path.realpath(parent), os.path.basename(path))
    return aliased in table


def _unmount_ntfs(deps: DepsStatus, session: MountSession, log: LogFn) -> list[str]:
    """Unmount the decrypted volume mountpoint, forcing via diskutil if needed."""
    table = _mount_table()
    if not _is_mounted_in(table, session.ntfs_mount):
        log(f"already unmounted: {session.ntfs_mount}")
        return []
    # A reassigned /Volumes mountpoint may now belong to another disk;
    # unmounting it would yank someone else's volume (non-/dev devices from
    # ntfs-3g/macFUSE carry no device number and keep the old behavior).
    device = (table or {}).get(session.ntfs_mount)
    if (
        device is not None
        and device.startswith("/dev/disk")
        and device != session.raw_disk
        and not device.startswith(session.raw_disk + "s")
    ):
        log(f"{session.ntfs_mount} is now a different disk ({device}); not unmounting")
        return [f"NTFS mountpoint {session.ntfs_mount} is now a different disk ({device})"]
    log(f"Unmounting volume at {session.ntfs_mount}…")
    return _run_with_fallback(
        [deps.umount, session.ntfs_mount],
        [deps.diskutil or "/usr/sbin/diskutil", "unmount", "force", session.ntfs_mount],
        log,
    )


def _attached_disk_images(hdiutil: str) -> dict[str, str] | None:
    """Map every attached disk-image device to its decoded image path.

    Covers each ``images[].system-entities[].dev-entry`` (whole disks and
    partition nodes alike). Returns None when the listing is unavailable.
    """
    try:
        # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- argv list, no shell; executables come from trusted deps
        proc = subprocess.run(
            [hdiutil, "info", "-plist"], check=False, capture_output=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        info = plistlib.loads(proc.stdout)
    except Exception:
        return None
    if not isinstance(info, dict) or not isinstance(info.get("images"), list):
        return None
    return _image_device_map(info["images"])


def _image_device_map(images: list[object]) -> dict[str, str]:
    """Map each image's system-entity devices to its decoded image path."""
    found: dict[str, str] = {}
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("image-path"), str):
            continue
        # Percent-decoding applies to the hdiutil side only; session paths
        # are compared literally so a real "%" is never mis-decoded.
        decoded = unquote(image["image-path"])
        entities = image.get("system-entities")
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if isinstance(entity, dict) and isinstance(entity.get("dev-entry"), str):
                found[entity["dev-entry"]] = decoded
    return found


def _expected_image_path(fuse_mount: str, dislocker_file: str) -> str:
    """Expected on-disk image path, without statting inside the FUSE mount.

    Resolves only the temp dir itself, then re-appends the last two name
    parts (``<fuse dir>/<image file>``).
    """
    fuse = Path(fuse_mount)
    return os.path.join(os.path.realpath(fuse.parent), fuse.name, Path(dislocker_file).name)


def _normalized_hdiutil_image_path(image_path: str) -> str:
    """Normalize an hdiutil image path the same way, via its grandparent."""
    path = Path(image_path)
    return os.path.join(os.path.realpath(path.parent.parent), path.parent.name, path.name)


def _image_identity_ok(images: dict[str, str], raw_disk: str, session: MountSession) -> bool:
    """Return whether *raw_disk* is attached as this session's own image."""
    actual = images.get(raw_disk)
    if actual is None:
        return False
    expected = _expected_image_path(session.fuse_mount, session.dislocker_file)
    return _normalized_hdiutil_image_path(actual) == expected


def session_targets_gone(
    session: MountSession, hdiutil: str, session_path: Path | None = None
) -> bool:
    """Return whether every recorded target is verifiably gone.

    All three checks must agree: the session passes path validation (the
    root child supplies its canonical session path here), both mounts are
    absent from a successfully read mount table, and the raw disk is not
    attached as our image per a successfully read image list. Unknown
    table/list state, or a session failing validation, is never stale.
    """
    if session_paths_error(session, session_path) is not None:
        return False
    table = _mount_table()
    if _is_mounted_in(table, session.ntfs_mount) or _is_mounted_in(table, session.fuse_mount):
        return False
    images = _attached_disk_images(hdiutil)
    if images is None:
        return False
    return not _image_identity_ok(images, session.raw_disk, session)


def session_looks_stale(session: MountSession, session_path: Path | None = None) -> bool:
    """Advisory staleness hint for GUI status; never raises."""
    try:
        return session_targets_gone(session, HDIUTIL, session_path)
    except Exception:
        return False


def _detach_raw_disk(deps: DepsStatus, session: MountSession, log: LogFn) -> list[str]:
    """Detach the hdiutil raw disk, but only after verifying its identity.

    A device number recorded before a reboot may now belong to an unrelated
    disk; detaching it blind would eject someone else's drive.
    """
    images = _attached_disk_images(deps.hdiutil or HDIUTIL)
    if images is None:
        return [f"Could not verify {session.raw_disk} is still our disk image; not detaching"]
    if not _image_identity_ok(images, session.raw_disk, session):
        log(f"raw disk {session.raw_disk} no longer attached as our image; skipping detach")
        return []
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
