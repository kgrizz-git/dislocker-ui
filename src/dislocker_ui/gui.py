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

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from dislocker_ui import __version__
from dislocker_ui.deps import DepsStatus, discover_deps
from dislocker_ui.disks import DiskEntry, list_disk_entries
from dislocker_ui.runner import MountRequest, RunnerError, UnlockMethod, mount_volume, unmount_volume
from dislocker_ui.session import load_session


class DislockerApp(ttk.Frame):
    """Main application frame."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.deps: DepsStatus = discover_deps()
        self.disk_entries: list[DiskEntry] = []
        self._busy = False

        self.volume_var = tk.StringVar()
        self.method_var = tk.StringVar(value=UnlockMethod.USER_PASSWORD.value)
        self.secret_var = tk.StringVar()
        self.readonly_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="")

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
        ttk.Button(deps_row, text="Recheck deps", command=self._recheck_deps).pack(
            side=tk.RIGHT
        )

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
        self.deps = discover_deps()
        self._refresh_deps_label()
        self._update_rw_hint()
        self.log("Dependency check refreshed")

    def _refresh_deps_label(self) -> None:
        """Update the dependency summary line."""
        if self.deps.core_ok:
            write = "RW available (ntfs-3g found)" if self.deps.can_write else "RW needs ntfs-3g"
            text = (
                f"dislocker: {self.deps.dislocker_fuse}\n"
                f"Core tools OK. {write}."
            )
        else:
            missing = ", ".join(self.deps.missing_core())
            text = f"Missing required tools: {missing}. See README."
        self.deps_label.configure(text=text)

    def _update_rw_hint(self) -> None:
        """Enable/disable RW based on ntfs-3g presence."""
        if self.deps.can_write:
            self.rw_hint.configure(
                text="Uncheck Read-only to mount with ntfs-3g (writable)."
            )
            self.readonly_check.configure(state=tk.NORMAL)
        else:
            self.readonly_var.set(True)
            self.rw_hint.configure(
                text="ntfs-3g not found — only read-only mounts are offered."
            )

    def _on_method_change(self) -> None:
        """Toggle password vs BEK browse UI."""
        if self.method_var.get() == UnlockMethod.BEK_FILE.value:
            self.secret_label.configure(text="BEK path:")
            self.secret_entry.configure(show="")
            self.bek_button.configure(state=tk.NORMAL)
        else:
            self.secret_label.configure(text="Password:")
            self.secret_entry.configure(show="*")
            self.bek_button.configure(state=tk.DISABLED)

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
        session = load_session()
        if session:
            mode = "RO" if session.readonly else "RW"
            self.status_var.set(f"Active session ({mode}): {session.ntfs_mount}")
        else:
            self.status_var.set("No active session")

    def log(self, message: str) -> None:
        """Append a log line (thread-safe via after)."""

        def _append() -> None:
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

        method = UnlockMethod(self.method_var.get())
        req = MountRequest(
            volume=self._resolve_volume(),
            method=method,
            secret=self.secret_var.get(),
            readonly=bool(self.readonly_var.get()),
        )

        def worker() -> None:
            try:
                session = mount_volume(req, self.deps, self.log)
                self.master.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Mounted", f"Available at:\n{session.ntfs_mount}"
                    ),
                )
            except RunnerError as exc:
                self.log(f"ERROR: {exc}")
                self.master.after(
                    0, lambda: messagebox.showerror("Mount failed", str(exc))
                )
            except Exception as exc:  # noqa: BLE001 — surface unexpected errors in UI
                self.log(f"ERROR: {exc}")
                self.master.after(
                    0, lambda: messagebox.showerror("Mount failed", str(exc))
                )
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
            try:
                unmount_volume(self.deps, self.log)
                self.master.after(
                    0, lambda: messagebox.showinfo("Unmounted", "Volume unmounted.")
                )
            except RunnerError as exc:
                self.log(f"ERROR: {exc}")
                self.master.after(
                    0, lambda: messagebox.showerror("Unmount failed", str(exc))
                )
            except Exception as exc:  # noqa: BLE001
                self.log(f"ERROR: {exc}")
                self.master.after(
                    0, lambda: messagebox.showerror("Unmount failed", str(exc))
                )
            finally:
                self.master.after(0, lambda: self._set_busy(False))
                self.master.after(0, self._refresh_session_status)

        self._set_busy(True)
        self.log("— Unmount starting —")
        threading.Thread(target=worker, daemon=True).start()


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
    root.mainloop()
