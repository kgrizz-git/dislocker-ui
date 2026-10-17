# Double-clickable launch for dislocker-ui

Status: **active** — implementation shipped as 0.7.0 on branch
`double-clickable-app` (launcher + docs + release notes); manual
verification (BitLocker drive) still open. Three independent model
reviews on 2026-10-17 (two free-tier, plus Claude Opus 5.5 via the
claude CLI) plus per-chunk reviews returned approve-with-changes and
their findings are folded in below.

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

Control notes:

- The internal whole-disk EPERM (`disk0`, `disk0s2`, `disk2`, `disk3`)
  appears in all three root runs *including* the successful Terminal run,
  so it is a separate mechanism (live-system / sealed-volume protection),
  not the removable-volume gate. The unprivileged control run
  (`tmp/tcc-test/probe-baseline-unprivileged.txt`, recorded **before**
  the USB drive was attached, so it contains no USB data) shows the same
  nodes failing EPERM on writes even without root (reads fail EACCES on
  plain Unix perms) — matching the root behavior and strengthening the
  separate-mechanism reading.
- diskutil tags the Kiro disk images `removable-media=True`, yet their
  nodes open fine as root even in the `.app`-spawned run — the TCC gate
  in play applies to physical external media, not to every node diskutil
  labels removable.
- Run 1 was recorded with the USB volume mounted; runs 2 and 3 with it
  unmounted (mount state is known from the operator log, not captured in
  the probe files). Runs 2 vs 3 are therefore the clean same-state pair;
  run 1 is corroborating.

Interpretation (mechanism is hypothesis; the differential outcome is the
evidence):

- Measured: root-from-`.app`-via-osascript cannot open the physical USB
  nodes — even with Full Disk Access granted to the `.app` (run 2) —
  while root-from-Terminal opens all eight (run 3). The gate keys on
  launch context, not uid.
- Hypothesis (consistent background knowledge, not directly probed): the
  osascript admin prompt severs descent — the privileged child is
  reparented to a system authorization daemon, so the launching `.app`
  is never the responsible process and no grant on it can apply — while
  Terminal-launched `sudo` keeps descent through consent-holding
  Terminal. The probe recorded no ppid/process-tree or TCC-consent data,
  and cannot distinguish responsible-process attribution from
  designated-requirement keying — but every candidate mechanism leaves
  the admin-prompted child without the launcher's consent, so the
  conclusion is robust to the distinction.

Conclusion: every "`.app` + osascript admin prompt" wrapper design is
blocked by TCC and cannot be rescued by grants. The double-clickable
designs that work are (a) a Terminal-attached launcher, or (b) a signed
privileged LaunchDaemon (SMAppService) — out of scope per `run.sh`.
Later polish, not blocked: a plain `.app` whose executable just runs
`open -a Terminal <repo>/dislocker-ui.command` stays Terminal-attached
(LaunchServices `open` — no osascript elevation; Terminal remains the
responsible app) and would add a Dock/Launchpad icon. Optional follow-up
after the `.command` ships.

## Decision

Ship a **`.command` launcher** (Terminal-attached). Finder double-click →
Terminal opens → user types the admin password → `sudo` runs the existing
`run.sh` → GUI is root and mounts in-process, exactly like today's
documented flow. All root-side hardening stays in `run.sh`; the
`.command` is a thin unprivileged wrapper.

Design sketch (shipped form; `readlink` resolution and the `-f` guard
were added in review — a symlink named like the launcher plus a planted
executable `run.sh` must never reach sudo):

```bash
#!/usr/bin/env bash
set -euo pipefail
SELF="$0"
# readlink -f is macOS 12.3+; older systems fall back to $0 itself.
if _resolved="$(readlink -f "$SELF" 2>/dev/null)"; then
  SELF="$_resolved"
fi
ROOT="$(cd "$(dirname "$SELF")" && pwd)" || {
  echo "dislocker-ui: cannot resolve $(dirname "$SELF")" >&2
  exit 1
}
if [ ! -f "$ROOT/run.sh" ] || [ ! -x "$ROOT/run.sh" ]; then
  echo "dislocker-ui: $ROOT/run.sh is missing or not executable" >&2
  exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
  exec "$ROOT/run.sh" "$@"   # already root (relaunched via sudo)
fi
exec /usr/bin/sudo "$ROOT/run.sh" "$@"
```

Name: `dislocker-ui.command` at the repo root (next to `run.sh`).

## Checklist

- [x] Add `dislocker-ui.command` (thin wrapper; the symlink resolution,
      ROOT-failure diagnostic, and `-x`/`-f` guard are the only pre-sudo
      logic — nothing in it runs privileged, `run.sh` re-validates
      everything).
- [x] Commit the executable bit: `chmod +x dislocker-ui.command`
      **before** `git add` (git records the mode at add time); verify
      `git ls-files -s` shows `100755`.
