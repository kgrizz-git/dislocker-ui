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
#   - Resolve the script's real path (following symlinks) so ROOT is the
#     true repo root, never cwd or $PATH (Terminal starts us in $HOME).
#     Without resolution, a symlink named like this file plus a planted
#     executable run.sh in a user-writable directory would put a
#     double-click's admin password behind sudo of the planted script.
#   - /usr/bin/sudo is absolute because login-shell PATH is not
#     guaranteed under every launcher.
#   - Why not an .app? Measured 2026-10-17: a root child spawned by
#     osascript's admin prompt cannot open removable /dev/disk* nodes —
#     even with Full Disk Access granted to the .app — while sudo in
#     Terminal opens them all. The severed-descent explanation is the
#     working hypothesis for that differential (see
#     plans/double-clickable-app.md). Terminal-attached sudo is the
#     only viable double-click design.
set -euo pipefail

SELF="$0"
# Fail closed: if the real path cannot be resolved, refuse to run —
# falling back to $0 would let a symlink named like this file reach
# sudo for an attacker's planted run.sh. Resolvers are absolute for the
# same reason sudo is: login-shell PATH is not guaranteed. readlink -f
# needs macOS 12.3+; older systems hit this refusal.
if ! _resolved="$(/usr/bin/readlink -f "$SELF" 2>/dev/null)" || [ -z "$_resolved" ]; then
  echo "dislocker-ui: cannot resolve $SELF" >&2
  exit 1
fi
SELF="$_resolved"
ROOT="$(cd "$(/usr/bin/dirname "$SELF")" && pwd)" || {
  echo "dislocker-ui: cannot resolve $(/usr/bin/dirname "$SELF")" >&2
  exit 1
}

if [ ! -f "$ROOT/run.sh" ] || [ ! -x "$ROOT/run.sh" ]; then
  echo "dislocker-ui: $ROOT/run.sh is missing or not executable" >&2
  exit 1
fi

# run.sh's own gates run as root *inside* run.sh, i.e. after sudo has
# already executed whatever run.sh is. Gate it before sudo: refuse a
# symlinked run.sh (its target's mode bits prove nothing) and a run.sh
# or repo root that another user could replace.
if [ -L "$ROOT/run.sh" ]; then
  echo "dislocker-ui: refusing symlinked $ROOT/run.sh" >&2
  exit 1
fi
for _p in "$ROOT" "$ROOT/run.sh"; do
  _mode="$(/usr/bin/stat -f '%OLp' "$_p")"
  if [ $((8#$_mode & 022)) -ne 0 ]; then
    echo "dislocker-ui: refusing group/world-writable $_p" >&2
    exit 1
  fi
done

if [ "$(id -u)" -eq 0 ]; then
  exec "$ROOT/run.sh" "$@"   # already root (e.g. sudo ./dislocker-ui.command)
fi

# Cosmetic, first launch only: give this file its Finder icon. Git does not
# carry Finder icons (they live in xattrs), so set it locally, as the user,
# before sudo. Best-effort: any failure is ignored and never blocks launch.
if [ -f "$ROOT/assets/icon.png" ] && [ -x /usr/bin/osascript ] &&
  ! /usr/bin/xattr "$SELF" 2>/dev/null | /usr/bin/grep -qx com.apple.ResourceFork; then
  /usr/bin/osascript -l JavaScript \
    -e 'ObjC.import("AppKit")' \
    -e 'function run(a) { $.NSWorkspace.sharedWorkspace.setIconForFileOptions($.NSImage.alloc.initWithContentsOfFile(a[0]), a[1], 0) }' \
    "$ROOT/assets/icon.png" "$SELF" >/dev/null 2>&1 || true
fi

exec /usr/bin/sudo "$ROOT/run.sh" "$@"
