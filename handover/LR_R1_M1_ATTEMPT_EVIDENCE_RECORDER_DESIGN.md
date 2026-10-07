# LR-R1-M1: Attempt Evidence Recorder (design)

**Status:** DESIGN APPROVED WITH CHANGES (owner review, 2026-10-04). The decisions and required additions are recorded
in §0.1 and applied throughout. **Implementation while the CAGC matrix runs: NOT AUTHORIZED.** Implementation starts
only under a separate authorization after 80/80 completes and its evidence is frozen. The read-only M1.0 investigation
notes are in `handover/LR_R1_M1_0_INVESTIGATION_NOTES.md`.
**Parent spec:** KRIYA-LIVE-RELIABILITY-R1 v0.1, §5-7 and §18.
**Code basis:** the common base `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03` (origin/main, Arm A). It was read from a
`git archive` export. Nothing in the running experiment, `~/.kriya`, Ollama, the arms or PROTOCOL_v2 was touched. The
checked-out branch `feature/cagc-r1` does not contain this base, so every path and line below refers to 61a867f.
**Claim labels:** MEASURED (observed by command), TRACED (read in the code that produces it), INFERRED (consistent
with the evidence, not proven), UNKNOWN.

---

## 0. Summary

- **Infrastructure exists.** Kriya already records a great deal (§1). There is a typed run-event stream (83 producer
  sites), a decision ledger, RunRecords, `traces.db` rows, normalized `CompletionResult`s, PRD-026 progress vectors,
  and fallback/transition events.
- **What it lacks.** The M1 questions need the exact prompt, the raw response, the authoritative source shown, the
  per-attempt candidate diff and per-gate output. None of these is persisted per attempt. Everything that is
  persisted is written once, at the end of each `run_generation_workflow` invocation, as a JSON column. So it is lost
  on a crash, and it cannot be reconstructed per attempt.
- **Design.** M1 adds one append-only, hash-chained, per-run evidence store under the state directory. It has two
  layers:
  1. **Mirror (no new semantics).** Every existing `RunEvent`, `Decision` and failure `EvidenceRecord` is mirrored
     into the store the moment it is recorded, tagged with run/unit/attempt identity.
  2. **Content records (only what nothing records today).** Model request/response, prompt section manifest,
     authority snapshot, Developer parse outcome, candidate diff, gate output, and scope open/close.
- **No decision reads it.** The recorder authorizes nothing and is structurally write-only from production code.
  When it fails, it degrades and says so. It never changes a run's outcome, prompt or timing-dependent behaviour.
- **The 80-run baseline cannot be served by M1.** M1 records forward runs only. The R1 Phase-2 census of the 80 runs
  needs a separate legacy importer, and several M1 questions are permanently unanswerable for those runs (§14, R2).

### 0.1 Owner decisions and required additions (2026-10-04)

| Item | Decision | Applied in |
|---|---|---|
| D3 raw transport content | **APPROVED.** Transport content is recorded verbatim, before `split_reasoning`. Inline `<think>` content that is physically part of transport content stays part of the raw response. A separately returned reasoning field is digest + length only, unless explicit `full_with_reasoning` | §3.3, §5.3, §7 |
| D4 default capture | **APPROVED = `full`.** `digest_only` is an operator-controlled alternative; `full_with_reasoning` is explicit opt-in only | §7 |
| D5 production fail-closed at store open | **DEFERRED.** M1 is observational. If the store cannot open, record or log `RECORDER_UNAVAILABLE` where possible and let the run continue; the run and its evidence report attempt evidence as unavailable/incomplete. **No `ATTEMPT_EVIDENCE_UNAVAILABLE` execution blocker in M1.** An SEC-009-controlled audit-required/fail-closed mode is a separate later decision, after deterministic certification, mutation, performance testing and bounded live validation | §7, §9.4, §12 T14 |
| D7 typed reason codes | **APPROVED** in the separate M1.8b commit for `NO_AUTHORIZED_REPAIR_TARGET` and `REGRESSION_UNATTRIBUTED`. Existing messages and decisions are unchanged; no production decision consumes the new codes in M1 | §13 M1.8b |
| Hierarchy Run → Unit → Invocation → Phase → Attempt → Call → Wire | APPROVED | §3.1 |
| Runtime evidence vocabulary separate from human investigation vocabulary | APPROVED | §3.3, §14 D2 |
| Append-only hash-chained per-run store | APPROVED | §3.5, §6 |
| `full` local-only capture + retention | APPROVED | §7, §10 |
| M1.11 legacy importer | APPROVED | §8, §13 |
| Decomposition M1.0-M1.11 | APPROVED | §13 |
| Addition 1: prove no exact-list-type dependency on `gate_outcomes` before a list subclass, else choose another centralized mechanism | **Done (read-only, M1.0 notes §1).** No exact-type dependency, but a production **reassignment** (`attempt.py:6391`, resume) and test reassignments would silently replace a subclass with a plain list. **List subclass REJECTED**; replaced by an explicit recording method + AST tripwire | §5.2 |
| Addition 2: precise durability statement (process termination vs machine/power loss) | Added | §6.6 |
| Addition 3: persistence layout is internal; consumers use the reader API | Added as invariant I-1 | §6.7, §8 |
| Addition 4: strongest acceptance condition | Added as invariant I-2 and test T3 | §6.7, §12 |
| Addition 5: the legacy importer never infers; missing evidence is `NOT_RECORDED` | Added as invariant I-3 | §6.7, §8, §13 |

Knock-on change from D5, made for consistency: the earlier text had the production profile **refuse `capture: off`
at config load**. That is the same audit-required blocking behaviour D5 defers, so it is deferred with it.

What stays is the SEC-009 classification: `evidence.attempt_recorder.*` is SECURITY_AUTHORITY, so a **repository**
config can still never disable or reduce capture. Only the operator can.

---

## 1. Current evidence/telemetry architecture (TRACED at 61a867f)

| Layer | Module / class | What it holds | When written | Where | Content? |
|---|---|---|---|---|---|
| Run trace row | `kriya/core/trace.py::TraceLogger.log_run` | `runs` row per `run_generation_workflow` invocation: goal, attempts, status, `gate_outcomes`, `run_events`, `evidence_records`, `generation_metrics`, `failure_report`, `prompt_rendered` (Planner prompt only, `workflow.py:4474/5472/5848`), model hops | Once, at each invocation's terminal points (`INSERT OR REPLACE` on `run_id`) | `<state>/traces.db` (`kriya/core/state_paths.py::trace_db_path`) | Yes: gate output, `failed_content` (full failed source), attempted edits |
| Outcome rows | `kriya/workflow/run_trace.py::write_outcome_trace`, `record_run_exceptions` | `<run_id>.enforce`, `<group>.milestone-plan`, `<trace_id>.exception` rows | At those terminals | same | Events only |
| Run events | `kriya/workflow/run_events.py::RunEvent` (frozen; kind, attempt, source, authority, message, failure_type, operation, details, created_at), `GenerationState.record_event` (`state.py:737`) | ~80 kinds, e.g. `attempt.started`, `developer.prompt_composition`, `generation.completed/failed`, `model.transition`, `model.fallback_selection`, `model.fallback_incompatible`, `retry.progress_vector`, `retry.no_progress_terminal`, `retry.strategy_transition`, `context.request_fit`, `context.known_target_package`, `failure.recorded`, `candidate_gates.passed`, `investigation.*`, `capability.guidance` | In memory; persisted only inside the trace row | `traces.db.runs.run_events` | Messages may contain paths/diagnostics |
| Failure ledger | `run_events.py::FailureLedger` | primary/secondary failure | In memory | — | — |
| Local evidence | `kriya/workflow/evidence.py::EvidenceRecord` (`sensitivity="local_only"`) | failure payload (type, message, raw_output, likely_files, failed-content *revisions*, attempted_edits) | In memory, then trace row | `traces.db.runs.evidence_records` | Yes |
| Decision ledger | `kriya/control/decisions.py::DecisionLedger` (+ `control/telemetry.py`, `workflow/subtask_telemetry.py`) | Engineering decisions (plan_created, subtask_attempt, structured_plan_validation, context_package ...), stamped `_workspace_id` and `_run_record` | `append_to_file` **re-reads and rewrites the whole file** per decision through `write_control_file` | `<ws>/.kriya/control/decisions.jsonl` | Content-free by design |
| RunRecord | `kriya/control/run_record.py` (schema 3) | Lifecycle, commit cycles, candidate hash, config fingerprint, `model_runtime_fingerprint_ids`, work-unit states | Every transition, atomically | `<ws>/.kriya/control/runs/<run_id>.json` | Content-free by design |
| Run scope | `kriya/control/run_coordinator.py::begin_mutating_run`, `RunContext`, `current_run_context()` (ContextVar) | The run id; **nested workflow/controller boundaries reuse the same RunContext** (`run_coordinator.py:387-391`) | — | — | — |
| Model call | `kriya/core/llm.py::LLMClient.complete_result` / `complete_with_tools_result` → `CompletionResult` (`core/completion.py:53`) | Model, runtime fingerprint digest + exact, inference-settings digest, protocol (incl. `provider_contract` record, `response_format_dropped`, `empty_content_floor_retry`), finish, tokens, budget, provider metadata, prefix reuse | `llm.last_completion` / `last_call_metrics` (**last call only**, overwritten) | Fragments copied into events | Content and reasoning text are **not** retained (PRD-015: reasoning presence only) |
| Role metrics | `kriya/core/role_metrics.py` (`_ROLE` ContextVar, `model_role()`) | Per (role, model, runtime, settings) counts | Per call → `model.role_metrics` event | trace row | No |
| Retry progress | `kriya/workflow/retry_progress.py::ProgressVector` (failure signature, workspace hash, implicated/missing files, evidence fingerprint, context revisions, action, protocol, request profile, plan revision, repair-contract revision, diagnostics) + `changed_dimensions()` | Per failed attempt, as a `retry.progress_vector` event (digest + changed dimensions) | trace row | Digests only |
| Recovery | `recovery_coordinator.py::RecoveryCoordinator` → `RecoveryDecision(stop_loop, action, budgets_exhausted)`; `retry_policy.py::RetryDecision(action, reason, reserved_fallback)` | **Not persisted as a record** (only logged and partly reflected in events) | — | — |
| Fallback | `attempt.py::_select_developer_fallback`, `_record_fallback_selection`, `_raise_fallback_incompatible`; `model_transition.py` | `model.fallback_selection`, `model.fallback_incompatible`, `model.transition` events | trace row | No |
| Developer parse | `kriya/agents/response_protocol.py::DeveloperResponse`; parse sites `agent.py:2005-2009`, `1025-1035` | kind / protocol / reason_code / detail | **Not persisted** (raw completion only at `logger.debug`, `agent.py:~1991`) | — | — |
| Gates | `kriya/tools/validate.py::_verification_gate` (one decorator for every validator gate); `Failure.to_gate_outcome()` (85 `gate_outcomes.append` sites) | Failing gate outcome incl. full output and failed content; passing gates only as `validator_timings` + `candidate_gates.passed` / `terminal_regression.passed` | trace row | Yes |
| Candidate | `GenerationState.candidate_digests`, `all_original_raw`, `verification_tree_binding`; `verification_binding.bind_candidate` | Raw digests of staged candidate and base bytes | In memory; commit evidence at commit | `.kriya/control` commit evidence | Digests |
| Logs | `kriya/core/logging_setup.py` | `<log_dir>/runs/<run_id>/kriya.log` | Streaming | log dir | Operational text |
| Metrics | `kriya/metrics/` (PRD-033) | Content-free projections of traces/RunRecords | Derived | — | Never content (canary test) |

