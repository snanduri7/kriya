# PRD-033 (Wave 7) closure checklist - current lineage

Requirement source (precedence): the original signed PRD-033 text is NOT in this repo (`tasks/` does not exist); the canonical text carried into the campaign is the Wave-7 directive section C2 plus the
owner-approved release-threshold text and the owner's reporting rule ("EVERY metrics/certification report must state: runs/cases; exact runtime identities; observed incidents; what statistical claim is and is
NOT supported"). Historical `handover/PRD-033_*` define nothing. No acceptance criterion was invented.

PRODUCT revision: `4a9273e1520901de7d0f327a5fb0b50b1bb88891` (tree `1520...` = `git rev-parse 4a9273e^{tree}`; `kriya/` tree `05d611e9da623b090f83f04226e97713e1f34ea7`). Tests run AT that revision: 639 passed
(`tests/test_prd033_*.py`, every other test file referencing `kriya.metrics`, `tests/architecture`), ruff 0, pylint 0. Python 3.14.6, pytest 9.1.1, macOS-27.0.1-arm64, editable install of this tree. No live model
was used (C2 states no live requirement for PRD-033). Statuses: MEASURED PASS / CURRENT LIVE PASS / DETERMINISTICALLY VERIFIED / NOT APPLICABLE / OPEN.

| # | Requirement (C2) | Status | Evidence |
|---|---|---|---|
| 1 | metrics derived from persisted structured evidence (traces, RunRecords, adjudications) only | DETERMINISTICALLY VERIFIED | `test_the_same_evidence_always_gives_the_same_report`, `test_no_proprietary_content_reaches_a_projection_or_a_report` (content-free canary), NEW `tests/test_prd033_current_lineage.py`: the real engine, one verified success and one rejected run, derived through the production TraceLogger and loader (so Waves 3-6 evidence shapes are covered); mutant "every run counted as success" killed |
| 2 | first-pass compile | DETERMINISTICALLY VERIFIED | `test_first_pass_compile_is_the_first_compile_outcome`; real-engine 1/2 in the lineage test |
| 3 | final verified success | DETERMINISTICALLY VERIFIED | `test_outcomes_first_pass_retries_and_wall_time`; lineage test 1/2 |
| 4 | false-success incidents | DETERMINISTICALLY VERIFIED | adjudication only: `test_false_success_comes_only_from_adjudication`, `test_only_the_operator_cli_writes_adjudications`, tamper/supersession tests |
| 5 | regression escape | DETERMINISTICALLY VERIFIED | same adjudication tests (`regression_escape_rate`); NEW statement + 0/0 threshold tests |
| 6 | authority rejection | DETERMINISTICALLY VERIFIED | `test_static_analysis_escalation_authority_and_commit_evidence` |
| 7 | context insufficiency | DETERMINISTICALLY VERIFIED | same test file (`context_insufficiency`, `context_request_fit_reductions`) |
| 8 | tool / structured-output failure | DETERMINISTICALLY VERIFIED | `test_model_metrics_are_keyed_by_runtime_settings_role_and_task_class` (`protocol_failure_rate`, `structured_output_failure_rate`) |
| 9 | retry amplification | DETERMINISTICALLY VERIFIED (NEW) | `outcomes.*.developer_attempts_per_run` = Developer attempts per run that made any; `test_retry_amplification_is_developer_attempts_per_run_that_made_any`; mutant (all-runs denominator) killed. Before: raw retry counts only |
| 10 | no-progress termination | DETERMINISTICALLY VERIFIED | `test_outcomes_first_pass_retries_and_wall_time` and the thresholds tests |
| 11 | fallback use | DETERMINISTICALLY VERIFIED | `fallback_transitions`, `fallback_selection_skips` in the static-analysis/escalation test |
| 12 | calls / tokens / wall time | DETERMINISTICALLY VERIFIED | `llm_calls`, `wall_time_seconds`, `prompt_tokens/completion_tokens` rows; lineage test |
| 13 | containment failures | DETERMINISTICALLY VERIFIED (NEW assertion) | `test_containment_failures_count_exactly_the_typed_containment_setup_failures`; mutant (wrong category constant) killed. It had NO asserting test before |
| 14 | resume invalidation | DETERMINISTICALLY VERIFIED | `test_chaos_and_run_record_inputs` (`resume_invalidation` over RunRecords) |
| 15 | human escalation | DETERMINISTICALLY VERIFIED | `human_escalations` from `approval.decision` events; UNAVAILABLE (not zero) without them (`test_no_evidence_is_unavailable_never_zero`, lineage test) |
| 16 | model-sensitive metrics keyed by exact runtime fingerprint + role + task class | DETERMINISTICALLY VERIFIED | `test_model_metrics_are_keyed_by_runtime_settings_role_and_task_class`, `test_outcomes_are_keyed_by_the_developer_identity_and_task_class` |
| 17 | no remote telemetry dependency | MEASURED PASS | `grep httpx|requests|urllib|socket kriya/metrics` = no match; the package reads local SQLite / JSON only |
| 18 | no inference of false success from LLM opinion | DETERMINISTICALLY VERIFIED | adjudication store has a single writer (the operator CLI), structural test; the evidence statement refuses a claim without adjudication (`test_the_statement_never_turns_missing_adjudication_into_a_claim`) |
| 19 | release-threshold configuration for PRD-036 | DETERMINISTICALLY VERIFIED + owner values installed | mechanism (`kriya/metrics/thresholds.py`, outside-workspace only, no repo default) existed; NEW `test_the_owner_zero_zero_release_thresholds_pass_only_on_adjudicated_zero_and_fail_on_one` (nothing adjudicated -> INCONCLUSIVE; adjudicated zero -> PASS; one false success -> FAIL). Operator file `~/.kriya/operator/release-thresholds-wave7.yaml` (digest `075d5dce37a8c383a49e88fd314b023e0b6df922e557a79b2da9c99fe8449d24`, false_success_rate max 0 and regression_escape_rate max 0, min_samples 1) |
| 20 | every metrics report states runs/cases, exact runtime identities, observed incidents, and what claim is / is NOT supported | DETERMINISTICALLY VERIFIED (NEW) for the METRICS report | `content.evidence_statement` + Markdown "What this evidence supports"; exact one-sided 95% bound only for zero incidents in >=1 adjudicated runs; unadjudicated successes named UNKNOWN; mutants (bound, unadjudicated, no-store claim, identity exactness, judged==0 branch) killed. **The same rule for the PRD-035/036 CERTIFICATION report is PRD-035's closure row, not PRD-033's** (carried) |

## Limits and carried items (stated, not hidden)
- `tests/test_live_prd033_metrics.py` was NOT run: C2 specifies no live requirement for PRD-033; PRD-035's live matrix produces the real population that must then be adjudicated and reported through this statement.
- A PASS of the 0/0 thresholds means "no incident OBSERVED among the adjudicated runs"; it never proves a zero underlying rate (the statement says so on every report). `LIVE-CERTIFICATION-REPEATED-TRIALS-001` stays required for Wave 8.
- The rule-of-evidence in item 20 must be applied by PRD-035/036 to their certification report (carried), and every successful PRD-035 run must be adjudicated, otherwise the thresholds are INCONCLUSIVE, not PASS.
- Fool-proofing: no signed row is OPEN; stale evidence: none reused (historical PRD-033 handover/live evidence not consulted as acceptance); pins: no architecture pin changed (architecture 183 passed); mutants: all listed killed.

Decision: PRD-033 CLOSED locally. Nothing pushed.
