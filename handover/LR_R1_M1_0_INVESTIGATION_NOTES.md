# LR-R1-M1.0: read-only investigation notes

**Scope:** read-only. No Kriya production code, test, config, `~/.kriya`, Ollama, experiment arm, PROTOCOL_v2 or CAGC
evidence was changed or read for outcomes.
**Isolated workspace:** a `git archive` export of the common base
`61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03` into the session scratchpad (`base61/`). All paths and line numbers refer
to that revision.
**Design:** `handover/LR_R1_M1_ATTEMPT_EVIDENCE_RECORDER_DESIGN.md` (approved with changes, 2026-10-04).
**Labels:** MEASURED, TRACED, INFERRED, UNKNOWN.

## 1. `GenerationState.gate_outcomes`: exact-list-type dependency check (owner addition 1)

**Method:** `grep -rn gate_outcomes kriya tests`. There are 152 references in `kriya/` and 164 lines in `tests/`
(MEASURED). Every non-append reference was read.

**Results:**

| Question | Finding | Status |
|---|---|---|
| Declaration | `state.py:675` `gate_outcomes: List[Dict[str, Any]] = field(default_factory=list)` | TRACED |
| Append sites | 85, all with receiver `state.gate_outcomes.append` (72 `attempt.py`, 12 `workflow.py`, 1 `retry_strategy.py`) | MEASURED |
| Other mutation (`extend`/`insert`/`+=`/slice assign/`clear`/`pop`/`sort`/`remove`) | none in `kriya/` or `tests/` | MEASURED |
| Exact-type checks (`type(...) is list`, `type(...) == list`, `isinstance(..., list)` on it) | none in `kriya/` or `tests/` | MEASURED |
| `dataclasses.asdict`/`deepcopy`/pickle/`replace` of the state, `vars(state)` | none | MEASURED |
| Readers | iteration and comprehension (`attempt.py:2356`, `retry_strategy.py:1114,1414`, `workflow.py:540,4451,4590,4663,5823`); passing as a `Sequence`/`List` argument (`verified_no_change.py:73`, `review_context.py:28`, `workflow.py:528,618`); `TraceLogger.log_run` `json.dumps(gate_outcomes or [])` (`trace.py:134`) | TRACED; all type-agnostic |
| Equality in tests | `test_best_of_n.py:74`, `test_finite_command_artifact_preparation.py:85,113`, `test_workflow.py:13853` compare with plain list literals; a subclass would compare equal | TRACED |
| **Reassignment** | **production:** `attempt.py:6391` `state.gate_outcomes = list(ctx.resume_state.get("gate_outcomes") or state.gate_outcomes)` (resume with skipped candidate gates). **Tests:** `test_best_of_n.py:33` assigns a plain list; `test_finite_command_artifact_preparation.py:53` is a fake state with a bare list attribute | MEASURED |

**Conclusion:**
- There is no exact-list-type dependency.
- A list subclass is still **rejected**. The production resume path reassigns the attribute to a plain `list`, and from
  then on every append would go unmirrored with no error. A subclass cannot detect being replaced.
- The design now uses an explicit `GenerationState.record_gate_outcome()` / `restore_gate_outcomes()` pair plus an
  AST tripwire (design §5.2, T13).

**Remaining for M1.0 under the implementation authorization:** enumerate every test fake state that production append
paths reach (one known: `test_finite_command_artifact_preparation.py:53`).

## 2. Thread hand-offs and ContextVar propagation (design risk R6)

`asyncio.to_thread` runs the function in a copy of the caller's context. `loop.run_in_executor` and plain executors do
not.

| Site | Mechanism | Evidence emit inside? | Status |
|---|---|---|---|
| `workflow.py:503, 1843, 2814, 3287, 3733, 4088, 4100` | `asyncio.to_thread` | no direct emit; context copied anyway | TRACED |
| `attempt.py:10145` | `asyncio.to_thread` | no | TRACED |
| `terminal_gate_service.py:447, 469` | `asyncio.to_thread` | requirement-closure gates; context copied | TRACED |
| `core/llm.py:370, 531` | `asyncio.to_thread` (served-context observation, runtime fingerprint) | no; the call record is emitted on the loop thread | TRACED |
| `tools/process.py:222, 225` | `threading.Thread` stdout/stderr pumps | no | TRACED |
| `mcp/mcp.py` | `run_in_executor` (container cleanup) | no | TRACED |