The parent spec asked for these confirmations:
- **One run id spans the whole run.** TRACED: `cli.py:2671, 3131, 4076, 4537, 751` open `begin_mutating_run` around
  the whole workflow, including the Planner. Enforce subtasks are nested `run_generation_workflow(work_unit=...)` calls
  under the same RunContext.
- **All inference goes through LLMClient.** MEASURED: there are 42 `complete*` call sites outside `llm.py`. The 3
  `chat.completions.create` sites and the 3 native `http.post/stream` sites are all inside the runtime adapters.
  TRACED: every inference path reaches `LLMClient._request_once` or the tools call at `llm.py:1236`.
- **Raw responses were not logged during the experiment.** MEASURED: the experiment config snapshots have no
  `logging:` section. TRACED: the default is `INFO` (`default_config.yaml:51`). INFERRED: the 80 runs' logs therefore
  contain no raw Developer completions.

---

## 2. Gaps against M1

Each of the nine M1 questions is assessed against what is recorded today.

| M1 question | Recorded today | Gap |
|---|---|---|
| 1. What exactly was the model asked? | Planner prompt text only (`prompt_rendered`); Developer per-section token *estimates* (`developer.prompt_composition`); `context.request_fit` reductions | **No final prompt for any Developer, Architect, Reviewer, verifier or investigation call.** No wire body. No per-section content or digest. No record of which section was dropped for which call. |
| 2. What authoritative source did it receive? | `context.known_target_package` (tier/member/revision summaries); `known_target_context_items` in memory | No per-request authority snapshot: EditCapability per target, exact span, minimum editable unit, write scope, D1 authority. Not bound to the request it governed. |
| 3. What did it return? | Status/finish/tokens of the **last** call (`last_call_metrics`) | **Raw content never persisted.** The iterative per-file path keeps only the final file's call (documented undercount). Wire-level resends inside one call (`response_format_dropped`, `empty_content_floor_retry`) are flags only. |
| 4. What candidate change resulted? | Final `workspace.diff` (outside Kriya); failed content inside failing gate outcomes; candidate digests in memory | No per-attempt before/after digest + diff bound to the attempt. Parse outcome (kind, reason code) not persisted. Refused-before-write candidates are only visible as failures. |
| 5. What deterministic check failed? | Failing gate outcome (type, output, attribution) | Passing gates are not individually recorded. Gate command, exit code and duration are not bound per attempt. Global obligation state per attempt is not recorded. |
| 6. Why did Kriya retry? | `retry.progress_vector`, `attempt.started{mode}`, `failure.recorded` | **The `RecoveryDecision`/`RetryDecision` (action, reason, reserved fallback, budgets) is never persisted.** Diagnosis reason codes for `NO_AUTHORIZED_REPAIR_TARGET` and `REGRESSION_UNATTRIBUTED` exist only as message-text prefixes (`retry_strategy.py:1301`, `workflow.py:4998`, matched as text at `workflow.py:5680`). |
| 7. What changed in the next retry? | Outcome-side `changed_dimensions` of the progress vector | No **input-side** delta (what the next request had that the previous did not: model, sections, authority, targets, evidence). `retry_information_gain` does not exist. |
| 8. Why was fallback selected or refused? | `model.fallback_selection` / `model.fallback_incompatible` / `model.transition` | Adequate as events. Missing: identity binding to the attempt and the call, and crash-safe persistence. |
| 9. Why did the run finally succeed or fail? | Trace row status/failure_category, RunRecord terminal state, `.enforce` row | Adequate, but spread across three stores with no single per-run index; lost if the process dies before the row is written. |

Cross-cutting gaps:
- **Persistence is end-of-invocation only.** A `kill -9`, OOM or power loss mid-run leaves no attempt evidence
  (TRACED: `log_run` call sites).
- **Not append-only.** `INSERT OR REPLACE` per invocation. `DecisionLedger` is a whole-file rewrite (O(n²) per run).
  Neither is tamper-evident.
- **No call/attempt identity in many records.** `RunEvent.attempt` is the invocation-local attempt number. There is no
  subtask id on most events, and no call id at all.

---

## 3. Structured schema (`kriya.attempt_evidence/1`)

### 3.1 Identity hierarchy

The spec's Run → Subtask → Attempt is extended with two levels the code actually has. The reasons are in §14, D1.

```
Run          run_id                          RunContext.run_id (one per begin_mutating_run)
 └─ Unit     unit_id                         WorkUnitInvocation.work_unit_id (enforce subtask id, milestone id,
                                             "direct" for a direct goal); unit_kind from PlanSourceKind
     └─ Invocation  invocation_seq           1..n per (run, unit): one run_generation_workflow call. A unit can be
                                             invoked more than once (plan repair, resume, re-execution).
         └─ Phase   phase                    planning | architect | localization | attempt | review | requirement_verification |
                                             milestone_planning   (non-attempt phases carry no attempt number)
             └─ Attempt attempt_number       GenerationState.attempt_number (only inside phase=attempt)
                 └─ Call  call_seq           run-global monotonic model-call counter
                     └─ Wire  wire_seq       1..k physical provider requests inside one logical call
```

`attempt_id = f"{run_id}:{unit_id}:{invocation_seq}:{attempt_number}"`. `call_id = f"{run_id}:c{call_seq}"`. Every
record carries every applicable level explicitly; none is inferred by position.

### 3.2 Record envelope (every line of `records.jsonl`)

```json
{
  "schema": "kriya.attempt_evidence/1",
  "seq": 412,
  "prev": "sha256:<digest of record 411's canonical bytes>",
  "kind": "model.response",
  "run_id": "r-...", "unit_id": "subtask-3", "unit_kind": "structured", "invocation_seq": 1,
  "phase": "attempt", "attempt_number": 2, "call_seq": 17, "wire_seq": 1,
  "role": "developer",
  "t_wall": "2026-10-05T01:02:03.456Z", "t_mono_ms": 812345,
  "provenance": "OBSERVED",
  "payload": { ... kind-specific ... },
  "blobs": {"content": "sha256:..."}
}
```

