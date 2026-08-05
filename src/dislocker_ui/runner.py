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
  Side effects: FUSE mount under the process temp dir (not world-writable
  /tmp), raw disk attach, /Volumes mount, session file.

Requirements:
  dislocker-fuse, hdiutil, umount, and ntfs-3g (RO and RW on modern macOS).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dislocker_ui.deps import DepsStatus
from dislocker_ui.ntfs_mount import assert_mount_owner as _assert_mount_owner
from dislocker_ui.ntfs_mount import mount_ntfs as _mount_ntfs
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
    *,
    session_path: Path | None = None,
    uid: int | None = None,
    gid: int | None = None,
    elevated: bool = False,
    fuse_log_path: Path | None = None,
    fuse_log_handle: object | None = None,
) -> MountSession:
    """
    Unlock and mount a BitLocker volume for Finder access.

    Public facade: on Darwin when not root, dispatches through elevate.py.
    Privileged child calls this with elevated=True and euid==0 (no re-entry).
    """
    from dislocker_ui.elevate import elevation_transaction, needs_elevation, run_elevated_mount
    from dislocker_ui.session import root_session_path

    if not elevated and needs_elevation():
        # Reject an already-active session here, before prompting for admin
        # credentials — the child would reject it anyway (exit 3).
        canonical_path = session_path or root_session_path(os.getuid())
        existing = load_session(canonical_path)
        if existing is not None:
            raise RunnerError(
                "A session is already active. Click Unmount before mounting again.\n"
                f"NTFS mount: {existing.ntfs_mount}"
            )
        # Hold the per-user lock across prepare + osascript so a concurrent
        # elevation cannot sweep this transaction's request/log files.
        with elevation_transaction() as (sess_path, log_path):
            target = session_path or sess_path
            return run_elevated_mount(
                req,
                deps,
                log,
                session_path=target,
                log_path=log_path,
            )

    return _mount_in_process(
        req,
        deps,
        log,
        session_path=session_path,
        uid=uid,
        gid=gid,
        elevated=elevated,
        fuse_log_path=fuse_log_path,
        fuse_log_handle=fuse_log_handle,
    )


def _mount_in_process(
    req: MountRequest,
    deps: DepsStatus,
    log: LogFn,
    *,
    session_path: Path | None = None,
    uid: int | None = None,
    gid: int | None = None,
    elevated: bool = False,
    fuse_log_path: Path | None = None,
    fuse_log_handle: object | None = None,
) -> MountSession:
    """In-process mount pipeline (root / already elevated / non-Darwin)."""
    req = _canonicalize_bek_secret(req)
    volume = _validate_mount_request(req, deps, session_path=session_path)

    if elevated:
        _validate_elevated_request(req)
        if session_path is None or uid is None:
            raise RunnerError("privileged mount requires canonical session state")
        fuse_mount = _allocate_privileged_fuse_path(session_path, uid)
    else:
        fuse_mount = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    ntfs_mount = _allocate_volume_path(req.volume_label)
    fuse_proc: subprocess.Popen[str] | None = None
    raw_disk: str | None = None

    try:
        cmd = _build_dislocker_cmd(deps, req, fuse_mount)
        log("Starting dislocker-fuse…")
        log(_redact_cmd(cmd))
        if elevated and fuse_log_handle is not None:
            # The root helper owns and has already validated this open handle.
            popen_kwargs: dict = {
                "stdout": fuse_log_handle,
                "stderr": subprocess.STDOUT,
                "start_new_session": True,
            }
        else:
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
            }
        fuse_proc = subprocess.Popen(cmd, **popen_kwargs)

        dislocker_file = fuse_mount / "dislocker-file"
        _wait_for_file(
            dislocker_file,
            fuse_proc,
            log,
            timeout_s=45,
            fuse_log_handle=fuse_log_handle if elevated else None,
        )

        log("Attaching raw NTFS image with hdiutil…")
        raw_disk = _hdiutil_attach(deps, dislocker_file, log)
        log(f"Attached as {raw_disk}")

        used_ntfs3g = _mount_ntfs(deps, req, raw_disk, ntfs_mount, log, uid=uid, gid=gid)
        _assert_mount_owner(ntfs_mount, uid=uid, gid=gid, log=log)
        session = MountSession(
            volume=volume,
            fuse_mount=str(fuse_mount),
            dislocker_file=str(dislocker_file),
            raw_disk=raw_disk,
            ntfs_mount=str(ntfs_mount),
            readonly=req.readonly,
            used_ntfs3g=used_ntfs3g,
            elevated=elevated,
        )
        save_session(session, session_path)
        log(f"Mounted successfully at {ntfs_mount}")
        return session

    except Exception:
        log("Mount failed — attempting cleanup…")
        _best_effort_cleanup(
            fuse_mount,
            ntfs_mount,
            raw_disk=raw_disk,
            log=log,
            session_path=session_path,
        )
        if fuse_proc is not None and fuse_proc.poll() is None:
            fuse_proc.terminate()
        raise
    finally:
        # The privileged child owns the log descriptor and closes it after the
        # complete operation.  Do not reopen or close it here.
        pass


