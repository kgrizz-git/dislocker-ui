# Elevation checkout seal

**Status:** Implemented on `fix/elevation-checkout-seal` (0.7.1). Not merged.

The administrator dialog waits up to 600 seconds after the unprivileged
parent has inspected `src/`. The authorized command used to bind only the
request-file digest. `python -m dislocker_ui.privileged` then imported the
package as root before `main()` could look at the tree again, so a
same-mode rewrite of a user-owned module during the wait became root code.

## Approach

- Measure importable `.py` / `.pyc` / `.so` bytes and directory modes in
  the parent, immediately before `osascript`.
- Embed that digest and the seal source (captured when the parent imported
  it) in the authorized command. The root process runs `python -I -S -c`
  with that source, not `python -m`.
- The seal re-measures the tree. On a match it copies those bytes into a
  mode-0700 directory and imports from the copy. A mismatch exits 2 and
  does not import the package.
- Refuse importable symlinks and symlink directories, matching `run.sh`.
- Refuse the dialog when the Python interpreter, or any ancestor, is not
  root-owned and free of group/world write. `sudo ./run.sh` remains the
  path for pyenv and user-owned Homebrew.

## Checklist

- [x] Seal module with no `dislocker_ui` imports.
- [x] Dialog command binds the digest and does not start via `python -m`.
- [x] Bootstrap test: a rewritten module is not executed.
- [x] Bootstrap test: a match imports the private copy, then deletes it.
- [x] User-owned interpreter never reaches `osascript`.
