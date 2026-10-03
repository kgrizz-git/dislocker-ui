# Stale Session Unmount Fixes

**Created:** 2026-10-01
**Status:** Implemented 2026-10-03 in 0.5.3 (commits 48a4b72..061bf27); manual reboot verification still pending (tracked in TO_DO.md)
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

- `ntfs_mount`: if the path is not in the mount table → log "already
  unmounted" and return `[]`.
- `fuse_mount`: same check for `session.fuse_mount`.

Rationale: an unmounted/missing target *is* the success state; only a
mount that exists but fails to unmount is an error. This makes
`clear_session` reachable for stale sessions while preserving the
retain-for-retry behavior for genuinely stuck mounts (busy volume, etc.).

**Liveness source: the mount table, not `os.path.ismount()`.** CPython's
`ismount` returns False whenever `lstat` raises. A dead FUSE mount
(dislocker-fuse, or ntfs-3g — also FUSE) answers `lstat` with ENXIO/EIO,
so `ismount` would call it "already unmounted", skip umount, clear the
session, and orphan the mount. A wedged daemon can also block `lstat`
outright. `_is_mounted(path)` therefore parses `/sbin/mount` output
(`<dev> on <path> (<fstype>, …)`; our paths are app-generated, so
splitting on `" on "` / `" ("` is safe) and never stats the target. If
`/sbin/mount` itself fails, treat the target as possibly mounted and
attempt umount (fail toward the old behavior, not toward clearing).

### D1b: Validate session paths before any unmount step (blocker)

D1 removes the implicit guard that kept `_remove_fuse_dir`'s
`shutil.rmtree(session.fuse_mount)` and `_remove_empty_ntfs_dir`'s
`rmdir` unreachable on non-mount paths: today `umount <fuse_mount>`
fails on a path that is not mounted, so the error list is non-empty and
cleanup never runs (runner.py:287-293). With D1 all steps return `[]`
and cleanup runs as root on whatever path the session names.

Non-elevated sessions (the `sudo ./run.sh` path) are not validated today:
`elevated_session_error` runs only for `session.elevated`
(runner.py:279-282), and `load_session` does not check the file owner
(session.py:144-148). The file lives below the user-owned
`$HOME/Library/Application Support`, so same-user malware can plant
`{"fuse_mount": "/Library/…"}` and the root GUI deletes that tree on
Unmount.

Fix: add `_validate_session_paths(session, session_path)` and call it at
the top of `unmount_volume` for **every** session, before any step:

- Elevated: keep `elevated_session_error` (privileged staging child,
  `/Volumes` child, physical device selectors).
- Non-elevated: `fuse_mount` must be a direct, non-symlink
  `dislocker-ui-*` child of `tempfile.gettempdir()` (both sides resolved);
  `ntfs_mount.parent == VOLUMES_ROOT` with a safe name; `raw_disk` must
  match `/dev/diskN` or `/dev/diskNsM`.
- When running as root, reject a session file not owned by the euid
  (`os.lstat(path).st_uid != os.geteuid()`). Put this owner check in
  `load_session` behind an opt-in flag so the unprivileged GUI status read
  of the root-owned elevated file keeps working.

Invalid sessions raise `RunnerError` with the manual-recovery hint and
are never cleaned up automatically. Shared validation belongs in
`mount_policy.py` next to `elevated_session_error`.

### D2: Verified detach (fix the data-safety hole)

`_detach_raw_disk` must not trust the bare device number. Before detaching:

1. Run `hdiutil info -plist` and build the set of (device → image-path)
   for attached disk images. The map must include **every**
   `system-entities[].dev-entry`, not just the whole disk:
   `_hdiutil_attach` can return a `/dev/diskNsM` partition node
   (runner.py:563-565). Verified shape (2026-10-03, no images attached):
   top-level keys `framework, images, revision, vendor`; `images == []`.
2. Detach `session.raw_disk` only when that device is listed as a disk
   image whose `image-path` matches `session.dislocker_file`.
3. Otherwise log "raw disk no longer attached or reassigned; skipping
   detach" and return `[]` (not an error — the goal state is "not
   attached").

Path comparison must normalize two things before matching:

- URL-encoding. `hdiutil info -plist` emits `image-path` percent-encoded
  (e.g. `Kiro%20CLI.dmg`, observed 2026-10-02; not re-observable
  2026-10-03 with no images attached). `Path.resolve()` keeps `%20`
  (`/private/tmp/Kiro%20CLI.dmg`). Apply `urllib.parse.unquote()` to the
  **hdiutil side only** — unquoting the session side would mis-decode a
  literal `%`. Our own image paths never contain characters that need
  encoding, so this only matters for robustness.
