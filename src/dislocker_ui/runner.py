"""
Mount/unmount orchestration for dislocker-ui.

Overall purpose:
  Drive the macOS sequence: dislocker-fuse → hdiutil attach → NTFS mount,
  and the reverse for Unmount. Commands are logged via a callback; secrets are
  never written to the log.

Inputs:
  MountRequest (volume, unlock method, credentials, readonly).
  DepsStatus from deps.discover_deps().

Outputs:
  MountSession on success; raised RunnerError on failure.
  Side effects: FUSE mount, raw disk attach, /Volumes mount, session file.

Requirements:
  dislocker-fuse, hdiutil, mount/umount; optional ntfs-3g for writable mounts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from dislocker_ui.deps import DepsStatus
from dislocker_ui.session import MountSession, clear_session, load_session, save_session

LogFn = Callable[[str], None]


class UnlockMethod(str, Enum):
    """How BitLocker keys are unlocked."""

    USER_PASSWORD = "user_password"
    RECOVERY_PASSWORD = "recovery_password"
    BEK_FILE = "bek_file"


@dataclass
class MountRequest:
    """User choices for a Mount operation."""

    volume: str
    method: UnlockMethod
    secret: str
    readonly: bool
    volume_label: str = "DislockerUI"


class RunnerError(RuntimeError):
    """Raised when a mount/unmount step fails."""


def mount_volume(
    req: MountRequest,
    deps: DepsStatus,
    log: LogFn,
) -> MountSession:
    """
    Unlock and mount a BitLocker volume for Finder access.

    Starts dislocker-fuse in the background, attaches the virtual NTFS file as
    a raw disk, then mounts it read-only (stock ntfs) or read-write (ntfs-3g).
    """
    if not deps.core_ok:
        missing = ", ".join(deps.missing_core())
        raise RunnerError(f"Missing required tools: {missing}")

    volume = req.volume.strip()
    if not volume:
        raise RunnerError("Volume path is empty")
    if not Path(volume).exists():
        raise RunnerError(f"Volume path does not exist: {volume}")

    if req.readonly is False and not deps.can_write:
        raise RunnerError(
            "Read/write requested but ntfs-3g was not found. "
            "Install ntfs-3g (and a FUSE backend) or leave Read-only checked."
        )

    if req.method == UnlockMethod.BEK_FILE:
        bek = Path(req.secret).expanduser()
        if not bek.is_file():
            raise RunnerError(f"BEK file not found: {bek}")
    elif not req.secret:
        raise RunnerError("Password / recovery password is empty")

    existing = load_session()
    if existing is not None:
        raise RunnerError(
            "A session is already active. Click Unmount before mounting again.\n"
            f"NTFS mount: {existing.ntfs_mount}"
        )

    fuse_mount = Path(tempfile.mkdtemp(prefix="dislocker-ui-", dir="/tmp"))
    ntfs_mount = _allocate_volume_path(req.volume_label)
    fuse_proc: Optional[subprocess.Popen[str]] = None
    raw_disk: Optional[str] = None

    try:
        cmd = _build_dislocker_cmd(deps, req, fuse_mount)
        log("Starting dislocker-fuse…")
        log(_redact_cmd(cmd))
        fuse_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        dislocker_file = fuse_mount / "dislocker-file"
        _wait_for_file(dislocker_file, fuse_proc, log, timeout_s=45)

        log("Attaching raw NTFS image with hdiutil…")
        raw_disk = _hdiutil_attach(deps, dislocker_file, log)
        log(f"Attached as {raw_disk}")

        ntfs_mount.mkdir(parents=True, exist_ok=True)
        used_ntfs3g = False
        if req.readonly:
            log(f"Mounting NTFS read-only at {ntfs_mount}…")
            _run(
                [deps.mount, "-t", "ntfs", "-o", "rdonly", raw_disk, str(ntfs_mount)],
                log,
            )
        else:
            assert deps.ntfs3g
            log(f"Mounting NTFS read/write via ntfs-3g at {ntfs_mount}…")
            _run([deps.ntfs3g, raw_disk, str(ntfs_mount)], log)
            used_ntfs3g = True

        session = MountSession(
            volume=volume,
            fuse_mount=str(fuse_mount),
            dislocker_file=str(dislocker_file),
            raw_disk=raw_disk,
            ntfs_mount=str(ntfs_mount),
            readonly=req.readonly,
            used_ntfs3g=used_ntfs3g,
        )
        save_session(session)
        log(f"Mounted successfully at {ntfs_mount}")
        return session

    except Exception:
        log("Mount failed — attempting cleanup…")
        _best_effort_cleanup(fuse_mount, ntfs_mount, raw_disk=raw_disk, log=log)
        if fuse_proc is not None and fuse_proc.poll() is None:
            fuse_proc.terminate()
        raise


def unmount_volume(deps: DepsStatus, log: LogFn) -> None:
    """
    Reverse the last MountSession: umount NTFS, detach raw disk, umount FUSE.
    """
    session = load_session()
    if session is None:
        raise RunnerError("No active session found to unmount")

    errors: list[str] = []

    log(f"Unmounting NTFS at {session.ntfs_mount}…")
    try:
        _run([deps.umount, session.ntfs_mount], log, check=True)
    except RunnerError as exc:
        errors.append(str(exc))
        try:
            _run(["diskutil", "unmount", "force", session.ntfs_mount], log, check=True)
        except RunnerError as exc2:
            errors.append(str(exc2))

    log(f"Detaching {session.raw_disk}…")
    try:
        _run([deps.hdiutil, "detach", session.raw_disk], log, check=True)
    except RunnerError as exc:
        errors.append(str(exc))
        try:
            _run([deps.hdiutil, "detach", "-force", session.raw_disk], log, check=True)
        except RunnerError as exc2:
            errors.append(str(exc2))

    log(f"Unmounting FUSE at {session.fuse_mount}…")
    try:
        _run([deps.umount, session.fuse_mount], log, check=True)
    except RunnerError as exc:
        errors.append(str(exc))

    fuse_path = Path(session.fuse_mount)
    if fuse_path.is_dir():
        shutil.rmtree(fuse_path, ignore_errors=True)

    ntfs_path = Path(session.ntfs_mount)
    if ntfs_path.is_dir():
        try:
            if not any(ntfs_path.iterdir()):
                ntfs_path.rmdir()
        except OSError:
            pass

    clear_session()
    if errors:
        log("Unmount finished with warnings:\n" + "\n".join(errors))
    else:
        log("Unmounted successfully")


def _build_dislocker_cmd(
    deps: DepsStatus,
    req: MountRequest,
    fuse_mount: Path,
) -> list[str]:
    """Assemble the dislocker-fuse argv (includes secret; do not log raw)."""
    assert deps.dislocker_fuse
    cmd = [deps.dislocker_fuse, "-V", req.volume]
    if req.readonly:
        cmd.append("-r")

    if req.method == UnlockMethod.USER_PASSWORD:
        cmd.append(f"--user-password={req.secret}")
    elif req.method == UnlockMethod.RECOVERY_PASSWORD:
        cmd.append(f"--recovery-password={req.secret}")
    elif req.method == UnlockMethod.BEK_FILE:
        cmd.extend(["--bekfile", str(Path(req.secret).expanduser())])
    else:
        raise RunnerError(f"Unsupported unlock method: {req.method}")

    cmd.extend(["--", str(fuse_mount)])
    return cmd


def _redact_cmd(cmd: list[str]) -> str:
    """Return a shell-ish string safe for logs (passwords redacted)."""
    redacted: list[str] = []
    for part in cmd:
        if part.startswith("--user-password="):
            redacted.append("--user-password=***")
        elif part.startswith("--recovery-password="):
            redacted.append("--recovery-password=***")
        else:
            redacted.append(part)
    return " ".join(redacted)


def _wait_for_file(
    path: Path,
    proc: subprocess.Popen[str],
    log: LogFn,
    timeout_s: float,
) -> None:
    """Wait until dislocker-file exists or the fuse process exits."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists():
            log(f"Found {path}")
            return
        if proc.poll() is not None:
            out = ""
            if proc.stdout is not None:
                out = proc.stdout.read() or ""
            raise RunnerError(
                "dislocker-fuse exited before creating dislocker-file.\n"
                + out.strip()
            )
        time.sleep(0.25)
    raise RunnerError(f"Timed out waiting for {path}")


