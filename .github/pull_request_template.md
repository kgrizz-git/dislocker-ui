## Summary
<!-- What changed and why (1–3 bullets). -->

## Test plan
- [ ] `python3 -m compileall -q src`
- [ ] `pre-commit run --all-files` (if hooks installed)
- [ ] Manual mount/unmount smoke test on macOS when mount logic changed

## Checklist
- [ ] No passwords, recovery keys, or secrets in code/logs/commits
- [ ] `VERSION` / changelogs updated when user-visible or harness behavior changes
- [ ] Docs (`README.md` / `AGENTS.md`) updated if commands or conventions changed