**Conclusion:**
- Every relevant hand-off at 61a867f copies context. INFERRED: scope attribution holds on today's paths.
- **UNKNOWN until tested:** whether the `PolymorphicValidator` gate methods always run on the loop thread or inside one
  of the `to_thread` calls above. Either way context propagates, but this must be confirmed by a probe test.
- Planned guard: a structural test fails on a new `run_in_executor`/`ThreadPoolExecutor` in the evidence-emitting
  packages unless it is classified.

## 3. Run scope boundaries

| Fact | Evidence | Status |
|---|---|---|
| Mutating commands open one run scope around the whole workflow, Planner included | `cli.py:751` (tools execute, mutating only), `2671` (milestones), `3131` (generate), `4076` (proposal execute), `4537` (fix) | TRACED |
| Nested workflow/controller boundaries reuse the same RunContext | `run_coordinator.py:386-391` | TRACED |
| Non-mutating commands (`ask`, `review`, `plan-milestones`, qualify, doctor) have no RunContext | no `begin_mutating_run` around them | INFERRED (spot-checked, not exhaustive) |
| Resume gets a new run id | `begin_mutating_run(run_id=None)` at all five sites; resume provenance in `RunRecord.resume_decision` | INFERRED; confirm by test |

## 4. Model-call boundary

| Fact | Evidence | Status |
|---|---|---|
| All inference is inside the runtime adapters | `chat.completions.create` ×3 (`inference_runtime.py:288, 304, 332`), native `http.post/stream` ×3 (`model_runtime.py:1288, 1300, 1323`) | MEASURED |
| 42 `complete*` call sites outside `llm.py`, all through `LLMClient` | grep | MEASURED |
| Wire requests per logical call: initial, `response_format` drop, empty-content floor | `llm.py:952, 972, 1008`; tools `llm.py:1236` | TRACED |
| Raw transport content is available pre-split | `raw["content"]` before `split_reasoning` (`llm.py` complete_result) | TRACED |
| Raw Developer completions are logged only at DEBUG | `agent.py:~1991` `logger.debug(...)`; default level INFO (`default_config.yaml:51`); the experiment config snapshots have no `logging:` section | TRACED + MEASURED; that the 80 runs therefore hold no raw completions is INFERRED |

## 5. Not done here (needs implementation authorization)

- Probe tests for validator thread placement, nested-run ContextVar reuse and resume run-id semantics.
- The complete `fit_*` site inventory.
- The test-fake-state inventory for `record_gate_outcome`.
- Anything that executes Kriya code.

## 6. M1.0 executable completion (implementation authorization, 2026-10-05)

**Implementation base.** `feature/lr-r1-m1` was cut from `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03` (origin/main),
the exact revision the design was traced against.
- The main checkout's `0e12535` (`feature/cagc-r1`) does **not** contain 61a867f (merge-base `0bea22f`). It carries
  CAGC-0, which is on HOLD, so M1 is not built on it. MEASURED.
- Every design line reference was re-checked at the implementation HEAD: `state.py:675/737/936`,
  `attempt.py:328/2003/2033/2076/2113/2551/6248/6326/6391`, `llm.py:806/952/972/1008/1131/1236`, `validate.py:248`,
  `decisions.py:94`, and the 85 append sites (72 + 12 + 1). All hold. MEASURED.

**Probes** (permanent tests, `tests/test_lr_r1_m1_0_inventory.py`, 8 tests):

