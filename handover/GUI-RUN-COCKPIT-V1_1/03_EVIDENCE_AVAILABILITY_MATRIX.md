# 03 — Evidence availability matrix (design-freeze precondition)

**Repository state:** Kriya-main-demo `main` @ 1a89bc5 (product frozen at executable 3844540). Every row was traced
against this revision on 2026-10-10. **Status labels:** TRACED = the writer/reader code was read; MEASURED = counted
or observed by a command; UNKNOWN = not traced, treated as the worse category until traced (listed in §4).

**Categories.** A = available now through a supported read operation (KUP v1, nine operations, or an M1 host
capability). B = recorded durably but not exposed by any supported read operation (needs an operation from
`04_UI_READ_API_CONTRACT.md`). C = not recorded anywhere. D = live-only (needs the future live transport). E = action
(needs a backend action contract). **Rule:** no implementation moves a datum from B or C to A by console scraping,
log parsing, direct store access or reading the current working tree.

**The two durable sources.**

| Source | Location | Writer | Reader today | Consistency kind | Immutability |
|---|---|---|---|---|---|
| S1 trace store (history) | `<state>/traces.db`, read via a published SQLite backup `kup-snapshots/<id>/traces.snapshot.db` | `kriya/core/trace.py` (`runs` table: run_id, timestamp, goal, duration_sec, attempts, status, files_modified, retrieved_chunks, active_skills, prompt_rendered, gate_outcomes, model_hops, failure_category, milestone_group_id/index/total, run_events, evidence_records, generation_metrics, failure_report, enforce_run_id) | KUP `history.list`, `history.detail`, `history.prompt` (`kriya/kup/inspect.py`) | `snapshot_copy` | one row per run, `INSERT OR REPLACE`; a snapshot is immutable once published and digest-verified at pin |
| S2 attempt-evidence store | `<state>/attempt-evidence/<run_id>/` (JSONL record chain + `blobs/<xx>/<sha256>.gz` + manifest + seal) | `kriya/core/attempt_evidence/writer.py` via `scope.py` helpers; capture default `full` (`AttemptRecorderConfig`, SECURITY_AUTHORITY: a repository config can never change it) | CLI only: `kriya evidence show/explain/verify/leak-check/candidate` over `reader.py` (invariant I-1: explain is built only on the reader) | none in KUP today; proposed `sealed_store` (= the responding operation verified the seal and chain for the stated head_digest in this invocation, or names the `evidence.verify` that did) and `live_observation` (anything else, including `evidence.manifest`) | append-only hash chain (`prev` digest per record); sealed at run close; retention (keep_runs 200, max_bytes 5 GiB) removes whole stores without a tombstone |
| S3 point observations | workspace control records, qualification home, authority approval artifacts | `kriya/control/recovery.py`, `kriya/core/model_qualification.py`, `kriya/config/authority_approval.py` | KUP `workspace.status` (A); `kriya authority inspect`, `model_qualification.load_record` (CLI/API only) | `live_observation` | observation at request time, never attached to a trace row (gate D-6) |

Envelope fields S2 records carry (`model.ENVELOPE_KEYS`): schema, seq, prev, kind, run_id, unit_id, unit_kind,
invocation_seq, phase, attempt_number, call_seq, wire_seq, role, t_wall, t_mono_ms, provenance, payload,
content_digests, blobs. Provenance values: OBSERVED, DERIVED, MODEL_CLAIMED, MIRRORED, NOT_RECORDED,
LEGACY_RECONSTRUCTED. Diagnosis evidence classes: MEASURED, DERIVED_DETERMINISTIC, MODEL_CLAIMED, UNKNOWN.

---

## 1. Matrix

Columns: datum; exact source (writer → field); persistence status; read operation today; category; capture/retention
limits; UI behaviour when unavailable. "proposed" names an operation in `04_UI_READ_API_CONTRACT.md`.

