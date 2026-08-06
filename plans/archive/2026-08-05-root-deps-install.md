# Root-managed dependency installer

## Decision

Add a one-time, opt-in installer that builds `dislocker-fuse` and `ntfs-3g`
from source and installs them — root-owned, mode-restricted — into a
root-managed `sbin` directory that the existing privileged dependency policy
(`deps.discover_privileged_deps`) already accepts. Point the GUI's existing
"Privileged tools unavailable" dialog and the README at the script. Do **not**
fold installation into `run.sh`; it stays a pure unprivileged launcher.

This removes the gap users hit on first use: the GUI's preflight accepts a
user-owned Homebrew `dislocker-fuse`/`ntfs-3g`, but the elevated mount flow
refuses to execute anything not root-managed, so the user is told an
administrator must install the tools and given no actionable path beyond "see
README." The installer is that actionable path.

## Evidence and affected invariants

| Evidence | Affected boundary | Required invariant |
| --- | --- | --- |
| GUI "Privileged tools unavailable" dialog (`src/dislocker_ui/gui.py:245-251`) blocks Mount with no install path | User onboarding → first successful mount | A user who can authorize admin can complete the one-time trusted toolchain setup without hand-editing sbin. |
| `deps._is_trusted_executable` / `_has_trusted_ancestors` (`src/dislocker_ui/deps.py:124-162`; the `st_uid != 0` executable check is `deps.py:137`) reject user-owned binaries and ancestor dirs | Homebrew prefix → root exec | Installed binaries and every ancestor directory must be root-owned, regular, non-group/world-writable. The installer must produce that layout exactly. |
| Homebrew bottles link against `@rpath`/`@loader_path` dylibs under the user-owned Homebrew prefix | Copy/symlink binary → runtime dyld failure or trust failure | `install`-copy of a Homebrew binary breaks either dyld or the ancestor check. A from-source `--prefix=$PREFIX` build links against `$PREFIX/lib` so the binary and its libs are colocated root-managed. |
| `deps._PRIVILEGED_TOOL_CANDIDATES` (`src/dislocker_ui/deps.py:33-36`) accepts `/usr/local/sbin` and `/opt/local/sbin` only | Install location → acceptance | The installer must target one of these two prefixes and refuse anything else. |
| AGENTS.md non-goals: do not bundle macFUSE / FUSE-T / ntfs-3g in the repo | Distribution scope | The repo still ships no third-party binaries or sources; the installer fetches and builds them on the user's machine. |

## Design and compatibility constraints

- Keep all existing privileged-dependency enforcement in `deps.py` unchanged.
  The script must *satisfy* the policy, not weaken it. No new accepted prefix
  dirs, no bypass flag, no "trust this user-owned path" knob.
- Standard library only for any Python touched; the installer itself is Bash.
- `run.sh` is unchanged. So is `sudo ./run.sh` as a power-user escape hatch.
- The installer is interactive where macOS forces it: `brew install --cask
  macfuse` needs an interactive Terminal `sudo`, a System Settings approval,
  and often a reboot. The script pauses at that step and resumes on re-run.
- macOS only. Refuse to run on Linux even though the module tree is CI-tested
  there — this installer is a macOS-only convenience.
- Do not commit anything machine-specific (Homebrew prefixes, user paths,
  device nodes) into the script's defaults beyond the two canonical prefixes.
- GPL-3.0-or-later continues to cover the repo; the installer fetches
  dislocker (GPL-2.0-or-later) and ntfs-3g (GPL-2.0+) under their own terms
  and does not vendor their sources.

## Chosen design

