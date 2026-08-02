# macOS admin elevation for mount/unmount

Status: **revise-complete — ready to implement** on `feature/macos-elevation` as **0.2.0**.

## Advisor history

| Review | Verdict |
|--------|---------|
| Claude Opus (architecture) | APPROVE WITH CHANGES — folded in (ntfs-3g required, full pipeline, deps serialize, FUSE lifetime, etc.) |
| Codex (detail sufficiency) | **DETAILED ENOUGH WITH FIXES** — folded in below (API/routing, parent/child protocol, BEK `~`, session path, contradictions) |

## Problem

1. `/dev/disk*` is `root:operator` mode `640`. The GUI user is not in `operator`, so `dislocker-fuse` fails with `Permission denied`.
2. On macOS 26, `/sbin/mount_ntfs` and `ntfs.fs/.../mount_ntfs` do **not exist**. `runner.py` default RO path `mount -t ntfs -o rdonly` cannot work. `ntfs-3g` is installed and must become required for RO and RW.

## Chosen approach

**Elevate the full mount/unmount pipeline** via `osascript` (`do shell script … with administrator privileges`). Keep the tkinter GUI unprivileged.

**Public API (locked):** GUI keeps calling `runner.mount_volume()` / `runner.unmount_volume()` only. Those functions become the facade that either:
- runs the existing in-process path when already root (`euid == 0`), or
- dispatches through `elevate.py` → privileged child when Darwin and `euid != 0`.

Do **not** teach `gui.py` a second elevation entry point.

**Why full pipeline:** elevating only `dislocker-fuse` still leaves `/Volumes` mkdir and NTFS mount needing root, and `allow_other` would land on dislocker (no `uid=`/`gid=`). Full elevation moves `allow_other` onto **ntfs-3g**, which supports `uid=`, `gid=`, `umask=`, etc.

```mermaid
sequenceDiagram
  participant UI as GuiUserProcess
  participant Run as RunnerFacade
  participant Elev as Elevate
  participant Req as RequestFile0600
  participant OSA as OsascriptAuth
  participant Priv as PrivilegedPython

  UI->>Run: mount_volume / unmount_volume
  alt already root
    Run->>Run: in-process pipeline
  else Darwin and euid != 0
    Run->>Elev: prepare request plus osascript
    Elev->>Req: write mkstemp request 0600
    Elev->>OSA: admin prompt timeout 600s
    OSA->>Priv: abs python -s -P -m dislocker_ui.privileged
    Priv->>Req: read validate unlink
    Priv->>Priv: in-process pipeline elevated true
    Priv-->>Elev: exit code
    Elev->>Elev: always unlink request; on failure tail log
    Elev-->>Run: raise or return via load_session
  end
  Run-->>UI: MountSession or RunnerError
```

## Branch / version

- Branch: `feature/macos-elevation` (already created from `main`)
- Semver: **0.2.0**
- Update `VERSION`, `CHANGELOG.md`, `README.md`, `AGENTS.md`
- Fix pre-existing version drift while editing:
  - README says “currently 0.1.5”; `VERSION` is already `0.1.6`.
  - `CHANGELOG.md` has no `0.1.6` entry — add it (or fold notes) plus the new `0.2.0` section.

## Module boundaries (keep runner thin)

| Module | Owns |
|--------|------|
| `elevate.py` | Predicate, AppleScript build/run, request write/unlink, cancel/timeout errors, log tail helper |
| `privileged.py` | CLI, request validate/parse, log open hardening, call runner in-process with explicit paths |
| `runner.py` | Mount/unmount pipeline + **dispatch** to elevate when needed; ntfs-3g options; session_path plumbing. Keep elevation/request/quoting **out** of runner to stay under the 600-line soft cap. |
| `gui.py` | Unchanged call sites; deps-hint copy; map cancel error to dialog text |

---

## Implementation checklist

### 0. NTFS: require ntfs-3g (blocker)