def unmount_volume(
    deps: DepsStatus,
    log: LogFn,
    *,
    session_path: Path | None = None,
) -> None:
    """
    Reverse the last MountSession: umount NTFS, detach raw disk, umount FUSE.

    When the session was created via elevation and we are not root, re-elevate
    (second admin prompt — accepted for 0.2.0).
    """
    from dislocker_ui.elevate import elevation_transaction, needs_elevation, run_elevated_unmount
    from dislocker_ui.session import root_session_path

    canonical_path = (
        session_path or root_session_path(os.getuid()) if needs_elevation() else session_path
    )
    session = load_session(canonical_path)
    if session is None:
        raise RunnerError("No active session found to unmount")

    if session.elevated and needs_elevation():
        with elevation_transaction() as (sess_path, log_path):
            target = session_path or sess_path
            run_elevated_unmount(deps, log, session_path=target, log_path=log_path)
        return

    if session.elevated:
        _validate_elevated_session(session, session_path)
    errors: list[str] = []
    errors.extend(_unmount_ntfs(deps, session, log))
    errors.extend(_detach_raw_disk(deps, session, log))
    errors.extend(_unmount_fuse(deps, session, log))
    if not errors:
        errors.extend(
            _remove_fuse_dir(
                session.fuse_mount, elevated=session.elevated, session_path=session_path
            )
        )
        errors.extend(_remove_empty_ntfs_dir(session.ntfs_mount))
    if not errors:
        clear_session(session_path)
    if errors:
        raise RunnerError("Unmount incomplete; state was retained for retry:\n" + "\n".join(errors))
    else:
        log("Unmounted successfully")


def _canonicalize_bek_secret(req: MountRequest) -> MountRequest:
    """
    Resolve BEK paths before use.

    Absolute paths (elevated child) are used as-is via resolve() without
    expanduser, so root does not reinterpret ``~``. Relative/tilde paths are
    expanded only in the unprivileged parent path.
    """
    if req.method != UnlockMethod.BEK_FILE:
        return req
    path = Path(req.secret)
    resolved = str(path.resolve()) if path.is_absolute() else str(path.expanduser().resolve())
    return MountRequest(
        volume=req.volume,
        method=req.method,
        secret=resolved,
        readonly=req.readonly,
        volume_label=req.volume_label,
    )


