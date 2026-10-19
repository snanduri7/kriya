# E14 preflight test-only group - characterization (OI-05/06/40/42/59/63/65), OI-33 evidence, harness group (OI-34/43/45/53)

Base: **ed397d5** (branch arch/full-modularization-v1, clean tree; E1-E12 + HX1 accepted, E13 PARTIAL/BLOCKED BY OI-67).
Brief: owner decision 2026-10-19 items 8 and 12 (handover/MODULARIZATION/E14-TRIAGE.md). Test-only: `git diff ed397d5 -- kriya plugins pyproject.toml ui` is empty.
Evidence classes per handover/ENGINEERING_RULES.md §2 (MEASURED / TRACED / INFERRED / CONFIRMED / UNKNOWN).
Pins: `tests/test_e14_pre_characterization.py` (commit e56cb26; 40 test ids, ~16 s at `-n 4`, ~41 s serial; NOT in the
kernel / state_machine tiers because it drives ~40 real scenario runs; it patches no kernel module name, so the
PATCH_POINTS counts are unchanged - tests/architecture 91 passed with it).
Mutation proof: `~/kriya-modularization-tools/mutrun.py` v2, campaign `20261019_064326_74b868e0`, rev e56cb26, 18 mutants of
production bytes in a git-archive sandbox (`kriya.__file__` under the sandbox), **VALID, 18/18 KILLED at L1** (each mutant's
L1 = only the pin node(s) it targets), 82 s. Tool caveat MEASURED: mutrun's `detail` column takes the first `FAILED ` lines of
the output, and a pin that drives the real pytest gate prints the INNER gate's `FAILED test_calc.py::...` lines first; the four
affected mutants (m42a, m42b, m42f, m05b) were re-run one by one in a fresh git-archive sandbox (scratchpad `verify_mut.py`):
each fails exactly its target pin, and the four pins pass unmutated (control: 4 passed).

## Summary (one line per item)

| Item | Status of the claim | Defective? | Severity | Real owner (TRACED) | Closing slice (PROPOSED; the orchestrator routes) |
|---|---|---|---|---|---|
| OI-65 | CONFIRMED, REACHABLE (was "INFERRED unreachable") | YES | P2 | `kriya/workflow/enforce_loop.py` EnforceLoop.run (retained-baseline redirect); trigger in `kriya/workflow/plan_validation.py` validate_plan | a dedicated small Type-C correction slice (proposed "R3") before F1; not E14 (Type A) and not E14-C |
| OI-42 | MEASURED; class (ii) - reporting, NOT authority | YES (reporting) | P2 | decision: `kriya/workflow/stage_review.py` final_review reuse rule; the drop exists only in `kriya/workflow/development_loop.py` accept_candidate's held-gate block | the same proposed correction slice (reporting), or H5 (typed terminal reasons); NOT an AUTHORITY_BLOCKER |
| OI-68 (new) | MEASURED | YES (evidence binding) | P2 | `kriya/workflow/workflow.py` `_record_approval_decision` (approval.decision event) | C-family authority evidence (owner of approval records); proposed with OI-42's slice |
| OI-05 | MEASURED (three identities, by path) | NO for every traced consumer; the key's meaning varies by path (contract ambiguity) | P3 | result builders: `outcome.completed_result` (engine id), `stage_apply._abort_without_applying` (engine id), `stage_knowledge_guard` / `stage_planning` / `stage_architecture` / `development_loop.prepare_development` (trace id), controller enforce path (RunRecord id) | H5 (decision-evidence run linking) as the registry row already says |
| OI-06 | MEASURED | YES (result-contract inconsistency; one consumer loses the class) | P3 | `kriya/workflow/stage_apply.py` approval_gate `_abort_without_applying` | H5 typed terminal reasons, together with OI-40/OI-69 |
| OI-40 | MEASURED | YES (same class) | P3 | `stage_knowledge_guard.py:63`, `stage_planning.py:382`, `stage_architecture.py:178` (each passes failure_category to the trace and omits it from the dict) | H5, with OI-06/OI-69 |
| OI-69 (new) | MEASURED | YES (same class) | P3 | `development_loop.prepare_development` (baseline_indeterminate), the PRD-008 refusals (workflow.py / controller), every enforce-path result (controller / enforce_loop) | H5, with OI-06/OI-40 |
| OI-59 | named route MEASURED blocked upstream; staging fallback reach UNKNOWN | NO on the named route; latent (fails closed) | P3 | guard: `candidate_transaction._decide_edit_capabilities`; fallback: `candidate_transaction._stage_candidate_writes` | G1/G2 (CandidateTransaction characterization/extraction) |
| OI-63 | MEASURED | YES (audit record incomplete; no decision changes) | P3 | the call ORDER in `WorkflowController.execute_milestones` (revalidate before migrate); the re-save is `milestones._publish_reuse_assessment` | a milestone/resume evidence slice (D2 is DONE; proposed: with the M3/P1 milestone controller work) |
| OI-33 | MEASURED (AST over every use) | n/a | - | typed-contract owner (STATE: GenerationState) | ROUTE (criteria 1 and 2 do not hold); see Part 2 |

