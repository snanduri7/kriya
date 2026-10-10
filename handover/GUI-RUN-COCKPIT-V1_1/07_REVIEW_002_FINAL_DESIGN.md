# 07 — Review 002: the single independent final design review, and its reconciliation

**Reviewer:** an independent agent (fresh context, did not author 01–06), 2026-10-10, read-only over Kriya-main-demo
`main` @ 1a89bc5. **Scope given:** the five questions of `01 §4` only. **Reconciled by:** Claude (author), same day.
**Result:** 0 BLOCKER · 6 MAJOR · 17 MINOR · 9 NOTE; recommendation READY_FOR_GATE_AFTER_FIXES. Every MAJOR was
re-verified by the author against the code before being applied (§2). After reconciliation: 29 fixed, 2 owner
decisions, 1 no change. There is no Review 003.

## 1. Disposition table

| Finding | Severity | Q | Author re-check | Disposition | Where changed |
|---|---|---|---|---|---|
| F-1 KUP "1.1" impossible: schema_version is the integer 1 everywhere | MAJOR | cons/2 | CONFIRMED (`policy.py:25`, click `type=int`, tests pin `[1]`) | FIXED: schema version stays 1; "capability set 1.1" advertised via `capabilities`; all argv `--kup-version 1` | 04 title, §2, §3, §4; 05 §2, slice 1A-1 |
| F-2 content visibility enforced by host argv, not Kriya | MAJOR | 2 | CONFIRMED (no `visibility`/`content_policy` field in config or authority) | FIXED: Kriya config field `evidence.attempt_recorder.content_visibility` (default off, SECURITY_AUTHORITY); host setting only narrows; no argv flag. OWNER_DECISION on the default → Q-2 | 04 §3, §8; 05 §2, 1A-3; 02 §14; 01 §6 |
| F-3 blob `content_class` table omits writer keys (`proposed`, `attempted_edits`, `goal`, `environment_failure`, `test_execution`, `pytest_<name>`, `baseline_cases`/`post_cases`, mirror keys) | MAJOR | 2 | CONFIRMED (grep of `content={…}` writers) | FIXED: full class table, gating per class, unclassified key fails closed, tripwire test | 04 §4.4; 05 1A-3 |
| F-4 capture mode and absence reason are in S1 (`evidence.attempt_store` event) | MAJOR | 1 | CONFIRMED (`workflow.py:2752`, `scope.store_pointer` statuses) | FIXED: row 25 → A; row 27 → DERIVED; specific absence banners; manifest `absence` block derived from the S1 event | 03 rows 25, 27, §3 rule 1; 04 §4.1; 02 §7 |
| F-5 run-start qualification is in S1 (`model.runtime` → `developer_qualification`) | MAJOR | 1 | CONFIRMED (`workflow.py:2761`, `:555`) | FIXED: row 19 split into 19a (A, recorded at run start) and 19b (B, observed now); digest-keyed lookup note | 03 row 19a/19b, §2; 02 §7; 04 §4.8 |
| F-6 per-test evidence is persisted (S2 `test_execution` blob with `test_cases`; `baseline_cases`/`post_cases`; checkpoint `test_outcomes`) | MAJOR | 1/3 | CONFIRMED (`validate.py:363–381`, `test_execution.py:133`, `pytest_stability.py:261–263`) | FIXED: rows 46, 47 → B; row 44 payload keys; Validation renders per-test tables from blobs; Gradle stays C; new trace item 4.6 for runner coverage | 03 rows 44, 46, 47, §2, §4.3, §4.6; 02 §9.5 |
| F-7 `run.opened` carries base_revision / base_tree_hash | MINOR | 1 | accepted (reviewer TRACED `scope.py:239–245`) | FIXED: row 29 A/B; §4.2 resolved | 03 row 29, §4.2 |
| F-8 declared `ExecutionPlan` persisted in `RunRecord.execution_plan` | MINOR | 1 | accepted (`run_record.py:188–191`) | FIXED: row 70 → B; Plan tab wording | 03 row 70, §4.4; 02 §9.3 |
| F-9 MCP invocations not persisted | MINOR | 1 | accepted (`telemetry.py:174`) | FIXED: row 87 → C; §4.5 resolved | 03 row 87, §4.5 |
| F-10 served context has no RunEvent | MINOR | 1 | accepted (`llm.py:706–719`) | FIXED: row 65 → C; row 66 inputs 62/64; trace item 4.7 | 03 rows 65, 66, §4.7 |
| F-11 status literals `milestone_plan_<status>`, `unknown` missing | MINOR | 1 | accepted (`milestones.py:845`, `workflow_controller.py`) | FIXED: row 4; Runs pane grouping of `.milestone-plan` / `.enforce` rows; label table | 03 row 4; 02 §6, §13 |
| F-12 derived-phase rule incomplete (planner_shadow, raw-role fallback, attempt scope) | MINOR | 1 | accepted (`scope.py:87–91, 120–126`) | FIXED: row 10 three rules, not a closed list; `phase_provenance` values | 03 row 10; 04 §4.3 |
| F-13 seal/manifest shape mismatch | MINOR | 1/cons | accepted (`writer.py:139–141, 286–291`) | FIXED: returned verbatim; real keys documented | 04 §4.1 |
| F-14 `sealed_store` defined two ways | MINOR | 2 | accepted | FIXED: single definition (verified in-invocation or by a named verify); manifest = live_observation | 03 header; 04 §4.1, §5 rule 0 |
| F-15 TRUNCATED_TAIL on a sealed store is corruption | MINOR | 2 | accepted (`reader.py:145`) | FIXED: rule 1 limited to unsealed; sealed joins rule 6 | 04 §5 |
| F-16 `BLOB_CORRUPT` used as a code but undefined | MINOR | 2/cons | accepted | FIXED: `EVIDENCE_VERIFY_FAILED` with `status: BLOB_CORRUPT`, `ref` | 04 §4.4, §6 |
| F-17 closed-vocabulary tripwires not listed | MINOR | 2/cons | accepted (`tests/test_kup_cli.py:82, 208`; `contract.test.ts:20`) | FIXED: three lock-step sites + two tripwires + parity test in slice 1A-1 | 05 1A-1; 04 §9 |
| F-18 `--verify` collision in `cockpit.run` | MINOR | cons | accepted (`cli.py:5061`) | FIXED: `--evidence-verify` | 04 §4.10 |
| F-19 flags used in 02 not defined in 04 (`role_metrics`, `live_observation`) | MINOR | cons | accepted | FIXED: Models gate = `qualification`; Workspace wording | 02 §3.1, §14 |
| F-20 per-attempt role metrics not in S1 | MINOR | 3 | accepted (`run_trace.py:45`, per-run aggregates) | FIXED: role/model from S2 only; S1 shows run-level; Compare omits role metrics | 02 §9.6 |
| F-21 Settings snapshot inventory has no matrix row | MINOR | 3 | accepted | FIXED: row 92 (A via `snapshot.list`); Settings in §2 | 03 row 92, §2; 02 §14 |
| F-22 Git stream over-claims row 42; no operation defined | MINOR | 3/cons | accepted | FIXED: Git = row 11 only; row 42 B with no v1.1 op; drawer wording | 03 §2; 02 §11.3 |
| F-23 Context/Changes selectors outside the selection model | MINOR | 4 | accepted | FIXED: `callSeq` added; selectors dispatch to the reducer; §16 row | 02 §5, §16 |
| F-24 "extend selection.ts" is a replacement of the M1 reducer | NOTE | 4/cons | accepted (`selection.ts:2–5, 50`) | OWNER_DECISION → Q-4; 02 §19.1 scoped to KUP contracts and host boundary | 02 §15, §19.1; 01 §6 |
| F-25 `phase_provenance` constant would be wrong after Phase 6 | NOTE | 5 | accepted | FIXED: reader-computed per record (explicit / derived_from_attempt_scope / derived_from_role / role_name_fallback) | 04 §4.3 |
| F-26 live/actions addable without restructuring; wiring note | NOTE | 5 | accepted | NO_CHANGE | — |
| F-27 non-existent test file; `evidence verify --json` exists | NOTE | cons | accepted (`tests/test_kup_cli.py:48`; `cli.py:1469`) | FIXED: real test named; D-3 wording | 05 §7, 1A-1; 04 §9 |
| F-28 gate pointers cite non-existent §2.x | NOTE | cons | accepted | FIXED | 01 §5 |
| F-29 row 67 wording stronger than evidence | NOTE | 1 | accepted | FIXED: wording | 03 row 67 |
| F-30 S1 attempt lifecycle events missing from the matrix | NOTE | 1 | accepted (`attempt.py:7046`) | FIXED: row 15 S1 degraded source | 03 row 15 |
| F-31 blob ref validation; `evidence prune` allowlist | NOTE | 2 | accepted | FIXED: regex before lookup; prune excluded | 04 §4.4, §8 |
| F-32 row 44 under-describes `gate.result` | NOTE | 1 | accepted (`validate.py:349–385`) | FIXED: keys listed | 03 row 44 |

