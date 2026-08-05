# Privileged helper hardening

## Decision

Replace the elevated workflow's user-owned control files with a root-controlled,
per-user state store, and reduce the request file to untrusted mount intent. The
privileged child must derive every privileged path and executable policy itself.
This is an incremental redesign: it preserves the tkinter UI, `runner.py`
orchestration, and the existing `session.py` model rather than adding a parallel
mount flow.

The implementation targets source revision `ce0d038`. The audit evidence came
from the 2026-08-05 static review; no source code has changed since that review.

## Evidence and affected invariants

| Evidence | Affected boundary | Required invariant |
| --- | --- | --- |
| SA-01: root unmount trusts user-writable session JSON | Unprivileged UI state → root cleanup | Root cleanup targets come only from root-owned state and pass strict shape checks before use. |
| SA-02: symlink races in request, session, and log paths | Mutable pathnames → root filesystem operations | The root helper uses validated file descriptors or root-owned directories; it never follows a user-controlled pathname after a check. |
| SA-03: dependency locations inherited from `PATH` | Parent environment → root `exec` | The root helper selects only trusted, policy-approved executable paths. |
| SA-04: `volume_label` escapes `/Volumes` | User input → root mountpoint creation | Every mountpoint is a safely created direct child of `/Volumes`; labels cannot select another path. |
| SA-05: failed unmount erases the retry record | Cleanup failure → recovery | Canonical state remains until all owned resources are confirmed cleaned up. |

The primary security property is that a successful administrator authorization
must grant only the narrowly defined mount or unmount operation. It must not
turn user-owned session JSON, a raceable file path, or the caller's `PATH` into
root authority.

## Design and compatibility constraints

- Continue to use only the Python standard library at runtime.
- Keep the GUI unprivileged and keep the current `runner.py` mount lifecycle.
- Preserve the default read-only mount behavior and existing macOS elevation UX.
- Do not persist passwords, recovery keys, or BEK contents in the canonical
  state or diagnostics.
- Maintain Linux CI and focused unit-test support without requiring root or a
  macOS mount stack.
- Treat direct `sudo ./run.sh` as a documented power-user escape hatch, not as
  a safe substitute for the elevated GUI protocol.
- Elevated GUI mounts support physical `/dev/diskN` and `/dev/diskNsM` sources
  only. Regular-image mounting is intentionally out of scope for this release.

## Chosen design

We will implement one canonical state boundary owned by root:

```text
Unprivileged GUI
  │ mount intent + short-lived secret
  ▼
0600 request file in user directory ──► root child
                                      │ validates intent via file descriptor
                                      │ derives trusted tools and paths
                                      ▼
                         root-owned per-user state directory
                         ├── active_session.json
                         └── operation.log
                                      │ read-only status/error access
                                      ▼
                              Unprivileged GUI
```

The request file remains necessary to avoid putting credentials into AppleScript,
but it is not authoritative. The child derives the canonical session and log
locations from the authenticated UID, not JSON fields. The request contributes
only an action, device selection, method, secret, readonly flag, and a strictly
validated label.

The root-owned state directory should live in a system-managed location (for
example, a fixed directory below `/var/db/dislocker-ui/<uid>`). Root creates it
with a root-owned parent and a per-user group-readable, non-writable child so
the GUI can read its fixed status/log filenames but cannot replace, rename, or
delete them. The final ownership and mode must be verified with descriptor-based
checks; no production environment variable may redirect this root state root.

For a safe migration, the new helper must not import old user-writable session
JSON as trusted state. Release notes should require users to unmount existing
pre-hardening sessions before upgrading. If a legacy session is present after
upgrade, show a clear manual-recovery message rather than passing its paths to
root cleanup.

## Review follow-up

The post-implementation review adds these enforcement details to the same
design, rather than creating a parallel protocol:

- Keep the request-name allowlist synchronized with `tempfile.mkstemp()`'s
  generated alphabet, including underscores.
- Verify a request-supplied group ID belongs to the invoking account before it
  reaches any root-owned state, log, or mount ownership operation.
- Reject a writable executable file itself as well as writable ancestor
  directories in the privileged dependency policy.
- Use one shared physical-device/label policy in the parent, root child, and
  cleanup validation; preserve the parent-side whitespace normalization only
  before it serializes the request.
- Preserve manual recovery guidance for legacy state, finalization failures,
  and FUSE failures whose output is routed to the privileged diagnostic log.
- Read elevated session status from the same canonical path in the GUI and
  preflight the trusted root toolchain before prompting for authorization.
- Redact password and BEK argument variants from unexpected helper errors, and
  keep the root-owned FUSE staging tree private and free of empty parents.

## Ordered work packages

### 1. Establish canonical state and session schema

Affected modules: `session.py`, `elevate.py`, `privileged.py`, `runner.py`.

