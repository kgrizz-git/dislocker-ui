"""
Unit tests for the tkinter mount UI.

Overall purpose:
  Exercise DislockerApp widget behavior and Mount/Unmount handlers with
  mocked dependency discovery, disk listing, runner calls, and dialogs so CI
  never needs a real BitLocker volume or elevated privileges.

Inputs / fixtures:
  A withdrawn Tk root; patched discover_deps, list_disk_entries, load_session,
  mount_volume, unmount_volume, and messagebox helpers.

Outputs:
  Assertions on widget state, MountRequest construction, dialogs, and logs.

Requirements:
  pytest; tkinter (stdlib).
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.disks import DiskEntry
from dislocker_ui.gui import PWD_AUTO_HIDE_MS, DislockerApp, _raise_root_window, run_app
from dislocker_ui.runner import MountRequest, RunnerError, UnlockMethod
from dislocker_ui.session import MountSession


def _core_deps(*, ntfs3g: str | None = "/bin/ntfs-3g") -> DepsStatus:
    """Return a DepsStatus with all core tools present (ntfs-3g required)."""
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g=ntfs3g,
    )


def _missing_deps() -> DepsStatus:
    """Return a DepsStatus missing required tools."""
    return DepsStatus(
        dislocker_fuse=None,
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g=None,
    )


def _sample_disks() -> list[DiskEntry]:
    """Return a couple of selectable disk entries."""
    return [
        DiskEntry(device="/dev/disk2s1", summary="/dev/disk2s1  (WIN, 64.0 GB)"),
        DiskEntry(device="/dev/disk3s1", summary="/dev/disk3s1  (DATA, 128.0 GB)"),
    ]


def _sample_session(*, readonly: bool = True) -> MountSession:
    """Return a MountSession resembling a successful mount."""
    return MountSession(
        volume="/dev/disk2s1",
        fuse_mount="/var/folders/xx/dislocker-ui-test",
        dislocker_file="/var/folders/xx/dislocker-ui-test/dislocker-file",
        raw_disk="/dev/disk4",
        ntfs_mount="/Volumes/DislockerUI",
        readonly=readonly,
        used_ntfs3g=not readonly,
    )


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


@pytest.fixture(autouse=True)
def no_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep GUI unit tests independent of the host platform's privilege state."""
    monkeypatch.setattr("dislocker_ui.gui.needs_elevation", lambda: False)


def _build_app(
    root: tk.Tk,
    *,
    deps: DepsStatus | None = None,
    disks: list[DiskEntry] | None = None,
    session: MountSession | None = None,
) -> DislockerApp:
    """Construct DislockerApp with discovery helpers mocked."""
    disk_list = disks if disks is not None else _sample_disks()
    with (
        patch("dislocker_ui.gui.discover_deps", return_value=deps or _core_deps()),
        patch("dislocker_ui.gui.list_disk_entries", return_value=disk_list),
        patch("dislocker_ui.gui.load_session", return_value=session),
    ):
        return DislockerApp(root)