### 1.1 Run identity and history

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 1 | Run list (run_id, timestamp, goal, duration_sec, attempts, status, failure_category, files_modified) | S1 `runs` columns; `inspect.LIST_COLUMNS` (TRACED) | durable | `history.list` (paged, cursor bound to snapshot) | A | timestamp has no timezone; files_modified is the raw comma-joined string | n/a |
| 2 | Milestone grouping (milestone_group_id, milestone_index, milestone_total) | S1 columns, in `LIST_COLUMNS` (TRACED) | durable, NULL for non-milestone runs | `history.list` | A | none | rows without a group are listed individually |
| 3 | Enforce linkage (enforce_run_id) | S1 column (TRACE-ENFORCE-SUBTASK-LINKAGE-001) (TRACED) | durable | none: the column is in neither `LIST_COLUMNS` nor `DETAIL_TEXT_COLUMNS` of `inspect.py` (TRACED) | B | needs a v1.1 additive column in `history.list`/`detail` | linkage not drawn; rows listed individually |
| 4 | Run status | S1 `status`; values written in production: success, failure, error, in_progress, knowledge_gap, baseline_indeterminate, architect_file_list_rejected (`workflow.py`, `cli.py`), `milestone_plan_<status>` for `<group>.milestone-plan` rows (`milestones.py:845`), `unknown` for `<run>.enforce` rows (`workflow_controller.py`) (MEASURED + review 002, not necessarily exhaustive) | durable | `history.list`, `history.detail` | A | an `in_progress` row has no terminal record (crash or active); never shown as "Active" | show the literal; `in_progress` displays as **Unfinished (no terminal record)** |
| 5 | Workspace active state (CLEAN, RUN_ACTIVE, RECOVERY_AVAILABLE, COMPLETE_PARTIAL_REQUIRED, MANUAL_ACTION_REQUIRED; run_active id) | S3 `RecoveryAssessment.to_dict` (TRACED) | observation | `workspace.status --workspace <path>` | A | belongs to the selected workspace at observation time, never to a trace row | trust strip shows **Not observed** when no workspace is selected |
| 6 | Failure category / failure report | S1 `failure_category`; `failure_report` JSON list of {failure_type, category, attribution_tier} (TRACED) | durable | `history.detail` | A | category vocabulary mixes uppercase classes (VERIFICATION, RESOURCE, GENERATION_COMPLETENESS, EDIT_TARGETING, BUILD, INTERNAL, UNCLASSIFIED) and lowercase stop reasons (knowledge_gap, baseline_indeterminate) | show literal; absent = **Not recorded** |
| 7 | Generation metrics (calls, successful_calls, duration_seconds, wall_seconds, total_wall_seconds, files_requested, validated_files, validation_invalidations, operation_fallbacks, terminal_status, llm) | S1 `generation_metrics` from `state.generation_metrics()` (TRACED) | durable | `history.detail` | A | aggregate per run | **Not recorded** |
| 8 | Model hops | S1 `model_hops`: a list of model names appended on transitions (TRACED) | durable | `history.detail` | A | names only, no timestamps | **Not recorded** |

### 1.2 Hierarchy: phases, units, attempts

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 9 | Explicit phase boundaries (`phase.opened` / `phase.closed`) | S2 kinds declared in `model.KINDS`; **zero producers** (MEASURED) | not recorded | none | **C** | the banner names PLANNING / ARCHITECTURE / DEVELOPMENT & QUALITY GATES / REVIEW are log lines only | phase rail shows **Not recorded** for boundaries; see row 10 for the only honest substitute |
| 10 | Derived phase per record (`envelope.phase`) | S2 `scope.py`, three rules (TRACED): (i) inside an attempt scope every record gets `attempt` regardless of role; (ii) otherwise a role in `_ROLE_PHASES` maps (planner and planner_shadow→planning, architect→architect, reviewer→review, spec_compliance/run_verifier→requirement_verification, localization→localization, milestone_planner→milestone_planning); (iii) an unmapped role falls through as its raw role name. The explicit-phase context variable exists but nothing sets it | durable on every S2 record | none (CLI `evidence show`) | B | derived, not observed, **not a closed list** (rule iii); no KnowledgeGuard, no Commit, no ordering guarantee | rail groups records by this value, every group badged **DERIVED**; never implies boundaries |
| 11 | Phase sentinel events in the trace (planning.failed, review.refused, review.pre_approval_unattached, workspace_commit.failed, terminal_regression.passed, generation.completed, run.exception) | S1 `run_events` kinds (MEASURED from producers) | durable | `history.detail` | A | sparse: mostly failure-side; absence proves nothing | shown as recorded events; never used to mark a phase "completed" |
| 12 | Units (unit_id, unit_kind, invocation_seq; open/close) | S2 `unit.opened`/`unit.closed` payload {unit_id, unit_kind, invocation_seq} (TRACED) | durable | none | B | per run; pre-recorder runs have none | timeline degrades to attempt → event |
| 13 | Unit identity on trace gate outcomes (subtask_id, plan_id, milestone_id, planned_files, verification_target, authoritative_files) | S1 `gate_outcomes` via `Failure.to_gate_outcome` (TRACED, `ui/docs/GATE_OUTCOME_SHAPES.md`) | durable **on failed gates only**; success writers do not record them | `history.detail` | A (partial) | successful gates cannot be placed under a unit from S1 alone | gate shown under its attempt, unit **Not recorded** |
| 14 | Attempts (count, per-attempt events, per-attempt gates) | S1 `attempts`, `run_events[].attempt`, `gate_outcomes[].attempt` (TRACED) | durable | `history.detail` | A | attempt numbering is the writers' | n/a |
| 15 | Attempt lifecycle (attempt.opened/closed, attempt.concluded {succeeded}) | S2 (TRACED); S1 degraded source: `attempt.started` {mode}, `attempt.passed`, `attempt.failed` events (`attempt.py` ~7046; MEASURED kinds) | durable | S1 events via `history.detail`; S2 none | A (S1 events) / B (S2) | none | falls back to row 14 |
| 16 | Record order / chronology | S1 run_events preserve recorded order, `created_at` epoch; S2 seq + t_wall + t_mono_ms (TRACED) | durable | S1 via `history.detail`; S2 none | A / B | no cross-source clock guarantee; never interleave S1 and S2 by time, only by (run_id, attempt) | timeline sorts within one source only |

