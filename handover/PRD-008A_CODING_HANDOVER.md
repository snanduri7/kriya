# PRD-008A Coding Agent Handover — Unified ExecutionPlan / WorkUnit Orchestration

## Status
READY_FOR_PYTEST_VERIFICATION (2026-09-24). Slices A1–A7 implemented as one batch, one local commit per slice, all unpushed.
A8 (focused pytest, full non-live pytest, live-model cases) is the user's. Not marked VERIFIED.

## Current-state paths found (traced from source before any edit)

**Direct** (`kriya generate`, `fix`, proposal promotion):
- controller disabled: `cli._dispatch_generation` → `WorkflowEngine.run_generation_workflow` (`@coordinated_mutation`).
- controller enabled: `WorkflowController.execute` (`@coordinated_mutation`) → legacy/shadow: `_run_legacy_generation` → `run_generation_workflow`; enforce: `_run_structured_enforce` (own subtask loop, one `run_generation_workflow` per subtask).
- Resume: `run_generation_workflow` picked `find_latest_checkpoint()` — the newest checkpoint in the workspace, whichever unit (milestone or direct) saved it; only fingerprint mismatch stopped a cross-unit pick. Direct checkpoints saved `work_unit=None`.

**Milestone** (`generate --from-milestones`):
- `cli._dispatch_milestones` → `run_milestones` (`@coordinated_mutation`), or `WorkflowController.execute_milestones` → `run_milestones(authoritative=True)`.
- `run_milestones` was a second orchestrator: its own topological loop, completed-skip (`if id in completed_milestone_ids`), active-unit annotation (`_set_work_unit`), per-unit checkpoint selection (`select_unit_checkpoint`), stop-on-first-failure (later units simply never ran, nothing recorded as BLOCKED), replay, then an integration call (its own unit kind), and its own success/`milestone_failed`/`integration_failed`/`milestone_replay_failed`/`artifact_drift`/`artifact_registry_failed`/`dependency_regression` statuses.
- Completion reuse: PRD-008 S4b/S4c `revalidate_completed_milestones` (proof + ledger + reconstruction), milestone-only.
- The CLI never validates a hand-edited plan file; `topological_order()` silently dropped milestones caught in a cycle or depending on an unknown id (they never ran, the sequence could still report success).

**Shared already:** RunCoordinator ownership/lock/commit-state gate (`begin_mutating_run`), RunRecord, commit seam, PRD-008 `validate_resume_against_reality`, the generation primitive.
**Duplicated/milestone-specific:** ordering, skip/reuse, active-unit record, checkpoint selection (milestone only), failure/terminal decision, integration pass.

## `run_generation_workflow` call sites (every one labelled)
| Call site | Kind | Marker |
|---|---|---|
| `cli._dispatch_generation` | top-level intent (direct) | none → one-unit plan |
| `cli` `fix` command | top-level intent (direct) | none → one-unit plan |
| `WorkflowController._run_legacy_generation` | top-level intent inside the controller-owned run | none → one-unit plan |
| `proposal_promotion.execute_approved_proposal` | top-level intent (direct) | none → one-unit plan |
| `_MilestonePlanDriver.run_unit` (milestone + integration) | unit invocation | `WorkUnitInvocation(MILESTONE, …)` |
| `WorkflowController._invoke_bounded_subtask` | unit invocation (STRUCTURED) | `WorkUnitInvocation(STRUCTURED, plan_id, subtask_id, plan hash)`; `active_work_unit` stays None |

## Final architecture
```
user intent ─┬─ direct goal ──────► direct_execution_plan()    ─┐
             └─ MilestoneRunState ► milestone_execution_plan() ─┴► execute_plan(plan, driver)
                                                                    │ per unit, in plan order:
                                                                    │  select_work_unit_checkpoint → run_generation_workflow(work_unit=…)
                                                                    ▼
                                           one lifecycle / active-unit record / terminal decision
```
- `run_generation_workflow(work_unit=None)` = new intent → `execute_direct_goal` → one-unit plan → calls back with its `WorkUnitInvocation`. A new intent inside a running unit raises `NestedExecutionPlanError`.
- `source_kind` is provenance only; the executor never branches on it. Remaining shape-based rules: an explicit `--resume-id` in a one-unit plan passes to PRD-008 (below); a multi-unit plan result carries `work_unit_states`.

