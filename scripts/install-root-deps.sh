#!/usr/bin/env bash
#
# install-root-deps.sh — one-time, opt-in installer for the privileged
# dislocker-ui toolchain.
#
# What it does:
#   Builds dislocker-fuse and ntfs-3g FROM SOURCE and installs them
#   root-owned, mode-restricted, into a root-managed sbin directory
#   (/opt/local/sbin by default, /usr/local/sbin if explicitly requested and
#   root-owned) so the elevated mount flow's trust policy
#   (dislocker_ui.deps.discover_privileged_deps) accepts them.
#
# Why from source (not a Homebrew copy/symlink):
#   A copied Homebrew binary keeps @rpath/@loader_path load commands pointing
#   at the user-owned Homebrew prefix; a root-trusted binary must not load
#   user-mutable dylibs. A --prefix build colocates the binaries with a
#   root-managed dyld closure. See plans/archive/2026-08-05-root-deps-install.md.
#
# Privilege model:
#   macOS only. Invoked as `sudo scripts/install-root-deps.sh`. The process
#   STAYS root (single authentication) and spawns the brew+build work as the
#   invoking user with `sudo -H -u` (Homebrew refuses to run as root). Only the
#   final install into $PREFIX runs as root. It never re-elevates, so there is
#   no second password prompt.
#
# Licensing:
#   This repo is GPL-3.0-or-later. This script fetches and builds dislocker
#   (GPL-2.0-or-later) and ntfs-3g (GPL-2.0+) under their own terms; it does
#   not vendor their sources.
#
set -euo pipefail
umask 022

# --- Pinned upstream sources ------------------------------------------------
# Pin exact commit SHAs (recorded in CHANGELOG.dev.md). dislocker MUST be a
# master-branch commit: master uses find_package(MbedTLS 3) and fuse3, whereas
# v0.7.3 uses PolarSSL and osxfuse_i64. Replace the placeholders below with the
# verified SHAs before the first real run; the build aborts if HEAD != pin.
readonly DISLOCKER_REPO="https://github.com/Aorimn/dislocker"
readonly DISLOCKER_SHA="38dab03175cb5798d625375154e716665201bae1"
readonly NTFS3G_REPO="https://github.com/tuxera/ntfs-3g"
# 2026.7.7 release (edge lineage). Do NOT pin tuxera/ntfs-3g master tip:
# master lacks Darwin getxattr/setxattr `position` support that macFUSE's
# fuse2 headers require (AppleClang -Wincompatible-function-pointer-types).
# 2026.7.7 matches Homebrew ntfs-3g-mac and includes that Darwin API.
readonly NTFS3G_SHA="d327833ec1d5eb1358b6f2c37139f10a3460944d"

# --- Accepted install prefixes ----------------------------------------------
# Must stay in lockstep with deps._PRIVILEGED_TOOL_CANDIDATES, which accepts
# binaries only from /opt/local/sbin or /usr/local/sbin. No other prefix is
# accepted, and no "trust this path" override exists by design.
readonly PREFIX_DEFAULT="/opt/local"

# Minimum macFUSE that ships the libfuse3 reference implementation + fuse3.pc.
readonly MACFUSE_MIN="4.10"

log()  { printf '%s\n' "$*"; }
info() { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo scripts/install-root-deps.sh [--prefix /opt/local|/usr/local] [-y]

  --prefix DIR   Install prefix; must be /opt/local (default) or /usr/local.
                 /usr/local is accepted only if it and its ancestors are
                 root-owned (Homebrew on Intel makes /usr/local user-owned).
  -y             Do not prompt for confirmation.
  -h, --help     Show this help.

macOS only. Run under sudo. See the README "One-time root install" section.
EOF
}

# ===========================================================================
# Shared helpers (used in more than one phase)
# ===========================================================================

# Portable macOS ownership/mode probes via stat(1).
owner_uid() { stat -f '%u' "$1"; }
mode_octal() { stat -f '%Lp' "$1"; }

