"""Tests for the GUI Help menu."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

import pytest

from dislocker_ui import __version__, help_text
from dislocker_ui.deps import DepsStatus
from dislocker_ui.gui import DislockerApp


def _core_deps() -> DepsStatus:
    """Return a DepsStatus with all core tools present."""
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


@pytest.fixture(autouse=True)
def no_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep GUI unit tests independent of the host platform's privilege state."""
    monkeypatch.setattr("dislocker_ui.gui.needs_elevation", lambda: False)


def _build_app(root: tk.Tk) -> DislockerApp:
    """Construct DislockerApp with discovery helpers mocked."""
    with (
        patch("dislocker_ui.gui.discover_deps", return_value=_core_deps()),
        patch("dislocker_ui.gui.list_disk_entries", return_value=[]),
        patch("dislocker_ui.gui.load_session", return_value=None),
    ):
        return DislockerApp(root)


def _help_menu(app: DislockerApp) -> tk.Menu:
    """Return the Help cascade widget from the app menubar."""
    menubar = app.nametowidget(app.master.cget("menu"))
    assert isinstance(menubar, tk.Menu)
    last = menubar.index("end")
    assert last is not None
    for index in range(last + 1):
        if menubar.type(index) == "cascade" and menubar.entrycget(index, "label") == "Help":
            cascade = menubar.nametowidget(menubar.entrycget(index, "menu"))
            assert isinstance(cascade, tk.Menu)
            assert str(cascade).endswith(".help")
            return cascade
    raise AssertionError("Help cascade not found")


def test_help_menu_has_three_entries(tk_root: tk.Tk) -> None:
    """The Help cascade offers About, Usage, and Troubleshooting."""
    cascade = _help_menu(_build_app(tk_root))
    assert cascade.index("end") == 2
    assert [cascade.entrycget(i, "label") for i in range(3)] == [
        "About dislocker-ui",
        "Usage",
        "Troubleshooting",
    ]


def test_about_dialog_shows_version(tk_root: tk.Tk) -> None:
    """Invoking About opens a dialog with the running version."""
    cascade = _help_menu(_build_app(tk_root))
    with patch("dislocker_ui.gui.messagebox.showinfo") as showinfo:
        cascade.invoke(0)
    showinfo.assert_called_once()
    assert showinfo.call_args.args[0] == "About dislocker-ui"
    assert __version__ in showinfo.call_args.args[1]


@pytest.mark.parametrize(
    ("index", "text"),
    [(1, help_text.USAGE_TEXT), (2, help_text.TROUBLESHOOTING_TEXT)],
)
def test_help_dialogs_show_readonly_text(tk_root: tk.Tk, index: int, text: str) -> None:
    """Usage/Troubleshooting open a disabled Text dialog that closes cleanly."""
    app = _build_app(tk_root)
    _help_menu(app).invoke(index)
    dialogs = [w for w in tk_root.winfo_children() if isinstance(w, tk.Toplevel)]
    assert len(dialogs) == 1
    bodies = [w for w in dialogs[0].winfo_children() if isinstance(w, tk.Text)]
    assert len(bodies) == 1
    assert bodies[0].cget("state") == "disabled"
    assert text.strip() in bodies[0].get("1.0", "end")
    closers = [
        w
        for w in dialogs[0].winfo_children()
        if isinstance(w, ttk.Button) and w.cget("text") == "Close"
    ]
    assert len(closers) == 1
    closers[0].invoke()
    assert not dialogs[0].winfo_exists()


def test_help_dialog_reopening_lifts_existing(tk_root: tk.Tk) -> None:
    """Opening Usage twice yields one dialog; the second call lifts it."""
    app = _build_app(tk_root)
    cascade = _help_menu(app)
    with patch.object(tk.Toplevel, "lift") as lift:
        cascade.invoke(1)
        cascade.invoke(1)
    lift.assert_called_once_with()
    assert len([w for w in tk_root.winfo_children() if isinstance(w, tk.Toplevel)]) == 1
