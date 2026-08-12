"""
macOS administrator elevation for dislocker-ui mount/unmount.

Overall purpose:
  When the GUI runs unprivileged on Darwin, wrap the mount/unmount pipeline in
  an osascript ``do shell script … with administrator privileges`` call that
  launches a privileged Python child (``dislocker_ui.privileged``). Secrets
  travel only via a mode-0600 request file — never in AppleScript.

Inputs:
  MountRequest / DepsStatus from the unprivileged parent; explicit session and
  log paths; process euid / platform for the elevation predicate.

Outputs:
  MountSession on elevated mount success; raises ElevationCancelled,
  ElevationTimedOut, or RunnerError on failure. Side effects: temporary
  request file (always unlinked), osascript admin prompt. Concurrent
  elevation is serialized with a per-user flock + thread lock so a second
  prepare cannot sweep another transaction's request/log files.

Requirements:
  macOS ``/usr/bin/osascript``; absolute sys.executable and PYTHONPATH embedded
  in the shell string (admin do shell script does not inherit parent env).
  Standard library only.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from dislocker_ui import session as session_mod
from dislocker_ui.mount_policy import is_physical_volume
from dislocker_ui.runner import MountRequest, RunnerError, UnlockMethod
from dislocker_ui.session import MountSession, load_session, root_log_path, root_session_path

LogFn = Callable[[str], None]

_OSASCRIPT = "/usr/bin/osascript"
_ADMIN_PROMPT = (
    "dislocker-ui needs administrator privileges to mount or unmount a BitLocker volume."
)
_TIMEOUT_SECONDS = 600
_LOG_TAIL_BYTES = 8 * 1024
_ERROR_NUMBER_RE = re.compile(r"\((-?\d+)\)\s*$|number\s+(-?\d+)", re.IGNORECASE)
_EXIT_REASONS = {2: "request validation", 3: "mount/unmount", 4: "unexpected"}
_TIMED_OUT_MSG = (
    "Administrator authorization timed out. Try again and complete the prompt promptly."
)
# Serialize prepare+run across threads (flock alone is not thread-safe in-process)
# and across processes (flock) so a second elevation cannot sweep the first's files.
_ELEVATION_THREAD_LOCK = threading.RLock()
_ELEVATION_LOCK_NAME = "dislocker-ui-elevate.lock"


class ElevationCancelled(RunnerError):
    """User dismissed the macOS administrator authorization dialog."""


class ElevationTimedOut(RunnerError):
    """Administrator authorization or AppleEvent timed out."""


def needs_elevation() -> bool:
    """True when running on Darwin without root (euid != 0)."""
    return sys.platform == "darwin" and os.geteuid() != 0


def run_elevated_mount(
    req: MountRequest,
    log: LogFn,
    *,
    session_path: Path,
    log_path: Path,
    request_dir: Path | None = None,
) -> MountSession:
    """
    Write a mount request and run the privileged child via osascript.

    On success, load and return the session written by the child. Always
    unlinks the request file. Does not clear an existing session on cancel.
    """
    validate_volume_path(req.volume)
    request_path = _write_request(
        action="mount",
        req=req,
        request_dir=request_dir or _request_directory(),
    )
    try:
        _run_osascript(request_path, action="mount", log=log, log_path=log_path)
        session = load_session(session_path)
        if session is None:
            raise RunnerError(
                "Elevated mount reported success but session file is missing or invalid.\n"
                + _tail_log(log_path)
            )
        return session
    finally:
        _unlink_quiet(request_path)


def run_elevated_unmount(
    log: LogFn,
    *,
    log_path: Path,
    request_dir: Path | None = None,
) -> None:
    """
    Write an unmount request and run the privileged child via osascript.

    On cancel/timeout the session file is left intact (caller must not clear).
    """
    request_path = _write_request(
        action="unmount",
        req=None,
        request_dir=request_dir or _request_directory(),
    )
    try:
        _run_osascript(request_path, action="unmount", log=log, log_path=log_path)
    finally:
        _unlink_quiet(request_path)


@contextlib.contextmanager
def elevation_transaction() -> Iterator[tuple[Path, Path]]:
    """
    Hold the per-user elevation lock for one prepare + privileged-run cycle.

    Acquire before the stale-file sweep; release only after ``run_elevated_mount``
    / ``run_elevated_unmount`` returns (caller must keep the ``with`` block open
    for the full elevated operation). Prevents a concurrent prepare from deleting
    another transaction's request or log file while an auth dialog is pending.
    """
    request_dir = _request_directory()
    with _elevation_lock(request_dir):
        yield prepare_elevation_paths()


def prepare_elevation_paths() -> tuple[Path, Path]:
    """
    Prepare the user-owned request directory and report canonical root paths.

    Returns (session_path, log_path). The root child creates both files in its
    root-owned state directory.

    Callers that run osascript afterward must use :func:`elevation_transaction`
    so the stale-file sweep cannot race another elevation.
    """
    request_dir = _request_directory()
    assert_safe_path_for_elevation(request_dir, label="Application Support")
    package_dir = Path(__file__).resolve().parent
    src_root = package_dir.parent
    # Root executes these modules via PYTHONPATH; guard both the package dir it
    # imports from and its parent against symlink/world-writable tampering.
    assert_safe_path_for_elevation(src_root, label="package src")
    assert_safe_path_for_elevation(package_dir, label="package dir")
    # Reject group/world-writable .py files: an attacker with group-write on
    # the checkout could trojan a module that root imports via PYTHONPATH.
    # Scan src_root (the actual PYTHONPATH entry), not just package_dir, so a
    # writable sibling like src/os.py is also caught.  Skip for pip-installed
    # packages where site-packages is the root (pip controls file modes, and
    # scanning site-packages recursively would be prohibitively slow).
    _assert_no_writable_py_files(src_root)
    # These temp files live in the persistent Application Support dir (not a
    # reboot-cleared temp dir) so the child can confine them; sweep any left
    # behind by a killed run first — a stale request file can hold a secret.
    _sweep_stale_elevation_files(request_dir)
    # The root child creates both of these inside its root-owned state dir.
    # They are returned only so the parent knows where to read status/errors.
    return root_session_path(os.getuid()), root_log_path(os.getuid())


def _request_directory() -> Path:
    """Return the user-owned transport directory, creating it before elevation."""
    directory = session_mod.default_session_path().parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextlib.contextmanager
def _elevation_lock(directory: Path) -> Iterator[None]:
    """
    Exclusive per-user lock: threading.RLock (in-process) + fcntl.flock (IPC).

    The lock file lives beside session/request temps and is never swept.
    """
    lock_path = directory / _ELEVATION_LOCK_NAME
    with _ELEVATION_THREAD_LOCK:
        # O_NOFOLLOW: refuse a symlinked lock path under Application Support.
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _sweep_stale_elevation_files(directory: Path) -> None:
    """Best-effort removal of stale request files left by a prior run."""
    for pattern in ("dislocker-ui-req-*.json",):
        for stale in directory.glob(pattern):
            _unlink_quiet(stale)


def assert_safe_path_for_elevation(path: Path, *, label: str) -> None:
    """Refuse elevation if *path* is a symlink or group/world-writable."""
    if path.is_symlink():
        raise RunnerError(f"Refusing to elevate: {label} is a symlink ({path})")
    if not path.exists():
        raise RunnerError(f"Refusing to elevate: {label} does not exist ({path})")
    mode = path.stat().st_mode
    if mode & 0o022:
        raise RunnerError(f"Refusing to elevate: {label} is group/world-writable ({path})")


def _assert_no_writable_py_files(src_root: Path) -> None:
    """Refuse elevation if any .py, .pyc, directory, or __pycache__ under *src_root* is writable.

    Root imports these modules via PYTHONPATH; a writable source file, bytecode
    file, or directory is a trojan vector.  Skips the recursive scan for
    pip-installed packages (site-packages / dist-packages) where the package
    manager controls file modes and scanning the entire directory would be
    prohibitively slow.
    """
    if _is_system_managed_install(src_root):
        return
    for entry in src_root.rglob("*"):
        if not entry.exists():
            continue
        mode = entry.stat().st_mode
        if mode & 0o022:
            if entry.is_dir():
                raise RunnerError(
                    f"Refusing to elevate: directory {entry} is group/world-writable "
                    f"(mode {oct(mode & 0o777)}). Fix with: chmod o-w,g-w {entry}"
                )
            if entry.suffix in (".py", ".pyc") or entry.name == "__pycache__":
                raise RunnerError(
                    f"Refusing to elevate: {entry} is group/world-writable "
                    f"(mode {oct(mode & 0o777)}). Fix with: chmod o-w,g-w {entry}"
                )


def _is_system_managed_install(src_root: Path) -> bool:
    """True when *src_root* is a root-owned pip-managed site-packages directory.

    In that case the package manager owns file integrity and a recursive scan
    would be too slow to run before every elevation.  A user-controlled
    directory merely named ``site-packages`` is not exempt.
    """
    if src_root.name not in ("site-packages", "dist-packages"):
        return False
    try:
        return src_root.stat().st_uid == 0
    except OSError:
        return False


def validate_volume_path(volume: str) -> None:
    """Refuse elevation unless *volume* is a physical macOS disk selector."""
    if is_physical_volume(volume.strip()):
        return
    raise RunnerError(
        f"Refusing to elevate for volume path (must be /dev/diskN or /dev/diskNsM): {volume}"
    )


def build_osascript(shell_command: str, *, prompt: str = _ADMIN_PROMPT) -> str:
    """
    Build the AppleScript source with a 600s timeout around do shell script.

    *shell_command* must already be AppleScript-escaped for embedding in quotes.
    """
    escaped_cmd = _applescript_escape(shell_command)
    escaped_prompt = _applescript_escape(prompt)
    return (
        f"with timeout of {_TIMEOUT_SECONDS} seconds\n"
        f'  do shell script "{escaped_cmd}" with administrator privileges '
        f'with prompt "{escaped_prompt}"\n'
        f"end timeout"
    )


def build_privileged_shell_command(action: str, request_path: Path) -> str:
    """
    Assemble ``cd / && env PYTHONPATH=… python -s -P -m dislocker_ui.privileged …``.

    Each argv token is shlex-quoted; the result is NOT yet AppleScript-escaped.
    """
    if action not in ("mount", "unmount"):
        raise RunnerError(f"Invalid privileged action: {action}")
    python = str(Path(sys.executable).resolve())
    src_root = Path(__file__).resolve().parent.parent
    argv = [
        "env",
        f"PYTHONPATH={src_root}",
        python,
        "-s",
        "-P",
        "-m",
        "dislocker_ui.privileged",
        action,
        "--uid",
        str(os.getuid()),
        "--request",
        str(request_path.resolve()),
    ]
    return "cd / && " + " ".join(shlex.quote(part) for part in argv)


def classify_osascript_failure(stderr: str, *, log_path: Path | None = None) -> RunnerError:
    """
    Map osascript stderr text to ElevationCancelled / TimedOut / RunnerError.

    Parses trailing ``(N)`` / ``number N`` for child exit codes 2/3/4 vs -128.
    """
    text = (stderr or "").strip()
    code = _parse_error_number(text)
    lower = text.lower()
    tail = _tail_log(log_path) if log_path is not None else ""
    detail = text or "(no osascript stderr)"

    # The child's shell exit status is authoritative — classify on it first, so a
    # child failure whose stderr merely contains "timed out"/"canceled" is not
    # misread as an auth cancel/timeout.
    if code == -128:
        return ElevationCancelled("Administrator authorization was cancelled.")
    if code == -1712:
        return ElevationTimedOut(_TIMED_OUT_MSG)
    if code in _EXIT_REASONS:
        return RunnerError(
            f"Elevated {_EXIT_REASONS[code]} failed (exit {code}).\n{detail}\n{tail}".strip()
        )

    # No recognizable error number — fall back to text heuristics.
    if "user canceled" in lower or "user cancelled" in lower:
        return ElevationCancelled("Administrator authorization was cancelled.")
    if "timed out" in lower or "appleevent timed out" in lower or "timeout of" in lower:
        return ElevationTimedOut(_TIMED_OUT_MSG)
    return RunnerError(f"Administrator elevation failed.\n{detail}\n{tail}".strip())


def _applescript_escape(value: str) -> str:
    """Escape a string for embedding inside an AppleScript double-quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_error_number(text: str) -> int | None:
    """Extract AppleScript error number from stderr samples."""
    match = _ERROR_NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    try:
        return int(raw)
    except ValueError:
        return None


