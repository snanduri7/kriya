# LR-R1-M1 Attempt Evidence Recorder - Final Certification

Date: 2026-10-05. Labels: MEASURED (observed in this certification), TRACED (read in the code), INFERRED, NOT_MEASURED.
Every result below was produced at FINAL HEAD. This report and its evidence files are committed afterwards in a
separate evidence-only commit, so the certified executable revision stays distinct (ENGINEERING_RULES §23).

```text
BASE
61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03   (= origin/main)

FINAL HEAD
ceb9943   (branch feature/lr-r1-m1; worktree ~/kriya-wt/lr-r1-m1; not pushed)
```

This report supersedes the first certification (`af5b383`, which certified `58853ad` with T6 PARTIAL and READY FOR
MERGE REVIEW NO). Every gate below was re-run at `ceb9943`.

## COMMITS

In order. Five commits are fixes of my own earlier work, each committed separately with a regression that fails
without it.

| # | Commit | Step |
|---|---|---|
| 1 | `25ef6f7` | M1.0 design, investigation notes, executable probes |
| 2 | `41ce8c3` | M1.1 store: schema, append-only writer, reader/verify |
| 3 | `6527ccf` | M1.2 scopes, run/unit/attempt lifecycle, config section, I-2 skeleton |
| 4 | `00395e7` | M1.3a mirror existing evidence streams |
| 5 | `2949742` | fix (own M1.2): pin git commit dates in the I-2 harness |
| 6 | `5af78c2` | M1.3 prep: fake state gains `record_gate_outcome` |
| 7 | `4cd41bc` | M1.3b 85-site gate-outcome conversion (mechanical only) |
| 8 | `bb5e811` | M1.3c tripwire: one gate-outcome producer |
| 9 | `fbc7daf` | M1.4 `wire_payload` + untrimmed transport content |
| 10 | `47b2eb1` | M1.5 `model.request` / `model.response` / `model.result` |
| 11 | `e42517a` | M1.6 `prompt.sections` |
| 12 | `7303f6c` | M1.7 `authority.snapshot`, `developer.parse` |
| 13 | `012f291` | M1.8a `candidate.change`, `gate.result`, `obligations.snapshot` |
| 14 | `f3e509b` | M1.8b `diagnosis`, `recovery.decision`, `fallback.decision`, `retry.delta` |
| 15 | `f8c4376` | M1.8b D7 typed reason codes (separate commit) |
| 16 | `70d9a63` | M1.9a retention |
| 17 | `4d4f90f` | fix (own M1.2 defect): attempt identity spans the whole retry-loop iteration |
| 18 | `a9be11b` | M1.9b `kriya evidence show/explain/verify/prune` |
| 19 | `c836c11` | M1.9c doctor row |
| 20 | `1e62d5a` | M1.9d T15 layout tripwire + docs |
| 21 | `3ec3cc0` | M1.10 I-2 over enforce/fallback/resume/Planner repair; overhead |
| 22 | `09a1ccf` | fix (own M1.9c defect): two structural tests |
| 23 | `90f9c44` | M1.11 legacy importer |
| 24 | `74796bd` | D7 review: dedicated `StopReasonEvidence` carrier |
| 25 | `58853ad` | OBS-1: native request-body parity + exact pin (test-only) |
| — | `af5b383` | first certification report (evidence only; superseded by this report) |
| 26 | `f3d5c8f` | T6-A: no-progress terminal evidence in `recovery.decision` |
| 27 | `5b1013e` | T6-B: refused answers and NO_CHANGE are distinct candidate facts |
| 28 | `7dd40b7` | T6-C: Q9 names the recorded terminal cause (explain only) |
| 29 | `765a981` | T6-D: an enforce TOOL subtask is its own evidence unit |
| 30 | `73b52be` | T6-E: pre-dispatch refusals and no-model-call are not missing evidence |
| 31 | `ab39589` | T6-F: authority is per call across a within-attempt protocol fallback |
| 32 | `cb0c367` | T6-G1: the recorded plan-scope conflict is explained (explain only) |
| 33 | `a3e0d89` | T6-G2: the recorded authorized write scope is explained (explain only) |
| 34 | `85383bb` | T6-G3: a Developer call that ended without an answer (explain only) |
| 35 | `ec68134` | T6-H: Q9's terminal cause follows terminal controller evidence (explain only) |
| 36 | `ecc6b01` | T6: the ten design paths end to end through `explain` |
| 37 | `ceb9943` | fix (own T6 test): the TOOL path no longer mutates its matrix row (test-only) |