- Canonical bytes are `json.dumps(record_without_digest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
  encoded as UTF-8.
- `prev` chains the records. The record's own digest is not stored in the line: it is recomputed by the reader, and
  the final head is sealed in `seal.json`.
- `provenance` is one of:
  - `OBSERVED`: Kriya saw the value at a boundary.
  - `DERIVED`: deterministically computed from observed values by named code.
  - `MODEL_CLAIMED`: text the model asserted, e.g. FIX ANALYSIS.
  - `MIRRORED`: a copy of an existing RunEvent or Decision.
  - `NOT_RECORDED`: an explicit gap with a reason.
- Timestamps are informational. Order is `seq`, never time (ENGINEERING_RULES §12).

### 3.3 Record kinds and payloads

Fields marked † are content and go to blobs (§7). Everything else is content-free.

| Kind | Emitted at | Payload |
|---|---|---|
| `run.opened` | run scope open | kriya build identity (`kriya/build_info.py`), release tree, config fingerprint (`compute_config_fingerprint`), workspace identity, base revision/tree, run kind (generate/fix/milestones/enforce/proposal/tool), capture mode, redaction policy version, goal digest + goal† |
| `unit.opened` | `run_generation_workflow` entry | unit id/kind, plan id + plan hash, subtask description†, planned files, dependencies, requirement ids, authorized write scope (validated plan paths), `invocation_seq`, resume decision ref |
| `phase.opened` / `phase.closed` | non-attempt phases | phase, outcome code |
| `attempt.opened` | `run_attempt` start (beside `attempt.started`) | mode (`state.last_attempt_mode`), operation, retry decision that caused it (ref to `recovery.decision.seq`), budgets snapshot (`retry_counters`) |
| `model.request` | `LLMClient`, before each wire request | model, binding alias, runtime fingerprint digest + exact, inference settings digest + settings, qualification identity + status (from the call's routing/qualification record, when known), adapter name/version, json_mode/schema/tools, max_tokens, budget decision (`budget.to_dict()`), deadline record, `messages`† (exact list), `tools`† (exact schemas), `wire_body`† (adapter's exact JSON, §5.3), section manifest ref, authority snapshot ref, `wire_reason` (`initial` / `response_format_dropped` / `empty_content_floor` / `investigation_turn` / `output_budget_lower_protocol`) |
| `model.response` | `LLMClient`, after each wire request | status, finish_reason, parser_status, backend_status/error (≤500 chars, as today), prompt tokens reported/estimated, completion tokens, elapsed, provider metadata, prefix reuse, provider_contract verdict, reasoning presence/chars/source, `content`† (transport content verbatim, before `split_reasoning`; inline `<think>` blocks physically in the content stay; D3 approved), `tool_calls`† (raw), separately returned reasoning field: digest + length always, text† only in `full_with_reasoning` mode |
| `prompt.sections` | request fitters (`fit_variable_section`, `fit_developer_request`, `fit_planner_request`, `review_requests`) | ordered list `{name, mandatory, requested_tokens, kept_tokens, outcome: kept/trimmed/dropped, reason, digest, content_ref†}`, capacity, model the request was fitted for. **The concatenation of kept sections must reproduce the request's user message byte-for-byte** (a test asserts it), so "context inserted / omitted" is exact, not estimated. |
| `authority.snapshot` | Developer choke point after `_decide_edit_capabilities`, before the call | per target: path, raw revision (`read_file_revision`), exists, `EditCapability` (operations offered, coverage kind, loci, capability digest), context tier shown (`full`/`member_exact`/`signatures`/...), member id + span + is_exact, D1 whole-file authority (yes/no + basis), minimum editable unit, in write scope (yes/no), requested operation; plus write scope, protected paths, redirected test obligations |
| `developer.parse` | each parse site (`agent.py:2005-2009`, `1031-1035`) | file path, protocol selected (`kriya_sentinel_v1` / `strict_legacy_v1` / raw), parse kind (FILE/EDITS/NO_CHANGE/INVALID), reason_code, detail, whole-payload unwrap applied (`outer_fence` / `json_envelope` / none), edit count, analysis†(`MODEL_CLAIMED`), truncation verdict, `call_seq` it parsed |
| `candidate.change` | after staging, at `_bind_verification_tree` (the point the candidate is frozen for gates); and at each pre-write refusal | per path: before raw digest (`all_original_raw`), after raw digest (`candidate_digests`), mode, deleted, line-diff stats, `diff`† (unified, text files; binary = digests only); write decision `STAGED` / `REFUSED` + reason code (authority/protocol/scope/integrity); candidate binding digest (`bind_candidate`) |
| `gate.result` | `validate._verification_gate` wrapper + non-validator gates (§5) | gate name, stage, command argv (Kriya-owned), cwd kind (sandbox/worktree/container), containment/egress, exit code, success, timed out, duration, target tests, `output`† (gz, hard cap 8 MiB with `truncated: true`, `original_bytes`, `full_digest`), runtime artifacts, verification-tree check result |
| `obligations.snapshot` | attempt close | obligation ledger revision + hash, unresolved ids by kind, requirement outcomes (codes) |
| `diagnosis` | `GenerationState.record_failure` and the recording step of `RecoveryCoordinator` | failure type, source, authority, reason_code (typed, §14 D7), message†, likely files, file locations, attribution tier/confidence/kind, attribution reasoning†, attributed target(s) and whether they are inside write scope, `evidence_class` ∈ {`MEASURED` (gate/tool output), `DERIVED_DETERMINISTIC` (locator/AST/grounding), `MODEL_CLAIMED`, `UNKNOWN`} |
| `recovery.decision` | `RecoveryCoordinator.handle` return | classification flags (`ClassifiedAttemptFailure`), `RecoveryDecision` (stop_loop, action, budgets_exhausted), `RetryDecision` (action, reason, reserved_fallback), progress classification + consecutive no-progress, no-progress terminal reason, plan-scope conflict, environment failure; `retry: yes/no` |
| `fallback.decision` | `_select_developer_fallback`, `_substitute_for_required_patch`, `_raise_fallback_incompatible`, escalation in `resolve_fallback_model` | requested model, candidates evaluated in order with each `ModelRequestProfile` digest and rejection reasons, selected model or none, phase (escalation/call) |
| `retry.delta` | `attempt.opened` of attempt n>1 | input-side delta vs the previous attempt's last Developer request (§3.4); outcome-side `ProgressVector.to_dict()` and `changed_dimensions` of the failed attempt |
| `attempt.closed` | `run_attempt` exit (normal, raise, cancel) | outcome PASSED / FAILED(type, reason_code) / STOPPED, gates passed, calls made, wall time |
| `unit.closed` | `run_generation_workflow` exit | status, failure_category, quality_gates_passed, SubtaskResult status, files committed |
| `run.closed` | run scope exit | terminal status, failure category, RunRecord lifecycle + revision + commit result, totals; followed by `seal.json` |
| `mirror.event` / `mirror.decision` / `mirror.evidence` | `GenerationState.record_event`, `DecisionLedger.record`, `GenerationState.record_failure` | the existing object's `to_dict()`, unchanged |
| `recorder.gap` | any record the recorder could not write, or a path entered with no scope | what was lost, why (`exception type`, no `RunContext`, capture disabled) |

### 3.4 Retry information delta (descriptive only in M1)

At `attempt.opened` for attempt n>1, the recorder compares the **inputs** of attempt n's first Developer request with
attempt n-1's last Developer request:

| Dimension | Source |
|---|---|
| `model_profile` | `ModelRequestProfile.digest` |
| `operation` / `mode` | attempt mode, requested operations |
| `targets` | sorted known target paths |
| `authority` | digest of `authority.snapshot` minus timestamps |
| `sections.<name>` | per-section digest from `prompt.sections` |
| `retry_evidence` | `budgets.last_retry_evidence_fingerprint` |
| `failure_signature` | of the failure that triggered the retry |
| `strategy` | repair contract / API-contract recovery phase / investigation enabled |
| `temperature` | effective retry temperature |

`information_gain = NONE` iff no dimension except the declared non-informative ones (attempt counter) changed.
Otherwise it is `PRESENT` with the list. It is `UNKNOWN` when either side has no recorded Developer request (e.g.
the attempt stopped before inference).

This value is **recorded, never read** by any decision in M1. Using it is R1.3's job, after it is measured against
the 80-run census and the certification cases.

### 3.5 Files

- `manifest.json` (written once, create-exclusive): schema version, run identity (as `run.opened`), redaction policy,
  capture mode, writer version.
- `records.jsonl` (append-only).
- `blobs/<aa>/<sha256>.gz` (content-addressed, gzip of exact bytes; the digest is of the *uncompressed* bytes).
- `seal.json` (written once at `run.closed`): `{final_seq, head_digest, record_count, blob_count, complete: bool,
  gaps: n, closed_reason}`.

A run without `seal.json` is `UNSEALED` (crashed or still running); the reader never guesses which.

---

## 4. Storage layout, naming and versioning

```
<state_dir>/attempt-evidence/                     (0700)
    <run_id>/                                     (0700; run_id from RunContext, already [A-Za-z0-9_-])
        manifest.json                             (0600)
        records.jsonl                             (0600)
        blobs/ab/abcdef....gz                     (0600)
        seal.json                                 (0600)
