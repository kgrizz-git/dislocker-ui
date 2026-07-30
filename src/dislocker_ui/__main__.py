"""
Entry point for `python3 -m dislocker_ui`.

Overall purpose:
  Launch the dislocker-ui GUI.

Inputs:
  None (GUI-driven).

Outputs:
  Runs the Tk event loop until the window closes.

Requirements:
  Same as gui.py / README.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    """Allow running this file directly without installing the package."""
    src = Path(__file__).resolve().parents[1]
    src_str = str(src)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


def main() -> None:
    """Start the application."""
    _ensure_src_on_path()
    from dislocker_ui.gui import run_app

    run_app()


if __name__ == "__main__":
    main()
