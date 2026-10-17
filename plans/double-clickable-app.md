# Double-clickable launch for dislocker-ui

Status: **active** (findings complete; implementation not started)

Goal: a double-clickable way to launch the GUI on macOS, despite the sudo
requirement, without weakening the existing root-launch security model.

## Findings — 2026-10-17 TCC experiments

Question: can a double-clickable `.app` that elevates via
`osascript`'s `do shell script … with administrator privileges` produce a
root process able to open removable `/dev/disk*` nodes (the operation
BitLocker mounts require)?

Method: a read-only probe (`tmp/tcc-test/probe.py`, gitignored scratch)
that, as root, only `open()`/`close()`s device nodes and records errno.
Test media: one physical USB drive (FAT-32, tested both mounted and
unmounted), plus in-run controls (internal slices, user-owned disk-image
nodes). Three launches:

| Launch context (same probe, same root uid) | Removable USB nodes | Control nodes |
|---|---|---|
| 1. `.app` → osascript admin prompt → root probe | EPERM ×8 (silent; no consent dialog, mounted volume) | OK (internal slices, disk images) |
| 2. Same `.app` **with Full Disk Access granted to the .app** | EPERM ×8 (volume unmounted) | OK |
| 3. `sudo` in Terminal → root probe | **OK ×8** (volume unmounted) | OK |

Interpretation:

- TCC gates removable-volume device opens even for root; the gate keys on
  the *responsible process* (launch context), not uid.
- `do shell script … with administrator privileges` severs process
  descent: the privileged child is reparented to a system authorization
  daemon, so the launching `.app` is never in its tree. No privacy grant
  on the `.app` can apply — run 2 proves the strongest grant (Full Disk
  Access) does nothing.
- `sudo` in Terminal keeps the root process a descendant of Terminal,
  which holds removable-volume consent — run 3 — matching why
  `sudo ./run.sh` works today.

Conclusion: every "`.app` + osascript admin prompt" wrapper design is
blocked by TCC and cannot be rescued by grants. The only double-clickable
designs that work are (a) a Terminal-attached launcher, or (b) a signed
privileged LaunchDaemon (SMAppService) — out of scope per `run.sh`.

## Decision

Ship a **`.command` launcher** (Terminal-attached). Finder double-click →
Terminal opens → user types the admin password → `sudo` runs the existing
`run.sh` → GUI is root and mounts in-process, exactly like today's
documented flow. All root-side hardening stays in `run.sh`; the
`.command` is a thin unprivileged wrapper.

Design sketch (final shape at implementation time):

```bash
#!/usr/bin/env bash
# Double-clickable launcher: Finder opens this in Terminal; sudo prompts there.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ "$(id -u)" -eq 0 ]; then
  exec "$ROOT/run.sh" "$@"   # already root
fi
exec sudo "$ROOT/run.sh" "$@"
```

Name: `dislocker-ui.command` at the repo root (next to `run.sh`).

## Checklist

- [ ] Add `dislocker-ui.command` (thin wrapper as above; no logic before
      `sudo` — nothing in it runs privileged, `run.sh` re-validates everything).
- [ ] Commit the executable bit (`git ls-files -s` shows mode `100755`).
- [ ] Tests (one-off script under `tests/`): `bash -n` syntax gate; refuses
      sanely when `run.sh` is missing/not executable.
- [ ] README: document double-click launch in the run section — password
      prompt appears in Terminal; **keep the Terminal window open while the
      app runs** (closing it SIGHUPs the GUI); `sudo ./run.sh` stays as the
      documented power-user equivalent; note that zip-downloaded copies may
      hit Gatekeeper quarantine (right-click → Open once) while git clones
      do not.
- [ ] Help menu (`help_text.py` Usage text): mention the double-click
      launcher alongside `sudo ./run.sh`.
- [ ] Ship as **0.7.0**: bump `VERSION`, add `CHANGELOG.md` entry
      (user-visible feature), update `plans/README.md` active list.
- [ ] Manual verification (needs a real BitLocker drive): double-click →
      password in Terminal → GUI opens → Mount completes and files are
      readable; closing Terminal while running quits the app (documented
      behavior, verify it's clean, not a hang).

## Non-goals

- Any `.app` bundle elevation via osascript — blocked by the TCC findings
  above; do not retry variants (Removable-Volumes grant, signed bundle,
  Platypus) — they share the severed-descent flaw.
- Privileged LaunchDaemon / SMAppService / SMJobBless — signing + XPC
  refactor of `elevate.py`/`privileged.py`; `run.sh` declares root-owned
  install packaging out of scope for this personal helper.
- py2app/PyInstaller bundling (self-contained app) — fragile with Tcl/Tk,
  new build dependency, and the elevation problem is orthogonal.

## Risks / notes

- Terminal window must stay open while the GUI runs; that is inherent to
  the design (the consent that makes mounts work rides on Terminal).
  Document, don't fight. Possible later polish: backgrounding after auth
  (`sudo -b`) trades away stdout visibility and clean shutdown — deferred
  unless the window proves annoying in practice.
- `.command` files run with a minimal environment; the wrapper must
  resolve `run.sh` relative to itself, never via `$PATH`.
- The probe artifacts live in gitignored `tmp/tcc-test/`; the results
  tables above are the durable record.