```

- **State directory, not workspace `.kriya/`.** The state directory is `trace_db_path`'s directory. Reasons:
  - Evidence survives worktree/sandbox cleanup and `git clean`.
  - It lives beside `traces.db`, which already holds proprietary failed content.
  - Candidate write authority can never reach it (`AuthorizedFileWriter` refuses `.kriya/**` anyway, and this is
    outside the workspace altogether).
  - Repository content can never point it elsewhere: no new path field (§7).
  - Enforce subtasks running in `.kriya/worktrees/<name>` write to the same run directory.
- **Per-run directory.** One run = one directory = one writer process. The workspace run lock already prevents two
  mutating runs in one workspace, and run ids are unique across workspaces, so no cross-process locking is needed.
- **Versioning.**
  - `schema` on every line. The reader supports an explicit list of versions and refuses others with
    `UnsupportedEvidenceSchema`, never best-effort.
  - Additive payload fields do not bump the version. Renamed or retyped fields do.
  - The blob format is fixed (`gz` of exact bytes).
  - The redaction policy has its own version in the manifest.
- **Link from the existing stores** (no schema change). A `evidence.attempt_store` RunEvent (AUXILIARY) is added to
  each trace row's `run_events` with `{run_id, path, seq_range, head_at_row}`. RunRecord is not changed in M1.

---

## 5. Lifecycle and integration points (61a867f)

The new code lives in **`kriya/core/attempt_evidence/`**:
- `model.py`: record dataclasses.
- `writer.py`: `AttemptEvidenceWriter`.
- `scope.py`: ContextVars and scope managers.
- `reader.py`: verify, explain and iterate.

It lives in `kriya/core` so that `kriya/core/llm.py` can call it without importing `kriya/workflow`. Production
modules import only `scope`/`writer`'s narrow `emit_*` facade.

### 5.1 Scopes (ContextVars, set and reset in `try/finally`, like `role_metrics._ROLE` and `run_trace._RUN_TRACE`)

| Scope | Opened at | Closed at |
|---|---|---|
| run | `run_coordinator.begin_mutating_run` after the RunContext is created (non-nested branch only, `run_coordinator.py:393+`) | its exit (normal, exception, KeyboardInterrupt), then `seal.json` |
| unit/invocation | `WorkflowEngine.run_generation_workflow` (the `record_run_exceptions` wrapper in `run_trace.py` already brackets it, so the scope goes in the same wrapper) | same wrapper's `finally` |
| phase | Planner (`workflow.py:2942/3096`), enforce structured Planner + repair rounds (`workflow_controller`), Architect, localization decision (`localization_decision.py`), Reviewer (`review_requests` callers), spec-compliance/requirement verifier, milestone Planner | phase end |
| attempt | `attempt.run_attempt` (the `attempt.started` site, `attempt.py:6326`) | `run_attempt` return/raise, and the retry loop's `handle_attempt_failure` completion |
| call | inside `LLMClient` per logical call | after `_finish` |

Calls outside any run scope (`kriya ask`, `review`, `plan-milestones`, `model qualify`, doctor) produce **no store**.
This is by design and stated, because M1's definition of done concerns production execution paths. Their
`CompletionResult` behaviour is unchanged.

### 5.2 Mirroring (one hook each, no producer changes)

| Existing stream | Hook |
|---|---|
| 83 `record_event` producer sites | `GenerationState.record_event` (`state.py:737`) |
| `structured_plan_validation`, `plan_created`, `subtask_attempt`, context package, ... (14 producer sites) | `DecisionLedger.record` (`decisions.py`) |
| Failure evidence | `GenerationState.record_failure` (`state.py:936`) |
| 85 `gate_outcomes.append` sites (72 `attempt.py`, 12 `workflow.py`, 1 `retry_strategy.py`; every receiver is `state.gate_outcomes`, MEASURED) | **`GenerationState.record_gate_outcome(outcome)`**: appends exactly as today, then emits `mirror.gate_outcome`. All 85 sites become calls to it (a mechanical, behaviour-neutral edit). An AST tripwire test forbids `.gate_outcomes.append/extend/insert` and `+=` outside `state.py`. The one production reassignment (`attempt.py:6391`, resume restoring a checkpoint's outcomes) becomes `state.restore_gate_outcomes(list)`, which emits one `mirror.gate_outcomes_restored` record (count + digest + checkpoint reference). |

**Why not a list subclass** (owner addition 1, checked read-only, M1.0 notes §1):
- **No exact-type dependency.** No `type(...) is list`, `isinstance` on it, `asdict`/`deepcopy`/pickle of the state,
  or `+=`/`extend`/slice-assignment in `kriya/` or `tests/`. Equality with plain lists is used in tests (`== []`,
  `== [{...}]`), and a subclass would satisfy it.
- **The decisive problem is reassignment.** `attempt.py:6391` (resume) assigns
  `state.gate_outcomes = list(...)`, and `tests/test_best_of_n.py:33` assigns a plain list. Either silently swaps the
  subclass for a plain list, and every later append goes unmirrored with no error.
- **A per-producer method plus tripwire is centralized and explicit**, and it cannot be bypassed silently.

**Cost:** fake states in tests that call production append paths (MEASURED: `tests/test_finite_command_artifact_preparation.py:53`
builds one with a bare `gate_outcomes = []`) need the method. M1.0 lists them all before the change.

**Rejected alternative:** a "watermark sweep" that mirrors new entries at attempt/recovery boundaries. It needs no
producer edits, but it is not immediate (a crash between append and sweep loses entries), so it was rejected for the
evidence path.

### 5.3 Model calls (`kriya/core/llm.py`)

- **Logical-call boundary.** `complete_result` (`llm.py:806`) and `complete_with_tools_result` (`llm.py:1131`):
  - Allocate `call_seq` after the egress check and before `_request_once`.
  - Emit `model.request` immediately before each `_request_once` (`llm.py:952/972/1008`) and before the tools call at
    `llm.py:1236`.
  - Emit `model.response` right after each, including the exception branches. Those are `BACKEND_ERROR`/`TIMEOUT`/
    `CANCELLED` and the deadline stop at `_record_deadline_stop`; the response record is written before re-raising.
  - Policy refusals before any wire request (`ContextBudgetUnsatisfiableError`, `OutputBudgetUnsatisfiableError`,
    `StructuredOutputUnsupportedError`, egress) emit a `model.request` with `dispatched: false` and the refusal code.
    The prompt that was refused is still recorded.
- **Wire body.** Add a pure port method `InferenceRuntimePort.wire_payload(request, *, stream) -> dict`:
  - The native adapter returns its existing `_payload(request, stream=...)` (`model_runtime.py:1288/1300/1323`).
  - The OpenAI-compatible transport returns the kwargs dict it passes to `chat.completions.create`
    (`inference_runtime.py:288/304/332`).
  - Both adapters are refactored to build the body once through this method and post exactly that object. A parity
    test patches `http.post`/`create` and asserts equality. INF-001's pinned wire tests must stay green unchanged.
- **Raw content.** `_request_once` returns `response.to_raw()`. The recorder takes `raw["content"]` before
  `split_reasoning` (decision D3).
- **No new data source is invented.** Every field is already computed in these functions; the recorder reads it
  after the fact, exactly like `last_call_metrics` (KRIYA_PERFORMANCE_TELEMETRY.md's control-flow-equivalence rule).

### 5.4 Developer attempt (`kriya/workflow/attempt.py`)

| Point | Record |
|---|---|
| `_run_developer_generation_as_developer` (`attempt.py:2551`), after `_decide_edit_capabilities` and `DeveloperRequestFit` construction, before `ctx.developer.run_generation` | `authority.snapshot` (from `context.edit_capability`, `state.known_target_context_items`, `known_target_member_items`, write scope from ctx) |
| `_maybe_run_developer_investigation` | each turn is a call in phase `attempt` with `wire_reason=investigation_turn` (no new hook: it goes through LLMClient) |
| `kriya/agents/agent.py` parse sites | `developer.parse` |
| `_bind_verification_tree` (`attempt.py:328`) | `candidate.change` (STAGED), once per attempt before the first gate |
| Pre-write refusals: `_validate_actual_mutation_authority`, `_authorize_anchors`, `_raise_unsafe_*`, file-integrity stops | `candidate.change` (REFUSED + reason code) via the existing `Failure` they raise: emitted from `record_failure` with `diagnosis`, so no per-site edit |
| `run_attempt` (`attempt.py:6248`) entry/exit | `attempt.opened` / `attempt.closed` / `obligations.snapshot` |

### 5.5 Gates

- **Validator gates.** In the `validate._verification_gate` wrapper (`validate.py:248`), emit one `gate.result` per
  gate invocation, around the existing tree-binding checks. It is emitted on the exception path too, before
  re-raising.
- **Non-validator deterministic gates.** Static checks, structural corruption, policy/authority refusals, spec
  compliance, requirement verdicts, the static-analysis gate and terminal gates (`TerminalGateService` reports): these
  already produce a `Failure` or a typed report.
  - A `Failure` is emitted through `record_failure` → `diagnosis`, plus a `gate.result` derived from
    `to_gate_outcome()` via the mirrored `gate_outcomes`.
  - A `TerminalGateReport` is emitted as one `gate.result` per gate at `TerminalGateService.run`'s return.
- The benchmark judge is not a Kriya gate and is never recorded by Kriya.

### 5.6 Diagnosis, recovery, fallback

- **Diagnosis and recovery.** `retry_strategy.handle_attempt_failure` → `RecoveryCoordinator.handle`
  (`recovery_coordinator.py:272`): after `_record_failure`, emit `diagnosis`; after `conclude_attempt_failure`, emit
  `recovery.decision`.
  - The `RetryDecision` must be visible, so `conclude_attempt_failure` returns it inside `RecoveryDecision` as a new
    optional field, `retry_decision`. That is an additive field and no caller changes.
- **Fallback.** Emit `fallback.decision` in `_select_developer_fallback` (`attempt.py:2033`),
  `_substitute_for_required_patch` (`2076`), `_raise_fallback_incompatible` (`2003`) and `_enter_developer_model`
  (`2113`).
- **Retry delta.** Emit `retry.delta` at `attempt.opened` for n>1, computed by the writer from the two
  attempts' recorded `model.request`/`prompt.sections`/`authority.snapshot` digests (pure function, unit-tested).

### 5.7 Planning, review and terminal

- **Planning phases.** Each Planner/repair round is a call. The validation outcome of each round is already a
  `structured_plan_validation` Decision and a `planning-diagnostics` record; mirrored.
- **Review and requirement verification.** These are calls in their phases; `final_review_refused` and requirement
  verdict events are mirrored.
- **Terminal.** `run.closed` is emitted from `begin_mutating_run`'s exit after the RunRecord's terminal transition.
  It reads the record via `load_run_record`, which is read-only. Then the seal is written.

---

## 6. Append-only and identity binding

1. **Single writer per run.** One `AttemptEvidenceWriter` per run scope (process-local). Appends go through
   `os.open(O_WRONLY|O_APPEND|O_CREAT, 0o600)`. Each append is one complete line written with a single `os.write`
   loop and followed by `flush`, with a `threading.Lock` around it. Validators can run in executor threads; asyncio
   emits come from the loop thread.
2. **No rewrite path exists.** The writer has no update or delete API. A correction is a new record with
   `supersedes: <seq>`, e.g. a diagnosis refined by a later attribution. A structural test asserts that `writer.py`
   opens `records.jsonl` only in append mode and never calls `truncate`/`seek`/`os.replace` on it.
3. **Hash chain + seal.**
   - Each line carries `prev`. `seal.json` carries the head digest and count.
   - `kriya evidence verify <run_id>` recomputes the chain and reports one of `VERIFIED`, `UNSEALED` (no seal: crashed
     or running), `TRUNCATED_TAIL` (last line partial: reported, never repaired) or `CHAIN_BROKEN(seq)`.
   - The chain detects accidental corruption and casual edits. It is not a signature against a local attacker with
     write access, and is stated as such.
4. **Blob binding.**
   - A record references blobs by SHA-256 of the exact uncompressed bytes. A blob is written `tmp → fsync →
     os.link`, create-if-absent, so it is immutable once present.
   - The reader re-hashes on read; a mismatch is `BLOB_CORRUPT`.
5. **Identity binding.**
   - Every record carries `run_id` and, where applicable, `unit_id`, `invocation_seq`, `attempt_number`, `call_seq`
     and `wire_seq`. These are read from the scope ContextVars, never passed by callers, so a caller cannot misattribute.
   - The run scope's `run_id` must equal `current_run_context().run_id`. A mismatch is a `recorder.gap` and the writer
     stops writing (fail-safe, never cross-run).
   - Candidate digests are the same raw-byte digests as `read_file_revision`/commit evidence schema 3 and
     `bind_candidate`, so a commit is joinable to the attempt that produced it.
6. **Durability.** These are the only guarantees M1 claims.

   **Write discipline:**
   - Every record is passed to `os.write` (loop until complete) before its `emit_*` returns.
   - Blobs are written `tmp → write → fsync(tmp) → os.link` before the record that references them is written.
   - `records.jsonl` is `fsync`ed only at `attempt.closed`, `unit.closed`, `run.closed` and immediately before
     `seal.json` is written. `seal.json` is written `tmp → fsync → rename`, then the directory is fsynced.

   **(a) Process termination** (exception, `SIGKILL`, OOM kill, crash of the Python process, with the OS still
   running): every record whose `emit_*` returned is in the kernel page cache and survives. At most the one record
   being written at the instant of death can be partial; the reader reports it as `TRUNCATED_TAIL` and never repairs
   it. The run is `UNSEALED`. No fsync is involved in this guarantee.

   **(b) Machine crash or power loss** (kernel panic, power cut, forced reboot): only bytes covered by a completed
   fsync are guaranteed, and only to the extent the OS's fsync guarantees it.
   - Records after the last attempt/unit/run boundary fsync **may be lost or partially present**, including records
     of an attempt that was in progress.
   - The file can end in a truncated or garbage tail. The chain check then reports `TRUNCATED_TAIL` or
     `CHAIN_BROKEN(seq)` at the first bad record, and everything before it remains verifiable.
   - **macOS caveat:** `os.fsync` on macOS does not force the drive's write cache to media; that needs
     `F_FULLFSYNC`. M1 uses plain `fsync`, so on macOS even boundary records are durable only against OS crashes, not
     guaranteed against power loss. A full-sync option would have to go through a `kriya/platform/` port (the
     platform guard forbids `fcntl` elsewhere), and it is out of M1 scope.

   **Nothing stronger is claimed.** In particular, M1 makes no per-record durability claim for power loss, and does
   not claim a seal exists for a run whose process or machine died.

7. **Architecture invariants** (owner-approved; each is enforced by a test named in §12):

   - **I-1 Layout is internal.** Evidence persistence layout is internal. Consumers such as the GUI, analyzers,
     benchmark scripts and the CLI must use `kriya/core/attempt_evidence/reader.py`'s stable reader API (or
     `kriya evidence ... --json`, which is built on it). They must never parse `records.jsonl`, `manifest.json`,
     `seal.json` or blob paths directly.
     - Layout and file names may change without notice. The reader API and the record schema are the versioned
       contract.
     - Enforcement: an AST/text tripwire (T15) fails on any code in `kriya/` (outside `attempt_evidence/`),
       `benchmarks/` or `scripts/` that names `records.jsonl`, `seal.json`, `manifest.json` together with
       `attempt-evidence`, or `blobs/`.
   - **I-2 Zero behavioural effect.** The recorder in `full`, `digest_only`, `off` or a fault-injected state must not
     change any of these:
     - provider request bytes;
     - workspace bytes;
     - the run result;
     - retry/fallback behaviour;
     - any existing control-plane outcome: RunRecord lifecycle/commit, obligation and requirement outcomes, terminal
       gates, the decision ledger and trace rows (apart from the one `evidence.attempt_store` pointer event).

     This is the strongest M1 acceptance condition. T3 proves it, and M1 cannot close without it.
   - **I-3 No inference of missing evidence.** No producer (the recorder or the M1.11 legacy importer) infers or
     synthesizes a value that was not observed. Absent evidence is recorded as an explicit `NOT_RECORDED` with a
     reason. For legacy runs this includes, at least, the prompt, the raw response, the per-attempt diff, the
     authority snapshot, per-gate output of passing gates, and the recovery decision object.

---

## 7. Security and redaction policy

- **Classification.** The store is `local_only_proprietary`, the same class as `EvidenceRecord` and `traces.db`. It
  holds proprietary source, prompts and model output. It is never transmitted:
  - The recorder has no network code (the `test_prd012_network_inventory.py` inventory is unchanged).
  - The outward-lookup sanitized request types cannot accept these records. The design reuses the existing type
    separation, `evidence.py` docstring.
- **Never recorded:**
  - API keys, client headers, base-URL credentials. Only `messages`/`tools`/wire *body* are recorded, never transport
    configuration.
  - MCP `env` values. They are not in prompts, and SEC-009 digests them already.
  - Reasoning text, unless capture mode is `full_with_reasoning` (decision D3).
  - Benchmark gold targets or judge data. The recorder has no input for them; a structural test asserts no `gold`
    field or name in `kriya/core/attempt_evidence/`.
  - Untrusted learned/web knowledge is recorded exactly as it was fenced in the prompt (it is part of the prompt). It
    is never unfenced or treated as instructions by any reader.
- **Exact bytes, no scrubbing.** Evidence fidelity requires the actual prompt. A secret-scrubbing pass would make the
  record not the actual prompt and could hide a real leak. Secrets reaching a prompt is governed upstream
  (`autonomy.sensitive_paths`, `AuthorizedFileReader`). The recorder is not a second, weaker filter.
- **Capture modes.** The config key is `evidence.attempt_recorder.capture`:
  - `full` (recommended default): content blobs recorded.
  - `digest_only`: every content field is recorded as a digest + byte length, and no blobs are written. It is
    content-free, like PRD-033.
  - `full_with_reasoning`: adds reasoning text.
  - `off`: no store; a `evidence.attempt_store` event says `disabled`.
- **SEC-009 classification.**
  - `evidence.attempt_recorder.*` is `SECURITY_AUTHORITY` as a whole. A repository must not be able to turn off or
    reduce the audit trail of what was done to it, nor widen capture to reasoning text.
  - There is **no path field**: the location is the state directory, which is already governed (env > SEC-009
    `paths.state` > default).
  - **No production-profile sealing in M1 (D5 deferred).** The operator may set any capture mode, including `off`.
    `off` and degraded states are reported loudly (run event, seal, doctor), never refused. A future SEC-009
    audit-required mode is a separate decision.
- **Filesystem.** Directories are 0700 and files 0600, created with an explicit mode, independent of the process
  umask. Doctor checks this.
- **Export.** Out of M1. A future `kriya evidence export --redact` would be a separate, explicit, operator-invoked
  path.

---

## 8. Compatibility with existing evidence and analyzer infrastructure

- **Unchanged stores.**
  - `traces.db`: schema unchanged; one additional AUXILIARY event kind, `evidence.attempt_store`.
  - RunRecord: schema 3, unchanged.
  - `decisions.jsonl`: unchanged. It is still written exactly as today, and also mirrored.
  - PRD-033 metrics: unchanged, and **must not read the store**. A structural test asserts `kriya/metrics` does not
    import `attempt_evidence`, and the existing content canary stays green.
- **CAGC `ab_analyzer.py`.** Unchanged. It reads `traces.json` + `untracked.tar` and keeps working.
- **Future harnesses.** Driver scripts copy `<arm state>/attempt-evidence/<run_id>/` into the run's evidence
  directory, verified with `kriya evidence verify`.
- **R1 reliability analyzer** (a new script under `benchmarks/`, not `kriya/`). It reads the store through
  `reader.py`'s stable iteration API, never by parsing internals, and produces the §8 metrics of the R1 spec.
- **Legacy importer** (`benchmarks/reliability/import_legacy.py`). It maps an old run's `traces.json`/control archive
  into the same record kinds with `provenance: "LEGACY_RECONSTRUCTED"` and an explicit `NOT_RECORDED` record for every
  field the old evidence never had. This is the only way the 80-run census can share the M1 schema (§14, R2).
- **`kriya traces`.** Unchanged. A new read-only `kriya evidence show|explain|verify <run_id>`:
  - `explain` answers the nine M1 questions per attempt.
  - It prints `NOT RECORDED (<reason>)` rather than an empty field.

---

## 9. Failure handling: telemetry never corrupts or authorizes a run

1. **Write-only from production.** Nothing in `kriya/workflow`, `kriya/control`, `kriya/policy`, `kriya/agents`,
   `kriya/tools` or `kriya/core` (other than the writer itself) imports `reader.py`. Every `emit_*` returns `None`.
   A structural AST test enforces both.
2. **Non-raising emits.** Every `emit_*` catches `Exception` internally (never `BaseException`: cancellation and
   KeyboardInterrupt propagate unchanged). On failure it:
   - logs one warning;
   - records an in-memory `degraded` reason;
   - stops writing further content blobs for this run (records continue if the records file still works);
   - surfaces `evidence.attempt_recorder_degraded` in the run events and `complete: false` + `gaps` in the seal.

   Per CLAUDE.md quality bar 4, each broad catch has a test asserting the normal (non-exception) output, so a
   `TypeError` inside an emit fails a test.
3. **No outcome change.** A degraded recorder never changes status, retry, commit or exit code. The equivalence suite
   (§12, T3) proves byte-identical provider requests, workspace bytes, RunRecord and trace rows (minus the pointer
   event) with the recorder `full`, `digest_only`, `off` and fault-injected.
4. **Opening the store (D5 DEFERRED: observational only).** In every profile, production included, a store that
   cannot be opened never blocks or alters the run. Typical causes are an unwritable state dir, a full disk, a
   permission error, or a `run_id` that already has a directory. When that happens:
   - one `RECORDER_UNAVAILABLE` warning is logged with the cause;
   - a `evidence.attempt_store` run event `{status: "RECORDER_UNAVAILABLE", reason}` goes into the trace row (if the
     trace row itself cannot be written, the log line is the only record, and that is stated);
   - every later `emit_*` is a no-op;
   - the run continues unchanged (I-2);
   - `kriya evidence show/explain` reports `ATTEMPT EVIDENCE UNAVAILABLE (<reason>)` for that run, and doctor's
     `evidence.attempt_recorder` row reports the last unavailable run.

   Mid-run failures degrade the same way, with `seal.complete: false` when a seal can still be written.

   **No `ATTEMPT_EVIDENCE_UNAVAILABLE` execution blocker exists in M1.**
5. **No authority leak.** The recorder writes only beneath `<state>/attempt-evidence/<run_id>/` through its own
   writer, never through `write_control_file` or `AuthorizedFileWriter`. It is classified in
   `tests/test_file_integrity_contract_001.py::_AUDITED_WRITE_SITES` and
   `handover/FILE_INTEGRITY_CONTRACT_001.md`. It never writes in the workspace or sandbox.
6. **Platform guard.** File modes use `os.open(..., mode)`; no `fcntl`/`pwd`/`resource` (the
   `test_platform_architecture_guard.py` allowlist stays empty).

---

## 10. Performance, token and disk impact

- **Tokens: zero by construction.** No prompt text changes. T3 asserts byte-identical requests. `prompt.sections`
  reads what the fitters already computed.
- **CPU and latency** (INFERRED; measured in M1.10 before closure):
  - Per call: SHA-256 + gzip of the prompt and response. A 100–400 KB prompt is ~5–20 ms, against model calls of
    10–300 s (MEASURED pace this matrix: ~16 min per run).
  - Per gate: gzip of output, ≤8 MiB.
  - Writes are synchronous on the loop thread. If M1.10 measures more than 1% of wall time, move blob compression to
    a bounded single worker thread with an ordered queue; records stay synchronous.
- **Disk:**
  - Reference sizes (MEASURED): one completed experiment run's `traces.json` is ~540 KB, and its `untracked.tar` is
    ~31 MB, mostly worktrees.
  - Estimate (INFERRED): 10–40 calls per run × (prompt 50–400 KB + response 2–30 KB) + gate outputs → 1–15 MB raw per
    run, ~0.3–3 MB gz after content-addressed dedup of repeated system prompts and sections.
  - `digest_only` mode is ~50–200 KB/run.
- **Retention.** The state dir has no retention today. M1 adds `evidence.attempt_recorder.retention`:
  - `keep_runs` (default 200) and `max_bytes` (default 5 GiB).
  - Pruning runs at run close, under the workspace lock, oldest sealed runs first.
  - It never touches the active run, an unsealed run younger than 24 h, or a run named by a resume checkpoint.
  - It is a mark-and-sweep that mirrors `control/retention.py`'s rules.
  - `kriya evidence prune --dry-run` shows the plan.

---

## 11. Backward compatibility

- **Old runs.** Runs before M1 have no store. The reader returns `NOT_RECORDED (pre-M1)`; the legacy importer is the
  explicit path for them.
- **No migrations.** No schema migration of `traces.db`, RunRecord or decisions.
- **Config.** The new section has defaults. An absent section means the defaults; this mirrors
  `static_analysis` "missing = disabled" semantics, except that the default here is `full` (decision D4).
- **Behaviour.** With `capture: off`, behaviour is byte-identical to 61a867f, apart from one `evidence.attempt_store
  {disabled}` event. A test pins this.
- **Resume.** INFERRED, not yet traced; M1.0 verifies it. A resumed run gets a new RunContext with a fresh run id
  (`begin_mutating_run(run_id=None)`) and records what it reused in `RunRecord.resume_decision`. It therefore writes
  a new store whose `run.opened` references the resumed run's id. If M1.0 shows the run id is reused, the store must
  open a new `segment-<n>` file rather than append across processes. Stores are never appended to across processes.
- **Qualification and certification.** Unaffected. The recorder is not an inference setting and does not enter the
  runtime fingerprint, inference settings, qualification identity or release identity. Adding `wire_payload` to the
  port is a refactor with pinned wire parity, so `MODEL_PROTOCOL_ADAPTER_VERSION` is **not** bumped. If the parity
  test shows any byte difference, the refactor is wrong, not the version.

---

## 12. Deterministic test plan and mutation targets

All tests are mocked (no live model). They use the existing doubles:
- `tests/_fake_inference_runtime.py`;
- `tests/_edit_protocol_harness.py`;
- `tests/_strict_doubles.py`;
- `tests/_provider_usage.py`;
- `tests/_protocol_responses.py`.

There is one new file per area, `tests/test_lr_r1_m1_*.py`.

| # | Test | Reproduces |
|---|---|---|
| T1 | Schema round-trip; every kind validates; unknown schema version is refused by the reader | contract |
| T2 | Append-only chain: tamper middle record → `CHAIN_BROKEN(seq)`; partial last line → `TRUNCATED_TAIL`, file bytes unchanged after `verify`; missing seal → `UNSEALED`; blob byte flip → `BLOB_CORRUPT` | integrity |
| T3 | **Invariant I-2 (strongest acceptance condition).** The same scripted runs, each run four times (`full`, `digest_only`, `off`, fault-injected writer incl. store-open failure): direct success; protocol failure → retry → success; enforce 2 subtasks; fallback incompatible; no-progress terminal; resume with restored gate outcomes; Planner repair. Must match across the four: provider request bytes (captured by the fake runtime, incl. every wire resend); workspace bytes; result dict; the sequence of attempt modes, retry decisions and fallback selections; RunRecord (minus timestamps); obligation/requirement outcomes; decision ledger; trace rows (minus the pointer event) | non-interference |
| T4 | Fault injection at every emit site (raise `OSError`, `TypeError`, disk-full) → run outcome unchanged, `degraded` event present, seal `complete:false`; `CancelledError` and `KeyboardInterrupt` still propagate | §9 |
| T5 | Structural: no production import of `reader`; `kriya/metrics` imports nothing from `attempt_evidence`; writer opens records only `O_APPEND`; no `gold` names; no network clients | §9, §7 |
| T6 | **M1 coverage** — `explain(run_id)` answers each of the 9 questions with a non-`NOT_RECORDED` value, asserted per question, for each path: single-shot batch Developer; iterative per-file Developer (one call per file — fixes the documented undercount); `response_format_dropped` and `empty_content_floor` (two wire requests, one call); output-budget lower-protocol retry; investigation turns; patch-only target + whole-file-only fallback (`FALLBACK_MODEL_INCOMPATIBLE`, every rejected candidate with reasons); `REPEATED_VECTOR` terminal; plan-scope conflict; Planner repair rounds; enforce TOOL subtask (no model call: `NOT_RECORDED(no_model_call)` is the correct answer to Q1-3, asserted); final review refusal; exception escape; cancellation; deadline stop; context-budget refusal before dispatch (prompt recorded, `dispatched:false`) | definition of done |
| T7 | Identity: nested enforce subtasks share `run_id`, distinct `unit_id`; a unit invoked twice gets `invocation_seq` 1, 2; two sequential runs in one process never cross-attribute (ContextVar reset in `finally`); two concurrent asyncio tasks with separate contexts; scope `run_id` ≠ RunContext → gap + stop writing | §6.5 |
| T8 | Candidate binding: recorded after-digests equal `state.candidate_digests` and `bind_candidate`; applying the recorded diff to the recorded before blob reproduces the after blob; REFUSED candidates carry the authority reason code | §3.3 |
| T9 | Prompt fidelity: recorded `messages` equal the fake runtime's received `ChatRequest` exactly; `prompt.sections` kept-concatenation equals the user message; `wire_payload` equals the JSON the patched `http.post`/`create` received (both adapters) | §5.3 |
| T10 | Redaction: canary API key and canary MCP env value absent from every byte of the store; in `digest_only` a canary goal/source string is absent; reasoning text absent unless `full_with_reasoning`; modes 0700/0600 | §7 |
| T11 | Retry delta: an identical request pair → `information_gain: NONE`; each dimension changed alone → `PRESENT` naming exactly that dimension; missing request → `UNKNOWN` | §3.4 |
| T12 | Retention: never prunes active/unsealed-young/resume-referenced runs; prunes oldest sealed first; dry-run writes nothing | §10 |
| T13 | `record_gate_outcome` / `restore_gate_outcomes`: list contents identical to the pre-M1 appends; every append mirrored exactly once; resume restoration emits one restore record; AST tripwire fails on a direct `.gate_outcomes.append/extend/insert/+=` outside `state.py` (negative control: a planted direct append is caught) | §5.2 |
| T14 | SEC-009: a repository-sourced `evidence.attempt_recorder.*` (incl. `capture: off`) is denied. **D5 deferred:** under `runtime_profile: production` an unopenable store (unwritable dir, full disk, existing run dir) gives `RECORDER_UNAVAILABLE` evidence, and the run's result is identical to a run with a working store (asserted as part of T3); no `ATTEMPT_EVIDENCE_UNAVAILABLE` code exists in `kriya/` (structural) | §7, §9.4 |
| T15 | Invariant I-1: a tripwire over `kriya/` (outside `attempt_evidence/`), `benchmarks/` and `scripts/` finds no reference to the store's internal file names or blob paths; negative control planted | §6.7 |
| T16 | Invariant I-3 (importer): for a synthetic legacy run fixture, every field the fixture lacks (prompt, raw response, per-attempt diff, authority snapshot, passing-gate output, recovery decision object) is emitted as `NOT_RECORDED` with a reason, and no other field is. A mutation that fills any of them from a neighbouring field (e.g. the final `workspace.diff` as the last attempt's diff) fails | §6.7, M1.11 |

**Mutation targets** (ENGINEERING_RULES §8). Each must make a named test fail:

| Mutation | Must fail |
|---|---|
| omit `prev` | T2 |
| not increment `seq` | T2/T7 |
| skip the scope reset in `finally` | T7 |
| re-raise inside an emit | T4 |
| swallow `CancelledError` | T4 |
| record the post-split content instead of the raw content | T9 |
| skip the second wire record | T6 |
| swap before/after digests | T8 |
| drop the REFUSED path | T8 |
| a constant `information_gain = PRESENT` | T11 |
| write blobs in `digest_only` | T10 |
| allow `capture: off` from a repository config | T14 |
| store-open failure raises instead of degrading | T14/T3 |
| prune an unsealed run | T12 |
| `record_gate_outcome` mirrors twice, or not at all | T13 |
| resume reassigns directly | T13 tripwire |
| importer fills a missing field instead of `NOT_RECORDED` | T16 |
| an analyzer reads `records.jsonl` directly | T15 |
| `wire_payload` built separately from the posted body | T9 |

Before closure, also run:
- `pylint`/`ruff` gates;
- the adjacent suites (`test_performance_telemetry.py`, `test_prd033_*`, `test_prd018_role_metrics.py`,
  `test_inf001_*`, `test_provider_contract_*`, `test_file_integrity_contract_001*.py`,
  `test_platform_architecture_guard.py`, `test_prd012_network_inventory.py`);
- one full parallel suite at the batch boundary.

**Original-symptom check** (rules §6). Re-run one recorded scripted reproducer of each live failure family the R1
spec lists:
- protocol invalid;
- fallback incompatible;
- `REPEATED_VECTOR`;
- `REGRESSION_UNATTRIBUTED`;
- `NO_AUTHORIZED_REPAIR_TARGET`;
- Planner repair.

Each must show `explain` answering Q1-Q9.

---

## 13. Implementation tasks (small, independently reviewable commits)

Each task is behaviour-neutral, except M1.8b, which is called out.

| Task | Content | Proof |
|---|---|---|
| M1.0 | **Trace inventory (no production change).** The read-only part is done in the notes. Remaining, under the implementation authorization: confirm by test that ContextVars reach every model call path (incl. validator call path, investigation, self-correction, enforce subtasks); confirm RunContext reuse in nested runs and resume run-id semantics; list every `fit_*` site; list every test fake state that the `record_gate_outcome` change needs | throwaway probes + one permanent structural test |
| M1.1 | `kriya/core/attempt_evidence/{model,writer,reader}.py`: envelope, chain, blobs, seal, verify; no wiring | T1, T2, T10 (unit) |
| M1.2 | Scopes + run/unit/attempt/phase open/close + `evidence.attempt_store` pointer event + config section (SEC-009 classification, defaults) | T7, T14 (part), T3 skeleton |
| M1.3 | Mirroring hooks: `record_event`, `DecisionLedger.record`, `record_failure`; `record_gate_outcome`/`restore_gate_outcomes` + the 85-site mechanical conversion + tripwire (its own commit inside M1.3, diff reviewable as pure substitution) | T13, T3 |
| M1.4 | `InferenceRuntimePort.wire_payload` refactor in both adapters (parity only) | INF-001 pinned wire tests unchanged + T9 (wire) |
| M1.5 | `model.request`/`model.response` at LLMClient, including wire resends, refusals, errors, cancellation, deadline | T9, T6 (call paths), T4 |
| M1.6 | `prompt.sections` from the fitters (`fit_variable_section` primitive, `fit_developer_request`, `fit_planner_request`, `review_requests`) | T9 (sections) |
| M1.7 | `authority.snapshot` + `developer.parse` | T6 |
| M1.8a | `candidate.change`, `gate.result` (validator wrapper + `TerminalGateReport`), `obligations.snapshot` | T8, T6 |
| M1.8b | `diagnosis`, `recovery.decision` (additive `RecoveryDecision.retry_decision` field), `fallback.decision`, `retry.delta`. **Separate commit, D7 APPROVED:** typed `reason_code` in `Failure.diagnostics` for `NO_AUTHORIZED_REPAIR_TARGET` and `REGRESSION_UNATTRIBUTED`. It is additive; existing messages and decisions are byte-unchanged, and **no production decision may consume the new codes in M1**. A structural test: the codes are referenced only at their producers, the recorder and tests | T11, T6, T3, rule-22 report |
| M1.9 | `kriya evidence show/explain/verify/prune` (read-only except prune) + doctor row `evidence.attempt_recorder` (writable, modes, capture mode, last run sealed) + docs (`docs/design.md`, `user_guide.md`, CLAUDE.md section, FILE_INTEGRITY write-site classification) | T6 via CLI, T12 |
| M1.10 | Equivalence + fault-injection suites complete; mutation campaign; overhead measurement on a scripted 30-call run (and later one bounded live run, owner-authorized) | T3, T4, mutation table |
| M1.11 | `benchmarks/reliability/import_legacy.py` (outside `kriya/`): CAGC-v2 evidence → schema with `LEGACY_RECONSTRUCTED`. **Invariant I-3:** it never infers unavailable legacy fields. Prompt, raw response, per-attempt diff, authority snapshot, passing-gate output and recovery decision object, and anything else absent from the frozen artifacts, are explicit `NOT_RECORDED` with a reason. Final `workspace.diff` is recorded as the run-level final diff, never attributed to an attempt. It emits through the writer API and reads legacy artifacts only (I-1 applies to its output side). It runs only on a copy of the frozen 80-run evidence, after the freeze | T16 on synthetic fixtures; then a read-only run on a copy |

M1.11 can start right after the evidence freeze in parallel with M1.1-M1.10, because it touches nothing in `kriya/`.

---

## 14. Architectural risks and disagreements with the R1 plan

### Disagreements and refinements

- **D1. The hierarchy Run → Subtask → Attempt is insufficient.**
  - The code has model calls that belong to no subtask attempt: Planner and its repair rounds, Architect,
    localization decision, Reviewer, requirement verifier, milestone Planner.
  - It has several calls per attempt: the iterative per-file Developer, investigation turns, self-correction, the
    output-budget lower-protocol retry.
  - It has several wire requests per call: `response_format_dropped`, `empty_content_floor_retry`.
  - Recording only attempts would recreate today's "last call only" undercount. The design adds Unit/Invocation,
    Phase, Call and Wire (§3.1).
- **D2. "MEASURED / TRACED / INFERRED / UNKNOWN" on runtime diagnoses.**
  - TRACED means a human read the producing code; Kriya cannot honestly emit it at run time.
  - The runtime vocabulary is `MEASURED / DERIVED_DETERMINISTIC / MODEL_CLAIMED / UNKNOWN`, mapped onto the existing
    `attribution_tier`.
  - TRACED/CONFIRMED stay labels for human adjudication, kept beside the store, as PRD-033 does with adjudication.
- **D3. "Raw response, never destroyed" vs PRD-015.**
  - PRD-015 deliberately keeps reasoning presence, never reasoning text.
  - Recommendation: record the transport `content` verbatim, pre-`split_reasoning`. That keeps inline `<think>`
    blocks and makes the splitter itself auditable. Record a separate reasoning *field* as length + digest only,
    unless `full_with_reasoning`.
  - **APPROVED** (2026-10-04) as recommended.
- **D4. Default capture mode.** **APPROVED = `full`**; `digest_only` is operator-controlled; `full_with_reasoning`
  is explicit opt-in.
- **D5. Fail-closed at open under production.** **DEFERRED.** M1 is observational (§9.4); revisit after M1's
  certification, mutation, performance and bounded live validation as a separate SEC-009 audit-required mode.
- **D6. "Repair applied to response, if any."** Kriya never repairs a payload (FILE-INTEGRITY-CONTRACT-001); the only
  transformation is removal of a whole-payload wrapper (one outer fence, the JSON envelope). The record states exactly
  that and nothing more. No "repair" field is introduced, because one would suggest an operation that must not exist.
- **D7. Two diagnosis codes are text prefixes.** `NO_AUTHORIZED_REPAIR_TARGET` and `REGRESSION_UNATTRIBUTED` are
  matched as message text (`workflow.py:5680`). **APPROVED:** an additive typed `reason_code` at the two producers in
  the separate M1.8b commit. Messages and decisions are unchanged, and nothing consumes the codes in M1.

### Risks

- **R1. Parallel-system risk.** It is mitigated: existing streams are mirrored, not re-modelled, and new kinds exist
  only where nothing records the fact today. The table in §1 is the dedup reference.
  - Why not just extend `traces.db`? It is written once per invocation by `INSERT OR REPLACE` (lost on crash, not
    append-only, one JSON blob per column).
  - Why not just extend `DecisionLedger`? It rewrites the whole file per append, lives in the workspace and is
    content-free by contract.
  - Extending either would break their own contracts.
- **R2. The 80-run census cannot use M1, and the R1 order implies it might.**
  - R1 §16 places "reconstruct 80-run failure census" after R1.1. M1 only records forward runs.
  - For the 80 runs, Q1 (prompt), Q3 (raw response) and per-attempt Q4 (diff) are **permanently NOT RECORDED**. This
    is INFERRED from the INFO log level and from the TRACED absence of these fields in trace rows.
  - The census must use the legacy importer (M1.11) over the existing events. Protocol-failure subtypes are available
    only as the reason codes the events carry.
  - Recommendation: build the census importer first, right after the freeze. It is independent of M1, so R1.2-R1.4
    priorities can be set from the baseline while M1 is implemented.
- **R3. Evidence volume and sensitivity.** Storing actual prompts duplicates proprietary source per call. This is
  bounded by retention, dedup and `digest_only`, and is classified local-only. The operator must understand that
  `full` evidence is as sensitive as the repository itself. Doctor and the user guide say so.
- **R4. `information_gain` misuse.** A descriptive delta can be mistaken for a decision input before it is validated.
  Mitigations:
  - M1 forbids any read (structural test T5).
  - Its definition is versioned.
  - R1.3 must validate it against the census and certification cases before any policy uses it.
- **R5. Observer effect on timing.** Synchronous writes add latency. The equivalence suite proves outputs are
  identical, not timing. Deadline-sensitive paths (`generation_time_budget_seconds`) could be affected by a slow
  disk. M1.10 measures it, with the bounded worker as the remedy (§10).
- **R6. ContextVar propagation.** `asyncio.to_thread` copies the caller's context. `loop.run_in_executor` and plain
  `ThreadPoolExecutor.submit` do **not**.
  - TRACED (M1.0 notes §2): every thread hand-off in `kriya/workflow/` and `kriya/core/llm.py` at 61a867f is
    `asyncio.to_thread` (10 sites).
  - `run_in_executor` appears only in `kriya/mcp/mcp.py` (container cleanup; no evidence emit).
  - The `threading.Thread`s in `kriya/tools/process.py` are stdout/stderr pumps that emit nothing.
  - The residual risk is a future site. M1.0 adds a structural test that fails on a new `run_in_executor`/
    `ThreadPoolExecutor` in `kriya/workflow`, `kriya/agents`, `kriya/core/llm.py` or `kriya/tools/validate.py`
    unless it is classified. M1.0 measures it. The remedy is passing the scope explicitly
  into the `_verification_gate` emit (it runs on the validator object, which can carry the scope captured at
  construction), never guessing attribution.
- **R7. Resume semantics.** A resumed run is a new run id (INFERRED; verified in M1.0). Explaining "why did the run finally fail" across a resume
  chain needs the `resume_decision` link, recorded in `run.opened`; the reader follows it. Cross-run chains are
  displayed, never merged.
- **R8. GUI coupling.** The GUI (R1 §15) should consume `reader.py`'s iteration API and `kriya evidence explain --json`,
  never the file layout, so the store can evolve without breaking it.

---

## 15. What is explicitly out of scope for M1

- Any retry, fallback, Planner, protocol or diagnosis **policy** change (R1.2-R1.6).
- Using `information_gain` or any recorded field in a decision.
- Evidence for non-mutating commands (`ask`, `review`, `plan-milestones`, qualification, doctor).
- Signatures or remote attestation of evidence.
- Export and redaction tooling.
- GUI views.
