"""
Privileged child entry point for elevated mount/unmount.

Overall purpose:
  Run as root via osascript ``do shell script … with administrator privileges``.
  Read a mode-0600 request JSON written by the unprivileged parent, validate it,
  open the diagnostic log safely, then run the in-process runner pipeline with
  serialized deps (never rediscover) and an explicit session_path.

Inputs:
  CLI: ``python -s -P -m dislocker_ui.privileged mount|unmount --request PATH``
  Request JSON fields documented in plans/macos-elevation.md.

Outputs:
  Exit 0 on success; 2 validation failure; 3 RunnerError; 4 unexpected.
  Diagnostics appended to request log_path; mount writes session_path.

Requirements:
  Must not call discover_deps() or default_session_path(). cwd should be ``/``
  (parent forces ``cd /``). Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pwd
import re
import stat
import sys
import time
import traceback
from pathlib import Path
from typing import Any, TextIO

from dislocker_ui.deps import DepsStatus
from dislocker_ui.elevate import validate_volume_path
from dislocker_ui.runner import (
    MountRequest,
    RunnerError,
    UnlockMethod,
    mount_volume,
    unmount_volume,
)

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_RUNNER = 3
EXIT_UNEXPECTED = 4

_REQUIRED_DEPS = ("dislocker_fuse", "hdiutil", "diskutil", "umount", "ntfs3g")


class _ValidationError(ValueError):
    """Request failed schema / safety checks."""


def main(argv: list[str] | None = None) -> int:
    """CLI entry: parse args, force cwd ``/``, dispatch mount/unmount."""
    parser = argparse.ArgumentParser(prog="dislocker_ui.privileged")
    parser.add_argument("action", choices=("mount", "unmount"))
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        os.chdir("/")
    except OSError as exc:
        sys.stderr.write(f"failed to chdir /: {exc}\n")
        return EXIT_UNEXPECTED

    log_fp: TextIO | None = None
    try:
        # Confine every filesystem path this root process touches under the
        # invoking user's own Application Support dir, derived from the system
        # passwd database via the trusted --uid arg (not request data).
        base = _user_base(args.uid)
        request_path = _confine_under_base(str(args.request), base, label="request")
        payload = _load_and_unlink_request(request_path)
        _validate_request(payload, expected_action=args.action)

        uid = int(payload["uid"])
        gid = int(payload["gid"])
        if uid != args.uid:
            raise _ValidationError(f"request uid {uid} != --uid {args.uid}")
        session_path = _confine_under_base(payload["session_path"], base, label="session_path")
        log_path = _confine_under_base(payload["log_path"], base, label="log_path")

        log_fp = _open_log(log_path, owner_uid=uid)
        log_fp.write(
            f"audit ts={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
            f"action={payload['action']} uid={uid} "
            f"volume={payload.get('volume', '')}\n"
        )
        log_fp.flush()
        log_handle: TextIO = log_fp

        def log(message: str) -> None:
            log_handle.write(message + "\n")
            log_handle.flush()

        deps = _deps_from_payload(payload["deps"])

        if args.action == "mount":
            req = _mount_request_from_payload(payload)
            session = mount_volume(
                req,
                deps,
                log,
                session_path=session_path,
                uid=uid,
                gid=gid,
                elevated=True,
                fuse_log_path=log_path,
            )
            try:
                _chown_session(session_path, uid, gid)
            except RunnerError as exc:
                # The mount succeeded; a chown failure only means the session
                # file stays root-owned. Warn and still report success.
                log(f"warning: {exc}")
            log(f"privileged mount ok: {session.ntfs_mount}")
        else:
            unmount_volume(deps, log, session_path=session_path)
            log("privileged unmount ok")
        return EXIT_OK
    except _ValidationError as exc:
        _append_log_best_effort(log_fp, f"validation error: {exc}")
        return EXIT_VALIDATION
    except RunnerError as exc:
        _append_log_best_effort(log_fp, f"runner error: {exc}")
        return EXIT_RUNNER
    except Exception as exc:
        _append_log_best_effort(log_fp, _format_unexpected(exc))
        return EXIT_UNEXPECTED
    finally:
        if log_fp is not None:
            with contextlib.suppress(OSError):
                log_fp.close()


def _user_base(uid: int) -> Path:
    """
    Return the invoking user's Application Support dir from the passwd database.

    Derived from *uid* (a validated int) via the system passwd entry, so the
    confinement base does not itself come from attacker-influenced request data.
    """
    try:
        home = Path(pwd.getpwuid(uid).pw_dir)
    except KeyError as exc:
        raise _ValidationError(f"uid {uid} has no passwd entry") from exc
    base = home / "Library" / "Application Support" / "dislocker-ui"
    _assert_safe_base(base, uid)
    return base.resolve()


def _assert_safe_base(base: Path, uid: int) -> None:
    """Refuse a confinement base that a non-owner could have tampered with."""
    try:
        info = os.lstat(base)
    except OSError as exc:
        raise _ValidationError(f"confinement base unavailable ({base}): {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise _ValidationError(f"confinement base must not be a symlink: {base}")
    if not stat.S_ISDIR(info.st_mode):
        raise _ValidationError(f"confinement base is not a directory: {base}")
    if info.st_uid != uid:
        raise _ValidationError(f"confinement base not owned by uid {uid}: {base}")
    if info.st_mode & 0o022:
        raise _ValidationError(f"confinement base is group/world-writable: {base}")


def _confine_under_base(value: str, base: Path, *, label: str) -> Path:
    """
    Resolve *value*, require it directly inside *base*, and rebuild it safely.

    The child only ever addresses single files that live directly in the base
    dir, so after the containment check we return ``base / basename`` — stripping
    any directory component so the returned path cannot escape *base*.
    """
    resolved = Path(os.path.realpath(value))
    try:
        parent = resolved.parent.relative_to(base)
    except ValueError as exc:
        raise _ValidationError(f"{label} escapes {base}: {value}") from exc
    if parent != Path("."):
        raise _ValidationError(f"{label} must be directly inside {base}: {value}")
    return base / os.path.basename(resolved)


def _load_and_unlink_request(path: Path) -> dict[str, Any]:
    """Read request JSON then unlink promptly (secret lives only briefly)."""
    if path.is_symlink():
        raise _ValidationError("request path must not be a symlink")
    safe = Path(os.path.realpath(path))
    try:
        raw = safe.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ValidationError(f"cannot read request: {exc}") from exc
    with contextlib.suppress(OSError):
        safe.unlink(missing_ok=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _ValidationError("request root must be an object")
    return data


def _validate_request(payload: dict[str, Any], *, expected_action: str) -> None:
    """Re-validate action, absolute paths, uid/gid, and deps existence."""
    action = payload.get("action")
    if action != expected_action:
        raise _ValidationError(f"action mismatch: {action!r} != {expected_action!r}")
    _require_absolute_paths(payload, ("session_path", "log_path"))
    _require_int_fields(payload, ("uid", "gid"))
    _validate_deps_payload(payload.get("deps"))
    if action == "mount":
        _validate_mount_fields(payload)


def _require_absolute_paths(payload: dict[str, Any], keys: tuple[str, ...]) -> None:
    """Ensure named fields are non-empty absolute path strings."""
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise _ValidationError(f"missing {key}")
        if not Path(value).is_absolute():
            raise _ValidationError(f"{key} must be absolute: {value}")


def _require_int_fields(payload: dict[str, Any], keys: tuple[str, ...]) -> None:
    """Ensure named fields parse as integers."""
    for key in keys:
        if key not in payload:
            raise _ValidationError(f"missing {key}")
        try:
            int(payload[key])
        except (TypeError, ValueError) as exc:
            raise _ValidationError(f"invalid {key}") from exc


def _validate_deps_payload(deps: object) -> None:
    """Ensure deps is a complete map of existing absolute files."""
    if not isinstance(deps, dict):
        raise _ValidationError("deps must be an object")
    for name in _REQUIRED_DEPS:
        path_s = deps.get(name)
        if not isinstance(path_s, str) or not path_s:
            raise _ValidationError(f"deps.{name} missing")
        p = Path(path_s)
        if not p.is_absolute() or not p.is_file():
            raise _ValidationError(f"deps.{name} must be an existing absolute file: {path_s}")


def _validate_mount_fields(payload: dict[str, Any]) -> None:
    """Validate mount-only request fields including BEK absolute-path rule."""
    for key in ("volume", "method", "secret", "readonly", "volume_label"):
        if key not in payload:
            raise _ValidationError(f"missing mount field {key}")
    # Independently re-validate the volume path (don't trust the request).
    try:
        validate_volume_path(str(payload["volume"]))
    except RunnerError as exc:
        raise _ValidationError(str(exc)) from exc
    method = payload["method"]
    if method not in {m.value for m in UnlockMethod}:
        raise _ValidationError(f"invalid method: {method}")
    if method == UnlockMethod.BEK_FILE.value:
        secret = payload["secret"]
        if not isinstance(secret, str) or not Path(secret).is_absolute():
            raise _ValidationError("BEK secret must be an absolute path")
        if "~" in secret:
            raise _ValidationError("BEK path must not contain ~ (parent must canonicalize)")


def _deps_from_payload(deps: dict[str, Any]) -> DepsStatus:
    """Build DepsStatus from serialized absolute paths (no discover_deps)."""
    return DepsStatus(
        dislocker_fuse=str(deps["dislocker_fuse"]),
        hdiutil=str(deps["hdiutil"]),
        diskutil=str(deps["diskutil"]),
        umount=str(deps["umount"]),
        ntfs3g=str(deps["ntfs3g"]),
    )


def _mount_request_from_payload(payload: dict[str, Any]) -> MountRequest:
    """Construct MountRequest; BEK paths are used as-is (no expanduser)."""
    return MountRequest(
        volume=str(payload["volume"]),
        method=UnlockMethod(str(payload["method"])),
        secret=str(payload["secret"]),
        readonly=bool(payload["readonly"]),
        volume_label=str(payload["volume_label"]),
    )


def _open_log(path: Path, *, owner_uid: int) -> TextIO:
    """
    Open log with O_APPEND|O_NOFOLLOW; require regular file owned by request uid, mode 0600.
    """
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise _ValidationError(f"cannot open log: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _ValidationError("log_path is not a regular file")
        if info.st_uid != owner_uid:
            raise _ValidationError("log_path owner does not match request uid")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise _ValidationError("log_path must be mode 0600")
        return os.fdopen(fd, "a", encoding="utf-8")
    except Exception:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


def _chown_session(session_path: Path, uid: int, gid: int) -> None:
    """chown the session file to the invoking user after a successful mount."""
    try:
        os.chown(session_path, uid, gid)
    except OSError as exc:
        raise RunnerError(f"failed to chown session file: {exc}") from exc


def _append_log_best_effort(log_fp: TextIO | None, message: str) -> None:
    """Write an error line if the log is already open."""
    if log_fp is None:
        return
    with contextlib.suppress(OSError):
        log_fp.write(message + "\n")
        log_fp.flush()


def _format_unexpected(exc: BaseException) -> str:
    """Format traceback with password-like argv fragments redacted."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    composed = f"unexpected error: {exc}\n{tb}"
    return re.sub(r"(--(?:user|recovery)-password=)\S*", r"\1***", composed)


if __name__ == "__main__":
    raise SystemExit(main())