# True when DIR and every ancestor up to / are root-owned, real dirs, not
# symlinks, and not group/other-writable. Mirrors deps._has_trusted_ancestors.
has_trusted_ancestors() {
  local path="$1" parent
  parent="$path"
  while :; do
    parent="$(dirname "$parent")"
    [ -L "$parent" ] && return 1
    [ -d "$parent" ] || return 1
    [ "$(owner_uid "$parent")" = "0" ] || return 1
    # Reject group-write (020) or other-write (002).
    local m; m="$(mode_octal "$parent")"
    (( (8#$m & 8#0022) == 0 )) || return 1
    [ "$parent" = "/" ] && break
  done
  return 0
}

# True when DIR itself is a root-owned, non-writable real directory AND all its
# ancestors are trusted.
dir_is_root_managed() {
  local dir="$1"
  [ -d "$dir" ] || return 1
  [ -L "$dir" ] && return 1
  [ "$(owner_uid "$dir")" = "0" ] || return 1
  local m; m="$(mode_octal "$dir")"
  (( (8#$m & 8#0022) == 0 )) || return 1
  has_trusted_ancestors "$dir"
}

# ===========================================================================
# Phase 0 — root orchestrator (default entry point)
# ===========================================================================

phase0() {
  # --help works without sudo or macOS.
  for a in "$@"; do
    case "$a" in -h|--help) usage; exit 0 ;; esac
  done

  [ "$(uname -s)" = "Darwin" ] || die "macOS only; refusing to run on $(uname -s)."
  [ "$(id -u)" = "0" ] || die "Run under sudo: sudo scripts/install-root-deps.sh"
  if [ -z "${SUDO_UID:-}" ] || [ -z "${SUDO_USER:-}" ]; then
    die "Must be invoked via sudo (SUDO_UID/SUDO_USER unset)."
  fi

  # Env PREFIX is the default only; an explicit --prefix flag wins.
  local prefix="${PREFIX:-$PREFIX_DEFAULT}" assume_yes="no"
  while [ $# -gt 0 ]; do
    case "$1" in
      --prefix) prefix="${2:-}"; shift 2 ;;
      --prefix=*) prefix="${1#*=}"; shift ;;
      -y|--yes) assume_yes="yes"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown argument: $1 (see --help)" ;;
    esac
  done

  # Validate prefix against the two accepted roots only (binaries must end up
  # in /opt/local/sbin or /usr/local/sbin to satisfy the trust policy).
  case "$prefix" in
    /opt/local|/usr/local) ;;
    *) die "Prefix must be /opt/local or /usr/local; got '$prefix'." ;;
  esac
  local sbindir="$prefix/sbin"

  # /usr/local is only safe if it (and ancestors) are root-owned.
  if [ "$prefix" = "/usr/local" ]; then
    if [ -d "$prefix" ] && ! dir_is_root_managed "$prefix"; then
      die "/usr/local is user-owned (Homebrew?). Use --prefix /opt/local."
    fi
    warn "/usr/local can have user-owned ancestors; verifying at install time."
  fi

  # Resolve Homebrew by absolute path (sudo resets PATH).
  local brew=""
  for cand in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$cand" ] && { brew="$cand"; break; }
  done
  [ -n "$brew" ] || die "Homebrew not found at /opt/homebrew or /usr/local. Install it first."

  if ! { command -v xcode-select >/dev/null 2>&1 && xcode-select -p >/dev/null 2>&1; }; then
    warn "Xcode Command Line Tools not detected; the build may fail. Run: xcode-select --install"
  fi

  info "Installer plan"
  log  "  prefix:            $prefix (binaries in $sbindir)"
  log  "  invoking user:     $SUDO_USER (uid $SUDO_UID)"
  log  "  homebrew:          $brew"
  log  "  dislocker pin:     $DISLOCKER_SHA"
  log  "  ntfs-3g pin:       $NTFS3G_SHA"
  log  "  will create root-owned: $sbindir/dislocker-fuse, $sbindir/ntfs-3g"
  # MacPorts users: /opt/local is their prefix; these files are unmanaged by port.
  [ "$prefix" = "/opt/local" ] && [ -x /opt/local/bin/port ] && \
    warn "MacPorts detected at /opt/local; installed files are not port-managed."

  if [ "$assume_yes" != "yes" ]; then
    printf 'Proceed? [y/N] '
    local reply; read -r reply
    case "$reply" in y|Y|yes|YES) ;; *) die "Aborted by user." ;; esac
  fi

  # Handoff dir for the unprivileged child to report STAGEDIR + manifest back.
  local handoff; handoff="$(mktemp -d /tmp/dislocker-rootdeps.XXXXXX)"
  chown "$SUDO_UID" "$handoff"; chmod 0700 "$handoff"

  # --- spawn phase 1 as the invoking user (brew + build + stage) ----------
  # -H so Homebrew sees the user's HOME (not /var/root) and its caches.
  info "Building as $SUDO_USER (unprivileged)…"
  if ! sudo -H -u "#$SUDO_UID" /usr/bin/env \
        HANDOFF="$handoff" PREFIX="$prefix" BREW="$brew" \
        DISLOCKER_SHA="$DISLOCKER_SHA" NTFS3G_SHA="$NTFS3G_SHA" \
        bash "$0" --phase1; then
    rm -rf "$handoff"
    die "Build phase failed (see output above)."
  fi

  # If phase 1 chose to pause (macFUSE install/approval), it signals via a file.
  if [ -f "$handoff/paused" ]; then
    rm -rf "$handoff"
    exit 0   # message already printed by phase 1; user reboots and re-runs.
  fi

  local stagedir manifest
  stagedir="$(cat "$handoff/stagedir")"
  manifest="$handoff/manifest.sha256"
  if [ ! -d "$stagedir" ] || [ ! -s "$manifest" ]; then
    rm -rf "$handoff"
    die "Staging did not complete."
  fi

  # --- phase 2 runs here, still root (no re-auth) -------------------------
  phase2 "$prefix" "$sbindir" "$stagedir" "$manifest"
  local rc=$?
  rm -rf "$handoff" "$stagedir" 2>/dev/null || true
  return $rc
}

