# Documentation and In-App Help

**Created:** 2026-10-01
**Status:** Active
**Target Version:** 0.6.0 (user-visible in-app help) — or folded into the
0.5.3 stale-session release if convenient
**Branch:** drafted on `fix/stale-session-unmount`

## Assessment (2026-10-01)

**Strengths (keep as-is):**

- 100% docstring coverage: every module carries a structured docstring
  (purpose / inputs / outputs / requirements) and every function/class is
  documented. Verified via AST scan — zero undocumented defs across the
  package.
- `README.md` (286 lines) is thorough: staged install paths (Homebrew
  tap, root-deps installer), usage, read-vs-write matrix, safety notes,
  repo layout, developer checks, license boundaries.
- `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` + `CHANGELOG.dev.md`,
  `plans/` with its own `README.md` index, `TO_DO.md`, `AGENTS.md`.
- GUI error dialogs are already actionable in places (missing-tools
  dialog names the exact one-time install command).

**Weaknesses (this plan):**

1. **Zero in-app help.** `gui.py` has no menu bar, no Help menu, no About
   dialog, no usage text. A user facing the "session is already active"
   wedge (2026-10-01 incident) had no recovery path inside the app and
   none documented either.
2. **No Troubleshooting section in README.** Nothing covers the errors a
   real user hits: stale session recovery, where session state lives
   (`$HOME/Library/Application Support/dislocker-ui/` vs
   root-owned `/var/db/dislocker-ui/`), macFUSE approval flow, TCC
   restrictions, the mbedtls-symlink breakage after `brew upgrade`
   (mentioned only inside the install section), "NTFS needs ntfs-3g".
3. **In-app pointer without a target:** `_refresh_deps_label`
   (src/dislocker_ui/gui.py:182) tells the user "See README" for missing
   tools, but README has no section matching the failures users actually
   encounter.
4. Minor: no screenshots, no `docs/` folder. Single-file README is fine
   at this project's size; not a weakness to fix.

## Goal

- Every failure a user can hit in normal use has a documented recovery
  path — in the README and, for the most common ones, in the app itself.
- The app carries a minimal Help menu (About / Usage / Troubleshooting)
  without bloating `gui.py` or violating the "keep UI thin" convention.

## Design Decisions

### D1: README "Troubleshooting" section

Add a `## Troubleshooting` section between "Safety notes" and "Layout",
covering, in error-message-first order:

- **"A session is already active" / Unmount can never finish** — explain
  the pre-0.5.3 stale-session wedge in two sentences, point at Unmount
  first, then give the manual recovery for older releases:
  `sudo rm "$HOME/Library/Application Support/dislocker-ui/active_session.json"`
  (and the root-state variant under `/var/db/dislocker-ui/<uid>/` for
  elevated sessions). Note that 0.5.3+ self-heals (cross-reference
  `plans/archive/2026-10-03-stale-session-unmount.md`). Mention that leftover
  empty `/Volumes/DislockerUI*` dirs are harmless. 0.5.3+ removes only
  the directory recorded in the session on successful unmount. Other
  leftovers (e.g. `DislockerUI-2` from the 2026-10-01 incident) stay
  until removed by hand with `sudo rmdir`.
- **"Missing required tools"** — one-line pointer to the install section
  and `scripts/install-root-deps.sh`.
- **"NTFS volume requires ntfs-3g"** — FAT/ExFAT works without it.
- **macFUSE not approved / mount fails after reboot** — Recovery-mode
  kext approval pointer (condensed from install section).
- **dislocker stops loading after `brew upgrade`** — repeat the
  mbedtls symlink fix with a link to the install section.
- **TCC: unprivileged GUI cannot open the disk** — restate why
  `sudo ./run.sh` is the supported launch.

Style rules: error message in bold as the heading of each entry, ≤6 lines
per entry, no machine-specific home paths (use `$HOME`).

### D2: In-app Help menu

- Add a native `tk.Menu` menubar to the root window in `gui.py._build()`
  with a single **Help** menu. Create it as
  `tk.Menu(menubar, name="help")`: on macOS Tk only merges a cascade
  into the system Help menu when it carries that exact name. Optionally
  put About in `tk.Menu(menubar, name="apple")` (the application menu)
  on Darwin; elsewhere keep it under Help. Both forms work under Xvfb in
  CI.
  - **About dislocker-ui** — `messagebox.showinfo` with version
    (reuse `__version__`, already imported in gui.py), one-line purpose,
    GPL-3.0-or-later, and repo URL.
  - **Usage** — short modal dialog (read-only `tk.Text` in a `Toplevel`,
    or reuse the messagebox) summarizing the Usage steps from README
    (select volume → method → secret → Mount; Unmount before eject).
  - **Troubleshooting** — same dialog pattern with the condensed
    error→fix list from D1.