### 1.3 Model identity, qualification, authority, trust

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 17 | Model identity per role (role, model, runtime_digest, runtime_exact, inference_settings_digest) | S1 `run_events` kind `model.role_metrics` from `RoleRuntimeMetrics` (TRACED) | durable | `history.detail` | A | one record per role per run; absence = **Not recorded** | **Not recorded** |
| 18 | Model identity per call (role, call_seq, wire_seq; request metadata) | S2 `model.request` envelope + payload (TRACED) | durable | none | B | content governed by row 31 | row 17 only |
| 19a | Developer qualification **recorded at run start** (status, fingerprint_digest, missing, failed, reasons) | S1 `run_events` kind `model.runtime` {fingerprint, developer_qualification = `QualificationAssessment.to_dict()`} (`workflow.py` ~2761, ~555) (TRACED) | durable | `history.detail` | A | Developer role only; a run-start fact, never presented as the current state | **Not recorded** |
| 19b | **Current** qualification per model identity (QUALIFIED, STALE, …) | S3 qualification home (`KRIYA_QUALIFICATION_HOME`), record keyed by sha256({runtime, inference_settings digest}); `load_record(runtime_digest, settings)` takes a settings object, so the read op needs a digest-keyed helper (TRACED) | durable record, read as an observation | none in KUP; `kriya model …` CLI group exists | B | digests come from row 17; a missing record ≠ unqualified; distinct fact from 19a (C-3: neither is inferred from the other) | trust strip **Unavailable (no qualification read)** until the proposed op exists; then **No record for this identity** |
| 20 | Retry-time qualification refusal | S1 `run_events` kind `model.retry_identity_not_qualified` (MEASURED) | durable | `history.detail` | A | event only on refusal | n/a |
| 21 | SEC-009 approval state (approval artifact, set digest, integrity) | S3 `authority_approval.py` (build/verify artifact), surfaced by `kriya authority inspect` (TRACED) | durable artifact, read as an observation | none in KUP | B | observation of the configuration directory at request time, not of the run | **Unavailable (no authority read)** |
| 22 | Snapshot id, acquisition times, source metadata change flag | KUP envelope `consistency` block (TRACED) | per response | every history op | A | n/a | n/a |
| 23 | Consistency kind (snapshot_copy, live_observation, not_applicable) | KUP `policy.CONSISTENCY_KINDS` (TRACED) | per response | every op | A | closed vocabulary; S2 needs the proposed `sealed_store` kind | n/a |
| 24 | State-store path, trace database path, configuration directory | KUP envelope `source` + M1 host settings (TRACED, M1 trust strip) | per response | every op | A | n/a | n/a |
| 25 | Attempt-evidence capture mode and store status at run start | S1 `run_events` kind `evidence.attempt_store` {run_id, status ∈ PENDING/OPEN/DISABLED/RECORDER_UNAVAILABLE/CLOSED, capture, reason?, seq_at_row?, path?} (`workflow.py` ~2752, `scope.store_pointer`) (TRACED); also S2 manifest {schema, writer_version, redaction_policy_version, capture, run_id, + run manifest keys} | durable | `history.detail` (A); S2 manifest none (B) | **A** | DISABLED = capture off or no evidence configuration; RECORDER_UNAVAILABLE carries `reason` | event absent → **Recorded before the recorder** |
| 26 | Attempt-evidence seal / verification state (VERIFIED, UNSEALED, NOT_FOUND, CHAIN_BROKEN, TRUNCATED_TAIL, BLOB_CORRUPT, SEAL_MISMATCH; record_count, broken_seq, blob_problems) | S2 `reader.verify` (TRACED) | computed on read | CLI `kriya evidence verify` | B | full verify reads every record and every blob: cost grows with the store; cache by head_digest | **Unavailable** |
| 27 | Retention / pruning state | S2 retention: keep_runs 200, max_bytes 5 GiB; pruned store renamed then removed, **no tombstone** (TRACED). Disambiguation is **DERIVED** from row 25 + row 26: event absent ⇒ pre-recorder; status DISABLED ⇒ capture off; RECORDER_UNAVAILABLE ⇒ its `reason`; OPEN/CLOSED with `path` and S2 NOT_FOUND ⇒ pruned | derived | rows 25, 26 | **DERIVED (A + B)** | the derivation is only as good as the row-25 event; badge DERIVED | specific banner per case: **Pruned by retention** / **Capture was off** / **Recorder unavailable: ‹reason›** / **Recorded before the recorder** |
| 28 | Recorder gaps and degradation (recorder.gap {lost_kind, reason}; seal `gaps`, `degraded`, `closed_reason`) | S2 (TRACED) | durable | none | B | none | n/a |
| 29 | Base / run revision (tracked tree binding) | S1 gate outcomes `execution_evidence` fields (toolchain_identity, egress, resources, runtime_artifacts) when a result carries them (A, partial); S2 `run.opened` payload {kriya_build, workspace_identity, workspace_path, base_revision, base_tree_hash} (`scope.py` ~229–245) (TRACED) | durable | `history.detail`; S2 none | A (partial) / B | host-mode results carry none | **Not recorded** |

