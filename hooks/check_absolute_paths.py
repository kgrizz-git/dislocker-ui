#!/usr/bin/env python3
"""
Block machine-specific home-directory paths from entering the repository.

Overall purpose:
  Catch accidental leaks of local usernames / home layouts in tracked text
  (e.g. /Users/alice/..., /home/bob/..., ~/Documents/...). Portable system
  paths such as /tmp, /Volumes, /dev, and /opt/homebrew are allowed.

Inputs:
  Optional file paths (pre-commit passes staged files). With no args, scans
  every tracked text-ish file via `git ls-files`.

Outputs:
  Exit 0 when clean; exit 1 and print file:line hits (matched snippet only,
  never expanded secrets) when violations are found.

Requirements:
  Python 3.10+, git available on PATH. Stdlib only.

Allowlist:
  - This script (documents the patterns it searches for).
  - Lines containing the token ``absolute-path-allow`` (use sparingly).
  - Relative paths listed in ``.absolute-paths-allowlist`` (one path per line).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Placeholder / documentation usernames that may appear in examples.
_SAFE_USER = (
    r"(?:user|username|you|yourname|your-user|YOUR_USER|YOURNAME|"
    r"<[^>\s/]+>|\$\{?(?:USER|USERNAME|LOGNAME)\}?)"
)

# /Users/<name>/... or /home/<name>/... (reject real-looking names).
_HOME_ABS = re.compile(
    rf"(?<![\w.])/(?:Users|home)/(?!{_SAFE_USER}\b)([A-Za-z0-9._-]+)(/[^\s'\"`]*)?"
)

# file:///Users/... or file:///home/...
_FILE_URL = re.compile(rf"(?i)file:///(?:Users|home)/(?!{_SAFE_USER}\b)([A-Za-z0-9._-]+)")

# Windows-style user profiles.
_WIN_USER = re.compile(
    r"(?i)(?<!\w)[A-Z]:\\(?:Users|Documents and Settings)\\"
    rf"(?!{_SAFE_USER}\b)([A-Za-z0-9._-]+)"
)

# Tilde paths that usually mean a real local home layout.
_TILDE_SENSITIVE = re.compile(
    r"(?<!\w)~/(?:\.ssh|\.config|Desktop|Documents|Downloads|Library|MyCode)\b"
)

# Rare but useful: /root/... as a home (not /root for package paths alone — require more).
_ROOT_HOME = re.compile(r"(?<![\w.])/root/(?:\.ssh|\.config|Desktop|Documents)\b")

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ABS_UNIX_HOME", _HOME_ABS),
    ("FILE_URL_HOME", _FILE_URL),
    ("ABS_WIN_HOME", _WIN_USER),
    ("TILDE_HOME", _TILDE_SENSITIVE),
    ("ROOT_HOME", _ROOT_HOME),
)

_SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".pyc",
    ".zip",
    ".gz",
    ".tgz",
    ".dmg",
    ".pkg",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}

_ALLOW_MARKER = "absolute-path-allow"
_ALLOWLIST_FILE = ".absolute-paths-allowlist"
_SELF = Path(__file__).resolve()


def _repo_root() -> Path:
    """Return the git repository root, or this script's parents[1] as fallback."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    return _SELF.parents[1]


def _tracked_files(root: Path) -> list[Path]:
    """List tracked files under *root* via git ls-files."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        print("check_absolute_paths: git ls-files failed", file=sys.stderr)
        return []
    return [
        root / rel for rel in result.stdout.decode("utf-8", errors="replace").split("\0") if rel
    ]


def _load_allowlist(root: Path) -> set[str]:
    """Load repo-relative paths that may contain matching examples."""
    path = root / _ALLOWLIST_FILE
    allowed = {
        str(_SELF.relative_to(root))
        if _SELF.is_relative_to(root)
        else "hooks/check_absolute_paths.py"
    }
    # Always allow this checker (documents patterns).
    allowed.add("hooks/check_absolute_paths.py")
    if not path.is_file():
        return allowed
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        allowed.add(stripped)
    return allowed


def _should_scan(path: Path) -> bool:
    """Return True when *path* is a text-ish file worth scanning."""
    if not path.is_file():
        return False
    return path.suffix.lower() not in _SKIP_SUFFIXES


def _scan_file(path: Path, rel: str) -> list[str]:
    """Return human-readable violation lines for one file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _ALLOW_MARKER in line:
            continue
        for rule_name, pattern in _RULES:
            match = pattern.search(line)
            if not match:
                continue
            snippet = match.group(0)
            # Avoid dumping huge lines; keep the match short.
            if len(snippet) > 80:
                snippet = snippet[:77] + "..."
            hits.append(f"{rel}:{lineno}: [{rule_name}] {snippet}")
    return hits


def main(argv: list[str] | None = None) -> int:
    """CLI entry: scan given files or all tracked files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to scan (default: all git-tracked files)",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    allowlist = _load_allowlist(root)
    candidates = [Path(p) for p in args.files] if args.files else _tracked_files(root)

    violations: list[str] = []
    for path in candidates:
        path = path.resolve() if path.exists() else path
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        if rel in allowlist or rel.replace("\\", "/") in allowlist:
            continue
        if not _should_scan(path if path.is_absolute() else root / path):
            continue
        target = path if path.is_file() else root / path
        violations.extend(_scan_file(target, rel.replace("\\", "/")))

    if not violations:
        return 0

    print(
        "check_absolute_paths: machine-specific home paths found.\n"
        "Use $HOME, Path.home(), repo-relative paths, or placeholders "
        f"(e.g. /Users/{'{user}'}). To allow one line, add "
        f"`{_ALLOW_MARKER}` on that line, or list the file in "
        f"`{_ALLOWLIST_FILE}`.\n",
        file=sys.stderr,
    )
    for item in violations:
        print(f"  {item}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
