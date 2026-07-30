"""
Mount session persistence for dislocker-ui.

Overall purpose:
  Remember the last successful mount so Unmount can reverse FUSE attach/mount
  steps in the correct order.

Inputs:
  MountSession dataclass written after a successful mount.

Outputs:
  JSON file under the user's Application Support directory.

Requirements:
  Standard library (json, pathlib, dataclasses).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MountSession:
    """Paths/devices created by a successful Mount operation."""

    volume: str
    fuse_mount: str
    dislocker_file: str
    raw_disk: str
    ntfs_mount: str
    readonly: bool
    used_ntfs3g: bool


def default_session_path() -> Path:
    """Return the default path for the active session file."""
    base = Path.home() / "Library" / "Application Support" / "dislocker-ui"
    base.mkdir(parents=True, exist_ok=True)
    return base / "active_session.json"


def save_session(session: MountSession, path: Optional[Path] = None) -> Path:
    """Write session JSON; return the path written."""
    target = path or default_session_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(session), indent=2) + "\n", encoding="utf-8")
    return target


def load_session(path: Optional[Path] = None) -> Optional[MountSession]:
    """Load a session if present; return None when missing or invalid."""
    target = path or default_session_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return MountSession(**raw)
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        return None


def clear_session(path: Optional[Path] = None) -> None:
    """Delete the session file if it exists."""
    target = path or default_session_path()
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
