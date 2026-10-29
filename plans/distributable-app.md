# Distributable app (notarized .dmg + guided setup)

Status: **draft / not started**. Branch: `plan/distributable-app`.

## Goal

An average macOS user downloads a `.dmg`, drags **Dislocker UI.app** to
Applications, double-clicks it, and is walked through one-time dependency
setup. No git checkout, no hand-typed commands beyond what macOS forces.

Mac App Store distribution is out of scope (sandbox forbids root, raw
`/dev/disk*`, and spawning `dislocker-fuse`; FUSE cannot ship via the store).
The maintainer has an Apple Developer Program membership, so **Developer ID
signing + notarization** is available.

## Constraints carried over

- TCC finding from [`double-clickable-app.md`](double-clickable-app.md):
  root spawned via an osascript admin prompt cannot open removable disk
  nodes. Elevation must stay **Terminal-attached** (`sudo` in Terminal).
- Do not bundle macFUSE (license forbids redistribution; system extension
  needs user approval anyway). Do not vendor dislocker sources (AGENTS.md).
- Runtime stays stdlib-only Python.

## Options

### A. Packaging (pick one sub-option)

| Option | How | Pros | Cons |
|--------|-----|------|------|
| **A1. Thin launcher .app + bundled Python** (recommended) | `.app/Contents/MacOS/launcher` (shell) runs `open -a Terminal` on a bundled `.command` that `sudo`s the bundled interpreter with `-m dislocker_ui`. Python from python-build-standalone (includes Tk) inside `Contents/Resources/python`. | No dependence on system Python/CLT; no py2app; same proven elevation path | ~40–60 MB; must sign every Mach-O in the runtime; refresh Python for CVEs |
| A2. Thin launcher .app, system Python | Same launcher, uses `/usr/bin/python3` | Tiny bundle | `/usr/bin/python3` is a CLT stub on clean Macs; Apple Tk is old/buggy |
| A3. py2app / PyInstaller | Frozen app | Familiar tooling | Plan already rejected: Tk fragility, new build dep; still needs Terminal handoff for elevation |

`run.sh` hardening (root-owned/ non-writable source checks) must be
re-expressed for the bundle: verify the signed bundle path instead of a
repo checkout, and refuse if `Contents/` is user-writable when running as
root. **Security review required** — a root-run interpreter in a
user-writable `/Applications` copy is the main new risk.

### B. Guided first-run setup

Extend `deps.py` detection into a setup panel shown before the main UI
when anything is missing (also reachable from Help → Setup):

1. **FUSE layer** — detect macFUSE or FUSE-T. Offer buttons that open the
   official download pages (no bundling). Show the Apple Silicon
   system-extension approval steps for macFUSE (may require Recovery →
   reduced security). Prefer FUSE-T if validation below succeeds.
2. **Homebrew** — detect; if missing, show the official one-line installer
   with a Copy button (we do not run it for the user).
3. **dislocker** — show `brew install …` command (tap TBD; upstream
   formula targets macFUSE). Copy button + Recheck.
4. **ntfs-3g** — optional, only for RW NTFS.
5. **Recheck** button; proceed when required deps are present.

Keep the panel logic in a non-GUI module (`setup_check.py`) returning a
list of steps with status, so it stays testable; `gui.py` only renders.

### C. (Deferred) SMAppService privileged helper

No Terminal window, true one-click. Requires XPC helper (likely Swift),
rewrite of `elevate.py`/`privileged.py`, and is **unverified** against the
TCC removable-volume gate. Not part of this plan.

## FUSE-T validation spike

Question: does `dislocker-fuse` built against FUSE-T mount a BitLocker
volume and expose `dislocker-file` that `ntfs-3g` / `mount_msdos` /
`mount_exfat` can mount? Web sources claim it works; **unconfirmed**.

**Docker cannot test this.** Docker on macOS runs a Linux VM; FUSE-T is a
macOS-only NFS/SMB-based FUSE implementation and there are no macOS
containers. Options:

- **Host test (simplest):** install FUSE-T on the dev Mac alongside / in
  place of macFUSE, rebuild dislocker against it, run the real-drive
  smoke test. Risk: perturbs the working macFUSE setup.
- **macOS VM (isolated):** UTM or `tart` on Apple Silicon (macOS guests
  are allowed under Apple's license on Apple hardware). USB passthrough
  of a macOS guest is limited, so test against a **BitLocker image file**
  (dislocker accepts image files via `-V`). Needs a test image created on
  Windows (e.g. a small BitLocker-encrypted VHD/raw image with a known
  throwaway password — never a real key) — store under `tmp/`, not git.

Spike checklist:
- [ ] Create throwaway BitLocker test image (NTFS + FAT32/exFAT variants)
- [ ] Spin up macOS VM (tart/UTM), install FUSE-T + Homebrew
- [ ] Build dislocker against FUSE-T (record exact flags/patches)
- [ ] Mount image RO via dislocker-fuse; mount NTFS (ntfs-3g needs FUSE
      too — check it works on FUSE-T) and FAT/exFAT
- [ ] Unmount cleanly via `unmount_steps.py` flow
- [ ] Record result here; decide FUSE-T vs macFUSE as the recommended path

## Release pipeline

- Tag `vX.Y.Z` (matches `VERSION`) → GitHub Actions on `macos-latest`:
  1. run existing checks
  2. assemble `.app` (script under `scripts/build-app.sh`)
  3. `codesign --options runtime --timestamp` all Mach-O + bundle
     (Developer ID Application cert from Actions secrets as base64 .p12)
  4. build `.dmg` (`hdiutil`), sign it
  5. `xcrun notarytool submit --wait` (App Store Connect API key secret),
     `xcrun stapler staple`
  6. upload to GitHub Release with `CHANGELOG.md` section as notes
- Secrets (GitHub Actions, names TBD): Developer ID cert (.p12, base64)
  and its passphrase; App Store Connect API key ID, issuer ID, and .p8.
  Never commit them.
- Optional later: Homebrew cask in a personal tap pointing at the release.

## Checklist

- [ ] FUSE-T spike (above)
- [ ] Choose A1 vs A2 (default A1)
- [ ] `scripts/build-app.sh` + launcher + bundled `.command`
- [ ] Bundle-aware root hardening (replace repo checks from `run.sh`) +
      security review
- [ ] `setup_check.py` + tests; setup panel in `gui.py`; help text
- [ ] Local signing + notarization dry run
- [ ] `release.yml` workflow + secrets
- [ ] README "Download" section; CHANGELOG + VERSION bump (0.8.0)
- [ ] Manual smoke test on a clean macOS (VM) from download to mount

## Risks

- Root-run bundled interpreter: bundle tampering if `/Applications` copy is
  user-writable. Mitigate with ownership/mode checks and signature check
  (`codesign --verify`) before `sudo` proceeds.
- macFUSE extension approval remains a hard UX step if FUSE-T fails.
- GPL-3.0: binaries must ship with source offer (link to tagged source).