| Fact | Result | Status |
|---|---|---|
| A scope ContextVar reaches `asyncio.to_thread` work | yes | CONFIRMED (test) |
| Validator gates (`_verification_gate`) run synchronously on the caller's thread with its context, directly and inside a `to_thread` hand-off (requirement-closure validators) | yes. Validators are called synchronously from the attempt (`attempt.py:8600/8828/...`); the closures run inside `to_thread` (`workflow.py:4088/4100`, `terminal_gate_service.py:447/469`) | CONFIRMED (test). The notes §2 UNKNOWN is closed |
| Nested `begin_mutating_run` reuses the active RunContext | yes | CONFIRMED (test) |
| Resume run id | a fresh id per top-level run. No production caller passes `run_id` (5 call sites, AST test); the resume link is `RunRecord.resume_decision` | CONFIRMED (test + structural). Design §11/R7 INFERRED → CONFIRMED; no `segment-<n>` path is needed |
| Non-copying thread hand-offs in evidence-emitting modules | none (`run_in_executor` only in `kriya/mcp/mcp.py`; `threading.Thread` only in `kriya/tools/process.py`) | structural test, with a planted negative control |

**Complete `fit_*` inventory** (MEASURED, `grep` at 61a867f):

| Fitter (`context_budget.py`) | Callers |
|---|---|
| `fit_variable_section` (primitive, :724) | inside the fitters below only |
| `fit_spec_compliance_files` (:791) | `agents/agent.py:3027` (spec-compliance verifier) |
| `fit_reference_section` (:828) | `fit_planner_request` (:923); `workflow_controller.py:4240` (enforce Planner) |
| `fit_structural_evidence` (:851) | `workflow_controller.py:4225` (enforce Planner) |
| `fit_planner_request` (:895) | `workflow.py:2925` (Planner), `workflow.py:3203` (Architect) |
| `fit_developer_request` (:976) / `DeveloperRequestFit` (:1056, :1071) | `attempt.py:1815` (investigation), `attempt.py:2566` (Developer choke point) |
| `fit_review_batches` (:1125) | inside `review_requests` (:1219), called from `workflow.py:4327`, `workflow.py:5573`, `cli.py:3380` (`kriya review`, non-mutating: no store) |

**Deviation (design §3.3/§13 M1.6).** The design names four fitters for `prompt.sections`. The enforce structured
Planner fits through `fit_structural_evidence` + `fit_reference_section`, and the spec-compliance verifier through
`fit_spec_compliance_files`. M1.6 therefore hooks the shared primitive `fit_variable_section` (which every fitter goes
through) plus `fit_developer_request`, so no fitter is missed.

**Fake-state inventory for `record_gate_outcome`** (MEASURED):
- Production reassignment: `attempt.py:6391` only → `restore_gate_outcomes`.
- Test reassignments of a real `GenerationState`, which stay legal setup in tests; the tripwire scope is `kriya/`:
  - `tests/test_best_of_n.py:33`;
  - `tests/test_planner_test_stage_no_change.py:130,135,138`. This one was **missed in §1**.
- Fake state objects with a bare `gate_outcomes` list: `tests/test_finite_command_artifact_preparation.py:53`.
  Whether a production append path reaches it is settled by running the test file after M1.3.
- `gate_outcomes=` keyword uses in `tests/test_workflow.py`, `test_prd008_resume_fingerprints.py` and
  `test_prd033_metrics.py` build result/trace inputs, not states: unaffected.

**Verdict:** no major design assumption is invalidated, so M1.1 may proceed. The only change is the M1.6 hook point
above.

## 7. Implementation deviations from the design (recorded as they are made)