## DESIGN DEVIATIONS

Full text: `handover/LR_R1_M1_0_INVESTIGATION_NOTES.md` §7.

| ID | Deviation | Status | Why acceptable |
|---|---|---|---|
| DEV-1 | A non-attempt call's phase comes from the role the code already set (`role_metrics.model_role`), not from per-phase scopes | ACCEPTED | Identity still comes from code, never from the caller or text; no per-phase producer edits were needed |
| DEV-2 | The goal is recorded in `unit.opened`, not `run.opened` | ACCEPTED | The run scope opens before any goal is known, and the units of one run can have different goals |
| DEV-3 | The `evidence` config section is excluded from every execution-identity fingerprint | ACCEPTED, REQUIRED by I-2 | MEASURED: before this, the RunRecord fingerprint differed between capture modes (an I-2 violation). Pinned by `test_capture_mode_is_part_of_no_execution_identity`, with an ordinary setting as negative control |
| DEV-4 | The store opens lazily, at the first scope that knows the config | ACCEPTED | `begin_mutating_run` has no config; a store that cannot open is RECORDER_UNAVAILABLE (D5) |
| DEV-5 | The equivalence suite measures volatile values (two `off` runs) instead of listing them | ACCEPTED | Measured, not guessed; it masks only what differs between identical runs |
| DEV-6 | Raw content is captured inside the adapters (`ChatResponse.raw_content`) | ACCEPTED, REQUIRED by D3 | TRACED: both adapters strip content before `LLMClient` sees it. Additive field; `content` is unchanged |
| DEV-7 | D7 codes are not in `Failure.diagnostics`; they live in a dedicated evidence-only carrier | ACCEPTED, REVIEWED at certification | `diagnostics.reason_code` feeds the ProgressVector (a decision input). Renamed from `environment_failure_code` to `StopReasonEvidence` (`74796bd`), see D7 below |
| DEV-8 | `retry.delta` is emitted at the retry's first Developer request, or at attempt close as UNKNOWN | ACCEPTED | The inputs it compares do not exist when the attempt opens |
| DEV-9 | `RecoveryDecision.retry_decision` is `compare=False` | ACCEPTED | Additive field; the decision is still `stop_loop`/`action`/`budgets_exhausted`, and the PRD-031 equality tests are unchanged |

None is unresolved.

## OBS-1

```text
CLOSED
```

Proof: `tests/test_lr_r1_m1_native_wire_parity.py` (9 tests, MEASURED passing at FINAL HEAD).
- **Setup.** A real `LLMClient`, the real `OllamaNativeRuntimeAdapter` and real HTTP to a loopback `/api/chat`
  server.
- **Parity.** Every recorded `model.request` `wire_body` equals, one for one and in order, the body the server
  received, as parsed structures and as canonical bytes.
- **Request shapes covered.** Plain, streamed, JSON mode, schema-constrained and tool-call requests, plus both internal
  resends, `empty_content_floor` and `response_format_dropped` (two labelled wires each).
- **Digest mode.** `digest_only` records the digest of the exact body sent.
- **Exact pin.** The plain native body is pinned key by key. This was OBS-1's original gap: an unknown key used to
  pass every suite.
- **Negative controls.** Each of these makes the suite fail:
  - the posted body diverging, on the non-stream, stream and tool paths;
  - an unknown key added to the body;
  - the recorder using the wrong stream flag;
  - an unlabelled resend.

Test-only; no provider semantics changed.

## D7

**Final storage mechanism.** `GenerationState.stop_reason_evidence: Optional[StopReasonEvidence]`, where
`StopReasonEvidence(code, message)` is a frozen dataclass in `kriya/workflow/diagnosis_codes.py`. Neither stop is an
environment failure; both only reuse `state.environment_failure` as the stop channel. The messages are byte-identical
to before.

All readers and writers (TRACED):

