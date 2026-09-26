# MODEL-EVIDENCE-HARDENING-001: qualification identity and model telemetry

**Status:** READY_FOR_PYTEST_VERIFICATION (2026-09-26). Not pushed. No live command was run by Claude. Packaged default models, the demo-03 fallback and PRD-019 routes are unchanged.

**Commits:**
- `6c163f2` MODEL-QUAL-IDENTITY-001 (defect record: `DEFECT_MODEL_QUAL_IDENTITY_001.md`);
- `596bfbe` the three telemetry defects (MODEL-EVAL-001 findings 2–4);
- `02ec465` handover, tracker and results updates;
- `ddd9258` runtime-scoped tokenizer floors;
- `95c1163` qualification environment identity, runtime portability seam and INF-001;
- plus a final commit: the conftest host pin, the tool-path fallback, the widened tripwire and these notes.

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

## Addition A: qualification environment identity (user spec, 2026-09-26)

**Split.** Functional qualification belongs to the runtime + inference settings. Environment-dependent evidence (`ENVIRONMENT_DEPENDENT_CAPABILITIES` = `context_capacity`: memory feasibility, near-window timeout) also belongs to the machine serving the runtime.

**Environment fingerprint** (`kriya/core/execution_environment.py`):
- **In the identity:**
  - OS and architecture;
  - inference runtime/version;
  - accelerator backend (metal/cuda/rocm/cpu), model and count;
  - accelerator memory class;
  - system memory class and unified memory;
  - runtime parallelism.
- **Memory is a class,** not a measurement: the nearest 8 GiB from 16 GiB up, a power of two below that. So a 62.7 GiB MemTotal and a 64 GiB report are both class 64, and an 80 GiB GPU is 80, not 64.
- **Never in the identity:** hostnames, serial numbers, MAC addresses, free memory, temperatures or load. They are not even read; a test checks which commands the probe runs.
- **Observable only for a loopback endpoint.** A LAN endpoint's hardware is not this machine's, so its environment is unavailable and capacity evidence is never reused for it.
- **Ollama does not report its server-side parallelism**, so `runtime_parallelism` is `unavailable` for it. The field stays for runtimes that do report it.
- **Disable the probe** with `KRIYA_EXECUTION_ENVIRONMENT_PROBE=0`.
- **Tools.** `sysctl` and `nvidia-smi` are found on PATH, else in `/usr/sbin`, `/usr/bin`, `/sbin` or `/bin`, so a minimal PATH (IDE, launchd) does not lose qualified tiers.
- **The mocked suite pins a fixed, exact fake host** (an autouse fixture in `tests/conftest.py`), so it never depends on the machine it runs on. The prd016 tier tests are green under `PATH=/usr/bin:/bin`.

**Records:**
- Capacity cases are stored in `environment_evidence[<environment digest>]`; functional cases stay in `cases`.
- `assess` counts capacity evidence only for the current, exact environment.
- `save_record` keeps other environments' evidence for the same identity. So qwen3.8@64K's FAIL on the 64 GiB M1 Max and a future PASS on a 128 GiB machine coexist, and neither is used on the other machine.
- `kriya model qualify` prints the environment, and `model status` reports it.

**No hardware special cases.** Callers compare digests only; no WorkflowEngine or routing code names hardware.

**Tests** (`tests/test_qual_environment_identity.py`):
- same environment → reused;
- memory class change → capacity not reused, functional still QUALIFIED;
- backend change → capacity stale;
- runtime version change → the whole record is stale;
- volatile and identifying values → identity unchanged;
- a stronger machine is freshly qualified for the failed tier, and neither environment's evidence leaks to the other;
- an unobservable environment → capacity never counted;
- PRD-016 tier offering follows the environment.

The environment binding is mutation-checked.

## Addition B: runtime portability invariant (user spec, 2026-09-26)

- **Provider-neutral fingerprint.** The runtime fingerprint already distinguishes:
  - provider and version;
  - exact artifact and weights digest;
  - context configuration;
  - chat template, tool-call parser and Kriya protocol;
  - adapter version.

  With the inference settings and the environment added in this batch, the same weights under Ollama and vLLM have different runtime digests, qualification identities and environments, and share no evidence (tested).
- **No new provider conditionals.** The batch had added its own `num_ctx` handling in three modules. Instead, the per-request context window is now one runtime-adapter contract in `model_runtime.py`:
  - `CONTEXT_WINDOW_REQUEST_OPTION`;
  - `configured_context_window` / `with_context_window` / `without_context_window`;
  - `supports_per_request_context_window`.

  Inference settings, LLMClient `_request_options`, `qualification_config` and PRD-016 tier offering (formerly `provider != "ollama"`) all go through it.
- **Tripwire:** a test fails if any non-comment code in `kriya/` outside `model_runtime.py` contains:
  - a provider comparison or membership (`==`, `!=`, `in`, `not in`; loop variables excluded);
  - an `"ollama"` literal;
  - a `"num_ctx"` literal;
  - an Ollama-native `/api/...` path.

  Anything else must be a named INF-001 inventory item. Today there is one: `kriya/memory/vector.py`'s `OllamaEmbeddingClient` native `/api/embeddings` fallback (embeddings, not model inference or qualification). The doctor's runtime check goes through the adapter's probe and the OpenAI-compatible `/models`. The tripwire is mutation-checked.
