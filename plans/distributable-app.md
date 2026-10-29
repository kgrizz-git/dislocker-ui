# Distributable app (notarized .dmg + guided setup)

Status: **draft / not started**. Branch: `plan/distributable-app`.
Revision 2 (2026-10-29): folded in an external read-only review
(Qoder / DeepSeek-Flash) and a license check of libfuse / macFUSE / FUSE-T.

## Goal

An average macOS user downloads a `.dmg`, installs the app and a signed
toolchain package, and is walked through the remaining one-time steps
(FUSE install + approval). No git checkout, no Homebrew, no Xcode.

Honest ceiling: macFUSE's kernel-extension approval can require a reboot
into Recovery to lower security on Apple Silicon (see
`scripts/install-root-deps.sh` notes). The UI must say so up front.

Mac App Store distribution is out of scope (sandbox forbids root, raw
`/dev/disk*`, and spawning `dislocker-fuse`; FUSE cannot ship via the store).
The maintainer has an Apple Developer Program membership, so **Developer ID
signing + notarization** (Application and Installer certs) is available.

## Gate

Do not start packaging until the 0.7.0 launcher smoke test in `TO_DO.md`
**Next Up** passes — this plan reuses that Terminal-attached elevation path.

## Constraints carried over

- TCC finding from [`double-clickable-app.md`](double-clickable-app.md):
  root spawned via an osascript admin prompt cannot open removable disk
  nodes. Elevation stays **Terminal-attached** (`sudo` in Terminal).
- As root, the GUI only trusts root-owned tools at fixed paths
  (`discover_privileged_deps`: `/usr/local/sbin`, `/opt/local/sbin`).
  Anything installed to `/opt/homebrew/bin` is **invisible** to the root
  GUI. Setup must therefore produce a root-owned toolchain, not a
  `brew install`.
- Do not vendor dislocker **sources** into this repo (AGENTS.md). Building
  and shipping dislocker / ntfs-3g **binaries** (GPL) with a source offer
  is allowed and is a separate release artifact.
- Runtime stays stdlib-only Python.

## Licensing findings (2026-10-29, not legal advice)

Checked primary texts: `macfuse/macfuse` LICENSE.txt,
`macos-fuse-t/fuse-t` License.txt, `macfuse/library` (libfuse fork) repo.

| Component | License | Redistribute in a **free** GPL release? | If **sold** |
|-----------|---------|----------------------------------------|-------------|
| macFUSE package (kext, installer, its binaries) | BSD-style + clause 4: no bundling with commercial software, incl. automated download/install, without written permission | Bundling not needed and not planned; link to official installer | Needs written permission even to auto-download/install |
| macFUSE libfuse **source** (`macfuse/library`) | LGPL-2.1 for the library (`COPYING.LIB`); GPL-2 for utilities | Yes, if we build it ourselves from source: ship notices, source offer, dynamic link (user can replace dylib) | LGPL itself permits it; **unclear** whether macFUSE clause 4 also attaches to the libfuse dylib copied out of macFUSE's binary package — avoid by building from source |
| FUSE-T binary | Free for non-commercial use; commercial use / bundling needs a commercial license | Link to official installer | Commercial license needed (and "commercial use" may cover users, not only bundling — unverified) |
| FUSE-T's libfuse | LGPL (fork of osxfuse/fuse) | Yes, with LGPL obligations | Same as left; FUSE-T runtime still needs its license |
| dislocker | GPL-2.0-or-later (verify at pinned commit) | Yes, binaries + corresponding source | Yes (GPL permits sale), source still required |
| ntfs-3g | GPL-2 (library LGPL) | Yes, binaries + source | Yes, source required |

Consequences:

- **Today's `install-root-deps.sh` copies macFUSE's libfuse dylib** out of
  the user's macFUSE install into `$PREFIX/lib`. That is fine on the
  user's own machine. For a *redistributed* `.pkg`, build libfuse from the
  `macfuse/library` source at a pinned tag instead of copying macFUSE's
  binary, so only LGPL terms apply.
- **Keep the release free** (donations OK) unless written permission is
  obtained from the macFUSE author and/or a FUSE-T commercial license.
  Charging money turns "user installs FUSE themselves" into a grey area
  under both FUSE licenses' "commercial" wording.
- Never bundle or silently download macFUSE or FUSE-T; the setup panel
  links to their official pages and the user installs them.

## Architecture (revised)

Release artifact: one notarized `.dmg` containing

