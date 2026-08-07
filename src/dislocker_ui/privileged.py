"""Root child for the macOS elevation flow.

The request file is an untrusted, short-lived transport for mount intent.  It
never selects a session path, log path, or executable.  Those root authority
boundaries are derived locally from the authenticated UID.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pwd
import re
import stat
import time
import traceback
from pathlib import Path
from typing import Any, TextIO

from dislocker_ui.deps import discover_privileged_deps
from dislocker_ui.mount_policy import is_physical_volume, is_safe_volume_label
from dislocker_ui.runner import (
    MountRequest,
    RunnerError,
    UnlockMethod,
    mount_volume,
    unmount_volume,
)
from dislocker_ui.session import ensure_root_state_dir, root_log_path, root_session_path

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_RUNNER = 3
EXIT_UNEXPECTED = 4
_REQUEST_NAME_RE = re.compile(r"^dislocker-ui-req-\w{6,}\.json$", re.ASCII)


class _ValidationError(ValueError):
    """Request failed schema / safety checks."""


def main(argv: list[str] | None = None) -> int:
    """Parse a request, derive trusted state, and run the requested operation."""
    parser = argparse.ArgumentParser(prog="dislocker_ui.privileged")
    parser.add_argument("action", choices=("mount", "unmount"))
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    log_fp: TextIO | None = None
    try:
        os.chdir("/")
        user_base = _user_base(args.uid)
        request_name = _request_entry_name(args.request, user_base)
        payload = _load_and_unlink_request(request_name, user_base, args.uid)
        _validate_request(payload, expected_action=args.action, expected_uid=args.uid)
        uid, gid = args.uid, _validated_gid(args.uid, int(payload["gid"]))
        state_dir = ensure_root_state_dir(uid, gid)
        session_path = root_session_path(uid)
        log_fp = _open_root_log(root_log_path(uid), state_dir, gid)
        log_fp.write(
            f"audit ts={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
            f"action={args.action} uid={uid} volume={payload.get('volume', '')}\n"
        )
        log_fp.flush()

        def log(message: str) -> None:
            """Write one diagnostic line to the root log file."""
            if log_fp is None:
                raise RunnerError("privileged diagnostic log is unavailable")
            log_fp.write(message + "\n")
            log_fp.flush()

        deps = discover_privileged_deps()
        if not deps.core_ok:
            raise RunnerError(
                "Trusted root-managed tools are missing: " + ", ".join(deps.missing_core())
            )
        if args.action == "mount":
            session = mount_volume(
                _mount_request_from_payload(payload),
                deps,
                log,
                session_path=session_path,
                uid=uid,
                gid=gid,
                elevated=True,
                fuse_log_handle=log_fp,
            )
            try:
                _verify_session_readable(session_path, gid)
            except RunnerError as exc:
                log(
                    f"Mount succeeded at {session.ntfs_mount}, but session state could not be "
                    f"finalized: {exc}. Manually unmount {session.ntfs_mount}, detach "
                    f"{session.raw_disk}, then have an administrator remove {session_path}."
                )
                raise
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
    """Return and validate the user's request-transport directory."""
    try:
        home = Path(pwd.getpwuid(uid).pw_dir)
    except KeyError as exc:
        raise _ValidationError(f"uid {uid} has no passwd entry") from exc
    base = home / "Library" / "Application Support" / "dislocker-ui"
    _assert_safe_base(base, uid)
    return base


def _assert_safe_base(base: Path, uid: int) -> None:
    """Require a non-symlink, user-owned, non-group-writable directory."""
    try:
        info = os.lstat(base)
    except OSError as exc:
        raise _ValidationError(f"request directory unavailable ({base}): {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise _ValidationError("request directory must be a real directory")
    if info.st_uid != uid or info.st_mode & 0o022:
        raise _ValidationError("request directory ownership or mode is unsafe")


def _request_entry_name(path: Path, base: Path) -> str:
    """Validate the one request entry accepted below the already-checked base."""
    if not path.is_absolute():
        raise _ValidationError("request path must be absolute")
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise _ValidationError("request must be inside the request directory") from exc
    if len(relative.parts) != 1 or not _REQUEST_NAME_RE.fullmatch(relative.name):
        raise _ValidationError("request entry name is invalid")
    return relative.name


def _load_and_unlink_request(name: str, base: Path, owner_uid: int) -> dict[str, Any]:
    """Read a verified request descriptor, then unlink its exact directory entry."""
    if not _REQUEST_NAME_RE.fullmatch(name):
        raise _ValidationError("request entry name is invalid")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        dir_fd = os.open(str(base), flags)
    except OSError as exc:
        raise _ValidationError(f"cannot open request directory: {exc}") from exc
    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid:
                raise _ValidationError("request must be a regular file owned by the invoking user")
            if stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise _ValidationError("request must be mode 0600 with one link")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                raw = handle.read()
        finally:
            if fd >= 0:
                os.close(fd)
        # This removes a replacement directory entry without ever following it.
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=dir_fd)
    except OSError as exc:
        raise _ValidationError(f"cannot read request: {exc}") from exc
    finally:
        os.close(dir_fd)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _ValidationError("request root must be an object")
    return data


