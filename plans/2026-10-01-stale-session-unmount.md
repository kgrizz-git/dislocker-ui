# Stale Session Unmount Fixes

**Created:** 2026-10-01
**Status:** Active
**Target Version:** 0.5.3 (bugfix)
**Branch:** `fix/stale-session-unmount`

## Background

On 2026-10-01 the app wedged permanently after normal usage: a session
recorded on 2026-08-06 outlived its mounts (reboot cleared `/tmp`, the raw
disk attach, and the `/Volumes` mount), then:

1. **Mount** refused with "A session is already active" — the pre-check
   (`mount_volume`, src/dislocker_ui/runner.py:107-119) loads any parseable
   session and hard-blocks, with no liveness check.
2. **Unmount** could never succeed: `unmount_volume`
   (src/dislocker_ui/runner.py:283-297) only clears the session when the
   error list is empty, but treats "not currently mounted" as an error even
   though it is the desired end state. Every retry fails identically, so
   the session file is retained forever ("state was retained for retry").
3. During the failed unmount, `_detach_raw_disk`
   (src/dislocker_ui/runner.py:423-430) ran `hdiutil detach /dev/disk5`
   against the stale session's device number and **ejected whatever
   currently occupied that slot** — very likely the user's just-plugged
   physical drive (device numbers are reassigned after reboot). The drive
   re-enumerated as `/dev/disk6`; `/dev/disk5` no longer existed.

4. The incident left `/Volumes/DislockerUI` and `/Volumes/DislockerUI-2`
   as empty root-owned dirs (verified 2026-10-02):
   `_remove_empty_ntfs_dir` never ran because unmount errored first —
   consistent with the wedge mechanics above.

The only escape today is manual `sudo rm` of the root-owned 0600 session
file in Application Support. Existing tests mock the individual unmount
steps (tests/test_runner_elevation.py:169-171), so this scenario was never
covered.

## Goal

Make unmount idempotent and safe, and stop stale sessions from blocking
fresh mounts:

- Unmount succeeds (and clears the session) when targets are already gone.
- Never detach a device that no longer belongs to the recorded session.
- Mount detects a fully-stale session and cleans it up instead of blocking.

## Design Decisions

### D1: Idempotent unmount steps (fix the wedge)

`_unmount_ntfs` and `_unmount_fuse` check liveness **before** invoking
umount:

- `ntfs_mount`: if `Path(session.ntfs_mount)` is missing or
  `os.path.ismount()` is False → log "already unmounted" and return `[]`.
- `fuse_mount`: same check for `Path(session.fuse_mount)`.

Rationale: an unmounted/missing target *is* the success state; only a
mount that exists but fails to unmount is an error. This makes
`clear_session` reachable for stale sessions while preserving the
retain-for-retry behavior for genuinely stuck mounts (busy volume, etc.).

### D2: Verified detach (fix the data-safety hole)

`_detach_raw_disk` must not trust the bare device number. Before detaching:

1. Run `hdiutil info -plist` and build the set of (device → image-path)
   for attached disk images.
2. Detach `session.raw_disk` only when that device is listed as a disk
   image whose `image-path` matches `session.dislocker_file`.
3. Otherwise log "raw disk no longer attached or reassigned; skipping
   detach" and return `[]` (not an error — the goal state is "not
   attached").

Path comparison must normalize two things before matching:
URL-encoding (`hdiutil info -plist` emits `image-path` percent-encoded,
e.g. `Kiro%20CLI.dmg` — verified 2026-10-02 via plistlib, and
`Path.resolve()` does NOT decode `%20`, so apply
`urllib.parse.unquote()` first) and the macOS `/tmp` → `/private/tmp`
style prefix difference (`Path.resolve()` on both sides, after
unquoting). If the device is absent from the image list (detached, or now
a *physical* disk), skip — this is exactly the guard that prevents
ejecting a reassigned device like the 2026-10-01 incident.

Fallback (`detach -force`) only runs after identity verification passes.
Both attempts must clear the same identity gate.

### D2b: Same gate in `_best_effort_cleanup` (mount-failure path)

