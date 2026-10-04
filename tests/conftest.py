"""Shared test helpers for the dislocker-ui suite."""

from __future__ import annotations

import contextlib
import os
import tkinter as tk
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tk_root() -> tk.Tk:
    """
    Create a withdrawn Tk root for headless widget tests.

    Zero-delay `after(0, …)` callbacks run immediately so tests never call
    `update()` (which can hang under macOS Tk when reusing the process).
    """
    root = tk.Tk()
    root.withdraw()
    real_after = root.after

    def after(ms: object, func: object | None = None, *args: object) -> object:
        if func is not None and int(ms) == 0:  # type: ignore[arg-type]
            assert callable(func)
            func(*args)
            return "after-immediate"
        return real_after(ms, func, *args)  # type: ignore[arg-type]

    root.after = after  # type: ignore[method-assign]
    try:
        yield root
    finally:
        root.destroy()


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