## Files changed
Base: `282b306` (DEF-DEV-EMPTY-ARRAY PYTEST_VERIFIED at `fd42e01`, full suite 5106/0/8).

| Slice | Commit | Content |
|---|---|---|
| A1 | `e1d0e4c` | ExecutionPlan/WorkUnit contracts, validation, order, fingerprint, serialization |
| A2 | `eacac0d` | direct adapter |
| A3 | `933d11b` | milestone adapter |
| A4 | `39cb5c2` | common executor; both modes routed through it |
| A5 | `2181aec` | WorkUnit-aware checkpoint selection; common completion reuse |
| A6 | `5f3c846` | common terminal decision (RunCoordinator); cross-path invariant suite |
| A7 | `6a90ff1` | cleanup, docs (design §2.9a, user_guide §3.4/§3.4.1, CLAUDE.md) |
| A6b | `594fa16` | fix-up: exception in preconditions/selection/phase fails the unit (was left PENDING); lock, commit-eligibility, outside-write cross-path invariants |
| handover | `eefb01d` + follow-up | |

- New: `kriya/workflow/execution_plan.py`, `kriya/workflow/plan_adapters.py`, `kriya/workflow/plan_executor.py`.
- `kriya/workflow/workflow.py` — `work_unit` parameter; new-intent wrapper (11 lines).
- `kriya/workflow/milestones.py` — `run_milestones` builds the plan and delegates its loop to `execute_plan` via `_MilestonePlanDriver` (the old loop body moved verbatim into the driver hooks); `_set_work_unit` removed.
- `kriya/workflow/milestone_completion.py` — `select_unit_checkpoint`/`_same_unit` delegate to the common selector; `milestone_unit_record`; selection codes aliased.
- `kriya/workflow/workflow_controller.py` — STRUCTURED marker on subtask calls.
- `kriya/control/run_record.py` — optional `execution_plan`, `work_unit_states` (annotatable; old records load).
- `kriya/control/run_coordinator.py` — `_complete_successful_run` refuses SUCCESS with a non-VERIFIED unit.
- `kriya/cli.py` — docstring only.
- Docs: `docs/design.md` §2.9 + new §2.9a, `docs/user_guide.md` §3.4/§3.4.1, `CLAUDE.md`.
- Tests (new): `test_prd008a_execution_plan.py` (29), `test_prd008a_plan_adapters.py` (15), `test_prd008a_plan_executor.py` (18), `test_prd008a_resume_convergence.py` (12), `test_prd008a_cross_path.py` (19). No existing test modified.

