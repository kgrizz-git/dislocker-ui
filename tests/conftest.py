"""Shared test helpers for the dislocker-ui suite."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch


@contextlib.contextmanager
def fake_root_owned(path: Path) -> Iterator[None]:
    """Report *path* as uid-0-owned through os.open/os.fstat (root simulation)."""
    real_open, real_fstat = os.open, os.fstat
    owned: set[int] = set()

    def _open(file: object, flags: int, *args: object, **kwargs: object) -> int:
        fd = real_open(file, flags, *args, **kwargs)  # type: ignore[arg-type]
        if isinstance(file, str | bytes | os.PathLike) and os.path.abspath(file) == str(path):
            owned.add(fd)
        return fd

    def _fstat(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        if fd in owned:
            st = os.stat_result(
                (
                    st.st_mode,
                    st.st_ino,
                    st.st_dev,
                    st.st_nlink,
                    0,
                    st.st_gid,
                    st.st_size,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime,
                )
            )
        return st

    with patch("os.open", side_effect=_open), patch("os.fstat", side_effect=_fstat):
        yield
