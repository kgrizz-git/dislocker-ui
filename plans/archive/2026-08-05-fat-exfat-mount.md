# BitLocker To Go FAT/ExFAT mount support

## Decision

After `dislocker-fuse` + `hdiutil attach`, probe the decrypted image with
`diskutil info -plist` and mount **NTFS** via `ntfs-3g` (unchanged) or
**FAT/ExFAT** via system `/sbin/mount_msdos` / `/sbin/mount_exfat`.

## Why

BitLocker To Go volumes often wrap FAT32 (diskutil may still show
`Windows_FAT_32` on the encrypted partition). The previous pipeline always
called `ntfs-3g`, which fails with `NTFS signature is missing`.

## Work

- [x] `fs_probe.py` — classify device / fall back to partitions
- [x] `fat_mount.py` — mount_msdos / mount_exfat with uid/gid/mask
- [x] `runner.py` — dispatch after hdiutil attach
- [x] Tests (`test_fs_mount.py`)
- [x] README + CHANGELOG + VERSION 0.4.0
- [x] Clear root-deps TO_DO / archive that plan

## Non-goals

- Changing privileged trust policy or installer
- Dropping the ntfs-3g core dependency (still required for NTFS volumes)
- GUI redesign beyond accurate docs