## Part 1 - per-item characterization

### OI-65 - PLAN_REPAIR round-0 regression (SAFETY-RELEVANT: crash on model-authored input) - CONFIRMED REACHABLE
- (a) Current behaviour, MEASURED through the real entry (`WorkflowController.execute(migration_mode="enforce")`, strict engine over
  a real AppConfig; real `parse_planner_structured_output`, `build_engineering_plan_from_planner_output`, `validate_plan`; only the
  Planner text, the triage route and `run_generation_workflow` scripted): a Planner response that REPEATS a subtask id (`s2`), the
  first copy declaring the provider of a required capability in `depends_on` and the second copy not, raises
  `TypeError: 'NoneType' object is not iterable` out of `execute()` in round 0 (enforce_loop.py `prompt_reason_codes =
  list(retained_reason_codes)`). One Planner call, zero generation calls, RunRecord settles `FAILURE/FAILURE`, the trace row is
  `<run>.enforce / error / TypeError`; a second run in the same workspace proceeds normally (state settled, nothing written).
- Mechanism (CONFIRMED by the discriminating check): `validate_plan` flags `DUPLICATE_SUBTASK_ID` (plan_validation.py:608) but still
  records the per-subtask `SUBTASK_SEMANTIC_CONTRACT` obligations; the same id `plan.subtask.s2.requires.cap` is recorded SATISFIED
  (first copy) then VIOLATED (second copy, SEMANTIC_DEPENDENCY_EDGE_MISSING) in ONE call, which `ObligationLedger.record` reports as a
  regression event; `_semantic_contract_regression_subtasks` returns it (no prompting errors in round 0, so nothing is exempt) and the
  loop takes the retained-baseline branch with no baseline. Negative controls (MEASURED): the same duplicate with VIOLATED first, both
  copies satisfied, and unique ids record NO regression and end typed (`needs_review`, STRUCTURED_PLAN_REPAIR_EXHAUSTED, 3 Planner calls).
  The E12 note ("a regression needs an earlier SATISFIED revision, which round 0 cannot have") is DISPROVED: the earlier revision can be
  an earlier record in the same round.
- (b) Pins: `test_oi65_current_behaviour_a_same_round_regression_in_round_0_raises_typeerror` (killed by m65a `list(retained_reason_codes
  or ())`, m65b regression condition flipped), `..._the_controls_end_as_a_typed_plan_repair_outcome` x3,
  `..._validate_plan_records_a_round_0_regression_only_for_the_downgrade` x4 (killed by m65b).
- (c) Defective: YES. Severity P2: reachable from untrusted model output; an untyped exception instead of the typed PLANNING refusal
  (fail-closed in effect: nothing executed or written, records settled), so not P1. Owner: the decision is enforce_loop's redirect
  (TRACED enforce_loop.py 1087-1112); the trigger is validate_plan recording obligations for a plan it already rejected as having
  duplicate ids. Closing: a dedicated Type-C correction (both: a typed refusal when no baseline exists, and no same-round downgrade
  from duplicate ids), proposed as its own small slice; the E14 extraction must not carry it.

