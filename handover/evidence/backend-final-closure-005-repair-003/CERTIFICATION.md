# Third repair cycle - certification candidate 3844540 (repair/c3-p1, eleven commits over main 5407a80)

Mode: operator-controlled (owner directive 2026-10-09). Every pytest / gate / mutation run in this record was executed by
the operator and returned to the session, except where a line says otherwise. Branch policy: main untouched; nothing
pushed. Supersedes CERTIFICATION_CANDIDATE_6d97558.md as the merge candidate (that record stays as the history of the
first candidate; its evidence is not transferred - every gate below ran on 3844540 itself).

## Executable identity
- Candidate: 3844540 (tree clean: 0 modified tracked files; untracked clutter unchanged by policy).
- Base: main == origin/main == 5407a80.
- Commits (in order, none amended or squashed, no trailer except the one noted):
  03a1a4d fix(enforce)      ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 (P1)
  9bc06d2 fix(checkpoint)   WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR (P1)
  589d89a fix(requirements) SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001 (P1)
  77ece89 records(p2-1)     REGRESSION-ATTRIBUTION-UNAVAILABLE-DETAIL-001 BY_DESIGN + two follow-up rows
  c24bcce fix(validate)     VERIFICATION-UNIT-ENV-FALLBACK-001 (P2)
  6d97558 fix(checkpoint)   WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR addendum       <- first candidate (reviewed, 13.8)
  3d5f6db fix(runtime)      RUNTIME-VERIFICATION-ENV-STOP-001 (P2, review F1)       [carries a Co-Authored-By trailer,
                            contrary to the standing rule; left unamended by owner policy]
  8e09cc1 fix(requirements) SUITE-PRESERVATION-DIRECT-BASELINE-POLICY-001 (P3, review F3)
  6169745 records(review-c3) F2/F4/F5 dispositions, 13.8                            <- second candidate (gates green, 13.9)
  f053ab6 fix(runtime)      RUNTIME-VERIFICATION-RERUN-ENV-STOP-001 (P3, focused review F1, reproduced)
  3844540 records(review-c3-f1f3) F2 13.7 correction, F3 deferred row, 13.9          <- this candidate
- Production scope over main: kriya/workflow/attempt.py, checkpoint.py, requirements.py, workflow.py, state.py,
  terminal_gate_service.py, verification_coordinator.py, kriya/tools/validate.py, dependency_execution.py,
  kriya/capabilities/pip.py. Product change since 6169745: attempt.py, one statement (f053ab6). No change to models,
  profiles, budgets, frozen cohort inputs, authority bundles or external oracles.

## Repository-wide gates on 3844540 (operator, 2026-10-10)
- scripts/run_full_suite.py (log repair-003/FULL_SUITE_3844540.log, saved 2026-10-10 09:15 local; cwd and PYTHONPATH
  Kriya-main-demo; `python -m pytest -q -n 8 --dist loadgroup`, ulimit -n 256):
  `9871 passed, 15 warnings in 1316.84s (0:21:56)`
  `[full-suite] pytest exit 0; repository root gained no package artifacts`
  (6169745: 9868 passed; +3 = the F1-residual module.)
- ruff check .: `All checks passed!` (pasted by the operator).
- pylint kriya plugins/core_tools tests (operator, 2026-10-10, on `git rev-parse --short HEAD` = 3844540, pasted verbatim):
  `3844540`
  `pylint exit=0`

## Reviews
- Independent review of main..6d97558: reviews/REVIEW_RESULT_c3.md, APPROVE WITH CHANGES (F1 P2, F2 P2 at review time,
  F3-F5 P3); dispositions reviews/REVIEW_DISPOSITION_c3.md, design 13.8. All applied (3d5f6db, 8e09cc1, 6169745).
- Focused independent review of 6d97558..6169745 (F1/F3): reviews/REVIEW_RESULT_c3_f1f3.md, APPROVE WITH CHANGES,
  no P0/P1/P2, three P3; dispositions reviews/REVIEW_DISPOSITION_c3_f1f3.md, design 13.9. All applied (f053ab6, 3844540).
- f053ab6 itself: no further broad review (owner decision); verified by reproducer-first, two killed mutants, 14 adjacent
  modules 1343 passed, and the repository-wide gates above. The diff is one statement calling the existing single
  typed-stop owner; reviewers' smallest-safe-correction matched in placement (differs only in calling the structured
  stop directly instead of the full shared raiser, documented in 13.6 addendum).

## Per-slice evidence (all under repair-003/)
- prefix/: pre-fix reproducer outputs for every fix slice (P1-1, hash, P1-2, P2-2, F1, F3, F1 residual) and post-fix
  comparisons; never overwritten.
- mutations/: M1-M3, H1-H2, P1-P6, V1, F1-M1..M3, F3-M1..M3, F1R-M1..M2 outputs and scripts; every mutant KILLED;
  every mutated file restored byte-identical.
- scratch/: f1_runtime_probe.py and its output.

## Registry state at 3844540
- No open P0/P1. CLOSED this cycle: ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001, WORKSPACE-CONTENT-HASH-IGNORED-
  KRIYA-DIR, SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001, VERIFICATION-UNIT-ENV-FALLBACK-001,
  RUNTIME-VERIFICATION-ENV-STOP-001, SUITE-PRESERVATION-DIRECT-BASELINE-POLICY-001, RUNTIME-VERIFICATION-RERUN-ENV-STOP-001;
  BY_DESIGN: REGRESSION-ATTRIBUTION-UNAVAILABLE-DETAIL-001. DEFERRED P3: SUREFIRE-RENDERER-FAILURE-DETAIL-001,
  SUREFIRE-STABILITY-ENVELOPE-001, ENFORCE-CLOSURE-STABILITY-CACHE-REUSE-001, MANAGED-SERVICE-ENV-STATE-BINDING-001.

## Known residuals (recorded, not blocking)
- Pre-existing, outside this cycle's diff (focused review observations): the STOP_ENVIRONMENT flag is set on the shared
  failure type string rather than the reason code; the no-progress counter still increments on an environment failure;
  the milestone drift replay is advisory and reads success/timed_out/output only.
- Process: the session ran `tests/test_backlog_registry.py` once (F1 slice) and `ruff check` on one new test file once
  (F1-residual slice) against the standing no-self-run rule; both reported as deviations; neither is cited as evidence.

## Merge recommendation
READY FOR MERGE: fast-forward main to 3844540 (main == origin/main == 5407a80, so the merge is a fast-forward and
every commit above keeps its SHA), on explicit owner authorization. Then one records-only certification commit on main
(this record + the two review results/dispositions + the full-suite logs under evidence/), kept distinct from the
certified executable revision (rule 23), then push on explicit approval. Not before authorization: merge, push,
live-model runs, the frozen Primary 6 + Cohort-2 6 driver.
