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
5. **Archive a finished plan through a pull request.** Prefer the pull request that implements the plan, before that pull request merges. When that pull request has already merged, or a later check such as a smoke test is what finishes the plan, use a small follow-up pull request. An abandoned plan uses its own small pull request. Do not commit the archive directly on `main`.
   - Move `plans/<name>.md` → `plans/archive/YYYY-MM-DD-<name>.md`. The date is the day the plan moves into the archive.
   - Strip any remaining TO_DO entries for that plan.
   - Record user-visible impact in `CHANGELOG.md` + `VERSION`; harness-only notes in `CHANGELOG.dev.md`.
6. **Do not** put secrets, recovery keys, or machine-specific home paths in plans.
7. **Scratch / throwaway notes** still go under `.context/` (gitignored), not here.

## Active plans

- [`double-clickable-app.md`](double-clickable-app.md) — `.command`
  Terminal-attached launcher (shipped in 0.7.0; manual verification
  pending).
