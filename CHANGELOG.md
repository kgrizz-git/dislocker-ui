# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-30

### Added

- Initial macOS Python/tkinter frontend for installed `dislocker`.
- Mount / Unmount flow: BitLocker unlock via FUSE, attach raw NTFS image, mount filesystem.
- Unlock methods: user password, recovery password, `.bek` file.
- Read-only mounts by default; optional read/write when `ntfs-3g` is available.
- Dependency detection for `dislocker-fuse`, `hdiutil`, and optional `ntfs-3g`.
- Session state so Unmount can reverse the last successful Mount.
- README install guide for macFUSE and gromgit `dislocker-mac` / `ntfs-3g-mac` (plus mbedtls@3 symlink note when needed).

### Notes

- Does not bundle `dislocker`, macFUSE/FUSE-T, or `ntfs-3g`; those remain system dependencies.
- Writable Finder access on macOS requires a writable NTFS stack (`ntfs-3g` + FUSE), not dislocker alone.
