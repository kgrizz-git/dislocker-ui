#!/usr/bin/env bash
# Launch dislocker-ui from the repo root.
#
# Must run under sudo: macOS TCC blocks the unprivileged GUI's osascript-elevated
# child from opening removable /dev/disk* nodes (EPERM), so a useful mount
# requires an already-root process. The GUI then mounts in-process and skips
# the administrator dialog.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "dislocker-ui: run with sudo so mounts can open /dev/disk*:" >&2
  echo "  sudo $0" >&2
  exit 1
fi
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
# Drop to a clean cwd; keep SUDO_UID/SUDO_GID so mounts own files as the user.
exec python3 -m dislocker_ui "$@"