def _hdiutil_attach(deps: DepsStatus, image: Path, log: LogFn) -> str:
    """Attach a raw disk image; return the /dev/diskN node."""
    assert deps.hdiutil
    proc = subprocess.run(
        [
            deps.hdiutil,
            "attach",
            "-imagekey",
            "diskimage-class=CRawDiskImage",
            "-nomount",
            str(image),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    log(proc.stdout.strip() or "(no hdiutil stdout)")
    if proc.returncode != 0:
        raise RunnerError(proc.stderr.strip() or "hdiutil attach failed")

    whole = re.findall(r"(/dev/disk\d+)\b", proc.stdout)
    if whole:
        return whole[0]
    parts = re.findall(r"(/dev/disk\d+s\d+)\b", proc.stdout)
    if parts:
        return parts[0]
    raise RunnerError("Could not parse disk device from hdiutil attach output")


def _allocate_volume_path(label: str) -> Path:
    """Choose a free /Volumes/<label> path."""
    base = Path("/Volumes") / label
    if not base.exists():
        return base
    for i in range(2, 50):
        candidate = Path("/Volumes") / f"{label}-{i}"
        if not candidate.exists():
            return candidate
    raise RunnerError("Could not allocate a free /Volumes mount point")


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


def _best_effort_cleanup(
    fuse_mount: Path,
    ntfs_mount: Path,
    raw_disk: Optional[str],
    log: LogFn,
) -> None:
    """Attempt to undo partial mount state after a failure."""
    if ntfs_mount.exists():
        subprocess.run(["umount", str(ntfs_mount)], check=False, capture_output=True)
    if raw_disk:
        subprocess.run(
            ["hdiutil", "detach", "-force", raw_disk],
            check=False,
            capture_output=True,
        )
    if fuse_mount.exists():
        subprocess.run(["umount", str(fuse_mount)], check=False, capture_output=True)
        shutil.rmtree(fuse_mount, ignore_errors=True)
    clear_session()
    log("Partial cleanup done")
