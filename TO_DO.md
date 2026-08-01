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

### macOS elevation (target 0.2.0)

Plan: [`plans/macos-elevation.md`](plans/macos-elevation.md)

- [ ] Create branch `feature/macos-elevation` from `main`
- [ ] Make `ntfs-3g` required on Darwin; replace kernel `mount -t ntfs` with ntfs-3g (`ro`/`rw` + `allow_other,local,uid,gid,volname`)
- [ ] Add `elevate.py` (Darwin `euid != 0`, two-layer quoting, osascript + 600s timeout, cancel `-128`)
- [ ] Add `privileged.py`; serialize `DepsStatus`; use `sys.executable` + `PYTHONPATH`; user-owned request `mkstemp` lifecycle
- [ ] Add `MountSession.elevated`; plumb `session_path` through all runner call sites including cleanup
- [ ] Elevated path: dislocker-fuse log-file redirect + `start_new_session`; wait errors from log tail
- [ ] Route mount/unmount through elevation when Darwin and not root; in-process fallback when already root
- [ ] Unit tests in `elevate` / `privileged`; Darwin no-auth osascript round-trip; secret-absence assertion
- [ ] Bump to 0.2.0; update CHANGELOG / README / AGENTS; macFUSE caveat; two-prompt note
