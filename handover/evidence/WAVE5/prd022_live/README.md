# Wave-5 PRD-022 live acceptance (2026-11-01): redirect -> owner-scoped corrective retry

Fixture: `tests/test_live_prd022_redirect_chain.py` (permanent, `live_model` + `live_target`, default-deselected). Scripted: Planner, Architect and
Developer call #1 (the violating attempt). Real: the sandbox, compile/test gates, `find_brownfield_test_redirections`, the redirect failure,
`ownership_redirect_recovery`, the deterministic restore, the retry request, Developer call #2+ on the qualified model, the terminal gates, the commit decision.
- Top-level files: the single live run (exit 0, 1 passed, branch b fail-closed); `head.txt` = executable HEAD + dirty files at the time; `live_failure_events.txt` + `requirement_verdicts.txt` = the classification evidence.
- `dry_run/` the offline run (retry scripted): branch (a), same trigger path. `mutants/` the two kill-checks (ownership gate disabled; restore disabled), each failed on the intended assertion.
- `live_failure_events.txt` the classification evidence. Wrapper script: `run-prd022-live.sh` (reads ~/kriya-gate-e-2026-11-01/env.sh).
Earlier dry-run attempts (dry, dry2, dry3 under ~/kriya-gate-e-2026-11-01/evidence/w5_prd022/) were fixture-assertion fixes of this file and are not part of the verdict.