### OI-42 - final review after a terminal-regression failure (human-in-the-loop) - class (ii), NOT an authority defect
- (a) MEASURED (legacy pipeline, `_scripted_run`, human-in-the-loop with an approver, real pytest gate, real sandbox and commit): each of
  the 8 attempts' candidates passes the held gates and fails the terminal full regression after the person approved it. The person is
  asked once per attempt; the Reviewer runs once per attempt at the approval gate (the pre-build review, standard system prompt); the
  final `review` is the last pre-approval review, `review_included_in_approval` is True, no rejected-candidate diagnostic is produced,
  nothing is applied. The CLI prints the final review only when `review_included_in_approval` is false (kriya/cli.py 3737 / 5248), so
  the person sees no review describing the failure.
- Digest table (sha256, first 16 hex; MEASURED in the pin and the probe):

| attempt | bytes the person approved | bytes the terminal regression executed | review shown with the approval (call n saw candidate n) | regression |
|---|---|---|---|---|
| 1..7 | e250e17ca6e01bc0, 8f1387b6166adce8, ec6877c817527788, e645d61fbadc62f4, cdcae255909cb535, 7abca66176c08abc, 420f78fee9fb5125 | identical, attempt by attempt | REVIEW-CALL-1..7 | failed |
| 8 (last) | 60c2ebca8395cab5 | 60c2ebca8395cab5 | REVIEW-CALL-8 | failed |
| final result | - | - | `review` = REVIEW-CALL-8 (the pre-build review of 60c2ebca8395cab5), `review_included_in_approval` True | - |
| applied | NONE: workspace calc.py stays af626eb7a9c3d865 (= base) | | | |

  Durable record: the `approval.decision` event (trace + attempt-evidence mirror) carries `{"outcome", "triggers"}` only - it names NO
  candidate digest (new OI-68).
- Can a stale approval be applied to different bytes? NO, MEASURED twice (GOOD = ad1102fd6d1bc9de, tampered = 41126400563f40a6):
  bytes changed inside the approval callback are refused before any execution (`CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW`, held-gate guard
  `_candidate_is_the_reviewed_one`); bytes changed during the terminal regression are refused by the commit binding
  (`WORKSPACE_COMMIT_NOT_COMPLETED: VERIFIED_CANDIDATE_EVIDENCE_STALE (workspace UNCHANGED)`). A new attempt is approved afresh
  (`begin_attempt` resets the review and the binding; the approval gate runs per candidate). A resumed run regenerates and asks again
  (MEASURED: run 1 = 8 Developer calls / 8 approvals, resume = 1 / 1).
- Classification: **(ii)**, precisely: the review shown is about the RIGHT bytes but is a pre-build review that does not describe what
  finally failed, and the CLI suppresses it; the approval gate re-binds to the right bytes. Not (i). Not (iii) (the held-gate comment in
  development_loop.py states the intended behaviour - "the review shown with the final failure must describe what finally failed" - and
  only the held-gate failure path implements it). Control MEASURED: the autonomous twin produces the rejected-candidate diagnostic (one
  Reviewer call after the loop, a non-standard system prompt, the last failing candidate, `review_included_in_approval` False).
- (b) Pins and killers: reuse pin (m42a: final_review never reuses), autonomous control (m42b: no rejected-candidate system prompt),
  tamper x2 (m42c: the held-gate guard always true; m42d: the commit binding check bypassed), durable-record pin (m42e: a digest key
  added), resume pin (m42f: approval never asked).
- (c) Defective: YES (reporting). Severity P2: the person loses the failure explanation on exactly the runs that need it; no wrong
  bytes reach the workspace. Owner: `stage_review.final_review` (the reuse rule) with the reset scope in
  `development_loop.accept_candidate` (only the held-gate except block drops `pre_approval_review`; `run_terminal_regression` failures do
  not). Not an AUTHORITY_BLOCKER (owner item 9 does not trigger).

### OI-05 / OI-06 / OI-40 (+ OI-69) - result identity and category per terminal path
Table built from the real runs (the kernel golden scenarios; MEASURED and pinned by
`test_oi05_oi06_oi40_current_behaviour_result_identity_and_category_on_every_terminal_path`, 24 ids). Columns: kind of the result
dict's `run_id`; its `failure_category` (`<absent>` = key missing); its `quality_gates_passed`; its `status`; the settled RunRecord's
`retry_state_reference` ("result" = `checkpoint:<result run_id>`); the kind of `WorkflowResult.run_id`; the traces.db rows (status,
failure_category). In every run: each evidence store id is a RunRecord id, and every surviving checkpoint's `_run_record.run_id`
names the RunRecord.

