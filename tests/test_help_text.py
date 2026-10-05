"""Tests for the in-app help strings."""

from __future__ import annotations

import pytest

from dislocker_ui.help_text import (
    ABOUT_TEXT,
    REPO_URL,
    TROUBLESHOOTING_TEXT,
    USAGE_TEXT,
    about_text,
)

_ALL_TEXTS = (ABOUT_TEXT, USAGE_TEXT, TROUBLESHOOTING_TEXT)


@pytest.mark.parametrize("text", _ALL_TEXTS)
def test_help_constants_are_non_empty(text: str) -> None:
    """Every help dialog has real content."""
    assert text.strip()


def test_about_text_includes_version_license_and_url() -> None:
    """The About dialog names the running version, license, and repo."""
    assert "{version}" in ABOUT_TEXT
    rendered = about_text("9.9.9")
    assert "9.9.9" in rendered
    assert "{version}" not in rendered
    assert "GPL-3.0-or-later" in rendered
    assert REPO_URL in rendered
    assert REPO_URL == "https://github.com/kgrizz-git/dislocker-ui"


def test_usage_text_covers_mount_flow() -> None:
    """Usage condenses the README steps."""
    for fragment in ("sudo ./run.sh", "Mount", "Unmount"):
        assert fragment in USAGE_TEXT


def test_troubleshooting_covers_stale_session_recovery() -> None:
    """The stale-session entry keeps the guard and the manual command."""
    assert "confirm" in TROUBLESHOOTING_TEXT
    assert "hdiutil info" in TROUBLESHOOTING_TEXT
    assert "sudo rm" in TROUBLESHOOTING_TEXT
    assert "active_session.json" in TROUBLESHOOTING_TEXT
    assert "/var/db/dislocker-ui/$(id -u)/active_session.json" in TROUBLESHOOTING_TEXT


@pytest.mark.parametrize("text", (*_ALL_TEXTS, REPO_URL))
def test_help_text_has_no_machine_paths(text: str) -> None:
    """Help strings never name a developer machine."""
    assert "/Users/" not in text
    assert "/home/" not in text


@pytest.mark.parametrize("text", _ALL_TEXTS)
def test_help_text_has_no_secret_patterns(text: str) -> None:
    """Help strings carry no credential-shaped content."""
    lowered = text.lower()
    for pattern in ("password=", "secret=", "--bekfile", "begin private key", "sk-"):
        assert pattern not in lowered


@pytest.mark.parametrize("text", _ALL_TEXTS)
def test_help_text_lines_stay_short(text: str) -> None:
    """Prose wraps near 72 columns; shell commands stay intact."""
    for line in text.splitlines():
        assert len(line) <= 72 or line.startswith("sudo "), line