- [ ] `deps.py`: treat `ntfs-3g` as **core** on Darwin (RO and RW). Update `core_ok` / missing-tool messaging / GUI hints.
- [ ] `runner.py` `_mount_ntfs`: drop kernel `mount -t ntfs -o rdonly`. Always use ntfs-3g; RO → `-o ro`.
- [ ] Options: `ro` or RW defaults, plus `allow_other,local,uid=<orig_uid>,gid=<orig_gid>,umask=077,volname=<label>`. **`umask=077` is mandatory** with `allow_other` (otherwise every local account can read decrypted BitLocker data). Prefer `fmask=177,dmask=077` if directory execute bits matter.
- [ ] **Decision (locked): drop `mount`** from `DepsStatus` / `core_ok` / `missing_core`. After removing the kernel RO path it is unused; `umount` stays. Update `test_deps.py`.
- [ ] **`uid`/`gid` source (decided):**
  - Elevated path: integers from the **unprivileged parent** (`os.getuid()` / `os.getgid()`) written into the request.
  - Already-root in-process path: use `SUDO_UID` / `SUDO_GID` when set and valid (int, resolves via `pwd`); otherwise fall back to `0`/`0`. **Warn + proceed — never refuse.** With `0`/`0` an RW mount is root-owned, so log a clear line that the user may need `sudo` to write; refusing would only trap the power-user `sudo ./run.sh` escape hatch for no safety gain (they are already root by choice).
- [ ] Docs: ntfs-3g required on modern macOS.

### 1. `elevate.py` — exported API (locked)

```text
needs_elevation() -> bool
  # Darwin and os.geteuid() != 0

volume_needs_elevation(volume: str) -> bool
  # UI messaging only: not root and not os.access(volume, R_OK)

class ElevationCancelled(RunnerError): ...
class ElevationTimedOut(RunnerError): ...

run_elevated_mount(req, deps, log, *, session_path, log_path) -> MountSession
run_elevated_unmount(deps, log, *, session_path, log_path) -> None
```

- [ ] Build osascript via `subprocess.run(["/usr/bin/osascript", "-e", script], …)` — never `shell=True`.
- [ ] Two-layer quoting: `shlex.quote` each argv piece, then AppleScript-escape the assembled shell string. Do **not** use `json.dumps` as the AppleScript escaper.
- [ ] Secrets never in AppleScript — only the `mkstemp` request path + constant prompt.
- [ ] Embed **absolute** `sys.executable`, `PYTHONPATH=<abs src>`, and `-s -P` **inside** the `do shell script` string (parent `env=` is **not** inherited by admin `do shell script`).
- [ ] Wrap in `with timeout of 600 seconds`.
- [ ] Map cancel (`User canceled. (-128)`) → `ElevationCancelled`; timeout → `ElevationTimedOut`; other failures → `RunnerError` with log tail.
- [ ] On unmount cancel: do **not** `clear_session`.

### 2. `privileged.py` + parent/child protocol (locked)

CLI:

```text
<abs python> -s -P -m dislocker_ui.privileged mount|unmount --request PATH
# cwd forced to /
```

**Request JSON** (`mkstemp` in `$TMPDIR`, mode 0600) fields:

| Field | Notes |
|-------|--------|
| `action` | `"mount"` \| `"unmount"` |
| `volume`, `method`, `secret`, `readonly`, `volume_label` | mount only; **BEK `secret` must already be an absolute real path** (parent canonicalized; child must **not** `expanduser`) |
| `session_path` | **Fixed** Application Support `active_session.json` (see §3) — not a random temp path |
| `log_path` | Path to pre-created 0600 log from parent `mkstemp` |
| `uid`, `gid` | Invoking user’s ids |
| `deps` | Serialized absolute paths from `DepsStatus` — child must **not** call `discover_deps()` |

**Exit codes (child → parent):**