# ===========================================================================
# Phase 1 — unprivileged child: brew, build, stage. Runs as the invoking user.
# ===========================================================================

phase1() {
  : "${HANDOFF:?}" "${PREFIX:?}" "${BREW:?}"
  local brew="$BREW"

  info "brew update (avoids stale build deps)"
  "$brew" update >/dev/null || warn "brew update failed; continuing with current formulae."

  # --- macFUSE must be installed (headers/libs present to build against) ---
  # macFUSE is a KERNEL EXTENSION, not a System Extension, so it never appears
  # in systemextensionsctl / Privacy & Security as a "system extension"; its
  # kext loads on demand at first mount (and is approved then, which on Apple
  # Silicon can require enabling kernel extensions in Recovery). For BUILDING
  # we only need macFUSE's libs/headers, which pkg-config confirms below.
  local pc_dir="/usr/local/lib/pkgconfig"
  export PKG_CONFIG_PATH="$pc_dir${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  if ! macfuse_installed; then
    info "macFUSE not installed — installing the cask"
    "$brew" install --cask macfuse || die "brew install --cask macfuse failed."
    cat <<'EOF'

macFUSE was installed. Its kernel extension loads the first time you mount a
FUSE volume; macOS will prompt you to allow it then (on Apple Silicon this can
require enabling kernel extensions in Recovery, then a reboot). No action is
needed in Privacy & Security right now. Re-run this installer to continue.

EOF
    printf 'Press Enter to acknowledge… '; read -r _ || true
    : > "$HANDOFF/paused"
    exit 0
  fi

  # macFUSE (>= 4.10) ships libfuse2/libfuse3 + .pc files, normally in
  # /usr/local/lib. dislocker master requires fuse3; gate on it explicitly.
  if ! pkg-config --exists fuse3; then
    die "macFUSE >= $MACFUSE_MIN with fuse3 not found (pkg-config --exists fuse3 failed).
Install/upgrade macFUSE, or use the manual Homebrew path in the README."
  fi
  # NOTE: macFUSE often installs libfuse into a USER-OWNED /usr/local/lib (true
  # even on Apple Silicon). We do not require that dir to be root-owned; phase 2
  # VENDORS libfuse root-owned into $PREFIX/lib and repoints the binaries, so
  # the final dyld closure is fully root-managed regardless of who owns
  # /usr/local/lib. The recursive otool gate in phase 2 is the backstop.

  info "Installing build dependencies via Homebrew"
  "$brew" install cmake autoconf automake libtool pkg-config mbedtls@3 \
    || die "Failed to install build dependencies."

  # dislocker master needs MbedTLS 3, but Homebrew's mainline `mbedtls` is 4.x;
  # mbedtls@3 is keg-only, so point CMake straight at its config dir or
  # find_package(MbedTLS 3) rejects the 4.x one it finds by default.
  local mbedtls3; mbedtls3="$("$brew" --prefix mbedtls@3)"
  local mbedtls_dir="$mbedtls3/lib/cmake/MbedTLS"
  [ -f "$mbedtls_dir/MbedTLSConfig.cmake" ] || die "mbedtls@3 CMake config not found at $mbedtls_dir."

  local stagedir; stagedir="$(mktemp -d /tmp/dislocker-stage.XXXXXX)"
  chmod 0700 "$stagedir"
  local workdir; workdir="$(mktemp -d /tmp/dislocker-build.XXXXXX)"
  # shellcheck disable=SC2064
  trap "rm -rf '$workdir'" EXIT

  build_dislocker "$workdir" "$stagedir" "$pc_dir" "$mbedtls_dir"
  build_ntfs3g "$workdir" "$stagedir" "$pc_dir"

  # Record manifest (relative path + sha256) for the root phase to re-verify.
  ( cd "$stagedir" && find . -type f -print0 \
      | xargs -0 shasum -a 256 ) > "$HANDOFF/manifest.sha256"
  printf '%s\n' "$stagedir" > "$HANDOFF/stagedir"
  info "Staged install tree at $stagedir"
}