Items the reviewer verified as matching the code with no finding are recorded in §3 of the review text below.

## 2. Author re-verification of the MAJOR findings (MEASURED, 2026-10-10)

```
kriya/kup/policy.py:25            KUP_SCHEMA_VERSION = 1
kriya/cli.py:4294                 --kup-version ... type=int
kriya/workflow/workflow.py:2752   kind="evidence.attempt_store"   (store_pointer keys: run_id, status, capture, reason, seq_at_row, path)
kriya/core/attempt_evidence/scope.py:33-37   PENDING / OPEN / DISABLED / RECORDER_UNAVAILABLE / CLOSED
kriya/workflow/workflow.py:2761   kind="model.runtime";  :555 {"fingerprint", "developer_qualification"}
kriya/tools/validate.py:363-381   payload["test_execution"], content["test_execution"], pytest_evidence, pytest_<name> blobs
kriya/tools/test_execution.py:133 data["test_cases"] = [...]
kriya/workflow/pytest_stability.py:261,263   content["baseline_cases"], content["post_cases"]
blob keys written in production (content={...} grep): after analysis_model_claimed attempted_edits before diff
    environment_failure goal messages output proposed tool_output tools wire_body  (+ dynamic pytest_<name>, mirror keys)
kriya/config/{config,authority}.py   no visibility / content_policy field
```

## 3. The review as delivered (preserved verbatim)

# Review 002 — findings

Reviewer: independent (did not author 01–06). Checkout: `/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo`, `main` @ 1a89bc5 (MEASURED `git log -1`; working tree: only the untracked package folder and the root PNG). Read-only: no file created, modified or deleted; no pytest/npm/model run.

