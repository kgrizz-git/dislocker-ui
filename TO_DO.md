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

- [Documentation and in-app help](plans/2026-10-01-docs-and-in-app-help.md):
  add README Troubleshooting section (stale-session recovery, missing
  tools, macFUSE/TCC, mbedtls symlink), and a minimal in-app Help menu
  (About / Usage / Troubleshooting) with text in a new `help_text.py`.
  Extend the minimal Troubleshooting section shipped in 0.5.3.
- Stale-session fix (0.5.3, [archived plan](plans/archive/2026-10-03-stale-session-unmount.md)):
  manual verification only — mount a BitLocker volume, reboot, launch, and
  confirm Mount self-heals (or Unmount clears) with no eject of unrelated
  disks (`diskutil list` before/after).
- [Privileged helper hardening](plans/2026-08-05-privileged-helper-hardening.md):
  remediate the security audit findings in the elevated mount/unmount workflow.
  - Follow-up (reviews 2026-09-15/16, deferred deliberately): the osascript
    preflight `elevate._assert_no_writable_py_files` is fail-open where
    `run.sh` is now fail-closed — it silently skips unreadable dirs (`os.walk`
    without `onerror`) and swallows per-file stat `OSError`s, and it follows
    `.py` symlinks checking only target mode bits. Scope if ever taken up:
    fail closed on scan *incompleteness* only (raise on walk/stat errors with
    an ownership-fix message), NOT full `run.sh` parity — outright symlink
    refusal belongs to the rare sudo-launch path; on the everyday elevation
    path it would strand legitimate checkouts (e.g. root-owned caches from a
    prior sudo run). No live hole either way: exploitation still needs
    directory-write (refused when visible) or same-user tampering (out of
    scope).