- **Keep gui.py thin:** put all help strings in a new module
  `src/dislocker_ui/help_text.py` (plain constants + a tiny
  `open_help_dialog` helper if needed). `gui.py` is 471 lines and the
  soft cap is 600; a constants module keeps the menu wiring to ~20 lines.
  This mirrors how mount logic lives outside the GUI module
  (AGENTS.md "Keep UI thin; put subprocess and path logic in non-GUI
  modules" — help text is the same separation).
- No tooltips: tkinter has no native support and hand-rolled bindings
  are not worth the complexity; the existing deps/rw hint labels already
  cover the main state explanation.

### D3: Consistency with the stale-session fix

Troubleshooting text must be written to be correct both before and after
`plans/archive/2026-10-03-stale-session-unmount.md` lands: "click Unmount (fixed
in 0.5.3 to clean up stale sessions automatically); on older releases
remove the session file manually".

Status 2026-10-03: the stale-session plan shipped in 0.5.3 and created a
minimal README Troubleshooting section with the stale-session entry.
This plan extends that section; it does not rewrite the entry.

Ownership is fixed, not "whichever lands second": **this plan's
Troubleshooting section owns** the stale-session recovery text (the
self-healing wording and the manual `sudo rm`). The stale-session plan
adds only a one-line pointer from "Safety notes" to Troubleshooting. If
the stale-session plan lands first, it creates the Troubleshooting entry
using the wording in D1 above, and this plan extends that section rather
than rewriting it.

### D4: Changelog placement

- In-app Help menu = user-visible feature → `CHANGELOG.md` under
  `### Added`, with `VERSION` bump.
- README troubleshooting section on its own would be docs-only →
  `CHANGELOG.dev.md`; if shipped in the same release as the help menu,
  one `CHANGELOG.md` entry covers both.

## Implementation Plan

- [ ] `src/dislocker_ui/help_text.py` — ABOUT_TEXT, USAGE_TEXT,
      TROUBLESHOOTING_TEXT constants (+ dialog helper if the Text-widget
      dialog earns >10 lines in gui.py).
- [ ] `gui.py` — menubar + Help menu wiring in `_build()`; three
      callbacks. No other behavioral changes.
- [ ] `README.md` — Troubleshooting section (D1).
- [ ] `VERSION` + `CHANGELOG.md` (and/or `CHANGELOG.dev.md` per D4).
- [ ] Tests in a **new** `tests/test_help_menu.py`. `tests/test_gui.py` is
      already 691 lines (soft cap 600, hard cap 750), so do not grow it.
      Reuse its Tk root fixture pattern (move it to `tests/conftest.py`
      if both files need it):
      - Help menu exists and all three commands open without error
        (Tk root fixture pattern already in the file).
      - `help_text` constants: non-empty, contain the manual-recovery
        command, contain no absolute `/Users/…` paths (mirrors the
        absolute-path policy) and no secrets.
      - The Help cascade is named `help` (macOS system-menu merge).
      - (Moved) The stale-session status-label test belongs to the
        stale-session plan (its D7). The post-operation refresh already
        exists at gui.py:389 and gui.py:434.
- [ ] Gates: `python3 -m compileall -q src`, ruff check + format,
      `pytest --cov` (≥80%), `python3 hooks/check_file_size.py`,
      `python3 hooks/check_absolute_paths.py`.

## Out of Scope

- Screenshots / hosted website / `docs/` tree.
- CLI `--help` (the app is GUI-only by design).
- Tooltips.
- Any change to error dialogs raised by mount/unmount flows (those
  improve separately via the stale-session plan).

## Security Considerations

- Help text contains no secrets and no machine-specific paths.
- The documented manual-recovery `sudo rm` targets a fixed,
  user-known filename under Application Support — same trust posture as
  the app's own `clear_session`.
- No new subprocess, network, or filesystem behavior is introduced by
  the help menu.

## References

- GUI structure: `gui.py:82-162` (`_build`), `gui.py:182` ("See README"
  pointer), log pane at `gui.py:156-162`.
- Absolute-path policy: `AGENTS.md` (no literal home paths),
  `hooks/check_absolute_paths.py`.
- Cross-referenced plan: `plans/archive/2026-10-03-stale-session-unmount.md`
  (this plan's Troubleshooting section owns the self-healing wording; that
  plan adds a Safety-notes pointer only — see D3).
- Plan review 2026-10-03: verified gui.py has no menu (471 lines),
  README has no Troubleshooting section (286 lines), `__version__` is
  imported at gui.py:25. Folded in: macOS menu naming, leftover-dir
  wording, README ownership, status-label test moved.