- The macOS `/tmp` → `/private/tmp` (and `/var` → `/private/var`) prefix.
  Do **not** call `resolve()` on `session.dislocker_file` itself: that
  walks into the FUSE mount and can hang on a wedged daemon. Resolve
  `Path(session.fuse_mount).parent` and re-append the last two name parts
  (`<fuse dir>/dislocker-file`).

If the device is absent from the image list (detached, or now a
*physical* disk), skip — this is exactly the guard that prevents ejecting
a reassigned device like the 2026-10-01 incident. The window between
`hdiutil info` and `hdiutil detach` is a residual TOCTOU, but it is
microseconds and unavoidable when detaching by device node.

Fallback (`detach -force`) only runs after identity verification passes.
Both attempts must clear the same identity gate.

### D2b: Same gate in `_best_effort_cleanup` (mount-failure path)

`_best_effort_cleanup` (runner.py:604-625, detach at 615-620) runs
`hdiutil detach -force <raw_disk>` with no identity check. The risk here
is much lower than on the unmount path: `raw_disk` was parsed seconds
earlier from this process's own `hdiutil attach` output, so reassignment
is very unlikely. Gate it anyway for defense in depth, using the same
helper, with two adjustments:

- The function hardcodes `/usr/bin/hdiutil` and receives no `deps`; pass
  that same fixed path to `_attached_disk_images` (or thread `deps`
  through — either is fine, keep one source of truth).
- When the gate skips, log loudly ("raw disk <dev> not verified as our
  image; left attached — detach manually"). Otherwise our image stays
  attached after `clear_session` (line 624) with nothing tracking it.

### D3: Staleness detection at mount time

Add a helper (e.g. `_session_targets_gone(session)`) returning True when
**all** recorded targets are dead: `ntfs_mount` and `fuse_mount` absent
from the mount table (D1 `_is_mounted`), and raw disk not attached as
our image (D2 helper). A session that fails D1b validation is never
"stale" — it keeps blocking with the manual-recovery message.

Two call sites with different authority:

- **`_validate_mount_request` is authoritative.** It is the only gate on
  the `sudo ./run.sh` path (euid 0 → `needs_elevation()` False → the
  `mount_volume` pre-check at 107-119 is skipped). It is also the gate
  inside the root child for elevated mounts: privileged.py:89 calls
  `mount_volume(elevated=True)` → `_mount_in_process` →
  `_validate_mount_request(session_path=<root session path>)`
  (runner.py:156). So it always runs with root's view of the system.
  Stale → log a notice, `clear_session`, proceed. Live → raise as today.
- **The `mount_volume` pre-check is advisory only.** It runs *only* when
  `needs_elevation()` is True (runner.py:107), i.e. unprivileged. It must
  never call `clear_session`: the state dir is root-owned 0750, and
  session.py:192 would swallow the OSError silently. Its staleness view
  is also unreliable — the FUSE staging dir is root 0700
  (mount_policy.py:98-103), and visibility of root-attached images in an
  unprivileged `hdiutil info` is unverified. So: looks stale → do not
  raise, proceed to the admin prompt and let the child decide. Looks live
  → raise as today (saves a pointless admin prompt). A wrong "stale"
  guess costs one prompt that ends in the child's exit-3 "session is
  already active"; it can never delete state.

No new privileged plumbing is needed. The stale-unmount path is already
re-validated in the child too: privileged.py:110 calls `unmount_volume`
with euid 0, so D1/D1b/D2 run there with root's own checks.

### D4: Keep runner.py within size/complexity gates

`runner.py` is already past the 600-line soft cap (638 lines). The new
helpers add roughly 80 lines, which lands near the 750 hard cap. Do the
split **up front**, not conditionally: move the unmount steps
(`_unmount_*`, `_detach_*`, `_remove_*_dir`) plus the new liveness and
identity helpers into a new `unmount_steps.py` module (mirroring
`ntfs_mount.py`/`fat_mount.py`). Keep `unmount_volume` in `runner.py` as
the facade. Add the module to the AGENTS.md architecture table. C901
max 12 applies.

### D6: Stale `/Volumes` dir that is not empty

`_remove_empty_ntfs_dir` returns an error when the dir is not a mount and
not empty (runner.py:468-469). For a stale session that error retains
state and re-creates the wedge. Keep refusing to delete the contents,
but downgrade it to a logged warning when D1 has confirmed the path is
not in the mount table. The session then clears, and the user is told
which directory to inspect.

### D7: GUI status for a stale session

At startup `_refresh_session_status` (gui.py:283-290) shows "Active
session (RO): …" for a dead session, which is what the user saw during
the incident. Keep the GUI thin: expose `session_looks_stale(session)`
from the non-GUI layer (same advisory check as the D3 pre-check) and
append " — looks stale; click Unmount to clean up" to the status line.
No new behavior beyond the label.

### D5 (optional, separate commit): filter non-physical disks from the GUI list

`disks.py` lists every `diskutil` entry, including mounted DMGs (a
leftover "Kiro CLI" installer image showed up as a selectable candidate).
Use `diskutil info` / the plist's `VirtualOrPhysical` (or
`diskutil list -plist` disk-level keys) to drop virtual/synthesized disk
images from the dropdown. Low priority; keep out of scope if it risks the
freeze — tracked as a stretch item.

