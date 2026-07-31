"""
Unit tests for disk enumeration helpers.

Overall purpose:
  Parse mocked diskutil plist payloads and size formatting without calling a
  real diskutil binary (so Linux CI can run these tests).

Requirements:
  pytest.
"""

from __future__ import annotations

import plistlib
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.disks import DiskEntry, _fmt_size, list_disk_entries


def test_fmt_size_units() -> None:
    """Byte counts format into short UI strings."""
    assert _fmt_size(512) == "512 B"
    assert _fmt_size(2048).endswith("KB")
    assert _fmt_size(5 * 1024**3).endswith("GB")
    assert _fmt_size("nope") == "?"
    assert _fmt_size(None) == "?"


def test_list_disk_entries_parses_plist() -> None:
    """Partitions and whole disks become DiskEntry values."""
    payload = {
        "AllDisksAndPartitions": [
            {
                "DeviceIdentifier": "disk2",
                "Size": 1_000_000_000,
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk2s1",
                        "VolumeName": "EFI",
                        "Size": 200_000_000,
                    },
                    {
                        "DeviceIdentifier": "disk2s2",
                        "Content": "Microsoft Basic Data",
                        "Size": 800_000_000,
                    },
                ],
            }
        ]
    }
    stdout = plistlib.dumps(payload)
    proc = MagicMock(returncode=0, stdout=stdout, stderr=b"")
    with patch("dislocker_ui.disks.subprocess.run", return_value=proc):
        entries = list_disk_entries()

    assert entries[0] == DiskEntry(
        device="/dev/disk2",
        summary=entries[0].summary,
    )
    assert entries[0].device == "/dev/disk2"
    assert "disk" in entries[0].summary
    assert entries[1].device == "/dev/disk2s1"
    assert "EFI" in entries[1].summary
    assert entries[2].device == "/dev/disk2s2"
    assert "Microsoft Basic Data" in entries[2].summary


def test_list_disk_entries_raises_on_failure() -> None:
    """Non-zero diskutil exit becomes RuntimeError."""
    proc = MagicMock(returncode=1, stdout=b"", stderr=b"diskutil blew up\n")
    with (
        patch("dislocker_ui.disks.subprocess.run", return_value=proc),
        pytest.raises(RuntimeError, match="diskutil blew up"),
    ):
        list_disk_entries()
