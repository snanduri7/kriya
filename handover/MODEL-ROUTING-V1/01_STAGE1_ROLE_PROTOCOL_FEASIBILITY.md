# MODEL ROUTING V1 - Stage 1: role/protocol feasibility and axis closure (2026-10-13)

Labels: MEASURED / TRACED / INFERRED / UNKNOWN (ENGINEERING_RULES s2). Product code unchanged (`git diff d0d5423 HEAD -- kriya` is empty; only `handover/MODEL-ROUTING-V1/` changes). No repository task, no cohort, no quality comparison was run. Model time used: 3 qualification runs of existing mechanism for protocol eligibility (+2 short re-qualifications) and 6 single-request replay-mechanics probes. Evidence outside the repo: `~/kriya-m1-live/model-routing-v1/` (configs, qualification logs/records, replay outputs). V2 stores were opened read-only (records.jsonl sha256 unchanged after every probe).

## 0. Git
`origin/main` fast-forwarded `c98b18d -> d0d5423` (ancestor check passed; no tag). `model-routing/v1` pushed (tracking `origin/model-routing/v1`); it is the sole active workstream branch; `backend/readiness-v2` (== main) is a CLOSED workstream.

## Corrections to Stage 0 (found while tracing; Stage 0 doc left unchanged as the record)
1. **Profile-rendered Developer prompts: NOT supported by the data.** All 42 Developer requests of the 10 recorded closing-cohort stores carry `tools=0` and the same sentinel text protocol (`<<<KRIYA:EDIT>>>`, parse protocol `structured`) for qwen3.8 AND qwen3.6; the system prompts have identical protocol text (MEASURED). `developer_investigation_enabled` defaults false (TRACED, `config.py:755`), so native tools are not used by the baseline Developer. Capability profiles still differ per binding (below) but did not change the recorded request semantics. Stage 0 section 5's caveat is withdrawn for the Developer main call.
2. **Which qualification records the V2 runs saw:** `env.sh` points `KRIYA_QUALIFICATION_HOME` at the read-only 3-record view `~/kriya-m1-live/qual-view-postupgrade-0400`, not `~/.kriya/qualifications` (22 records, mostly pre-/8). Stage 0 counted the latter. This campaign uses its own home `~/kriya-m1-live/model-routing-v1/qual-home` (the 3 view records + the /8 records of `~/.kriya`); a DeepSeek record accidentally written into the view during the first probe was moved out (the view again holds its original 3).
3. Stage 0 said "FIX ANALYSIS may steer retries (INFERRED)". Now traced - section 2.

## 1. Corrected model-controlled axes (TRACED + MEASURED)
Baseline config = frozen V2 operator config (`~/kriya-m1-live/backend-readiness-001/tasks/*/config.yaml`, native Ollama adapter, `runtime_profile: production`, `num_ctx` 32768, temperature 0.7, top_p 0.8, top_k 20; run budget 3600 s with 120 s reserved; retry limits 4/3/3).

