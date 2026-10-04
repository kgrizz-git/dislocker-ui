"""Session state for dislocker-ui.

The regular UI may keep non-privileged state in its Application Support
directory.  Sessions created by the macOS privileged helper live instead below
``/var/db/dislocker-ui``: that directory is controlled by root, while the
invoking user can read their fixed status file.  Never use a user-supplied path
for privileged cleanup.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_STATE_ROOT = Path("/var/db/dislocker-ui")
SESSION_FILE = "active_session.json"
LOG_FILE = "operation.log"
SESSION_VERSION = 1


@dataclass
class MountSession:
    """Paths/devices created by one successful mount operation."""

    volume: str
    fuse_mount: str
    dislocker_file: str
    raw_disk: str
    ntfs_mount: str
    readonly: bool
    used_ntfs3g: bool
    elevated: bool = False
    version: int = SESSION_VERSION


def default_session_path() -> Path:
    """Return the legacy, unprivileged session path without creating it."""
    return Path.home() / "Library" / "Application Support" / "dislocker-ui" / SESSION_FILE


def active_session_path_for_user() -> Path:
    """Return the session path used by this UI process without creating it.

    The macOS GUI delegates mounts to the root helper, so its status reads must
    use the helper's fixed per-UID state.  Other platforms and a directly-run
    root process retain the legacy in-process session location.
    """
    if sys.platform == "darwin" and os.geteuid() != 0:
        return root_session_path(os.getuid())
    return default_session_path()


def root_state_dir(uid: int, *, state_root: Path = ROOT_STATE_ROOT) -> Path:
    """Return the fixed root-owned state directory for *uid*."""
    if uid < 0:
        raise ValueError("uid must not be negative")
    return state_root / str(uid)


def root_session_path(uid: int, *, state_root: Path = ROOT_STATE_ROOT) -> Path:
    """Return the fixed canonical session filename for *uid*."""
    return root_state_dir(uid, state_root=state_root) / SESSION_FILE


def root_log_path(uid: int, *, state_root: Path = ROOT_STATE_ROOT) -> Path:
    """Return the fixed diagnostic log filename for *uid*."""
    return root_state_dir(uid, state_root=state_root) / LOG_FILE


def ensure_root_state_dir(uid: int, gid: int, *, state_root: Path = ROOT_STATE_ROOT) -> Path:
    """Create and verify the root-owned, user-readable state directory.

    This function is intentionally called only by the privileged helper.  The
    user may read files through the directory's group permissions but cannot
    create, rename, or remove entries in it.
    """
    root = state_root
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- users may traverse to their known UID directory but cannot list this root
        os.chmod(root, 0o711)
        directory = root_state_dir(uid, state_root=root)
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chown(directory, 0, gid)
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- user group may read fixed status files but cannot modify entries
        os.chmod(directory, 0o750)
        root_info = os.lstat(root)
        info = os.lstat(directory)
    except OSError as exc:
        raise RuntimeError(f"cannot prepare privileged state directory: {exc}") from exc
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise RuntimeError("privileged state root is not a real directory")
    if root_info.st_uid != 0 or root_info.st_mode & 0o022:
        raise RuntimeError("privileged state root ownership or mode is unsafe")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError("privileged state directory is not a real directory")
    if info.st_uid != 0 or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o750:
        raise RuntimeError("privileged state directory ownership or mode is unsafe")
    return directory


def save_session(
    session: MountSession,
    path: Path | None = None,
    *,
    owner_gid: int | None = None,
) -> Path:
    """Atomically write session JSON; return the path written."""
    target = path or default_session_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(asdict(session), indent=2, sort_keys=True) + "\n"
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        if owner_gid is not None:
            os.fchown(fd, 0, owner_gid)
        os.fchmod(fd, 0o640 if session.elevated else 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            # Keep the descriptor open through fsync; an error still closes it
            # via the context manager and removes the uncommitted temp file.
            os.fsync(handle.fileno())
        os.replace(name, target)
        return target
    except Exception:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            Path(name).unlink()
        raise


def load_session(
    path: Path | None = None, *, require_owner: int | None = None
) -> MountSession | None:
    """Load a strictly shaped session, or return ``None`` when unavailable.

    When *require_owner* is set, files not owned by that uid are rejected.
    """
    target = path or default_session_path()
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        # Regular-file and ownership checks run on the open descriptor, so a
        # same-user swap between check and read cannot redirect the load.
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if require_owner is not None and info.st_uid != require_owner:
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            raw = json.load(handle)
        expected = set(MountSession.__dataclass_fields__)
        if not isinstance(raw, dict) or set(raw) != expected:
            return None
        session = MountSession(**raw)
        if (
            session.version != SESSION_VERSION
            or not all(
                isinstance(value, str)
                for value in (
                    session.volume,
                    session.fuse_mount,
                    session.dislocker_file,
                    session.raw_disk,
                    session.ntfs_mount,
                )
            )
            or not all(
                isinstance(value, bool)
                for value in (session.readonly, session.used_ntfs3g, session.elevated)
            )
        ):
            return None
        return session
    except (OSError, ValueError, TypeError, KeyError):
        return None
    finally:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)


def session_owner_mismatch(path: Path | None = None, uid: int = 0) -> bool:
    """True when a session file exists but is not owned by *uid*."""
    target = path or default_session_path()
    try:
        return os.lstat(target).st_uid != uid
    except OSError:
        return False


def manual_session_cleanup_hint(canonical_path: Path | None) -> str:
    """Point at the session file the operator must remove by hand."""
    target = canonical_path or default_session_path()
    return (
        "The session was left untouched for safety. Unmount the volume manually, "
        f"detach its raw disk, then remove {target} before mounting again."
    )


def legacy_session_recovery_message() -> str:
    """Explain how to recover safely from untrusted pre-versioned state."""
    return (
        "A pre-0.3.0 session record was found and cannot be trusted for automated cleanup. "
        "Unmount the existing volume manually, detach its raw disk, then remove the old "
        "dislocker-ui session file before mounting or unmounting again."
    )


def legacy_session_present(path: Path | None = None) -> bool:
    """True when a regular session file appears to predate schema versioning."""
    target = path or default_session_path()
    try:
        info = os.lstat(target)
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            return False
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(raw, dict) and "version" not in raw and "ntfs_mount" in raw


def clear_session(path: Path | None = None) -> None:
    """Delete the fixed session file if it exists."""
    target = path or default_session_path()
    with contextlib.suppress(OSError):
        target.unlink(missing_ok=True)
