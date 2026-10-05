"""
Tests for the stale-session GUI status hint (plan D7).

Overall purpose:
  Verify the session status line appends a stale hint only when the
  non-GUI staleness check says so, and never fails the refresh.

Requirements:
  pytest; tkinter (stdlib).
"""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.gui import DislockerApp
from dislocker_ui.session import MountSession

_STALE_HINT = "looks stale; click Unmount to clean up"


def _core_deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def _sample_session() -> MountSession:
    """Return a MountSession resembling a successful mount."""
    return MountSession(
        volume="/dev/disk2s1",
        fuse_mount="/var/folders/xx/dislocker-ui-test",
        dislocker_file="/var/folders/xx/dislocker-ui-test/dislocker-file",
        raw_disk="/dev/disk4",
        ntfs_mount="/Volumes/DislockerUI",
        readonly=True,
        used_ntfs3g=False,
    )


@pytest.fixture
def tk_root() -> tk.Tk:
    """Create a withdrawn Tk root for headless widget tests."""
    root = tk.Tk()
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture(autouse=True)
def no_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep GUI unit tests independent of the host platform's privilege state."""
    monkeypatch.setattr("dislocker_ui.gui.needs_elevation", lambda: False)


def _build_app(
    root: tk.Tk, session: MountSession | None, *, stale: bool | Exception
) -> DislockerApp:
    """Construct DislockerApp with discovery and staleness mocked."""
    if isinstance(stale, Exception):
        look = MagicMock(side_effect=stale)
    else:
        look = MagicMock(return_value=stale)
    with (
        patch("dislocker_ui.gui.discover_deps", return_value=_core_deps()),
        patch("dislocker_ui.gui.list_disk_entries", return_value=[]),
        patch("dislocker_ui.gui.load_session", return_value=session),
        patch("dislocker_ui.gui.session_looks_stale", look),
    ):
        return DislockerApp(root)


def test_stale_session_status_appends_hint(tk_root: tk.Tk) -> None:
    """A stale-looking session gains the cleanup hint."""
    app = _build_app(tk_root, _sample_session(), stale=True)
    assert "Active session" in app.status_var.get()
    assert _STALE_HINT in app.status_var.get()


def test_live_session_status_has_no_hint(tk_root: tk.Tk) -> None:
    """A live-looking session keeps the plain status line."""
    app = _build_app(tk_root, _sample_session(), stale=False)
    assert "Active session" in app.status_var.get()
    assert _STALE_HINT not in app.status_var.get()


def test_stale_check_failure_keeps_plain_status(tk_root: tk.Tk) -> None:
    """A raising staleness check never breaks the status refresh."""
    app = _build_app(tk_root, _sample_session(), stale=RuntimeError("boom"))
    assert "Active session" in app.status_var.get()
    assert _STALE_HINT not in app.status_var.get()


def test_no_session_status_unchanged(tk_root: tk.Tk) -> None:
    """No session still reports the empty state."""
    app = _build_app(tk_root, None, stale=False)
    assert app.status_var.get() == "No active session"
