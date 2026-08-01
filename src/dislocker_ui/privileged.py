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
import stat
import sys
import traceback
from pathlib import Path
from typing import Any, TextIO

from dislocker_ui.deps import DepsStatus
from dislocker_ui.runner import (
    MountRequest,
    RunnerError,
    UnlockMethod,
    _redact_cmd,
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
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        os.chdir("/")
    except OSError as exc:
        sys.stderr.write(f"failed to chdir /: {exc}\n")
        return EXIT_UNEXPECTED

    log_fp: TextIO | None = None
    try:
        payload = _load_and_unlink_request(args.request)
        _validate_request(payload, expected_action=args.action)
        log_fp = _open_log(Path(payload["log_path"]), owner_uid=int(payload["uid"]))
        log_fp.write(
            f"audit action={payload['action']} uid={payload['uid']} "
            f"volume={payload.get('volume', '')}\n"
        )
        log_fp.flush()

        def log(message: str) -> None:
            assert log_fp is not None
            log_fp.write(message + "\n")
            log_fp.flush()

        deps = _deps_from_payload(payload["deps"])
        session_path = Path(payload["session_path"])
        uid = int(payload["uid"])
        gid = int(payload["gid"])

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
            )
            _chown_session(session_path, uid, gid)
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


def _load_and_unlink_request(path: Path) -> dict[str, Any]:
    """Read request JSON then unlink promptly (secret lives only briefly)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ValidationError(f"cannot read request: {exc}") from exc
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
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
    for pattern in ("--user-password=", "--recovery-password="):
        if pattern in tb:
            tb = tb.replace(pattern, pattern + "***")
    # Ensure _redact_cmd remains imported and available for structured logging.
    _ = _redact_cmd(["dislocker-fuse", "--user-password=secret"])
    return f"unexpected error: {exc}\n{tb}"


if __name__ == "__main__":
    raise SystemExit(main())