| Site | Role |
|---|---|
| `kriya/workflow/state.py`: field declaration | declares only, never reads |
| `kriya/workflow/retry_strategy.py`: `StopReasonEvidence(REGRESSION_UNATTRIBUTED, ...)` | writer, set only when `failure.type == "regression_unattributed"` and a stop message exists |
| `kriya/workflow/retry_strategy.py`: `StopReasonEvidence(NO_AUTHORIZED_REPAIR_TARGET, ...)` | writer, beside the unchanged message |
| `kriya/workflow/recovery_coordinator.py::_typed_stop_reason` | the only reader: returns the code only while its message is still the current stop message |
| `_record_recovery_decision` | the only consumer: the `stop_reason_code` key of the `recovery.decision` evidence payload |

**Proof that no decision consumes it.** `tests/test_lr_r1_m1_reason_codes.py`:
- **Producers.** An AST walk over `kriya/` requires that only the producers set the field, name the codes or import
  `diagnosis_codes`.
- **Declaration.** `state.py` may only declare the carrier type.
- **Reader.** `_typed_stop_reason` is the only reader.
- **Negative control.** A planted read inside `decide_for_state` fails the walk.
- **Mutants killed.** A planted decision read; `state.py` importing a code; a stale code being reported; a dropped
  producer; the code being set on any stop.
- **Retry, recovery, fallback, progress vector, terminal outcome, planning, review, RunRecord and commit:** none
  reads the field.
- **Serialization.** `GenerationState` is not serialized wholesale (TRACED).

## FULL TEST SUITE

```text
command   cd ~/kriya-wt/lr-r1-m1; ulimit -n 256;
          PYTHONPATH=$PWD <main checkout>/.venv/bin/pytest -q -n 8 --dist loadgroup
          (the worktree has no .venv of its own; the main checkout's venv with PYTHONPATH pointed at the
          worktree is how every run in this work was made)
passed    8658
failed    0
errors    0
duration  540.05 s (wall 541 s)
```

Evidence: `handover/evidence/lr-r1-m1/final2_full_suite_summary.txt` (the first certification's 8608/0 run is kept in
`final_full_suite_summary.txt`). No exclusions, no xfails, single run, at FINAL HEAD (MEASURED).

## RUFF

`ruff 0.16.0`, `ruff check .`: **PASS** (exit 0, zero findings).

## PYLINT

`pylint kriya plugins/core_tools tests`: **PASS** (exit 0).

## MUTATION TESTS

Consolidated campaign v2 at FINAL HEAD (`ceb9943`; started 11:02:48, after the 11:02:07 commit):
`handover/evidence/lr-r1-m1/m1_mutation_campaign_v2.py`, with results in `m1_mutation_results_v2.json` and
`m1_mutation_campaign_v2.log` (the `.log` files are git-ignored and stay in the worktree; the committed JSON carries
every mutant's outcome). It contains every mutant of the first campaign (`m1_mutation_campaign.py`, 52/52, kept
as evidence) plus the T6 mutants.
- **Method.** Each mutant runs every `tests/test_lr_r1_m1_*.py` (baseline: 229 passed). The file is restored and
  checked clean after every mutant.

```text
mutants run     91
mutants killed  91
survivors       0
```

Coverage by guarantee:

| Guarantee | Mutants |
|---|---|
| hash chain | 5 |
| capture mode | 1 |
| file permissions | 1 |
| raw response / reasoning | 2 |
| identity | 2 |
| prompt / request parity | 5 |
| authority | 1 |
| parse | 1 |
| candidate diff | 3 |
| gate attribution | 3 |
| retry delta | 3 |
| fallback | 3 |
| recovery decision | 2 |
| recorder-failure non-interference | 2 |
| I-2 planted effects | 2 |
| retention | 5 |
| I-1 | 2 |
| I-3 | 4 |
| D7 | 3 |
| explain | 2 |
| T6-A no-progress terminal evidence | 3 |
| T6-B refused / NO_CHANGE candidates | 5 |
| T6-C Q9 terminal cause | 2 |
| T6-D TOOL evidence unit | 5 |
| T6-E pre-dispatch refusal / no-model-call | 4 |
| T6-F per-call authority | 4 |
| T6-G1 plan-scope conflict | 2 |
| T6-G2 authorized write scope | 1 |
| T6-G3 no model answer | 4 |
| T6-H controller terminal precedence | 7 |
| T6 path harness | 2 |