### F-1: "KUP schema version 1.1" is impossible under the frozen M1 contract (integer const 1 everywhere)
- Severity: MAJOR
- Scope question: consistency (also 2)
- Location: 04 §2, §3, §4 (every argv `--kup-version 1.1`), 05 §2 slice 1A-1
- Evidence (TRACED): `kriya/kup/policy.py:25` `KUP_SCHEMA_VERSION = 1`; `ui/kup/schema/common.schema.json:222-224` `"schema_version": {"type":"integer","const":1}`; `ui/kup/schema/capabilities.schema.json:9` `kup_versions` items `integer`; `kriya/cli.py:4294` `--kup-version ... type=int`; `ui/kup/src/index.ts:11` `KUP_SCHEMA_VERSION = 1 as const`; `ui/shared/src/model/normalize.ts:18-19` refuses any other value with `UNSUPPORTED_SCHEMA_VERSION`; `tests/test_kup_cli.py:34,81` pin `schema_version == 1` and `kup_versions == [1]`.
- Claim vs code: 04 says "`KUP_SCHEMA_VERSION` advances to `1.1`" and "`capabilities.kup_versions` lists both". A non-integer cannot be parsed by click, cannot validate against the schema, and the M1 renderer would reject every v1.1 envelope.
- Proposed disposition: OBJECTIVE_FIX. Keep `schema_version` 1 (the extension is additive: new `operations`, `features`, error codes, consistency kind are already enumerable through `capabilities`); call the package "KUP v1, capability set 1.1" and use `--kup-version 1`; or move to integer 2 with the host accepting `[1, 2]`. Update 04 §2/§3/§4 and 05 1A-1 accordingly.

### F-2: Evidence-content visibility is enforced by the host's argv, not by Kriya (contradicts 04 §3 and M1 "Kriya is the sole authority")
- Severity: MAJOR
- Scope question: 2
- Location: 04 §3 ("Kriya enforces it too (the `--content` flag is refused), so a host cannot turn it on by itself"), 04 §8 ("Settings gain `evidenceContentVisible` ... which maps to the `--content-policy` the child enforces"), 05 §2 ("if a content-policy field is needed it is classified in `kriya/config/authority.py` before use")
- Evidence (MEASURED): `grep -n "visibility\|content_policy" kriya/config/config.py kriya/config/authority.py` → no match: no Kriya-side policy field exists. (TRACED) `kriya/cli.py:1395` already has `evidence show --content <ref>` meaning "print one content ref", so the flag name in 04 §3 collides with an existing option.
- Claim vs code: if the host passes `--content-policy on`, Kriya only executes the host's choice; nothing on the Kriya side can refuse. That is the host turning it on by itself, the opposite of what §3 states. Flag name is also inconsistent (`--content` vs `--content-policy`).
- Proposed disposition: OBJECTIVE_FIX + OWNER_DECISION. Fix: the policy is a Kriya config field (e.g. `evidence.attempt_recorder.content_visibility: off|on`, default off), classified SECURITY_AUTHORITY in `authority.py`; `capabilities.features.evidence_content` reports it; the host setting may only narrow (AND), never widen; one flag name. Owner question (extends Q-2): is the default `off` per Kriya config, with the host unable to override?

### F-3: The blob `content_class` closed list omits recorded blob keys, including repository and goal content
- Severity: MAJOR
- Scope question: 2 (privacy)
- Location: 04 §4.4 (`content_class` derived from `messages, tools, wire_body, before, after, diff, output, tool_output, analysis_model_claimed`); 03 row 31
- Evidence (MEASURED `grep -rhoE 'content=\{...' kriya`): additional blob keys written in production: `proposed` (`kriya/core/attempt_evidence/scope.py:921`, candidate file content), `attempted_edits` (`scope.py:924`), `goal` (`kriya/workflow/run_trace.py:137`), `environment_failure` (`kriya/workflow/recovery_coordinator.py:399`), `test_execution` and `pytest_<name>` raw streams (`kriya/tools/validate.py:363-381`), `baseline_cases`/`post_cases` (`kriya/workflow/pytest_stability.py:260-263`), and the mirror blobs `event`, `decision`, `evidence`, `gate_outcome`, `gate_outcomes_restored` (`scope.py:617-635`; the `evidence` mirror is an `EvidenceRecord` which carries a `sensitivity` field).
- Claim vs code: the contract's class table is incomplete; an unlisted key has no defined class, so the policy gate is undefined for candidate content (`proposed`), goal text and mirrored evidence.
- Proposed disposition: OBJECTIVE_FIX. Enumerate every blob key from the writers (the list above), assign each a class (`model_facing`, `candidate_content`, `gate_output`, `mirror`, `goal`), state which classes `evidence_content` gates (at least `model_facing`, `candidate_content`, `goal`, `mirror.evidence` with `sensitivity != public`), and fail closed: an unknown key → `EVIDENCE_CONTENT_POLICY` with `content_class: "unclassified"`. Add a tripwire test that the class table covers every key the writers emit.

### F-4: Capture mode and the "pruned / capture off / pre-recorder" distinction are recorded in S1 (rows 25 and 27 wrong)
- Severity: MAJOR
- Scope question: 1
- Location: 03 rows 25 (B), 27 ("**C** for was pruned"), §3 rule 1; 02 §7 (capture mode shown only with `evidence` flag); 04 §4.1 `absence_reason`
- Evidence (TRACED): `kriya/workflow/workflow.py:2749-2755` records S1 run event `evidence.attempt_store` with `details=attempt_evidence_scope.store_pointer()`; `kriya/core/attempt_evidence/scope.py:293-305` → `{run_id, status, capture, reason?, seq_at_row?, path?}` with `status` ∈ `PENDING | OPEN | DISABLED | RECORDER_UNAVAILABLE | CLOSED` (`scope.py:33-37`; `DISABLED` = "capture: off (operator) or no evidence configuration"). This event is in `run_events`, read by `history.detail`.
- Claim vs code: row 25 is **A** (capture mode per run, from S1), not B. Row 27: NOT_FOUND *can* be disambiguated — event absent ⇒ pre-recorder run; `DISABLED` ⇒ capture off; `RECORDER_UNAVAILABLE` ⇒ `reason`; `OPEN`/`CLOSED` with `path` and S2 NOT_FOUND ⇒ pruned. That is DERIVED from A + B, not C. F-7 makes these rows mandatory, so the error matters.
- Proposed disposition: OBJECTIVE_FIX. Row 25 → A (`history.detail`, event `evidence.attempt_store.details.capture`); row 27 → DERIVED (inputs: S1 event status/path + S2 verify NOT_FOUND), badge DERIVED; 03 §3 rule 1 and 04 §4.1 `absence_reason` carry the specific reason instead of the three-way "or"; 02 §7 shows capture mode without the `evidence` flag.

