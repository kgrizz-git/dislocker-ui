"""
In-app help strings for dislocker-ui.

Overall purpose:
  Hold the About / Usage / Troubleshooting texts shown by the GUI Help
  menu, so gui.py stays thin and the wording lives in one place.

Inputs:
  A version string for `about_text`; nothing for the constants.

Outputs:
  Plain-text strings (no markdown) sized for dialog display. Lines are
  wrapped near 72 columns except copy-pasteable shell commands, which
  are kept intact on one line.

Requirements:
  Standard library only (just the __future__ import). Exports REPO_URL,
  ABOUT_TEXT, USAGE_TEXT, TROUBLESHOOTING_TEXT, and about_text(). No
  secrets and no machine-specific paths.
"""

from __future__ import annotations

REPO_URL = "https://github.com/kgrizz-git/dislocker-ui"

ABOUT_TEXT = """dislocker-ui {version}
A macOS helper that unlocks BitLocker volumes with an
installed dislocker-fuse, mounts them under /Volumes
(read-only by default), and unmounts them again.
License: GPL-3.0-or-later.
"""

USAGE_TEXT = """Usage
1. Launch with: sudo ./run.sh
2. Select a volume (or type /dev/diskXsY).
3. Choose the unlock method and enter the secret.
4. Leave Read-only checked unless you need writes.
5. Click Mount, then open the path under /Volumes.
6. Click Unmount before ejecting the disk.
"""

TROUBLESHOOTING_TEXT = """Troubleshooting
"A session is already active" after a reboot, or Unmount
never finishes: click Unmount (0.5.3+ cleans up stale
sessions itself). On older releases, first confirm nothing
is still mounted or attached (mount | grep -E
'DislockerUI|dislocker-ui'; hdiutil info), then:
sudo rm "/var/root/Library/Application Support/dislocker-ui/active_session.json"
(use your own $HOME instead of /var/root if your sudo preserves it;
sudo rm "/var/db/dislocker-ui/$(id -u)/active_session.json"
for the admin-prompt path).
"Missing required tools": install per the README (Homebrew tap),
then run sudo scripts/install-root-deps.sh from the repo root,
then Recheck deps.
"NTFS volume requires ntfs-3g": FAT/ExFAT works without it.
macFUSE not approved: approval appears the first time you
mount a FUSE volume (System Settings, Privacy & Security).
On Apple Silicon, enabling kernel extensions may need
Recovery first; then reboot.
dislocker stops loading after brew upgrade: reinstall
mbedtls@3 and re-create the libmbedcrypto symlink (see the
README install section); re-run it after each upgrade.
Cannot open the disk: launch with sudo ./run.sh — an
unprivileged GUI is blocked by macOS TCC.
"Refusing to elevate: cannot inspect ...": usually a
root-owned or unreadable file under src/ (e.g. a __pycache__
left by an earlier sudo ./run.sh). Follow the printed
hint, or launch with sudo ./run.sh instead.
"""


def about_text(version: str) -> str:
    """Return the About dialog text with the running version filled in."""
    return ABOUT_TEXT.replace("{version}", version) + REPO_URL + "\n"