# Clone a repo at a pinned SHA into $1/$2, aborting if HEAD != pin.
clone_pinned() {
  local repo="$1" sha="$2" dest="$3"
  git clone --quiet "$repo" "$dest" || die "git clone $repo failed."
  git -C "$dest" checkout --quiet "$sha" || die "checkout $sha failed for $repo."
  local head; head="$(git -C "$dest" rev-parse HEAD)"
  [ "$head" = "$sha" ] || die "Pin mismatch for $repo: HEAD=$head expected=$sha."
}

build_dislocker() {
  local workdir="$1" stagedir="$2" pc_dir="$3" mbedtls_dir="$4"
  local src="$workdir/dislocker"
  info "Building dislocker @ $DISLOCKER_SHA"
  clone_pinned "$DISLOCKER_REPO" "$DISLOCKER_SHA" "$src"
  # -Dbindir puts dislocker-fuse in sbin (master defaults it to bin).
  # -DWITH_RUBY=OFF avoids pulling system libruby into the root-trusted binary.
  # -DMbedTLS_DIR pins find_package(MbedTLS 3) to the keg-only mbedtls@3 config
  #   (Homebrew's mainline mbedtls is 4.x and gets rejected on version).
  # -DFUSE_DARWIN_ENABLE_EXTENSIONS=0: macFUSE ≥4.10 enables Darwin-specific
  #   fuse3 API extensions by default (fuse_darwin_attr instead of struct
  #   stat). Dislocker uses the vanilla FUSE3 API; without this flag AppleClang
  #   fails with -Wincompatible-function-pointer-types on getattr/readdir
  #   (macFUSE#1064; same fix used by nixpkgs for dislocker on Darwin).
  # dislocker's own CMake sets CMAKE_INSTALL_RPATH, so we pass no rpath flag.
  # PKG_CONFIG_PATH selects macFUSE's fuse3 (FindFUSE.cmake uses fuse3).
  PKG_CONFIG_PATH="$pc_dir" cmake -S "$src" -B "$src/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_C_FLAGS="-DFUSE_DARWIN_ENABLE_EXTENSIONS=0" \
    -DMbedTLS_DIR="$mbedtls_dir" \
    -Dbindir="$PREFIX/sbin" \
    -DWITH_RUBY=OFF \
    -DWITH_FUSE=ON || die "dislocker cmake configure failed."
  cmake --build "$src/build" || die "dislocker build failed."
  # DESTDIR (not --prefix): dislocker bakes absolute bindir/libdir into
  # install() rules at configure time ($PREFIX/sbin, $PREFIX/lib). cmake
  # --install --prefix only remaps CMAKE_INSTALL_PREFIX-relative paths, so it
  # would try to write /opt/local/lib as the unprivileged phase-1 user and
  # fail. DESTDIR prepends to absolute destinations — same pattern as
  # ntfs-3g's make DESTDIR=… install, and what dislocker's own symlink
  # install(CODE) already expects via $ENV{DESTDIR}.
  DESTDIR="$stagedir" cmake --install "$src/build" \
    || die "dislocker stage-install failed."
}