### F-5: Qualification state at run start is recorded in S1 (row 19 B is only half the picture)
- Severity: MAJOR
- Scope question: 1
- Location: 03 row 19; 02 §7 ("qualification state per role ... flag present"); 04 §4.8
- Evidence (TRACED): `kriya/workflow/workflow.py:2759-2764` records S1 event `model.runtime` with `details=await _primary_model_runtime_details(cfg)`; `workflow.py:541-555` returns `{"fingerprint": fingerprint.to_dict(), "developer_qualification": QualificationAssessment.to_dict()}` (`status`, `fingerprint_digest`, `missing`, `failed`, `reasons`; `kriya/core/model_qualification.py:541-546`).
- Claim vs code: the Developer role's qualification assessment *as of run start* is A via `history.detail`; the proposed `observe.qualification` is the *current* state (a different fact; C-3 forbids inferring one from the other). Also, `load_record(runtime_digest, settings: InferenceSettings)` (`model_qualification.py:527`) takes a settings object; the record key is `sha256({"runtime": d, "inference_settings": settings.digest})` (`kriya/core/inference_settings.py:247-252`), so 04 §4.8's `--settings-digest` needs a small digest-keyed helper, not a change of key.
- Proposed disposition: OBJECTIVE_FIX. Split row 19 into 19a "qualification recorded at run start (developer role)" = A (S1 `model.runtime`) and 19b "current qualification per identity" = B (04 §4.8); 02 §7 shows 19a always and 19b only with the flag, labelled "recorded at run start" vs "observed now"; 04 §4.8 names the digest-keyed lookup.

### F-6: Per-test evidence *is* persisted (rows 46/47 and UNKNOWN 4.3 resolved to B, not C)
- Severity: MAJOR
- Scope question: 1 (also 3: 02 §9.5 "today none")
- Location: 03 rows 46, 47, §4.3; 02 §9.5; 03 row 44
- Evidence (TRACED): (a) S2 `gate.result` content blob `test_execution` = `TestExecutionReport.to_dict()` with `test_cases: [{identity, classname, name, status}]` for every case (`kriya/tools/validate.py:363-366`; `kriya/tools/test_execution.py:130-135`), plus payload `test_execution` summary (`status_counts`, `completeness`, `runner`) and `pytest_evidence {complete, integrity_ok, integrity_reason}` with raw `pytest_<name>` blobs (`validate.py:367-381`). (b) S2 `regression.decision` content `baseline_cases`/`post_cases` = `PytestSuiteEvidence.to_dict()` including the `passed` list (`kriya/workflow/pytest_stability.py:260-263`; `kriya/workflow/validation_baseline.py:213-218`). (c) workspace control-store checkpoint `validation_baseline_targeted/full_regression` = `ValidationBaseline.to_dict()` with `test_outcomes [{test_id, status, failure_fingerprint}]` and `pytest_evidence` (`kriya/workflow/workflow.py:3896-3903`; `validation_baseline.py:439-458`).
- Claim vs code: rows 46/47 "persistence UNKNOWN → treated as C" and 02 §9.5 "today none" are wrong: pytest per-test rows (including passes) are B via S2 blobs; Surefire failure/error rows are B via (c) and, where `report_from_result` yields a report, via (a). Runner coverage of (a) for Maven/Gradle is not traced here (`test_execution.py:490` `report_from_result`), so Gradle stays C until traced.
- Proposed disposition: OBJECTIVE_FIX. Row 46 → B (S2 `gate.result` blob `test_execution`; S2 `regression.decision` blobs `baseline_cases`/`post_cases`); row 47 → B (checkpoint `test_outcomes`; gate.result when a report exists); row 44 lists the `test_execution`/`pytest_evidence` fields; §4.3 marked resolved; 02 §9.5 renders per-test tables from these blobs under Mode-A-style gating (blob op, cap) and keeps "never N more passed" only for Surefire-summary-derived rows.

### F-7: UNKNOWN 4.2 resolved — `run.opened` carries the base revision and tree hash
- Severity: MINOR
- Scope question: 1
- Location: 03 row 29, §4.2
- Evidence (TRACED): `kriya/core/attempt_evidence/scope.py:229` emits `run.opened` with `_run_manifest`, which returns `{kriya_build, workspace_identity, workspace_path, base_revision, base_tree_hash}` (`scope.py:239-245`).
- Claim vs code: "UNKNOWN whether authority.snapshot or run.opened carries a tree digest" → it is `run.opened.payload.base_tree_hash` / `base_revision` (B).
- Proposed disposition: OBJECTIVE_FIX: row 29 = A (partial, S1 `execution_evidence`) / B (S2 `run.opened` base_revision, base_tree_hash); §4.2 resolved.

### F-8: UNKNOWN 4.4 resolved — the declared `ExecutionPlan` is persisted in the workspace control store
- Severity: MINOR
- Scope question: 1
- Location: 03 row 70, §4.4; 02 §9.3
- Evidence (TRACED): `kriya/control/run_record.py:188-191` `RunRecord.execution_plan = ExecutionPlan.to_dict()` ("evidence only"); published by `kriya/workflow/plan_executor.py:198`; RunRecords live under `<workspace>/.kriya/` (`kriya/control/control_store.py:3`); pruned by `kriya/control/retention.py:71-98` (`keep_terminal_runs`).
- Claim vs code: row 70 is B (workspace-scoped S3 record, no read op, subject to control retention), not UNKNOWN→C.
- Proposed disposition: OBJECTIVE_FIX: row 70 → B with the source above; 02 §9.3 wording "available only when a workspace is selected and the RunRecord survives retention"; 04 may add `observe.run_record` later (not required for v1.1).