1. **`Dislocker UI.app`** — thin launcher + bundled Python (option A1).
2. **`Dislocker UI Tools.pkg`** — signed (Developer ID Installer) and
   notarized; installs prebuilt, root-owned `dislocker-fuse`, `ntfs-3g`,
   and the self-built LGPL libfuse into `/opt/local/sbin` + `/opt/local/lib`
   (paths the root GUI already trusts). Built in CI from pinned commits
   with SHA-256-verified sources, mirroring `install-root-deps.sh` build
   flags (`-DFUSE_DARWIN_ENABLE_EXTENSIONS=0`, fuse3, mbedtls@3 static).
3. `LICENSES/` + `SOURCE.txt` (source-offer URLs for every GPL/LGPL
   binary, pointing at tagged source tarballs attached to the release).
4. Uninstaller script for the pkg payload (pkgutil receipts + file list).

`install-root-deps.sh` stays for source-checkout users; the pkg is the
download path. Both must produce the same layout.

### A. App packaging options

| Option | How | Pros | Cons |
|--------|-----|------|------|
| **A1. Thin launcher .app + bundled Python** (recommended) | `Contents/MacOS/launcher` (prefer a tiny signed Mach-O; shell script only if spike shows Gatekeeper accepts it) runs `open -a Terminal` on a bundled `.command`, which verifies the bundle then `sudo`s the bundled interpreter. Python from python-build-standalone (PBS) in `Contents/Resources/python`. | No system Python/CLT; no py2app; proven elevation path | ~40–60 MB; sign every Mach-O; track PBS for CVEs |
| A2. Launcher .app, system Python | Uses `/usr/bin/python3` | Tiny | CLT stub on clean Macs; old Apple Tk |
| A3. py2app / PyInstaller | Frozen app | Familiar | Previously rejected (Tk fragility); still needs Terminal handoff |

### Security requirements for the root-run bundle

- **Pinned signature check, before and after sudo.** In the unprivileged
  `.command` and again as root: `codesign --verify --strict` against a
  designated requirement pinning our **Team ID** (`-R '=anchor apple
  generic and certificate leaf[subject.OU] = "<TEAMID>"'`), plus
  `spctl -a -t exec`. A bare `codesign --verify` accepts any signer.
  Avoid `--deep`; sign inside-out.
- **Ownership:** a Finder-dragged app is owned by the user, so a
  "refuse if user-writable" gate would always fail. Decision: rely on the
  pinned signature check (tampering breaks the seal) and refuse if the
  bundle path or any parent is writable by someone other than root or the
  invoking user. Document the residual TOCTOU window (same class as
  today's repo checkout); re-verify as root immediately before import.
- **Sanitized root environment**, mirroring `run.sh` / `elevate.py`:
  `sudo -- /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=…
  SUDO_UID=… SUDO_GID=… <bundle>/python3 -I -B -m dislocker_ui`
  (`-I` isolated: ignores `PYTHON*` env and user site; `-B` no
  `__pycache__` writes, which would also break the signature). No
  `DYLD_*` passes through (`env -i`; hardened runtime also strips them).
- **No writes into the bundle** at runtime; ship precompiled `.pyc`
  inside the signed seal or none at all.
- **App Translocation:** if launched from the dmg or a quarantined
  Downloads copy, the path is under `AppTranslocation`; detect and show
  "Move to Applications first" instead of handing a translocated path
  to Terminal.
- **Architecture:** decide arm64-only vs universal2 (PBS has both;
  combine with `lipo`). Set `LSMinimumSystemVersion` ≥ 12.3 (launcher
  already relies on `readlink -f`).

### B. Guided first-run setup

New non-GUI module `setup_check.py` returns ordered steps with status;
`gui.py` renders a setup panel before the main UI (also Help → Setup).

1. **FUSE layer** — detect macFUSE (≥4.10 with fuse3) or FUSE-T (if the
   spike passes). Open the official download page; show the kext approval
   steps (System Settings → Privacy & Security; Recovery reduced-security
   on Apple Silicon). Never auto-download.
2. **Toolchain pkg** — detect root-owned tools in `/opt/local/sbin`.
   If missing, offer "Open installer" for the bundled `.pkg`.
3. **Advisory detection** — also look in `/opt/homebrew/bin`,
   `/usr/local/bin` and report "installed but not trusted (not root-owned
   in a trusted path)" distinctly from "not installed".
4. **ntfs-3g** — required for NTFS mounts (included in the pkg).
5. **Recheck**; proceed when required deps are present.

### C. (Deferred) SMAppService privileged helper

No Terminal window. Needs an XPC helper (likely Swift), rewrite of
`elevate.py` / `privileged.py`; **unverified** against the TCC
removable-volume gate. Not part of this plan.