| Code | Meaning |
|------|---------|
| `0` | Success (mount: session written; unmount: cleared) |
| `2` | Request validation failed |
| `3` | Mount/unmount `RunnerError` (details appended to `log_path`) |
| `4` | Unexpected exception (traceback appended to `log_path`, secrets redacted) |
| other / osascript non-zero | Auth cancel/timeout/osascript failure — classified by `elevate.py` from stderr text |

**Exit-code surfacing (required for the table above to work):** `do shell script` raises an AppleScript error whose **`error number` equals the child's shell exit status** (and `-128` for user cancel). So the AppleScript must let that status propagate — do **not** mask it with `|| true`, a trailing pipe, or `; echo`. `elevate.py` reads `osascript`'s non-zero exit and parses the trailing `(N)` / `number N` from its stderr to recover `2`/`3`/`4` vs `-128` vs timeout. Test `elevate.py` against captured sample stderr strings for each case so the mapping can't silently regress.

**Parent behavior after osascript returns:**

1. Always `unlink` request in `finally`.
2. On non-zero: read/tail last ~8 KiB of `log_path` into the raised error (if log exists).
3. On mount success (`0`): `load_session(session_path)` and return; if missing/invalid → `RunnerError`.
4. Child stdout/stderr from osascript itself is secondary; **authoritative diagnostics live in `log_path`**.

### 3. Request / log / session lifecycle (security)

**Paths (locked):**