### 1.4 Context, prompts, model I/O

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 30 | Prompt sections (section, kept_chars, kept_digest, fitted, fitter, request_tokens, capacity_tokens, unlocated_sections, optional_sections_unchanged, segments) | S2 `prompt.sections` (TRACED, 3 producers) | durable | none | B | digests and sizes always; text only via blobs (row 31) | **Not recorded** |
| 31 | Model-facing request content (messages, tools, wire_body) and response content | S2 `model.request` / `model.response` blobs (content keys measured: messages, tools, wire_body) (TRACED) | durable in `full` / `full_with_reasoning`; digests only in `digest_only`; none when `off` | none (CLI `evidence show --content-ref`) | B | privacy-sensitive (repository content, possibly hidden-oracle text: `leak-check` exists for this); size unbounded; must be capped, lazy, local-only, visibility-gated | **Content not captured (digest only)** or **No attempt-evidence store** |
| 32 | Context composition summary (tiers, truncated_files, known_target_files, batches, unit_count) | S1 `run_events` kinds `context.request_fit`, `context.known_target_package`, `context.retry_target_source`, `context.edit_capability`, `context.search_evidence_grounding` (MEASURED kinds; keys TRACED) | durable | `history.detail` | A | summaries of paths/tiers/packages, not the rendered text | **Not recorded** |
| 33 | Omitted files and reasons (e.g. budget_exhausted; `omitted`, `omitted_regions`) | S1 event details where the producer recorded them (TRACED: budget_exhausted at several `attempt.py` sites) | durable where written | `history.detail` | A (partial) | no guarantee every omission is recorded | **Omissions not recorded for this request** |
| 34 | Prompt composition estimate vs provider total | S1 `developer.prompt_composition` (estimated per section; `prompt_tokens_reported`, provider_metadata, prefix_reuse) (TRACED) | durable | `history.detail` | A | estimates and provider totals are separate fields and must stay visually separate | **Not recorded** |
| 35 | Planning prompt text | S1 `prompt_rendered` (plan prompt only) | durable | `history.prompt` (explicit request only) | A | planning prompt, not Developer requests | **Not recorded** |
| 36 | Rules / guidance / skills injected | S1 `active_skills` (names), `retrieved_chunks` (see `ui/docs/RETRIEVED_CHUNKS_AND_FIELDS.md`); S2 `prompt.sections` section names (TRACED) | durable | `history.detail` (A); S2 none (B) | A / B | names and digests prove selection, not content | **Not recorded** |
| 37 | Developer tool-protocol execution (attempt, authority, mode, model_profile, proposals, targets, staged, tool_output, trigger_failure, retry_evidence, sections) | S2 `tool.execution` (TRACED) | durable | none | B | this is the Developer edit protocol, **not MCP** | **Not recorded** |
| 38 | Authority snapshot (authority transitions, operations) | S2 `authority.snapshot` via `record_authority_transition` (TRACED, 3 producers) | durable | none | B | none | **Not recorded** |

### 1.5 Candidates and changes

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 39 | Files modified (final) | S1 `files_modified` raw comma-joined string (TRACED) | durable | `history.list/detail` | A | raw string; a path containing a comma is ambiguous | n/a |
| 40 | Candidate file list per attempt (path, kind, proposal_kind, parse_reason_code, reason_code, decision STAGED/…) | S2 `candidate.change` payload (TRACED, 5 producers) | durable in every capture mode except off | none (CLI `evidence candidate` exports the last staged candidate) | B | none | **Candidate files not recorded** |
| 41 | Candidate content: before / after bytes and unified diff; before_digest / after_digest | S2 `candidate.change` blobs `before`, `after`, `diff` + digests (TRACED, `candidate_export.py`) | blobs only in `full` / `full_with_reasoning`; digests only in `digest_only`; absent when pruned / off / pre-recorder | none (CLI `evidence candidate --out <dir outside workspace>`) | B | **Changes Mode A** needs blobs; **Mode B** (paths, operations, digests) in every other case; never reconstructed from the working tree | **Candidate content was not retained.** |
| 42 | Commit evidence (transaction_id, state, operations, result revisions) | workspace-local `commit_evidence_dir(workspace)` via `edit_safety._persist_commit_evidence`; read by recovery (TRACED) | durable, workspace-scoped | none in KUP (consumed by `workspace.status` assessment indirectly) | B | belongs to a workspace, not a run row; observation | Git drawer shows S1 `workspace_commit.failed` / `terminal_regression.passed` events only |