| path | result run_id | failure_category | qgp | status | RR.retry_state_reference | WorkflowResult.run_id | trace rows |
|---|---|---|---|---|---|---|---|
| direct_success | engine (checkpoint) id | None | True | `<absent>` | result | (no WorkflowResult) | success/None |
| legacy_success, legacy_approval_diff_size_trigger | engine id | None | True | `<absent>` | result | RunRecord | success/None |
| enforce_success_with_verification_unit | RunRecord id | `<absent>` | True | success | None | RunRecord | success/None x3 |
| legacy_gates_exhausted | checkpoint id | quality_gates_exhausted | False | `<absent>` | None | RunRecord | failure/quality_gates_exhausted |
| legacy_no_progress | checkpoint id | no_progress | False | `<absent>` | None | RunRecord | failure/no_progress |
| legacy_budget_stop | checkpoint id | generation_budget_exhausted | False | `<absent>` | None | RunRecord | failure/generation_budget_exhausted |
| legacy_static_analysis_blocked | checkpoint id | static_analysis_blocked | False | `<absent>` | None | RunRecord | failure/static_analysis_blocked |
| legacy_commit_revision_conflict | checkpoint id | workspace_commit_failed | False | `<absent>` | result | RunRecord | failure/workspace_commit_failed |
| legacy_final_review_refused | engine id | final_review_refused | False | `<absent>` | result | RunRecord | failure/final_review_refused |
| enforce_verification_only_failed | RunRecord id | `<absent>` | False | failed | None | RunRecord | success, failure/no_progress x2, failed/failed |
| legacy_approval_refused (human_rejected) | engine id | `<absent>` | False | `<absent>` | None | RunRecord | human_rejected/human_rejected |
| legacy_approval_required_no_callback | engine id | `<absent>` | False | `<absent>` | None | RunRecord | approval_required/approval_required |
| enforce_approval_refused | RunRecord id | `<absent>` | False | failed | None | RunRecord | human_rejected/human_rejected, failed/failed |
| legacy_knowledge_gap | trace row id | `<absent>` | `<absent>` | knowledge_gap | None | RunRecord | knowledge_gap/knowledge_gap |
| legacy_planner_output_incomplete / _unauthorized_path / _schema_invalid | trace row id | `<absent>` | `<absent>` | the status | None | RunRecord | status/status |
| legacy_architect_file_list_rejected | trace row id | `<absent>` | `<absent>` | architect_file_list_rejected | None | RunRecord | status/status |
| legacy_baseline_indeterminate (stage-4 prelude) | trace row id | `<absent>` | False | baseline_indeterminate | None | RunRecord | status/status |
| legacy_worktree_sync_refused (stage-4 prelude) | trace row id | worktree_sync_failed | False | failure | None | RunRecord | failure/worktree_sync_failed |
| enforce_plan_invalid | RunRecord id | `<absent>` | False | needs_review | None | RunRecord | needs_review/PLANNING_ERROR |
| legacy_workspace_refused (PRD-008, controller) | = WorkflowResult.run_id, no RunRecord | `<absent>` | False | needs_review | (no settled record) | not a RunRecord | none |
| direct_workspace_refused (PRD-008, engine) | None | `<absent>` | False | needs_review | (no settled record) | (no WorkflowResult) | none |