Earlier per-commit mutation rounds (listed in each commit message) found these survivors:
- **Weak tests, then strengthened:**
  - M1.8a terminal gates;
  - M1.8b evidence class, call substitution and refused-call record;
  - M1.9a symlink and age guard;
  - M1.9b Q5, Q6 and Q7;
  - M1.11 absence list;
  - T6-B no-change-refused and any-parse; T6-C first-unit; T6-F unchanged-counted; T6-H deciding-any-status.
- **Redundant code, then removed:**
  - M1.8b `delta_emitted`;
  - M1.9b's payload-attempt fallback;
  - T6-H's seq restriction on failed terminal gates (gates-any-time survived because the condition never decided).

No unclassified survivor remains.

## I-1

```text
PASS
```

- `tests/test_lr_r1_m1_layout_tripwire.py`: no code in `kriya/` (outside `attempt_evidence/`), `scripts/` or
  `benchmarks/` names the store files together with the store, or `blobs/`. Planted negative controls are included.
- `test_no_production_code_reads_the_store`: the reader's consumers are only the CLI, `explain.py` and the doctor.
- `test_metrics_never_import_the_recorder`.

5 passed at FINAL HEAD (MEASURED): the tripwire file plus `test_lr_r1_m1_mirroring.py::test_no_production_code_reads_the_store`
and `::test_metrics_never_import_the_recorder`.

## I-2

```text
PASS
```

`tests/test_lr_r1_m1_equivalence.py` and `tests/test_lr_r1_m1_equivalence_scenarios.py`, 12 passed at FINAL HEAD
(MEASURED; 9 at the first certification, plus the three scenarios T6-D and T6-F added).
- **Variants.** `off` ×2, `full`, `digest_only`, store open refused, every append failing.
- **Scenarios.** Direct success; retried until the no-progress stop; output-budget lower-protocol fallback (T6-F);
  fallback refused; fallback substituted at the call; resume; enforce success; enforce refused verified-no-change;
  enforce Planner repair; enforce TOOL subtask; enforce TOOL subtask failed (T6-D). Plus
  `test_capture_mode_is_part_of_no_execution_identity`.
- **Identical across variants.** Request bytes, workspace bytes, result, trace run events (minus the pointer),
  RunRecords and the decision ledger.
- **Planted recorder effects.** A candidate byte, the prompt, and the retry budget: each is caught (also in the
  mutation campaign).

## I-3

```text
PASS
```

`tests/test_lr_r1_m1_legacy_import.py`, 7 passed at FINAL HEAD (MEASURED).
- **Absences.** Every absent field (prompt, raw response, per-attempt diff, authority snapshot, recovery decision,
  retry delta, fallback decision, per-attempt outcome, and passing-gate output where absent) is an explicit
  `not_recorded` record with its reason, and nothing else is.
- **Final diff.** The final diff is run-level only.
- **Read-only.** The legacy evidence is never written.
- **Explain.** It reports legacy reasons and never overrides present evidence.
- **Real copies.** Two copied frozen runs imported to VERIFIED stores (139 / 153 records), with the originals'
  SHA-256 listings unchanged (MEASURED).

## M1 Q1-Q9 COVERAGE

Per question: answered from recorded evidence on real pipeline runs.

