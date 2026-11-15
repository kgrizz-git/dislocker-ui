"""
Seal a checkout before the privileged child imports it.

Overall purpose:
  The administrator dialog can sit open for up to 600 seconds after the
  unprivileged parent has looked at ``src/``. A same-user process can replace
  a mode-0644 module during that wait. Mode bits do not catch a same-mode
  rewrite, and ``python -m dislocker_ui.privileged`` would execute the
  replacement at import time, before ``main()`` runs.

  The parent measures the tree with :func:`measure` and embeds that digest in
  the authorized command, along with this file's source captured when the
  parent imported it. The root process executes that captured source, not the
  copy on disk. It measures the tree again and only then imports
  ``dislocker_ui`` from a private directory filled with the bytes just hashed.

Inputs:
  Absolute checkout root (the ``PYTHONPATH`` entry, normally ``src/``), the
  hex digest the administrator command authorized, and the privileged argv.

Outputs:
  :func:`measure` returns a sha256 hex digest. :func:`materialize` returns a
  mode-0700 directory whose ``.py`` / ``.pyc`` / ``.so`` bytes match that
  digest, or ``None`` when the root is a root-owned system install that the
  user cannot rewrite. The bootstrap exits 2 on a seal failure and otherwise
  returns the privileged ``main()`` status.

Requirements:
  Python 3.10+. Standard library only. No imports from ``dislocker_ui`` — this
  module is what runs before that package is trusted. ``os.O_NOFOLLOW`` is
  required (Darwin and Linux).
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import os
import shutil
import stat
import sys
import tempfile
import traceback
from pathlib import Path

# Names the elevation scan also skips. A drift test locks this to elevate.
PRUNE_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".pytest_cache",
        ".tox",
        ".eggs",
        "build",
        "dist",
        "tmp",
    }
)
_IMPORT_SUFFIXES = frozenset({".py", ".pyc", ".so"})
_PRIVILEGED_MODULE = "dislocker_ui.privileged"
_SHA256_RE_LENGTH = 64
_PRIVATE_PREFIX = "dislocker-ui-seal-"
# Run by `python -I -S -c`. Isolated mode ignores PYTHONPATH so a module
# planted next to the checkout cannot shadow the stdlib before this runs.
BOOTSTRAP_STUB = "import base64,sys; exec(base64.b64decode(sys.argv[1]), {'__name__': '__main__'})"


class SealError(Exception):
    """The checkout is not safe to import as root."""


def _capture_source() -> bytes:
    """Read this file once, at import, so a later disk swap cannot change it."""
    path = globals().get("__file__")
    if not path:
        return b""
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


EMBEDDED_SOURCE = _capture_source()


def _valid_digest(value: str) -> bool:
    """True when *value* is 64 lowercase hex characters."""
    return len(value) == _SHA256_RE_LENGTH and all(ch in "0123456789abcdef" for ch in value)


def _matches(actual: str, expected: str) -> bool:
    """Constant-time compare of two hex digests. Different lengths never match."""
    if not _valid_digest(actual) or not _valid_digest(expected):
        return False
    return hmac.compare_digest(actual, expected)


def _rel(root: Path, path: Path) -> str:
    """Return a posix path relative to *root*, rejecting escapes and newlines."""
    rel = "." if path == root else path.relative_to(root).as_posix()
    if "\n" in rel or "\r" in rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise SealError("refusing unusual checkout path")
    return rel


def is_system_managed(src_root: Path) -> bool:
    """True for a root-owned, non-writable ``site-packages`` or ``dist-packages``.

    A user-owned directory that merely uses one of those names is a checkout
    and is measured in full. The caller still re-checks on the root side.
    """
    if src_root.name not in {"site-packages", "dist-packages"}:
        return False
    try:
        info = src_root.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    writable = stat.S_IMODE(info.st_mode) & 0o022
    return info.st_uid == 0 and writable == 0


def system_digest(src_root: Path) -> str:
    """Digest the identity of a root-owned install, not every neighboring package."""
    if not is_system_managed(src_root):
        raise SealError(f"source root is not a root-owned system install ({src_root})")
    info = src_root.lstat()
    payload = (
        f"system-managed\n{info.st_uid}\n{stat.S_IMODE(info.st_mode):04o}\n{src_root.resolve()}\n"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _real_source_root(src_root: Path) -> Path:
    """Resolve *src_root* after refusing a symlink or a non-directory."""
    if src_root.is_symlink():
        raise SealError(f"source root is a symlink ({src_root})")
    if not src_root.is_dir():
        raise SealError(f"source root is not a directory ({src_root})")
    return src_root.resolve()


def measure(src_root: Path) -> str:
    """Return the sha256 the authorized command must carry for *src_root*."""
    root = _real_source_root(src_root)
    if is_system_managed(root):
        return system_digest(root)
    digest, _files = _read_checkout(root)
    return digest


def materialize(src_root: Path, expected: str) -> Path | None:
    """Re-measure *src_root* and copy a matching checkout into a private directory.

    Returns ``None`` for a root-owned system install (import that path itself).
    Raises :class:`SealError` when the digest does not match or the tree is
    not safe. The copy contains the bytes that were hashed, so a rewrite of
    the checkout after the read cannot change what gets imported.
    """
    if not _valid_digest(expected):
        raise SealError("checkout digest is invalid")
    root = _real_source_root(src_root)
    if is_system_managed(root):
        actual = system_digest(root)
        if not _matches(actual, expected):
            raise SealError("system install no longer matches the authorized digest")
        return None
    digest, files = _read_checkout(root)
    if not _matches(digest, expected):
        raise SealError("checkout changed after administrator approval")
    return _write_private(files)


def _read_checkout(root: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """Hash importable files and directory modes under *root*. Refuse symlinks."""
    records: list[str] = []
    files: list[tuple[str, bytes]] = []
    _scan_dir(root, root, records, files)
    records.sort()
    text = "\n".join(records) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), files


def _scan_dir(
    root: Path,
    directory: Path,
    records: list[str],
    files: list[tuple[str, bytes]],
) -> None:
    """Record *directory* and scan its entries without following links."""
    records.append(_dir_record(root, directory))
    try:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
    except OSError as exc:
        raise SealError(f"cannot inspect {directory} ({exc})") from exc
    for entry in entries:
        _consider_entry(root, entry, records, files)


def _dir_record(root: Path, directory: Path) -> str:
    """Return the manifest line for a real, non-writable directory."""
    try:
        info = directory.lstat()
    except OSError as exc:
        raise SealError(f"cannot inspect {directory} ({exc})") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SealError(f"symlink directory {directory}")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022:
        raise SealError(f"directory {directory} is group/world-writable (mode {oct(mode)})")
    return f"d {mode:04o} {_rel(root, directory)}"


def _consider_entry(
    root: Path,
    entry: os.DirEntry[str],
    records: list[str],
    files: list[tuple[str, bytes]],
) -> None:
    """Record one directory entry, or skip pruned and non-importable names."""
    name = entry.name
    if name in PRUNE_DIR_NAMES or name.endswith(".egg-info"):
        return
    if entry.is_symlink():
        _refuse_or_skip_symlink(entry)
        return
    try:
        is_dir = entry.is_dir(follow_symlinks=False)
    except OSError as exc:
        raise SealError(f"cannot inspect {entry.path} ({exc})") from exc
    if is_dir:
        _scan_dir(root, Path(entry.path), records, files)
        return
    if Path(name).suffix in _IMPORT_SUFFIXES:
        _read_importable(root, Path(entry.path), records, files)


def _refuse_or_skip_symlink(entry: os.DirEntry[str]) -> None:
    """Refuse directory links and importable names. Ignore other links.

    Matches ``run.sh``: a documentation symlink is not a module, but a
    ``.py`` / ``.pyc`` / ``.so`` link (or a linked package directory) is
    importable and its target mode bits prove nothing.
    """
    try:
        followed = os.stat(entry.path)
    except OSError:
        followed = None
    if followed is not None and stat.S_ISDIR(followed.st_mode):
        raise SealError(f"symlink directory {entry.path}")
    if Path(entry.name).suffix in _IMPORT_SUFFIXES:
        raise SealError(f"importable symlink {entry.path}")


def _read_importable(
    root: Path,
    path: Path,
    records: list[str],
    files: list[tuple[str, bytes]],
) -> None:
    """Read one regular importable file without following a link. Record its bytes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise SealError(f"cannot inspect {path} ({exc})") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SealError(f"importable path is not a regular file ({path})")
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o022:
            raise SealError(f"{path} is group/world-writable (mode {oct(mode)})")
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    content = hashlib.sha256(data).hexdigest()
    records.append(f"f {mode:04o} {content} {_rel(root, path)}")
    files.append((_rel(root, path), data))


