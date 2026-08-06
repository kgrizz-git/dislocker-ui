# TO_DO

Open work only for **dislocker-ui**.

## Rules (agents)

- Add new work here when it is actually queued.
- Link the matching plan under [`plans/`](plans/) when one exists.
- **When an item is finished, delete it from this file** — do not accumulate checked-off history here.
- When an entire plan is finished, move it to [`plans/archive/`](plans/archive/) and remove its section below.
- User-visible completions also need `VERSION` + [`CHANGELOG.md`](CHANGELOG.md); harness-only notes go in [`CHANGELOG.dev.md`](CHANGELOG.dev.md).
- See [`plans/README.md`](plans/README.md) for the full plans workflow.

## Active

- [Privileged helper hardening](plans/2026-08-05-privileged-helper-hardening.md):
  remediate the security audit findings in the elevated mount/unmount workflow.
- [Root-managed dependency installer](plans/2026-08-05-root-deps-install.md):
  write `scripts/install-root-deps.sh`, point the GUI "Privileged tools
  unavailable" dialog at it, and document the one-time root install.
  (Revised 2026-08-05 per PR-review + plan-assessment in `tmp/`: two-phase
  unprivileged/elevated design, pinned commit SHAs, `-Dbindir`/`-DWITH_RUBY=OFF`,
  `PKG_CONFIG_PATH` instead of `--with-fuse`/`-DFUSE_LIBRARY`, recursive
  `otool -L` transitive-dylib gate, `/opt/local` default on both arches.)
