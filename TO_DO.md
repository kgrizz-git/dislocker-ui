# TO_DO

Open work only for **dislocker-ui**.

## Rules (agents)

- Add new work here when it is actually queued.
- Link the matching plan under [`plans/`](plans/) when one exists.
- **When an item is finished, delete it from this file** — do not accumulate checked-off history here.
- When an entire plan is finished, move it to [`plans/archive/`](plans/archive/) and remove its section below.
- User-visible completions also need `VERSION` + [`CHANGELOG.md`](CHANGELOG.md); harness-only notes go in [`CHANGELOG.dev.md`](CHANGELOG.dev.md).
- See [`plans/README.md`](plans/README.md) for the full plans workflow.

## Active

- Stale-session fix (0.5.3, [archived plan](plans/archive/2026-10-03-stale-session-unmount.md)):
  manual verification only — mount a BitLocker volume, reboot, launch, and
  confirm Mount self-heals (or Unmount clears) with no eject of unrelated
  disks (`diskutil list` before/after).
- Privileged-helper hardening residual ([archived plan](plans/archive/2026-10-04-privileged-helper-hardening.md)):
  the elevation scan still follows `.py` symlinks (checking only target
  mode bits), and `os.walk(followlinks=False)` never descends symlinked
  directories under `src/` (they stay unscanned). Refusal is deferred:
  on the everyday elevation path it would strand legitimate checkouts
  (e.g. root-owned caches from a prior sudo run); `run.sh` already
  refuses both on the rare sudo-launch path.
- Docs/help menu (0.6.0, [archived plan](plans/archive/2026-10-04-docs-and-in-app-help.md)):
  manual verification only — on macOS confirm the Help-only menubar keeps
  Tk's default application menu and Cmd-Q, and that Help merges into
  the system Help menu.