## Schemas
**ExecutionPlan** (schema 1): `plan_id`, `source_kind` (direct|milestone|structured), `work_units` (declared order), `commit_strategy` (`incremental_work_unit`; `atomic_plan` declared, fails validation `UNSUPPORTED_COMMIT_STRATEGY`), `terminal_phases` (`replay_prior_verifications`), `provenance` (not fingerprinted), `fingerprint` = SHA-256 over canonical JSON of schema/source/strategy/phases/units in declared order. `from_dict` refuses a stored fingerprint that no longer matches (`PLAN_FINGERPRINT_MISMATCH`) and unknown schemas.
**WorkUnit**: `id`, `goal`, `definition_digest` (adapter-supplied), `role` (primary|integration), `acceptance_criteria` ((id, description)), `depends_on`, `provides`, `consumes`, `obligation_refs`, `verification_requirements`, `provenance` (fingerprinted). Frozen; sequences coerced to tuples.
**Validation codes**: `EMPTY_PLAN`, `INVALID_WORK_UNIT`, `DUPLICATE_WORK_UNIT_ID`, `MISSING_DEPENDENCY`, `DEPENDENCY_CYCLE`, `DIRECT_PLAN_UNIT_COUNT`, `INTEGRATION_UNIT_DEPENDENCIES`, `UNSUPPORTED_COMMIT_STRATEGY`. All issues reported together; an invalid plan cannot be constructed.
**WorkUnitState** (runtime, never hashed): `work_unit_id`, `status` PENDING/RUNNING/VERIFIED/FAILED/BLOCKED/STALE, `reason_codes`, `blocked_by`. COMMITTED is not a unit state: commit strategy is per unit and the commit itself stays in the RunRecord cycles.
**Identity layering (sign-off item 2):** `plan.fingerprint` = provenance/audit/revision detection, **not** a reuse key. Reuse keys stay the per-unit `definition_digest` (+ S4b's upstream proof identity). Adding an unrelated unit or changing an unrelated unit's definition changes the plan fingerprint but does not invalidate an unaffected unit — the PRD-008 S4b rule, which a plan-level key would have regressed.

## Adapters
- **Direct**: one unit `id="direct"`, goal verbatim, digest = SHA-256(goal) (= RunRecord `goal_hash`), no decomposition, no model call. Record `{"kind":"direct","group_id":null,"milestone_id":null,"definition_digest":null,"work_unit_id":"direct"}` — no digest, because PRD-008's goal fingerprint judges the goal (matching on it would pre-empt that validator's typed decision).
- **Milestone**: unit per milestone (declared order), digest = `milestone_definition_digest` (unchanged), provenance = full `MilestoneV2.model_dump`; INTEGRATION unit `__integration__` depending on all, goal = `build_integration_goal_text`, digest = `_plan_digest(ordered)`; records produced by `milestone_completion`'s own helpers → byte-identical to S4c records. Legacy v1 plan files adapt through the existing `MilestoneRunState.from_dict` normalization.
- **Integration pass decision**: a WorkUnit (it calls the generation primitive, commits, has its own checkpoint identity); deterministic replay is a plan-wide phase (no model, no writes) run after every PRIMARY unit is VERIFIED and before the integration unit. A replay failure BLOCKS the integration unit (`TERMINAL_PHASE_FAILED`).

## Common executor ownership (`execute_plan`)
Requires the caller's RunCoordinator ownership (`require_mutating_run`); owns: plan validation (by construction), order (Kahn, ties by declared position = `topological_order` parity, tested), lifecycle + `RunRecord.execution_plan`/`work_unit_states`, active unit record (set per unit, cleared in `finally`), checkpoint selection, completion reuse, dependency blocking, plan phases, terminal decision, exception handling (unit FAILED `WORK_UNIT_EXCEPTION`, rest BLOCKED, exception re-raised so the RunCoordinator's UNCERTAIN/FAILURE decision still sees it).
`PlanDriver` hooks (source-specific bookkeeping only): `before_unit` (milestone: control state, banner, artifact drift → stop), `run_unit`, `check_passed_unit` (milestone: dependency-drop guard can only downgrade), `retry_failed_unit` (milestone failure callback), `complete_unit` (ledger, judge capture, `_complete_milestone`), `unit_failed`, `run_phase` (replay), `plan_result`, `on_checkpoint_selection`. Milestone result dicts and their statuses are unchanged apart from the added `work_unit_states`.

