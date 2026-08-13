"""
tkinter GUI for dislocker-ui.

Overall purpose:
  Present a small window to pick a volume, unlock method, and mount/unmount
  BitLocker volumes via the runner module.

Inputs:
  User interactions (volume, credentials, buttons).

Outputs:
  Side effects through runner.mount_volume / unmount_volume; log text in UI.

Requirements:
  Python tkinter; dislocker toolchain described in README.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from dislocker_ui import __version__
from dislocker_ui.deps import DepsStatus, discover_deps, discover_privileged_deps
from dislocker_ui.disks import DiskEntry, list_disk_entries
from dislocker_ui.elevate import ElevationCancelled, ElevationTimedOut, needs_elevation
from dislocker_ui.runner import (
    MountRequest,
    RunnerError,
    UnlockMethod,
    mount_volume,
    unmount_volume,
)
from dislocker_ui.session import active_session_path_for_user, load_session

__all__ = ["PWD_AUTO_HIDE_MS", "DislockerApp", "_raise_root_window", "run_app"]

PWD_AUTO_HIDE_MS = 30_000


def _discover_deps_for_euid() -> DepsStatus:
    """Use trusted fixed paths when running as root; PATH-based discovery otherwise.

    When ``sudo ./run.sh`` launches the GUI as root, ``shutil.which`` follows
    the caller's effective PATH which may include attacker-writable directories.
    The privileged child already uses :func:`discover_privileged_deps`; mirror
    that for the root-GUI path so mount tools always come from root-managed
    locations.
    """
    if os.geteuid() == 0:
        return discover_privileged_deps()
    return discover_deps()


class DislockerApp(ttk.Frame):
    """Main application frame."""

    def __init__(self, master: tk.Tk) -> None:
        """Build the main frame and load initial deps, disks, and session state."""
        super().__init__(master, padding=12)
        self.master = master
        self.deps: DepsStatus = _discover_deps_for_euid()
        self.disk_entries: list[DiskEntry] = []
        self._busy = False

        self.volume_var = tk.StringVar()
        self.method_var = tk.StringVar(value=UnlockMethod.USER_PASSWORD.value)
        self.secret_var = tk.StringVar()
        self.readonly_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="")
        self.pwd_visible = False
        self._pwd_hide_timer: str | None = None

        self.pack(fill=tk.BOTH, expand=True)
        self._build()
        self._refresh_deps_label()
        self.refresh_disks()
        self._refresh_session_status()

    def _build(self) -> None:
        """Construct widgets."""
        self.master.title(f"dislocker-ui {__version__}")
        self.master.minsize(640, 480)

        deps_row = ttk.Frame(self)
        deps_row.pack(fill=tk.X, pady=(0, 8))
        self.deps_label = ttk.Label(deps_row, text="", wraplength=600, justify=tk.LEFT)
        self.deps_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(deps_row, text="Recheck deps", command=self._recheck_deps).pack(side=tk.RIGHT)

        vol_frame = ttk.LabelFrame(self, text="Volume", padding=8)
        vol_frame.pack(fill=tk.X, pady=4)
        self.disk_combo = ttk.Combobox(vol_frame, textvariable=self.volume_var, width=70)
        self.disk_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(vol_frame, text="Refresh", command=self.refresh_disks).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        method_frame = ttk.LabelFrame(self, text="Unlock method", padding=8)
        method_frame.pack(fill=tk.X, pady=4)
        methods = [
            ("User password", UnlockMethod.USER_PASSWORD.value),
            ("Recovery password", UnlockMethod.RECOVERY_PASSWORD.value),
            (".bek file", UnlockMethod.BEK_FILE.value),
        ]
        for label, value in methods:
            ttk.Radiobutton(
                method_frame,
                text=label,
                value=value,
                variable=self.method_var,
                command=self._on_method_change,
            ).pack(anchor=tk.W)

        secret_frame = ttk.Frame(self)
        secret_frame.pack(fill=tk.X, pady=4)
        self.secret_label = ttk.Label(secret_frame, text="Password:")
        self.secret_label.pack(side=tk.LEFT)
        self.secret_entry = ttk.Entry(secret_frame, textvariable=self.secret_var, show="*")
        self.secret_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.pwd_toggle_btn = ttk.Button(
            secret_frame,
            text="Show",
            width=5,
            command=self._toggle_password_visibility,
        )
        self.pwd_toggle_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.bek_button = ttk.Button(
            secret_frame, text="Browse…", command=self._browse_bek, state=tk.DISABLED
        )
        self.bek_button.pack(side=tk.LEFT)

        opts = ttk.Frame(self)
        opts.pack(fill=tk.X, pady=4)
        self.readonly_check = ttk.Checkbutton(
            opts,
            text="Read-only (recommended)",
            variable=self.readonly_var,
        )
        self.readonly_check.pack(anchor=tk.W)
        self.rw_hint = ttk.Label(opts, text="", foreground="#555")
        self.rw_hint.pack(anchor=tk.W)
        self._update_rw_hint()

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, pady=8)
        self.mount_btn = ttk.Button(btns, text="Mount", command=self.on_mount)
        self.mount_btn.pack(side=tk.LEFT)
        self.unmount_btn = ttk.Button(btns, text="Unmount", command=self.on_unmount)
        self.unmount_btn.pack(side=tk.LEFT, padx=6)

        ttk.Label(self, textvariable=self.status_var).pack(anchor=tk.W)

        log_frame = ttk.LabelFrame(self, text="Log", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.log_text = tk.Text(log_frame, height=16, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scroll.set)

    def _recheck_deps(self) -> None:
        """Re-run dependency discovery."""
        self.deps = _discover_deps_for_euid()
        self._refresh_deps_label()
        self._update_rw_hint()
        self.log("Dependency check refreshed")

    def _refresh_deps_label(self) -> None:
        """Update the dependency summary line."""
        if self.deps.core_ok:
            ntfs_note = (
                "ntfs-3g present (NTFS OK)."
                if self.deps.has_ntfs3g
                else "ntfs-3g missing (FAT/ExFAT OK; NTFS needs ntfs-3g)."
            )
            text = f"dislocker: {self.deps.dislocker_fuse}\nCore tools OK. {ntfs_note}"
        else:
            missing = ", ".join(self.deps.missing_core())
            text = f"Missing required tools: {missing}. See README."
        self.deps_label.configure(text=text)

    def _update_rw_hint(self) -> None:
        """Enable RW only when ntfs-3g is present; otherwise force read-only."""
        if self.deps.can_write:
            self.rw_hint.configure(
                text="Uncheck Read-only for writable NTFS (ntfs-3g) or FAT/ExFAT mounts."
            )
            self.readonly_check.configure(state=tk.NORMAL)
        elif self.deps.core_ok:
            # Mounts still work (FAT/ExFAT RO; NTFS blocked later without ntfs-3g).
            self.readonly_var.set(True)
            self.rw_hint.configure(
                text="ntfs-3g missing — read-only only (FAT/ExFAT OK; NTFS needs ntfs-3g)."
            )
            self.readonly_check.configure(state=tk.DISABLED)
        else:
            self.readonly_var.set(True)
            self.rw_hint.configure(
                text="Core tools missing — mounts are unavailable until they are installed."
            )
            self.readonly_check.configure(state=tk.DISABLED)

    def _on_method_change(self) -> None:
        """Toggle password vs BEK browse UI; manage visibility toggle button."""
        method = self.method_var.get()

        # Clear any existing secret to prevent stale data exposure
        self.secret_var.set("")

        # Reset password visibility if currently showing
        if self.pwd_visible:
            self._toggle_password_visibility()

        if method == UnlockMethod.BEK_FILE.value:
            # BEK mode: show path in clear text, hide toggle button
            self.secret_label.configure(text="BEK path:")
            self.secret_entry.configure(show="")
            self.pwd_toggle_btn.pack_forget()
            self.bek_button.configure(state=tk.NORMAL)
        else:
            # Password modes (user or recovery): both masked, both disable BEK button
            self.secret_label.configure(text="Password:")
            self.secret_entry.configure(show="*")
            self.bek_button.configure(state=tk.DISABLED)

            # Toggle button visible ONLY for user password mode
            if method == UnlockMethod.USER_PASSWORD.value:
                # Re-pack both buttons in correct order to fix layout after pack_forget
                self.bek_button.pack_forget()
                self.pwd_toggle_btn.pack(side=tk.LEFT, padx=(0, 6))
                self.bek_button.pack(side=tk.LEFT)
            else:
                # Recovery password mode: hide toggle button
                self.pwd_toggle_btn.pack_forget()

    def _toggle_password_visibility(self) -> None:
        """Toggle password field between masked and clear text."""
        self.pwd_visible = not self.pwd_visible
        if self.pwd_visible:
            self.secret_entry.config(show="")
            self.pwd_toggle_btn.config(text="Hide")
            # Start auto-hide timer (30 seconds, fixed countdown)
            self._pwd_hide_timer = self.master.after(PWD_AUTO_HIDE_MS, self._auto_hide_password)
        else:
            self.secret_entry.config(show="*")
            self.pwd_toggle_btn.config(text="Show")
            # Cancel timer if exists
            if self._pwd_hide_timer is not None:
                self.master.after_cancel(self._pwd_hide_timer)
                self._pwd_hide_timer = None

    def _auto_hide_password(self) -> None:
        """Automatically hide password after 30-second timeout."""
        self._pwd_hide_timer = None
        if self.pwd_visible:
            # Delegate to toggle to avoid state duplication
            self._toggle_password_visibility()

    def _browse_bek(self) -> None:
        """Pick a .bek file."""
        path = filedialog.askopenfilename(
            title="Select BEK file",
            filetypes=[("BEK files", "*.BEK *.bek"), ("All files", "*.*")],
        )
        if path:
            self.secret_var.set(path)

    def refresh_disks(self) -> None:
        """Reload disk list into the combobox."""
        try:
            self.disk_entries = list_disk_entries()
        except RuntimeError as exc:
            self.log(f"diskutil error: {exc}")
            self.disk_entries = []
        values = [e.summary for e in self.disk_entries]
        self.disk_combo.configure(values=values)
        if values and not self.volume_var.get():
            self.volume_var.set(values[0])

    def _refresh_session_status(self) -> None:
        """Show whether a session is currently recorded."""
        session = load_session(active_session_path_for_user())
        if session:
            mode = "RO" if session.readonly else "RW"
            self.status_var.set(f"Active session ({mode}): {session.ntfs_mount}")
        else:
            self.status_var.set("No active session")

    def log(self, message: str) -> None:
        """Append a log line (thread-safe via after)."""

        def _append() -> None:
            """Insert one log line on the UI thread."""
            self.log_text.insert(tk.END, message.rstrip() + "\n")
            self.log_text.see(tk.END)

        self.master.after(0, _append)

    def _set_busy(self, busy: bool) -> None:
        """Disable actions while a worker thread runs."""
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.mount_btn.configure(state=state)
        self.unmount_btn.configure(state=state)

    def _resolve_volume(self) -> str:
        """Map combobox display text or raw path to a device path."""
        raw = self.volume_var.get().strip()
        for entry in self.disk_entries:
            if raw == entry.summary or raw == entry.device:
                return entry.device
        # Allow typing /dev/diskXsY or a file path directly
        if "  (" in raw:
            return raw.split("  (", 1)[0].strip()
        return raw

    def on_mount(self) -> None:
        """Handle Mount button."""
        if self._busy:
            return
        if not self.deps.core_ok:
            messagebox.showerror("Missing tools", "Install required tools first (see README).")
            return
        if needs_elevation():
            privileged_deps = discover_privileged_deps()
            if not privileged_deps.core_ok:
                missing = ", ".join(privileged_deps.missing_core())
                messagebox.showerror(
                    "Privileged tools unavailable",
                    "Mounting requires root-managed tools before administrator authorization.\n\n"
                    f"Missing trusted tools: {missing}\n\n"
                    "An administrator must install dislocker-fuse under "
                    "/usr/local/sbin or /opt/local/sbin (ntfs-3g too for NTFS). See README.\n\n"
                    "One-time install: sudo scripts/install-root-deps.sh (from the repo root).",
                )
                return

        method = UnlockMethod(self.method_var.get())
        req = MountRequest(
            volume=self._resolve_volume(),
            method=method,
            secret=self.secret_var.get(),
            readonly=bool(self.readonly_var.get()),
        )

        def worker() -> None:
            """Background mount worker; posts results back to the UI thread."""
            try:
                session = mount_volume(req, self.deps, self.log)
                mount_path = session.ntfs_mount
                self.master.after(
                    0,
                    lambda path=mount_path: messagebox.showinfo(
                        "Mounted", f"Available at:\n{path}"
                    ),
                )
            except ElevationCancelled as exc:
                self.log(f"Cancelled: {exc}")
                self.master.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Cancelled",
                        "Administrator authorization was cancelled. Nothing was mounted.",
                    ),
                )
            except ElevationTimedOut as exc:
                self.log(f"ERROR: {exc}")
                self.master.after(
                    0,
                    lambda: messagebox.showerror(
                        "Authorization timed out",
                        "The administrator prompt timed out. Try Mount again "
                        "and complete the password dialog promptly.",
                    ),
                )
            except RunnerError as exc:
                self.log(f"ERROR: {exc}")
                err = str(exc)
                self.master.after(0, lambda msg=err: messagebox.showerror("Mount failed", msg))
            except Exception as exc:
                self.log(f"ERROR: {exc}")
                err = str(exc)
                self.master.after(0, lambda msg=err: messagebox.showerror("Mount failed", msg))
            finally:
                self.master.after(0, lambda: self._set_busy(False))
                self.master.after(0, self._refresh_session_status)

        self._set_busy(True)
        self.log("— Mount starting —")
        threading.Thread(target=worker, daemon=True).start()

    def on_unmount(self) -> None:
        """Handle Unmount button."""
        if self._busy:
            return

        def worker() -> None:
            """Background unmount worker; posts results back to the UI thread."""
            try:
                unmount_volume(self.deps, self.log)
                self.master.after(0, lambda: messagebox.showinfo("Unmounted", "Volume unmounted."))
            except ElevationCancelled as exc:
                self.log(f"Cancelled: {exc}")
                self.master.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Cancelled",
                        "Administrator authorization was cancelled. "
                        "The volume may still be mounted.",
                    ),
                )
            except ElevationTimedOut as exc:
                self.log(f"ERROR: {exc}")
                self.master.after(
                    0,
                    lambda: messagebox.showerror(
                        "Authorization timed out",
                        "The administrator prompt timed out. Try Unmount again.",
                    ),
                )
            except RunnerError as exc:
                self.log(f"ERROR: {exc}")
                err = str(exc)
                self.master.after(0, lambda msg=err: messagebox.showerror("Unmount failed", msg))
            except Exception as exc:
                self.log(f"ERROR: {exc}")
                err = str(exc)
                self.master.after(0, lambda msg=err: messagebox.showerror("Unmount failed", msg))
            finally:
                self.master.after(0, lambda: self._set_busy(False))
                self.master.after(0, self._refresh_session_status)

        self._set_busy(True)
        self.log("— Unmount starting —")
        threading.Thread(target=worker, daemon=True).start()


def _raise_root_window(root: tk.Tk) -> None:
    """Bring the main window to the front on first launch.

    When started from Terminal (or another app), macOS often opens Tk behind the
    launcher. A brief ``-topmost`` pulse plus ``lift`` / ``focus_force`` makes
    the window visible without keeping it permanently always-on-top.
    """
    try:
        root.update_idletasks()
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(50, lambda: root.attributes("-topmost", False))
        root.focus_force()
    except tk.TclError:
        pass


def run_app() -> None:
    """Create the Tk root and start the main loop."""
    root = tk.Tk()
    # Prefer a native-ish theme when available
    try:
        style = ttk.Style(root)
        if "aqua" in style.theme_names():
            style.theme_use("aqua")
    except tk.TclError:
        pass
    DislockerApp(root)
    _raise_root_window(root)
    root.mainloop()
