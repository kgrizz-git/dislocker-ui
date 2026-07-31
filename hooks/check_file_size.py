#!/usr/bin/env python3
"""
Enforce soft/hard line-count caps on source and docs (slim harness).

Overall purpose:
  Keep modules reviewable for humans and agents. Soft warnings are advisory;
  hard violations fail the hook / CI.

Inputs:
  Optional file paths (pre-commit). With no args, scans git-tracked files whose
  extensions are in the source/doc sets.

Outputs:
  Exit 0 when no hard violations (soft warnings may still print).
  Exit 1 when any hard line-count or size violation is found.

Requirements:
  Python 3.10+, git on PATH. Stdlib only.

Thresholds (env overrides):
  POLICY_SOFT_LINE_CAP   default 600
  POLICY_HARD_LINE_CAP   default 800
  POLICY_DOC_SOFT_LINE_CAP default 1000
  POLICY_DOC_HARD_LINE_CAP default 2000
  POLICY_MAX_BYTES       default 512000 (500 KiB)
  POLICY_WARN_AS_ERROR   set to 1 to promote soft warnings to errors

Per-file override (first 12 lines):
  # policy:file-size allow=1200 reason=generated-tables
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SOFT_LINE_CAP = int(os.getenv("POLICY_SOFT_LINE_CAP", "600"))
HARD_LINE_CAP = int(os.getenv("POLICY_HARD_LINE_CAP", "800"))
DOC_SOFT_LINE_CAP = int(os.getenv("POLICY_DOC_SOFT_LINE_CAP", "1000"))
DOC_HARD_LINE_CAP = int(os.getenv("POLICY_DOC_HARD_LINE_CAP", "2000"))
MAX_BYTES = int(os.getenv("POLICY_MAX_BYTES", str(500 * 1024)))
WARN_AS_ERROR = os.getenv("POLICY_WARN_AS_ERROR", "0") == "1"

SOURCE_EXTS = {".py", ".sh", ".bash", ".zsh", ".toml"}
DOC_EXTS = {".md", ".rst", ".txt", ".yaml", ".yml"}
OVERRIDE_RE = re.compile(r"policy:file-size\s+allow=(\d+)", re.IGNORECASE)

IGNORE_FRAGMENTS = (
    ".context/",
    "node_modules/",
    "__pycache__/",
    ".git/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    "tmp/",
    "LICENSE",
)


def _repo_root() -> Path:
    """Return git toplevel or this script's parent directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    return Path(__file__).resolve().parents[1]


def _tracked_files(root: Path) -> list[Path]:
    """List tracked files via git ls-files."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    return [
        root / rel for rel in result.stdout.decode("utf-8", errors="replace").split("\0") if rel
    ]


def _ignored(rel: str) -> bool:
    """Return True when *rel* should skip size checks."""
    normalized = rel.replace("\\", "/")
    if normalized == "LICENSE" or normalized.endswith("/LICENSE"):
        return True
    return any(frag in normalized for frag in IGNORE_FRAGMENTS)


def _override_cap(path: Path) -> int | None:
    """Return per-file allow=N override if present near the top of the file."""
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for index, line in enumerate(handle):
                if index >= 12:
                    break
                match = OVERRIDE_RE.search(line)
                if match:
                    return int(match.group(1))
    except OSError:
        return None
    return None


def _check_file(path: Path, rel: str) -> tuple[list[str], list[str]]:
    """Return (hard_errors, soft_warnings) for one file."""
    errors: list[str] = []
    warnings: list[str] = []
    if not path.is_file() or _ignored(rel):
        return errors, warnings

    size = path.stat().st_size
    if size > MAX_BYTES:
        errors.append(f"{rel}: {size} bytes exceeds max {MAX_BYTES}")
        return errors, warnings

    ext = path.suffix.lower()
    if ext not in SOURCE_EXTS and ext not in DOC_EXTS:
        return errors, warnings

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return errors, warnings

    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    override = _override_cap(path)

    if ext in DOC_EXTS:
        soft, hard = DOC_SOFT_LINE_CAP, DOC_HARD_LINE_CAP
    else:
        soft, hard = SOFT_LINE_CAP, HARD_LINE_CAP

    if override is not None:
        hard = max(hard, override)
        soft = min(soft, hard)

    if lines > hard:
        errors.append(f"{rel}: {lines} lines exceeds hard cap {hard}")
    elif lines > soft:
        warnings.append(f"{rel}: {lines} lines exceeds soft cap {soft}")

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    """CLI entry for pre-commit / CI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Files to check")
    args = parser.parse_args(argv)

    root = _repo_root()
    candidates = [Path(p) for p in args.files] if args.files else _tracked_files(root)

    all_errors: list[str] = []
    all_warnings: list[str] = []
    for path in candidates:
        resolved = path if path.is_absolute() else root / path
        try:
            rel = str(resolved.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = str(path)
        errs, warns = _check_file(resolved, rel.replace("\\", "/"))
        all_errors.extend(errs)
        all_warnings.extend(warns)

    for warning in all_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in all_errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if all_errors or (WARN_AS_ERROR and all_warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