- OI-05: (a) MEASURED: three identities exist by design (RunRecord id = WorkflowResult id = evidence-store id; the engine's run id =
  checkpoint id; the traces.db row id) and the result dict's `run_id` names a different one per path class (table). Links exist
  (checkpoint `_run_record` -> RunRecord; RunRecord `retry_state_reference` -> checkpoint, but only once the terminal apply ran).
  (c) Consumers TRACED: the CLI resume hint (`--resume-id {res['run_id']}`, kriya/cli.py 3730/5229) is printed only when `files` is
  non-empty, i.e. only on stage-4 paths, where the value IS the checkpoint id (correct); `_mark_run_in_progress` / `trace_id_override`
  (cli.py 3440/3458/3490/3508) read it only after a `knowledge_gap` result, where it IS the trace row id (correct); `application/run.py:184`
  reads the launch record's `run.run_id` (`LaunchSession.run_started`), never the result dict. Defective: NO for every traced consumer
  (discriminating evidence: each consumer receives the identity kind it needs on every path where it reads the key); an ambiguity of the
  result contract (one key, three meanings), P3, owner: the result builders listed in the summary; closing H5 (typed run linking).
  UNKNOWN: external library/MCP callers of `run_generation_workflow` that might read `run_id` as the RunRecord id (none in this repo).
- OI-06 / OI-40 / OI-69: (a) MEASURED (table): `failure_category` is absent from the dict on the approval aborts, the design-stage stops,
  the stage-4 prelude baseline stop, the PRD-008 refusals and every enforce path, while the trace row carries it (= status); the
  design-stage dicts also lack `quality_gates_passed`. (c) Producers TRACED: each stop passes `failure_category=status` to
  `persist_early_exit_trace` and omits it from the dict it returns (stage_knowledge_guard.py:55-68, stage_planning.py:380-390,
  stage_architecture.py:178, development_loop.py:342-350); the approval aborts build their dict in `stage_apply._abort_without_applying`.
  Consumer divergence TRACED: `control/launch.py` `finish()` copies `result.get("failure_category")` and `result.get("status")` into the
  desktop launch record (`ended`), so a human_rejected / approval_required launch records `workflow_status` None and `failure_category`
  None while its trace row says human_rejected; `ui/shared/src/cockpit/core/execution.tsx` renders `ended.failure_category` only when
  present. Metrics / history read the trace row (correct). Defective: YES (contract inconsistency, CLAUDE.md quality bar rule 2), P3
  (the authoritative trace row is right; one display loses the class). Pins killed by m06a (category added to the abort dict), m40a-d
  (category added to each stop dict), m05a-c (identity mutations).

### OI-59 - staging loop's workspace-base fallback
- (a) MEASURED through `WorkflowController.execute` (legacy): the route the item names (a workspace file the sandbox sync does not carry:
  a git-ignored target) never reaches the staging loop: zero Developer calls; the edit-capability decision stops with the typed
  `context_edit_protocol_unsatisfiable` ("calc.py: no authoritative full source"); nothing written. TRACED why: every producer of
  full-source authority reads the sandbox (`CurrentSourceResolver` - once a worktree exists it is authoritative for the run
  (context_source.py 112-119); attempt.py:597 reads `ctx.worktree_path`), so a file the sandbox lacks never has a full/exact item.
  MEASURED downstream if the guard is bypassed (sandbox mutation m59a `full_file = True`): the staging fallback raises the untyped
  BatchCommitError, which the loop records as `general_error` and retries until `no_progress` (4 Developer calls, nothing written).
- (b) Pin: `test_oi59_current_behaviour_a_git_ignored_target_stops_before_the_staging_loop` (killed by m59a). The unit pin in
  tests/test_candidate_transaction.py keeps the fallback itself.
- (c) Defective on the named route: NO (typed refusal). The fallback is latent and fails closed. Reach of the fallback through a real run:
  UNKNOWN - it needs a full/exact authority item recorded from the sandbox for a path the sandbox no longer holds while the workspace
  still holds identical bytes (a file removed from the sandbox after its authority was recorded); no such route was found or built.
  Decisive test: a scripted run in which a gate or an earlier attempt removes a tracked file from the sandbox after attempt 1 recorded
  it, then attempt 2 rewrites it. Severity P3. Owner: `candidate_transaction` (guard `_decide_edit_capabilities`, fallback
  `_stage_candidate_writes`). Closing: G1/G2 (transaction characterization/extraction at the CandidateTransaction boundary).

### OI-63 - milestone ownership migration never records the sidecar
- (a) MEASURED through `WorkflowController(real WorkflowEngine).execute_milestones` (helper re-built in the pin file, no import of another
  test module): run 1 completes M1 and fails M2; the four control documents are made ownerless; run 2 resumes and succeeds. The
  `legacy_workspace_ownership_migrated` decision lists `["checkpoints:1", "contract_registry", "control_state"]`; the sidecar was
  ownerless at entry and is owned at exit, but `milestone_run_state` is not listed.