### 1.6 Validation and gates

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 43 | Gate outcomes (attempt, type, success, output + writer-specific fields: attribution_tier/kind/confidence/reasoning, likely_files, file_locations, commands, steps, graded_by, deterministic_result, runtime_disposition, verifier_evidence, status/reason_code for goal_spec_compliance, recovered_by for test_selection) | S1 `gate_outcomes` (TRACED, inventory in `ui/docs/GATE_OUTCOME_SHAPES.md`) | durable | `history.detail` | A | result = `success` boolean only; `type` literals: compile, test, targeted_test, regression_test, goal_spec_compliance, test_selection, runtime/managed-service types, static analysis; heterogeneity shown verbatim | **Result not recorded** when `success` absent |
| 44 | Recorder gate results. Validator stage payload: stage `validator`, gate, duration_seconds, success, exit_code, timed_out, runtime_artifacts, error_type, bounded-output meta, `test_execution` summary {status_counts, completeness, runner}, `pytest_evidence` {complete, integrity_ok, integrity_reason}; blobs `output`, `test_execution` (full `TestExecutionReport.to_dict()` incl. `test_cases`), `pytest_<name>` raw streams. Terminal stage payload: stage `terminal`, gate ∈ terminal_obligations / original_requirements / artifact_registry, success, commit_eligible; blob `output` | S2 `gate.result` (TRACED: `tools/validate.py` ~349–385, `terminal_gate_service.py` ~270–279) | durable | none | B | blobs capped (04 §4.4) | row 43 only |
| 45 | Build / compile / test console output | S1 `gate_outcomes[].output` (A); S2 gate.result output blob (B) | durable | `history.detail` | A | text, size unbounded in S1 (subject to RESPONSE_TOO_LARGE 8 MiB per envelope) | **No output recorded** |
| 46 | Per-test results, pytest (every case incl. passes: identity, classname, name, status) | S2 `gate.result` blob `test_execution` = `TestExecutionReport.to_dict()` with `test_cases` (`tools/validate.py` ~363–366, `tools/test_execution.py` ~130–135); S2 `regression.decision` blobs `baseline_cases` / `post_cases` = `PytestSuiteEvidence.to_dict()` incl. the passed list (`pytest_stability.py` ~260–263); S1 event detail `pytest_evidence_status` (status only) (TRACED, review 002) | durable | S1 status via `history.detail`; S2 blobs none | **B** (A for the status) | blob-gated (04 §4.4 class `gate_output`); only when the gate ran under the recorder | **Structured per-test evidence not available for this run** |
| 47 | Per-test results, Maven Surefire (**failures and errors only**, never a pass list) | workspace control-store checkpoint `validation_baseline_targeted` / `validation_baseline_full_regression` = `ValidationBaseline.to_dict()` with `test_outcomes [{test_id, status, failure_fingerprint}]` (`workflow.py` ~3896–3903, `validation_baseline.py` ~439–458); S2 `gate.result` blob `test_execution` when `report_from_result` yields a report for the Maven runner (coverage UNKNOWN, §4.6) (TRACED, review 002) | durable (checkpoint subject to control retention) | none | **B** | aggregate counts + failure/error rows only; never "N more passed" | **Structured per-test evidence not available for this run** |
| 48 | Per-test results, Gradle | none; deferred P2 GRADLE-REGRESSION-DELTA-PER-TEST-001 (registry) | not recorded | none | **C** | console only | **Structured per-test evidence unavailable for Gradle** |
| 49 | Baseline / regression attribution (validation_baseline.* events: policy, full_regression_source/delta, targeted_delta, candidate_gate_delta, compile_regression_attributed, ambiguous_regression_replay, reuse_rejected) | S1 `run_events` (MEASURED kinds) | durable | `history.detail` | A | attribution values: evidence_incomplete, aggregate_delta, per_test_unattributable | **Not recorded** |
| 50 | Regression decision (available, reason) | S2 `regression.decision` (TRACED) | durable | none | B | none | row 49 |
| 51 | Static analysis result | S1 `static_analysis.result` event; gate outcome type (MEASURED) | durable | `history.detail` | A | n/a | **Not recorded** |
| 52 | Verification contract events (baseline, compiled, sealed, refused, dispositioned, authority_requested, no_mutation_required) | S1 `run_events` (MEASURED) | durable | `history.detail` | A | n/a | **Not recorded** |