The installer runs in **two phases** under a **single root orchestrator**.
Homebrew hard-aborts under `euid 0` ("Running Homebrew as root is a bad
idea"), so anything that touches `brew` must run unprivileged; only the actual
install-into-`$PREFIX` step runs elevated. The script is invoked by the user
as `sudo`, so it starts as root and **stays root for the whole run**. It
*spawns* the brew+build work as the invoking user via `sudo -H -u`, waits for
that child to finish staging, then continues — still root, no re-authentication
— into the install step. This is deliberately *not* a "drop to user, then
re-elevate" model: a process that has dropped to the user cannot return to
root without a second password prompt, and `sudo -k` would force one. Keeping
root as the parent means exactly one authentication for the whole run.
Idempotent re-runs short-circuit at each phase.

```text
sudo scripts/install-root-deps.sh [--prefix /usr/local|/opt/local] [-y]
  │  (all phases: macOS only, umask 022 set at top)
  │
  ├── phase 0 (root orchestrator): preflight — refuse non-darwin; resolve
  │                  $SUDO_UID/$SUDO_GID to *spawn* the build as the invoking
  │                  user with `sudo -H -u "#$SUDO_UID"` (`-H` so brew gets the
  │                  user's HOME, not /var/root); detect brew from
  │                  /opt/homebrew/bin/brew or /usr/local/bin/brew (NOT $PATH,
  │                  since sudo resets PATH). Root stays the parent throughout.
  ├── phase 1 (unprivileged child, as invoking user):
  │     ├── choose prefix (default /opt/local on both arches; warn if /usr/local
  │     │   has user-owned ancestors — see E1 below)
  │     ├── brew update (idempotent, avoids stale formulae)
  │     ├── brew install --cask macfuse  (only if /Library/Filesystems/macfuse.fs
  │     │   missing; then print System Settings approval + reboot instructions,
  │     │   wait for Enter, exit 0 so user reboots and re-runs)
  │     ├── brew install cmake autoconf automake libtool pkg-config mbedtls@3
  │     │   (build deps stay user-owned; only the final binaries need root)
    │     ├── clone + build dislocker @ pinned commit (see below):
    │     │     PKG_CONFIG_PATH="$(macfuse fuse3 pc dir)" \
    │     │     cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    │     │       -DCMAKE_INSTALL_PREFIX=$PREFIX -Dbindir=$PREFIX/sbin \
    │     │       -DCMAKE_C_FLAGS=-DFUSE_DARWIN_ENABLE_EXTENSIONS=0 \
    │     │       -DWITH_RUBY=OFF -DWITH_FUSE=ON
    │     │     cmake --build build        (staged in a temp build dir, NOT installed yet)
  │     ├── clone + build ntfs-3g @ pinned 2026.7.7 SHA (edge lineage, not master):
  │     │     ./autogen.sh
    │     │     PKG_CONFIG_PATH="$(macfuse fuse pc dir)" \
    │     │       ./configure --prefix=$PREFIX --exec-prefix=$PREFIX \
    │     │         --bindir=$PREFIX/sbin --sbindir=$PREFIX/sbin \
    │     │         --enable-mount-helper=no \
    │     │         --disable-ldconfig --disable-library  (link libntfs-3g static;
    │     │                                                   see "ntfs-3g" note)
  │     │     make                       (staged, not installed yet)
  │     └── stage full install set into a `mktemp -d` STAGEDIR (mode 0700),
  │         record the manifest (relative path + sha256 per file), and exit 0.
  │         The root parent regains control and reads STAGEDIR + manifest.
  └── phase 2 (root parent resumes — no re-auth): verify staging, install
        ├── re-verify every staged file's sha256 against the phase-1 manifest
        │   BEFORE copying (STAGEDIR is user-writable between phases; this
        │   closes the TOCTOU window). Abort if any hash differs.
        ├── snapshot pre-install listing of $PREFIX/sbin and $PREFIX/lib so
        │   failure cleanup is precise (records only files THIS run created).
        ├── cp -p each staged file into $PREFIX with install -o root -g wheel
        │   -m 0755 (dirs 0755). No symlinks are created for executables.
        ├── for dylibs under $PREFIX/lib that legitimately ship as versioned
        │   symlinks (libntfs-3g.88.dylib -> libntfs-3g.X.Y.Z.dylib): allow the
        │   symlink, but verify both the link and its ultimate target are
        │   root-owned, mode 0755, with root-owned ancestors (scoped dylib
        │   trust rule — executables get the strict no-symlink rule).
        ├── vendor libfuse: copy each libfuse the binaries link from
        │   /usr/local/lib into $PREFIX/lib root-owned, install_name_tool
        │   -change the binaries to the copy, ad-hoc re-sign (E1b).
        ├── otool -L every executable AND every non-system dylib under
        │   $PREFIX/lib recursively; assert every LC_LOAD_DYLIB target
        │   resolves to one of: $PREFIX/lib, /usr/lib, /System/Library, or the
        │   root-owned macFUSE subtree /Library/Filesystems/macfuse.fs. After
        │   vendoring nothing points at /usr/local/lib. Fail on any user-owned
        │   path (closes the transitive-dylib gap S1).
        ├── run PYTHONPATH=src python -c 'from dislocker_ui.deps import
        │   discover_privileged_deps; assert discover_privileged_deps().core_ok'
        ├── on any failure: remove only files recorded in THIS run's manifest
        │   under $PREFIX/sbin and $PREFIX/lib/libdislocker*/libntfs-3g*; never
        │   touch files the user installed by other means.
        └── print: OK + discovered paths, or FAIL + which attribute failed.
            Then "open dislocker-ui and click Recheck deps."
```

### Why from source over Homebrew + copy/symlink

- A bare `install -m 0755` copy of the Homebrew binary keeps its Mach-O
  `@rpath`/`@loader_path` load commands pointing at the user-owned Homebrew
  lib dir. dyld either fails (`dyld: Library not loaded`) or quietly depends
  on a path that `brew upgrade` can break. Copying the dependent `.dylib`s
  and rewriting load commands with `install_name_tool -add_rpath` is exactly
  what `cmake --prefix` and `make install` do for you.
- A symlink `/usr/local/sbin/dislocker-fuse -> /opt/homebrew/bin/dislocker-fuse`
  fails `_has_trusted_ancestors` (`deps.py:151-161`) because `/opt/homebrew` is
  user-owned. On Intel where Homebrew still lives under the root-managed
  `/usr/local`, the symlink may pass — but the binary itself is user-owned,
  so `_is_trusted_executable` rejects it on `info.st_uid != 0` (`deps.py:137`).
  Copying the binary alone fails the transitive dylib check above. From source
  is the only option that satisfies both the ancestor rule and the recursive
  dyld invariant uniformly.

### Pinned upstream sources

Verified against the upstreams on 2026-08-05; the dislocker tag/version split
is load-bearing — the two branches use incompatible TLS libraries and
different CMake flags, so the plan must pin exactly one.

- **dislocker — `master` branch** (not `v0.7.3`). `v0.7.3` calls
  `find_package(PolarSSL REQUIRED)` and links `osxfuse_i64`; `master` calls
  `find_package(MbedTLS 3 REQUIRED)`, exposes `-DWITH_RUBY=ON|OFF|AUTO`, treats
  `FUSE` via `pkg_check_modules(fuse3)`, and is the version the existing
  `gromgit/fuse/dislocker-mac` Homebrew formula and `mbedtls@3` README note
  already assume. Pin an explicit commit SHA rather than the mutable `master`
  ref; record the chosen SHA in `CHANGELOG.dev.md`. CMake minimum on master
  is 3.5 (verified in root `CMakeLists.txt`), so the `brew install cmake` step
  is sufficient — there is **no** CMake-3.30 requirement (assessment C2
  over-stated this; the master `cmake_minimum_required(VERSION 3.5)` is
  authoritative). Verify `git rev-parse HEAD` after clone and abort if it
  does not match the pinned SHA.
- **ntfs-3g — pinned to the `2026.7.7` release**
  (`d327833ec1d5eb1358b6f2c37139f10a3460944d`, edge lineage — **not**
  tuxera/ntfs-3g `master` tip). `master` tip lacks Darwin
  `getxattr`/`setxattr` `position` support that macFUSE fuse2 headers
  require; first real-hardware build failed with
  `-Wincompatible-function-pointer-types` (F13). `2026.7.7` matches
  Homebrew `ntfs-3g-mac` and includes the Darwin wrappers. Configure on
  `darwin*` **forces `with_fuse="external"`** and ignores any `--with-fuse=`
  value you pass (verified in `configure.ac`); the original plan's
  `--with-fuse=macfuse` was silently ignored and would have linked the wrong
  libfuse. The correct mechanism on macOS is `PKG_CONFIG_PATH` pointing at
  macFUSE's `fuse.pc` (verified: `pkg-config --list-all` shows both `fuse` and
  `fuse3` shipped by macFUSE). Pin the exact SHA in `CHANGELOG.dev.md`.

### dislocker CMake flags (verified against master `src/CMakeLists.txt`)

- `-DCMAKE_INSTALL_PREFIX=$PREFIX` — top-level install root.
- **Install RPATH for `libdislocker` needs no flag — dislocker's own
  `src/CMakeLists.txt` already handles it** (Q2, verified against master):

  ```cmake
  set (CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)
  list (FIND CMAKE_PLATFORM_IMPLICIT_LINK_DIRECTORIES "${libdir}" isSystemDir)
  if("${isSystemDir}" STREQUAL "-1")
     set (CMAKE_INSTALL_RPATH "${libdir}")
  endif()
  ```

  Because `CMAKE_INSTALL_RPATH` is set here with a plain (non-CACHE) `set()`,
  a `-DCMAKE_INSTALL_RPATH=` on the command line would be **overridden** by the
  project — so do **not** pass it; it is a silent no-op. dislocker installs
  `dislocker-fuse` with an rpath of its own `${libdir}` (= `$PREFIX/lib`) plus,
  via `USE_LINK_PATH TRUE`, the link dirs of external libs (including macFUSE's
  libfuse3 dir — see the FUSE note and E1b). The phase-2 recursive `otool`
  gate remains the backstop that fails the install if any resulting load
  command or rpath resolves to a user-owned path.
- `-Dbindir=$PREFIX/sbin` — **required**: master defaults `bindir` to
  `${CMAKE_INSTALL_PREFIX}/bin`, so without this `dislocker-fuse` lands in
  `$PREFIX/bin` and `deps.discover_privileged_deps()` (which only checks
  `$PREFIX/sbin`) never finds it. (`bindir` is a `set(... CACHE)` in the
  project, so `-D` on the cmake command line overrides it.)