## Resume/checkpoint changes (A5)
- One selector, `select_work_unit_checkpoint`, for every unit. Implicit `--resume` → the newest checkpoint of **this unit** (kind/group/milestone/definition digest). Direct: direct checkpoints, plus pre-PRD-008A direct checkpoints (no `work_unit`, no `milestone_group_id`) via a compatibility reader. Milestone: unchanged S4c rule (pre-S4c checkpoints never offered).
- Explicit `--resume-id`: in a one-unit plan it can only mean that unit → passed to PRD-008, which returns its typed `resume_refused` on mismatch (unchanged direct behaviour — pre-filtering would have turned that into a silent fresh run spending model calls). In a multi-unit plan the id reaches every unit, so only its own unit takes it (S4c `CHECKPOINT_IDENTITY_MISMATCH`).
- **Behaviour change (strengthening):** a checkpoint owned by another workspace now raises `WorkspaceOwnershipError` from the selector for milestones too (previously swallowed and skipped).
- **Behaviour change:** direct `--resume` with only milestone checkpoints present now starts fresh ("No saved checkpoint found for this work unit") instead of loading the milestone checkpoint and having PRD-008 refuse it.
- PRD-008 `validate_resume_against_reality` remains the only reuse validator; checkpoint key = run_id (checkpoint file) + unit record (plan/unit) + stage.

## Completion reuse changes
- The executor skips only `reusable_unit_ids` = what PRD-008 S4b/S4c revalidation (and contract invalidation) left in `completed_milestone_ids`; they are `VERIFIED/COMPLETION_REUSED`. The old milestone-only skip branch is gone. Stale ids start `STALE/STALE_COMPLETION`.
- Coherence guard: a reusable unit with a non-reusable ancestor → nothing runs, `needs_review` / `COMPLETION_REUSE_INCONSISTENT` (S4b already reruns descendants, so this can only fire on a defect in the evidence handed over).
- Direct plans have no cross-run completion reuse (each `generate` is a new intent); their only reuse is checkpoint resume.
- S4b/S4c rules (COMMITTED_CHANGE, VERIFIED_NO_CHANGE, lineage, byte verification, recovery-origin, shared-write invalidation) are unchanged and still decide; not reimplemented.

## Dependency/terminal behaviour
- Policy unchanged: sequential, stop at the first failed unit. Now recorded: dependents `BLOCKED/DEPENDENCY_FAILED`, independent later units `BLOCKED/PLAN_HALTED_AFTER_FAILURE`, each with `blocked_by`. Precondition stop (artifact drift) = unit `BLOCKED/WORK_UNIT_PRECONDITION_FAILED`.
- Plan success only when every unit is VERIFIED; a result claiming success otherwise → `needs_review/PLAN_TERMINAL_MISMATCH`. The RunCoordinator independently refuses SUCCESS while `work_unit_states` has a non-VERIFIED unit (negative-controlled).
- **New fail-closed refusal:** a hand-edited milestone plan with a cycle/unknown dependency → `milestone_plan_invalid` with `plan_issues`, before any work (previously those milestones were silently dropped).

## Compatibility/migrations
- No persisted format migrated. RunRecord gains two optional fields (old records load; strict loader unchanged). Checkpoints gain a `work_unit` for direct runs; old checkpoints stay readable. Milestone sidecars/plan files unchanged; legacy v1 plan files adapt. `select_unit_checkpoint`, `_same_unit`, `milestone_work_unit`, `integration_work_unit` and the S4c selection code constants remain importable.

## Focused pytest
```
.venv/bin/pytest tests/test_prd008a_execution_plan.py tests/test_prd008a_plan_adapters.py tests/test_prd008a_plan_executor.py tests/test_prd008a_resume_convergence.py tests/test_prd008a_cross_path.py tests/test_milestones.py tests/test_milestone2.py tests/test_milestone3_4.py tests/test_prd008_s4b_milestone_completion.py tests/test_prd008_s4c_milestone_resume.py tests/test_prd008_resume_fingerprints.py tests/test_resume_integrity.py tests/test_state001_checkpoint_workspace_identity.py tests/test_prd007_run_lifecycle.py tests/test_run_ownership.py tests/test_prd008_commit_state_gate.py tests/test_prd008_recovery.py tests/test_workflow_controller.py tests/test_workflow_controller_enforce.py tests/test_dispatch_generation.py tests/test_developer_file_list_answer_as_content.py tests/test_proposal_promotion.py tests/test_control_contracts.py tests/test_cli_smoke.py
```
If something fails, each commit message lists the files its slice most likely affects (bisect A4 → A5 → A6 → A7 → A6b).

