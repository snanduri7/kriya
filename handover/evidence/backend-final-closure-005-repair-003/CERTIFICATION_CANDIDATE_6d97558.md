# Third repair cycle - certification candidate 6d97558 (repair/c3-p1, six commits over main 5407a80)

Mode: operator-controlled (owner directive 2026-10-09). Every pytest / gate / mutation run in this record was executed by
the operator and returned to the session; the session ran only targeted diagnostics and (on the operator's explicit
allowance) single-module targeted pytest, never the full suite. Branch policy: main untouched; nothing pushed.

## Executable identity
- Candidate: 6d97558 (tree clean: 0 modified tracked files; untracked clutter unchanged by policy).
- Base: main == origin/main == 5407a80 (the previous cycle's records commit; certified executable 557035d).
- Commits (in order, none amended or squashed):
  03a1a4d fix(enforce)   ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 (P1)
  9bc06d2 fix(checkpoint) WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR (P1, found by the P1-2 reproducer)
  589d89a fix(requirements) SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001 (P1)
  77ece89 records(p2-1)  REGRESSION-ATTRIBUTION-UNAVAILABLE-DETAIL-001 BY_DESIGN + two follow-up rows
  c24bcce fix(validate)  VERIFICATION-UNIT-ENV-FALLBACK-001 (P2, FIX narrowly)
  6d97558 fix(checkpoint) WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR addendum (own defect of 9bc06d2, found by the full suite)
- Production scope: kriya/workflow/attempt.py, checkpoint.py, requirements.py, workflow.py, verification_coordinator.py,
  kriya/tools/validate.py, dependency_execution.py, kriya/capabilities/pip.py (8 files, +364/-104). No change to models,
  profiles, budgets, frozen cohort inputs, authority bundles or external oracles.

## Repository-wide gates on 6d97558 (operator, 2026-10-09)
- scripts/run_full_suite.py on 6d975582e8b19504d90dad4b67f31aa43c8d3c47 (operator rerun, authoritative certification
  run, log repair-003/FULL_SUITE_6d97558.log; start 2026-10-09T18:10:20Z, end 2026-10-09T18:32:35Z):
  `9852 passed, 15 warnings in 1332.84s (0:22:12)`
  `[full-suite] pytest exit 0; repository root gained no package artifacts`
  `end 2026-10-09T18:32:35Z exit=0`
  (The earlier 2026-10-09 full-suite run on 6d97558 reported by the operator as "all green" had no saved log and is
  not cited as evidence.)
- ruff check .: 0 findings
- pylint kriya plugins/core_tools tests: exit 0
- Earlier canonical run on c24bcce: 4 failed / 9846 passed - the four were the hash addendum's defect, fixed in 6d97558.

## Per-slice evidence (all operator-run unless stated)
| Slice | Reproducer pre-fix | Post-fix | Mutants | Adjacent |
|---|---|---|---|---|
| P1-1 03a1a4d | 2 failed (ACCEPTANCE_COVERAGE_INCOMPLETE every attempt) | 31/31 | M1/M2/M3 killed 6/4/6 | step-2 modules green |
| hash 9bc06d2 | 3 None-hash cases | 8/8 | H1 killed 3 | 1134 passed |
| P1-2 589d89a | 5 failed / 1 passed (raw reason) | 8/8 | P1/P2/P3/P4/P6 killed | 333 passed (+ registry) |
| P2-2 c24bcce | 4 failed / 3 passed (host false-success shape) | 7/7 | V1 killed 2 | 1830 passed after 2 classified corrections |
| hash addendum 6d97558 | not-ignored dirty worktree case | 10/10 | H1 killed 4, H2 killed 1 | 122 passed |
Pre-fix outputs: repair-003/prefix/*. Mutant outputs: repair-003/mutations/*. Probes: repair-003/scratch/*.

## Registry
Open P0/P1: none. Rows closed this cycle: ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001,
SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001, WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR,
REGRESSION-ATTRIBUTION-UNAVAILABLE-DETAIL-001 (BY_DESIGN), VERIFICATION-UNIT-ENV-FALLBACK-001.
Added DEFERRED: SUREFIRE-RENDERER-FAILURE-DETAIL-001 (P3 diagnostic), SUREFIRE-STABILITY-ENVELOPE-001 (P3 capability).
Registry tripwire: green.

## Not yet done (gates of the checkpoint, in order)
1. Independent review of main..6d97558 (reviews/REVIEWER_PROMPT_c3.md) - operator decides when/how; findings reconciled.
2. Merge: fast-forward main to 6d97558 (no merge commit needed: main has not moved) - operator authorizes.
3. Records-only commit on main: design authority 13.6 (certification), registry closure evidence pointing here.
4. Push: separate explicit authorization.
5. Final frozen twelve (Primary 6 + Cohort-2 6) driver: prepared, not executed; readiness bar unchanged
   (Primary >=4/6, Cohort 2 >=4/6, combined >=8/12, FN <=1, FS 0, authority 0, corruption 0, leaks 0, zero unresolved
   supported-backend correctness defects). Stop condition: no further harness repair merely to reach 8/12 - remaining
   failures that are model/planner reasoning after correct Kriya context are the qualified-model reliability ceiling.