build_ntfs3g() {
  local workdir="$1" stagedir="$2" pc_dir="$3"
  local src="$workdir/ntfs-3g"
  info "Building ntfs-3g @ $NTFS3G_SHA"
  clone_pinned "$NTFS3G_REPO" "$NTFS3G_SHA" "$src"
  ( cd "$src" && ./autogen.sh ) || die "ntfs-3g autogen.sh failed."
  # --enable-mount-helper=no: dislocker-ui invokes ntfs-3g via argv, never via
  #   /sbin/mount.ntfs-3g, so the mount helper is intentionally not built. Do
  #   NOT "fix" this by enabling it.
  # --disable-library: link libntfs-3g statically into the binary so no
  #   versioned libntfs-3g.*.dylib symlinks land in $PREFIX/lib.
  # --bindir=$PREFIX/sbin: required. ntfs-3g/lowntfs-3g are rootbin_PROGRAMS;
  #   with --exec-prefix set, configure sets rootbindir=$(bindir) (default
  #   $PREFIX/bin). deps.discover_privileged_deps only accepts …/sbin/ntfs-3g,
  #   so bindir must be sbin. --sbindir alone is not enough (that only covers
  #   mkntfs and other sbin_PROGRAMS).
  # Do NOT pass --with-fuse=; on darwin configure forces with_fuse=external and
  #   ignores it. PKG_CONFIG_PATH selects macFUSE's fuse2 (fuse.pc).
  ( cd "$src" && PKG_CONFIG_PATH="$pc_dir" ./configure \
      --prefix="$PREFIX" --exec-prefix="$PREFIX" \
      --bindir="$PREFIX/sbin" --sbindir="$PREFIX/sbin" \
      --enable-mount-helper=no --disable-ldconfig --disable-library ) \
    || die "ntfs-3g configure failed."
  ( cd "$src" && make ) || die "ntfs-3g make failed."
  ( cd "$src" && make DESTDIR="$stagedir" install ) || die "ntfs-3g stage-install failed."
}

# ===========================================================================
# Phase 2 — root: verify staging, install into $PREFIX, verify trust.
# ===========================================================================