| Q | Status | Test proving it |
|---|---|---|
| Q1 asked | PASS | `test_lr_r1_m1_model_calls.py::test_one_call_one_wire_records_exact_messages_and_verbatim_content`; `test_lr_r1_m1_sections.py::test_developer_segments_reproduce_the_message_actually_sent`; native parity suite; `test_lr_r1_m1_cli.py::test_a_passing_run_answers_every_question` |
| Q2 authority | PASS | `test_lr_r1_m1_authority_parse.py::test_each_developer_request_is_preceded_by_its_authority_and_followed_by_its_parse` |
| Q3 returned | PASS | `test_lr_r1_m1_model_calls.py` (verbatim content, errors, cancellation, two-wire calls); `test_lr_r1_m1_authority_parse.py::test_a_malformed_answer_is_recorded_as_parsed_invalid_with_its_typed_code` |
| Q4 candidate | PASS | `test_lr_r1_m1_candidate_gates.py::test_candidate_change_binds_digests_and_its_diff_reproduces_the_candidate` (`git apply` reproduces the bytes), `::test_a_file_without_final_newline_still_diffs_exactly`; `test_lr_r1_m1_recovery.py::test_content_proposed_but_never_staged_is_recorded_refused` |
| Q5 failed check | PASS | `test_lr_r1_m1_candidate_gates.py::test_every_validator_gate_is_recorded_after_it_ran_with_its_output`, `::test_terminal_gates_are_recorded_from_the_report_the_service_returns`; `test_lr_r1_m1_attempt_iteration.py::test_the_terminal_regression_gate_belongs_to_its_attempt`; `test_lr_r1_m1_cli.py::test_a_retried_run_explains_the_failure_the_decision_and_the_delta` |
| Q6 why retry | PASS | `test_lr_r1_m1_recovery.py::test_a_failed_attempt_records_its_diagnosis_then_the_recovery_decision`, `::test_an_exhausted_run_records_the_stop_decision` |
| Q7 what changed | PASS | `test_lr_r1_m1_recovery.py::test_each_retry_records_its_delta_at_its_first_developer_request` + four pure `retry_delta` tests |
| Q8 fallback | PASS | `test_lr_r1_m1_recovery.py::test_an_escalation_records_...`, `::test_an_incompatible_fallback_records_...`, `::test_a_call_substituted_to_the_next_fallback_records_both_models`, `::test_a_call_no_fallback_can_serve_records_the_refused_call` |
| Q9 final outcome | PASS | `test_lr_r1_m1_cli.py::test_a_passing_run_answers_every_question`, `::test_an_incompatible_fallback_is_explained_with_its_rejection` |

## T6 PATH MATRIX

```text
T6 PATHS 10/10
T6 Q1-Q9 FULL
```

`tests/test_lr_r1_m1_t6_paths.py` (`ecc6b01`, fixed in `ceb9943`): the ten paths the first certification listed as
"recorded but not asserted through `explain`", each run deterministically through the real pipeline (only the model
runtime scripted, `tests/_t6_harness.py`) and read back through the evidence reader and `explain`, never from logs.
Every cell is asserted. With `KRIYA_T6_MATRIX` set the suite writes the matrix below; regenerated at FINAL HEAD
(`handover/evidence/lr-r1-m1/t6_matrix.json`, 10 passed, MEASURED). The focused T6 suite (paths + A-H fixes) is
47 passed.

PASS = RECORDED with content and asserted. N/A = a typed `NOT_APPLICABLE(reason)`; no cell is blank or NOT_RECORDED.

| Path | Q1 | Q2 | Q3 | Q4 | Q5 | Q6 | Q7 | Q8 | Q9 |
|---|---|---|---|---|---|---|---|---|---|
| iterative per-file Developer | PASS | PASS | PASS | PASS | N/A ¹ | N/A ² | N/A ³ | PASS | PASS |
| investigation turns | PASS | PASS | PASS | PASS | N/A ¹ | N/A ² | N/A ³ | PASS | PASS |
| output-budget lower-protocol retry | PASS | PASS | PASS | PASS | N/A ¹ | N/A ² | N/A ³ | PASS | PASS |
| `REPEATED_VECTOR` terminal | PASS | PASS | PASS | PASS | PASS | PASS | N/A ³ | PASS | PASS |
| plan-scope conflict | PASS | PASS | PASS | PASS | PASS | PASS | N/A ³ | PASS | PASS |
| Planner repair rounds | PASS | PASS | PASS | PASS | N/A ¹ | N/A ² | N/A ³ | PASS | PASS |
| enforce TOOL subtask | N/A ⁴ | N/A ⁴ | N/A ⁴ | N/A ⁵ | N/A ⁶ | N/A ⁷ | N/A ³ | N/A ⁴ | PASS |
| final review refusal | PASS | PASS | PASS | PASS | N/A ¹ | N/A ² | N/A ³ | PASS | PASS |
| exception escape | PASS | PASS | PASS | PASS | N/A ¹ | N/A ² | N/A ³ | PASS | PASS |
| deadline stop | PASS | PASS | PASS | N/A ⁸ | PASS | PASS | N/A ³ | PASS | PASS |

1. `every recorded check passed (N gate result(s))`
2. `no failure was recorded for this attempt; nothing was retried`
3. `no later attempt in this unit invocation`. A row is the path's last attempt, and Q7 is the change into the
   *next* attempt. On `REPEATED_VECTOR` (four attempts), every earlier attempt is also checked PASS-or-typed for every
   question; the plan-scope row is the conflicting attempt itself, whose decision is `retry: false`. The Q7 delta's content is asserted by
   `test_lr_r1_m1_cli.py::test_a_retried_run_explains_the_failure_the_decision_and_the_delta` and
   `test_lr_r1_m1_recovery.py::test_each_retry_records_its_delta_at_its_first_developer_request`.
