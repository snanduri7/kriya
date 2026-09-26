# MODEL-EVIDENCE-HARDENING-001: qualification identity and model telemetry

**Status:** READY_FOR_PYTEST_VERIFICATION (2026-09-26). Not pushed. No live command was run by Claude. Packaged default models, the demo-03 fallback and PRD-019 routes are unchanged.

**Commits:**
- `6c163f2` MODEL-QUAL-IDENTITY-001 (defect record: `DEFECT_MODEL_QUAL_IDENTITY_001.md`);
- `596bfbe` the three telemetry defects (MODEL-EVAL-001 findings 2–4);
- plus this handover and the tracker/results updates.

**MODEL-EVAL-001 verdict is unchanged:**
- qwen3-coder:30b stays the default;
- qwen3.8:27b does not replace it;
- qwen3.8 vs qwen3.6 as Developer fallback is undecided;
- qwen3.8 @64K is NOT_QUALIFIED on this M1 Max / Ollama 0.34.2 runtime.

## 1. MODEL-QUAL-IDENTITY-001: qualification identity

**Identity.** qualification identity = runtime fingerprint digest + inference-settings digest (`kriya/core/inference_settings.py`).
- **In the settings:** temperature, the `reasoning` flag, and the whole `extra_body` except per-call `options.num_ctx`. That covers `reasoning_effort`, `think`, `top_p`, `top_k`, `min_p`, `repeat_penalty`, `seed` and anything else sent.
- **Normalization:**
  - keys sorted;
  - empty `options` = absent `options` = `extra_body: None`;
  - `1.0` = `1`.
- **`max_tokens`** is not identity: PRD-016 sizes it per call. The binding's configured ceiling is stored in the record as `inference_settings.output_ceiling` (metadata).
- **Provider and endpoint identity, exact artifact digest and `num_ctx`** were already in the runtime fingerprint.

**The runtime fingerprint is deliberately unchanged**, and so is `MODEL_PROTOCOL_ADAPTER_VERSION`. Changing either would change the runtime digest that other things are keyed on:
- role independence;
- role-metrics rows and the routing table;
- the runtime store;
- resume.

Two roles differing only in temperature would then count as "independent", and old records would read MISSING instead of STALE.

**Records:**
- `QUALIFICATION_POLICY_VERSION` is now `kriya-qualification/3` (schema 2), and records are stored as `<qualification identity>.json`.
- A policy-/2 record (`<runtime digest>.json`) is still found and reported **STALE**, with reasons "predates inference-settings identity" and "qualification policy changed". It is never MISSING, never reused and never migrated.

**Per-role settings, as each role's real call path sends them** (`role_inference_settings` / `binding_inference_settings`; a test drives the real Planner, Reviewer and Developer-fallback calls and compares):

| role / binding | temperature sent |
|---|---|
| Developer, primary **and every fallback** | `llm.temperature` (see finding A) |
| Reviewer on the primary binding | `llm.reviewer_temperature` when set, else `llm.temperature` |
| Any role with its own `agent_llms` binding or chain entry | that binding's `temperature` |
| Other roles on the primary binding | `llm.temperature` |

`retry_temperature` is a per-call Developer override, not a role setting. LLMClient dispatch resolves measured limits and adaptive tiers from the request's **actual** settings (`request_settings`). A retry sent at `retry_temperature` is therefore its own inference identity, usually unqualified: it gets no qualified tier and no `reasoning_tokens_max`. It is off by default; no shipped or demo config sets it.

**Tokenizer floors are runtime-scoped.** This was a follow-up fix, not a separate commit. `bytes_per_token_floor` and `non_ascii_bytes_per_token_floor` measure the tokenizer, not sampling.
- They are the most conservative value over every current (/3) record of the runtime, whatever its settings. A /2 record never contributes.
- So a prompt sized at allocation (under the Developer identity) is counted with the same ratio at dispatch under another identity: a retry at `retry_temperature`, or a Reviewer at `reviewer_temperature`.
- Without this, dispatch could fall back to the default ratio and refuse a prompt allocation had accepted (CONTEXT_BUDGET_UNSATISFIABLE).
- Tested and mutation-checked. `reasoning_tokens_max` and the context tiers stay identity-scoped.