**Coding-agent pre-check (plain-Python runner, NOT pytest; pytest is authoritative - manual runs have diverged before).**
- At `594fa16`: the five new files 29+15+18+12+19 = 93/93; `test_prd008_s4c_milestone_resume` 45/0, `test_prd008_s4b_milestone_completion` 28/0, `test_milestones` 68/0.
- At `eefb01d` (= A7 code): `test_workflow` 862 ok / 1 fail / 5 skipped (runner lacks `caplog`), `test_workflow_controller_enforce` 271/0. The one failure, `test_resolve_jdk_home_for_version_falls_back_to_bin_java_heuristic_on_linux`, fails identically on the pre-PRD-008A baseline under the runner (runner artifact); so does `test_milestone3_4::test_staged_skill_accrual`.
- At `2181aec`-`6a90ff1` (after the last change to the code they exercise): `test_prd008_resume_fingerprints` 97/0, `test_resume_integrity` 20/0, `test_state001_checkpoint_workspace_identity` 35/0, `test_prd007_run_lifecycle` 28/0, `test_run_ownership` 32/0, `test_prd008_commit_state_gate` 26/0, `test_prd008_recovery` 23/0, `test_proposal_promotion` 52/0, `test_developer_file_list_answer_as_content` 32/0, `test_workflow_controller` 32/0, `test_cli_smoke` 65/0, `test_dispatch_generation` 5/0, `test_control_contracts` 40/0.
- Negative controls: the direct unit-aware resume test fails with the pre-A5 pass-through selector; the RunCoordinator terminal test fails with its guard stubbed.

**PRD required test → coverage.**
| PRD item | Where | Note |
|---|---|---|
| Contracts: serialization, hash, duplicate/missing/cycle, topo order, direct = 1 unit, milestone conversion | `test_prd008a_execution_plan.py`, `test_prd008a_plan_adapters.py` | |
| Direct parity: success, failure, checkpoint, resume, recovery-required refusal, commit, terminal, one unit | `test_prd008a_plan_executor.py` (direct section), `test_prd008a_cross_path.py` | |
| Direct parity: retry, candidate reuse | existing `test_workflow.py`, `test_prd008_resume_fingerprints.py` (now running through the one-unit wrapper) | indirect only |
| Milestone: sequential, failed dependency, blocked downstream, completed-unit reuse, invalid plan | `test_prd008a_plan_executor.py`, `test_prd008a_cross_path.py` | |
| Milestone: acceptance criteria / provides / consumes preservation | `test_prd008a_plan_adapters.py` | |
| Milestone: independent units | `test_prd008a_plan_executor.py` (scripted driver: `PLAN_HALTED_AFTER_FAILURE`) | scripted driver only |
| Milestone: obligation propagation | none new - no milestone carries obligation-ledger input today (risk 5) | gap by design |
| Milestone: crash/resume within unit, recovery-completed unit, shared-file invalidation | existing S4b/S4c suites, unchanged, now running through the executor | |
| Cross-path: lock, RunRecord, commit-state gate, PRD-008 validation, checkpoint ownership, commit eligibility, terminal, mutation authority (outside-workspace write), verification (failure never commits) | `test_prd008a_cross_path.py`, parameterized direct/milestone | mutation authority is outcome-level only |
| Cross-path: recovery | not parameterized - recovery engine untouched and path-agnostic; `test_prd008_recovery.py` + S4c crash tests | single-path |
| Plan drift: goal, criterion, dependency, new unit | `test_prd008a_resume_convergence.py` | "reorder only independent units" not tested |
| Checkpoint identity W1/W2, PRD-008 still validates | `test_prd008a_resume_convergence.py` | |
| Completion reuse: committed / no-change / recovered / stale bytes / missing evidence / shared writer | existing S4b/S4c suites (rules unchanged) + reuse-coherence guard test | |


