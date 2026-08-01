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
  request file (always unlinked), osascript admin prompt.

Requirements:
  macOS ``/usr/bin/osascript``; absolute sys.executable and PYTHONPATH embedded
  in the shell string (admin do shell script does not inherit parent env).
  Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from dislocker_ui.deps import DepsStatus
from dislocker_ui.runner import MountRequest, RunnerError, UnlockMethod
from dislocker_ui.session import MountSession, load_session

LogFn = Callable[[str], None]

_OSASCRIPT = "/usr/bin/osascript"
_ADMIN_PROMPT = (
    "dislocker-ui needs administrator privileges to mount or unmount a BitLocker volume."
)
_TIMEOUT_SECONDS = 600
_LOG_TAIL_BYTES = 8 * 1024
_VOLUME_RE = re.compile(r"^/dev/disk\d+(s\d+)?$")
_ERROR_NUMBER_RE = re.compile(r"\((-?\d+)\)\s*$|number\s+(-?\d+)", re.IGNORECASE)


class ElevationCancelled(RunnerError):
    """User dismissed the macOS administrator authorization dialog."""


class ElevationTimedOut(RunnerError):
    """Administrator authorization or AppleEvent timed out."""


def needs_elevation() -> bool:
    """True when running on Darwin without root (euid != 0)."""
    return sys.platform == "darwin" and os.geteuid() != 0


def run_elevated_mount(
    req: MountRequest,
    deps: DepsStatus,
    log: LogFn,
    *,
    session_path: Path,
    log_path: Path,
) -> MountSession:
    """
    Write a mount request and run the privileged child via osascript.

    On success, load and return the session written by the child. Always
    unlinks the request file. Does not clear an existing session on cancel.
    """
    validate_volume_path(req.volume)
    request_path = _write_request(
        action="mount",
        deps=deps,
        session_path=session_path,
        log_path=log_path,
        req=req,
    )
    try:
        _run_osascript(request_path, action="mount", log=log)
        session = load_session(session_path)
        if session is None:
            raise RunnerError(
                "Elevated mount reported success but session file is missing or invalid.\n"
                + _tail_log(log_path)
            )
        return session
    finally:
        _unlink_quiet(request_path)
        _unlink_quiet(log_path)


def run_elevated_unmount(
    deps: DepsStatus,
    log: LogFn,
    *,
    session_path: Path,
    log_path: Path,
) -> None:
    """
    Write an unmount request and run the privileged child via osascript.

    On cancel/timeout the session file is left intact (caller must not clear).
    """
    request_path = _write_request(
        action="unmount",
        deps=deps,
        session_path=session_path,
        log_path=log_path,
        req=None,
    )
    try:
        _run_osascript(request_path, action="unmount", log=log)
    finally:
        _unlink_quiet(request_path)
        _unlink_quiet(log_path)


def prepare_elevation_paths() -> tuple[Path, Path]:
    """
    Prepare user-owned session directory and an ephemeral 0600 log file.

    Returns (session_path, log_path). Does not create a placeholder session file.
    """
    from dislocker_ui.session import default_session_path

    session_path = default_session_path()
    assert_safe_path_for_elevation(session_path.parent, label="Application Support")
    package_dir = Path(__file__).resolve().parent
    src_root = package_dir.parent
    # Root executes these modules via PYTHONPATH; guard both the package dir it
    # imports from and its parent against symlink/world-writable tampering.
    assert_safe_path_for_elevation(src_root, label="package src")
    assert_safe_path_for_elevation(package_dir, label="package dir")
    fd, name = tempfile.mkstemp(prefix="dislocker-ui-log-", suffix=".log")
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return session_path, Path(name)


def assert_safe_path_for_elevation(path: Path, *, label: str) -> None:
    """Refuse elevation if *path* is a symlink or group/world-writable."""
    if path.is_symlink():
        raise RunnerError(f"Refusing to elevate: {label} is a symlink ({path})")
    if not path.exists():
        raise RunnerError(f"Refusing to elevate: {label} does not exist ({path})")
    mode = path.stat().st_mode
    if mode & 0o022:
        raise RunnerError(f"Refusing to elevate: {label} is group/world-writable ({path})")


def validate_volume_path(volume: str) -> None:
    """Refuse elevation unless volume looks like /dev/disk* or an existing file."""
    stripped = volume.strip()
    if _VOLUME_RE.match(stripped):
        return
    path = Path(stripped)
    if path.is_file() and not path.is_symlink():
        return
    raise RunnerError(
        f"Refusing to elevate for volume path (must be /dev/diskNsM or a regular file): {volume}"
    )


def serialize_deps(deps: DepsStatus) -> dict[str, str]:
    """Serialize DepsStatus absolute paths for the privileged child (no rediscovery)."""
    mapping = {
        "dislocker_fuse": deps.dislocker_fuse,
        "hdiutil": deps.hdiutil,
        "diskutil": deps.diskutil,
        "umount": deps.umount,
        "ntfs3g": deps.ntfs3g,
    }
    out: dict[str, str] = {}
    for key, value in mapping.items():
        if not value:
            raise RunnerError(f"Cannot elevate: missing dependency path for {key}")
        out[key] = str(Path(value).resolve())
    return out


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
        return ElevationTimedOut(
            "Administrator authorization timed out. Try again and complete the prompt promptly."
        )
    if code in (2, 3, 4):
        return RunnerError(
            f"Elevated { {2: 'request validation', 3: 'mount/unmount', 4: 'unexpected'}[code] } "
            f"failed (exit {code}).\n{detail}\n{tail}".strip()
        )

    # No recognizable error number — fall back to text heuristics.
    if "user canceled" in lower or "user cancelled" in lower:
        return ElevationCancelled("Administrator authorization was cancelled.")
    if "timed out" in lower or "appleevent timed out" in lower or "timeout of" in lower:
        return ElevationTimedOut(
            "Administrator authorization timed out. Try again and complete the prompt promptly."
        )
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
    deps: DepsStatus,
    session_path: Path,
    log_path: Path,
    req: MountRequest | None,
) -> Path:
    """Create a mode-0600 request JSON; secret is inserted last when present."""
    fd, name = tempfile.mkstemp(prefix="dislocker-ui-req-", suffix=".json")
    path = Path(name)
    try:
        os.fchmod(fd, 0o600)
        payload: dict[str, object] = {
            "action": action,
            "session_path": str(session_path),
            "log_path": str(log_path),
            "uid": os.getuid(),
            "gid": os.getgid(),
            "deps": serialize_deps(deps),
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


def _run_osascript(request_path: Path, *, action: str, log: LogFn) -> None:
    """Invoke osascript; raise classified errors on non-zero exit."""
    shell_cmd = build_privileged_shell_command(action, request_path)
    script = build_osascript(shell_cmd)
    log("Requesting administrator privileges…")
    # log_path is inside the request; recover from request JSON for tails on failure
    log_path = _log_path_from_request(request_path)
    completed = subprocess.run(
        [_OSASCRIPT, "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return
    stderr = completed.stderr or completed.stdout or ""
    raise classify_osascript_failure(stderr, log_path=log_path)


def _log_path_from_request(request_path: Path) -> Path | None:
    """Read log_path from the request file when still present."""
    try:
        raw = json.loads(request_path.read_text(encoding="utf-8"))
        return Path(raw["log_path"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


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
