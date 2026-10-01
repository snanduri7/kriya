# Kriya Performance Telemetry

R1 Deliverable 5. Answers: *if a future production task takes 40–80 minutes, can we tell whether the time was spent in LLM inference, deterministic build/test/runtime work, retry/recovery amplification, or orchestration overhead?* This document is a field reference, not a performance target — no thresholds or SLAs are set here; not enough measured production runs exist yet to justify one.

## Architecture: extended, not duplicated

Per the task's own Step 1 audit requirement, this deliverable extends three existing mechanisms rather than building a parallel telemetry subsystem:

1. **`GenerationState.generation_metrics()`** (`kriya/workflow/state.py`) — already existed, already documented as "content-free operational telemetry safe to persist," already aggregated from existing fields (`generation_timings`, `run_events`, `validated_file_revisions`), and already flowed unconditionally into `TraceLogger.log_run(generation_metrics=...)` and the CLI/JSON-facing result dict. This is the one place the task's own "extend generation_metrics first, not ten SQL columns" comment (MA1.4, pre-existing) explicitly anticipated future additions like this one.
2. **`TraceLogger`/`traces.db`** (`kriya/core/trace.py`) — the `generation_metrics` column already existed (additive, nullable, JSON-serialized). Zero schema changes were made. `kriya traces` already surfaces this column; no new persistence, no new database.
3. **`LLMClient.last_call_metrics`** (`kriya/core/llm.py`) — new, but a pure side-channel. `complete()` already computed `elapsed_time`, `prompt_tokens`, `completion_tokens` internally (even already printing them via `click.secho`) before this task — they were computed and then discarded. The only change is capturing that already-computed data onto `self` instead of discarding it. `complete()`'s return type, signature, and every one of its ~80+ call sites across the codebase are completely unchanged.

No new database, no new trace format, no `telemetry.json` artifact — `generation_metrics` already is the machine-readable, per-run, persisted representation the task's Step 5 asks for.

## Control-flow equivalence

**Guarantee:** for identical deterministic inputs and mocked LLM/tool outputs, behavior before and after this task is identical except for emitted telemetry data. Enforced by:

- Every timing/token capture is a **side-channel read after an `await`/call already returned or raised** — never a value substituted into a decision, prompt, or return value. `last_call_metrics` is read, never fed back into `complete()`'s own next call.
- Every telemetry increment (`state.planner_calls += 1`, `state.validator_timings.append(...)`, etc.) is a plain, non-branching statement immediately after the real call it measures — no telemetry code path can raise and change control flow, and none of it is consulted by any `if`/`elif` anywhere in the retry, recovery, obligation, or write-authorization code.
- `candidate_independent_diagnostic_invocations`/`baseline_replay_count` are computed by comparing `len(store._records)` before/after `evaluate_candidate_independent_failure()` — a **read** of the store's own size, never a write, and never consulted by that function or its caller.

Proven by `tests/test_performance_telemetry.py::test_pt01_telemetry_does_not_change_outcome_or_authorized_writes` (same outcome/files/quality_gates_passed as the pre-existing `test_predetermined_plan_and_design_use_the_real_architect_files_list` shape) and, more broadly, by the full existing regression suite (799 tests across `test_workflow.py` + `test_deterministic_failure_diagnostic.py`) passing unchanged after this task's changes — see Test Execution below for the one genuinely unrelated pre-existing failure found along the way.

## Timing semantics: inclusive, not exclusive

**`total_wall_seconds`, `llm.wall_seconds`, and `validators.wall_seconds` overlap. They are NOT mutually exclusive and do not sum to `total_wall_seconds`.** A validator call happens *during* wall-clock time that also counts toward the run's total; an LLM call and a validator call from a different attempt both count fully toward their own totals even though real time only elapsed once. This is a deliberate choice (Step 6): raw event durations plus clearly-labeled aggregate totals, never a fabricated exclusive breakdown that would require refactoring the retry loop's own timing boundaries to compute correctly. The CLI summary prints an explicit reminder of this every time it prints performance data.

## Field reference

### Run level (`generation_metrics()`'s top-level keys)

