#!/usr/bin/env bash
# Launch dislocker-ui from the repo root.
#
# Must run under sudo: macOS TCC blocks the unprivileged GUI's osascript-elevated
# child from opening removable /dev/disk* nodes (EPERM), so a useful mount
# requires an already-root process. The GUI then mounts in-process and skips
# the administrator dialog.
#
# Ownership model (power-user path): this runs Python from the *checkout* as
# root via PYTHONPATH. Keep the repo (especially run.sh and src/) owned by you
# and not world-/group-writable. Do not point sudo at a shared or untrusted
# tree. A root-owned install package is out of scope for this personal helper.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "dislocker-ui: run with sudo so mounts can open /dev/disk*:" >&2
  echo "  sudo $0" >&2
  exit 1
fi
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Refuse a world-/group-writable launcher or src tree before root executes it.
_check_not_group_world_writable() {
  local path="$1" mode
  [ -e "$path" ] || return 0
  # BSD/macOS stat (this launcher is Darwin-only). %OLp = octal mode bits.
  mode="$(stat -f '%OLp' "$path")"
  # Reject group-write (020) or other-write (002).
  if [ $((8#$mode & 022)) -ne 0 ]; then
    echo "dislocker-ui: refusing world/group-writable path as root: $path" >&2
    exit 1
  fi
}
_check_not_group_world_writable "$0"
_check_not_group_world_writable "$ROOT"
_check_not_group_world_writable "$ROOT/src"
_check_not_group_world_writable "$ROOT/src/dislocker_ui/__main__.py"

# Mirror elevate._assert_no_writable_py_files: a group-/world-writable .py,
# .pyc, .so, or directory anywhere under src/ is a trojan module that root
# would import via PYTHONPATH. Checking src/ itself is not enough because a
# single file can be writable while its parent stays 755.
if ! _bad_src="$(find "$ROOT/src" \
  \( -name '.git' -o -name '.hg' -o -name '.pytest_cache' -o -name '.tox' \
     -o -name '.eggs' -o -name 'build' -o -name 'dist' -o -name 'tmp' \
     -o -name '*.egg-info' \) -prune \
  -o \( -type d \( -perm -020 -o -perm -002 \) -print \) \
  -o \( -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.so' \) \
     \( -perm -020 -o -perm -002 \) -print \) 2>/dev/null)"; then
  # Fail closed with a diagnostic (set -e would otherwise abort silently).
  echo "dislocker-ui: could not scan src/ for writable modules as root" >&2
  exit 1
fi
# The top-level src/ dir was already gated above; ignore it here so the
# recursive report lists only descendant trojan paths. Fixed-string match:
# $ROOT may contain regex metacharacters (a broken pattern + || true would
# silently discard every finding).
_bad_src="$(printf '%s\n' "$_bad_src" | grep -v -xF -- "$ROOT/src" || true)"
# Symlinks are not followed by find (-P). Classify each link: a symlinked
# directory is importable as a package yet never descended into, and an
# importable-named link (.py/.pyc/.so, as seen by the import system) resolves
# to a target whose mode bits alone prove nothing (attacker-owned 0644, or
# replaceable through a writable parent) — refuse both outright. A
# non-importable link (docs, resources) cannot be imported and is skipped, so
# benign links cannot block startup.
_bad_links=""
if ! _bad_links="$(find "$ROOT/src" \
  \( -name '.git' -o -name '.hg' -o -name '.pytest_cache' -o -name '.tox' \
     -o -name '.eggs' -o -name 'build' -o -name 'dist' -o -name 'tmp' \
     -o -name '*.egg-info' \) -prune \
  -o \( -type l -print \) 2>/dev/null)"; then
  echo "dislocker-ui: could not scan src/ symlinks for writable targets as root" >&2
  exit 1
fi
_bad_link_targets=""
if [ -n "$_bad_links" ]; then
  while IFS= read -r _link; do
    [ -n "$_link" ] || continue
    if [ ! -e "$_link" ] && [ ! -L "$_link" ]; then
      # Unresolvable entry (e.g. a newline-split filename fragment, or a file
      # that vanished mid-scan): the scan cannot represent it, so fail closed
      # instead of silently skipping a possibly-writable target.
      echo "dislocker-ui: refusing unreadable path under src/ as root" >&2
      exit 1
    fi
    if [ -d "$_link" ]; then
      _bad_link_targets="${_bad_link_targets:+$_bad_link_targets
}$_link"
    else
      case "$_link" in
      *.py | *.pyc | *.so)
        # Importable name: refuse outright. Target mode bits prove nothing
        # (attacker-owned 0644, or replaceable through a writable parent),
        # so no stat check can clear it.
        _bad_link_targets="${_bad_link_targets:+$_bad_link_targets
}$_link"
        ;;
      esac
    fi
  done <<< "$_bad_links"
fi
if [ -n "$_bad_src" ] || [ -n "$_bad_link_targets" ]; then
  echo "dislocker-ui: refusing group/world-writable Python module under src/ as root:" >&2
  if [ -n "$_bad_src" ]; then
    printf '%s\n' "$_bad_src" >&2
  fi
  if [ -n "$_bad_link_targets" ]; then
    printf '%s\n' "$_bad_link_targets" >&2
  fi
  exit 1
fi

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
# Drop to a clean cwd; keep SUDO_UID/SUDO_GID so mounts own files as the user.
exec python3 -m dislocker_ui "$@"