### 1.7 Requirements, recovery, diagnosis

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 53 | Requirement lineage / derived / verdicts / closure (verdicts, closures, evidence_id, requirement_set_digest, findings, outcomes) | S1 `run_events` requirement.* (TRACED keys) | durable | `history.detail` | A | n/a | **No structured requirements recorded** |
| 54 | Evidence records (kind, source, attempt, payload, sensitivity, created_at) | S1 `evidence_records` from `EvidenceRecord.to_dict` (TRACED; carries no identifier) | durable | `history.detail` | A | `attribution.evidence_ids` have no namespace (M1 evidence checker: unresolved) | n/a |
| 55 | Retry / recovery decisions in the trace (retry.strategy_transition, progress_decision, progress_vector, verification_admission, sampling_resample, reserved_fallback, no_progress_terminal; model.fallback_routing/selection/incompatible; model.transition; repair_retry, repair_group_started, repair_contract_satisfied; api_contract_recovery.phase_advanced) | S1 `run_events` (MEASURED kinds) | durable | `history.detail` | A | n/a | **Not recorded** |
| 56 | Recorder recovery / fallback decisions (recovery.decision {environment_failure,…}; fallback.decision {phase: routing, reason_code, reasons,…}) | S2 (TRACED) | durable | none | B | n/a | row 55 |
| 57 | Diagnosis with evidence classes (payload: diagnosis, call, fit_developer_request; classes MEASURED / DERIVED_DETERMINISTIC / MODEL_CLAIMED / UNKNOWN) | S2 `diagnosis` via `record_diagnosis` (TRACED, 2 producers) | durable | none (CLI `evidence explain`) | B | the Why panel's per-statement class comes only from here | Why shows S1 failure_report + gate attribution only, and lists the diagnosis as **Not available (no attempt-evidence read)** |
| 58 | Retry delta (what changed between attempts: request_digest, sections, targets, scope, model/model_profile, temperature, blocking/blocking_reasons, ledger_revision/digest, unresolved_terminal) | S2 `retry.delta` (TRACED keys) | durable | none | B | n/a | Compare Attempts limits itself to S1 differences |
| 59 | Obligations snapshot | S2 `obligations.snapshot` (TRACED) | durable | none | B | n/a | **Not recorded** |
| 60 | Explain (nine fixed questions, each RECORDED / NOT_APPLICABLE / NOT_RECORDED) | S2 `explain.py` over the reader (TRACED) | computed on read | CLI `kriya evidence explain` | B | answers reference records by seq and content by blob ref | **Unavailable** |

### 1.8 Tokens, latency, capacity

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 61 | Prompt / completion tokens per role; estimated_token_calls; calls; protocol/schema failures | S1 `model.role_metrics` (TRACED fields) | durable | `history.detail` | A | aggregated per role per run; estimated vs provider-reported distinguished by `estimated_token_calls` | **Not recorded** |
| 62 | Provider-reported prompt tokens per Developer request | S1 `developer.prompt_composition.prompt_tokens_reported` (TRACED) | durable | `history.detail` | A | null when the provider did not report | **Provider did not report** |
| 63 | Latency per role (latency_seconds) | S1 `model.role_metrics` (TRACED) | durable | `history.detail` | A | aggregate | **Not recorded** |
| 64 | Context capacity and request size per call (capacity_tokens, request_tokens) | S2 `prompt.sections` (TRACED) | durable | none | B | n/a | percentage omitted |
| 65 | Served context (provider_context_window) | `llm.py` ~706–719 builds an admission-miss record with `served_context` into `result.protocol["provider_contract"]["admission_miss"]` and logs a warning; **no RunEvent carries it** (grep outside llm/model_runtime/provider_contract/context_certification hits only production_doctor and inference_runtime) (TRACED, review 002) | not persisted as evidence | none | **C** (UNKNOWN only if a trace of `result.protocol` consumers into an event is shown, §4.7) | n/a | percentage omitted |
| 66 | Context percentage | derived from rows 62/64 only (S1 provider-reported prompt tokens; S2 `prompt.sections` request_tokens / capacity_tokens) | n/a | n/a | derived | shown only when numerator and authoritative capacity exist for the same request, labelled with both sources | **Capacity not reported; percentage not computed** |
| 67 | Tokens per second | needs a per-call generation duration; `model_runtime.py` maps prompt_eval_count → prompt_tokens, eval_count → completion_tokens, prompt_eval_duration → prompt_eval_ms, load_duration → load_ms and **never reads eval_duration** (TRACED). The per-role aggregates latency_seconds / completion_tokens (row 61/63) are not a token rate and F-12 forbids displaying any rate | not recorded | none | **C** | n/a | never displayed |