def _write_request(
    *,
    action: str,
    req: MountRequest | None,
    request_dir: Path,
) -> Path:
    """Create a mode-0600 request JSON; secret is inserted last when present."""
    fd, name = tempfile.mkstemp(prefix="dislocker-ui-req-", suffix=".json", dir=str(request_dir))
    path = Path(name)
    try:
        os.fchmod(fd, 0o600)
        payload: dict[str, object] = {
            "action": action,
            "uid": os.getuid(),
            "gid": os.getgid(),
        }
        if action == "mount":
            if req is None:
                raise RunnerError("Mount elevation requires a MountRequest")
            secret = req.secret
            if req.method == UnlockMethod.BEK_FILE:
                secret = str(Path(req.secret).expanduser().resolve())
            payload["volume"] = req.volume.strip()
            payload["method"] = req.method.value
            payload["readonly"] = req.readonly
            payload["volume_label"] = req.volume_label
            # Write secret last so it is the final JSON key (insertion order).
            payload["secret"] = secret
        data = json.dumps(payload, indent=2) + "\n"
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1  # ownership transferred
            handle.write(data)
    except Exception:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        _unlink_quiet(path)
        raise
    return path


def _run_osascript(request_path: Path, *, action: str, log: LogFn, log_path: Path) -> None:
    """Invoke osascript; raise classified errors on non-zero exit."""
    shell_cmd = build_privileged_shell_command(action, request_path)
    script = build_osascript(shell_cmd)
    log("Requesting administrator privileges…")
    try:
        completed = subprocess.run(
            [_OSASCRIPT, "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS + 30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ElevationTimedOut(_TIMED_OUT_MSG) from exc
    if completed.returncode == 0:
        return
    stderr = completed.stderr or completed.stdout or ""
    raise classify_osascript_failure(stderr, log_path=log_path)


def _tail_log(log_path: Path | None) -> str:
    """Return the last ~8 KiB of the elevated log for error messages."""
    if log_path is None or not log_path.is_file():
        return ""
    try:
        data = log_path.read_bytes()
    except OSError:
        return ""
    if len(data) > _LOG_TAIL_BYTES:
        data = data[-_LOG_TAIL_BYTES:]
    text = data.decode("utf-8", errors="replace").strip()
    return f"--- log tail ---\n{text}" if text else ""


def _unlink_quiet(path: Path) -> None:
    """Unlink path, ignoring missing-file errors."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