- Add a root-only state-path provider that derives the location solely from a
  validated UID. Keep the user Application Support directory only for ephemeral
  request transport; do not place canonical session or root diagnostic files
  there.
- Version `MountSession` and add a random session identifier. Save only the
  minimum cleanup state, with a strict decoder that rejects unknown/missing
  fields and validates types before any side effect.
- Make session save, load, and clear operations use a root-owned directory and
  atomic replacement. The write path must use a new temporary file in that
  directory, `fsync` the content as appropriate, set the intended mode, and
  atomically replace the fixed session filename. Do not call path-based
  `chown()` on a user-controlled pathname.
- Keep an explicitly injected state-root parameter or test-only helper for unit
  tests; production code must use the fixed root state root.
- Update unprivileged status reads to treat a missing canonical session as
  "No active session" without trying to create its parent directory.
- Reject legacy user-directory session files in the elevated path and surface a
  precise recovery instruction. Do not silently migrate their cleanup fields.

Acceptance criteria:

- A normal user can read their own fixed session status but cannot write,
  rename, delete, or substitute canonical state files.
- The root unmount path never consumes a session whose bytes came from a
  user-writable directory.
- A partial state write cannot leave malformed JSON treated as an active
  session.

### 2. Replace pathname checks with descriptor-safe request and log handling

Affected modules: `elevate.py`, `privileged.py`, `runner.py`.

- Remove `session_path` and `log_path` authority from the request schema. The
  privileged child derives both canonical paths from `--uid`; it must reject
  payload-supplied alternatives rather than merely confining them.
- Open the request directory and request entry using descriptor-relative calls
  with `O_DIRECTORY`/`O_NOFOLLOW` where supported. Validate the opened request
  with `fstat`: regular file, request UID ownership, mode 0600, and one link.
  Read only from that descriptor.
- Unlink the request through the already-open directory descriptor after the
  file descriptor has been opened. A concurrently substituted symlink must be
  removed as a directory entry, never followed to its target.
- Move elevated diagnostics into the canonical state directory. Open the log
  once, validate it through its descriptor, and pass that descriptor/handle
  through the FUSE wait and error paths. Remove later `is_file()`/`read_bytes()`
  opens of the log pathname.
- Ensure every exception path closes descriptors and removes only the specific
  request directory entry. Retain the canonical log only long enough to report
  a safe error tail, then apply a documented cleanup policy.

Acceptance criteria:

- A symlink substitution at each former request, session, or log race point
  cannot cause root to read, write, chown, or unlink a target outside its owned
  directory.
- Error reporting still distinguishes cancellation, timeout, validation, and
  runner failures without logging unlock secrets.

### 3. Create a root-owned dependency policy

Affected modules: `deps.py`, `elevate.py`, `privileged.py`, `runner.py`,
`README.md`.

- Define fixed absolute paths for macOS system tools (`hdiutil`, `diskutil`, and
  `umount`) and stop using bare command names in fallback and cleanup paths.
- Add a single privileged dependency resolver for `dislocker-fuse` and
  `ntfs-3g`. It must accept only explicitly configured, root-owned executable
  files whose ancestor directories are not writable by group or other users.
  The privileged child must neither accept their locations from request JSON nor
  rediscover them through the caller's `PATH`.
- Decide and document the supported installation model for third-party tools.
  The default should fail closed with a helpful instruction when a user-owned
  Homebrew location is detected. A root-managed configuration file may be added
  only if it is itself root-owned, mode-restricted, and parsed with the same
  descriptor-safe rules.
- Keep unprivileged discovery for the GUI's preflight label, but identify it as
  advisory. Before elevating, compare the UI result with the privileged policy
  result so the user gets an actionable mismatch message instead of an opaque
  failure.
- Pass a validated `DepsStatus` from the privileged resolver into the existing
  runner; change all cleanup and fallback calls to use these paths, including
  `_best_effort_cleanup()`.

Acceptance criteria:

- A user-controlled executable added to `PATH` cannot be selected or executed
  by the root child.
- The root child never invokes `diskutil`, `hdiutil`, or `umount` via a bare
  command name.
- Supported root-managed dislocker and ntfs-3g installations continue to mount
  successfully; unsupported ownership or mode fails before any mount side
  effect.

### 4. Constrain all privileged mount and cleanup targets

Affected modules: `runner.py`, `ntfs_mount.py`, `privileged.py`.

- Add one root-side request validator with typed validation for every remaining
  untrusted field. Validate the action, UID, unlock method, boolean readonly
  value, permitted device form, secret representation, and bounded label before
  the runner is called.
- For elevated operations, accept only a physical device selector in the
  documented `/dev/diskN` or `/dev/diskNsM` form. Reject regular-image paths
  before request creation and independently in the root child. A future
  regular-image feature requires a separately designed descriptor-safe
  interface and macOS integration validation.
- Restrict `volume_label` to a bounded display-name character set and reject an
  absolute path, separators, empty components, dot components, control
  characters, and overlong values. Test both slash and `..` escape attempts.