### 1.9 Plan

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 68 | Plan validation / repair outcomes (structured_plan_validation, structured_plan_repair_requested/result, plan.file_list_entries_rejected) | S1 `run_events` (MEASURED kinds) | durable | `history.detail` | A | reason codes in `execution_plan.py` closed table (EMPTY_PLAN, INVALID_WORK_UNIT, DUPLICATE_WORK_UNIT_ID, MISSING_DEPENDENCY, DEPENDENCY_CYCLE, …) | **No plan validation recorded** |
| 69 | Plan units as executed (unit_id, unit_kind, invocation order) | S2 `unit.opened` (row 12) | durable | none | B | executed units only, not the declared plan | **Not recorded** |
| 70 | Declared plan (units, declared files, dependencies, immutable targets) | workspace control store: `RunRecord.execution_plan = ExecutionPlan.to_dict()` (`kriya/control/run_record.py` ~188–191, published by `plan_executor.py` ~198), under `<workspace>/.kriya/`, pruned by `control/retention.py` keep_terminal_runs (TRACED, review 002) | durable, workspace-scoped, subject to control retention | none (no KUP op in v1.1; a future `observe.run_record`) | B | needs a selected workspace and a surviving RunRecord | Plan tab shows rows 68/69 and states **Declared plan not available in recorded evidence** |
| 71 | Unit verdicts (unit.verified_no_change, unit.verified_no_change_refused, unit.no_change_proposed, unit.planned_files_delivered_unchanged) | S1 `run_events` (MEASURED) | durable | `history.detail` | A | n/a | n/a |

### 1.10 Workspace surface (observations)

| # | Datum | Source | Persistence | Read op today | Cat | Limits | UI when unavailable |
|---|---|---|---|---|---|---|---|
| 72 | Recovery assessment (status, run_active, records, evidence, unreadable_records, evidence_error) | S3 `workspace.status` (TRACED) | observation | `workspace.status` | A | per explicit workspace path | **No workspace selected** |
| 73 | Repository identity, branch, cleanliness | no read operation; not recorded in S1/S2 as a run fact | not recorded | none | **C** (future `live_observation` op) | n/a | section hidden |
| 74 | Toolchain identity | S1 gate outcomes `execution_evidence.toolchain_identity` when present (TRACED) | durable per gate | `history.detail` | A (partial) | per gate, not per workspace | **Not recorded** |
| 75 | Containment / approved roots / configuration | SEC-009 classification and TOOL-003 profiles are enforced at load; not exposed | not exposed | none | C | n/a | section hidden |
| 76 | Open in IDE | M1 host capability (P-R5) | n/a | `HostAdapter.openInIde` | A | path + optional line; validated in Electron main | hidden when no path |

### 1.11 Live and actions

| # | Datum | Cat | Note |
|---|---|---|---|
| 77 | Running state, current attempt, pending steps, elapsed timers, streaming timeline, live token rate, live context use | **D** | needs the future live transport topic; KUP `consistency.live_stream` is always false |
| 78 | New Run, Stop, Retry, Rerun validation, Resume, approve/deny authority | **E** | each needs a backend mechanism, authorization, transition contract and audit evidence (future actions topic) |
| 79 | Pause / resume | **E (no mechanism)** | no pause or cancel path exists in workflow.py, cli.py or kernel.py (MEASURED); never drawn |
| 80 | Interactive terminal, "ask Kriya" | **excluded** | forbidden by M1 P-31; not a capability gap |

### 1.12 Drawer streams and future destinations

| # | Datum | Cat | Source / note |
|---|---|---|---|
| 81 | Build stream | A | row 45 (compile gate outputs) |
| 82 | Tests stream | A | row 45 (test gate outputs), rows 46–48 for structure |
| 83 | Git stream | A (partial) | row 11 (S1 events); row 42 is B with no v1.1 operation |
| 84 | Raw events | A | S1 `run_events`; S2 records when exposed (B) |
| 85 | Model I/O | B | row 31, after the read contract, visibility-gated |
| 86 | LSP | **C** | no LSP evidence kind exists in S1 or S2 (MEASURED: no `lsp` kind) |
| 87 | MCP invocations | **C** | `kriya/policy/telemetry.py` ~174: "pure construction - no I/O, no logging, no persistence"; consumers are debug log lines only (`mcp.py`, `workflow.py`) (TRACED, review 002); log scraping is forbidden |
| 88 | Knowledge destination | B (partial) | rows 36; learned-knowledge store has no read op |
| 89 | Models destination | B | rows 17, 19, 61, 63 |
| 90 | Tools destination | C | rows 86, 87 |
| 91 | Experiments destination | C | cohort classifications (GENUINE, SAFE_FAILURE, FALSE_NEGATIVE, FALSE_SUCCESS) exist only in harness records outside `kriya/`; never a runtime verdict |
| 92 | Snapshot inventory for Settings (snapshot ids, acquisition times, bytes, retained count / limit, verify on request) | A | KUP `snapshot.list` (`live_observation`), `snapshot.verify`; limits from `capabilities.limits` |

