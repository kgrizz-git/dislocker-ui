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
Branch: `feature/macos-elevation` (exists)

- [ ] NTFS: require ntfs-3g; drop kernel `mount`; drop `deps.mount`; umask=077 + uid/gid/allow_other/local
- [ ] Add `elevate.py` (API, quoting, osascript protocol, cancel/timeout errors)
- [ ] Add `privileged.py` (request schema, validation, exit codes, `-s -P`, cwd `/`)
- [ ] Runner facade dispatch + session_path plumbing + BEK canonicalize + FUSE log/session detach
- [ ] GUI deps-hint reword + cancel/timeout dialogs
- [ ] Hardening checklist from plan (volume validate, fstat/O_NOFOLLOW, preflight perms, post-mount uid assert, audit line)
- [ ] Tests: elevate, privileged, deps, gui, focused runner elevation tests
- [ ] Docs/version 0.2.0 (CHANGELOG gap 0.1.6 + 0.2.0; README/AGENTS; macFUSE caveat; argv residual note)