## Implementation Plan

### 0. Module split (D4)

- [x] Create `unmount_steps.py`; move `_unmount_ntfs`, `_detach_raw_disk`,
      `_unmount_fuse`, `_remove_fuse_dir`, `_remove_empty_ntfs_dir` there
      unchanged first (pure move, tests green), then add the new helpers.
- [x] Update the AGENTS.md architecture table.

### 1. Session validation + idempotent unmount (D1, D1b, D6)

- [x] Add `_validate_session_paths(session, session_path)` (shared policy
      in `mount_policy.py`); call it at the top of `unmount_volume` for
      every session, before any step (D1b).
- [x] Add an opt-in owner check to `load_session` (reject a file not owned
      by the euid when running as root); use it on root unmount/mount
      paths only.
- [x] Add `_is_mounted(path: str) -> bool` parsing `/sbin/mount` output;
      never `lstat` the target. `/sbin/mount` failure → "possibly
      mounted" (D1).
- [x] Guard `_unmount_ntfs` and `_unmount_fuse` with it (D1).
- [x] Downgrade "not empty" in `_remove_empty_ntfs_dir` to a warning when
      the path is confirmed not mounted (D6).
- [x] `unmount_volume`: no signature change needed; with D1 the error list
      is empty for stale sessions and `clear_session` runs normally.

### 2. Verified detach (D2, D2b)

- [x] Add `_attached_disk_images(hdiutil: str) -> dict[str, str]` parsing
      `hdiutil info -plist` into `{dev-entry: image-path}` for every
      system entity (stdlib plistlib, matching `disks.py` style).
- [x] Rewrite `_detach_raw_disk`: identity check (D2) before both the
      primary and force attempts; log-and-skip on mismatch/absence.
- [x] Normalize before comparing: `unquote()` the hdiutil side only;
      resolve `Path(session.fuse_mount).parent` and re-append
      `<fuse dir>/dislocker-file` (never resolve inside the FUSE mount).
- [x] Apply the same identity gate to `_best_effort_cleanup` (D2b) with the
      fixed `/usr/bin/hdiutil` path; log loudly when it skips.

### 3. Staleness detection (D3, D7)

- [x] Add `_session_targets_gone(session) -> bool` using the helpers above
      (D3). Sessions failing D1b are never stale.
- [x] `_validate_mount_request` (authoritative): stale → log, clear,
      proceed; live → raise as today.
- [x] `mount_volume` pre-check (advisory, unprivileged): looks stale → do
      not raise and do not clear; proceed to elevation. Looks live → raise
      as today.
- [x] GUI status label appends a "looks stale" hint (D7), via a non-GUI
      helper.

### 4. Tests (new file `tests/test_unmount_stale.py`)

Exercise the **real** step functions (patch at the `subprocess`/`_run`
boundary, not by mocking the steps themselves):

- [x] Stale session unmount: no target exists → `unmount_volume` returns
      cleanly, session file is deleted, no subprocess detach/umount of
      unrelated devices.
- [x] Live mount unmount still retains state on failure (busy volume
      simulated via failing umount/detach subprocesses) — preserves retry
      semantics.
- [x] Detach identity gate: `hdiutil info` shows the device as a physical
      disk / different image → detach skipped, no error.
- [x] Detach identity gate: image-path matches → detach runs (primary,
      then force-fallback path).
- [x] Path normalization: `/tmp/...` vs `/private/tmp/...` image-path
      match.
- [x] URL-decoding: percent-encoded image-path (`Kiro%20CLI.dmg`) matches
      a session path with a literal space; a literal `%` in the session
      path is not decoded.
- [x] Partition node: session `raw_disk` `/dev/diskNsM` is found in the map.
- [x] `_best_effort_cleanup` identity gate: absent/mismatched raw_disk →
      no detach subprocess and a warning is logged; matching image →
      force-detach runs.
- [x] Crafted non-elevated session (D1b): `fuse_mount` outside
      `tempfile.gettempdir()`, `ntfs_mount` outside `/Volumes`, or a bad
      `raw_disk` → `RunnerError`, no `rmtree`/`rmdir`/umount/detach, session
      retained.
