"""
Static assertions on scripts/install-root-deps.sh.

Overall purpose:
  Guard the root-dependency installer's load-bearing invariants without a
  macOS host: the script is macOS-gated, targets only the two accepted
  prefixes, pins upstream commit SHAs, avoids the fatal FUSE flags identified
  in the plan, and keeps the trust-critical flags. Runs on the Linux CI runner.

Requirements:
  pytest. Reads the script as text only; never executes it.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-root-deps.sh"


def _text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _noncomment_text() -> str:
    """Script text with whole-line ``#`` comments stripped.

    The script deliberately documents *why* certain flags are wrong; negative
    assertions must not false-fail on that documentation.
    """
    lines = []
    for line in _text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_script_exists_and_is_executable() -> None:
    assert _SCRIPT.is_file(), f"missing {_SCRIPT}"
    mode = _SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "script should be executable in the repo"


def test_bash_shebang() -> None:
    assert _text().startswith("#!/usr/bin/env bash\n")


def test_sets_umask_022() -> None:
    assert re.search(r"^umask 022$", _text(), re.MULTILINE)


def test_only_accepted_prefixes_present() -> None:
    body = _text()
    assert "/opt/local/sbin" in body
    assert "/usr/local/sbin" in body


def test_no_machine_specific_home_path() -> None:
    # The absolute-paths hook covers this repo-wide; assert here too so a bad
    # edit to this script is caught by its own test.
    assert not re.search(r"/Users/[A-Za-z0-9._-]+/", _text())


def test_non_darwin_guard_exits_nonzero() -> None:
    body = _text()
    assert "uname -s" in body and "Darwin" in body
    # The guard must die() (non-zero exit) rather than warn.
    assert re.search(r"uname -s.*Darwin.*\|\|\s*die", body, re.DOTALL)


def test_pins_commit_shas_and_checks_rev_parse() -> None:
    body = _text()
    shas = re.findall(r'_SHA="([0-9a-f]{40})"', body)
    assert len(shas) >= 2, f"expected two 40-hex pinned SHAs, found {shas}"
    # Build must abort on HEAD != pin.
    assert "rev-parse HEAD" in body
    assert re.search(r"Pin mismatch", body)


def test_omits_fatal_fuse_flags() -> None:
    body = _noncomment_text()
    for bad in ("--with-fuse=macfuse", "--with-fuse=internal", "-DFUSE_LIBRARY="):
        assert bad not in body, f"fatal flag {bad!r} present in executable code"


def test_keeps_required_cmake_flags() -> None:
    body = _text()
    assert "-Dbindir=" in body
    assert "-DWITH_RUBY=OFF" in body
    # macFUSE ≥4.10 Darwin fuse3 extensions break vanilla FUSE3 clients
    # (dislocker); the installer must disable them at compile time.
    assert "-DFUSE_DARWIN_ENABLE_EXTENSIONS=0" in body
    # dislocker sets its own install RPATH; we deliberately pass no rpath flag.
    assert "-DCMAKE_INSTALL_RPATH=" not in _noncomment_text()


def test_dislocker_stages_via_destdir() -> None:
    """Absolute bindir/libdir require DESTDIR staging, not --prefix remapping."""
    body = _noncomment_text()
    assert 'DESTDIR="$stagedir" cmake --install' in body
    # Regression: --prefix "$stagedir$PREFIX" tries to write /opt/local as user.
    assert "cmake --install" in body
    assert (
        re.search(
            r'cmake --install\s+"\$src/build"\s+--prefix\s+"\$stagedir\$PREFIX"',
            body,
        )
        is None
    )


def test_macfuse_fuse3_version_gate() -> None:
    body = _text()
    assert "/usr/local/lib/pkgconfig" in body
    assert "pkg-config --exists fuse3" in body


def test_vendors_libfuse_root_owned() -> None:
    body = _text()
    # Phase 2 copies macFUSE's libfuse into $PREFIX/lib, repoints the binaries,
    # and ad-hoc re-signs so the runtime closure is fully root-managed even when
    # macFUSE installs libfuse into a user-owned /usr/local/lib.
    assert "vendor_foreign_dylibs" in body
    assert "install_name_tool" in body
    assert "codesign -f -s -" in body


def test_installs_versioned_dylib_symlinks() -> None:
    # cmake stages libdislocker.0.7.dylib as a symlink; find -type f skips it.
    # Without recreating it, @rpath/libdislocker.0.7.dylib fails at runtime.
    body = _text()
    assert "install_staged_lib_symlinks" in body
    assert "sanitize_rpaths" in body


def test_dyld_gate_checks_rpath_and_at_rpath() -> None:
    body = _text()
    assert "@rpath/$name not present" in body or "not present in $prefix/lib" in body
    assert "untrusted LC_RPATH" in body
    assert "only $prefix/lib allowed" in body


def test_trust_ancestor_check_present() -> None:
    # The recursive otool gate still verifies every load target's dir chain.
    assert "dir_is_root_managed" in _text()


def test_macfuse_detection_not_systemextensionsctl() -> None:
    # macFUSE is a kext, not a System Extension; the detection must not depend
    # on systemextensionsctl (which never lists it) to decide it is installed.
    body = _text()
    assert "macfuse_installed" in body


@pytest.mark.parametrize(
    "flag",
    [
        "-DWITH_FUSE=ON",
        "--disable-library",
        "--enable-mount-helper=no",
        '--bindir="$PREFIX/sbin"',
    ],
)
def test_expected_build_flags(flag: str) -> None:
    assert flag in _text()
