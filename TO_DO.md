# TO_DO

Open work only for **dislocker-ui**.

## Rules (agents)

- Add new work here when it is actually queued.
- Link the matching plan under [`plans/`](plans/) when one exists.
- **When an item is finished, delete it from this file** — do not accumulate checked-off history here.
- When an entire plan is finished, move it to [`plans/archive/`](plans/archive/) and remove its section below.
- User-visible completions also need `VERSION` + [`CHANGELOG.md`](CHANGELOG.md); harness-only notes go in [`CHANGELOG.dev.md`](CHANGELOG.dev.md).
- See [`plans/README.md`](plans/README.md) for the full plans workflow.

## Next Up

- **Smoke-test the double-click launcher** (0.7.0, [plan](plans/double-clickable-app.md),
  needs a real BitLocker drive, ~5 min):
  1. Double-click `dislocker-ui.command`, enter the admin password in
     Terminal, and confirm the GUI opens.
  2. Mount the volume and confirm its files are readable.
  3. With the volume still mounted, close Terminal (accept the
     "terminate running processes?" warning). Confirm the GUI exits
     promptly with no hang or leftover process.
  4. Relaunch and confirm Mount self-heals (or Unmount clears) the stale
     session. Compare `diskutil list` before and after: no unrelated
     disk may be ejected.
  If step 3 misbehaves, add a wrapper `trap` or a SIGHUP handler in
  `__main__.py` (see the plan). When it passes, archive the plan.

## Active

- Distributable app ([plan](plans/distributable-app.md)): FUSE-T spike
  (macOS VM + throwaway BitLocker image; Docker can't run macOS), build
  script + bundled Python, setup panel, signing/notarization, release
  workflow.

- Stale-session fix (0.5.3, [archived plan](plans/archive/2026-10-03-stale-session-unmount.md)):
  manual verification only — mount a BitLocker volume, reboot, launch, and
  confirm Mount self-heals (or Unmount clears) with no eject of unrelated
  disks (`diskutil list` before/after).
- Privileged-helper hardening residual ([archived plan](plans/archive/2026-10-04-privileged-helper-hardening.md)):
  the elevation scan still follows `.py` symlinks (checking only target
  mode bits), and `os.walk(followlinks=False)` never descends symlinked
  directories under `src/` (they stay unscanned). Refusing symlinked
  modules/dirs is deferred: legitimate checkouts use links (editable
  installs, shared tooling), and refusing them on the everyday elevation
  path would strand those checkouts; `run.sh` already refuses both on
  the rare sudo-launch path.
- Docs/help menu (0.6.0, [archived plan](plans/archive/2026-10-04-docs-and-in-app-help.md)):
  manual verification only — on macOS confirm the Help-only menubar keeps
  Tk's default application menu and Cmd-Q, and that Help merges into
  the system Help menu.
