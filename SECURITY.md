# Security Policy

This is a **personal, best-effort** project. There is no formal SLA or bounty program.

## Supported versions

Security fixes are applied on a best-effort basis to the latest release on `main`
(see `VERSION`). Older tags are not maintained.

## Reporting a vulnerability

Please **do not** open a public GitHub Issue for security problems.

Prefer one of:

1. **[Private vulnerability reporting](https://github.com/kgrizz-git/dislocker-ui/security/advisories/new)**
   (GitHub Security Advisories), or
2. Contact the maintainer via the email listed on their
   [GitHub profile](https://github.com/kgrizz-git).

Include enough detail to reproduce (OS version, tool versions, steps). **Never**
send BitLocker passwords, recovery keys, `.bek` files, or copies of encrypted
volume contents.

## Scope

**In scope (examples):**

- Accidental logging or persistence of unlock secrets in this repository’s code
- World-readable temp paths or session files that leak unlock material
- Clear flaws in how this GUI invokes `dislocker` / mount tools that create a
  local privilege or data-exposure risk beyond what those tools already require

**Out of scope / upstream:**

- Bugs or CVEs in `dislocker`, macFUSE / FUSE-T, `ntfs-3g`, or macOS
- BitLocker cryptography itself
- Social engineering, physical access, or “user already has admin and the volume
  key” scenarios that are inherent to mounting encrypted disks

## Hardening notes for contributors and reporters

- Do not paste secrets into Issues, PRs, or CI logs.
- Mount operations typically need elevated access to `/dev/disk*`; that is
  expected, not a bypass of BitLocker.
- Pull requests from outside collaborators may be restricted by repository
  settings; security reports remain welcome via private advisory even when
  public PRs are limited.
