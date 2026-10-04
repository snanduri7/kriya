# gate_outcomes: the shapes Kriya's writers record (inventory, TRACED 2026-10-04)

`runs.gate_outcomes` is a JSON TEXT column (`kriya/core/trace.py`); every entry is appended by production code to
`state.gate_outcomes` and persisted verbatim. KUP defines no gate record (the section carries the parsed list as-is), so
UI consumers must read the writers' own fields. The inventory below is what the writers record; `ui/fixtures/serializer_gates.py`
produces a committed fixture (`ui/fixtures/serializer/gate_outcomes.json`) through those constructors and the KUP adapter.

## Writers

| writer | sites | keys |
|---|---|---|
| `kriya/workflow/failure.py::Failure.to_gate_outcome` | every failed gate: `attempt.py` (~60 sites), `workflow.py`, `retry_strategy.py`, `verification_coordinator.py::_raise` | `attempt, type, success=False, output, mode, likely_files, file_locations[{filepath,line,col}], failed_content, attempted_edits, self_correction_attempt, attribution_tier, attribution_confidence, attribution_reasoning, attribution_kind, subtask_id, plan_id, milestone_id, planned_files, verification_target, authoritative_files` |
| `to_gate_outcome` + `.update(...)` | `attempt.py` 3945/3973 (runtime infrastructure/entrypoint), 5051/5080/5096 (managed service), 5550/9753 (graded runtime) | adds `commands`, `steps`; `managed_service_outcome`; `graded_by`, `deterministic_result`, `runtime_disposition`, `verifier_evidence` |
| compile success | `attempt.py` 8800, 8665 (self-corrected) | `attempt, type="compile", success=True, output` + `execution_evidence(...)` or `self_corrected, self_correction_turns, self_correction_transcript` |
| test selection fallback | `attempt.py` 8839 | `attempt, type="test_selection", success=False, output, target_test, recovered_by` (unsuccessful, not a Failure) |
| test success | `attempt.py` 8864 (`selection_fallback: True`), 8970 | `attempt, type="test", success=True, output` + `execution_evidence(...)` |
| targeted test success | `attempt.py` 8907 (self-corrected), 8938 | `attempt, type="targeted_test", success=True, output` + self-correction fields or `execution_evidence(...)` |
| runtime verification success | `attempt.py` 5103 (managed service probe), 5553, 9787 | `attempt, type, success=True, output, graded_by, commands, steps, deterministic_result` + `runtime_evidence_outcome_fields(grade)` (`runtime_disposition`, `verifier_evidence`) + `execution_evidence(...)`; 5103 adds `managed_service_outcome` |
| regression success | `workflow.py` 5036 (`deferred_to_future_owner`), 5204 | `attempt, type="regression_test", success=True, output` + `execution_evidence(...)` |
| spec compliance | `attempt.py` 10210 | `attempt, type="goal_spec_compliance", success=True, output` + optional `status` (PASS / UNAVAILABLE / SUPPRESSED), `reason_code`, `arbitrated_contradictions`, `planner_only_requirements` |
| `VerificationCoordinator._gate` | `verification_coordinator.py` | `attempt, type, success=result["success"], output` + `execution_evidence(result)` (success may be False) |

`execution_evidence(result)` (`kriya/tools/validate.py`) copies `toolchain_identity`, `egress`, `resources`, `runtime_artifacts`
when the result carries them; host-mode results carry none.

## What is common, and what is not

- **Every writer records** `attempt` (int), `type` (str), `success` (bool), `output` (str). These are the only fields a consumer
  may read as the gate's identity and result. `type` is the gate name; `success` is the recorded result.
- **No writer records** `gate`, `passed`, `name` or a top-level `reason_code` except `goal_spec_compliance`. The fixture generator
  used to invent `gate/passed/reason_code`; consumers that read them showed real records as "result not recorded". Fixed in
  this batch (Inspector `Gates` tab, `kup/tools/run_report.mjs`, `kup/tools/run_compare.mjs`).
- **Heterogeneity is real**: `success: true` beside `status: "UNAVAILABLE"` (spec compliance that did not run),
  `deterministic_result`/`graded_by` provenance, `test_selection` with `success: false` that is not a failure of the candidate.
  Consumers show these fields verbatim and never collapse them into the result.
- **Rule for consumers**: the result is the `success` boolean only. Absent -> "result not recorded"; non-boolean -> shown
  literally; a legacy `passed` boolean that disagrees with `success` -> "ambiguous", both values shown, never resolved by
  choosing one. Nothing is inferred from `output` text. Unknown fields are listed and kept.
- The run comparison keys gates by `(attempt, type)`; a repeated key on a side is listed as ambiguous and never matched; a
  side whose record is itself ambiguous makes the item `ambiguous`.
- The evidence checker reports a record outside the common shape as informational coverage (`EVC-GATE-001`) and conflicting
  result fields as an ambiguity (`EVC-GATE-002`); per-type semantics are not interpreted (no KUP definition).

## workspace.status fixture

`kriya/kup/cli_ops.py::_workspace_status` records `workspace = assessment.workspace_path`, `status = assessment.status`
(`kriya/control/recovery.py`: CLEAN, RUN_ACTIVE, RECOVERY_AVAILABLE, COMPLETE_PARTIAL_REQUIRED, MANUAL_ACTION_REQUIRED),
`run_active = status == RUN_ACTIVE`, `exit_code` 0 for CLEAN, 3 for RUN_ACTIVE, else 1 (= `kriya runs status`'s own exit
codes), and `assessment = RecoveryAssessment.to_dict()` (`workspace_path, status, run_active` (the active run id or null),
`records, evidence, unreadable_records, evidence_error`). The generated fixture now records exactly that for a CLEAN
workspace; it previously invented `NO_RECOVERY_REQUIRED` and `{reason, checkpoints}`.
