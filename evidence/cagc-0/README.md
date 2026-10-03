# CAGC-0 deterministic evidence (KRIYA_CAGC v0.7)

Branch `feature/cagc-r1`, base origin/main `0bea22f` (tree `e6f5c4c`), code head `b7cd737` (tree `d3d43ea`).
Live A/B: **not run** - stopped at the SEC-009 owner checkpoint (`ab/MANIFEST.md`).

| Commit | Content |
|---|---|
| 924fee7 | 1/5 framework, facts, selectors, render/refit (+131 tests) |
| 437f8ee | 2/5 canonical-fit integration (+17 tests) |
| 6b1a4d9 | 3/5 request wiring per role, final-fit telemetry (+7 end-to-end tests) |
| 1d8a218 | 4/5 stack-neutral base prompts, migration/deletion, tripwire (+15 tests) |
| 2522fda | 5/5 docs, registry, resume-identity test |
| b7cd737 | own defect in 1d8a218: two review-batching fixtures re-derived for the shorter Reviewer prompt |

## Gates (MEASURED)

- Full parallel suite at 2522fda: 8542 passed, 2 failed (`full_suite_2522fda_tail.txt`). Both failures traced to
  1d8a218's shorter Reviewer system prompt (they pass at 6b1a4d9), fixed in b7cd737 without changing an assertion.
- Full parallel suite at b7cd737: **8544 passed, 0 failed** (`full_suite_b7cd737_tail.txt`).
- ruff (tracked files) clean; `pylint kriya plugins/core_tools tests` exit 0.
- doctor --production (Spring XML bench workspace, v5-derived config): b7cd737 is identical, row for row, to the
  Spring XML closure baseline (`evidence/spring-xml-closure/doctor_v5_statuses.txt`; `context.recall_certification`
  FAIL pre-existing). The 0bea22f run (from a scratch worktree) differs only in `persistence.traces` /
  `runtime.fixed_guarantees` PASS vs WARN: the legacy `<install dir>/logs/traces.db` check, whose install dir was the
  scratch worktree (no legacy db) - a measurement artifact, not a CAGC change.

## Mutations (`mutations/`, run with `mutate.py <spec>`)

| Spec | Killed | Survivors |
|---|---|---|
| commit1_framework | 34/35 | `is_dir(follow_symlinks=False)` -> `is_dir()`: equivalent (a symlink is handled by the `is_symlink` branch first) |
| commit2_fit | 16/17 | rebuilding from the previous probe's block: equivalent by design (`refit_guidance` reads only the immutable base entries, which a refit block carries unchanged); one redundant line found by mutation was removed |
| commit3_wiring | 8/8 | - |
| commit4_tripwire | 5/5 | - |

## Prompt text migrated or deleted (MEASURED by AST scan of string constants)

Base prompt sizes (chars, 0bea22f -> b7cd737): Planner 11261->10632, Architect 2826->1985, Developer 2680->2489,
Reviewer 6139->5803, Milestone Planner 4976->4917, Run Verification Judge 10705->10295, structured Planner system
6803->6646. Incident narratives found in model-facing string constants: 4 (Planner, Architect, Run Verification
Judge, structured Planner), all deleted; the spec's count of 8 (6 + 2) is not reproduced by a string-constant scan
(the others are code comments/docstrings, never sent to a model).