## FUSE-T validation spike

Question: does dislocker built against FUSE-T's libfuse mount a BitLocker
volume, and can ntfs-3g (also FUSE) and `mount_msdos` / `mount_exfat`
mount the resulting `dislocker-file`? **Unconfirmed**; ntfs-3g-on-FUSE-T
has open upstream reports (tuxera/ntfs-3g#130).

Extra concerns from review:
- FUSE-T serves the filesystem over a **loopback NFS server**. A root-run
  `dislocker-fuse` would then run that server as root — evaluate
  exposure (bind address, auth) before recommending it.
- Adopting FUSE-T needs a second build recipe (no macFUSE `fuse3.pc`,
  different libfuse), i.e. two toolchain pkgs or a runtime choice.

Environment (Docker cannot run macOS):
- **Host test on this Mac** (preferred, no Windows needed): install FUSE-T
  alongside macFUSE; build dislocker/ntfs-3g into a separate prefix under
  `tmp/` (never over `/usr/local/sbin`). Test with the real drive by
  pointing the binaries at it directly. Rollback: uninstall FUSE-T; the
  existing macFUSE toolchain is untouched (coexistence unverified —
  confirm first).
- **macOS VM** (tart/UTM) if isolation is wanted; needs a throwaway
  BitLocker image made on Windows (stored under `tmp/`, never committed).

Checklist:
- [ ] Confirm FUSE-T + macFUSE coexist
- [ ] Build dislocker + ntfs-3g against FUSE-T into `tmp/fuse-t-prefix`
- [ ] RO mount: NTFS via ntfs-3g, FAT/exFAT via system helpers
- [ ] Clean unmount via `unmount_steps.py` flow
- [ ] Assess root NFS server exposure
- [ ] Record result; pick default FUSE layer

## Release pipeline

Tag `vX.Y.Z` (matches `VERSION`) → GitHub Actions `macos` runner:

1. Run existing checks.
2. Fetch PBS + dislocker + ntfs-3g + libfuse sources at pinned versions;
   verify SHA-256 (supply chain).
3. Build toolchain into a staging root; build `.pkg` (`pkgbuild` /
   `productbuild`), sign with Developer ID Installer.
4. Assemble `.app` (`scripts/build-app.sh`); CI smoke: bundled
   `python3 -I -c "import tkinter; tkinter.Tcl()"` and `otool -L` shows
   no system Tk/Python.
5. Temporary keychain: create, import cert, `set-key-partition-list`,
   delete at end. Sign all Mach-O inside-out with
   `--options runtime --timestamp`, then the bundle.
6. `hdiutil` dmg (app + pkg + licenses + uninstaller), sign it.
7. `xcrun notarytool submit --wait` (App Store Connect API key),
   `xcrun stapler staple`, then `stapler validate` + `spctl -a -vv`.
8. Upload to a GitHub Release with the `CHANGELOG.md` section, source
   tarballs, and SHA-256 sums.

Secrets (GitHub Actions, names TBD): Developer ID Application cert and
Developer ID Installer cert (.p12, base64) and passphrase; App Store
Connect API key ID, issuer ID, and .p8. Never commit them.

Optional later: Homebrew cask in a personal tap pointing at the release;
update check (manual re-download for now).

## Checklist

- [ ] Gate: 0.7.0 launcher smoke test passes
- [ ] FUSE-T spike
- [ ] Decide arm64-only vs universal2
- [ ] CI toolchain build (pinned, hashed) incl. self-built LGPL libfuse
- [ ] Signed + notarized toolchain `.pkg` and uninstaller
- [ ] `scripts/build-app.sh`: Mach-O launcher, bundled `.command`, PBS
- [ ] Pinned-signature + env sanitization + translocation checks;
      security review
- [ ] `setup_check.py` + tests; setup panel in `gui.py`; help text
- [ ] LICENSES / third-party notices / source offer in dmg + release
- [ ] Local signing + notarization dry run
- [ ] `release.yml` workflow + secrets
- [ ] README "Download" section; CHANGELOG + VERSION bump (0.8.0)
- [ ] Manual smoke test on a clean macOS (VM) from download to mount

## Risks

- Root-run bundled interpreter: tampering mitigated by pinned signature
  check; residual TOCTOU documented.
- macFUSE approval remains a hard UX step if FUSE-T fails.
- License drift: re-check macFUSE / FUSE-T terms each release; do not
  charge without written permission.
- Maintaining a second (pkg) build path alongside `install-root-deps.sh`.