| Field | Type | Units | Unavailable semantics | Source |
|---|---|---|---|---|
| `total_wall_seconds` | float or `null` | seconds | `null` when the caller didn't supply a measurement (`generation_metrics()` never samples its own clock — see below) | `time.monotonic() - state.generation_started_monotonic`, computed by `run_generation_workflow()` |
| `terminal_status` | string | — | never null | `"success"` / `"environment_failure"` / `"failed"`, derived from existing `state.final_workflow_quality_passed()`/`state.environment_failure` |
| `calls` | int | — | 0 if none | count of `generation_timings` (Developer attempts) — pre-existing field, unchanged |
| `successful_calls` | int | — | 0 if none | pre-existing field, unchanged |
| `duration_seconds` | float | seconds | 0.0 if none | pre-existing field (Developer LLM wall time only), unchanged in meaning |
| `files_requested`, `operation_fallbacks`, `validation_invalidations`, `validated_files` | int | — | — | pre-existing fields, unchanged |

`generation_metrics(total_wall_seconds=...)` is deliberately a **pure read** of already-recorded fields plus one caller-supplied value — never a fresh "now" sample of its own, since it is called more than once in some paths (a mid-run checkpoint write, then the final trace write) and must return consistent values for whatever has actually happened so far each time.

### LLM call level (`generation_metrics()["llm"]`)