---

## 2. What v1 can render (summary by surface)

| Surface | Renderable now (A) | After the read contract (B) | Never in v1 (C/D/E) |
|---|---|---|---|
| Runs pane | rows 1, 2, 4, 6 | row 3 linkage | "Active" chip |
| Run header | rows 1, 4, 7, 8, 14 | — | live phase |
| Trust strip | rows 5, 17, 19a, 22, 23, 24, 25 | rows 19b, 21, 26, 27 (derived), 28, 29 | — |
| Phase rail | row 11 sentinels | row 10 (DERIVED only) | row 9 explicit boundaries |
| Timeline | attempt → event (rows 14, 16) | unit → attempt → call/gate/decision (rows 12, 15, 18, 44, 56) | — |
| Overview | rows 4, 6, 7, 14, 39, 43, 53 | — | — |
| Context | rows 32, 33, 34, 35, 36, 62, 65 | rows 30, 31, 64, 66 | — |
| Plan | rows 68, 71 | rows 69, 70 (70 needs a future `observe.run_record`) | — |
| Changes | row 39 (Mode B minimal) | rows 40, 41 (Mode A / Mode B) | — |
| Validation | rows 43, 45, 49, 51, 52 | rows 44, 46, 47, 50 (per-test tables from S2 blobs, blob-gated) | row 48 Gradle |
| Attempts / Compare | rows 14, 43, 55, 61 | rows 15, 58 | — |
| Evidence | rows 43, 53, 54, 55 | rows 38, 57, 59, 60 | — |
| Why | rows 6, 43 (attribution), 55 | row 57 (evidence classes) | causal "first incorrect state" |
| Drawer | rows 81, 82, 84; Git = row 11 only | rows 85, 42 (no operation in v1.1) | rows 86, 87 |
| Settings | row 92, 24 | — | — |
| Workspace | rows 72, 74, 76 | — | rows 73, 75 |

## 3. Honest-degradation rules the UI must implement

1. **Store absent (NOT_FOUND):** every S2-backed element shows one shared banner whose text is derived per row 27 from the
   S1 `evidence.attempt_store` event (*Pruned by retention* / *Capture was off* / *Recorder unavailable: ‹reason›* /
   *Recorded before the recorder*), badged DERIVED; S1-backed elements are unaffected.
2. **Store unsealed (UNSEALED):** S2 elements render what is readable with the badge *Unsealed: run active or crashed*,
   consistency kind `live_observation`; nothing is cached.
3. **Chain broken / truncated / blob corrupt / seal mismatch:** S2 elements are not rendered; the trust strip shows the
   verification status literally and the Evidence tab shows the verification record.
4. **Capture digest_only:** Changes is Mode B; Context shows section digests and sizes; Model I/O shows digests.
5. **S1 and S2 disagree:** both are shown with their source; nothing is reconciled by choosing one.
6. **Any derived value** (row 10 phases, row 66 percentage) carries a DERIVED badge naming its inputs.

## 4. UNKNOWN items (trace before the surface is implemented; none blocks the shell)

| # | Item | Why it matters | Where to trace |
|---|---|---|---|
| 4.1 | resolved: `enforce_run_id` is not serialized by any KUP operation (row 3 is B) | row 3 | `kriya/kup/inspect.py` LIST_COLUMNS / DETAIL_TEXT_COLUMNS |
| 4.2 | resolved (review 002): `run.opened` carries base_revision and base_tree_hash | row 29 | `scope.py` ~229–245 |
| 4.3 | resolved (review 002): pytest cases in S2 `gate.result`/`regression.decision` blobs; Surefire failure/error rows in the control-store checkpoint | rows 46, 47 | `tools/validate.py`, `pytest_stability.py`, `workflow.py` ~3896 |
| 4.4 | resolved (review 002): `RunRecord.execution_plan` in the workspace control store | row 70 | `kriya/control/run_record.py` ~188 |
| 4.5 | resolved (review 002): not persisted | row 87 | `kriya/policy/telemetry.py` ~174 |
| 4.6 | whether `test_execution.report_from_result` yields a report for the Maven and Gradle runners (row 47 S2 path; row 48 stays C until shown) | rows 47, 48 | `kriya/tools/test_execution.py` ~490 |
| 4.7 | whether any consumer of `result.protocol["provider_contract"]["admission_miss"]` writes it into a RunEvent | row 65 | callers of `LLMClient` result `.protocol` |

Until traced, each open item is treated as the worse category and its surface renders the unavailable state. Items 4.1–4.5 were resolved during review 002 reconciliation (2026-10-10).