**Consumers now pass the role's settings:**
- `doctor --production`;
- `kriya model status`, which also shows the settings;
- routing's `candidate_evidence`;
- the PRD-017 fallback profile;
- the `model.runtime` event;
- allocation windows;
- LLMClient dispatch.

A routing candidate qualified under one `reasoning_effort` is MISSING under another (tested).

**Qualification runs at the identity it records.**
- Before this fix, qualifying a fallback binding sent the **primary's** temperature. `qualification_config(settings=)` now sets the temperature, the reasoning flag and `extra_body`.
- `context_capacity` still probes the server at temperature 0.0, and its evidence says so.
- `kriya model qualify` qualifies each distinct role identity of the model in one call, and prints each one. With several identities, `--out` holds `{"records": [...]}`.

**Adaptive tiers:**
- A declared tier stands in for qualification only while the tier runtime has **no record under any settings or policy** (`runtime_has_records`).
- So the existing qwen3.8@64K FAIL record (policy /2, now STALE) still keeps 65536 out, and so does a record under other settings. Both cases are tested.
- `recorded_context_sizes` only discovers tiers qualified under the same settings.

**Resume.** The `model_runtime` resume fingerprint now also binds each role's settings digest per model, and the tiers per settings identity. **Every existing checkpoint's `model_runtime` fingerprint therefore changes**, so a resume across this upgrade is refused (fail-closed; intended).

## 2. Pre-planning failure telemetry

- **Enforce runs.** The controller writes its own traces row `<run_id>.enforce` at every enforce terminal (`WorkflowController._write_enforce_trace` → `kriya/workflow/run_trace.py::write_outcome_trace`). The row holds:
  - the real terminal status (never a success it did not have);
  - a `planning.failed` event (reason codes, repair attempts, invalid subtasks);
  - a `requirement.verdicts` event;
  - the role metrics no subtask row reported: the Planner calls, and the terminal verifier calls that previously leaked into the next run's row in a long-lived process.

  The row ID is suffixed, so it never overwrites a subtask's own row. No RunRecord or lifecycle state is created.
