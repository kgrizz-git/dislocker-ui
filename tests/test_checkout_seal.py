"""
Tests for the privileged checkout seal.

Overall purpose:
  Prove the digest changes when importable bytes change, that importable
  symlinks are refused, and that the root bootstrap does not import a tree
  that no longer matches the authorized digest.

Requirements:
  pytest. The bootstrap cases spawn a fresh interpreter with ``-I -S`` so
  they do not load this process's already-imported ``dislocker_ui``.
"""

from __future__ import annotations

import base64
import os
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from dislocker_ui.checkout_seal import (
    BOOTSTRAP_STUB,
    EMBEDDED_SOURCE,
    PRUNE_DIR_NAMES,
    SealError,
    _assert_root_owned_chain,
    _is_private_parent,
    _private_parent,
    _same_directory,
    assert_root_owned_interpreter,
    bootstrap_main,
    materialize,
    measure,
)
from dislocker_ui.elevate import _NON_CODE_DIRS


def _chmod_tree(root: Path) -> None:
    """Force directory 0755 and file 0644 so the seal's writable check passes.

    0755 is the normal traversable directory mode (no group or other write).
    Bandit B103 treats that mask as permissive because it assumes a file.
    """
    for dirpath, _dirnames, filenames in os.walk(root):
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- directory must be traversable and not group-writable
        os.chmod(dirpath, 0o755)  # nosec B103
        for name in filenames:
            os.chmod(Path(dirpath) / name, 0o644)


def _checkout(tmp: Path, source: str = "def main():\n    return 0\n") -> Path:
    """Build a tiny importable package and return its ``src`` root."""
    root = tmp / "src"
    pkg = root / "dislocker_ui"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "privileged.py").write_text(source, encoding="utf-8")
    _chmod_tree(root)
    return root