`_best_effort_cleanup` (runner.py:613-620) runs
`hdiutil detach -force <raw_disk>` with no identity check — the same
reassigned-device hole as the unmount path, reachable when a mount fails
after attach. Reuse the `_attached_disk_images` helper there: skip the
detach when the device is absent from the image list or its image-path
mismatches. (Force-detaching a verified-own image stays fine.)

### D3: Staleness detection at mount time

Add a helper (e.g. `_session_targets_gone(session)`) returning True when
**all** recorded targets are dead: `ntfs_mount` not a mountpoint, fuse
mount dir missing/not mounted, raw disk not in the `hdiutil info` image
list (via the D2 helper).

- `mount_volume` pre-check and `_validate_mount_request`: when the loaded
  session is stale, log a notice, clear the session, and proceed with the
  mount instead of raising. (`_validate_mount_request` is not dead code:
  under `sudo ./run.sh` the euid is 0 so `needs_elevation()` is False and
  the `mount_volume` pre-check at 107-119 is skipped — `_validate_mount_request`
  is then the *only* gate on that path. Both wirings are required.)
- **Elevated sessions:** the unprivileged GUI can read but not delete the
  root-owned session file. When `needs_elevation()` and the session is
  stale, the stale-clearing must happen inside the privileged child (which
  re-validates staleness itself before deleting — never trust the
  unprivileged staleness verdict). If that plumbing is invasive, fall back
  to raising the current error with an actionable message ("session is
  stale; click Unmount once to clean it up") — because with D1+D2 the
  Unmount button now actually clears stale sessions.

### D4: Keep runner.py within size/complexity gates

`runner.py` is already past the 600-line soft cap (638 lines) and
`unmount_volume` will grow. Extract the new liveness/identity logic into
small module-level helpers; if the file approaches the 750 hard cap,
move unmount steps (`_unmount_*`, `_detach_*`) plus the new helpers into a
new `unmount_steps.py` module (mirroring `ntfs_mount.py`/`fat_mount.py`)
rather than cramming. C901 max 12 applies.

### D5 (optional, separate commit): filter non-physical disks from the GUI list

`disks.py` lists every `diskutil` entry, including mounted DMGs (a
leftover "Kiro CLI" installer image showed up as a selectable candidate).
Use `diskutil info` / the plist's `VirtualOrPhysical` (or
`diskutil list -plist` disk-level keys) to drop virtual/synthesized disk
images from the dropdown. Low priority; keep out of scope if it risks the
freeze — tracked as a stretch item.

## Implementation Plan

### 1. `runner.py` — idempotent unmount

- [ ] Add `_is_mounted(path: str) -> bool` helper (existence + `os.path.ismount`).
- [ ] Guard `_unmount_ntfs` and `_unmount_fuse` with it (D1).
- [ ] `unmount_volume`: no signature change needed; with D1 the error list
      is empty for stale sessions and `clear_session` runs normally.

### 2. `runner.py` — verified detach

- [ ] Add `_attached_disk_images(hdiutil: str) -> dict[str, str]` parsing
      `hdiutil info -plist` into `{device: image-path}` (stdlib plistlib,
      matching `disks.py` style).
- [ ] Rewrite `_detach_raw_disk`: identity check (D2) before both the
      primary and force attempts; log-and-skip on mismatch/absence.
- [ ] Normalize paths before comparing (`urllib.parse.unquote()` then
      `Path.resolve()` both sides — resolve alone preserves `%20`).
- [ ] Apply the same identity gate to `_best_effort_cleanup` (D2b):
      skip the force-detach when the device is absent or mismatched.

### 3. `runner.py` — staleness detection

- [ ] Add `_session_targets_gone(session) -> bool` using the helpers above
      (D3).
- [ ] Wire into `mount_volume` pre-check and `_validate_mount_request`:
      stale → clear + proceed (non-elevated); elevated → see D3 decision.
- [ ] Keep the existing hard-block for live sessions.

### 4. Tests (new file `tests/test_unmount_stale.py`)

Exercise the **real** step functions (patch at the `subprocess`/`_run`
boundary, not by mocking the steps themselves):