### F-9: UNKNOWN 4.5 resolved — MCP invocation decisions are not persisted (C confirmed)
- Severity: MINOR
- Scope question: 1
- Location: 03 row 87, §4.5
- Evidence (TRACED): `kriya/policy/telemetry.py:174-177` "Pure construction - no I/O, no logging, no persistence"; the only consumers are `logger.debug` lines (`kriya/mcp/mcp.py:805`, `kriya/workflow/workflow.py:2324`).
- Proposed disposition: OBJECTIVE_FIX: row 87 → C (debug log only; log scraping forbidden by 03's rule); §4.5 resolved.

### F-10: Row 65 (served context) claims an S1 event that does not exist
- Severity: MINOR
- Scope question: 1
- Location: 03 row 65 ("S1 event on the over-context path only (`llm.py` ~712 ...) ... `history.detail` A (partial)"), row 66
- Evidence (TRACED): `kriya/core/llm.py:706-719` builds `miss` (with `served_context`) and stores it in `result.protocol["provider_contract"]["admission_miss"]`, then `logger.warning`; (MEASURED) `grep -rn "served_context\|provider_context_window" kriya` outside `llm.py`/`model_runtime.py`/`provider_contract.py`/`context_certification.py` hits only `production_doctor.py` and `inference_runtime.py`; `context.request_fit` details are `fit_details`/`plan_fit`/`design_fit` (`workflow.py:4047, 4325`; `investigation.py:914`), no served-context key found.
- Claim vs code: no RunEvent carries the served context; row 65 is C unless a trace of `result.protocol` into an event is shown. Row 66's authoritative capacity then comes only from S2 `prompt.sections.capacity_tokens` (row 64).
- Proposed disposition: OBJECTIVE_FIX: row 65 → C (or UNKNOWN with the exact trace to run: `result.protocol` consumers); row 66 inputs = rows 62/64 only.

### F-11: Run status literals written in production are missing from row 4
- Severity: MINOR
- Scope question: 1
- Location: 03 row 4; 02 §6, §13
- Evidence (TRACED): `kriya/workflow/milestones.py:845` writes `status=f"milestone_plan_{status}"` for `<group>.milestone-plan` rows; `kriya/workflow/workflow_controller.py:4184,4294` writes `str(result.get("status") or "unknown")` for `<run>.enforce` rows; plus the seven literals the row lists (`workflow.py:466, 3082, 3186, 3236, 4376, 5013, 6799, 7201`; `cli.py:2714` `in_progress`).
- Claim vs code: row 4's set omits the `milestone_plan_*` family and `unknown`. 02 §6's generated chips cope, but §13's "Stopped" mapping and the Runs-pane grouping (plan rows sit beside milestone rows) do not mention them.
- Proposed disposition: OBJECTIVE_FIX: add `milestone_plan_<status>` and `unknown` to row 4; 02 §6 states how `.milestone-plan` and `.enforce` rows are grouped (same `milestone_group_id`/`enforce_run_id`).

### F-12: Derived-phase rule in row 10 and 04 §4.3 is incomplete
- Severity: MINOR
- Scope question: 1
- Location: 03 row 10; 04 §4.3 (`phase_provenance: "derived_from_role"` on every record); 02 §8, §13
- Evidence (TRACED): `kriya/core/attempt_evidence/scope.py:87-91` `_ROLE_PHASES` also maps `planner_shadow → planning`; `scope.py:126` falls back to the raw role name for an unmapped role (`_ROLE_PHASES.get(call.role, call.role)`); `scope.py:120-122` every record inside an attempt scope gets `"attempt"` regardless of role; `scope.py:97` explicit phase var exists but nothing sets it (MEASURED: no `_PHASE.set`, no `phase_scope`).
- Claim vs code: phase values are not a closed list of seven (unmapped roles leak through as phase names), and attempt-scope records are derived from scope, not from role.
- Proposed disposition: OBJECTIVE_FIX: row 10 states the three rules (role map incl. `planner_shadow`; raw role fallback; attempt scope); 04 §4.3 `phase_provenance` ∈ `derived_from_role | derived_from_attempt_scope | role_name_fallback`; 02 §13 keeps "unknown literal shown as-is".

### F-13: Seal shape in 04 §4.1 does not match the writer
- Severity: MINOR
- Scope question: 1 / consistency
- Location: 04 §4.1 `seal: { final_seq, head_digest, gaps[], degraded[], closed_reason }`; 03 row 28
- Evidence (TRACED): `kriya/core/attempt_evidence/writer.py:286-291` seal = `{schema, final_seq, head_digest, record_count, blob_count, complete, gaps: <int>, degraded: [..], closed_reason}`; manifest = `{schema, writer_version, redaction_policy_version, capture, run_id, **manifest}` (`writer.py:139-141`).
- Claim vs code: `gaps` is an integer count, not a list; `complete`, `record_count`, `blob_count` are omitted; manifest also carries `run_id` and the run manifest keys.
- Proposed disposition: OBJECTIVE_FIX: return the seal and manifest verbatim (F-3 "compose, never normalize" already requires it) and document the real keys.

### F-14: `sealed_store` is defined two different ways
- Severity: MINOR
- Scope question: 2
- Location: 03 header table ("proposed `sealed_store` (sealed + VERIFIED)") vs 04 §4.1 ("`sealed_store` when seal present, else `live_observation`") and 04 §8 (only `sealed_store` responses are cached)
- Evidence (TRACED): `kriya/core/attempt_evidence/reader.py:159` a seal can be present with `SEAL_MISMATCH`; `:131-139` `CHAIN_BROKEN` is independent of the seal; the manifest operation in 04 §4.1 does "a cheap line count, not a verify".
- Claim vs code: a manifest cannot know VERIFIED, so labelling it `sealed_store` (and caching it) asserts a trust state that was not established — the C-3 honesty rule applied to S2.
- Proposed disposition: OBJECTIVE_FIX: define `sealed_store` = "read from a store whose seal was verified (VERIFIED) for the stated `head_digest` in this invocation or by the `evidence.verify` the response names"; `evidence.manifest` returns `live_observation`; records/blob/candidate/compare return `sealed_store` only after the in-process seal+chain check they already perform per 04 §5 rule 6.

### F-15: TRUNCATED_TAIL on a sealed store is corruption, not "writer mid-append"
- Severity: MINOR
- Scope question: 2
- Location: 04 §5 rule 1 and rule 6
- Evidence (TRACED): `reader.py:145` returns `TRUNCATED_TAIL` with `sealed = seal is not None`, i.e. the status also arises on a sealed store.
- Claim vs code: rule 1 maps every partial tail to `live_observation`/`truncated_tail: true`; for a sealed store there is no writer, so records must not be served as live.
- Proposed disposition: OBJECTIVE_FIX: rule 1 applies only when `sealed == false`; when sealed, `TRUNCATED_TAIL` joins the rule-6 refusal set.

### F-16: `BLOB_CORRUPT` used as an error code but not defined in the error table
- Severity: MINOR
- Scope question: 2 / consistency
- Location: 04 §4.4 ("`BLOB_CORRUPT` otherwise") vs 04 §6
- Evidence (TRACED): `reader.py:37` `BLOB_CORRUPT` is a verification status; 04 §6 has no such code.
- Proposed disposition: OBJECTIVE_FIX: use `EVIDENCE_VERIFY_FAILED` with `status: BLOB_CORRUPT`, or add `EVIDENCE_BLOB_DIGEST_MISMATCH`.

### F-17: Closed-vocabulary tripwires that every new code/kind must satisfy are not listed in 05
- Severity: MINOR
- Scope question: 2 / consistency
- Location: 05 §2 slices, 04 §6, §9
- Evidence (TRACED): `tests/test_kup_cli.py:208` `assert produced >= set(policy.ERROR_CODES)` (every code must be produced inside that test); `tests/test_kup_cli.py:82` `set(operations) == set(policy.OPERATIONS)`; `ui/kup/test/contract.test.ts:20` requires `errors/<code>.json` for every `KUP_ERROR_CODES` entry; `ui/kup/src/index.ts:14-15` and `ui/kup/schema/common.schema.json:63,152,228` are hand-maintained enums; (MEASURED) no Python↔TS/schema parity test exists (`grep "common.schema.json\|index.ts" tests/*.py` → none).
- Claim vs code: no conflict with adding `sealed_store` or the new codes, but three places must be edited in lock-step and two existing tests must gain producers/fixtures; 05 does not say so.
- Proposed disposition: OBJECTIVE_FIX: 05 slice 1A-1 lists the three edit sites and the two tripwires; optionally add a parity test (policy ↔ schema enums).

### F-18: `--verify` flag collision in `cockpit.run`
- Severity: MINOR
- Scope question: consistency
- Location: 04 §4.10 ("`evidence_verify` is included only when the host passes `--verify`")
- Evidence (TRACED): `kriya/cli.py:5061` `traces --verify` already means "With --snapshots: also verify every snapshot's digest".
- Proposed disposition: OBJECTIVE_FIX: name it `--evidence-verify`.

### F-19: Capability flags used in 02 that 04 does not define
- Severity: MINOR
- Scope question: consistency
- Location: 02 §3.1 (Models gate "`qualification` + `role_metrics`"), 02 §14 ("hidden until a `live_observation` read exists") vs 04 §3 feature list
- Evidence (TRACED): 04 §3 features: `snapshot, prompt, comparisons, attribution, diagnostics, output, evidence, evidence_content, qualification, authority, knowledge, tools, experiments, live, actions`; `live_observation` is a consistency kind (`policy.py:71`), not a flag.
- Proposed disposition: OBJECTIVE_FIX: 02 §3.1 Models gate = `qualification` (role metrics are S1, always present); 02 §14 → "until a workspace-observation operation exists (`live` or a future `observe.workspace_identity` flag)".

### F-20: Attempts/Compare displays per-attempt role metrics that S1 records only per run
- Severity: MINOR
- Scope question: 3
- Location: 02 §9.6 ("role/model (S1 role_metrics or S2 call)"; Compare "S1 differences in gates and role metrics") vs 03 rows 61/63 ("aggregated per role per run")
- Evidence (TRACED): `kriya/workflow/workflow.py:2205-2210` one `model.role_metrics` event per trace write (`attempt=state.attempt_number` at write time, rows = `take_unreported()`); `kriya/workflow/run_trace.py:45` `attempt=0`; `kriya/core/role_metrics.py:99-101` aggregate counters.
- Claim vs code: per-attempt model/role and per-attempt metric deltas are not in S1; only S2 `model.request` (row 18) places a model on an attempt.
- Proposed disposition: OBJECTIVE_FIX: Attempts "role/model" from S2 only, S1 shows "run-level (not per attempt)"; Compare omits role metrics for S1.

### F-21: Settings "snapshot retention view" has no matrix row
- Severity: MINOR
- Scope question: 3
- Location: 02 §14 Settings; 03 §2 (no Settings row)
- Evidence (TRACED): KUP `snapshot.list`/`snapshot.prune` exist (`policy.py:30-33`, `live_observation`) but no row classifies snapshot ids, acquisition times, sizes, retained count.
- Proposed disposition: OBJECTIVE_FIX: add row "Snapshot inventory (ids, acquired_at, bytes, retained/limit)" = A via `snapshot.list`, consistency `live_observation`; Settings listed in 03 §2.

### F-22: Git stream lists a B row as renderable now, and 02 references an operation 04 does not define
- Severity: MINOR
- Scope question: 3 / consistency
- Location: 03 §2 "Git stream A (partial) rows 11, 42"; 02 §11.3 "commit evidence when exposed"; 03 row 42 (B, "needs op")
- Evidence (TRACED): `kriya/workflow/edit_safety.py:274-275` commit evidence is `<workspace>/<relative dir>`; 04 §4 defines no `observe.commit_evidence`.
- Proposed disposition: OBJECTIVE_FIX: 03 §2 Git = A row 11 only, B row 42 "no operation in v1.1"; 02 §11.3 drops "when exposed" or 04 adds the operation.

### F-23: Context and Changes attempt/call selectors are outside the selection model
- Severity: MINOR
- Scope question: 4
- Location: 02 §9.2 ("Scope selector: attempt → model call"), §9.4 ("Attempt selector"), §5 (no `callSeq`), §3.4, §16
- Evidence (TRACED): 02 §5 `Selection` has `attempt`, `eventRef`, `gateRef` but no model-call key; §9.2/§9.4 do not say whether their selectors dispatch to the reducer.
- Claim vs code: if they write `Selection.attempt`, a central-tab control resets `eventRef`/`gateRef` and moves the inspector node (a cross-surface side effect §16 does not list); if they hold local state, "selected attempt" has two owners, violating "all three read and write the single selection".
- Proposed disposition: OBJECTIVE_FIX: add `callSeq: number | null` to Selection; declare the Context/Changes selectors as views that dispatch `selectAttempt`/`selectCall` (selecting a call sets `eventRef` to that `model.request` record); add the row to §16.

### F-24: "Extend `selection.ts`" is in fact a replacement of the M1 reducer
- Severity: NOTE
- Scope question: 4 / consistency
- Location: 02 §5, §19.1; 05 §3
- Evidence (TRACED): `ui/shared/src/state/selection.ts:2-5` `InspectorTab = context|prompt|output|gates|evidence`, `DrawerTab = diff|why`; `:50` `selectComparison` auto-opens the drawer on `diff` (which 02 §16 forbids).
- Proposed disposition: OWNER_DECISION: keep the M1 Inspector panels on the old reducer behind the new shell, or migrate them in Phase 2 — 02 §19.1 "M1 behaviour unchanged" should be scoped to KUP contracts and the host boundary, not to `ui/shared` state.

### F-25: Phase 6 would make the constant `phase_provenance` wrong; a reader rule is needed (no shell change)
- Severity: NOTE
- Scope question: 5
- Location: 04 §4.3 (`phase_provenance: "derived_from_role"` on every record), 04 §7 ("no envelope field changes")
- Evidence (TRACED): `scope.py:97` explicit phase is reserved, unset; after Phase 6 `envelope.phase` holds explicit values with no per-record marker.
- Proposed disposition: OBJECTIVE_FIX (wording only): the reader derives `phase_provenance` per record (explicit when the record's `seq` lies inside a `phase.opened`…`phase.closed` range of the same chain, else derived). No shell element moves.

### F-26: Live and actions can be added without restructuring; one wiring note
- Severity: NOTE
- Scope question: 5
- Location: 02 §3.2, §4.3, §5, §16; 05 §1; 04 §1, §4.3
- Evidence (TRACED): flags `live`/`actions` reserved (04 §3); `eventRef.source` is an extensible union; drawer is defined as streams; toolbar right has room (02 §4.2 "no run actions" is copy, not structure); 04 §4.3 `--from-seq` + `live_observation` gives a tail without the `head_digest` cache. The only new host mechanism is a long-lived process (M1 D-2 `ui-server --stdio`), which replaces 04 §1's one-child-per-request transport for live only.
- Proposed disposition: NO_CHANGE.

### F-27: Non-existent test file named; `evidence verify --json` already exists
- Severity: NOTE
- Scope question: consistency
- Location: 05 §7 (`tests/test_kup_cli_byte_identical.py` "extended"); 04 §4.2, §9
- Evidence (MEASURED): `ls tests/test_kup_cli_byte_identical.py` → no such file; the D-3 test is `tests/test_kup_cli.py:48` `test_text_mode_is_byte_identical_to_the_baseline_golden`. (TRACED) `kriya/cli.py:1469` `evidence verify` already takes `--json`.
- Proposed disposition: OBJECTIVE_FIX: name the real test; state that `evidence verify --json` *without* `--kup-version` stays byte-identical (D-3: no KUP flag given).

### F-28: Gate evidence pointers cite non-existent sub-sections
- Severity: NOTE
- Scope question: consistency
- Location: 01 §5 G-10 ("02 §2.5"), G-12 ("02 §2.10"), G-14 ("02 §2.8")
- Evidence (TRACED): 02 §2 is a numbered list; there are no §2.x headings.
- Proposed disposition: OBJECTIVE_FIX: "02 §2 item 5/10/8".

### F-29: Row 67 "not derivable" is stronger than the evidence
- Severity: NOTE
- Scope question: 1
- Location: 03 row 67; F-12
- Evidence (MEASURED): `kriya/core/model_runtime.py:1274-1278` reads `prompt_eval_count`, `eval_count`, `prompt_eval_duration`, `load_duration`; `eval_duration` never read — claim correct. (TRACED) `role_metrics.py:99-101` has per-role `latency_seconds` and `completion_tokens`, so a crude aggregate ratio is computable.
- Proposed disposition: OBJECTIVE_FIX (wording): "no per-call generation duration is recorded; the aggregate per-role ratio is not a token rate and F-12 forbids displaying any rate".

### F-30: S1 attempt lifecycle events exist and are not in the matrix
- Severity: NOTE
- Scope question: 1
- Location: 03 rows 14/15
- Evidence (TRACED): `kriya/workflow/attempt.py:7046-7051` `attempt.started` with `details.mode`; `attempt.passed`/`attempt.failed` kinds present (MEASURED kind inventory).
- Proposed disposition: OBJECTIVE_FIX: row 15 "S1 degraded source: `attempt.started/passed/failed` events".

### F-31: `evidence.blob` lacks syntactic ref validation before path construction
- Severity: NOTE
- Scope question: 2
- Location: 04 §4.4
- Evidence (TRACED): refs are `sha256:<hex>` (`kriya/core/attempt_evidence/model.py:60-61`); blobs are addressed by path `blobs/<xx>/<sha256>.gz` (03 S2 row).
- Proposed disposition: OBJECTIVE_FIX: validate `^sha256:[0-9a-f]{64}$` → `INVALID_REQUEST` before any lookup; also state that `evidence prune` (`cli.py:1553`) is excluded from the host argv allowlist.

### F-32: Row 44 under-describes the `gate.result` payload
- Severity: NOTE
- Scope question: 1
- Location: 03 row 44
- Evidence (TRACED): validator stage payload = `stage:"validator", gate, duration_seconds, success, exit_code, timed_out, runtime_artifacts, error_type` + bounded-output meta + `test_execution` summary + `pytest_evidence` (`kriya/tools/validate.py:349-385`); terminal stage = `stage:"terminal", gate, success, commit_eligible` (`kriya/workflow/terminal_gate_service.py:270-279`).
- Proposed disposition: OBJECTIVE_FIX: list the keys (feeds F-6).

Verified as matching the code (no finding): `model.KINDS`/`ENVELOPE_KEYS`/provenances/evidence classes (`model.py:27-69`); every kind has ≥1 producer except `phase.opened`/`phase.closed` = 0 (MEASURED per-kind grep excluding model/explain/reader/candidate_export/leak_check); `reader.verify` statuses (`reader.py:33-39,120-161`), dot-prefixed stores invisible (`reader.py:169`), no lock probing (no `lock`/`fcntl` in reader); retention keep 200 / 5 GiB, rename-then-rmtree, no tombstone (`retention.py`, `config.py:1513-1518`); capture default `full` (`config.py:1533`); `runs` columns (`trace.py:32-82`); `LIST_COLUMNS`/`DETAIL_TEXT_COLUMNS` exclude `enforce_run_id` (`inspect.py:24-27`) and the five `not_persisted` sections (`inspect.py:230-236`); `policy.OPERATIONS`/`ERROR_CODES`/`CONSISTENCY_KINDS`/`MAX_RESPONSE_BYTES` 8 MiB; `Failure.to_gate_outcome` keys = `GATE_OUTCOME_SHAPES.md` (`failure.py:280-313`); the seven listed status literals; `explain.QUESTIONS` = 9; `authority inspect` exists (`cli.py:4054`), `_redact_env_values` exists; all S1 event kinds named in rows 11, 17, 20, 32, 49, 51-53, 55, 68, 71 exist in the kind inventory.

# Summary

- BLOCKER 0 · MAJOR 6 (F-1…F-6) · MINOR 17 (F-7…F-23) · NOTE 9 (F-24…F-32).

1. Matrix matches persisted evidence? **PARTIAL** — the S1/S2 inventory is largely right, but rows 25, 27, 19, 46/47, 65 are mis-categorised and three of the five UNKNOWNs resolve from code (4.2, 4.4, 4.5), one more (4.3) resolves to B.
2. Read contracts preserve provenance, privacy and authority? **PARTIAL** — compose-not-normalize, argv-only, no path parameters, no new write path and typed refusals are sound; the content-visibility policy is host-driven (F-2), the blob class table is incomplete (F-3), and the version/consistency-kind labels need the fixes in F-1, F-14, F-15.
3. Any UI surface claiming C/D/E or uncovered facts? **PARTIAL** — nothing C/D/E is rendered; three uncovered/over-claimed items (F-20, F-21, F-22) and one under-claim (F-6).
4. Navigation simple and non-duplicative? **YES** with one concrete gap (F-23: call/attempt selectors outside the reducer) and one scoping note (F-24).
5. Live/actions addable without restructuring the shell? **YES** (F-25, F-26 are wording/reader rules, no shell element moves).

Overall recommendation: **READY_FOR_GATE_AFTER_FIXES** — all MAJORs are objective corrections except the owner question folded into F-2 (where the content-visibility authority lives); none reopens identity, aesthetics or navigation.

## 4. Post-reconciliation status

All OBJECTIVE_FIX items applied in 02, 03, 04, 05 and 01 on 2026-10-10 (see the "Where changed" column). The two
OWNER_DECISION items are Q-2 (content-visibility default and authority) and Q-4 (M1 panel migration) in `01 §6`. The
five scope questions after fixes: 1 YES (two trace items 4.6, 4.7 remain open and are scheduled in 05 §8), 2 YES,
3 YES, 4 YES, 5 YES. Author recommendation to the owner: PASS, conditional on Q-1 … Q-4 being answered before the first
code slice.