| Axis | Production call site | Config source | Baseline model (Ollama ID) | Capability profile (source) | Input contract | Output contract | Receives previous-model state? | Routable without code |
|---|---|---|---|---|---|---|---|---|
| PLANNER | `PlannerAgent` via `BaseAgent.run` (plan + plan-repair rounds) | `agent_llms.planner.llm` | qwen3.6-35B-A3B `...-kriya-620d4d5ce36a` (0314ad2492d1), `reasoning_effort: none`, max_output 8192 | no tools, json_mode, text_markers (qualification-derived) | system + user, `json_object`, no tools; goal + Kriya-derived repo/graph facts | structured plan JSON (`parse_planner_structured_output`) | no (goal + deterministic state only) | YES |
| DEVELOPER_PRIMARY_BUNDLE | CI-6 `localization_decision.decide` (`workflow.py` per unit, after the Planner, before that unit's Developer; schema-constrained, <=1 call, only when deterministic localization is ambiguous) + Developer attempt 1 (`attempt._run_developer_generation`) | top-level `llm` (CI-6 uses `self.llm` = the primary; there is no `agent_llms` entry for it) | qwen3.8:27b dense (22130167c4c2), `repeat_penalty 1.05`, max_tokens 16384 | explicit_primary: native tools, json_mode, small_native_tools (operator-declared) | system + user text, `tools=0`; Kriya-derived context | sentinel protocol (`parse_structured`: EDIT/FILE/NO_CHANGE); CI-6: JSON schema of candidate ids | CI-6 result enters state as ADVISORY event and reorders candidates (`retrieval.adopt_decision`) | **bundled: CI-6 + attempt 1 are ONE route axis** |
| DEVELOPER_RETRY | same choke point, retry_count > 0 | `llm_chain` via `resolve_fallback_model` (`chain[min(retry-1, len-1)]`) | qwen3.6 `...-kriya-620d4d5ce36a`, no explicit capabilities (derived: text_markers) | qualification-derived | same sentinel request rebuilt from Kriya state + "Previous Error to Fix" | same | typed failure + (rarely) typed `likely_files`, section 2 | YES |
| run_verifier / spec_compliance / reviewer | `RunVerifierAgent`, `SpecComplianceAgent`, `ReviewerAgent` | `agent_llms.<role>.llm` | qwen3-coder:30b `...-kriya-e52213655394` (141e95d786b8) | explicit: native tools, json_mode | JSON | JSON opinion, never verification evidence | no | YES; **not a campaign axis** (46 calls in 10 runs, no attribution to outcomes; decision classes belong to deterministic gates) |
| architect, skill_gap, MilestonePlanner, planner_shadow | - | - | - | - | - | - | - | 0 calls in the cohort; excluded |

There is no Repair role (confirmed). `localization` is metrics-labelled separately from `developer` in the recorder (envelope `role`), so its success is separable even though its route is bundled; later metrics must report LOCALIZATION CORRECT and IMPLEMENTATION CORRECT GIVEN LOCALIZATION.

## 2. FIX ANALYSIS / retry state trace (blocker question) - answered
MEASURED on all 42 recorded Developer requests (10 stores) and TRACED in `agent.py`, `attempt.py`, `retry_strategy.py`, `attribution.py`:
1. Is FIX ANALYSIS generated by the previous model? It is part of each Developer response in the sentinel protocol ("1-3 sentences of analysis" before the outcome). YES, MODEL_CLAIMED.
2. Present in the evidence store? YES: the response blob (`model.response`) and, when used for targeting, a `diagnosis` record with `attribution_tier: self_diagnosis`, `evidence_class: MODEL_CLAIMED`, `authority: advisory` (3 such records in the cohort; the others are `judge`/`locator`/`full_set`, DERIVED_DETERMINISTIC).
3. Passed verbatim to the retry? **NO.** The retry request is exactly two messages (system, user) with no conversation history; the "Previous Error to Fix" section holds the failure type and authoritative validator evidence (DERIVED_DETERMINISTIC). A 5-gram overlap test of every attempt's prompt against the previous attempt's response found only repository source/boilerplate text, and 0 overlap at the two model-switch boundaries (attempts 4 and 6 of S4_B); a string-level control (prompt 3 vs prompt 4) overlapped 151/2182, so the test is not vacuous. The text "Its own analysis instead names: ..." (`attempt.py:8494`) occurs in 0 of 42 prompts.
4. Provenanced? The only channel is `state.last_self_diagnosis = (failure_signature, files, attempt)`: file NAMES extracted deterministically by `extract_self_diagnosed_files` from the previous response, restricted to known files, gated to the same failure signature and attempt, further restricted to the write scope (`retry_strategy.py:958-978`). It is tagged MODEL_CLAIMED/advisory in the store and classified JUDGMENT, not DETERMINISTIC, for scope-conflict decisions (`retry_strategy.py:1190ff`). It steers retry TARGET files (`likely_files`), which changes prompt sections - not free text.
5. Hidden reasoning dependence? NO: wire bodies send `think: false`; reasoning text is never stored or forwarded (`completion.py`: presence/size only); no conversation state exists.
6. Can an isolated retry replay hold the preceding model-generated state constant? YES: the recorded request already contains everything derived from it, including the effect of any `self_diagnosis` targeting.
**Decision: RETRY ROLE-ISOLATED REPLAY IS VALID.** Every candidate retry model receives the exact recorded request (the same MODEL_CLAIMED-derived targeting); FIX ANALYSIS is not regenerated. Disclosure for end-to-end comparison: a different attempt-1 model changes the `likely_files` channel and the failed candidate, so end-to-end retry results are interaction effects (rule 13). Sample caveat: the 42 requests come from 10 runs; the paths not exercised there (advisory-only "no edit" responses whose `evidence` string carries `FIX ANALYSIS` text into a Failure `raw_output`, `attempt.py:8514`) are TRACED but unmeasured - a possible verbatim channel; fresh-bank retry replays must check each retry prompt for it (the 5-gram test is `tools/retry_state_trace.py`).

## 3. Capability-profile groups (TRACED from `kriya model status` logs and probe configs)
| Model (ID) | Role position | Profile | Source |
|---|---|---|---|
| qwen3.8:27b (22130167c4c2) | Developer primary | native tools, json_mode, small_native_tools | explicit_primary (operator-declared) |
| qwen3-coder:30b kriya (141e95d786b8) | verifier roles; as primary; as chain | native tools, json_mode, small_native_tools | explicit_agent_llms / explicit_primary / explicit_llm_chain |
| qwen3.6 kriya (0314ad2492d1) | Planner, Developer chain | no tools, json_mode, multiline JSON, text_markers | qualification_derived (no declaration) |
| qwen3.6 as PRIMARY (probe config) | - | native tools, json_mode (**inherited from default `llm.capabilities`, not derived**) | explicit_primary - **an unintended profile; production would have to declare capabilities for it (operator decision); probe result is for this declared profile only** |
| deepseek-r1:32b | Planner (reasoning binding) | no tools, json_mode False/unverified | unverified_conservative_default |
Finding: a profile is a property of the binding position and operator declaration, not of the model. The recorded baseline Developer/Planner/retry requests do not differ in protocol text or tools across profiles (section "Corrections" 1).
- **TYPE A (same-profile exact replay):** all axes whose requests carry no tools and the same text protocol - Planner (JSON, no tools), Developer sentinel (qwen3.8 / qwen3.6 / coder), retry. Messages are byte-identical across models; the only wire differences are the model name and that model's own binding sampling (MEASURED: qwen3.8 adds `repeat_penalty 1.05`; coder/qwen3.6 differ only in `model`).
- **TYPE B (cross-profile route):** any route that changes a binding's declared capabilities or enables tool paths (investigation, qwen3.6-as-primary with a declared profile). Reported as MODEL + EXISTING PROFILE.

## 4. Exact-input replay class per axis
| Axis | messages | wire body | Class |
|---|---|---|---|
| PLANNER | recorded (gzip blob, 2 messages) | recorded | **EXACT SAME-PROFILE REPLAY** (proved) |
| CI-6 localization | recorded | recorded (`format` = json_schema) | EXACT RECORD; the driver does not replay schema requests yet -> class: exact record, replay driver extension needed (experiment-side only) |
| DEVELOPER attempt 1 / PRIMARY | recorded | recorded | **EXACT SAME-PROFILE REPLAY** (proved on a retry request of the same shape) |
| DEVELOPER RETRY | recorded incl. "Previous Error to Fix" | recorded | **EXACT SAME-PROFILE REPLAY** (proved: S4_B seq 192, a qwen3.6 retry) |
| verifier roles | recorded | recorded | exact (not a campaign axis) |
Failure evidence is inside the recorded user message; `prompt.sections` and `diagnosis` give the section/provenance structure. No product instrumentation was added.

## 5. Replay-driver feasibility - PROVED (experiment-side only: `tools/replay_probe.py`)
Mechanics only, six single-request probes on historical stores (outputs `~/kriya-m1-live/model-routing-v1/replay/*.json`): load recorded request -> send its exact system/user through the production `LLMClient` with the target model's own binding -> parse with the production parser (`parse_planner_structured_output`, `parse_structured`) -> record separated timings -> store untouched.
| Probe | wire messages identical to recorded | fields differing | parse | total / load / prefill (s) |
|---|---|---|---|---|
| Planner T3 seq 7 on recorded qwen3.6 | yes | none (byte-faithful reproduction) | ok | 33.6 / 0.0 / 16.4 |
| same on qwen3.8 | yes | model, options.repeat_penalty | ok | 190.7 / 9.9 / 109.8 |
| same on coder | yes | model | ok | 53.5 / 9.6 / 25.7 |
| Developer S4_B seq 192 on recorded qwen3.6 | yes | none | edits | 56.1 / 15.7 / 10.8 |
| same on qwen3.8 | yes | model, options.repeat_penalty | `invalid` (INVALID_EDIT_PROTOCOL) | 80.3 / 3.7 / 59.5 |
| same on coder | yes | model | edits | 30.5 / 9.1 / 10.8 |
These are protocol-mechanics results on ONE old request per axis (n=1, warm/cold effects uncontrolled); no model-quality conclusion is drawn and the old 12 are not used for selection. Not yet built (needed for Stage 2 only): materialize+gates evaluation in a scratch workspace and the schema (CI-6) replay.

## 6. Qualification (existing mechanism only; protocol eligibility, not quality)
Records in `qual-home`; production profile refuses anything not QUALIFIED. Statuses after probes (`kriya model status` per probe config):
| Model | Axis/binding | Status | Evidence |
|---|---|---|---|
| qwen3.8:27b | Planner | QUALIFIED | existing /8 record covers it (no probe) |
| qwen3.8:27b | Developer chain (retry) | QUALIFIED | existing |
| qwen3-coder:30b kriya | Planner | QUALIFIED | existing /8 all-roles record |
| qwen3-coder:30b kriya | Developer primary | QUALIFIED | probe: 19 PASS / 0 FAIL / 1 UNAVAILABLE (endpoint restart, never exercised live), 179 s |
| qwen3-coder:30b kriya | Developer chain (retry) | QUALIFIED | probe: 19/0/1, 179 s (a chain entry is a different identity from the primary) |
| qwen3.6 kriya | Developer primary (declared-default profile) | QUALIFIED | probe: 19/0/1, 114 s; **profile caveat above** |
| deepseek-r1:32b | Planner, non-reasoning binding | UNQUALIFIED | probe 1: 12 PASS / 4 FAIL (plain_completion, streaming_assembly: output truncated at 64/256 tokens because it always reasons; cancellation_semantics; context_capacity timeout 600 s) |
| deepseek-r1:32b | Planner, honest `reasoning: true` binding (precedent: qwen38-qualification.yaml) | **QUALIFIED** (protocol-eligible) | probe 2: 15 PASS / 1 FAIL (context_capacity: the 32k-fill request did not return within 600 s) / 4 UNAVAILABLE, 1408 s; required Planner cases pass; the FAIL is recorded latency evidence, not a role case |
Everything else (qwen3.5:9B/mlx, qwen3:8b, qwen2.5-coder, llama3.2, MTP/MLX variants, embeddings): not probed, per owner instruction; they remain MISSING (never assumed eligible). The mechanism failed closed as designed in every MISSING case. Operational finding: **qualification identity follows the route** - changing the primary `llm` made previously QUALIFIED coder bindings MISSING until re-qualified (observed), so every candidate route needs its own ~2-25 min qualification before it can run in production profile.
DeepSeek replay and quality were NOT tested; its Planner eligibility is protocol-level only, with a measured latency red flag (reasoning model; 32k fill >600 s).

## 7. Baseline route (verified) and expected upside
Planner qwen3.6 (MoE 35B-A3B) / Developer primary + CI-6 qwen3.8 (dense 27B) / retry qwen3.6 / verifier trio qwen3-coder (MoE 30B-A3B). **VERIFIED: the baseline already places three different strongest installed Qwen models on three axes** (MEASURED: 17 planner calls on qwen3.6, 26 developer + 11 localization on qwen3.8, 16 developer on qwen3.6, 46 verifier calls on coder). Expected routing upside: INFERRED LOW-TO-MODERATE; the realistic gains are permutations among three same-lineage models plus one slow reasoning outlier. This lowers the prior that any route clears the pre-registered `Cmin > Bmax` rule on 10 tasks.

## 8. Viable challenger matrix
| Axis | Baseline | Challenger | Profile type | Qualified | Replay | Distinct weights/arch | Competition |
|---|---|---|---|---|---|---|---|
| Planner | qwen3.6 | qwen3-coder 30B-A3B | A | yes | exact, proved | yes (qwen3moe) | **STRONG** |
| Planner | qwen3.6 | qwen3.8 27B dense | A | yes | exact, proved (prefill 5-7x slower, n=1) | yes (qwen35 dense) | STRONG, latency cost |
| Planner | qwen3.6 | deepseek-r1:32b (reasoning) | A | yes (protocol), context_capacity FAIL | exact (not run) | yes (Qwen2-arch R1 distill) | LIMITED (latency) |
| DEVELOPER_PRIMARY_BUNDLE | qwen3.8 | qwen3-coder | A | yes | exact, proved | yes | **STRONG** |
| DEVELOPER_PRIMARY_BUNDLE | qwen3.8 | qwen3.6 | B (profile must be declared) | yes (declared profile) | exact | yes | LIMITED |
| DEVELOPER_RETRY | qwen3.6 | qwen3-coder | A | yes | exact, proved | yes | **STRONG** |
| DEVELOPER_RETRY | qwen3.6 | qwen3.8 | A | yes | exact | yes | STRONG (note: equals "no model switch on retry") |
Not counted as challengers: aliases/tags sharing a weight blob (qwen3.6 x4 IDs, qwen3.8 base/mtp tag, coder latest/kriya), MTP/MLX variants, sub-10B models (not credible for these axes; INFERRED, not tested).

## 9. Model load / swap observations (MEASURED, n small)
Load time on first request after a different model: 0.0-15.7 s (median about 9 s) with 24 / 18 / 19 GB models; prefill dominates cost differences: qwen3.8 (dense) 59.5-109.8 s vs coder/qwen3.6 (MoE) 10.8-25.7 s for the same 5-9k-token prompts. A baseline unit cycle already swaps 3-4 times (planner q36 -> localization/developer q38 -> verifiers coder -> retry q36); at about 10 s each that is about 40 s per unit against about 13.6 min per V2 case (about 5%; the swaps are in the baseline too). Residency of two models on 64 GB (24+18 GB plus 32k KV caches) was not tested (owner: serial, no concurrent loads); `OLLAMA_MAX_LOADED_MODELS`/`KEEP_ALIVE` unset (defaults). Route cost to report later: swaps per run and prefill seconds, separate from capability. A qwen3.8 Planner route would add roughly 1.5-3 min per Planner call (INFERRED from n=1).

## 10. Cross-role state integrity - ACCEPTABLE
Planner -> Developer: plan, owners, scope, goal facts, deterministic gate results (typed; the plan text is MODEL_CLAIMED but validated/grounded before use). Developer -> retry: typed failure and obligations (MEASURED in prompts) plus the typed `likely_files` channel (MODEL_CLAIMED, advisory, recorded). CI-6 -> Developer: advisory reordering of candidates (recorded `localization.decision`). No conversation history, scratchpad or reasoning field crosses roles (MEASURED: 2-message requests, `think: false`). Two items to keep visible: the self-diagnosis channel and the unexercised advisory-evidence path (section 2).

## 11. Pre-registered rules (confirmed, owner 2026-10-13)
Fresh-bank role replay: baseline and every challenger run on the SAME full set of role cases (baseline-correct and -incorrect), normally x2; Planner, Developer-primary-compatible and retry replay alike. Route evaluation: ROUTING-DEV baseline x2, candidate x2; FINAL-HOLDOUT baseline x2, candidate x2; clear solve-rate improvement iff candidate worst > baseline best, no new false success/safety failure, else INCONCLUSIVE. Isolated vs end-to-end retry as in section 2.

## 12. Task-bank GO / NO-GO (owner criteria 1-7)
1 routing supported without code: YES. 2 cross-role integrity: YES (with the disclosed channels). 3 replay/capture feasible: YES (exact, proved). 4 important axis with credible distinct challenger: YES (Planner: coder / qwen3.8; Developer primary: coder; retry: coder). 5 challenger passes existing qualification: YES (probes recorded). 6 local cost practical: PARTIALLY - about 2 h 15 min per 10-task cohort run, 8 pre-registered cohort runs (about 18 h) if every stage is reached, plus replay and per-route qualification; qwen3.8-as-Planner and DeepSeek are slow. 7 can distinguish a meaningful result from noise: ONLY FOR LARGE EFFECTS end-to-end (n=10, range rule); role-level replay is the more sensitive instrument.
**Stage 1 classification: A. PROCEED TO FRESH TASK BANK** - conditional on the owner accepting that (a) expected upside is low-to-moderate (section 7), (b) the likely end-to-end outcome is INCONCLUSIVE or BASELINE RETAINED, and (c) the Developer-primary route is a bundle with CI-6. Role-level replay on the fresh bank (all role inputs, baseline included, x2) is where the experiment can say something. **No model download requested.** Suggested Stage 2 order if approved: bank construction/certification/partition first (no model time), then the baseline noise runs.

## 13. Open items / unknowns
- Replay driver does not yet do materialize+gates evaluation or CI-6 schema replay (needed before role shootouts; experiment-side).
- The unexercised advisory-evidence verbatim channel (section 2) must be checked per retry prompt on the fresh bank.
- qwen3.6-as-primary needs an operator capability declaration decision before it can be a real route.
- DeepSeek was probed for protocol eligibility only (no replay, no quality), and its 32k-fill latency is a risk.
- `probe-ws` authority approvals (SEC-009, digest-bound, local to the scratch workspace `~/kriya-m1-live/model-routing-v1/probe-ws`) were granted by this session to run the owner-authorized probes; they cover only the probe configs and can be revoked with `kriya authority revoke` in that workspace.