4. `no_model_call: a tool subtask makes no model call`
5. `tool_action: no Developer candidate`
6. `no_verification_gate`
7. `tool_subtask: executed once, never retried`
8. `no_model_answer: InferenceDeadlineError` (G3)

**The rest of the design's T6 list (§12)** was already asserted through `explain` at the first certification
(single-shot batch Developer, retried direct, `FALLBACK_MODEL_INCOMPATIBLE`) or is now:
- **Context-budget refusal before dispatch:** a real run, through `explain` (T6-E, T6-G3:
  `test_e_a_call_refused_before_dispatch_is_not_a_missing_response`, Q1 `dispatched: false`, Q3
  `NOT_APPLICABLE(provider_not_dispatched: OutputBudgetUnsatisfiableError)`).
- **Cancellation:** `explain`'s answer is asserted on recorded `model.response` shapes
  (`test_g3_a_cancelled_call_has_no_model_answer`); the real cancellation is asserted at `LLMClient` level
  (`test_lr_r1_m1_model_calls.py`), not end to end through a pipeline run.
- **Two-wire calls** (`response_format_dropped`, `empty_content_floor`): asserted at `LLMClient` level and by the
  native parity suite (both wires recorded and labelled), not through `explain`.

These two are coverage depth, not known recording or explain defects; they are outside the ten-path T6 gate this
round authorized.

## T6 DEFECTS A-H

```text
A-F CLOSED
G1-G3 CLOSED
H CLOSED
```

Each was found while writing the T6 path tests, stopped and reported, then fixed only after the owner authorized it.
Each fix has a test that fails without it and its mutants are in the v2 campaign (all killed).

| ID | Commit | Defect (MEASURED on the T6 run) | Fix | Kind |
|---|---|---|---|---|
| A | `f3d5c8f` | `REPEATED_VECTOR` stop: Q6/Q9 could not say the stop was no-progress | `recovery.decision` gains `progress_classification`/`no_progress_reason`; explain shows them | recorder payload (additive) |
| B | `5b1013e` | A parsed proposal refused before staging, and NO_CHANGE, both read as missing candidate evidence | REFUSED candidate from the parsed answer (`parse_seq`, `proposal_kind`, `parse_reason_code`, `candidate_staged: false`, diff NOT_APPLICABLE, no invented bytes); Q4 `NOT_APPLICABLE(model_proposed_no_change)` | recorder payload + explain |
| C | `7dd40b7` | Q9 did not name the terminal cause (final-review refusal, exception) | Q9 `terminal_cause` from the last unit; EXCEPTION branch | explain only |
| D | `765a981` | An enforce TOOL subtask had no evidence unit of its own | `scope.tool_unit`, `tool.execution` kind; the controller's TOOL branch is wrapped; explain answers a tool attempt | recorder payload (additive), I-2 TOOL scenarios |
| E | `73b52be` | A call refused before dispatch, and an attempt with no model call, read as NOT_RECORDED | Q3 `provider_not_dispatched` + `not_dispatched`/`missing_responses`; `no_model_call` is NOT_APPLICABLE | explain only |
| F | `ab39589` | Authority shown once per attempt across a within-attempt lower-protocol fallback | `scope.record_authority_transition` after `_lower_output_protocol_retry`; explain shows per-call authority | recorder payload (additive), I-2 scenario |
| G1 | `cb0c367` | A recorded plan-scope conflict was not explained | Q6 `plan_scope_conflict`, `Q9.plan_scope_conflicts` | explain only |
| G2 | `a3e0d89` | The recorded authorized write scope was not shown | Q2 `authorized_write_scope` | explain only |
| G3 | `85383bb` | A Developer call that ended without an answer read as missing candidate evidence | Q4 `NOT_APPLICABLE(no_model_answer: <cause>)` for errors, CANCELLED and refusal-before-dispatch; a missing response record, or an answer that was returned, stays NOT_RECORDED | explain only |
| H | `ec68134` | Q9's terminal cause came from the last closed unit even when the controller recorded the terminal decision (a failed enforce run explained by a later successful unit) | Q9 precedence below | explain only; no recorder payload or workflow change |

