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
  local path="$1"
  [ -e "$path" ] || return 0
  # macOS find -perm: reject group-write (020) or other-write (002).
  if find "$path" -maxdepth 0 \( -perm -0020 -o -perm -0002 \) | grep -q .; then
    echo "dislocker-ui: refusing world/group-writable path as root: $path" >&2
    exit 1
  fi
}
_check_not_group_world_writable "$0"
_check_not_group_world_writable "$ROOT"
_check_not_group_world_writable "$ROOT/src"
_check_not_group_world_writable "$ROOT/src/dislocker_ui/__main__.py"

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
# Drop to a clean cwd; keep SUDO_UID/SUDO_GID so mounts own files as the user.
exec python3 -m dislocker_ui "$@"
