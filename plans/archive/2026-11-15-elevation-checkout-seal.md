# Elevation checkout seal

**Status:** Shipped in 0.7.1 (PR #24). Archived 2026-11-15.

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
  does not import the package. When the process is root, that directory's
  parent is an existing root-owned directory (`/private/var/root`,
  `/var/root`, or `/root`), not the caller's `TMPDIR`. The seal checks
  the copy's device and inode again after writing it.
- Refuse importable symlinks and symlink directories, matching `run.sh`.
- Refuse the dialog when the Python interpreter, or any ancestor, is not
  root-owned and free of group/world write. `sudo ./run.sh` remains the
  path for pyenv and user-owned Homebrew.
- Do not skip file bytes because `site-packages` is root-owned. Seal the
  `dislocker_ui` package and import the private copy.
- Open importable paths with `O_NONBLOCK` as well as `O_NOFOLLOW`, so a
  FIFO is refused instead of stalling the open.
- Embed the interpreter path returned by the ownership check. Do not
  resolve it again while building the authorized command.

## Checklist

- [x] Seal module with no `dislocker_ui` imports.
- [x] Dialog command binds the digest and does not start via `python -m`.
- [x] Bootstrap test: a rewritten module is not executed.
- [x] Bootstrap test: a match imports the private copy, then deletes it.
- [x] User-owned interpreter never reaches `osascript`.
- [x] Root creates the private copy outside the caller's temporary directory.
- [x] An install root imports the private package copy; a missing package is refused.
- [x] An importable FIFO is refused without blocking.
- [x] The authorized command uses the interpreter path the ownership check returned.