- TRACED: `execute_milestones` calls `revalidate_completed_milestones` -> `_publish_reuse_assessment` -> `save_milestone_run_state` (the
  re-save stamps ownership) BEFORE `_migrate_legacy_milestone_ownership`, whose `json_document_is_ownerless(sidecar)` branch is then
  false. CONFIRMED by mutation m63a (the re-save removed): the decision then lists `milestone_run_state` and the pin fails.
- (c) Defective: YES (audit completeness only; ownership, refusal and resume decisions unchanged). P3. Owner: the call order in
  `WorkflowController.execute_milestones` (the controller composes it; resume_support owns the migration body). Closing: a
  milestone/resume evidence slice (proposed with the later milestone controller work; D2 is DONE).

## Part 2 - OI-33 `RunState = Any` (read-only evidence)
Method: AST scan (scratchpad `oi33_scan.py`) of every module naming `RunState` (14 modules: 12 define `RunState = Any`, outcome.py and
run_lifecycle.py import it from stage_context). 89 annotation uses, 0 runtime loads (no isinstance/construction/attribute use of the
name), 0 Protocol classes.
- Per module (uses): candidate_transaction 21 private params; runtime_verification 11; gate_orchestrator 9 private + 1 public;
  repair_orchestrator 9; spec_compliance 7; generation_service 5; fallback_decision 4; decision_evidence 2; attempt_lifecycle 6 public;
  outcome 4 public; run_lifecycle 3 public params + 1 public return; stage_apply 3 public; stage_context 3 public dataclass fields;
  development_loop 0 (defined, unused).
- (1) Internal/local typing only? NO: 68 of 89 are private parameters, but 21 are on module contracts.
- (2) Crosses a public/module contract? YES, 21 uses: public functions `attempt_lifecycle.plan_attempt/begin_attempt/record_phase_advanced/
  run_iteration` (incl. the `run_attempt`/`handle_attempt_failure` callable parameters), `gate_orchestrator.apply_pre_review_candidate_corrections`,
  `outcome.terminal_failure_category/failure_report_dicts/final_review_refusal_payload/completed_result`, `run_lifecycle.open_run_state`
  (return), `trace_run_events/persist_run_trace/persist_early_exit_trace`, `stage_apply.run_static_analysis_gate` (incl.
  `_captured_originals`)/`run_semantic_safety_gate`; Deps/Context fields `StageDeps.new_state`, `StageDeps.trace_events`,
  `StageContext.state`. No Protocol uses it.
- (3) Persisted/serialized as ambiguous state? NO: an annotation alias only; GenerationState persistence is unaffected.
- (4) Hides an authority/lifecycle distinction? NO measured case: every use stands for the one in-memory GenerationState; no decision keys on
  the type; ControlState / MilestoneRunState keep their own names. (Statically nothing stops a wrong state object, but no such call exists.)
- (5) Practical benefit of replacing `Any`? LIMITED today: no static type checker is part of any gate (no mypy/pyright in pyproject.toml or
  .github/workflows); the gain is a declared state-access surface per owner (architecture/documentation) and IDE checking.
- Cycle link: `Any` is the mechanism that keeps the owner modules out of the 67-module all-import SCC (stage_context.py docstring: even a
  TYPE_CHECKING import counts as a top-level edge). A Protocol in a NEW LEAF module (importing only `typing`) adds only owner -> leaf
  edges; a module with no outgoing kriya edge cannot be on any cycle, so the SCC cannot grow (TRACED by construction; the scc_sim.py planted
  control "leaf-constant move into a new module stays 67" is the same case). It must not import `kriya.workflow.state`.
- Conclusion: **ROUTE** (criteria 1 and 2 do not hold). Owner: the typed-contract owner of GenerationState (STATE); a leaf
  `RunState` Protocol declaring the attributes each contract reads; closing with the cycle-removal / typed-contract work (P2 or Q1), not
  E14 (Type A).

## Part 3 - test-harness group (OI-34 / OI-43 / OI-45 / OI-53)
(Filled in by the Part 3 commits; see below.)