def _write_private(files: list[tuple[str, bytes]]) -> Path:
    """Write *files* into a new mode-0700 directory and return that directory."""
    dest = Path(tempfile.mkdtemp(prefix=_PRIVATE_PREFIX))
    os.chmod(dest, 0o700)
    try:
        for rel, data in files:
            _write_sealed_file(dest, rel, data)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def _write_sealed_file(dest: Path, rel: str, data: bytes) -> None:
    """Create ``dest/rel`` as a new mode-0644 file containing *data*."""
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise SealError("invalid relative path in checkout seal")
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SealError("short write while sealing checkout")
            view = view[written:]
    finally:
        os.close(fd)


def assert_root_owned_interpreter(executable: Path) -> None:
    """Refuse a Python the unprivileged user can replace while the dialog is open.

    The seal runs inside that interpreter. A user-owned binary (pyenv,
    Homebrew) can be swapped for the whole wait, so the dialog path requires
    a root-owned interpreter with no group/world-writable ancestor. Launch
    with ``sudo ./run.sh`` otherwise; that path checks the tree after sudo
    returns and then execs immediately.
    """
    try:
        resolved = executable.resolve(strict=True)
    except OSError as exc:
        raise SealError(f"cannot resolve Python interpreter ({exc})") from exc
    _assert_root_owned_chain(resolved)