- **Deliberately not done:**
  - No vLLM adapter.
  - The Ollama probe (`/api/version`, `/api/tags`, `/api/show`) stays inside `model_runtime.py`.
  - The `context_capacity` case still sends the window via the adapter's request field.
  - The user-facing tier note still says "exact Ollama runtime", which is accurate while Ollama is the only adapter; a test asserts on it.
- **Follow-up recorded:** `INF-001` (Pluggable Inference Runtime Framework; tracker row, OPEN). It covers adapters for Ollama, vLLM and other OpenAI-compatible runtimes behind this seam, each owning its probe, per-request context control, capability reporting and environment/parallelism reporting. Generic orchestration, evidence, routing, retry and verification code must depend only on the abstraction.

**Disclosed consequences (fail-closed, intended):**
- A LAN (non-loopback) Ollama endpoint can no longer get a qualification-backed context tier: its environment is unobservable from this machine. Once any record exists for the tier runtime, declared tiers are blocked too. Adapter-reported environments belong to INF-001.
- Likewise for a platform with no detectable backend (e.g. Windows): capacity evidence never counts there.
- The resume `model_runtime` fingerprint binds the offered tiers, which now depend on the environment, so resuming a checkpoint on different hardware is refused.

**What `./setup.sh requalify` now records:** each `<arm>-32k.json` has an `environment` block (digest, os/arch, metal, "Apple M1 Max", 64 GiB class, `ollama/0.34.2`, exact) and an `environment_evidence` entry holding `context_capacity` under that digest.

**Focused command:** add `tests/test_qual_environment_identity.py` and `tests/test_bootstrap_contract.py` to the focused list above.

## Final closure: the four residual gaps (user review, 2026-09-26)

**1. Executed identity == qualified identity.**
- **Before:** the Developer's fallback calls sent the primary's temperature, and a bare `model_override` call sent the primary's `extra_body`.
- **Now:** `LLMClient._binding` returns each binding's own `temperature` and `extra_body`, and both completion paths use them whenever the caller does not override them. A fallback therefore executes with its own temperature, `reasoning_effort`/`think`, top_p, top_k, seed and every other `extra_body` field, never the primary's. No inheritance is implied.
- **Role identity follows the same rule:** a Developer fallback is identified by its own temperature.
- **Qualification setup** sets the binding's own temperature too.
- **Every `CompletionResult` carries `inference_settings_digest`,** the executed identity.
- **Tests:** through the real `_run_developer_generation` path (the prd017 harness):
  - the primary and the fallback send different temperatures and reasoning/sampling settings;
  - the executed digest equals `role_inference_settings`;
  - the fallback's qualification lookup is QUALIFIED;
  - changing the fallback's temperature or `reasoning_effort` makes it MISSING.

  Mutation-checked.
- **One deliberate per-call override remains:** `retry_temperature`. It is off in every shipped and demo config, and a retry sent with it is its own identity.

**2. Identity-keyed metrics.**
- Role-metrics rows are keyed by (role, model, runtime digest, **inference settings digest**) from the executed call. Attempts, schema failures and structured outcomes are charged to the identity of the call that produced them.
- Aggregation keeps identities apart, while identical identities aggregate normally.
- PRD-019's `metrics_row` reads only the candidate's own role identity. A row from before this change (no digest) matches no candidate: it is unmeasured, never misattributed.
- The table version is unchanged, so frozen route files stay valid. **Regenerate the routing table** (`kriya model metrics --write-table`) to measure under identities.
- The bundle extractor now aggregates per identity for future runs.

**3. Mid-planning exceptions.**
- `run_generation_workflow` is wrapped by `record_run_exceptions`. Any exception or cancellation escaping a run writes `<trace_id>.exception`: status `error`, failure category = the exception type, a `run.exception` event, and every call no earlier row reported. It then re-raises unchanged.
- A context-variable holder per call keeps nested enforce subtask runs apart.
- An enforce run that raises writes its `.enforce` row with status `error` before re-raising.
- No RunRecord, and never a success.
- **Tested:** an exception after the Planner call. The row holds the calls, counted once, with the reason.

**4. Planner outcomes everywhere.**
- **Milestone Planner responses** use the same closed taxonomy. Malformed list → malformed output. The milestone validator's plan-internal codes → structured validation failure. `UNJUSTIFIED_ENTRYPOINT`/`UNJUSTIFIED_BUILD_BOUNDARY` → policy rejection. The `EXTENSION_DEPENDENCY_NORMALIZED` warning is no outcome.
- **Shadow-mode planning** is classified too. Its calls and outcomes go to role `planner_shadow` (`BaseAgent.run(metrics_role=)`), which routing never reads, so shadow evidence cannot influence enforce routing. Before this, shadow calls were counted under `planner`.
- **Tests:** shadow validation failure, malformed and policy rejection stay distinct; milestone malformed, validation failures and repaired; one outcome per response.

**Kept as approved:**
- `/3` policy; `/2` records are STALE;
- per-role effective settings in the identity;
- max_tokens as metadata only;
- the `.enforce` row (approved) and the `.milestone-plan` row;
- typed UNKNOWN reasons;
- runtime-scoped tokenizer floors;
- the extractor tests;
- qwen3.8 64K: not re-run; the FAIL keeps 64K unavailable for this runtime and environment.

**Verification:**
- Claude: pylint 0, ruff 0, and 198 passed across the four new files plus prd017/prd018/prd019.
- User: the focused list plus `tests/test_model_evidence_hardening_final.py tests/test_qual_environment_identity.py tests/test_bootstrap_contract.py`, then the full suite, then `./setup.sh requalify`.