| Field | Type | Unavailable semantics | Notes |
|---|---|---|---|
| `calls` | int | — | Developer + Planner + Architect + Reviewer calls, summed |
| `wall_seconds` | float | — | sum of all four stages' own wall time (see overlap note above) |
| `developer_calls` / `developer_wall_seconds` | int / float | — | from `generation_timings` |
| `developer_prompt_tokens` / `developer_completion_tokens` | int | 0 when no attempt had token data (see `developer_tokens_available_for`) | **Known limitation**: `DeveloperAgent.run_generation()` may issue more than one underlying `LLMClient.complete()` call per attempt (the iterative per-file fallback path, one completion per filepath, when the model doesn't return a batch-JSON file-object array). `last_call_metrics` reflects only the **last** underlying call in that case, not a sum across all of them - the batch-JSON single-shot path (the common case) is unaffected. Not silently presented as exact; see `developer_tokens_available_for`. |
| `developer_tokens_available_for` | int | — | how many of `developer_calls` actually had real token data (vs. `None` because the underlying LLM call metadata wasn't captured, e.g. a mocked/predetermined path) - present specifically so a low token sum is never misread as "no tokens used" |
| `planner_calls`, `architect_calls`, `reviewer_calls` and their `*_wall_seconds` | int / float | 0 if the stage was skipped (predetermined plan/design, checkpoint resume) | captured directly at each stage's own call site in `run_generation_workflow()` |

Per-call fields the task asked for that are **not captured, and why**:

| Field | Status | Reason |
|---|---|---|
| `stage/role`, `model`, `attempt_number` | Partially available | `model` is captured per Developer attempt (`generation_timings[i]["model"]`); a per-call stage/attempt-number breakdown for Planner/Architect/Reviewer is not recorded per-call, only aggregated (each stage runs once or a small, non-retried number of times per run, so an aggregate count/sum was judged sufficient without adding a new per-call list). |
| Prompt/completion tokens for Planner/Architect/Reviewer | Not captured | Only Developer-attempt token counts are threaded through in this pass (via the `generation_timings` list that already existed for exactly this purpose); Planner/Architect/Reviewer capture only call count and wall time, not tokens. `llm.last_call_metrics` is technically readable at those call sites too and this is the natural next increment - deliberately deferred rather than expanded further in a single pass. |
| Time-to-first-token | Unavailable, always `null` if ever exposed | `LLMClient` uses the standard OpenAI-compatible non-streaming `usage` field; time-to-first-token requires a streaming response with per-chunk timestamps, which `complete()`'s current implementation does not measure even on its streaming path. Not fabricated. |
| Generated tokens/sec | Not derived | Would require time-to-first-token (above) to separate prompt-eval time from generation time; `elapsed_time` is whole-call wall time, not generation-only time, so a naive `completion_tokens / elapsed_time` would conflate network/queue time with generation - not presented as tokens/sec anywhere. |
| Retry mode per LLM call | Available indirectly | `state.last_attempt_mode` exists on `GenerationState` already (pre-existing) and is queryable alongside `generation_timings` by index for the Developer's own calls; not duplicated into each timing dict in this pass. |
| Termination/error status per call | Available indirectly | `generation_timings[i]["succeeded"]` (pre-existing) covers this for Developer attempts; `last_call_metrics` is `None` after any failed `complete()` call (see Control-flow equivalence above), which is itself the error signal for a caller reading it directly. |

### Validator/build/test level (`generation_metrics()["validators"]`)

| Field | Type | Notes |
|---|---|---|
| `invocations` | int | count of `state.validator_timings` entries |
| `wall_seconds` | float | sum of their `duration_seconds` |
| `by_kind` | dict | count per `kind` string (`"compile"`, `"test"`, `"run_verification"`, `"managed_service_verification"`) |

**Coverage boundary, stated explicitly (Step 3/"do not fake metrics that cannot be measured reliably")**: `validator_timings` covers exactly three call sites in `kriya/workflow/attempt.py`:
1. The primary full-set/targeted compile gate (`validator.run_compile_check(compile_known_files)`, kind=`"compile"`).
2. `_execute_runtime_verification_directly()`'s `run_app_sequence()` call (kind = the deterministic command classification, `"test"` or `"run_verification"`).
3. `_execute_managed_service_verification()`'s `run_managed_service_verification()` call (kind=`"managed_service_verification"`).

**Not separately instrumented, and why**: the targeted-test-selection-with-fallback block (`kriya/workflow/attempt.py`, the `target_test`/zero-collection-retry/full-suite-fallback logic around the main test gate) has multiple exit branches (`raise QualityGateFailure` at several points, a retry-with-fallback path) that could not be safely wrapped with a single `try/finally` without materially higher risk of introducing a bug into this codebase's most delicate, heavily-tested control-plane code — a real engineering trade-off, not an oversight. Two secondary `run_app_sequence()` call sites elsewhere in `attempt.py` are likewise not wrapped, for the same reason. This is a genuine, acknowledged gap in **test-gate wall-time attribution specifically** (compile-gate and runtime-verification/managed-service timing ARE captured) - a future increment could close it with more careful, incremental wrapping of each individual branch, not a single outer `try/finally`.

**Runtime-verification sub-phase timing (Step 5's own prepare/launch/readiness/probe/shutdown breakdown): not captured.** `run_managed_service_verification()` and `run_app_sequence()` are opaque single calls from `attempt.py`'s own vantage point - their internal phases are not separable from outside without modifying `kriya/tools/service_runtime.py`/`kriya/tools/validate.py` themselves, which this instrumentation-only task deliberately does not do (Step 5's own "do not refactor runtime architecture merely to create prettier telemetry"). Only the coarse, already-existing phase boundary (one call = one measurement) is recorded.

### Recovery level

**Not implemented as a distinct field set in this pass.** Recovery/coordinated-repair (`RepairContract`, `revise_plan_for_grounded_scope_owner`) already has extensive forensic logging via `RunEvent`/`gate_outcomes`/`plan_recovery_events` in the approved-plan document, which the `run_events`/`generation_metrics()["operation_fallbacks"]` fields already partially surface. A dedicated `recovery[]` telemetry list (invocation count, owning stage, duration, whether a candidate passed) was scoped out of this pass to keep the change surface bounded to what could be thoroughly verified - the retry-amplification counters (`retry.*`, below) cover the specific P7-class question (baseline replays, diagnostic invocations, full-set/targeted attempt counts) this task's own Step 3 named as the priority.

### Retry-amplification level (`generation_metrics()["retry"]`)

| Field | Type | Source |
|---|---|---|
| `full_set_attempts` | int | `state.budgets.retry_count` (pre-existing budget counter, read not duplicated). Labeled **"Developer full-set retries"** on the CLI (was mislabeled "Planner repair rounds" prior to the 2026-09-08 correction below - it is the Developer's own full-file-set retry counter, unrelated to structured-plan repair). |
| `targeted_attempts` | int | `state.budgets.targeted_retry_count` (pre-existing) |
| `unrecoverable_scope_denials` | int | `state.unrecoverable_scope_denial_count` (pre-existing) |
| `candidate_independent_diagnostic_invocations` | int | new counter, incremented at the exact `evaluate_candidate_independent_failure()` call site in `handle_attempt_failure()` |
| `baseline_replay_count` | int | new counter; **exact, not a heuristic** - every code path inside `evaluate_candidate_independent_failure()` that calls `store.record()` provably first called `replay_deterministic_verification_against_baseline()` (verified by direct code reading, not assumed), and every path that skips replay returns before ever calling `store.record()` - so comparing the store's own record count before/after is an exact count |

**`plan_repair_attempts` (2026-09-08 correction - now captured, NOT part of `generation_metrics()`):** the real structured-plan Planner-repair-round count from `WorkflowController._run_structured_enforce()`'s own `repair_attempts` local (the "PLAN VALIDATION" loop, `kriya/workflow/workflow_controller.py`) - a controller-level concept that exists before any per-subtask `GenerationState` is created, so it deliberately lives as a **sibling top-level key on the run result dict** (`result.legacy_result["plan_repair_attempts"]` / `res["plan_repair_attempts"]`), not nested inside `generation_metrics()`. The controller's `repair_attempts` local is the single source of truth for the whole run (fixed once the PLAN VALIDATION loop exits, unchanged by every later `save_approved_plan()` call for that run); this task added exactly one line copying that value into the result dict at the point the aggregated result is built (`kriya/workflow/workflow_controller.py`, right where `aggregated: Dict[str, Any] = {...}` is constructed), reusing the exact key name the pre-existing `_UnsafeStructuredPlan` except-handler already used for the repair-exhausted failure case. No new counter, no duplication, no control-plane coupling - a pure read of an existing local.

Only present (never `0` as a default) when `workflow_controller.enabled=True` (`enforce` migration mode) actually ran the structured-plan path - **not the packaged default**. For the packaged-default legacy/single-run path (no structured-plan repair loop exists at all), `plan_repair_attempts` is absent from the result dict; the CLI (below) reports this as `unavailable`, never as `0` - a run where the concept doesn't apply is not the same as a run with zero repair rounds. Verified by `tests/test_workflow_controller_enforce.py::test_plan_repair_attempts_is_zero_when_the_first_plan_validates` / `_is_one_after_a_single_repair_round` / `_is_two_after_the_maximum_allowed_repair_rounds` / `_on_repair_exhaustion_matches_the_except_handler_value` - each cross-checks the reported value against an independent count of `planner.run()` calls (one initial call + one call per repair round), not merely trusting the copied field.

`repeated_failure_count_by_signature`, `same_failure_after_material_candidate_change_count`: **not added** as separate fields in this pass - both are derivable from `state.budgets.last_failure_signature`'s own history but are not currently tracked as a running tally anywhere; adding that tally was judged to risk duplicating control-plane state the task's own Step 3 explicitly warned against ("do NOT add new behavioral tracking solely for metrics if it risks duplicating control-plane state").

## Privacy and security

`generation_metrics()` contains: durations (floats), counts (ints), boolean success flags, model name strings, a bounded validator `kind` string, and token counts. It contains **no prompt text, no system/user prompt content, no generated source-code file bodies, no compiler/test stdout/stderr blobs** (those already live in `gate_outcomes`/logs under this codebase's own pre-existing, separately-governed retention policy - untouched by this task), and no credentials/API keys/environment variables. Verified by `tests/test_performance_telemetry.py::test_pt08_generation_metrics_serializes_with_no_prompt_or_source_content`, which serializes a populated `generation_metrics()` dict and asserts none of a list of prompt/source/secret-shaped markers appear in the output.

## CLI reporting

`kriya generate`'s existing terminal summary (`kriya/cli.py`) gains one new block, printed after the existing Quality Gates/files/failure-category output. `gm` (`generation_metrics`) and `plan_repair_attempts` are independent, sibling keys on the result dict (see previous section) - the block is shown whenever either is present, and each sub-line is only printed for the source that actually produced it:

Packaged-default (legacy/single-run) path - `generation_metrics` present, no structured-plan repair concept applies:

```
Performance
-----------
Total wall:             16m 38s
LLM calls:              7
LLM wall:               9m 12s
Validator wall:         4m 48s
Developer attempts:     3
Developer full-set retries: 1
Baseline replays:       0
Planner repair rounds:  unavailable (structured-plan repair not active for this run)
(LLM/validator wall overlap with total wall - they are not additive)
```

`workflow_controller.enabled=True` (`enforce` mode, not the packaged default) - a real structured-plan repair count is available, but that path's own aggregated result does not populate `generation_metrics` (see "Not implemented as a distinct field set" under Recovery level, and the "Known limitations" item below):

```
Performance
-----------
Planner repair rounds:  1
```

High-value totals only, matching Step 5's own instruction not to flood normal output - detailed per-call data belongs in `traces.db`'s `generation_metrics` column, queryable via `kriya traces`, not the terminal. JSON output mode (`--json-output`) already includes the full result dict unchanged (`generation_metrics` and, for enforce mode, `plan_repair_attempts` were already part of the result dict before/via this task); no separate machine-readable file is written.

## Known limitations (consolidated)

1. Developer-attempt token counts undercount for the iterative per-file generation fallback (only the last file's usage is captured).
2. Test-gate (as opposed to compile-gate) wall time is not separately instrumented - too many exit branches to wrap safely in this pass.
3. Runtime-verification/managed-service sub-phase timing (prepare/launch/readiness/probe/shutdown) is not separable without modifying `service_runtime.py`/`validate.py` themselves - only the coarse whole-call duration is recorded.
4. Planner/Architect/Reviewer token counts are not captured, only call count and wall time.
5. Time-to-first-token and tokens/sec are never available through the current OpenAI-compatible client path - always absent, never estimated.
6. `plan_repair_attempts` (2026-09-08: now captured, see Retry-amplification level above) is only meaningful for `workflow_controller.enabled=True` (`enforce` mode, not the packaged default) - the packaged-default legacy path has no structured-plan repair concept at all, and enforce mode's own aggregated result does not populate `generation_metrics`, so a single run never shows both a full `generation_metrics` breakdown AND a real `plan_repair_attempts` value together today.
7. No `recovery[]` telemetry list yet - existing `run_events`/`plan_recovery_events` partially cover this today.

None of these are silently masked - each is a `null`/absent value or an explicitly documented undercount, never a fabricated number.

## Test execution

```
.venv/bin/pytest tests/test_performance_telemetry.py -v      # new PT-01/02/03/04/05/08 tests, 9 passed
.venv/bin/pytest tests/test_workflow.py -q                   # 788 passed, 1 pre-existing failure unrelated to this task (see below)
.venv/bin/pytest tests/test_deterministic_failure_diagnostic.py -q   # 19 passed, 1 deselected (live_model)
.venv/bin/pytest tests/test_service_runtime.py tests/test_polymorphic_validation.py tests/test_llm_extra.py tests/test_llm_egress_policy_integration.py -q   # 145 passed
.venv/bin/pytest tests/test_traces_command.py -q              # 7 passed
.venv/bin/pytest tests/test_workflow_controller_enforce.py tests/test_workflow_controller.py -k "plan_repair_attempts" -q   # 2026-09-08 correction: 4 new tests, 4 passed
.venv/bin/pytest tests/test_workflow_controller_enforce.py tests/test_workflow_controller.py -q   # 2026-09-08 correction: full-file regression, 290 passed
.venv/bin/pytest tests/test_run_events.py -q                 # 2026-09-08 correction: unchanged, 5 passed
```

**Full suite (2026-09-08, after the `plan_repair_attempts` correction)**: `.venv/bin/pytest -q` -> 3006 passed, 9 failed, 5 deselected. All 9 failures reproduce identically on the pre-telemetry baseline commit (`ba80965`, confirmed via `git stash`): `tests/test_workflow.py::test_django_test_command_bypasses_application_entrypoint_infrastructure_classification` (1) plus `tests/test_validate_package_audit.py`/`tests/test_validate_policy_audit.py` (8, all sharing `FileNotFoundError: 'pytest'` from a bare `subprocess.run(["pytest", ...])` call not resolving on this shell's PATH - an environment artifact, not a code defect). None caused by this task or its predecessor Deliverable 5 pass. Per Part 5 of this correction task: **`FULL PYTEST: DELTA-CLEAN / BASELINE-NOT-GREEN`** - not reported as a plain "PASS", since the baseline itself is not green; not fixed here (separately tracked baseline debt, out of this correction's own scope).

PT-06 (build/test phase separation) and PT-07 (runtime-verification phase timing) from the task's own required test list are not implemented as separate tests, per that same task's own explicit allowance ("If not [separable without invasive refactoring], record only the coarser existing phase and document the limitation") - the limitation is documented above instead of forcing a test around an unmeasurable boundary.

## Overhead

Not separately benchmarked with a dedicated timing harness in this pass. Structural argument instead (per Step 9's own fallback allowance when a percentage measurement would be noisy/flaky): every telemetry operation added is either (a) one `time.monotonic()` call (a cheap C-level syscall wrapper, already used pervasively elsewhere in this codebase's own retry/timing code, e.g. `_run_developer_generation`'s pre-existing Developer-call timer) or (b) an in-memory list append / dict-literal construction / integer increment - no blocking I/O, no additional network calls, no additional subprocess spawns anywhere in the telemetry path itself. `generation_metrics()` remains a pure in-memory aggregation over already-in-memory lists, called at the same 2-3 call sites it was already called at before this task (mid-run checkpoint, final trace write, final result dict) - no new call sites added to that function's own invocation count.
