# TEST-WORKTREE-POLLUTION-001 — test suite writes npm artifacts into the working tree (recorded, NOT fixed)

Status: OPEN. Classification: **TEST-SUITE SIDE EFFECT — LIKELY; NOT YET ROOT-CAUSE CONFIRMED.**
Deferred by owner decision until after the UI integration baseline is established. No investigation or fix attempted.

## Observed (MEASURED, 2026-10-07)

- A fresh worktree (`~/kriya-wt/integration-graphify`, created from origin/main and merged to be3cbb2) had a clean
  `git status --porcelain` after the merge.
- After one full test-suite run in it (`pytest -q -n 8 --dist loadgroup`, cwd = that worktree), it had three new
  untracked entries: `node_modules/`, `package.json`, `package-lock.json`.
- Package observed: `left-pad` (`package.json`: `{"dependencies": {"left-pad": "^1.3.0"}}`); `package-lock.json`
  differs between worktrees only in its `"name"` field (= the directory name); `node_modules/` holds one package.
- The same three entries are present in the main repository and in the other Kriya worktrees, consistent with
  earlier full-suite runs there.

## Impact

- Tests mutate the repository/worktree outside expected test artifacts.
- Clean worktrees appear to have untracked content; this interfered with branch/worktree cleanup during the
  milestone integration (worktrees had to be archived before removal).
- Local state may be non-reproducible.

## Not yet known

Which test (or fixture) runs an npm install in the process working directory, and whether it is deliberate (e.g. a
package-acquisition test) but mis-scoped (should use tmp_path).
