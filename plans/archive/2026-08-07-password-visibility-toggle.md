# Password Visibility Toggle

**Created:** 2026-08-07  
**Updated:** 2026-08-07 (post-review + 2nd assessment cleanup)  
**Status:** Planning  
**Target Version:** 0.5.0

**Assessments:**
- `tmp/2026-08-07T13-47-08Z-plan-assessment-password-visibility-toggle.md` — corrected type annotation (int | None), line references, added implementation notes
- `tmp/2026-08-07T13-58-43Z-plan-assessment-password-visibility-toggle.md` — cleaned up snippet duplication, fixed References section, simplified _on_method_change, added teardown snippet

## Goal

Add a button to the dislocker-ui GUI that allows users to toggle password visibility between masked (`•••`) and clear text. The feature should:

- Default to masked/hidden (current behavior)
- Support user password fields (not recovery passwords or BEK file paths)
- Provide clear visual indication of current state
- Follow macOS UI conventions

## Rationale

Users occasionally mistype passwords and need to verify input. A toggle button is standard in modern UIs (browsers, password managers, mobile apps). This improves usability without compromising security — the password is only visible when the user explicitly requests it and remains hidden by default.

## Design Options

### Option A: Toggle Button (Persistent State)
- Button with eye icon (👁️ or similar)
- Click once → show password, icon changes to crossed-out eye
- Click again → hide password, icon returns to eye
- State persists until changed or window closed

**Pros:**
- Standard pattern (most web forms use this)
- Works well for carefully reviewing a long password
- Single click for sustained viewing

**Cons:**
- Password remains visible if user forgets to toggle back
- Risk if user walks away from screen

### Option B: Hold-to-Show Button (Transient)
- Button with eye icon
- Press and hold → show password
- Release → hide password immediately
- No persistent state

**Pros:**
- More secure — password auto-hides on release
- Forces active engagement
- Better for shared/public environments

**Cons:**
- Awkward for long passwords requiring scrolling
- Requires holding mouse button (accessibility concern)

### Recommended: Option A (Toggle) with Security Consideration

Use a **toggle button** (Option A) but add a timeout: if the password is visible for more than 30 seconds, automatically mask it again. This is a **fixed countdown** from the moment "Show" is clicked — it does NOT reset on window interaction. Rationale: simpler implementation, predictable behavior, still mitigates "walk away from screen" risk. This balances usability with security.

## Implementation Plan

### 1. GUI Changes (`src/dislocker_ui/gui.py`)

**Current password field structure (lines 96-105 in `_build()`):**
```python
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
```

**Add toggle state variables in `__init__` (after line 53, after existing vars):**

```python
# After self.status_var = tk.StringVar(value="")
self.pwd_visible = False
self._pwd_hide_timer: int | None = None  # after() returns int
```

**Add toggle button in `_build()` (insert after `self.secret_entry.pack(...)` line 101):**

```python
# After: self.secret_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

# NEW: Password visibility toggle button
self.pwd_toggle_btn = ttk.Button(
    secret_frame,
    text="Show",
    width=5,
    command=self._toggle_password_visibility,
)
self.pwd_toggle_btn.pack(side=tk.LEFT, padx=(0, 6))

# (existing self.bek_button follows)
```

**Toggle callback (add new method):**

```python
def _toggle_password_visibility(self) -> None:
    """Toggle password field between masked and clear text."""
    self.pwd_visible = not self.pwd_visible
    if self.pwd_visible:
        self.secret_entry.config(show="")
        self.pwd_toggle_btn.config(text="Hide")
        # Start auto-hide timer (30 seconds, fixed countdown)
        if self._pwd_hide_timer is not None:
            self.master.after_cancel(self._pwd_hide_timer)
        self._pwd_hide_timer = self.master.after(30000, self._auto_hide_password)
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
```

