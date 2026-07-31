"""
dislocker-ui — macOS GUI frontend for an installed dislocker toolchain.

Copyright (C) 2026 the dislocker-ui contributors

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

SPDX-License-Identifier: GPL-3.0-or-later

Overall purpose:
  Provide a small personal UI that unlocks BitLocker volumes via dislocker-fuse,
  attaches the virtual NTFS image, and mounts it for Finder access.

Inputs:
  User-selected volume path, unlock method/credentials, read-only flag.

Outputs:
  Mounted volume under /Volumes (and a session file used for Unmount).

Requirements:
  Python 3.10+, tkinter, dislocker-fuse, FUSE backend; optional ntfs-3g for RW.
"""

from pathlib import Path

__all__ = ["__version__"]


def _read_version() -> str:
    """Return the project version from VERSION, falling back to a default."""
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        text = version_file.read_text(encoding="utf-8").strip()
        return text or "0.0.0"
    except OSError:
        return "0.0.0"


__version__ = _read_version()