- **`kriya plan-milestones`** writes `<group>.milestone-plan` for accepted, rejected and malformed outcomes. That process's Planner calls were never reported before.
- **Direct path.** The knowledge-gap, planner-rejection and baseline-indeterminate rows now carry run events, including the metrics. Those were its only early exits without them.
- **Exactly once:** `take_unreported` (tested: a later run's row does not repeat the calls, and the routing table counts them once).

## 3. Structured plan outcomes

- **One typed outcome per Planner response**, initial and each repair, on both the enforce and the direct path, charged to the model that actually answered (escalation may use a chain model). The outcomes:
  - valid;
  - malformed output;
  - **structured plan validation failure**;
  - deterministic policy rejection;
  - other.
- **The classification** is a closed reason-code table (`planner_repair.py`). A tripwire test fails on any unmapped code from `plan_validation.py`, `planner_validation.py` or the enforce loop.
  - Precedence: malformed > validation failure > policy > other.
  - Validation failure = not a valid plan under Kriya's plan contract, decided from the plan alone: schema, ids, dependencies, ownership, requires/provides, invariants, verification evidence producers, requirement ids, tool names, repair regressions.
  - Policy = a structurally valid plan refused against the repository, route or goal contract: workspace files, grounded evidence, stack/runtime contract, extension point, refactor baseline.
- **Model faults** (malformed output and structured validation failures) also count as `schema_failures`, which is routing's failure rate. Policy rejections are recorded (`structured_policy_rejections`) but not counted as a model fault.
- **The discriminating check:** MODEL-EVAL-001 qwen3.6 run 3 (VERIFICATION_EVIDENCE_PATH_MISSING ×2, STRUCTURED_PLAN_SCHEMA_INVALID) now counts 3 Planner faults, with failure rate 1.0.
- **Compatibility:** the new counters are additive. Old rows read 0, and the metrics/routing table version is unchanged, so frozen routes still replay.
- The legacy path's `unauthorized_path` counts as a validation failure, because in enforce the same failure is STRUCTURED_PLAN_SCHEMA_INVALID.

## 4. Requirement verdict diagnostics

- **What each verdict records:** REQ id, outcome, a normalized `reason_code`, the evidence text, the verifier identity (model, runtime fingerprint, exact), and the judged candidate (`evidence_id` + revision).
- **The reason codes:**

  | outcome | reason code | meaning |
  |---|---|---|
  | SATISFIED | VERIFIER_CONFIRMED | |
  | VIOLATED | VERIFIER_REPORTED_MISSING | |
  | UNVERIFIED | INSUFFICIENT_CODE_EVIDENCE | genuine "cannot confirm from code" |
  | UNVERIFIED | MISSING_CLAIM_NOT_CONCRETE | |
  | UNVERIFIED | CLAIM_CONTRADICTS_STRONGER_AUTHORITY | |
  | UNKNOWN | MODEL_RETURNED_NO_VERDICT | |
  | UNKNOWN | MALFORMED_VERIFIER_RESULT | unparseable answer, not an object, verdicts not a list, or an unreadable entry for that id |
  | UNKNOWN | VERIFIER_CALL_FAILED | |
  | UNKNOWN | VERIFIER_REQUEST_REFUSED | PRD-016 refusal |
  | PENDING | NOT_YET_VERIFIED | |

- **`SpecComplianceAgent.check`** returns `failure_reason_code` on each of its three unknown exits, and `verifier` on every result. The `compliant: True` fail-open is unchanged.
- **Where the reasons survive:**
  - the obligation ledger;
  - the result's `requirements.verdicts` on both paths;
  - the `requirement.verdicts` trace events: the direct attempt's, and the enforce row's.
- **Test:** the qwen3.6 run-1 shape (a readable answer with no per-id verdicts) is recorded as UNKNOWN/MODEL_RETURNED_NO_VERDICT for every REQ, with the verifier identity, through to the trace row.
- **REQUIREMENT_AMBIGUOUS and REQUIRED_RUNTIME_EVIDENCE_MISSING are not produced.** The verifier protocol has no such verdict, and adding one would change the live prompt. INSUFFICIENT_CODE_EVIDENCE is the existing equivalent for "unverifiable".

## Extractor regression (MODEL-EVAL-001 bundle, outside the repo)

- `trace_window.py` holds the per-run row selection, and `extract-run.py` imports it.
- `test_trace_window.py` covers:
  - insert-then-replace rowid gaps, including the original count-baseline bug;
  - adjacent runs one second apart;
  - multi-row enforce runs;
  - boundary seconds;
  - unreadable timestamps.
- The window was tightened from `[start−1, end+1]` to `(start−1, end]`. The loose window fails the adjacency test.
- **The tightened window selects exactly the same rows for all 9 recorded runs** (checked read-only), so the recorded results are unaltered and were not re-extracted.
- Not part of Kriya's CI. Run it with `<repo>/.venv/bin/pytest -p no:cacheprovider test_trace_window.py` from the bundle directory (5 passed here).

## Findings disclosed (not fixed here)

- **A. Developer fallback temperature.** The Developer path never sends a fallback's own `FallbackModelConfig.temperature`; it sends `llm.temperature`. This is pre-existing. The identity records what is actually sent, and that is tested. Honouring the fallback's own value would change live behaviour, so it is a separate decision.
- **B. Role metrics are keyed by runtime digest, not inference identity.** Metrics from two settings of one runtime, for example qwen3.6 with and without `reasoning_effort`, aggregate into one routing-table row. Routing's qualification gate is identity-correct, but its ranking is not. This should be fixed before metrics-ranked routing is used for such a model.
- **C. An exception raised mid-planning still writes no row** on the direct path (for example a Planner backend error that propagates). Enforce's `_StructuredPlanUnavailable` path is covered.
- **D. Other responses without typed outcomes.** Shadow-mode Planner responses and milestone-Planner responses get no typed outcomes. Milestone planning does get its telemetry row.
- **F. The enforce row goes beyond defect 2's letter.** `<run_id>.enforce` is written on every enforce terminal, including success, not only on planning failure. The reason: the terminal verifier's calls ran after the last subtask row and were charged to the next run. The row carries the run's real status and is not a RunRecord. Veto it if you read "no fake SUCCESS merely to capture telemetry" against it; the planning-failure half stands on its own.
- **E. RunRecord is not extended.** It is the commit-lifecycle authority, with a closed annotatable field set. Verdict reasons are persisted in the ledger, the result and traces instead.

## Verification

Allowed checks run by Claude:
- `pylint` 0 and `ruff` 0 at each commit;
- small targeted runs of the new files: `test_model_qual_identity_001.py` 28 passed, `test_model_evidence_hardening_001.py` 46 passed, the changed prd014 record/assess tests 15 passed;
- mutation checks, each failing a test:
  - declared-tier guard;
  - Developer temperature rule;
  - seed normalization;
  - the enforce trace call;
  - the legacy run events;
  - model-fault counting;
  - the extractor window.

**Focused (user):**
```bash
.venv/bin/pytest tests/test_model_qual_identity_001.py tests/test_model_evidence_hardening_001.py \
  tests/test_prd013_model_runtime.py tests/test_prd014_model_qualification.py tests/test_prd016_adaptive_budget.py \
  tests/test_prd016_token_budget.py tests/test_prd016_allocation.py tests/test_prd017_fallback_transition.py \
  tests/test_prd018_role_metrics.py tests/test_prd019_model_routing.py tests/test_prd019_milestone_route_resume.py \
  tests/test_prd020_requirement_lineage.py tests/test_prd020_milestone_requirements.py tests/test_production_doctor.py \
  tests/test_workflow_controller_enforce.py tests/test_planner_robust_001_repair.py tests/test_milestones.py \
  tests/test_milestone3_4.py tests/test_agents.py tests/test_cli_smoke.py tests/test_strict_doubles.py
```
**Full (user):** `.venv/bin/pytest`

**Operational consequence of the policy bump.** Every existing qualification record is STALE. Any `runtime_profile: production` config therefore fails `doctor --production` `model.qualification` (and production fallback gating) until it is requalified, not only the MODEL-EVAL-001 arms.

The identities were computed offline (probe disabled, no model calls):

| config | identities to qualify |
|---|---|
| qwen3-coder arm | 1 |
| qwen3.6 arm | 1 |
| qwen3.8 arm | 1 |
| demo-03 `generate-production.yaml` | 2 |

- For demo-03, `requalify` covers qwen3-coder only if its runtime digest also matches the arm's. The settings digest does match (`ed7bfc09…`). Check with `kriya --config config/generate-production.yaml model status` from the demo workspace.
- demo-03's qwen3.6 fallback sends `{}`: no `reasoning_effort`, and no top_p/top_k. That is a different identity. It stays unqualified: it was NOT_QUALIFIED under /2 too, and per the spec it is not changed or requalified in this batch.

**Then live requalification (user):**
- `cd ~/kriya-live-demo/demo-03-brownfield/model-eval-001 && ./setup.sh requalify`
- This covers qwen3-coder, qwen3.6 (`reasoning_effort: none`) and qwen3.8, all at 32K only.
- `status-before` should read STALE for every role. **Do not** re-run `qualify-64k`.

**If green:**
- mark MODEL-QUAL-IDENTITY-001 and MODEL-EVIDENCE-HARDENING-001 VERIFIED;
- update the MODEL-EVAL-001 evidence;
- proceed to Batch 6 (PRD-025..029).

Changing the demo-03 fallback to `reasoning_effort: none` is a separate proposal, made after qwen3.6 requalifies under that exact setting.