**Note:** The explicit `self._pwd_hide_timer = None` before delegating is safe (the toggle's cancel is guarded by `is not None`) but slightly redundant. Optional simplification: just call `_toggle_password_visibility()` directly and let it own the nulling.

**Update `_on_method_change()` to manage toggle button visibility and state:**

Extend the existing 2-way `_on_method_change` method to add a recovery-password branch (note: user password mode re-packs both buttons to maintain correct visual order after pack_forget):

```python
def _on_method_change(self) -> None:
    """Toggle password vs BEK browse UI; manage visibility toggle button."""
    method = self.method_var.get()

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
```

### 2. Layout Adjustments

No width adjustment needed — the `secret_entry` uses `pack(fill=tk.X, expand=True)` so it automatically adjusts to available space. The toggle button (`width=5`) and BEK button fit naturally with existing `padx` spacing.

### 3. Icon/Text Choice

**Options:**
- Unicode emoji: `👁` (show) / `🙈` or `👁‍🗨` (hide) — simple, no assets needed
- Text labels: `Show` / `Hide` — more explicit, better accessibility
- Custom icon images: requires bundling assets (out of scope for v1)

**Decision:** Use **text labels** (`Show` / `Hide`). Rationale:
- Emoji rendering in `ttk.Button` with the "aqua" theme can be inconsistent
- Text is unambiguous and accessible (screen readers, keyboard-only users)
- `Show`/`Hide` is standard across platforms (browsers, password managers)
- No concerns about emoji Unicode support or font availability

`🚫` (U+1F6AB "no entry sign") was semantically wrong anyway — it means "forbidden," not "hide."

### 4. Testing

**Automated tests** (add to `tests/test_gui.py`):

The project has **80% coverage requirement**. Adding new methods without tests will break CI. Add these tests using the existing `_build_app` fixture pattern:

```python
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

    app._toggle_password_visibility()

    # Timer should be scheduled
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
```

**Manual GUI tests** (supplement automated tests):

1. **Visual rendering:**
   - Confirm "Show"/"Hide" button text is readable on macOS 12+
   - Verify layout doesn't break when toggling

2. **Timer behavior (requires waiting):**
   - Show password, wait 30 seconds → password auto-masks
   - Show password, manually hide at 15 seconds → no auto-hide fires

3. **Edge cases:**
   - Rapid clicking of toggle → no state corruption
   - Very long password (100+ chars) → toggle works, field scrolls correctly

### 5. Documentation Updates

**`CHANGELOG.md`** (version 0.5.0):

```markdown
## [0.5.0] - 2026-MM-DD

### Added
- Password visibility toggle button for user password field. Click "Show" to
  reveal password in clear text (default: hidden). Passwords automatically
  re-mask after 30 seconds.
```

**`README.md`** (Usage section):

Add a note:
```markdown
4. Choose unlock method: user password, recovery password, or `.bek` file.
   - For user passwords, click the "Show" button to briefly reveal the password
     in clear text (auto-hides after 30 seconds).
```

### 6. Code Review Checklist

- [ ] Password defaults to masked (`show="*"`)
- [ ] `pwd_visible` and `_pwd_hide_timer` initialized in `__init__`
- [ ] `_pwd_hide_timer` typed as `int | None` (tkinter after() returns int)
- [ ] Toggle button uses `self.master.after()` not `self.root.after()`
- [ ] `_auto_hide_password` delegates to `_toggle_password_visibility` (DRY)
- [ ] Toggle button only shown for user password field (not recovery password or BEK)
- [ ] Toggle button uses `pack_forget()` when hidden (not just `state=DISABLED`)
- [ ] Auto-hide timer works (30 seconds, fixed countdown)
- [ ] Timer cancelled on manual hide or method change
- [ ] Timer cancelled on window close (if app teardown hook added in future)
- [ ] No logging of password visibility state changes
- [ ] Button text "Show"/"Hide" (not emoji)
- [ ] Layout does not break with new button (uses existing pack layout)
- [ ] `bek_button` pack order is stable (never forgotten, so always after toggle)
- [ ] Keyboard accessibility: relies on default `ttk.Button` behavior (Tab-focusable, Space/Return activates; untested by design)
- [ ] All automated tests pass with ≥80% coverage
- [ ] `test_gui.py` includes 8 new test cases covering toggle behavior

## Out of Scope

- Toggle for recovery password field (48-digit numeric, low typo risk)
- Toggle for BEK file path (file picker, not typed)
- Persistent toggle state across app restarts (always defaults to hidden)
- Custom icon assets (unicode emoji sufficient for v1)

## Security Considerations

- **Default state:** Masked (no change to current behavior)
- **Auto-hide:** 30-second fixed countdown from "Show" click (does NOT reset on interaction). Mitigates "walk away from screen" risk.
- **No logging:** Password visibility state is never logged
- **Elevation flow:** Password visibility is GUI-only; the elevated helper sees the password as a CLI argument regardless (existing documented risk)
- **Timer behavior:** Simple fixed countdown is more predictable and easier to test than activity-reset; user knows password will hide 30s after clicking Show

## Implementation Notes

### Timer type
`tkinter.Widget.after()` returns an `int` (timer ID) on CPython, not `str`. Type annotation corrected to `int | None`.

### Method change structure
Current `_on_method_change` is 2-way (BEK vs else). This plan adds a third branch for recovery password mode, making it 3-way (BEK / user password / recovery password).

### Layout stability
`bek_button` is forgotten and repacked only when switching TO user-password mode (to maintain correct visual order after the toggle button is re-inserted). In all other mode transitions, `bek_button` remains packed and only its `state` (NORMAL/DISABLED) changes. The toggle button is the only widget that uses `pack_forget()` in non-user-password modes.

### Teardown consideration
If the window is destroyed while the 30s timer is pending, `after()` fires on a dead widget. tkinter tolerates this (callback checks `pwd_visible` and may no-op). For single-root apps like this, no explicit cleanup is needed.

If future refactoring adds a teardown hook, cancel the timer:
```python
# In run_app() or wm_delete_window protocol handler:
if hasattr(app, '_pwd_hide_timer') and app._pwd_hide_timer is not None:
    root.after_cancel(app._pwd_hide_timer)
    app._pwd_hide_timer = None
```

### Accessibility
Keyboard accessibility (Tab-focusable, Space/Return activation) relies on default `ttk.Button` behavior. No explicit testing required unless the implementation switches to `Label` + event bindings.

## Rollout

1. Implement changes in `gui.py`
2. Manual GUI testing (checklist above)
3. Update `VERSION` to `0.5.0`
4. Update `CHANGELOG.md` and `README.md`
5. Merge to `main`, tag `v0.5.0`

## Decisions Made (Post-Review)

- **Recovery password toggle:** No. Recovery passwords are 48-digit numeric strings with format validation; typo risk is lower and UI clutter is not warranted.
- **BEK file path toggle:** No. BEK path is selected via file picker, not typed.
- **Timeout configurable:** No for v1. 30 seconds is a reasonable default. If users request configurability later, consider adding a preference.
- **Icon vs. text:** Text (`Show` / `Hide`) for better accessibility and cross-platform rendering.
- **Timer behavior:** Fixed 30-second countdown (not activity-reset) for simplicity and predictability.
- **Implementation approach:** Use existing `pack()` layout, hide toggle button with `pack_forget()` in non-user-password modes, delegate `_auto_hide_password` to `_toggle_password_visibility` to avoid state duplication.

## References

- Password field in `_build()`: `src/dislocker_ui/gui.py` lines 96-105 (`secret_frame`)
- `UnlockMethod` enum: `src/dislocker_ui/runner.py` lines 67-69
- tkinter `Entry` widget: `show` parameter controls masking character
- macOS HIG: password fields should mask by default, visibility toggle is acceptable
