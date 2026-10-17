#!/usr/bin/env bash
# Double-clickable launcher for dislocker-ui (macOS).
#
# Finder runs a .command in Terminal via the user's login shell
# ("path ; exit") with cwd=$HOME; the sudo prompt appears there. This
# wrapper stays unprivileged: every root-side check (writability gates,
# trojan-module scans, cwd drop) lives in run.sh, which re-validates
# everything once it runs as root.
#
# Invariants:
#   - Resolve ROOT from $0, never cwd or $PATH (Terminal starts us in
#     $HOME). Keep this a real file at the repo root: invoked through a
#     symlink, $0 is the link and ROOT would point at the wrong tree.
#   - /usr/bin/sudo is absolute because login-shell PATH is not
#     guaranteed under every launcher.
#   - Why not an .app? macOS TCC gates removable /dev/disk* opens on the
#     responsible process: an osascript-elevated child of a .app is
#     severed from the launcher and gets EPERM even as root, even with
#     Full Disk Access granted to the .app (measured 2026-10-17; see
#     plans/double-clickable-app.md). Terminal-attached sudo keeps
#     descent through consent-holding Terminal.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ ! -x "$ROOT/run.sh" ]; then
  echo "dislocker-ui: $ROOT/run.sh is missing or not executable" >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
  exec "$ROOT/run.sh" "$@"   # already root (relaunched via sudo)
fi

exec /usr/bin/sudo "$ROOT/run.sh" "$@"