**H: controller terminal inventory (TRACED, enumerated before the fix).**

| Record (in store) | Producer | Terminal status | Reason codes | Always terminal? | Also non-terminal? |
|---|---|---|---|---|---|
| `mirror.event planning.failed`, source `workflow_controller.enforce` | `_write_enforce_trace`, only when `failure_type == PLANNING_ERROR` | not carried | yes (`details.reason_codes` in content) | yes, written once at the enforce terminal | no (only producer) |
| `mirror.event run.exception`, source `workflow_controller.enforce` | `_write_enforce_trace`, status `error` | `error` implied | exception type + message | yes | no |
| `mirror.event run.exception`, source `workflow.run_generation_workflow` | `_record_run_exception` | one invocation's exception | type + message | terminal for that unit (unit also closes EXCEPTION) | — |
| `requirement.verdicts`, `model.role_metrics` | enforce trace / `write_outcome_trace` | no | no | no | yes (also on success) |
| `mirror.decision` `subtask_attempt` | `record_subtask_attempt` | that subtask's status/error | no | no (per subtask) | yes |
| `gate.result` stage `terminal` | `TerminalGateService`, once after the subtask loop | per gate | the gate's message | run-level terminal checks | no |
| `run.closed` | `begin_mutating_run` exit | RunRecord status | none | yes | no |
| `unit.closed` | unit scope | the unit's own result | `failure_category` | no | yes |

`write_outcome_trace` mirrors only events; the row's own status and failure_category are not in the store.

**H: Q9 precedence (`explain._run_terminal_cause`).**
1. Explicit controller terminal decision: the latest `planning.failed`/`run.exception` mirrored from
   `workflow_controller.enforce` (`source: controller_terminal_event`, with `seq`, `kind`, `reason_codes` or
   exception type/message from content, and the deciding subtask: the last one that did not complete).
2. Explicit terminal workflow result: failed terminal gates (`source: terminal_gates`, `failed_gates`).
3. The last closed unit's terminal cause. Guard: a success-shaped last unit never explains a run whose RunRecord is
   not SUCCESS (NOT_RECORDED, with the last unit as context).

Tests: `test_h_*` in `tests/test_lr_r1_m1_t6_fixes.py`. Required negative controls, all KILLED in the v2 campaign:
"always use last closed unit" (`always-last-unit`, 6 failed), "ignore controller reason_codes"
(`ignore-reason-codes`, 4), "treat any late controller event as terminal" (`any-controller-event-terminal`, 7),
"prefer an earlier controller record" (`earliest-controller-record`, 1); plus `ignore-terminal-gates`, `no-guard`,
`deciding-any-status`.

## PERFORMANCE

| Measurement | Result | Status |
|---|---|---|
| Recorder time, scripted direct run (7 attempts, 220 records) | `full` 93 ms/run, `digest_only` 9 ms/run | MEASURED |
| One `model.request`, unique 50 KB / 400 KB prompt | `full` 2.6 / 15.4 ms; `digest_only` 0.26 / 1.4 ms | MEASURED |
| Versus the 1% design threshold | about 0.2% of a live run, from the measured per-record cost and the measured live pace (10-300 s calls, ~16 min/run). Against a scripted run (instant model) the share is ~10% of ~1 s, which is not the quantity the threshold governs | INFERRED (below the threshold); not certified |
| Live overhead | — | **NOT_MEASURED** |

## SECURITY

- **Capture modes.** `full` (default, D4), `full_with_reasoning`, `digest_only` (digests only, no content blobs:
  `test_digest_only_records_no_prompt_or_response_text` canary, plus a mutation) and `off` (no store).
- **Permissions.** Store directories 0700, files 0600, set explicitly whatever the umask
  (`test_store_is_private_regardless_of_umask`, umask 0o277). Retention staging stays inside the store root;
  symlinked entries are never followed or removed.
- **Local-only guarantee.** The store is written only under `<state dir>/attempt-evidence/<run_id>/` through its own
  writer:
  - never in a workspace or sandbox, and never through `write_control_file`/`AuthorizedFileWriter` (classified in the
    FILE-INTEGRITY audit);
  - no network client in the package (the PRD-012 network inventory passes in the full suite);
  - no export path exists.