- [x] Session file not owned by the euid (root path) → rejected.
- [x] Dead FUSE mount (D1): path listed in `/sbin/mount` output while
      `os.lstat` raises ENXIO → umount is still attempted, not skipped.
- [x] `/sbin/mount` failure → targets treated as possibly mounted.
- [x] Stale `/Volumes` dir not empty and not mounted → warning, session
      cleared (D6).
- [x] Mount with stale session proceeds and clears the session file
      (`_validate_mount_request`, sudo path, `needs_elevation()` False).
- [x] Mount with live session still blocks.
- [x] Elevated pre-check (`needs_elevation()` True) with a stale-looking
      session: no `clear_session` call, no raise, elevation proceeds.
      Live-looking session still raises before the prompt.
- [x] Root child, mount: `_validate_mount_request` with the root
      `session_path` clears a stale session and proceeds.
- [x] Root child, unmount: drive `privileged.main()` → **real**
      `unmount_volume` (patch `needs_elevation` False and `subprocess.run`),
      not a mocked `unmount_volume` like
      `test_main_unmount_uses_canonical_session`.
- [x] GUI startup with a stale session shows the "looks stale" hint (D7).
      Put it in a new test file, not `tests/test_gui.py` (691 lines,
      near the 750 hard cap).
- [x] Note: when `needs_elevation()` is true, `unmount_volume` loads
      `session_path or active_session_path_for_user()` (runner.py:264-266),
      but `run_elevated_unmount` passes no path, so the **root child**
      always uses its canonical `root_session_path(uid)`. Elevated-path
      tests must stage that canonical path, not a tmp path.

### 5. Docs

- [x] `VERSION` → `0.5.3`, `CHANGELOG.md` under `Fixed`:
      unmount is idempotent for stale sessions; raw-disk detach now
      verifies identity (never ejects a reassigned device); stale sessions
      no longer block mounting.
- [x] README: this plan does **not** own the stale-session recovery text.
      The Troubleshooting section from
      `plans/2026-10-01-docs-and-in-app-help.md` owns the self-healing
      wording and the manual `sudo rm` command. This plan adds only a
      one-line pointer in "Safety notes" ("If Mount says a session is
      already active after a reboot, see Troubleshooting"). If this plan
      lands first, add that Troubleshooting entry here using the docs
      plan's wording.
- [x] AGENTS.md architecture table: add `unmount_steps.py`.
- [x] `CHANGELOG.dev.md`: test-approach note (tests now exercise the real
      step functions at the subprocess boundary) if it warrants an entry.

### 6. Verification

- [x] `python3 -m compileall -q src`
- [x] `ruff check src tests hooks` + `ruff format --check src tests hooks`
- [x] `pytest --cov --cov-report=term-missing` (≥80%, no C901)
- [x] `python3 hooks/check_file_size.py` (runner.py stays <750)
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
- D1 alone would open a new root `rmtree`/`rmdir` path on unvalidated
  session paths (sudo path, user-writable session location). D1b
  validation is a hard prerequisite for D1 and must land in the same
  commit.
- Stale-clearing on the elevated path stays inside the root child
  (`_validate_mount_request` / `unmount_volume` running as root) with its
  own staleness checks. The unprivileged pre-check is advisory: it can
  only skip a raise, never delete state (consistent with SA-01 in the
  privileged-helper hardening plan).
- Liveness never `lstat`s FUSE targets, so a wedged daemon cannot hang
  the GUI or root child during the check.
- No secrets are logged; `hdiutil info` output contains only device/image
  paths.
- `_attached_disk_images` parses plist from a trusted absolute binary
  (`deps.hdiutil`), matching the existing `_run` trust model.

## References

- Wedge mechanics: `unmount_volume` runner.py:283-297;
  `_unmount_ntfs` runner.py:413-420; `_detach_raw_disk` runner.py:423-430;
  `_unmount_fuse` runner.py:433-440.
- Mount pre-check: runner.py:107-119, `_validate_mount_request` 356-361.
- Cleanup reached only when steps report no errors: runner.py:287-293;
  `_remove_fuse_dir` 443-457; `_remove_empty_ntfs_dir` 460-472.
- Elevated-only path validation: runner.py:279-282 →
  `mount_policy.elevated_session_error` (mount_policy.py:84-93).
- Root child entry points: privileged.py:89 (mount), 110 (unmount).
- Plan review 2026-10-03: findings folded into D1, D1b, D2, D2b, D3, D4,
  D6, D7, and §4.
- Session file: `session.py` (root-owned 0600 when written by root;
  `clear_session` 189-193).
- Incident notes: `tmp/2026-10-01T22-40-08Z-kiro-cli-leftovers-and-shell-hooks.md`
  is unrelated to this fix but documents the same diagnostic session
  (scratch, untracked).
- plist parsing precedent: `disks.py:40-49`.