- Allocate the NTFS mountpoint as a newly created direct child of `/Volumes`,
  with exclusive creation and post-create metadata verification. Keep the
  generated path in canonical session state.
- Create FUSE staging directories below a root-owned application staging root,
  rather than accepting any cleanup directory from session data. Before cleanup,
  validate the session identifier, device selector, FUSE path, and mountpoint
  against those generated roots. Refuse cleanup if an invariant fails.
- Replace unconstrained `shutil.rmtree()` with deletion that is limited to a
  verified, application-created staging directory. Do not attempt destructive
  cleanup when an unmount stage has not established that the resource belongs
  to the active session.

Acceptance criteria:

- No input can make a privileged mountpoint resolve outside `/Volumes`.
- Root cleanup rejects arbitrary session values before invoking an unmount,
  detach, or recursive deletion command.
- An injected invalid payload fails before `dislocker-fuse`, `hdiutil`,
  `ntfs-3g`, or a destructive cleanup call.

### 5. Make unmount recovery transactional and retryable

Affected modules: `runner.py`, `session.py`, `gui.py`.

- Represent cleanup progress in canonical state (for example: NTFS unmounted,
  raw disk detached, FUSE unmounted, directories removed). Update it only after
  the corresponding command succeeds or the resource is independently verified
  absent.
- Retain canonical state when any cleanup stage fails. Report the completed and
  pending stages so a repeated Unmount retries only the remaining work.
- Remove session state only after all mount resources and generated directories
  have been verified absent. If verification is inconclusive, preserve the
  session and give the user safe manual recovery guidance.
- Update the GUI status and unmount messages to distinguish a completed
  unmount, an incomplete retryable cleanup, and an unsafe/legacy session that
  requires manual intervention.

Acceptance criteria:

- A failure in every cleanup command leaves retryable state intact.
- Re-running Unmount completes a partial cleanup without re-detaching or
  deleting unrelated resources.
- The GUI never claims a successful unmount while canonical state records
  pending resources.

### 6. Tests, documentation, release, and operational validation

Affected modules: `tests/`, `README.md`, `SECURITY.md`, `CHANGELOG.md`,
`CHANGELOG.dev.md`, `VERSION`.

- Add unit tests for descriptor-based request loading, symlink replacement at
  each former race site, canonical state permissions, rejection of payload
  session/log/dependency paths, and no-follow diagnostic reads.
- Add root-side request-validation tests for invalid labels, non-boolean
  readonly values, bad device forms, rejected regular-image paths, and
  dependency ownership/mode failures.
- Add runner tests proving cleanup is limited to generated roots, canonical
  session data survives failed stages, and retry resumes from recorded progress.
- Add GUI tests for the new status and recovery messages. Run the full suite
  under Xvfb in CI; retain the focused non-GUI suite for local headless runs.
- Add a macOS manual test matrix: trusted third-party tool configuration,
  normal RO/RW mount, cancelled auth, timed-out auth, legacy-session handling,
  simulated partial unmount, and symlink/adversarial-path rejection. Do not use
  a production BitLocker secret in fixtures or logs.
- Document the trusted dependency installation requirement, the one-time legacy
  session migration behavior, and recovery/retry semantics. Update `VERSION`
  and `CHANGELOG.md` for the user-visible hardening release, with implementation
  details in `CHANGELOG.dev.md`.

## Validation gates

- `python3 -m compileall -q src`
- `ruff check src tests hooks`
- `ruff format --check src tests hooks`
- `xvfb-run -a pytest --cov --cov-report=term-missing`
- `python3 hooks/check_absolute_paths.py`
- `python3 hooks/check_file_size.py`
- `pip-audit --progress-spinner off` in the isolated CI environment
- A focused source review that confirms no privileged path operation follows a
  user-provided pathname and no root subprocess executable comes from request
  JSON or inherited `PATH`.

## Rollout and rollback

Ship the state-store and cleanup protections as one security release; partial
deployment would leave the root child and GUI disagreeing about session state.
Before update, advise users to unmount any active session. On first run, detect
but do not trust legacy session files and offer manual recovery directions.

If a supported root-managed dependency layout proves impractical, keep the
state and descriptor fixes in place and temporarily disable elevation with an
actionable configuration error. Do not roll back to executing user-owned tools
as root. A rollback after release should revert only the new root policy or
state-store code after verifying no mount remains active; it must never restore
trust in legacy user-controlled session data.

## Completion criteria

- The five audit findings have regression tests that fail on the vulnerable
  behavior and pass with the hardened implementation.
- The elevated mount/unmount workflow passes the macOS manual matrix with
  trusted dependencies.
- Canonical state is root-controlled, recovery is retryable, and all user input
  is validated before privileged side effects.
- User-facing docs, version, and both changelogs reflect the compatibility and
  security behavior changes.