- **Reasoning handling (D3).** The transport content is recorded verbatim, before `split_reasoning`. A separately
  returned reasoning field is recorded as digest + length only, and its text only under `full_with_reasoning`
  (`test_one_call_one_wire_...` asserts no reasoning blob by default; `test_full_with_reasoning_also_keeps_the_reasoning_text`).
- **Repository cannot weaken the operator setting.** `evidence` is a blanket SECURITY_AUTHORITY key under SEC-009
  (`kriya/config/authority.py::_BLANKET_SECURITY_TOP_KEYS`).
  `test_repository_config_can_never_change_the_recorder` checks that `capture: off`, `digest_only`,
  `full_with_reasoning` and a retention change from a repository `kriya.yaml` each raise `ConfigAuthorityError`.
- **Disclosure.** In `full` mode the store contains prompts, model output and source code (local, owner-only). The
  hash chain detects corruption and casual edits; it is not a signature.

## OPEN ISSUES

```text
OPEN M1 CORRECTNESS DEFECTS 0
```

- **T6 path matrix:** CLOSED (10/10, Q1-Q9 full; was PARTIAL at the first certification).
- **Defects A-H:** all CLOSED. No further recorder or explain correctness defect appeared during this final
  certification.
- **Live overhead NOT_MEASURED.** Deferred to the bounded live operational validation (the first live run with the
  recorder); not a merge-review blocker, by owner decision.
- **Coverage depth, not defects:** real cancellation and the two-wire calls are asserted at `LLMClient` level, not
  end to end through `explain` (see T6 PATH MATRIX).

Outside M1, and not open items of this milestone:
- the LR-R1-P1/P4/P5 fixes (not implemented, by instruction);
- P2 and P3;
- ANALYZER-RULE7-MEDIAN-DEFINITION;
- `node_modules/`, `package.json` and `package-lock.json`: pre-existing untracked test-harness artifacts in the
  worktree root (they existed in the main checkout before M1; not M1 output; left in place, not committed).

## PRODUCTION BEHAVIOR CHANGED

```text
NO, except the approved additive evidence semantics
```

Those additive changes:
- the store under the state dir, plus its retention (pruning only old stores of its own);
- the `evidence` config section (SECURITY_AUTHORITY), excluded from execution fingerprints (DEV-3);
- one `evidence.attempt_store` pointer event per trace row;
- the additive `ChatResponse.raw_content`/`reasoning_text` and `RecoveryDecision.retry_decision` (compare=False);
- `GenerationState.stop_reason_evidence` (evidence only);
- the `kriya evidence` commands;
- the never-required doctor row `evidence.attempt_recorder`;
- T6 (authorized): the additive `recovery.decision` progress fields (A), REFUSED candidate records (B), the
  `tool.execution` unit around the controller's TOOL branch (D) and the per-call `authority.snapshot` after a
  lower-protocol fallback (F). C, E, G1-G3 and H change `explain` only. H changed no recorder payload and no
  workflow behaviour (`ec68134` touches only `kriya/core/attempt_evidence/explain.py` and its tests).

I-2 proves that request bytes, workspace bytes, results, retry/fallback behaviour and control-plane outcomes are
unchanged across all recorder modes, faults included. It now also covers the TOOL and output-budget-fallback
scenarios.

## FINAL STATUS

Every line below was measured at FINAL HEAD `ceb9943`.

```text
A-F CLOSED
G1-G3 CLOSED
H CLOSED
T6 PATHS 10/10
T6 Q1-Q9 FULL
FULL SUITE PASS
RUFF PASS
PYLINT PASS
I-1 PASS
I-2 PASS
I-3 PASS
MUTATION PASS
OPEN M1 CORRECTNESS DEFECTS 0
READY FOR MERGE REVIEW YES
LIVE OVERHEAD NOT_MEASURED
```

| Gate | Result at `ceb9943` |
|---|---|
| Full suite | 8658 passed, 0 failed, 0 errors (540 s) |
| Ruff | exit 0, zero findings |
| Pylint | exit 0 |
| I-1 / I-2 / I-3 | 5 / 12 / 7 passed |
| Focused T6 (paths + A-H) | 47 passed; matrix 10 rows, every cell PASS or typed NOT_APPLICABLE |
| Mutation v2 | 91/91 killed, 0 survivors (baseline 229 passed) |

Nothing was pushed or merged. No live model was run.
