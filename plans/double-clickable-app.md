# Double-clickable launch for dislocker-ui

Status: **active** — findings complete; two independent model reviews on
2026-10-17 both returned approve-with-changes, and their findings are
folded in below; implementation not started

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

| Launch context (same probe, same root uid) | Removable USB nodes | Control nodes (same run) |
|---|---|---|
| 1. `.app` → osascript admin prompt → root probe | EPERM ×8 (silent; no consent dialog, mounted volume) | Mixed: mounted disk-image nodes and some internal slices OK; internal whole-disk nodes (`disk0`, `disk2`, `disk3`, slice `disk0s2`) also EPERM |
| 2. Same `.app` **with Full Disk Access granted to the .app** | EPERM ×8 (volume unmounted) | Same mixed pattern |
| 3. `sudo` in Terminal → root probe | **OK ×8** (volume unmounted) | Same mixed pattern |

Control notes: the internal whole-disk EPERM appears in all three root
runs *including* the successful Terminal run, so it is a separate
mechanism (live-system / sealed-volume protection), not the
removable-volume gate — it is not the TCC signal and does not affect the
conclusion. An unprivileged control run
(`tmp/tcc-test/probe-baseline-unprivileged.txt`, recorded **before** the
USB drive was attached, so it contains no USB data) shows user-owned
disk-image nodes opening fine while internal nodes fail on plain Unix
permission bits — useful for telling EPERM sources apart.

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
- The probe cannot distinguish responsible-process attribution from
  designated-requirement keying; either mechanism leaves the
  admin-prompted child holding no consent from the launcher, so the
  conclusion is robust to that distinction.

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
# ROOT comes from $0, never $PATH: Terminal runs .command files with a
# minimal environment, and sudo's env_reset strips exports anyway. Keep the
# .command a real file at the repo root — invoked through a symlink, $0 is
# the link and ROOT would point at the wrong tree.
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$ROOT/run.sh" ]; then
  echo "dislocker-ui: $ROOT/run.sh is missing or not executable" >&2
  exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
  exec "$ROOT/run.sh" "$@"   # already root
fi
exec sudo "$ROOT/run.sh" "$@"
```

Name: `dislocker-ui.command` at the repo root (next to `run.sh`).

## Checklist

- [ ] Add `dislocker-ui.command` (thin wrapper as above; the `-x` guard is
      the only pre-sudo logic — nothing in it runs privileged, `run.sh`
      re-validates everything).
- [ ] Commit the executable bit: `chmod +x dislocker-ui.command`
      **before** `git add` (git records the mode at add time); verify
      `git ls-files -s` shows `100755`.
- [ ] Tests: `tests/test_command_launcher_static.py`, modeled on
      `tests/test_run_script_static.py` — `bash -n` syntax gate; wrapper
      exits 1 with the "missing or not executable" message when `run.sh`
      is absent/non-executable; exec-bit mode check via `git ls-files -s`.
- [ ] Lint gates: passes `bash -n` **and** the `shellcheck` pre-commit
      hook (the hook has no `types` filter, so it runs on the `.command`;
      the sketch is verified clean in the bash dialect the hook infers
      from the shebang).
- [ ] README: document double-click launch in the run section — password
      prompt appears in Terminal; **keep the Terminal window open while the
      app runs** (closing it SIGHUPs the GUI); `sudo ./run.sh` stays as
      the documented power-user equivalent. Gatekeeper wording for zip
      downloads: "If you downloaded a zip, the first launch may be
      blocked by Gatekeeper. Right-click `dislocker-ui.command` →
      **Open** → confirm; later launches open normally. Git clones are
      not quarantined." Also list the file in the README **Layout**
      section.
- [ ] Help menu (`help_text.py`): replace `USAGE_TEXT` step 1 with a
      line naming both launch paths, e.g. "1. Launch by double-clicking
      `dislocker-ui.command` (Finder opens Terminal for the password
      prompt), or run `sudo ./run.sh` in Terminal."
- [ ] Ship as **0.7.0** (`### Added` entry): bump `VERSION`, add the
      `CHANGELOG.md` entry, note any harness-only changes (e.g. the new
      static test) in `CHANGELOG.dev.md`, and archive this plan per the
      plans workflow.
- [ ] Manual verification (needs a real BitLocker drive): double-click →
      password in Terminal → GUI opens → Mount completes and files are
      readable; closing Terminal while running exits the GUI **cleanly**
      (process gone promptly; no zombie, no hang). If it misbehaves, add
      a `trap` in the wrapper or a SIGHUP handler in `__main__.py` and
      re-verify.

## Non-goals

- Any `.app` bundle elevation via osascript — blocked by the TCC findings
  above; do not retry variants (Removable-Volumes grant, signed bundle,
  Platypus) — they share the severed-descent flaw. Only the Full Disk
  Access grant was tested empirically (run 2); other grant categories are
  untested, but any grant keys on the launching `.app`, which is never the
  responsible process for the admin-prompted child — the same block
  applies regardless of grant type.
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
  unless the window proves annoying in practice. If manual verification
  shows an unclean exit on close, a wrapper `trap` or a SIGHUP handler in
  `__main__.py` is the sanctioned fix.
- `.command` files run with a minimal environment and `sudo` resets the
  environment (`env_reset`); the wrapper must resolve `run.sh` relative
  to itself, never via `$PATH` or inherited exports. `run.sh` sets its
  own `PYTHONPATH`, so nothing is lost.
- The probe artifacts live in gitignored `tmp/tcc-test/`; the results
  tables and control notes above are the durable record.