## Full suite
```
.venv/bin/pytest
```

## Live-model instructions (after pytest; current target local model, e.g. qwen3-coder:30b)
Use a scratch git repo under `~/kriya-live-validation/` (existing convention). After each case capture:
`ls -t .kriya/control/runs/*.json | head -1 | xargs python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print(json.dumps({k: r[k] for k in ('lifecycle_state','execution_plan','work_unit_states','resume_decision','milestone_reuse','commits')}, indent=2))"`,
plus `git diff`, the run log, and `ollama ps`/model digest.
1. **Simple direct** — `calc.py` with `add`; `kriya -c kriya.yaml generate "Add a subtract(a, b) function to calc.py" -y`. Expect `execution_plan.source_kind=direct`, one unit `direct` VERIFIED, SUCCESS, commit cycle `work_unit.kind=direct`, no milestone planning call in the log.
2. **Multi-unit** — hand-write `.kriya/milestones/g1.plan.json` with M1 (module), M2 depends M1 (function using it), M3 depends M2 (CLI entrypoint), then `generate --from-milestones … -y`. Expect `source_kind=milestone`, units M1, M2, M3, `__integration__` in order, all VERIFIED, SUCCESS.
3. **Deterministic W2 failure** — same plan, with `autonomy.mode: human-in-the-loop` in the scratch `kriya.yaml` and **without** `-y`: approve M1's changes (`y`), **reject** M2's at the approval gate (`n`) - that returns `human_rejected` with `quality_gates_passed=false`, no timing involved - then answer `abandon`. Expect JSON status `milestone_failed` (M2), `work_unit_states`: M1 VERIFIED, M2 FAILED, M3 and `__integration__` BLOCKED `DEPENDENCY_FAILED` blocked_by M2, RunRecord FAILURE. Rerun with `-y`: M1 `COMPLETION_REUSED`, M2/M3/integration run.
   (Stopping the model server instead is not deterministic: a transport error during planning raises out of `run_generation_workflow` - the planner call is not wrapped - so M2 is `FAILED/WORK_UNIT_EXCEPTION`, the CLI prints "Milestone sequence error" with no JSON result, and only the RunRecord shows the BLOCKED units; during Developer attempts it exhausts retries and returns `milestone_failed`. Either way the invariant is M3/integration BLOCKED `DEPENDENCY_FAILED`.)

## Remaining risks / sign-offs for the user
1. **STRUCTURED scope**: the enforce-mode subtask loop still orchestrates its own subtasks (own resume via ControlState, TOOL-subtask resume exclusion, completion skip). Its calls now carry a STRUCTURED `WorkUnitInvocation` and stay otherwise unchanged. Converging it onto `execute_plan` is a separate decision (it is not named in PRD-008A's acceptance criteria).
2. **Plan fingerprint vs unit identity** (above): a plan-fingerprint change alone does not invalidate an unaffected unit. This preserves S4b and the PRD's own "independent unaffected unit" requirement; it is the one place the PRD text ("changed plan identity/fingerprint invalidates") is read narrowly.
3. **Explicit `--resume-id` on a direct run** keeps PRD-008's typed refusal rather than an identity pre-filter (single-unit rule).
4. **Known parity quirk kept**: when contract invalidation replaces `run_state.milestones` mid-run, execution still follows the plan built at the start (exactly what the pre-PRD-008A loop did with its up-front `ordered` list).
5. `obligation_refs` is empty for both adapters: no milestone carries obligation-ledger inputs today, and a direct run's obligations stay in the per-run ledger restored by PRD-008 resume fingerprinting; nothing new was invented.
6. Only manual-runner evidence so far; pytest and the live cases are pending.
7. Behaviour changes to confirm: a foreign-workspace checkpoint now raises (milestones too); direct `--resume` ignores milestone checkpoints; a cyclic/unknown-dependency milestone plan is refused up front.