- [x] Tests: `tests/test_command_launcher_static.py`, modeled on
      `tests/test_run_script_static.py` — `bash -n` syntax gate; direct
      `shellcheck` call (CI never runs shellcheck); wrapper exits 1 with
      the "missing or not executable" message when `run.sh` is
      absent/non-executable; exec-bit mode check via `git ls-files -s`,
      skipped when `.git` is absent. Guard tests must run the wrapper
      only from a temp dir where `run.sh` is absent or a non-executable
      0644 stub — never where a real executable `run.sh` exists: CI
      runners have passwordless sudo and would really exec the root
      launcher.
- [x] Lint gates: passes `bash -n` and `shellcheck`. The pre-commit
      shellcheck-py hook filters on the `shell` tag — a `.command`
      extension is not auto-tagged, but an executable file with a bash
      shebang is — so the exec bit and shebang are what make the hook
      see the file; the static test's direct shellcheck call is the
      belt-and-braces gate. Sketch verified clean in bash dialect.
- [x] README: document double-click launch in the run section — password
      prompt appears in Terminal; **keep the Terminal window open while
      the app runs** (closing it SIGHUPs the GUI); `sudo ./run.sh` stays
      as the documented power-user equivalent; list the file in the
      README **Layout** section; extend install step 5 to
      `chmod +x run.sh dislocker-ui.command` (Finder will not run a
      non-executable `.command`; zip copies may lose the bit).
      Gatekeeper wording for zip downloads (right-click → Open was
      removed in macOS 15): "If you downloaded a zip, the first launch
      may be blocked by Gatekeeper. Use System Settings → Privacy &
      Security → **Open Anyway**, or run
      `xattr -d com.apple.quarantine dislocker-ui.command`. Git clones
      are not quarantined." Troubleshooting line: "If mounts fail with
      EPERM under sudo, allow Terminal under Privacy & Security →
      Removable Volumes (or Full Disk Access) — the sudo path rides on
      Terminal's own removable-volume consent."
- [x] Help menu (`help_text.py`): extend `USAGE_TEXT` step 1 to name
      both launch paths, observing the test constraints — lines wrap at
      72 columns (only `sudo `-prefixed lines are exempt), the literal
      fragment `sudo ./run.sh` must survive
      (`test_usage_text_covers_mount_flow`), and no backticks (Tk
      renders them literally), e.g.:
      "1. Launch by double-clicking dislocker-ui.command"
      "   (Finder opens Terminal for the password prompt),"
      "   or run sudo ./run.sh in Terminal."
      Add a test asserting "dislocker-ui.command" appears in
      `USAGE_TEXT`. Also mention the `.command` in the AGENTS.md
      "Commands" launch line.
- [x] Ship as **0.7.0** (`### Added` entry): bump `VERSION`, add the
      `CHANGELOG.md` entry, note any harness-only changes (e.g. the new
      static test) in `CHANGELOG.dev.md` (archive step split out below).
- [ ] Manual verification (needs a real BitLocker drive): double-click →
      password in Terminal → GUI opens → Mount completes and files are
      readable. Then close Terminal **with a volume mounted** (the
      realistic user action; expect Terminal's "terminate running
      processes?" warning): the GUI must exit **cleanly** (process gone
      promptly; no zombie, no hang), and the *next* launch must recover
      through the stale-session self-heal path with no eject of
      unrelated disks. If close misbehaves, add a `trap` in the wrapper
      or a SIGHUP handler in `__main__.py` and re-verify.
- [ ] Archive this plan per the plans workflow — move it to
      `plans/archive/` (date-prefixed) and remove its entry from
      `plans/README.md` "Active plans" and from `TO_DO.md` — once the
      manual verification above passes.

## Non-goals

- Any `.app` bundle elevation via osascript — blocked by the measured
  TCC findings above; do not retry variants (Removable-Volumes grant,
  signed bundle, Platypus) — under the hypothesized mechanism they share
  the severed-descent flaw. Only the Full Disk Access grant was tested
  empirically (run 2); other grant categories are untested, but under the
  same hypothesis any grant keys on the launching `.app`, which is never
  the responsible process for the admin-prompted child — the same block
  is expected regardless of grant type.
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
  `__main__.py` is the sanctioned fix. Caveat: Terminal's own
  removable-volume consent was never audited on this machine (TCC.db is
  unreadable); a fresh Mac may need the Privacy & Security grant before
  the sudo path works — covered by the README troubleshooting line.
- Terminal runs a `.command` through the user's login shell with
  cwd=`$HOME`, and `sudo` resets the environment (`env_reset`); so the
  wrapper resolves `run.sh` from `$0` — never from cwd, `$PATH`, or
  inherited exports. `run.sh` sets its own `PYTHONPATH`, so nothing is
  lost.
- The probe artifacts live in gitignored `tmp/tcc-test/`; the results
  tables and control notes above are the durable record.