@pytest.fixture
def sync_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Run threading.Thread targets immediately on start().

    Combined with immediate `after(0, …)` on the test root, Mount/Unmount
    workers finish (and clear busy state) before `on_mount`/`on_unmount` return.
    """

    class _ImmediateThread:
        def __init__(
            self,
            group: object = None,
            target: object | None = None,
            name: object = None,
            args: tuple[object, ...] = (),
            kwargs: dict[str, object] | None = None,
            *,
            daemon: bool | None = None,
        ) -> None:
            del group, name, daemon
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self) -> None:
            assert callable(self._target)
            self._target(*self._args, **self._kwargs)

    monkeypatch.setattr("dislocker_ui.gui.threading.Thread", _ImmediateThread)


def test_init_shows_core_ok_and_no_session(tk_root: tk.Tk) -> None:
    """Startup populates deps text, disks, and empty-session status."""
    app = _build_app(tk_root, deps=_core_deps())
    assert "Core tools OK" in app.deps_label.cget("text")
    assert "ntfs-3g" in app.deps_label.cget("text")
    assert "RW needs ntfs-3g" not in app.deps_label.cget("text")
    assert app.status_var.get() == "No active session"
    assert app.volume_var.get() == "/dev/disk2s1  (WIN, 64.0 GB)"
    assert app.readonly_var.get() is True
    assert "writable" in app.rw_hint.cget("text")


def test_rw_hint_when_ntfs3g_missing(tk_root: tk.Tk) -> None:
    """Without ntfs-3g, mounts stay read-only; FAT/ExFAT still noted as OK."""
    app = _build_app(tk_root, deps=_core_deps(ntfs3g=None))
    text = app.rw_hint.cget("text")
    assert "ntfs-3g missing" in text
    assert "read-only" in text
    assert "FAT/ExFAT" in text
    assert app.readonly_var.get() is True
    assert str(app.readonly_check.cget("state")) == str(tk.DISABLED)


def test_init_missing_deps_label(tk_root: tk.Tk) -> None:
    """Missing tools are listed in the deps banner."""
    app = _build_app(tk_root, deps=_missing_deps())
    text = app.deps_label.cget("text")
    assert "Missing required tools" in text
    assert "dislocker-fuse" in text


def test_init_active_session_status(tk_root: tk.Tk) -> None:
    """An existing session is summarized in the status line."""
    session = _sample_session(readonly=False)
    app = _build_app(tk_root, session=session)
    assert "Active session (RW)" in app.status_var.get()
    assert "/Volumes/DislockerUI" in app.status_var.get()


def test_init_reads_the_canonical_active_session_path(tk_root: tk.Tk) -> None:
    """Status display asks for the same root-owned path as elevated operations."""
    canonical = Path("/var/db/dislocker-ui/501/active_session.json")
    with (
        patch("dislocker_ui.gui.discover_deps", return_value=_core_deps()),
        patch("dislocker_ui.gui.list_disk_entries", return_value=_sample_disks()),
        patch("dislocker_ui.gui.active_session_path_for_user", return_value=canonical),
        patch("dislocker_ui.gui.load_session", return_value=None) as load,
    ):
        DislockerApp(tk_root)
    load.assert_called_once_with(canonical)


def test_rw_available_when_ntfs3g_present(tk_root: tk.Tk) -> None:
    """With ntfs-3g, core banner and writable hint are shown; RW is enabled."""
    app = _build_app(tk_root, deps=_core_deps(ntfs3g="/bin/ntfs-3g"))
    assert "Core tools OK" in app.deps_label.cget("text")
    assert "ntfs-3g present" in app.deps_label.cget("text")
    assert "writable" in app.rw_hint.cget("text")
    assert str(app.readonly_check.cget("state")) == str(tk.NORMAL)


def test_method_change_toggles_bek_ui(tk_root: tk.Tk) -> None:
    """BEK mode reveals the path field and Browse button."""
    app = _build_app(tk_root)
    assert app.secret_label.cget("text") == "Password:"
    assert str(app.bek_button.cget("state")) == str(tk.DISABLED)

    app.method_var.set(UnlockMethod.BEK_FILE.value)
    app._on_method_change()
    assert app.secret_label.cget("text") == "BEK path:"
    assert app.secret_entry.cget("show") == ""
    assert str(app.bek_button.cget("state")) == str(tk.NORMAL)

    app.method_var.set(UnlockMethod.USER_PASSWORD.value)
    app._on_method_change()
    assert app.secret_label.cget("text") == "Password:"
    assert app.secret_entry.cget("show") == "*"
    assert str(app.bek_button.cget("state")) == str(tk.DISABLED)


def test_browse_bek_sets_secret(tk_root: tk.Tk) -> None:
    """Choosing a BEK file stores the path in the secret field."""
    app = _build_app(tk_root)
    with patch(
        "dislocker_ui.gui.filedialog.askopenfilename",
        return_value="/keys/volume.bek",
    ):
        app._browse_bek()
    assert app.secret_var.get() == "/keys/volume.bek"


def test_browse_bek_cancel_leaves_secret(tk_root: tk.Tk) -> None:
    """Canceling the BEK dialog does not clear an existing secret."""
    app = _build_app(tk_root)
    app.secret_var.set("keep-me")
    with patch("dislocker_ui.gui.filedialog.askopenfilename", return_value=""):
        app._browse_bek()
    assert app.secret_var.get() == "keep-me"


def test_refresh_disks_handles_diskutil_error(tk_root: tk.Tk) -> None:
    """diskutil failures are logged and clear the combo values."""
    app = _build_app(tk_root)
    with patch(
        "dislocker_ui.gui.list_disk_entries",
        side_effect=RuntimeError("diskutil list failed"),
    ):
        app.refresh_disks()
    assert app.disk_entries == []
    assert app.disk_combo.cget("values") in ("", (), [])
    log = app.log_text.get("1.0", tk.END)
    assert "diskutil error" in log


def test_resolve_volume_from_summary_device_and_typed(tk_root: tk.Tk) -> None:
    """Volume resolution accepts summary text, device path, or typed summary."""
    app = _build_app(tk_root)
    app.volume_var.set("/dev/disk3s1  (DATA, 128.0 GB)")
    assert app._resolve_volume() == "/dev/disk3s1"

    app.volume_var.set("/dev/disk2s1")
    assert app._resolve_volume() == "/dev/disk2s1"

    app.volume_var.set("/dev/disk9s9  (custom, 1.0 GB)")
    assert app._resolve_volume() == "/dev/disk9s9"

    app.volume_var.set("/path/to/image.img")
    assert app._resolve_volume() == "/path/to/image.img"


def test_recheck_deps_refreshes_label(tk_root: tk.Tk) -> None:
    """Recheck deps re-runs discovery and updates the banner."""
    app = _build_app(tk_root, deps=_missing_deps())
    with patch(
        "dislocker_ui.gui.discover_deps",
        return_value=_core_deps(ntfs3g="/bin/ntfs-3g"),
    ):
        app._recheck_deps()
    assert "Core tools OK" in app.deps_label.cget("text")
    assert "ntfs-3g present" in app.deps_label.cget("text")
    assert "Dependency check refreshed" in app.log_text.get("1.0", tk.END)


def test_on_mount_blocked_when_missing_tools(tk_root: tk.Tk) -> None:
    """Mount with incomplete deps shows an error and does not start work."""
    app = _build_app(tk_root, deps=_missing_deps())
    with (
        patch("dislocker_ui.gui.messagebox.showerror") as showerror,
        patch("dislocker_ui.gui.mount_volume") as mount_volume,
    ):
        app.on_mount()
    showerror.assert_called_once()
    mount_volume.assert_not_called()
    assert app._busy is False


def test_on_mount_preflights_privileged_tools_before_prompt(tk_root: tk.Tk) -> None:
    """A missing trusted root toolchain blocks Mount before any elevation work."""
    app = _build_app(tk_root, deps=_core_deps())
    with (
        patch("dislocker_ui.gui.needs_elevation", return_value=True),
        patch("dislocker_ui.gui.discover_privileged_deps", return_value=_missing_deps()),
        patch("dislocker_ui.gui.messagebox.showerror") as showerror,
        patch("dislocker_ui.gui.mount_volume") as mount,
    ):
        app.on_mount()
    assert "root-managed" in showerror.call_args.args[1]
    assert "scripts/install-root-deps.sh" in showerror.call_args.args[1]
    mount.assert_not_called()
    assert app._busy is False


def test_on_mount_allows_a_trusted_privileged_toolchain(tk_root: tk.Tk, sync_threads: None) -> None:
    """A complete trusted toolchain preserves the regular Mount flow."""
    app = _build_app(tk_root, deps=_core_deps())
    with (
        patch("dislocker_ui.gui.needs_elevation", return_value=True),
        patch("dislocker_ui.gui.discover_privileged_deps", return_value=_core_deps()),
        patch("dislocker_ui.gui.mount_volume", return_value=_sample_session()) as mount,
        patch("dislocker_ui.gui.load_session", return_value=_sample_session()),
        patch("dislocker_ui.gui.messagebox.showinfo"),
    ):
        app.on_mount()
    mount.assert_called_once()


def test_on_mount_noop_when_busy(tk_root: tk.Tk) -> None:
    """Mount is ignored while a previous operation is in progress."""
    app = _build_app(tk_root)
    app._busy = True
    with patch("dislocker_ui.gui.mount_volume") as mount_volume:
        app.on_mount()
    mount_volume.assert_not_called()


def test_on_mount_success_builds_request_and_dialog(tk_root: tk.Tk, sync_threads: None) -> None:
    """Successful Mount builds MountRequest, shows info, refreshes status."""
    app = _build_app(tk_root, deps=_core_deps(ntfs3g="/bin/ntfs-3g"))
    app.secret_var.set("secret-password")
    app.readonly_var.set(False)
    session = _sample_session(readonly=False)

    with (
        patch("dislocker_ui.gui.mount_volume", return_value=session) as mount_volume,
        patch("dislocker_ui.gui.messagebox.showinfo") as showinfo,
        patch("dislocker_ui.gui.load_session", return_value=session),
    ):
        app.on_mount()

    assert not app._busy
    mount_volume.assert_called_once()
    req = mount_volume.call_args.args[0]
    assert isinstance(req, MountRequest)
    assert req.volume == "/dev/disk2s1"
    assert req.method == UnlockMethod.USER_PASSWORD
    assert req.secret == "secret-password"
    assert req.readonly is False
    showinfo.assert_called_once()
    assert "/Volumes/DislockerUI" in showinfo.call_args.args[1]
    assert "Active session (RW)" in app.status_var.get()
    assert "Mount starting" in app.log_text.get("1.0", tk.END)


@pytest.mark.parametrize(
    "exc",
    [RunnerError("bad password"), RuntimeError("boom")],
)
def test_on_mount_error_shows_dialog(tk_root: tk.Tk, sync_threads: None, exc: Exception) -> None:
    """RunnerError and unexpected exceptions both surface a Mount failed dialog."""
    app = _build_app(tk_root)
    with (
        patch("dislocker_ui.gui.mount_volume", side_effect=exc),
        patch("dislocker_ui.gui.messagebox.showerror") as showerror,
    ):
        app.on_mount()

    assert not app._busy
    showerror.assert_called_once()
    assert showerror.call_args.args[0] == "Mount failed"
    assert str(exc) in showerror.call_args.args[1]
    assert f"ERROR: {exc}" in app.log_text.get("1.0", tk.END)


def test_on_mount_cancel_shows_info_dialog(tk_root: tk.Tk, sync_threads: None) -> None:
    """ElevationCancelled uses a non-scary info dialog, not Mount failed."""
    from dislocker_ui.elevate import ElevationCancelled

    app = _build_app(tk_root)
    with (
        patch(
            "dislocker_ui.gui.mount_volume",
            side_effect=ElevationCancelled("cancelled"),
        ),
        patch("dislocker_ui.gui.messagebox.showinfo") as showinfo,
        patch("dislocker_ui.gui.messagebox.showerror") as showerror,
    ):
        app.on_mount()

    showerror.assert_not_called()
    showinfo.assert_called_once()
    assert showinfo.call_args.args[0] == "Cancelled"
    assert "cancelled" in showinfo.call_args.args[1].lower()


def test_on_unmount_success(tk_root: tk.Tk, sync_threads: None) -> None:
    """Successful Unmount shows confirmation and clears session status."""
    app = _build_app(tk_root, session=_sample_session())
    with (
        patch("dislocker_ui.gui.unmount_volume") as unmount_volume,
        patch("dislocker_ui.gui.messagebox.showinfo") as showinfo,
        patch("dislocker_ui.gui.load_session", return_value=None),
    ):
        app.on_unmount()

    assert not app._busy
    unmount_volume.assert_called_once()
    showinfo.assert_called_once()
    assert app.status_var.get() == "No active session"
    assert "Unmount starting" in app.log_text.get("1.0", tk.END)


@pytest.mark.parametrize(
    "exc",
    [RunnerError("still busy"), OSError("detach")],
)
def test_on_unmount_error_shows_dialog(tk_root: tk.Tk, sync_threads: None, exc: Exception) -> None:
    """RunnerError and unexpected exceptions both surface an Unmount failed dialog."""
    app = _build_app(tk_root)
    with (
        patch("dislocker_ui.gui.unmount_volume", side_effect=exc),
        patch("dislocker_ui.gui.messagebox.showerror") as showerror,
    ):
        app.on_unmount()

    assert not app._busy
    showerror.assert_called_once()
    assert showerror.call_args.args[0] == "Unmount failed"
    assert str(exc) in showerror.call_args.args[1]


def test_on_unmount_noop_when_busy(tk_root: tk.Tk) -> None:
    """Unmount is ignored while busy."""
    app = _build_app(tk_root)
    app._busy = True
    with patch("dislocker_ui.gui.unmount_volume") as unmount_volume:
        app.on_unmount()
    unmount_volume.assert_not_called()


def test_set_busy_disables_buttons(tk_root: tk.Tk) -> None:
    """Busy mode disables Mount and Unmount actions."""
    app = _build_app(tk_root)
    app._set_busy(True)
    assert str(app.mount_btn.cget("state")) == str(tk.DISABLED)
    assert str(app.unmount_btn.cget("state")) == str(tk.DISABLED)
    app._set_busy(False)
    assert str(app.mount_btn.cget("state")) == str(tk.NORMAL)
    assert str(app.unmount_btn.cget("state")) == str(tk.NORMAL)


def test_run_app_uses_aqua_when_available() -> None:
    """run_app constructs the root, prefers aqua, raises to front, and enters mainloop."""
    root = MagicMock()
    style = MagicMock()
    style.theme_names.return_value = ("aqua", "clam")

    with (
        patch("dislocker_ui.gui.tk.Tk", return_value=root),
        patch("dislocker_ui.gui.ttk.Style", return_value=style),
        patch("dislocker_ui.gui.DislockerApp") as app_cls,
        patch("dislocker_ui.gui._raise_root_window") as raise_root,
    ):
        run_app()

    style.theme_use.assert_called_once_with("aqua")
    app_cls.assert_called_once_with(root)
    raise_root.assert_called_once_with(root)
    root.mainloop.assert_called_once()


def test_run_app_ignores_style_tcl_error() -> None:
    """Theme selection TclError is ignored so the app still starts."""
    root = MagicMock()

    with (
        patch("dislocker_ui.gui.tk.Tk", return_value=root),
        patch("dislocker_ui.gui.ttk.Style", side_effect=tk.TclError("no display")),
        patch("dislocker_ui.gui.DislockerApp") as app_cls,
        patch("dislocker_ui.gui._raise_root_window") as raise_root,
    ):
        run_app()

    app_cls.assert_called_once_with(root)
    raise_root.assert_called_once_with(root)
    root.mainloop.assert_called_once()


def test_raise_root_window_pulses_topmost() -> None:
    """First-launch raise uses a temporary topmost attribute then clears it."""
    root = MagicMock()
    callbacks: list[tuple[int, object]] = []

    def capture_after(delay: int, fn: object) -> None:
        callbacks.append((delay, fn))

    root.after.side_effect = capture_after
    _raise_root_window(root)

    root.update_idletasks.assert_called_once()
    root.deiconify.assert_called_once()
    root.lift.assert_called_once()
    root.focus_force.assert_called_once()
    root.attributes.assert_any_call("-topmost", True)
    assert callbacks
    assert callbacks[0][0] == 50
    callbacks[0][1]()
    root.attributes.assert_any_call("-topmost", False)


def test_password_toggle_defaults_hidden(tk_root: tk.Tk) -> None:
    """Password field starts masked, toggle button shows 'Show'."""
    app = _build_app(tk_root)

    assert app.secret_entry.cget("show") == "*"
    assert not app.pwd_visible
    assert app.pwd_toggle_btn.cget("text") == "Show"


def test_password_toggle_shows_and_hides(tk_root: tk.Tk) -> None:
    """Toggle button shows password, changes text, and hides again."""
    app = _build_app(tk_root)

    # Initial state
    assert app.secret_entry.cget("show") == "*"
    assert app.pwd_toggle_btn.cget("text") == "Show"

    # Click to show
    app._toggle_password_visibility()
    assert app.secret_entry.cget("show") == ""
    assert app.pwd_visible
    assert app.pwd_toggle_btn.cget("text") == "Hide"

    # Click to hide
    app._toggle_password_visibility()
    assert app.secret_entry.cget("show") == "*"
    assert not app.pwd_visible
    assert app.pwd_toggle_btn.cget("text") == "Show"


def test_password_toggle_schedules_auto_hide_timer(tk_root: tk.Tk) -> None:
    """Showing password schedules a 30-second auto-hide timer."""
    app = _build_app(tk_root)

    with patch.object(app.master, "after", wraps=app.master.after) as after:
        app._toggle_password_visibility()

    # Timer should be scheduled with correct delay and callback
    after.assert_called_once_with(PWD_AUTO_HIDE_MS, app._auto_hide_password)
    assert app._pwd_hide_timer is not None
    assert app.pwd_visible


def test_password_toggle_manual_hide_cancels_timer(tk_root: tk.Tk) -> None:
    """Manually hiding password cancels the auto-hide timer."""
    app = _build_app(tk_root)

    # Show (starts timer)
    app._toggle_password_visibility()
    timer_id = app._pwd_hide_timer
    assert timer_id is not None

    # Hide (cancels timer)
    app._toggle_password_visibility()
    assert app._pwd_hide_timer is None
    assert not app.pwd_visible


def test_password_toggle_auto_hide_callback(tk_root: tk.Tk) -> None:
    """Auto-hide callback toggles password back to hidden."""
    app = _build_app(tk_root)

    app._toggle_password_visibility()  # show
    assert app.pwd_visible

    app._auto_hide_password()  # simulate timer callback
    assert not app.pwd_visible
    assert app.secret_entry.cget("show") == "*"
    assert app._pwd_hide_timer is None


def test_password_toggle_hidden_in_bek_mode(tk_root: tk.Tk) -> None:
    """Toggle button hidden when BEK file mode is active."""
    app = _build_app(tk_root)

    # User password mode: toggle visible
    assert app.method_var.get() == UnlockMethod.USER_PASSWORD.value
    # (Initial pack in _build ensures it's present)

    # Switch to BEK mode
    app.method_var.set(UnlockMethod.BEK_FILE.value)
    app._on_method_change()

    # Toggle button should be forgotten (not packed)
    # Check winfo_manager returns empty string when unpacked
    assert app.pwd_toggle_btn.winfo_manager() == ""
    assert app.secret_entry.cget("show") == ""  # BEK path shown in clear


def test_password_toggle_hidden_in_recovery_password_mode(tk_root: tk.Tk) -> None:
    """Toggle button hidden for recovery password mode."""
    app = _build_app(tk_root)

    # Switch to recovery password mode
    app.method_var.set(UnlockMethod.RECOVERY_PASSWORD.value)
    app._on_method_change()

    # Toggle button should be forgotten
    assert app.pwd_toggle_btn.winfo_manager() == ""
    assert app.secret_entry.cget("show") == "*"  # recovery pwd still masked


def test_password_toggle_resets_on_method_change_while_visible(tk_root: tk.Tk) -> None:
    """Changing method while password is visible hides it and cancels timer."""
    app = _build_app(tk_root)

    # Show password
    app._toggle_password_visibility()
    assert app.pwd_visible

    # Switch to BEK mode
    app.method_var.set(UnlockMethod.BEK_FILE.value)
    app._on_method_change()

    # Password should be hidden, timer cancelled
    assert not app.pwd_visible
    assert app._pwd_hide_timer is None


def test_discover_deps_for_euid_root() -> None:
    from dislocker_ui.gui import _discover_deps_for_euid

    expected = _core_deps()
    with (
        patch("dislocker_ui.gui.os.geteuid", return_value=0),
        patch("dislocker_ui.gui.discover_privileged_deps", return_value=expected),
    ):
        assert _discover_deps_for_euid() is expected


def test_discover_deps_for_euid_non_root() -> None:
    from dislocker_ui.gui import _discover_deps_for_euid

    expected = _core_deps()
    with (
        patch("dislocker_ui.gui.os.geteuid", return_value=501),
        patch("dislocker_ui.gui.discover_deps", return_value=expected),
    ):
        assert _discover_deps_for_euid() is expected


def test_app_init_uses_privileged_deps_when_root(tk_root: tk.Tk) -> None:
    """When euid==0, DislockerApp uses discover_privileged_deps, not discover_deps."""
    deps = _core_deps()
    with (
        patch("dislocker_ui.gui.os.geteuid", return_value=0),
        patch("dislocker_ui.gui.discover_privileged_deps", return_value=deps) as priv,
        patch("dislocker_ui.gui.discover_deps") as unpriv,
        patch("dislocker_ui.gui.list_disk_entries", return_value=_sample_disks()),
        patch("dislocker_ui.gui.load_session", return_value=None),
    ):
        DislockerApp(tk_root)
    priv.assert_called_once()
    unpriv.assert_not_called()