def _validate_mount_request(
    req: MountRequest,
    deps: DepsStatus,
    *,
    session_path: Path | None = None,
) -> str:
    """Validate mount inputs; return the stripped volume path."""
    if not deps.core_ok:
        missing = ", ".join(deps.missing_core())
        raise RunnerError(f"Missing required tools: {missing}")

    volume = req.volume.strip()
    if not volume:
        raise RunnerError("Volume path is empty")
    if not Path(volume).exists():
        raise RunnerError(f"Volume path does not exist: {volume}")

    if req.method == UnlockMethod.BEK_FILE:
        bek = Path(req.secret)
        if not bek.is_file():
            raise RunnerError(f"BEK file not found: {bek}")
    elif not req.secret:
        raise RunnerError("Password / recovery password is empty")

    existing = load_session(session_path)
    if existing is not None:
        raise RunnerError(
            "A session is already active. Click Unmount before mounting again.\n"
            f"NTFS mount: {existing.ntfs_mount}"
        )
    return volume


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
    """Unmount the NTFS volume, forcing via diskutil if needed."""
    log(f"Unmounting NTFS at {session.ntfs_mount}…")
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
    if elevated and not _is_privileged_fuse_path(fuse_path, session_path):
        raise RunnerError("Refusing to remove a FUSE path outside privileged staging")
    try:
        if fuse_path.is_dir():
            shutil.rmtree(fuse_path)
    except OSError as exc:
        return [f"Could not remove FUSE staging directory {fuse_path}: {exc}"]
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
        # Parent/elevate canonicalize; do not expanduser here (root would expand ~ wrongly).
        cmd.extend(["--bekfile", str(Path(req.secret))])
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
    *,
    fuse_log_handle: object | None = None,
) -> None:
    """Wait until dislocker-file exists or the fuse process exits."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists():
            log(f"Found {path}")
            return
        if proc.poll() is not None:
            detail = ""
            if proc.stdout is not None:
                detail = proc.stdout.read() or ""
            raise RunnerError(
                "dislocker-fuse exited before creating dislocker-file.\n" + detail.strip()
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
    """Create and return a new direct child of ``/Volumes`` for *label*."""
    _validate_volume_label(label)
    for i in range(2, 50):
        suffix = "" if i == 2 else f"-{i - 1}"
        candidate = Path("/Volumes") / f"{label}{suffix}"
        try:
            candidate.mkdir(mode=0o755)
            info = os.lstat(candidate)
            if not candidate.parent == Path("/Volumes") or not info:
                raise RunnerError("mountpoint creation escaped /Volumes")
            return candidate
        except FileExistsError:
            continue
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
    raw_disk: str | None,
    log: LogFn,
    *,
    session_path: Path | None = None,
) -> None:
    """Attempt to undo partial mount state after a failure."""
    if ntfs_mount.exists():
        subprocess.run(["/sbin/umount", str(ntfs_mount)], check=False, capture_output=True)
    if raw_disk:
        subprocess.run(
            ["/usr/bin/hdiutil", "detach", "-force", raw_disk],
            check=False,
            capture_output=True,
        )
    if fuse_mount.exists():
        subprocess.run(["/sbin/umount", str(fuse_mount)], check=False, capture_output=True)
        shutil.rmtree(fuse_mount, ignore_errors=True)
    clear_session(session_path)
    log("Partial cleanup done")


_VOLUME_RE = re.compile(r"^/dev/disk\d+(s\d+)?$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


def _validate_volume_label(label: str) -> None:
    if not isinstance(label, str) or not _LABEL_RE.fullmatch(label) or label in {".", ".."}:
        raise RunnerError("Volume label must be a short filesystem-safe display name")


def _validate_elevated_request(req: MountRequest) -> None:
    if not _VOLUME_RE.fullmatch(req.volume.strip()):
        raise RunnerError("Elevated mounts require a physical /dev/diskN or /dev/diskNsM device")
    if type(req.readonly) is not bool:
        raise RunnerError("readonly must be boolean")
    _validate_volume_label(req.volume_label)


def _allocate_privileged_fuse_path(session_path: Path, uid: int) -> Path:
    """Create a root-owned FUSE staging directory below canonical state."""
    state_dir = session_path.parent
    staging = state_dir.parent / "staging" / str(uid)
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="session-", dir=str(staging)))


def _is_privileged_fuse_path(path: Path, session_path: Path | None) -> bool:
    if session_path is None:
        return False
    staging = session_path.parent.parent / "staging" / session_path.parent.name
    try:
        return path.parent == staging and path.name.startswith("session-") and not path.is_symlink()
    except OSError:
        return False


def _validate_elevated_session(session: MountSession, session_path: Path | None) -> None:
    """Reject malformed root-state before privileged cleanup side effects."""
    if not _VOLUME_RE.fullmatch(session.volume) or not _VOLUME_RE.fullmatch(session.raw_disk):
        raise RunnerError("Privileged session has an invalid device selector")
    if not _is_privileged_fuse_path(Path(session.fuse_mount), session_path):
        raise RunnerError("Privileged session FUSE path is outside application staging")
    ntfs = Path(session.ntfs_mount)
    if ntfs.parent != Path("/Volumes") or ntfs.name in {"", ".", ".."}:
        raise RunnerError("Privileged session NTFS mountpoint is unsafe")