- [ ] Stale session unmount: no target exists → `unmount_volume` returns
      cleanly, session file is deleted, no subprocess detach/umount of
      unrelated devices.
- [ ] Live mount unmount still retains state on failure (busy volume
      simulated via `_run` raising) — preserves retry semantics.
- [ ] Detach identity gate: `hdiutil info` shows the device as a physical
      disk / different image → detach skipped, no error.
- [ ] Detach identity gate: image-path matches → detach runs (primary,
      then force-fallback path).
- [ ] Path normalization: `/tmp/...` vs `/private/tmp/...` image-path
      match.
- [ ] URL-decoding: percent-encoded image-path (`Kiro%20CLI.dmg`) matches
      a session path with a literal space.
- [ ] `_best_effort_cleanup` identity gate: absent/mismatched raw_disk →
      no detach subprocess; matching image → force-detach runs.
- [ ] Mount with stale session proceeds and clears the session file.
- [ ] Mount with live session still blocks.
- [ ] Elevated stale-session branch (per D3 decision, whichever shape it
      takes): staleness re-validated inside the root child
      (`privileged.py` → `unmount_volume`); an unprivileged verdict alone
      never deletes root-owned state.
- [ ] Note: when `needs_elevation()` is true, `unmount_volume` ignores a
      passed `session_path` (runner.py:264-266) — stale tests must stage
      the canonical per-user path, not a tmp path.

### 5. Docs

- [ ] `VERSION` → `0.5.3`, `CHANGELOG.md` under `Fixed`:
      unmount is idempotent for stale sessions; raw-disk detach now
      verifies identity (never ejects a reassigned device); stale sessions
      no longer block mounting.
- [ ] README "Safety notes": brief note that a stale session (reboot while
      mounted) is now self-healing via Unmount/Mount, plus the manual
      recovery command (`sudo rm` of the session file) for older releases.
- [ ] `CHANGELOG.dev.md`: test-approach note (tests now exercise the real
      step functions at the subprocess boundary) if it warrants an entry.

### 6. Verification

- [ ] `python3 -m compileall -q src`
- [ ] `ruff check src tests hooks` + `ruff format --check src tests hooks`
- [ ] `pytest --cov --cov-report=term-missing` (≥80%, no C901)
- [ ] `python3 hooks/check_file_size.py` (runner.py stays <750)
- [ ] Manual: mount a BitLocker volume, reboot, launch, confirm Mount
      self-heals (or Unmount clears) without the wedge; confirm no eject
      of unrelated disks (verify via `diskutil list` before/after).

## Out of Scope

- Live unmount-failure diagnostics (why a busy volume won't unmount).
- Session identifiers / schema v2 (`MountSession` unchanged here; the
  privileged-helper plan's "random session identifier" idea remains with
  that plan).
- Any decrypt-to-file or trust-model changes.

## Security Considerations

- Detach verification (D2) closes a real data-loss path: a stale session
  could previously eject an arbitrary reassigned device as root.
- Stale-clearing on the elevated path must remain inside the root child
  with its own staleness validation — the unprivileged GUI's verdict is
  never trusted for privileged cleanup (consistent with SA-01 in the
  privileged-helper hardening plan).
- No secrets are logged; `hdiutil info` output contains only device/image
  paths.
- `_attached_disk_images` parses plist from a trusted absolute binary
  (`deps.hdiutil`), matching the existing `_run` trust model.

## References

- Wedge mechanics: `unmount_volume` runner.py:283-297;
  `_unmount_ntfs` runner.py:413-420; `_detach_raw_disk` runner.py:423-430;
  `_unmount_fuse` runner.py:433-440.
- Mount pre-check: runner.py:107-119, `_validate_mount_request` 356-361.
- Session file: `session.py` (root-owned 0600 when written by root;
  `clear_session` 189-193).
- Incident notes: `tmp/2026-10-01T22-40-08Z-kiro-cli-leftovers-and-shell-hooks.md`
  is unrelated to this fix but documents the same diagnostic session
  (scratch, untracked).
- plist parsing precedent: `disks.py:40-49`.