def _spawn(
    src: Path, digest: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the embedded seal the way the authorized command does."""
    payload = base64.b64encode(EMBEDDED_SOURCE).decode("ascii")
    argv = [
        sys.executable,
        "-I",
        "-S",
        "-s",
        "-c",
        BOOTSTRAP_STUB,
        payload,
        str(src),
        digest,
        "dislocker_ui.privileged",
        "mount",
        "--uid",
        "0",
        "--request",
        "/missing",
        "--request-sha256",
        "ab" * 32,
    ]
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    if env:
        child_env.update(env)
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        env=child_env,
        timeout=30,
    )


def test_prune_names_match_elevation_scan() -> None:
    """The seal and the pre-dialog scan skip the same non-code directories."""
    assert PRUNE_DIR_NAMES == _NON_CODE_DIRS


def test_measure_is_stable_until_importable_bytes_change(tmp_path: Path) -> None:
    """Same tree, same digest. A rewritten module is a different digest."""
    root = _checkout(tmp_path)
    first = measure(root)
    assert first == measure(root)
    module = root / "dislocker_ui" / "privileged.py"
    module.write_text("def main():\n    return 1\n", encoding="utf-8")
    module.chmod(0o644)
    assert measure(root) != first


def test_measure_refuses_importable_symlink_and_linked_directory(tmp_path: Path) -> None:
    """Importable links and linked packages are refused, matching run.sh."""
    root = _checkout(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    outside.chmod(0o644)
    link = root / "dislocker_ui" / "extra.py"
    link.symlink_to(outside)
    with pytest.raises(SealError, match="importable symlink"):
        measure(root)
    link.unlink()
    target = tmp_path / "linked-pkg"
    target.mkdir()
    (root / "dislocker_ui" / "nested").symlink_to(target, target_is_directory=True)
    with pytest.raises(SealError, match="symlink directory"):
        measure(root)


def test_measure_ignores_non_importable_symlink_and_pruned_dirs(tmp_path: Path) -> None:
    """A docs symlink and a writable file under tmp/ are outside the seal."""
    root = _checkout(tmp_path)
    note = tmp_path / "note.txt"
    note.write_text("one", encoding="utf-8")
    (root / "README").symlink_to(note)
    pruned = root / "tmp"
    pruned.mkdir()
    bad = pruned / "bad.py"
    bad.write_text("trojan = True\n", encoding="utf-8")
    bad.chmod(0o666)
    _chmod_tree(root)
    # The pruned file stays writable; the walk re-chmod only if we walk it.
    bad.chmod(0o666)
    first = measure(root)
    note.write_text("two", encoding="utf-8")
    assert measure(root) == first


def test_importable_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    """A FIFO with an importable name is refused without waiting for a writer."""
    root = _checkout(tmp_path)
    os.mkfifo(root / "dislocker_ui" / "hook.py")
    holder: dict[str, object] = {}

    def _run() -> None:
        try:
            measure(root)
        except SealError as exc:
            holder["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    error = holder.get("error")
    assert isinstance(error, SealError)
    assert "not a regular file" in str(error)


def test_measure_refuses_group_writable_module(tmp_path: Path) -> None:
    """A group-writable importable file is not sealed."""
    root = _checkout(tmp_path)
    module = root / "dislocker_ui" / "privileged.py"
    module.chmod(0o664)
    with pytest.raises(SealError, match="writable"):
        measure(root)


def test_materialize_copies_approved_bytes_and_mismatch_leaves_no_directory(
    tmp_path: Path,
) -> None:
    """The private copy is the hashed bytes. A mismatch creates nothing."""
    root = _checkout(tmp_path, source="VALUE = 'approved'\n")
    digest = measure(root)
    private = materialize(root, digest)
    assert private is not None
    try:
        assert oct(private.stat().st_mode & 0o777) == "0o700"
        copied = (private / "dislocker_ui" / "privileged.py").read_text(encoding="utf-8")
        assert copied == "VALUE = 'approved'\n"
    finally:
        import shutil

        shutil.rmtree(private)
    module = root / "dislocker_ui" / "privileged.py"
    module.write_text("VALUE = 'replaced'\n", encoding="utf-8")
    module.chmod(0o644)
    before = set(Path(tempfile.gettempdir()).glob("dislocker-ui-seal-*"))
    with pytest.raises(SealError, match="changed"):
        materialize(root, digest)
    after = set(Path(tempfile.gettempdir()).glob("dislocker-ui-seal-*"))
    assert after == before


def test_embedded_source_measures_like_the_imported_module(tmp_path: Path) -> None:
    """The bytes embedded in the authorized command implement the same digest."""
    root = _checkout(tmp_path)
    namespace: dict[str, object] = {"__name__": "seal_copy"}
    exec(EMBEDDED_SOURCE, namespace)  # nosec B102 — run the seal source captured at import
    embedded_measure = namespace["measure"]
    assert callable(embedded_measure)
    assert embedded_measure(root) == measure(root)


def test_bootstrap_does_not_import_a_rewritten_module(tmp_path: Path) -> None:
    """A module replaced after the digest is not executed, even via PYTHONPATH."""
    marker = tmp_path / "executed"
    root = _checkout(tmp_path, source="def main():\n    return 0\n")
    digest = measure(root)
    module = root / "dislocker_ui" / "privileged.py"
    module.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "def main():\n"
        "    return 99\n",
        encoding="utf-8",
    )
    module.chmod(0o644)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "base64.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('shadow')\n"
        "raise SystemExit(97)\n",
        encoding="utf-8",
    )
    proc = _spawn(root, digest, env={"PYTHONPATH": str(shadow)})
    assert proc.returncode == 2
    assert "changed" in proc.stderr
    assert not marker.exists()


def test_bootstrap_imports_the_private_copy(tmp_path: Path) -> None:
    """On a match, root imports the sealed copy and then deletes it."""
    marker = tmp_path / "loaded"
    root = _checkout(
        tmp_path,
        source=(
            "def main():\n"
            "    from pathlib import Path\n"
            f"    Path({str(marker)!r}).write_text(Path(__file__).resolve().as_posix())\n"
            "    return 0\n"
        ),
    )
    proc = _spawn(root, measure(root))
    assert proc.returncode == 0, proc.stderr
    loaded = Path(marker.read_text(encoding="utf-8"))
    assert "dislocker-ui-seal-" in loaded.as_posix()
    assert not loaded.is_relative_to(root)
    assert not loaded.exists()


def test_bootstrap_main_rejects_mismatch_without_importing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process mismatch returns validation status 2 before import."""
    root = _checkout(tmp_path)
    digest = measure(root)
    module = root / "dislocker_ui" / "privileged.py"
    module.write_text("def main():\n    return 5\n", encoding="utf-8")
    module.chmod(0o644)
    monkeypatch.setattr(
        sys,
        "argv",
        ["-c", "ignored", str(root), digest, "dislocker_ui.privileged", "mount"],
    )
    assert bootstrap_main() == 2


def test_symlink_or_file_source_root_is_refused(tmp_path: Path) -> None:
    """The seal will not follow a linked checkout or hash a file as a tree."""
    real = _checkout(tmp_path)
    linked = tmp_path / "linked-src"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(SealError, match="symlink"):
        measure(linked)
    leaf = tmp_path / "not-a-tree"
    leaf.write_text("x", encoding="utf-8")
    with pytest.raises(SealError, match="not a directory"):
        measure(leaf)


def test_install_root_seals_package_bytes_not_sibling_projects(tmp_path: Path) -> None:
    """site-packages is not trusted as a directory. Only dislocker_ui bytes count."""
    root = tmp_path / "site-packages"
    pkg = root / "dislocker_ui"
    pkg.mkdir(parents=True)
    init = pkg / "__init__.py"
    init.write_text("x = 1\n", encoding="utf-8")
    sibling = root / "other.py"
    sibling.write_text("y = 1\n", encoding="utf-8")
    _chmod_tree(root)
    sibling.chmod(0o666)
    first = measure(root)
    sibling.write_text("y = 2\n", encoding="utf-8")
    assert measure(root) == first
    init.write_text("x = 2\n", encoding="utf-8")
    init.chmod(0o644)
    assert measure(root) != first


@pytest.mark.parametrize("name", ["site-packages", "dist-packages"])
def test_install_root_without_the_package_is_refused(tmp_path: Path, name: str) -> None:
    """An install root that does not contain dislocker_ui is not an empty seal."""
    root = tmp_path / name
    root.mkdir()
    (root / "other.py").write_text("x = 1\n", encoding="utf-8")
    _chmod_tree(root)
    with pytest.raises(SealError, match="not in the install"):
        measure(root)


def test_bootstrap_imports_the_private_copy_of_an_install(tmp_path: Path) -> None:
    """A site-packages root still imports the sealed package, not the live tree."""
    marker = tmp_path / "loaded"
    root = tmp_path / "site-packages"
    package = root / "dislocker_ui"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "privileged.py").write_text(
        "def main():\n"
        "    from pathlib import Path\n"
        f"    Path({str(marker)!r}).write_text(Path(__file__).resolve().as_posix())\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (root / "other.py").write_text("SENTINEL = True\n", encoding="utf-8")
    _chmod_tree(root)
    proc = _spawn(root, measure(root))
    assert proc.returncode == 0, proc.stderr
    loaded = Path(marker.read_text(encoding="utf-8"))
    assert "dislocker-ui-seal-" in loaded.as_posix()
    assert not loaded.is_relative_to(root)
    assert not loaded.exists()


def test_bootstrap_main_rejects_bad_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing argv and a swapped module name exit 2 before import."""
    monkeypatch.setattr(sys, "argv", ["-c"])
    assert bootstrap_main() == 2
    monkeypatch.setattr(sys, "argv", ["-c", "x", "/missing", "ab" * 32, "os", "mount"])
    assert bootstrap_main() == 2


def test_user_owned_interpreter_is_refused(tmp_path: Path) -> None:
    """The dialog path will not exec a Python the user can replace."""
    py = tmp_path / "python3"
    py.write_text("", encoding="utf-8")
    py.chmod(0o755)
    with pytest.raises(SealError, match="root-owned"):
        assert_root_owned_interpreter(py)


def test_interpreter_check_returns_the_validated_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path returned is the canonical path the chain check accepted."""
    binary = tmp_path / "python3"
    binary.write_text("", encoding="utf-8")
    seen: list[Path] = []

    def _accept(path: Path) -> None:
        seen.append(path)

    monkeypatch.setattr("dislocker_ui.checkout_seal._assert_root_owned_chain", _accept)
    resolved = assert_root_owned_interpreter(binary)
    assert resolved == binary.resolve()
    assert seen == [resolved]


def test_root_owned_chain_accepts_only_nonwritable_root_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fake root-owned chain passes; a symlink or group-writable node does not."""
    binary = tmp_path / "python3"
    binary.write_text("", encoding="utf-8")
    resolved = binary.resolve()
    real_lstat = os.lstat

    def _as(mode: int) -> object:
        def fake(path: object) -> os.stat_result:
            info = real_lstat(path)
            return os.stat_result(
                (
                    mode,
                    info.st_ino,
                    info.st_dev,
                    info.st_nlink,
                    0,
                    0,
                    info.st_size,
                    info.st_atime,
                    info.st_mtime,
                    info.st_ctime,
                )
            )

        return fake

    monkeypatch.setattr("dislocker_ui.checkout_seal.os.lstat", _as(stat.S_IFREG | 0o755))
    _assert_root_owned_chain(resolved)
    monkeypatch.setattr("dislocker_ui.checkout_seal.os.lstat", _as(stat.S_IFLNK | 0o755))
    with pytest.raises(SealError, match="symlink"):
        _assert_root_owned_chain(resolved)
    monkeypatch.setattr("dislocker_ui.checkout_seal.os.lstat", _as(stat.S_IFREG | 0o775))
    with pytest.raises(SealError, match="root-owned"):
        _assert_root_owned_chain(resolved)


def test_root_does_not_seal_inside_the_caller_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root process refuses a parent the invoking user can rename."""
    parent = tmp_path / "caller-temp"
    parent.mkdir()
    assert _is_private_parent(parent, owner=os.getuid())
    assert not _is_private_parent(parent, owner=0)
    writable = tmp_path / "group-writable"
    writable.mkdir()
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- directory write bit is the case under test
    writable.chmod(0o775)  # nosec B103
    assert not _is_private_parent(writable, owner=os.getuid())
    link = tmp_path / "linked-parent"
    link.symlink_to(parent, target_is_directory=True)
    assert not _is_private_parent(link, owner=os.getuid())
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr("dislocker_ui.checkout_seal._ROOT_SEAL_PARENTS", (str(parent),))
    with pytest.raises(SealError, match="root-owned"):
        _private_parent()
    root = _checkout(tmp_path / "tree")
    before = list(parent.glob("dislocker-ui-seal-*"))
    with pytest.raises(SealError, match="root-owned"):
        materialize(root, measure(root))
    assert list(parent.glob("dislocker-ui-seal-*")) == before


def test_private_copy_uses_the_verified_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seal directory is created under the parent the root check selected."""
    parent = tmp_path / "root-home"
    parent.mkdir()
    monkeypatch.setattr("dislocker_ui.checkout_seal._private_parent", lambda: str(parent))
    root = _checkout(tmp_path / "tree")
    private = materialize(root, measure(root))
    try:
        assert private.parent == parent
        assert private.name.startswith("dislocker-ui-seal-")
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
    finally:
        import shutil

        shutil.rmtree(private)


def test_replaced_private_directory_is_refused(tmp_path: Path) -> None:
    """A directory swapped onto the seal path is not the one that was created."""
    directory = tmp_path / "seal"
    directory.mkdir(mode=0o700)
    info = directory.lstat()
    assert stat.S_IMODE(info.st_mode) == 0o700
    _same_directory(directory, info)
    replaced = os.stat_result(
        (
            info.st_mode,
            info.st_ino + 1,
            info.st_dev,
            info.st_nlink,
            info.st_uid,
            info.st_gid,
            info.st_size,
            info.st_atime,
            info.st_mtime,
            info.st_ctime,
        )
    )
    with pytest.raises(SealError, match="changed"):
        _same_directory(directory, replaced)


def test_private_directory_refuses_a_mode_other_than_0700(tmp_path: Path) -> None:
    """A umask that clears the owner bits is a failed seal, not a chmod."""
    root = _checkout(tmp_path)
    digest = measure(root)
    parent = Path(tempfile.gettempdir())
    before = set(parent.glob("dislocker-ui-seal-*"))
    previous = os.umask(0o777)
    try:
        with pytest.raises(SealError, match="mode 0700"):
            materialize(root, digest)
    finally:
        os.umask(previous)
    assert set(parent.glob("dislocker-ui-seal-*")) == before