- `-DWITH_RUBY=OFF` — **required**: master unconditionally calls
  `find_package(Ruby)` when `WITH_RUBY != OFF`. On a Mac with system Ruby
  installed, this links `libruby.dylib` from `/System/Library/...` and pulls
  an uncontrolled extra dyld dependency into the root-trusted binary. `OFF`
  short-circuits before `find_package(Ruby)`.
- `-DWITH_FUSE=ON` (default, but explicit for clarity). FUSE discovery on
  master is `pkg_check_modules (FUSE REQUIRED fuse3)` via the bundled
  `cmake/FindFUSE.cmake` (verified verbatim); so the flag that actually selects
  the right `libfuse3` is `PKG_CONFIG_PATH` exported to the cmake invocation,
  pointing at the directory containing macFUSE's `fuse3.pc`. Do **not** pass
  `-DFUSE_LIBRARY` / `-DFUSE_INCLUDE_DIR` — `FindFUSE.cmake` does not consult
  them.
- `-DCMAKE_C_FLAGS=-DFUSE_DARWIN_ENABLE_EXTENSIONS=0` — **required on macOS.**
  macFUSE ≥4.10 enables Darwin fuse3 API extensions by default
  (`fuse_darwin_attr` / `fuse_darwin_fill_dir_t` instead of `struct stat` /
  `fuse_fill_dir_t`). Dislocker uses the vanilla FUSE3 API; without this
  define AppleClang fails with `-Wincompatible-function-pointer-types` on
  `getattr`/`readdir` (verified 2026-08-05 against fuse3 3.18.2 / macFUSE
  5.3.3; same fix as nixpkgs and macFUSE#1064).
- **`fuse3.pc` location (Q1, resolved).** dislocker master **requires** the
  fuse3 API, which macFUSE has shipped only since **macFUSE 4.10.0**. Modern
  macFUSE installs the libfuse3 reference implementation to
  `/usr/local/lib` — `fuse3.pc` at `/usr/local/lib/pkgconfig/fuse3.pc`,
  headers at `/usr/local/include/fuse3/`, dylib at
  `/usr/local/lib/libfuse3.4.dylib` — **not** under
  `/Library/Filesystems/macfuse.fs/...` (that path holds the older fuse2 API).
  The script must therefore: (a) require macFUSE ≥ 4.10 and fail early with a
  clear message if `pkg-config --exists fuse3` is false; (b) resolve the dir
  at runtime via `pkg-config --variable=pcfiledir fuse3` (with
  `PKG_CONFIG_PATH=/usr/local/lib/pkgconfig` seeded) rather than hard-coding;
  (c) heed **E1b below** — on Intel Homebrew installs `/usr/local/lib` is
  user-owned, which the trust gate must not silently accept.

### ntfs-3g configure flags (verified against `configure.ac`)

- `--prefix=$PREFIX --exec-prefix=$PREFIX --bindir=$PREFIX/sbin
  --sbindir=$PREFIX/sbin` — **`--bindir` is required.** ntfs-3g and
  lowntfs-3g are `rootbin_PROGRAMS`; when `--exec-prefix` is set,
  `configure.ac` sets `rootbindir=$(bindir)` (default `$PREFIX/bin`).
  `deps.discover_privileged_deps` only accepts `…/sbin/ntfs-3g`, so without
  `--bindir=$PREFIX/sbin` the binary lands in `bin` and the post-install
  policy check fails (verified 2026-08-05, F14). `--sbindir` alone only
  covers `sbin_PROGRAMS` like mkntfs.
- `--enable-mount-helper=no` — intentional: dislocker-ui invokes ntfs-3g
  via argv, never via `/sbin/mount.ntfs-3g`. **Add an inline comment next to
  this flag in the script** explaining the rationale, so a future reader
  doesn't "fix" it (PR-review point 1).
- `--disable-ldconfig` — macOS has no ldconfig; avoids a no-op warning.
- `--disable-library` — link libntfs-3g statically into the `ntfs-3g`
  binary; avoids shipping versioned `libntfs-3g.*.dylib` symlinks into
  `$PREFIX/lib` and the scoped-symlink-trust work they would require. If a
  future caller needs the shared lib, drop this flag and add the scoped
  dylib trust rule to the link step.
- FUSE linkage: ntfs-3g wants the **fuse2** API
  (`PKG_CHECK_MODULES([FUSE_MODULE],[fuse >= 2.6.0])`, `fuse` not `fuse3`).
  Modern macFUSE (≥ 4.10) ships the libfuse2 reference implementation to
  `/usr/local/lib` as well (`/usr/local/lib/pkgconfig/fuse.pc`,
  `/usr/local/lib/libfuse.2.dylib`), so seed `PKG_CONFIG_PATH` with
  `/usr/local/lib/pkgconfig` and resolve via `pkg-config --variable=pcfiledir
  fuse`. Do **not** pass `--with-fuse=`; it is ignored on darwin. Same
  `/usr/local/lib` ownership caveat as dislocker applies (E1b).

### Prefix selection

Default `/opt/local` on **both** architectures (not `/usr/local` on Intel).
Rationale: on Intel Macs that have Homebrew installed under `/usr/local`,
`_has_trusted_ancestors` rejects any binary there because `/usr/local` (and
often `/usr/local/bin`) is user-owned. Defaulting Intel to `/usr/local` would
silently fail for the very users most likely to run the installer. `/opt/local`
is MacPorts-canonical, created root-owned by the system or trivially created
as such by the script, and already in `deps._PRIVILEGED_TOOL_CANDIDATES`.

Caveat: on a machine that *does* run MacPorts, `/opt/local` is MacPorts-owned,
and dropping `dislocker-fuse`/`ntfs-3g` into `/opt/local/sbin` mingles with its
tree (MacPorts won't know about these files, and a future `port` operation
could in principle touch the prefix). This is acceptable — the files are still
root-owned and satisfy the trust rule — but the script should note it in its
confirmation output so a MacPorts user can pick `--prefix /usr/local` instead
if its ancestors pass the root-owned check.

Override via `--prefix <dir>` or `PREFIX=<dir>`. Validate the result is one
of the two accepted dirs; refuse anything else. If the user explicitly passes
`/usr/local`, warn that it may have user-owned ancestors and proceed only if
`/usr/local/sbin` and its ancestors are root-owned (verify with the same
`_has_trusted_ancestors` rule the script will use to verify installed files);
otherwise fail with a message suggesting `/opt/local`.

The script prints the chosen prefix, the full list of files it will create,
and the upstream commit SHAs it will pin, then prompts for confirmation
unless `-y`.

### macFUSE step

**Corrected after real-hardware testing (F10).** macFUSE is a **kernel
extension (kext)**, *not* a DriverKit System Extension — even on current Apple
Silicon (verified against macFUSE 5.3.3). Consequences that invalidate the
earlier draft:

- `systemextensionsctl list` **never lists macFUSE** (it reported
  `0 extension(s)` on a machine with macFUSE fully installed). Using it as the
  detection gate falsely concludes "not installed" and sends the user to a
  Privacy & Security entry that does not exist. Do **not** use it.
- The kext is **not loaded at build time** — it loads on demand at the first
  FUSE mount, so `kextstat`/`kmutil showloaded` also show nothing during
  install. "Loadedness" is therefore the wrong gate for an *installer*.
- The Q3 bundle-id discussion is moot for detection: we do not match ids at
  all. (For reference, the id family is `io.macfuse.filesystems.macfuse[.NN]`,
  legacy `com.github.osxfuse.filesystems.osxfusefs`.)

For the installer's purpose we only need macFUSE's **libraries/headers** to
build against. So the gate is simply: **is macFUSE installed?** — the
`/Library/Filesystems/macfuse.fs` bundle exists OR `pkg-config --exists
fuse3`/`fuse` succeeds. If installed, proceed. If not, `brew install --cask
macfuse`, tell the user the kext is approved at first *mount* (on Apple Silicon
this can require enabling kernel extensions in Recovery + reboot — **not** a
Privacy & Security toggle), and exit 0 to re-run. Kext approval is a runtime
mount concern surfaced by macFUSE's own error, not something the installer can
or should verify.

## Ordered work packages

### 1. `scripts/install-root-deps.sh`

Affected files: new `scripts/install-root-deps.sh`.

- `#!/usr/bin/env bash`, `set -euo pipefail`, `umask 022` set at the top.
  Document at top: macOS only; designed to be run under `sudo`; all
  `brew`/build steps drop privileges back to the invoking user, only the
  install step runs as root; GPL note for the fetched projects.
- **Phase 0 (root orchestrator)**: preflight — refuse non-darwin (`uname`);
  resolve the invoking user via `$SUDO_UID`/`$SUDO_GID` (fail if not under
  `sudo`), detect brew from `/opt/homebrew/bin/brew` or `/usr/local/bin/brew`
  **by absolute path** (sudo resets `PATH`), warn if Xcode CLT missing
  (`xcode-select -p`). Root stays the parent for the whole run; it *spawns*
  the build phase as the invoking user and resumes for the install phase with
  no second authentication. Mark the script so the spawned phase knows which
  brew to call.
- **Phase 1 (unprivileged child)**: spawn the build body as the invoking user
  with `sudo -H -u "#$SUDO_UID" bash "$0" --phase1 …` — `-H` is **required**
  so Homebrew sees the user's `HOME` (and `$HOME/Library/Caches/Homebrew`) instead
  of `/var/root`, which would make brew error or pollute root's home. Carry
  the chosen prefix, pinned SHAs, and resolved brew path as args/env. In this
  phase:
  - `brew update` (idempotent; avoids stale autoconf/automake that breaks
    the dislocker build — PR-review point 3).
  - macFUSE: detect via `macfuse_installed` (bundle dir exists OR `pkg-config`
    resolves fuse — **not** kext loadedness; see the macFUSE step and F10). If
    not installed, `brew install --cask macfuse` unprivileged, explain that the
    kext is approved at first *mount*, `read -r` wait, exit 0 to re-run.
  - **fuse3 gate:** seed `PKG_CONFIG_PATH=/usr/local/lib/pkgconfig` and require
    `pkg-config --exists fuse3` (dislocker master needs fuse3). Do **not**
    require `/usr/local/lib` to be root-owned — libfuse there is frequently
    user-owned (even on Apple Silicon); phase 2 vendors it (E1b).
  - `brew install cmake autoconf automake libtool pkg-config mbedtls@3`
    (these stay user-owned; only the installed binaries need root).
  - dislocker: `git clone https://github.com/Aorimn/dislocker` at a pinned
    commit SHA (recorded in `CHANGELOG.dev.md`) to a temp build dir;
    `git rev-parse HEAD` must equal the pinned SHA or abort. Configure with
    the flags in "dislocker CMake flags" above, with `PKG_CONFIG_PATH`
    resolved to macFUSE's `fuse3.pc` directory. `cmake --build`. **Do not
    `cmake --install` here** — stage the install tree via
    `DESTDIR=STAGEDIR cmake --install build` into a temp `STAGEDIR` so
    phase 2 can atomically copy a known-good set. (Do **not** use
    `cmake --install --prefix STAGEDIR`: dislocker bakes absolute
    `bindir`/`libdir` into `install()` rules, so `--prefix` remapping
    still writes `$PREFIX/lib` as the unprivileged user — F12.)
  - ntfs-3g: `git clone https://github.com/tuxera/ntfs-3g` at a pinned SHA;
    `git rev-parse HEAD` check; `./autogen.sh`; configure with the flags in
    "ntfs-3g configure flags" above, `PKG_CONFIG_PATH` to macFUSE's `fuse.pc`
    directory. `make`. `make DESTDIR=STAGEDIR install` to stage.
  - Stage into a `mktemp -d` STAGEDIR created mode 0700 by the phase-1 child.
    Record a manifest of every file staged under `STAGEDIR` (relative paths
    + sha256); the root parent reads `STAGEDIR` and the manifest after the
    child exits 0.
- **Phase 2 (root parent resumes — no re-authentication)**: install step only:
  - **Re-verify** each staged file's sha256 against the phase-1 manifest
    before copying. `STAGEDIR` is user-writable and lives between the two
    phases, so a mismatch means tampering or a partial write — abort without
    installing anything. This closes the stage→install TOCTOU.
  - Snapshot the pre-install listing of `$PREFIX/sbin` and `$PREFIX/lib`
    (records what was there *before* this run, so failure cleanup removes
    only files this run added — PR-review point 6 / assessment E5).
  - Create `$PREFIX/sbin` and `$PREFIX/lib` with `install -d -o root -g
    wheel -m 0755` if missing. Verify each ancestor of `$PREFIX/sbin` is
    root-owned and non-writable (reuse the `_has_trusted_ancestors` rule);
    if any ancestor is user-owned, abort before writing.
  - Copy each staged file into place: `install -o root -g wheel -m 0755`
    for executables; `install -o root -g wheel -m 0644` for dylibs and
    pkgconfig files. **No symlinks for executables.**
  - **Vendor libfuse (E1b)**: for each installed binary, for every `libfuse*`
    dylib it links from outside the trusted set (macFUSE ships these in a
    frequently user-owned `/usr/local/lib`), copy the dylib root-owned into
    `$PREFIX/lib` (`install -o root -g wheel -m 0755`), fix its `LC_ID` with
    `install_name_tool -id`, `install_name_tool -change` the binary to the
    copy, drop any leftover `/usr/local/lib` rpath, and **ad-hoc re-sign**
    (`codesign -f -s -`) both the copy and the binary (install_name_tool
    invalidates the signature; arm64 requires a valid one). The copies keep
    their `MFMount.framework` reference under the root-owned
    `/Library/Filesystems/macfuse.fs`.
  - **Recursive otool verification** (closes S1): for every executable in
    `$PREFIX/sbin` AND every non-system `.dylib` under `$PREFIX/lib` (including
    the vendored libfuse), run `otool -L` and walk each `LC_LOAD_DYLIB` target
    up to a fixed depth. After vendoring, every resolved path must be one of:
    under `$PREFIX/lib`, `/usr/lib`, `/System/Library`, or the root-owned
    macFUSE subtree `/Library/Filesystems/macfuse.fs` (verified with
    `_has_trusted_ancestors`). No installed binary may retain a load command or
    rpath into `/usr/local/lib`. Fail if any load command points at a
    user-owned path (e.g. `/opt/homebrew/lib`).
    This is the load-bearing gate the assessment flagged: `_is_trusted_executable`
    only inspects the executable's own path, so the installer's own check is
    the only thing preventing a root-owned binary from loading user-mutable
    code at runtime.
  - **Privileged-policy functional check**: run
    `PYTHONPATH=src python3 -c 'from dislocker_ui.deps import discover_privileged_deps; d=discover_privileged_deps(); assert d.core_ok, d.missing_core(); print(d)'`
    from the repo root. `core_ok` exercises `_is_trusted_executable` and
    `_has_trusted_ancestors` on the installed paths.
  - On any failure: remove only files recorded in THIS run's manifest under
    `$PREFIX/sbin` and `$PREFIX/lib/libdislocker*` / `libntfs-3g*`. Best-effort;
    never remove a path that existed before this run (use the phase-2
    pre-listing). Print a clear FAIL with which tool/attribute failed and
    the cleanup performed.
  - On success: print `OK`, the discovered paths, and the next step:
    `./run.sh` → click **Recheck deps** in dislocker-ui.

Acceptance criteria:

- `sudo scripts/install-root-deps.sh` end-to-end, on a clean macFUSE+Homebrew
  machine (Apple Silicon or Intel), ends with
  `discover_privileged_deps().core_ok == True` and the recursive `otool -L`
  check reporting zero user-owned dyld targets — including after libfuse has
  been vendored into `$PREFIX/lib` and the binaries re-signed.
- The vendored `$PREFIX/lib/libfuse*.dylib` are root-owned, and no installed
  binary retains a load command or rpath into `/usr/local/lib`.
- Re-running it is a no-op: macFUSE loadability check passes, dislocker and
  ntfs-3g binaries already satisfy `discover_privileged_deps()`, phase 1
  and the install step are skipped, exit 0.
- An interrupted run leaves no file under `$PREFIX/sbin` or `$PREFIX/lib`
  that would pass `_is_trusted_executable` but fail the recursive otool
  gate at runtime (i.e. every root-owned binary's complete dyld closure
  is also root-managed).
- `deps._PRIVILEGED_TOOL_CANDIDATES` is unchanged; no new accepted prefix.

### 2. GUI hint

Affected files: `src/dislocker_ui/gui.py`.

- In the "Privileged tools unavailable" branch (`src/dislocker_ui/gui.py:243`),
  after the existing "See README." text, append the script path:
  `"\n\nOne-time install: sudo scripts/install-root-deps.sh (from the repo root)."`
- Do not wire a button that runs it (the GUI is unprivileged and must not
  silently trigger sudo/brew/cask installs); the dialog stays a pointer to
  the script, not an executor.
- Keep the existing message copy describing the accepted sbin dirs so users
  who prefer a manual install still get the constraint.

Acceptance criteria:

- Triggering the missing-tools dialog mentions the script path verbatim.
- No new elevation, subprocess, or install side effect is added to the GUI.

### 3. README subsection

Affected files: `README.md`.

- Add a "One-time root install (optional helper)" subsection under
  Installation (after the existing Homebrew/manual steps, before "This app")
  that:
  - names `scripts/install-root-deps.sh`,
  - states what it builds and why from source (one sentence on the dylib
    / trust-model reason),
  - notes the interactive macFUSE approval + reboot,
  - **states arch support plainly (E1b): works on both Apple Silicon and
    Intel** because the installer vendors macFUSE's libfuse root-owned into the
    prefix (macFUSE's `/usr/local/lib` is frequently user-owned on both). One
    sentence that kext approval happens at first mount, not in Privacy &
    Security (F10).
  - points back to the manual Homebrew+tap path as the alternative.
- Reword the sentence introducing the Homebrew+tap path so it is unambiguous
  that it satisfies only the GUI preflight, not the elevated flow
  (PR-review point 5). Suggested exact wording:

  > The Homebrew build below satisfies the **GUI preflight check** only; the
  > elevated mount flow requires root-owned binaries. Run
  > `scripts/install-root-deps.sh` once to satisfy both.

- Do not delete the existing `brew tap gromgit/homebrew-fuse` instructions;
  they remain a valid user-pref build for the advisory check.

Acceptance criteria:

- A new user reading Installation in order sees: Homebrew → FUSE backend →
  dislocker + ntfs-3g (manual Homebrew, GUI advisory) → one-time root
  installer (for elevation) → this app.
- The Homebrew+tap block is clearly labeled as GUI-preflight-only; no reader
  is left thinking it is sufficient for mounts.

### 4. Tests, docs, tooling integration

Affected files: `tests/` (light), `CHANGELOG.md`, `CHANGELOG.dev.md`, `VERSION`.

- No unit test of the installer itself (it runs as root on macOS; the project
  defers root- and macFUSE-dependent paths to manual matrix per the existing
  hardening plan).
- Add a tiny `tests/test_install_script_static.py` that grep-style asserts:
  - the script exists, is executable-mode in the repo, starts with
    `#!/usr/bin/env bash`,
  - sets `umask 022`,
  - contains both accepted prefixes (`/usr/local/sbin`, `/opt/local/sbin`) as
    literals without referencing a machine-specific home path,
  - contains a non-Darwin guard (`uname` / `darwin`) that exits non-zero, so
    the Linux CI runner exercises the rejection branch without macOS
    (PR-review point 4),
  - pins dislocker and ntfs-3g commit SHAs (literal 40-hex-SHA patterns) and
    fails the build on `git rev-parse HEAD` mismatch,
  - no **non-comment** line contains `--with-fuse=macfuse`, `-DFUSE_LIBRARY=`,
    or `--with-fuse=internal` (regression guard against the fatal flags in the
    assessment). Strip `#`-comment lines before this assertion: the script is
    required to carry inline comments explaining *why* those flags are wrong,
    and a naive substring grep would false-fail on its own documentation.
  - contains `-Dbindir=` and `-DWITH_RUBY=OFF` literals (dislocker handles its
    own install rpath, so there is deliberately no `-DCMAKE_INSTALL_RPATH=`;
    see Q2), plus `-DFUSE_DARWIN_ENABLE_EXTENSIONS=0` (macFUSE ≥4.10 Darwin
    fuse3 extensions break vanilla clients — F11).
  - references `/usr/local/lib/pkgconfig` (the macFUSE ≥4.10 fuse/fuse3 `.pc`
    dir) and gates on `pkg-config --exists fuse3` for the macFUSE-version
    check (Q1).
  - asserts the vendoring path is present (E1b): `vendor_foreign_dylibs`,
    `install_name_tool`, and `codesign -f -s -` literals, plus
    `macfuse_installed` (kext-not-sysext detection, F10).
- `hooks/check_absolute_paths.py` and `.absolute-paths-allowlist` review: the
  script legitimately references `/usr/local/sbin`, `/opt/local/sbin`,
  `/usr/local/lib` (macFUSE libfuse/pkgconfig), `/Library/Filesystems/macfuse.fs`,
  and `/usr/bin/hdiutil`-style system paths. Add only those literals to the
  allowlist if needed; do not suppress globally.
- Bump `VERSION` patch (e.g. 0.3.1) and add a `CHANGELOG.md` entry under a
  new Added/Changed section: "Optional root-managed install script
  (`scripts/install-root-deps.sh`) that builds dislocker-fuse and ntfs-3g
  from source into `/opt/local/sbin` (or `/usr/local/sbin`) so the elevated
  mount flow trusts them. GUI 'Privileged tools unavailable' dialog now
  points at it." `CHANGELOG.dev.md` records the harness (allowlist tweak,
  static script test) AND the pinned dislocker + ntfs-3g commit SHAs.

Acceptance criteria:

- `ruff check src tests hooks`, `ruff format --check src tests hooks`,
  `python3 -m compileall -q src`, `python3 hooks/check_absolute_paths.py`,
  `python3 hooks/check_file_size.py`, and `pytest --cov` all pass.
- The new static test passes on the Linux CI runner (no macOS required).
- `VERSION` and both changelogs updated.

## Validation gates

- `bash -n scripts/install-root-deps.sh` (syntax-only, in CI).
- The new `tests/test_install_script_static.py` runs on Linux CI and exercises
  the non-Darwin rejection branch + all grep-style assertions in work
  package 4.
- `python3 -m compileall -q src`
- `ruff check src tests hooks`
- `ruff format --check src tests hooks`
- `python3 hooks/check_absolute_paths.py`
- `python3 hooks/check_file_size.py` — `.sh` is in `SOURCE_EXTS`, so the
  script is checked: 600 lines is a soft **warning**, 750 lines is a hard
  **failure** (assessment E6). Keep the entry script under 600 if practical;
  if it approaches 750, factor the phase-1 build steps into a sourced
  `scripts/lib/root-deps-build.sh` so neither file crosses the hard cap.
- `pytest --cov --cov-report=term-missing` (incl. the new static test)
- `pip-audit`
- Manual macOS matrix (separate from CI, documented in README): clean
  macFUSE+Homebrew install on **Apple Silicon** and **Intel** (both full
  success via libfuse vendoring); **first mount actually works** with the
  vendored + re-signed libfuse (the E1b residual-risk check — top priority);
  re-run idempotency; not-installed-macFUSE branch; wrong-prefix rejection
  (`--prefix /random`); recursive `otool -L` confirming no user-owned dyld
  targets after a real install.

## Resolved questions and decisions

The three questions raised in review were investigated against upstream
sources on 2026-08-05 and are **resolved** below. Investigation also surfaced a
**new design issue (E1b)** — macFUSE ships libfuse into a user-owned
`/usr/local/lib` **even on Apple Silicon** (proven on real hardware) — resolved
by **vendoring libfuse root-owned into `$PREFIX/lib`** (see E1b). A separate
real-hardware bug in macFUSE detection (it's a kext, not a System Extension)
was also fixed (F10).

- **Q1 — Does macFUSE ship `fuse3.pc`? — RESOLVED (yes, with caveats).**
  dislocker `master` `cmake/FindFUSE.cmake` is verbatim
  `pkg_check_modules (FUSE REQUIRED fuse3)`, so fuse3 is mandatory. macFUSE has
  shipped the libfuse3 reference implementation **since macFUSE 4.10.0**,
  installing `fuse3.pc`, headers, and `libfuse3.4.dylib` under **`/usr/local/lib`**
  (not under `/Library/Filesystems/macfuse.fs/...`, which holds the older
  fuse2 API). Actions folded into the plan: require macFUSE ≥ 4.10, gate on
  `pkg-config --exists fuse3`, seed `PKG_CONFIG_PATH=/usr/local/lib/pkgconfig`,
  and fail cleanly to the manual path if fuse3 is absent (never drop to fuse2).
  Still worth a matrix spot-check on the actual installed macFUSE version.
- **Q2 — Is dislocker's `libdislocker` runtime rpath handled? — RESOLVED (yes,
  by dislocker itself).** master `src/CMakeLists.txt` already sets
  `CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE` and `CMAKE_INSTALL_RPATH` to its own
  `${libdir}` when that isn't a system dir (quoted in the CMake-flags section).
  So no `-DCMAKE_INSTALL_RPATH` flag is needed — passing one is a silent no-op
  because the project's plain `set()` overrides the cache value. The earlier F2
  flag was dropped. Confirm on a real build with `otool -l dislocker-fuse` that
  `libdislocker` resolves under `$PREFIX/lib`; the phase-2 otool gate is the
  loud backstop if not.
- **Q3 — The macFUSE extension identifier? — RESOLVED.** Modern id is
  `io.macfuse.filesystems.macfuse` (team `3T5GSNBU6W`), with suffixed kext
  variants (`...macfuse.NN`) since 4.7.1 and legacy
  `com.github.osxfuse.filesystems.osxfusefs`. Match a case-insensitive
  `macfuse` substring in `systemextensionsctl list` / `kextstat` output; the
  draft's `com.apple.george.macfuse` was bogus and is removed.

### E1b — DECISION REVERSED after real-hardware testing: vendor libfuse into `$PREFIX/lib`

Resolving Q1 exposed that modern macFUSE puts **libfuse2 and libfuse3 in
`/usr/local/lib`**, which both binaries link. The first decision was to
**refuse** when that dir is user-owned, on the assumption that Apple Silicon
always has a root-owned `/usr/local/lib`. **Real-hardware testing (2026-08-05,
macFUSE 5.3.3 on Apple Silicon) proved that assumption false:**

```
/usr/local            root:wheel  0755
/usr/local/lib        kevingrizzard:staff  0755   ← USER-OWNED, on Apple Silicon
/usr/local/lib/libfuse.2.dylib     kevingrizzard:staff
/usr/local/lib/libfuse3.4.dylib    kevingrizzard:staff
```

So the "refuse" design would have **refused on a stock Apple Silicon machine**,
directly contradicting the "must work on Apple Silicon" requirement. Deeper
inspection also showed the trust hole is *narrow*: the entire
`/Library/Filesystems/macfuse.fs` subtree — including `MFMount.framework`,
which libfuse depends on — is fully **root-owned**. The **only** user-owned hop
in the whole dyld closure is `/usr/local/lib/libfuse{.2,3.4}.dylib` itself.

**Decision: vendor libfuse.** In phase 2 the installer copies each libfuse
dylib the binaries link from outside the trusted set into `$PREFIX/lib`
(root-owned, 0755), repoints the binaries with `install_name_tool -change`,
and **ad-hoc re-signs** (`codesign -f -s -`) both the copies and the binaries
(install_name_tool invalidates the Mach-O signature; arm64 requires a valid
one — this is the same relocation dance Homebrew does). The copies keep their
`MFMount.framework` reference under the root-owned `/Library/Filesystems`. The
recursive `otool` gate then confirms the closure resolves only to `$PREFIX/lib`,
`/usr/lib`, `/System/Library`, or the root-owned macFUSE subtree.

Why this over the alternatives:

- **vs. refuse:** refuse fails on real Apple Silicon machines — unacceptable
  given the requirement.
- **vs. `chown /usr/local/lib` to root:** simpler but modifies files macFUSE
  owns *outside* `$PREFIX`, makes `/usr/local/lib` unwritable for the user, and
  reverts on macFUSE upgrades. Vendoring is self-contained and touches nothing
  outside `$PREFIX`.

**Residual risk (only a real mount confirms):** macFUSE is assumed to accept a
*re-signed* libfuse (it authenticates via the `/dev/macfuse` node and the
setuid `load_macfuse` helper, not the client's code signature). If that
assumption were wrong, the phase-2 otool gate + `discover_privileged_deps()`
check still pass (they don't mount), so the failure would surface at first
mount, not silently — and the fallback is the `chown` approach. Track as the
top manual-matrix item.

**Caveat:** vendored libfuse is a frozen copy; after a macFUSE **upgrade**,
re-run the installer so the vendored copy (and the rebuild) track the new kext.

## Rollout and rollback

The installer is opt-in and additive: no existing install path changes, no
code path executes it automatically. Rollout is simply the new file + docs +
the pinned SHAs recorded in `CHANGELOG.dev.md`.

Rollback is removal of `scripts/install-root-deps.sh`, the GUI line, the
README subsection, the static test, the allowlist tweak, and the changelog
entries. No session state, privileged helper, or `deps.py` behavior depends
on the script existing — it only has to *satisfy* the existing policy.

If a from-source build proves infeasible on a given macOS version (e.g.
dislocker CMake cannot locate the macFUSE framework headers via
`PKG_CONFIG_PATH`), the script exits non-zero with a message pointing at the
manual Homebrew + tap path in the README. Do **not** fall back to copying a
user-owned binary into the root prefix; that would silently weaken the trust
model and reintroduce the transitive-dylib gap (S1). Do **not** fall back to
`--with-fuse=macfuse` or `--with-fuse=internal`; both are no-ops or wrong on
macOS per the verified `configure.ac`.

## Completion criteria

- **Both architectures (via libfuse vendoring, E1b):** a user who can authorize
  admin can, on a clean Mac with macFUSE installed, run
  `sudo scripts/install-root-deps.sh` once and end with
  `discover_privileged_deps().core_ok` true AND the recursive `otool -L` check
  reporting zero user-owned dyld targets, and a working Mount. This holds
  regardless of who owns `/usr/local/lib`, because phase 2 vendors libfuse
  root-owned into `$PREFIX/lib`. (Real-hardware note: even Apple Silicon had a
  user-owned `/usr/local/lib`, so vendoring — not an ownership assumption — is
  what makes both arches work.)
- The only remaining first-run interruption is macFUSE itself not yet
  installed/approved (kext approval happens at first mount).
- Re-running the installer is a no-op.
- The GUI dialog names the script; the README documents both paths and
  labels the Homebrew+tap block as GUI-preflight-only.
- `deps.py` trust enforcement is unchanged; no new accepted location.
- `VERSION`, `CHANGELOG.md`, and `CHANGELOG.dev.md` reflect the addition,
  including the pinned upstream commit SHAs.

## Review history

Two external reviews were filed under `tmp/` and are addressed below, followed
by a third in-repo follow-up pass. The upstream claims were re-verified on
2026-08-05 against the live `Aorimn/dislocker` and `tuxera/ntfs-3g` sources
before this rewrite.

### PR review (`tmp/2026-08-05T19-30-00Z-pr-review-root-deps-install.md`) — approve with minor notes

| # | Note | Resolution |
|---|------|------------|
| 1 | `--enable-mount-helper=no` needs an inline justification in the script | Work package 1 now requires an inline comment next to the flag. |
| 2 | `install -o root -g wheel` runs under sudo — note it explicitly | Work package 1 documents the two-phase model and that phase 2 runs as root. |
| 3 | `brew update` before installing build deps | Added to phase 1. |
| 4 | Pin tags for both upstreams | Pin commit SHAs (stricter than tags), verify `git rev-parse HEAD`, record in `CHANGELOG.dev.md` and static test. |
| 5 | README parity sentence for GUI-preflight vs elevation | Work package 3 now specifies the exact wording. |
| 6 | No test for macOS-only rejection | Static test now asserts a non-Darwin guard that exits non-zero. |
| 6b | "Best-effort cleanup" is vague | Defined: phase 2 snapshots the pre-install listing and removes only files in THIS run's manifest; never touches pre-existing files. |

### Plan assessment (`tmp/2026-08-05T21-00-00Z-plan-assessment-root-deps-install.md`) — needs revisions

| ID | Severity | Resolution |
|----|----------|------------|
| C1 | Fatal — dislocker installs to `bin` not `sbin` | Verified in master `src/CMakeLists.txt` (`set(bindir "${CMAKE_INSTALL_PREFIX}/bin")`). Added `-Dbindir=$PREFIX/sbin`. |
| C2 | Fatal — mbedtls version vs pinned tag mismatch | Verified: `v0.7.3` uses `find_package(PolarSSL)`, `master` uses `find_package(MbedTLS 3)`. Chose `master` + `mbedtls@3` (matches existing README). Note: master's `cmake_minimum_required(VERSION 3.5)`, so there is **no** CMake-3.30 requirement; the assessment over-stated this. |
| C3 | Fatal — wrong FUSE flags | Verified: master `cmake/FindFUSE.cmake` is just `pkg_check_modules(FUSE REQUIRED fuse3)`. `-DFUSE_LIBRARY`/`-DFUSE_INCLUDE_DIR` are not consulted. Replaced with `PKG_CONFIG_PATH` to macFUSE's `fuse3.pc`. |
| C4 | Fatal — `--with-fuse=macfuse` invalid | Verified in ntfs-3g `configure.ac`: on `darwin*` `with_fuse` is forced to `external`; the `--with-fuse=` flag is silently ignored. Replaced with `PKG_CONFIG_PATH` to macFUSE's `fuse.pc`. |
| C5 | Fatal — brew refuses to run as root | Restructured into two phases: unprivileged brew+build, elevated install only. |
| S1 | High — transitive dylib trust gap | Added recursive `otool -L` verification in phase 2: every non-system dylib under `$PREFIX/lib` and every load command of every executable must resolve to `$PREFIX/lib`, `/usr/lib`, `/System/Library`, or the root-owned macFUSE lib dir (see F1). |
| S2 | Medium — link-helper can't work as written | Dropped the bare `libfuse.2.dylib` copy; rely on `PKG_CONFIG_PATH` + cmake/autotools `--prefix` to produce correct install names natively. |
| S3 | Medium — tag pinning weak | Pin commit SHAs, not tags; `git rev-parse HEAD` check before build. |
| E1 | Robustness — Intel `/usr/local` ownership collision | Default `/opt/local` on **both** architectures. `/usr/local` only if explicitly requested AND its ancestors pass the root-owned check. |
| E2 | Robustness — no-symlink self-failure | Scope the no-symlink rule to executables. Dylibs (only when `--disable-library` is NOT used) get a scoped symlink-aware rule: link + target both root-owned, mode 0755, ancestors root-owned. Default `--disable-library` so the symlinks are not produced in the first place. |
| E3 | Robustness — invalid bash mode-check syntax `( 0755 & ~0o022 )` | Removed; concrete `install -o root -g wheel -m 0755` / `-m 0644` calls used instead. `umask 022` set at the top. |
| E4 | Robustness — macFUSE dir exists before approval | macFUSE gate now checks loadability (`kextstat`/`systemextensionsctl`), not just the directory. |
| E5 | Robustness — re-run idempotency via recursive trust+otool | Re-run short-circuits on `discover_privileged_deps().core_ok` (which re-runs `_is_trusted_executable`) AND the recursive otool check, not `-f`. |
| E6 | Robustness — file-size soft cap | Validation gate notes the 600-line cap and a factoring path into `scripts/lib/`. |
| M2 | Missing — dislocker pulls system Ruby | `-DWITH_RUBY=OFF` (verified the option exists on master `src/CMakeLists.txt`). |
| M3 | Missing — half-installed state under-specified | Stage into `STAGEDIR`, snapshot pre-install listing, manifest-based cleanup. |

### Follow-up review (2026-08-05, this pass)

A third pass caught gaps left by the two reviews above; all are resolved in the
sections referenced.

| ID | Severity | Resolution |
|----|----------|------------|
| F1 | High — otool allow-list omitted macFUSE's own lib dir, and named the wrong dir | The built binaries link libfuse from macFUSE, which is neither `$PREFIX/lib` nor `/usr/lib`/`/System`, and the prior text mislabeled it as "copied into `$PREFIX/lib`". Upstream check (Q1) corrected the location: macFUSE ≥4.10 puts libfuse2/libfuse3 in **`/usr/local/lib`**, not `/Library/Filesystems/macfuse.fs/...`. Added `/usr/local/lib` as an explicit allowed `otool` target, gated by a root-owned + root-owned-ancestors check — which surfaced **E1b** (fails on Intel+Homebrew). |
| F2 | ~~High — dislocker rpath uncontrolled~~ → **not a defect** | Investigation (Q2) found dislocker's own `src/CMakeLists.txt` sets `CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE` + `CMAKE_INSTALL_RPATH=${libdir}`. No flag needed; the earlier `-DCMAKE_INSTALL_RPATH` add was dropped (it's a silent no-op against the project's `set()`). |
| F3 | High — "drop to user then re-elevate" needs a second password | Restructured to a single root orchestrator that spawns the build as the user and resumes as root — one authentication. Dropped the `sudo -k` re-elevate. |
| F4 | Medium — brew spawned without user `HOME` | Spawn phase 1 with `sudo -H -u "#$SUDO_UID"` so Homebrew uses the user's HOME/caches, not `/var/root`. |
| F5 | Medium — stage→install TOCTOU on user-writable STAGEDIR | `mktemp -d` STAGEDIR mode 0700; phase 2 re-verifies each file's sha256 against the manifest before copying. |
| F6 | Low — brittle negative-grep static test | Strip `#`-comment lines before asserting the script omits the fatal flags, so the script's own explanatory comments don't false-fail. |
| F7 | Low — file-size note understated the failing gate | Clarified: 600 lines warns, 750 lines fails; `.sh` is in `SOURCE_EXTS`. |
| F8 | Low → moot for detection | The macFUSE bundle-id discussion (`io.macfuse.filesystems.macfuse` etc.) is no longer used: detection does not match ids (see F10). `com.apple.george.macfuse` was bogus regardless. |
| F9 | Nit — stale line refs | `gui.py` dialog is `245-251`; `st_uid` executable check is `deps.py:137`. |
| F10 | **High — real-hardware bug** — macFUSE detection used the wrong subsystem | Verified on macFUSE 5.3.3 (Apple Silicon): macFUSE is a **kext**, so `systemextensionsctl list` shows `0 extension(s)` and the kext isn't loaded at build time — the check falsely reported "not installed" and sent the user to a non-existent Privacy & Security entry. Replaced with `macfuse_installed()` (bundle dir exists OR `pkg-config` resolves fuse); kext approval is a first-*mount* concern, not an installer gate. |
| E1b | **High — NEW, decision reversed** — macFUSE libfuse lives in user-owned `/usr/local/lib` **even on Apple Silicon** | Surfaced resolving Q1; the first "refuse if not root-owned" decision was **falsified on real Apple Silicon hardware** (`/usr/local/lib` was `kevingrizzard:staff`), where it would have refused a stock machine. **Resolved by vendoring:** phase 2 copies libfuse root-owned into `$PREFIX/lib`, repoints binaries with `install_name_tool`, and ad-hoc re-signs; the only user-owned hop (libfuse) becomes root-managed while the root-owned macFUSE subtree (MFMount) stays put. Residual risk (re-signed libfuse acceptance) is a first-mount concern, not silent. |
| F11 | **High — real-hardware** — first `sudo scripts/install-root-deps.sh` failed compiling `dislocker-fuse.c` (`fuse_darwin_attr` vs `struct stat`) | macFUSE ≥4.10 enables Darwin fuse3 extensions by default. Added `-DCMAKE_C_FLAGS=-DFUSE_DARWIN_ENABLE_EXTENSIONS=0` (macFUSE#1064 / nixpkgs). Static test asserts the flag. |
| F12 | **High — real-hardware** — stage-install tried to create `/opt/local/lib` as the unprivileged user | dislocker `install(DESTINATION "${libdir}")` uses absolute paths baked at configure time; `cmake --install --prefix` does not remap them. Switched to `DESTDIR=$STAGEDIR cmake --install` (ntfs-3g already used DESTDIR; dislocker symlink `install(CODE)` already references `$ENV{DESTDIR}`). Static test asserts DESTDIR and rejects the `--prefix $stagedir$PREFIX` form. |
| F13 | **High — real-hardware** — ntfs-3g master tip failed on Darwin fuse2 xattr (`getxattr`/`setxattr` need `uint32_t position`) | macFUSE fuse2 always uses the Darwin position parameter (no `FUSE_DARWIN_ENABLE_EXTENSIONS` escape for fuse2). Bumped pin from master tip `5d4a3f6…` to release `2026.7.7` / `d327833…` (edge lineage; same as Homebrew ntfs-3g-mac). |
| F14 | **High — real-hardware** — post-install `discover_privileged_deps().core_ok` false, missing `ntfs-3g` | ntfs-3g is a `rootbin_PROGRAM` → `$(bindir)` when `--exec-prefix` is set; `--sbindir` alone left it in `$PREFIX/bin` while deps only accepts `…/sbin/ntfs-3g`. Added `--bindir=$PREFIX/sbin`. Static test asserts the flag. |
| F15 | **High — real-hardware** — mount failed: `dyld: Library not loaded: @rpath/libdislocker.0.7.dylib` | phase2 `find -type f` skipped cmake's `libdislocker.0.7.dylib` symlink; only `.0.7.3.dylib` was installed. `verify_dyld_closure` also skipped `@rpath` and never checked `LC_RPATH` (Homebrew mbedtls@3 remained). Added `install_staged_lib_symlinks`, `sanitize_rpaths`, and tightened the otool gate. |
