"""Shared validation policy for privileged mount inputs and generated paths."""

from __future__ import annotations

import re
from pathlib import Path

VOLUMES_ROOT = Path("/Volumes")
PHYSICAL_VOLUME_RE = re.compile(r"^/dev/disk\d+(s\d+)?$")
VOLUME_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


def is_physical_volume(value: object) -> bool:
    """Return whether *value* is a permitted physical macOS disk selector."""
    return isinstance(value, str) and bool(PHYSICAL_VOLUME_RE.fullmatch(value))


def is_safe_volume_label(value: object) -> bool:
    """Return whether *value* can name a direct child of ``/Volumes``."""
    return (
        isinstance(value, str)
        and bool(VOLUME_LABEL_RE.fullmatch(value))
        and value not in {".", ".."}
    )


def privileged_staging_dir(session_path: Path, uid: int) -> Path:
    """Return the root-owned staging directory for one user's session state."""
    return session_path.parent.parent / "staging" / str(uid)