def _assert_root_owned_chain(resolved: Path) -> None:
    """Walk *resolved* and its ancestors. All must be root-owned and not writable."""
    current = resolved
    while True:
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SealError(f"cannot inspect {current} ({exc})") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SealError(f"Python interpreter path contains a symlink ({current})")
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != 0 or mode & 0o022:
            raise SealError(
                "Python interpreter is not a root-owned, non-writable path "
                f"({resolved}). Launch with sudo ./run.sh"
            )
        parent = current.parent
        if parent == current:
            return
        current = parent


def bootstrap_main() -> int:
    """Verify argv, load the sealed tree, and call the privileged ``main``."""
    try:
        return _run_bootstrap()
    except SealError as exc:
        print(f"Refusing to elevate: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 4


def _run_bootstrap() -> int:
    """Parse the authorized argv and hand off to :func:`_launch`.

    ``sys.argv`` is ``[-c, base64-source, src_root, digest, module, ...privileged]``.
    The base64 slot is the source already executing; it is not read back here.
    """
    argv = sys.argv
    if len(argv) < 6:
        raise SealError("privileged bootstrap arguments are incomplete")
    module = argv[4]
    if module != _PRIVILEGED_MODULE:
        raise SealError("privileged module is not dislocker_ui.privileged")
    return _launch(Path(argv[2]), argv[3], module, argv[5:])


def _launch(src: Path, expected: str, module: str, rest: list[str]) -> int:
    """Import *module* from the sealed tree and return ``main()``."""
    private = materialize(src, expected)
    entry = str(src.resolve() if private is None else private)
    user_root = str(src.resolve())
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONHOME", None)
    sys.path = [entry, *[p for p in sys.path if p not in {"", entry, user_root}]]
    sys.argv = [module, *rest]
    try:
        imported = importlib.import_module(module)
        main = getattr(imported, "main", None)
        if not callable(main):
            raise SealError(f"{module} has no main()")
        code = main()
        return int(code) if code is not None else 0
    finally:
        if private is not None:
            shutil.rmtree(private, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(bootstrap_main())
