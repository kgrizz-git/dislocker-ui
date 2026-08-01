# Plans

Tracked implementation plans for **dislocker-ui**.

## Layout

| Path | Purpose |
|------|---------|
| `plans/*.md` | Active plans (work not yet fully shipped) |
| `plans/archive/` | Completed plans (kept for history) |
| [`TO_DO.md`](../TO_DO.md) | Open checklist only — current work |
| [`CHANGELOG.md`](../CHANGELOG.md) | User-visible product changes (semver) |
| [`CHANGELOG.dev.md`](../CHANGELOG.dev.md) | Harness / CI / docs-only notes |

## How agents should use this

1. **Before implementing a multi-step feature**, write or update a plan under `plans/` (one file per effort, kebab-case name).
2. **Mirror open checklist items** into root [`TO_DO.md`](../TO_DO.md). Link the plan from the TO_DO section.
3. **While working**, check off items in the plan file if useful; keep `TO_DO.md` as the live queue.
4. **When a checklist item is done**, remove it from `TO_DO.md` (do not leave a long history of completed checkboxes there).
5. **When the whole plan is done** (merged / released / abandoned):
   - Move `plans/<name>.md` → `plans/archive/<name>.md` (optionally prefix with completion date: `2026-07-31-macos-elevation.md`).
   - Strip any remaining TO_DO entries for that plan.
   - Record user-visible impact in `CHANGELOG.md` + `VERSION`; harness-only notes in `CHANGELOG.dev.md`.
6. **Do not** put secrets, recovery keys, or machine-specific home paths in plans.
7. **Scratch / throwaway notes** still go under `.context/` (gitignored), not here.

## Active plans

_(none)_