def _validate_request(payload: dict[str, Any], *, expected_action: str, expected_uid: int) -> None:
    """Validate all and only untrusted intent fields before side effects."""
    allowed = {"action", "uid", "gid"}
    if expected_action == "mount":
        allowed |= {"volume", "method", "secret", "readonly", "volume_label"}
    if set(payload) != allowed or payload.get("action") != expected_action:
        raise _ValidationError("request fields or action are invalid")
    if type(payload.get("uid")) is not int or payload["uid"] != expected_uid:
        raise _ValidationError("request uid does not match invoking user")
    if type(payload.get("gid")) is not int or payload["gid"] < 0:
        raise _ValidationError("invalid gid")
    if expected_action == "mount":
        _validate_mount_fields(payload)


def _validate_mount_fields(payload: dict[str, Any]) -> None:
    """Validate mount-specific fields in an elevated request payload."""
    volume = payload.get("volume")
    if not is_physical_volume(volume):
        raise _ValidationError("volume must be a physical /dev/diskN or /dev/diskNsM device")
    if payload.get("method") not in {item.value for item in UnlockMethod}:
        raise _ValidationError("invalid method")
    if type(payload.get("readonly")) is not bool:
        raise _ValidationError("readonly must be boolean")
    label = payload.get("volume_label")
    if not is_safe_volume_label(label):
        raise _ValidationError("volume label is invalid")
    secret = payload.get("secret")
    if not isinstance(secret, str) or not secret:
        raise _ValidationError("secret must be a non-empty string")
    if payload["method"] == UnlockMethod.BEK_FILE.value and (
        not Path(secret).is_absolute() or "~" in secret
    ):
        raise _ValidationError("BEK secret must be an absolute path without ~")


def _validated_gid(uid: int, gid: int) -> int:
    """Return *gid* only when it belongs to the invoking account."""
    try:
        entry = pwd.getpwuid(uid)
        allowed = set(os.getgrouplist(entry.pw_name, entry.pw_gid))
        allowed.add(entry.pw_gid)
    except (KeyError, OSError) as exc:
        raise _ValidationError(f"cannot validate groups for uid {uid}") from exc
    if gid not in allowed:
        raise _ValidationError("request gid is not assigned to the invoking user")
    return gid


def _mount_request_from_payload(payload: dict[str, Any]) -> MountRequest:
    """Build a MountRequest from a validated elevated payload."""
    return MountRequest(
        volume=payload["volume"],
        method=UnlockMethod(payload["method"]),
        secret=payload["secret"],
        readonly=payload["readonly"],
        volume_label=payload["volume_label"],
    )


def _open_root_log(path: Path, state_dir: Path, gid: int) -> TextIO:
    """Open the fixed root-created log; no user path is consulted."""
    if path.parent != state_dir:
        raise _ValidationError("root log path is outside its state directory")
    if os.geteuid() != 0:
        raise _ValidationError("root log must be opened by the privileged helper")
    try:
        fd = os.open(
            str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o640
        )
        os.fchown(fd, 0, gid)
        os.fchmod(fd, 0o640)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o640
        ):
            raise _ValidationError("root log ownership or mode is unsafe")
        return os.fdopen(fd, "a", encoding="utf-8")
    except OSError as exc:
        raise _ValidationError(f"cannot open root log: {exc}") from exc


def _verify_session_readable(path: Path, gid: int) -> None:
    """Verify the atomic session write has the intended root:*gid* mode."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise RunnerError(f"cannot finalize privileged session permissions: {exc}") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != 0o640
    ):
        raise RunnerError("privileged session ownership or mode is unsafe")


def _append_log_best_effort(log_fp: TextIO | None, message: str) -> None:
    """Append a log line, ignoring I/O errors during cleanup paths."""
    if log_fp is not None:
        with contextlib.suppress(OSError):
            log_fp.write(message + "\n")
            log_fp.flush()


def _format_unexpected(exc: BaseException) -> str:
    """Format a traceback while redacting password-like command arguments."""
    composed = f"unexpected error: {exc}\n" + "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return re.sub(
        r"(?P<option>--(?:user|recovery)-password|--bekfile)(?P<separator>=|[ \t]+)\S+",
        r"\g<option>\g<separator>***",
        composed,
    )


if __name__ == "__main__":
    raise SystemExit(main())