- `session_path` = normal Application Support session file from `default_session_path()` (`Path.home() / "Library" / "Application Support" / "dislocker-ui" / "active_session.json"`) run **as the user before elevating**. Pre-create the directory (user-owned); **never** a random `mkstemp` session path (that would break restart/unmount discovery).
  - **Decided: do not pre-create a placeholder session file.** The symlink-swap protection comes from the pre-flight check (§Hardening) that the directory is user-owned and not a symlink / group-world-writable. Once the dir is validated, only the user or root could plant a symlink at `active_session.json`, so a placeholder inode adds nothing (and `runner.save_session` writes via `write_text`, which wouldn't `O_NOFOLLOW` anyway). Keep it simple.
- `log_path` = **`mkstemp`** 0600 under `$TMPDIR` (ephemeral diagnostics only).
- `request_path` = **`mkstemp`** 0600 under `$TMPDIR`.

Lifecycle:

- [ ] User process always `unlink`s request in its own `finally` (covers auth cancel).
- [ ] Child opens log **`O_APPEND | O_NOFOLLOW`**, `fstat` → regular file, owner == request uid, mode 0600; uses request `session_path` only; `chown(uid,gid)` session file after success.
- [ ] Child must never call `default_session_path()` (its `mkdir` side effect as root would root-own Application Support).
- [ ] **Secrets:** request JSON holds the BitLocker secret briefly (better than putting it in AppleScript). **Residual risk remains:** `_build_dislocker_cmd` still passes `--user-password=` / `--recovery-password=` on `dislocker-fuse` **argv** (visible to `ps`). §7 keeps changing that out of scope — document both facts in README. Redact via `_redact_cmd` in privileged logging.
- [ ] Document: elevating a user-writable repo is no stronger than `sudo ./run.sh`.

### 4. Session + runner plumbing

- [ ] `MountSession.elevated: bool = False` (defaulted for forward-compat).
- [ ] Plumb explicit `session_path` through all five runner sites: save (~110), load unmount (~126), clear success (~137), load validate (~169), **`clear_session` in `_best_effort_cleanup` (~404)**.
- [ ] Elevated `dislocker-fuse`: stdout/stderr → log-file fd; `start_new_session=True`. `_wait_for_file` failure text from **log tail**, not `proc.stdout`.
- [ ] Unmount re-elevates when `session.elevated` (second password prompt — document).
- [ ] **BEK:** parent resolves `Path(secret).expanduser().resolve()` before writing the request; child uses the absolute path as-is (no `expanduser`).
- [ ] Already-root: in-process path (no osascript).

### 5. GUI

- [ ] Still calls `mount_volume` / `unmount_volume` only.
- [ ] Log `Requesting administrator privileges…` when `needs_elevation()`.
- [ ] `ElevationCancelled` → distinct non-scary dialog; `ElevationTimedOut` → distinct message.
- [ ] Reword deps hints (`_refresh_deps_label`, `_update_rw_hint`): without ntfs-3g there is **no** mount, not “RO only”.
- [ ] No new buttons.

### 6. Tests / docs

Coverage: `elevate.py` / `privileged.py` count toward `fail_under = 80`. `runner.py` remains omitted from coverage **but still gets focused unit tests** (mocked subprocess) for: ntfs-3g option assembly, session_path plumbing, elevated vs in-process dispatch, BEK canonicalize-before-request.

- [ ] `tests/test_elevate.py`: predicate; quoting with spaces/quotes/`$`/backticks; cancel vs timeout; AppleScript contains request path **not** secret.
- [ ] `tests/test_privileged.py`: request schema; deps round-trip; no `discover_deps()`; `O_NOFOLLOW` rejects symlink log; exit-code mapping via invoking `main` helpers.
- [ ] `tests/test_deps.py`: `mount` removed; ntfs-3g core on Darwin.
- [ ] `tests/test_gui.py`: deps hints; cancel message.
- [ ] `tests/test_runner_elevation.py` (or extend a new focused file): dispatch + ntfs-3g opts + BEK abs path (mocks only).
- [ ] Darwin-only no-auth osascript round-trip (skip on CI/Linux).
- [ ] Manual: RO mount; RW mount + write as user; other users cannot read mount; cancel; unmount; remount; `sudo ./run.sh`.
- [ ] README / AGENTS: admin dialog; two prompts; ntfs-3g required; macFUSE caveat; argv residual secret note; `sudo` fallback.

### 7. Out of scope

- SMJobBless / launchd helpers
- Linux/Windows elevation
- Changing dislocker password delivery away from CLI flags (**argv residual risk stays**)
- Auto-adding user to `operator`
- Full FUSE-T validation in 0.2.0 (README caveat)

---

## Hardening (in scope for 0.2.0)

Do **not** defer these:

- [ ] Validate volume before elevating: `^/dev/disk\d+(s\d+)?$` or existing regular file.
- [ ] Child re-validates request (action, absolute paths, uid/gid, deps exist).
- [ ] Python `-s -P`, cwd `/`, explicit `PYTHONPATH`.
- [ ] `fstat` log (and session fd if opened) after open.
- [ ] Pre-flight: refuse elevate if `src/` or Application Support dir is symlink or group/world-writable.
- [ ] Post-mount ownership assert on mountpoint uid; abort-cleanup on mismatch.
- [ ] **Audit log line** (not “tamper-evident”): timestamp, uid, action, volume — no secret. User-writable log is best-effort diagnostics only.
- [ ] Write secret into request last; unlink promptly after child read; keep `_redact_cmd`.

---

## Prioritized risks addressed

| Risk | Plan response |
|------|----------------|
| No `mount_ntfs` on macOS 26 | ntfs-3g required; RO via `-o ro` |
| Root PATH misses Homebrew | Serialize DepsStatus |
| Root ntfs-3g without uid/gid/umask | uid/gid/allow_other/local/umask=077 |
| BEK `~` expands as root | Parent canonicalizes; child no expanduser |
| FUSE daemon SIGPIPE | log fd + start_new_session |
| Secret file on cancel | User-process finally unlink |
| Root-owned Application Support | User pre-creates; child never default_session_path |
| Random session path breaks unmount | Fixed `active_session.json` path |
| cleanup clear_session wrong path | Plumb session_path including cleanup |
| AppleEvent timeout | 600s wrapper |
| Password still in dislocker argv | Document residual; out of scope to change |
| FUSE-T unknown | README macFUSE-only caveat |

## Non-security residual notes

- Two admin prompts per mount/unmount cycle — accepted for 0.2.0.
- `local` / `allow_other` / `umask` need macFUSE-backed ntfs-3g.
- Auth dialog idle → timeout error (distinct from cancel `-128`).