| # | Where | Deviation | Why |
|---|---|---|---|
| DEV-1 | M1.2, design §5.1 | **Phases of non-attempt calls are not opened by per-phase scopes.** A call's `phase` is the attempt scope's `attempt` when one is active. Otherwise it is mapped from the role the code already sets for that call (`role_metrics.model_role`: planner → `planning`, reviewer → `review`, spec_compliance/run_verifier → `requirement_verification`, ...). `phase.opened/closed` stay schema kinds that no producer emits in M1 | Eight call sites would each need an extra scope. The role ContextVar is set by those same sites for every call, so it is observed attribution, not a guess, and it adds no new hook |
| DEV-2 | M1.2, design §3.3 | **The goal is recorded in `unit.opened`** (content), not in `run.opened`. The run scope opens before any goal is known, and a run's units can have different goals (enforce subtask goals, milestone goals) | The record carries the goal that unit actually ran under |
| DEV-3 | M1.2, I-2 | **The `evidence` config section is excluded from every execution-identity fingerprint**: the RunRecord's `effective_config_fingerprint`, the checkpoint `config_fingerprint` and the resume `config` bucket (`checkpoint.OBSERVATIONAL_CONFIG_SECTIONS`). It stays in SEC-009 approval digests | MEASURED by T3: with it included, `full` vs `off` changed `effective_config_fingerprint`, so a capture-mode change between a run and its resume would have been config drift (a resume refusal). That is a behavioural effect, which I-2 forbids. Negative control: a log-level change still changes both fingerprints (test) |
| DEV-4 | M1.2 | The store opens **lazily**, at the first scope that knows the configuration: the unit scope in `run_generation_workflow`, or `ensure_store` at the start of enforce structured planning (which runs before any unit). `begin_mutating_run` has no configuration | No new parameter on the 5 CLI entry points |
| DEV-5 | M1.2, T3 | The equivalence suite **measures volatility** (two `off` runs; differing JSON paths are masked) instead of listing volatile keys. It also normalizes duration text (`in <n>s`) in every compared text, request bytes included | MEASURED: pytest's summary and Kriya's own "completed ... in 0.01s" messages vary between two identical runs, independently of the recorder. A flaky failure (1 in 8) was traced to this before the normalization; 10/10 passes after |
| DEV-6 | M1.4, D3 | **Raw content is captured inside the adapters**, as the new `ChatResponse.raw_content`, because both adapters already `.strip()` the content before `LLMClient` sees it (TRACED: `inference_runtime.py` OpenAI paths, `model_runtime.py` native paths). The separately returned reasoning text is kept in `ChatResponse.reasoning_text`. Both are additive and read by no decision; `to_raw()` and `content` are unchanged | D3 asks for content "verbatim before split_reasoning"; the strip happens even earlier, in the transport |
| OBS-1 | M1.4 (pre-existing, not M1) | **The native request body is not pinned exactly.** A mutant adding an unknown key to the native `/api/chat` body passes all 35 tests in `test_inf001_runtime_parity.py` + `test_provider_contract_native.py` (MEASURED). I-2 is still protected (T3 compares the bytes across recorder variants), but a recorder-independent wire drift would not be caught | Recorded for owner decision; not fixed in M1 (scope) |
| DEV-7 | M1.8b, D7 | **The typed codes are not in `Failure.diagnostics["reason_code"]`.** That field is already consumed by decisions: `retry_strategy._attempt_progress_vector` folds it into the ProgressVector (`diagnostics=(reason_code,)`), which drives no-progress classification, and `attempt.py`'s repeated-evidence check reads it (TRACED). Placing a D7 code there would let a production decision consume it, which D7 forbids. Instead `GenerationState.environment_failure_code = (code, exact message)` is set beside the two existing stop messages (`kriya/workflow/diagnosis_codes.py`); the recorder reports the code only while that message is still the current `environment_failure`. Also TRACED: `REGRESSION_UNATTRIBUTED` already had a typed `Failure.type` (`regression_unattributed`); only `NO_AUTHORIZED_REPAIR_TARGET` was text-only | Design §13 says "typed reason_code in Failure.diagnostics" |
| DEV-8 | M1.8b, §3.4 | **`retry.delta` is emitted at the retry's first Developer request**, not at `attempt.opened`: the delta compares that request's inputs, which do not exist yet when the attempt opens. An attempt that closes with no Developer request emits its delta at close, `information_gain: UNKNOWN`. Dimensions recorded: model, model profile digest, temperature, mode, targets, authority digest, retry-evidence fingerprint, triggering failure signature, per-section digests; the whole-request digest is recorded but is not a dimension (it always differs) | Design §5.6 "Emit retry.delta at attempt.opened" |
| DEV-9 | M1.8b | **`RecoveryDecision.retry_decision` is `compare=False`.** The PRD-031 tests compare decisions by equality; the policy's decision is still exactly `stop_loop`/`action`/`budgets_exhausted` | Additive field per design; equality kept |