phase2() {
  local prefix="$1" sbindir="$2" stagedir="$3" manifest="$4"

  # Re-hash catches incomplete/partial staging or changes by another user.
  # It does NOT stop the same unprivileged user rewriting stage+manifest as a
  # consistent pair before phase 2 copies; phase 2 still only installs
  # sbin/ + lib/ under the allow-listed prefix and then runs the dyld trust gate.
  info "Re-checking staged file hashes before root install"
  verify_manifest "$stagedir" "$manifest" || die "Staged file hash mismatch; refusing to install."

  # Record of files THIS run installs, so failure cleanup never touches
  # pre-existing files. (A file, not a bash array, to stay bash-3.2 clean.)
  local installed_list; installed_list="$(mktemp /tmp/dislocker-installed.XXXXXX)"

  install -d -o root -g wheel -m 0755 "$sbindir" || die "cannot create $sbindir"
  install -d -o root -g wheel -m 0755 "$prefix/lib" || die "cannot create $prefix/lib"
  dir_is_root_managed "$sbindir" || die "$sbindir has untrusted ancestors; aborting."

  local rel src mode dest
  while IFS= read -r -d '' src; do
    rel="${src#"$stagedir$prefix"/}"
    case "$rel" in
      sbin/*) mode=0755 ;;
      lib/*.dylib) mode=0755 ;;
      lib/pkgconfig/*) mode=0644 ;;
      lib/*) mode=0644 ;;
      *) continue ;;   # only install sbin/ and lib/ payloads
    esac
    dest="$prefix/$rel"
    # BSD/macOS install has no -D: create the parent dir explicitly first.
    install -d -o root -g wheel -m 0755 "$(dirname "$dest")" \
      || { cleanup_failed "$installed_list"; die "cannot create $(dirname "$dest")."; }
    install -o root -g wheel -m "$mode" "$src" "$dest" \
      || { cleanup_failed "$installed_list"; die "install of $rel failed."; }
    printf '%s\n' "$dest" >> "$installed_list"
  done < <(find "$stagedir$prefix" -type f -print0 2>/dev/null)

  # CMake (and some autotools) install versioned dylibs as real files plus
  # compatibility symlinks (libdislocker.0.7.dylib -> libdislocker.0.7.3.dylib).
  # find -type f above skipped those symlinks; without them @rpath lookups fail
  # at runtime (F15). Recreate only lib/ symlinks whose ultimate target is a
  # file we just installed under $prefix/lib.
  if ! install_staged_lib_symlinks "$prefix" "$stagedir" "$installed_list"; then
    cleanup_failed "$installed_list"
    die "Could not recreate staged lib/ symlinks; install rejected."
  fi

  info "Vendoring macFUSE libfuse into $prefix/lib (root-managed closure)"
  if ! vendor_foreign_dylibs "$prefix" "$sbindir" "$installed_list"; then
    cleanup_failed "$installed_list"
    die "Could not vendor libfuse into $prefix/lib; install rejected."
  fi

  info "Sanitizing LC_RPATH (drop Homebrew / other user-owned paths)"
  if ! sanitize_rpaths "$prefix" "$sbindir" "$installed_list"; then
    cleanup_failed "$installed_list"
    die "Could not sanitize LC_RPATH; install rejected."
  fi

  info "Verifying dyld closure is fully root-managed (otool)"
  if ! verify_dyld_closure "$prefix" "$sbindir"; then
    cleanup_failed "$installed_list"
    die "A binary loads a dylib from a user-owned path; install rejected."
  fi

  info "Running the privileged-policy functional check"
  local root; root="$(cd "$(dirname "$0")/.." && pwd)"
  # Installer always ships both tools; assert them explicitly (core_ok no longer
  # requires ntfs-3g so FAT-only hosts can mount without it).
  if ! PYTHONPATH="$root/src" python3 -c \
      'from dislocker_ui.deps import discover_privileged_deps as d; s=d(); assert s.dislocker_fuse and s.ntfs3g, s.missing_core(); print(s)'; then
    cleanup_failed "$installed_list"
    die "discover_privileged_deps() missing dislocker-fuse or ntfs-3g after install."
  fi

  rm -f "$installed_list"
  info "OK — installed and verified:"
  log  "  $sbindir/dislocker-fuse"
  log  "  $sbindir/ntfs-3g"
  log  ""
  log  "Next: ./run.sh  →  click \"Recheck deps\" in dislocker-ui."
}

# Re-hash every staged file and compare against the recorded manifest.
verify_manifest() {
  local stagedir="$1" manifest="$2"
  ( cd "$stagedir" && shasum -a 256 -c "$manifest" --status )
}

# Recreate versioned dylib symlinks that cmake/autotools staged under lib/
# (find -type f in phase2 skips them). Only links whose ultimate target resolves
# to an already-installed file under $prefix/lib are created.
install_staged_lib_symlinks() {
  local prefix="$1" stagedir="$2" installed_list="$3"
  local src rel dest target resolved
  while IFS= read -r -d '' src; do
    rel="${src#"$stagedir$prefix"/}"
    case "$rel" in
      lib/*.dylib|lib/*.so|lib/*.so.*) ;;
      *) continue ;;
    esac
    dest="$prefix/$rel"
    target="$(readlink "$src")" || return 1
    # Resolve relative symlink targets against the destination dir.
    case "$target" in
      /*) resolved="$target" ;;
      *)  resolved="$(dirname "$dest")/$target" ;;
    esac
    # Refuse dangling or out-of-prefix link targets.
    if [ ! -f "$resolved" ]; then
      warn "staged symlink $rel -> $target has no installed target at $resolved"
      return 1
    fi
    case "$resolved" in
      "$prefix"/lib/*) ;;
      *)
        warn "staged symlink $rel resolves outside $prefix/lib ($resolved)"
        return 1 ;;
    esac
    ln -sfn "$target" "$dest" || return 1
    chown -h root:wheel "$dest" || return 1
    printf '%s\n' "$dest" >> "$installed_list"
  done < <(find "$stagedir$prefix/lib" -type l -print0 2>/dev/null)
  return 0
}

# Drop LC_RPATH entries that are not $PREFIX/lib (CMAKE_INSTALL_RPATH_USE_LINK_PATH
# pulls in Homebrew mbedtls@3 and similar user-owned dirs). Keep a single
# $PREFIX/lib rpath so @rpath/libdislocker.*.dylib resolves locally. Re-sign
# any Mach-O we modify.
sanitize_rpaths() {
  local prefix="$1" sbindir="$2" installed_list="$3"
  local f path changed
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    changed=0
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      if [ "$path" = "$prefix/lib" ]; then
        continue
      fi
      install_name_tool -delete_rpath "$path" "$f" 2>/dev/null || true
      changed=1
    done < <(otool -l "$f" 2>/dev/null | awk '
      $1 == "cmd" && $2 == "LC_RPATH" { in_rpath=1; next }
      in_rpath && $1 == "path" { print $2; in_rpath=0 }
    ')
    # Ensure $PREFIX/lib is present as an rpath for @rpath loads.
    if ! otool -l "$f" 2>/dev/null | awk -v want="$prefix/lib" '
      $1 == "cmd" && $2 == "LC_RPATH" { in_rpath=1; next }
      in_rpath && $1 == "path" { if ($2 == want) found=1; in_rpath=0 }
      END { exit found ? 0 : 1 }
    '; then
      install_name_tool -add_rpath "$prefix/lib" "$f" 2>/dev/null || return 1
      changed=1
    fi
    if [ "$changed" = "1" ]; then
      codesign -f -s - "$f" >/dev/null 2>&1 || { warn "re-sign $f failed"; return 1; }
    fi
  done < <( { find "$sbindir" -type f -perm -u+x 2>/dev/null
              find "$prefix/lib" -type f -name '*.dylib' 2>/dev/null; } )
  return 0
}

# Copy every dylib the installed Mach-O files link by ABSOLUTE path from
# OUTSIDE the trusted set into $PREFIX/lib (root-owned), repoint the referrers,
# and ad-hoc re-sign (install_name_tool invalidates the Mach-O signature and
# arm64 requires a valid one). This covers macFUSE's libfuse (user-owned
# /usr/local/lib) and Homebrew's mbedtls (user-owned /opt/homebrew), which
# `libdislocker` links. @rpath/@loader_path deps resolve within $PREFIX/lib
# (root-managed) and are left alone. Runs a few bounded passes so transitive
# foreign deps of vendored copies are also brought in.
vendor_foreign_dylibs() {
  local prefix="$1" sbindir="$2" installed_list="$3"
  local _pass changed_any f dep base copy resign
  for _pass in 1 2 3 4; do
    changed_any=0
    while IFS= read -r f; do
      [ -f "$f" ] || continue
      resign=0
      while IFS= read -r dep; do
        case "$dep" in /*) ;; *) continue ;; esac   # absolute paths only
        case "$dep" in
          "$prefix"/lib/*|/usr/lib/*|/System/Library/*|/Library/Filesystems/macfuse.fs/*)
            continue ;;
        esac
        base="$(basename "$dep")"
        copy="$prefix/lib/$base"
        if [ ! -f "$copy" ]; then
          [ -f "$dep" ] || { warn "vendor source missing: $dep"; return 1; }
          install -o root -g wheel -m 0755 "$dep" "$copy" || return 1
          install_name_tool -id "$copy" "$copy" 2>/dev/null || return 1
          codesign -f -s - "$copy" >/dev/null 2>&1 || { warn "re-sign $copy failed"; return 1; }
          printf '%s\n' "$copy" >> "$installed_list"
        fi
        install_name_tool -change "$dep" "$copy" "$f" 2>/dev/null || return 1
        resign=1; changed_any=1
      done < <(otool -L "$f" | tail -n +2 | awk '{print $1}')
      if [ "$resign" = "1" ]; then
        install_name_tool -delete_rpath /usr/local/lib "$f" 2>/dev/null || true
        codesign -f -s - "$f" >/dev/null 2>&1 || { warn "re-sign $f failed"; return 1; }
      fi
    done < <( { find "$sbindir" -type f -perm -u+x 2>/dev/null
                find "$prefix/lib" -type f -name '*.dylib' 2>/dev/null; } )
    [ "$changed_any" = "0" ] && break
  done
  return 0
}

# otool-walk every installed executable and dylib; assert every load command
# resolves to $PREFIX/lib, /usr/lib, /System/Library, or a ROOT-OWNED macFUSE
# libfuse dir. Fail on any user-owned target. Also require @rpath names to
# exist under $PREFIX/lib, and every LC_RPATH to be $PREFIX/lib only.
verify_dyld_closure() {
  local prefix="$1" sbindir="$2"
  local targets=() f
  while IFS= read -r f; do targets+=("$f"); done < <(
    find "$sbindir" -type f -perm -u+x 2>/dev/null
    find "$prefix/lib" -type f -name '*.dylib' 2>/dev/null
  )
  # Bash 3.2 + set -u: empty "${targets[@]}" is an unbound-variable error.
  if [ ${#targets[@]} -eq 0 ]; then
    warn "no executables/dylibs found under $sbindir and $prefix/lib"
    return 1
  fi
  local t line dep name path
  for t in "${targets[@]}"; do
    while IFS= read -r line; do
      dep="$(printf '%s' "$line" | sed -E 's/^[[:space:]]*//; s/ \(compatibility.*$//')"
      case "$dep" in
        "") continue ;;
        @rpath/*)
          name="${dep#@rpath/}"
          if [ ! -e "$prefix/lib/$name" ]; then
            warn "@rpath/$name not present in $prefix/lib (needed by $t)"
            return 1
          fi
          continue ;;
        @loader_path/*|@executable_path/*)
          warn "unsupported relative load command '$dep' on $t"
          return 1 ;;
        "$prefix"/lib/*|/usr/lib/*|/System/Library/*) continue ;;
        *)
          # Allow ONLY a root-managed macFUSE libfuse dir.
          local depdir; depdir="$(dirname "$dep")"
          if dir_is_root_managed "$depdir"; then continue; fi
          warn "untrusted dylib '$dep' loaded by $t"
          return 1 ;;
      esac
    done < <(otool -L "$t" | tail -n +2)

    while IFS= read -r path; do
      [ -n "$path" ] || continue
      if [ "$path" != "$prefix/lib" ]; then
        warn "untrusted LC_RPATH '$path' on $t (only $prefix/lib allowed)"
        return 1
      fi
    done < <(otool -l "$t" 2>/dev/null | awk '
      $1 == "cmd" && $2 == "LC_RPATH" { in_rpath=1; next }
      in_rpath && $1 == "path" { print $2; in_rpath=0 }
    ')
  done
  return 0
}

# Best-effort removal of ONLY files this run installed (never pre-existing).
# Reads newline-delimited paths from the installed-list file (bash-3.2 safe).
# Symlinks are removed with rm -f as well.
cleanup_failed() {
  local installed_list="$1" p
  [ -f "$installed_list" ] || return 0
  while IFS= read -r p; do
    if [ -n "$p" ]; then
      rm -f "$p" 2>/dev/null || true
    fi
  done < "$installed_list"
  warn "Rolled back files created by this run."
}

# ===========================================================================
# macFUSE presence
# ===========================================================================
# True when macFUSE is INSTALLED (its bundle dir exists OR fuse pkg-config is
# resolvable). We intentionally do NOT probe kext "loadedness" here: the kext
# loads on demand at first mount, so it is normally not loaded at build time,
# and systemextensionsctl never lists it (macFUSE is a kext, not a DriverKit
# system extension).
macfuse_installed() {
  [ -d /Library/Filesystems/macfuse.fs ] && return 0
  pkg-config --exists fuse3 2>/dev/null && return 0
  pkg-config --exists fuse 2>/dev/null && return 0
  return 1
}

# ===========================================================================
# Dispatch
# ===========================================================================
case "${1:-}" in
  --phase1) shift; phase1 "$@" ;;
  *)        phase0 "$@" ;;
esac
