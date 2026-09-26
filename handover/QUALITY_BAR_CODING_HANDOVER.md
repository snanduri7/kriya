# Quality-bar follow-up after Batch 5: coding handover

**Status:** READY_FOR_PYTEST (2026-09-26). Local commits, not pushed.

This is the queued follow-up from the Batch 5 closure. It puts CLAUDE.md's "Mandatory quality bar" into force for rule 1 (static check) and rule 3 (strict test doubles). It also fixes the raw `kriya authority` traceback seen during demo-03 production prep.

## Commits

| Commit | What |
|---|---|
| 7dbe631 | Static-check gate: `.venv/bin/pylint kriya plugins/core_tools tests` with narrow rules in `pyproject.toml` `[tool.pylint]`. Must exit 0; the tree is at zero findings, so there is no baseline. New CI job `static-check`; `pylint>=3.2` added to the dev extra and the lock. Fixed two undefined names in tests to reach zero. |
| 62e4c10 | Strict test doubles: `tests/_strict_doubles.py` (`strict_config`, `strict_kernel`, `strict_engine`). 70 bare config/kernel/engine MagicMocks converted in 15 files. AST tripwire in `tests/test_strict_doubles.py`. |
| 875f7b4 | `kriya authority inspect/approve/revoke`: a configuration or approval-store error is printed as `Error: ...` with exit 1, not a traceback. Also covers `approve --out` inside the workspace. |
| beb4abf | **Fix of my own 62e4c10** (found in review). Each default `strict_config()` made a new temp dir, so two default configs never compared equal: resume/reuse fingerprint checks across two engines could fail, and the new prd007 assertion was vacuous. Now one temp root per test. CLI `Kernel` doubles are patched with `side_effect` so they carry the CLI's own cfg. |
| 7565387 | `authority`: the catch names the typed errors one by one. Catching every `ValueError` would have hidden coding errors. |
| 0a6dbf9 | Gate: add `undefined-loop-variable` (W0631); zero findings. |

## Evidence

- The gate flags the Batch 5 defect: on `0c19967`'s `workflow_controller.py` it reports E0606 at line 6647 (`requirement_closure_attempts`). Reverting the `List` import fix gives E0602.
- **Mutation checks:**
  - A reintroduced bare kernel fails the tripwire.
  - Disabling strict_config's unknown-field check fails its test.
  - A fresh temp dir per call fails the equality test.
  - Widening the authority catch to `Exception`, or to `ValueError`, fails the coding-error test.
- The 4 authority behaviour tests fail without 875f7b4.
- **My targeted pytest run:** 150/0. It covered:
  - test_strict_doubles, test_dispatch_generation, test_subtask_executor, test_cli_smoke and test_state_location;
  - the prd007 fingerprint test;
  - the prd020 fix-command test.

## New or changed test IDs

- `tests/test_strict_doubles.py`: all tests, new.
- `tests/test_state_location.py`: new tests:
  - `test_authority_reports_a_bad_state_directory_as_a_clean_error[inspect|approve|revoke]`
  - `test_authority_approve_out_inside_the_workspace_is_a_clean_refusal`
  - `test_authority_does_not_hide_a_coding_error[TypeError|ValueError]`
- `tests/test_prd007_run_lifecycle.py::test_entry_point_records_the_resume_config_fingerprint`: now uses a real config and a real inequality check.
- `tests/test_dispatch_generation.py::test_enabled_passes_the_configured_mode_through_unchanged`: mode is now `enforce`, because `legacy` is not a valid `workflow_controller.mode`.

## For the user's pytest

```bash
.venv/bin/pytest -q tests/test_strict_doubles.py tests/test_cli_smoke.py tests/test_dispatch_generation.py \
  tests/test_generate_json_contract.py tests/test_logging_location.py tests/test_milestones.py \
  tests/test_prd007_run_lifecycle.py tests/test_prd008_commit_state_gate.py tests/test_prd008_s4c_milestone_resume.py \
  tests/test_prd020_requirement_lineage.py tests/test_proposal_promotion.py tests/test_run_ownership.py \
  tests/test_subtask_executor.py tests/test_tool001_autonomous_tool_execution.py tests/test_workflow_controller.py \
  tests/test_workflow_controller_enforce.py tests/test_state_location.py tests/test_tools.py
```

`tests/test_workflow.py` is not in the list. Its only change is a `List` import used by a local annotation, and collect-only passed. The milestone, proposal, enforce and CLI files now run against a real config, so real config fingerprints are computed where a bare mock used to give None. A failure there most likely means the fixture doesn't match production. Report it back; don't loosen the assertion.

## qwen3.6 fallback thinking-off: finding (no code change)

- Kriya never sends a thinking control. The binding `reasoning` flag is client-side only: it strips `<think>` and sets the `REASONING_MIN_MAX_TOKENS` floor.
- In demo-03's production config, the `qwen3.6:35b-a3b-q4_K_M` fallback has `reasoning: false` and no `extra_body`.
- Ollama 0.34.2's `/api/show` lists the capability `thinking`. The model therefore thinks by default, and its hidden reasoning uses up the qualification case budgets.
- The whole `llm_chain` is SECURITY_AUTHORITY, so editing it needs `kriya authority approve` again. `extra_body` is a binding identity field, so any change makes the qualification stale and needs a live re-qualify.
- Candidates, each needing one live `kriya model qualify` with the user's approval:
  - `extra_body: {reasoning_effort: "none"}`;
  - then, if needed, `extra_body: {think: false}`.
- Optional Kriya improvement, not built: doctor/qualify could name the mismatch (the model serves `thinking`, the binding says `reasoning: false`, and no thinking control is sent).

## Not in scope

The CI `lint` job (`ruff check .`) has been red since 2026-08-18 with 462 pre-existing findings. That is a separate item.
