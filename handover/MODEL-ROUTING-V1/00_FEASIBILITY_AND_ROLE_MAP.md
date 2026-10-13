# MODEL ROUTING V1 - Stage 0: feasibility and role map (2026-10-13)

Evidence labels follow `handover/ENGINEERING_RULES.md` (MEASURED / TRACED / INFERRED / UNKNOWN). No model call was made in this stage; Ollama was read through `/api/tags`, `/api/show`, `/api/version`, `ollama ps`, and `kriya model status` only.

## 1. Baseline and branch
- BACKEND READINESS V2 integration (owner decision 1-4): `origin/main` (`c98b18d`) is an ancestor of `backend/readiness-v2` (`d0d5423`); tree clean; 52 commits; no GUI paths in the diff; no live evidence blobs (largest additions are committed test fixtures and replay JSON); doc 24 still reads `BACKEND SOLVER READINESS GATE: FAIL`, `SOLVER REPEATABILITY: NOT ESTABLISHED`. Product diff = 32 files under `kriya/` (reviewed V2 slices A/B + relevance + semantic safety) plus tests/docs. Last recorded full suite: 10676 passed / 1 failed (registered `TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001`, 2/2 green in isolation), product code unchanged since (`9de5404`). No model cohort re-run.
- `main` fast-forwarded LOCALLY to `d0d5423` (no squash, no tag). **`origin/main` NOT pushed** - needs an explicit owner push decision (ENGINEERING_RULES s23); `model-routing/v1` is based on that SHA.
- BASE_MAIN_SHA = `d0d54233f154a2b13be68deaf8d1c38df75c5b38`; MODEL_ROUTING_V1_BRANCH_SHA at creation = the same SHA.
- `gui/run-cockpit-v1_1` (`5be638e`) and `gui/end-user-experience-v1_1` (`c98b18d`) are fully merged into main (0 commits ahead); not touched. `backend/readiness-v2` now equals main (housekeeping candidate, not deleted).
- Product behavior: unchanged by this stage (only this document is added).

## 2. Authoritative model-driven role map (TRACED + MEASURED)
Config surfaces: `agent_llms.<role>.llm / .llm_chain` (planner, architect, reviewer, run_verifier, skill_gap, spec_compliance; `kriya/config/config.py:1020`), Developer = top-level `llm` + `llm_chain` (the config comment at `:1021` states the Developer deliberately has no `agent_llms` entry). Roles that actually issued calls in the 10 recorded V2 closing-cohort runs (`model.request` records, role from the attempt-evidence envelope; T6 and S6_A recorded no model call):

| Role (code name) | Purpose | Call site | Model in baseline (V2 config) | Requests in V2 cohort | Routable independently? |
|---|---|---|---|---|---|
| planner | structured plan; plan-repair rounds are more Planner calls | `PlannerAgent` | qwen3.6-35B-A3B (`...-kriya-620d4d5ce36a`) | 17 | YES: `agent_llms.planner.llm` |
| localization | CI-6 ambiguity-only candidate choice, schema-constrained, <=1 call | `workflow/localization_decision.py:163` (`model_role("localization")`) | qwen3.8:27b (it rides the **primary `llm`**) | 11 | **NO** - no `agent_llms` entry; follows the Developer primary |
| developer (attempt 1 and retries) | candidate generation, edit protocol, investigation, self-correction | `DeveloperAgent` via `attempt._run_developer_generation` | qwen3.8:27b primary (26); qwen3.6 on `llm_chain[0]` for retries (16) | 42 | YES: `llm`, `llm_chain` |
| run_verifier | JSON verdict on a run | `RunVerifierAgent` | qwen3-coder:30b (`...-kriya-e52213655394`) | 13 | YES |
| spec_compliance | JSON compliance opinion | `SpecComplianceAgent` | qwen3-coder:30b | 20 | YES |
| reviewer | JSON review | `ReviewerAgent` | qwen3-coder:30b | 13 | YES |
| architect, skill_gap | file list / knowledge-gap extraction | `ArchitectAgent`, `SkillGapAgent` | qwen3-coder:30b | **0** | YES (not exercised in V2) |

Other: `MilestonePlannerAgent` and `planner_shadow` (shadow planning, never read by routing) exist but did not run in the closing cohort; the self-correction loop and investigation loop are the Developer's (`_as_developer`).

**There is no standalone Repair role** (TRACED). A "repair" is a Developer retry; the retry model is `resolve_fallback_model(retry_count, chain)` = `chain[min(retry_count-1, len(chain)-1)]` (`workflow/attribution.py:327`), so in the baseline attempt 1 is qwen3.8 and retries are qwen3.6 (MEASURED: S4_B 7+10, T2 3+6 calls). A Repair-only change is expressible as the `llm_chain` order with `llm` fixed. Plan repair is inside the Planner. The campaign's three isolation axes are therefore **Planner**, **Developer-attempt-1 (`llm`)**, **Developer-retry (`llm_chain`)**.

Deterministic (never routed): all gates, the goal-contract compiler/feedback, plan feasibility, edit application, localization scoring (only its ambiguity tie-break is a model call), validators, static analysis, semantic safety, the oracle.

Baseline per-role settings (`~/kriya-m1-live/backend-readiness-001/tasks/*/config.yaml`, sha256 in each `FREEZE.json`): native Ollama adapter (`/api/chat`, truncate:false), `num_ctx` 32768 (explicit), temperature 0.7, top_p 0.8, top_k 20; Developer `max_tokens` 16384, Planner `max_output_tokens` 8192 (architect/reviewer/etc. 16384); retries `max_retries_full_set=4`, `targeted_max_retries=3`, `no_progress_limit=3`; run budget 3600 s (120 s reserved); `runtime_profile: production`; hard rules: qualification per role, SEC-009 authority classification.

## 3. Per-role routing configurability (answers section E)
| Role | Model selectable independently via existing config | Fallback selectable | Needs product code |
|---|---|---|---|
| planner / architect / reviewer / run_verifier / skill_gap / spec_compliance | YES (`agent_llms.<role>.llm`) | YES (`agent_llms.<role>.llm_chain`) | NO |
| developer attempt 1 | YES (`llm`) | YES (`llm_chain`, retry ladder) | NO |
| developer retry ("repair") | YES (`llm_chain[0]`; later entries by retry count) | n/a | NO |
| localization | **NO** (follows `llm`) | no | would need code -> **not a campaign axis; it is a confound** |

Also present, not needed: `model_policy.routing` (PRD-019, mode off/evidence/frozen, selects among qualified candidates by Kriya's own measured metrics, needs `kriya model metrics --write-table`). Static routing V1 uses explicit bindings only, which `choose_route` also treats as winning when qualified. Verdict: **per-role routing exists without code**. Confound to carry forward: changing the Developer `llm` also changes the model answering localization decisions; Developer-isolation results must be reported as "Developer + localization".

## 4. Model qualification mechanism (TRACED, `core/model_qualification.py`, `handover/claude-context/model-runtime-and-inference.md`)
- `kriya model qualify|status|fingerprint`. A record binds the exact runtime fingerprint (served digest, quantization, renderer/parser, tokenizer, window) + the effective inference-settings digest + adapter version + `QUALIFICATION_POLICY_VERSION` (now `kriya-qualification/8`) + policy digest (incl. Developer response-protocol identity). Any drift = STALE. Stored in `~/.kriya/qualifications` (outside the workspace; 22 records present).
- Role-aware: `required_capabilities(config, role, model)` = base cases (plain completion, finish_reason, truncation, reasoning behavior, endpoint errors, **over_context_refusal**) + role cases (developer: full_file_raw_content, anchored_edit_protocol, malformed_output_recovery; planner: malformed_output_recovery) + every protocol the capability profile enables (native tools, JSON, multiline JSON, streaming). One record serves every role that sends the same settings and whose cases it passed.
- Fail closed: under `runtime_profile: production`, an unqualified role model (and a Developer retry identity) is refused before any request; `kriya model status`/`doctor --production` show it; `model_policy.routing` treats only exact + QUALIFIED as eligible.
- Scope limit (important): these are **protocol/capability** cases, not engineering-quality cases. QUALIFIED = "speaks Kriya's wire protocol correctly", not "good at the role". Use it for eligibility only (Phase J), never as a ranking.
- Verdict: usable as-is for candidate eligibility; `kriya model qualify --model X --role R` is the existing way to create a record (it sends short probe prompts to the model; **not run in this stage**).

Current state for the route baseline (`kriya model status`, MEASURED, 2026-10-13 10:42): developer qwen3.8:27b QUALIFIED, developer qwen3.6-kriya QUALIFIED, planner qwen3.6-kriya QUALIFIED, architect/reviewer/run_verifier/skill_gap/spec_compliance qwen3-coder-kriya QUALIFIED.

## 5. Exact role-input capture (answers G) - EXACTLY RECORDED for every role (TRACED + MEASURED)
The attempt-evidence recorder (`kriya/core/attempt_evidence/`, LR-R1-M1; hooks in `core/llm.py`: `call_scope(current_model_role())`, `record_wire_request`) is a hash-chained, sealed per-run store written at the LLMClient boundary, so it covers every role. In the sealed store of V2 T3 (`~/kriya-m1-live/backend-readiness-001/state/attempt-evidence/20261013T072440-fe3abe9b/`): 22 `model.request` records, each with role, model, temperature, max_tokens, adapter, `response_format`, tools count and two content blobs - `messages` (41,333 bytes for the first Planner call) and `wire_body` (41,548 bytes) - plus `model.response/result`, `prompt.sections`, `developer.parse`, `gate.result`, `retry.delta`, `fallback.decision`. Capture default is `full` (design D4). **No production instrumentation and no proxy are needed.** Reasoning text is never stored (presence/size only).
Classification: SYSTEM/ROLE INSTRUCTIONS, GOAL, canonical state, deterministic facts, plan, scope, candidate, failure evidence = EXACTLY RECORDED (inside `messages`/`wire_body`; `prompt.sections` gives the section structure).
**Caveat (Phase 17 risk, MEASURED in the status log):** the Developer prompt is rendered per the TARGET model's capability profile: qwen3.8 and qwen3-coder are `small_native_tools` (tool schemas in the request), qwen3.6 resolves to `text_markers`/`full_file` (no tools). A recorded Developer request replayed to a model with a different profile is NOT the same semantic input as that model would receive in a real route. Replay is exact only among same-profile models (qwen3.8 <-> qwen3-coder); for others the real Kriya prompt builder must render the prompt from recorded canonical state, which is only deterministically reconstructable, not exactly recorded. Planner/Reviewer-family JSON requests carry no tools and replay exactly.
Open check for Stage 1: whether a replay driver can feed `messages` through the real `developer.parse`/edit application/gates in a scratch workspace without product edits (expected yes via the public classes; unverified).

## 6. Cross-role state integrity (answers H) - TRACED, partly UNKNOWN
- Each role call builds fresh messages from Kriya state; no conversation object is shared between roles (`BaseAgent.run` -> `LLMClient.complete*`). Reasoning/scratchpad text is dropped at the normalizer (`core/completion.py`: presence/size only), so no private reasoning crosses roles. Provider-specific reasoning fields are not forwarded.
- Planner -> Developer: the validated plan, owners, authorized scope, goal facts/examples are Kriya typed state (DERIVED_DETERMINISTIC; the plan text itself is MODEL_CLAIMED but validated/normalized and grounded by `plan_validation`/`edit_feasibility` before use). Developer -> repair: the retry carries the deterministic gate failure (MEASURED/DERIVED), the failed candidate (an artifact) and obligations (`ObligationLedger`, `contract_feedback` violation text).
- **Channel to watch (not fully traced):** the Developer's own `FIX ANALYSIS` text is MODEL_CLAIMED, is requested again on retries (`agent.py` retry prompts) and is read by `attribution.extract_self_diagnosed_files` (a deterministic string match that can steer retry targeting). A different model on the retry therefore receives retry prompts built from state but its attribution path may be influenced by the previous model's analysis text. Status: INFERRED-low risk, to be confirmed by reading `retry_strategy.py:957/1190` in Stage 1. If it creates hidden continuity that breaks on a model switch -> Stop Rule, not a workaround.
- Existing model-specific formatting difference (record only): edit protocol/tool schema per capability profile (section 5).

## 7. Installed model inventory (MEASURED, Ollama 0.40.0, Apple M1 Max, 64 GB; `/api/tags`+`/api/show`)
Aliases grouped by identical weight blob. Distinct generative weights:

| ID | Tags / digests | Arch, size, quant | Ctx cap | Caps (Ollama) | Kriya /8 qualification state (by digest) |
|---|---|---|---|---|---|
| M1 qwen3.6-35B-A3B | `qwen3.6:35b-a3b-q4_K_M` (b52ca43760e9, b8cba206bbea, llamacpp: alias), `...-kriya-620d4d5ce36a` (0314ad2492d1, 6b246a1d7e84, llamacpp: alias); one weight blob | qwen35moe, 36.0B, Q4_K_M, 23.9 GB | 262144 | completion, vision, tools, thinking | QUALIFIED: developer+planner (0314ad2492d1, 2026-10-07) |
| M1b qwen3.6 MTP | `qwen3.6:35b-a3b-mtp-q4_K_M` (096fdbd02fe6) | qwen35moe, 35.5B, Q4_K_M, 22.6 GB; different blob | 262144 | same | no /8 record found -> MISSING; near-variant of M1, not independent diversity |
| M2 qwen3.8-27B (dense) | `qwen3.8:27b`, `qwen3.8:27b-mtp-q4_K_M` (22130167c4c2, same digest) | qwen35, 27.3B, Q4_K_M, 17.7 GB | 262144 | completion, vision, tools, thinking | QUALIFIED: developer only (3299557609, 2026-10-07); planner/reviewer-type roles: no /8 record (/3 only) -> MISSING for them |
| M2b qwen3.8 MLX | `qwen3.8:27b-mlx` (5642e97495e1) | safetensors nvfp4, 18.2 GB | - | same | only /2 record; MLX previously rejected (memory: Ollama 0.40 requalification) -> exclude |
| M3 qwen3-coder-30B-A3B | `qwen3-coder:30b` = `:latest` (06c1097efce0), `...-kriya-e52213655394` (141e95d786b8) | qwen3moe, 30.5B, Q4_K_M, 18.6 GB | 262144 | completion, tools (no thinking) | QUALIFIED /8: all seven roles (141e95d786b8, 2026-10-01 and 2026-10-07) |
| M4 deepseek-r1:32b | edba8017331d | qwen2 arch distill, 32.8B, Q4_K_M, 19.9 GB | 131072 | tools, thinking, completion | no record -> MISSING; reasoning-only template; likely to meet the Developer response protocol poorly (INFERRED) |
| M5 small | `qwen3.5:9B` (6488c96fa5fa, 6.6 GB gguf), `qwen3.5:9b-mlx` (203e30078279), `qwen3:8b` (500a1f067a9f, 5.2 GB), `qwen2.5-coder:7b` (4.7 GB), `qwen2.5-coder:1.5b`, `llama3.2` (3.2B) | 1.5-9.7B | 32k-262k | mixed | no /8 records -> MISSING; 3.5-9B dense/small: not credible Planner/Developer challengers for the V2-class tasks (INFERRED from size and prior memory notes) |
Embedding models (nomic, embeddinggemma, qwen3-embedding, all-minilm) are not role models. Footprint: loaded size with `num_ctx` 32768 for M3 = 21 GB (`ollama ps`, MEASURED in V2 runs); M1 and M2 were not measured here.
All distinct large candidates are one lineage (Qwen). Real architectural/training diversity is low (dense 27B, two MoE, one R1-distill).

## 8. Protocol / role eligibility plan (J; design only)
Use the existing mechanism: `kriya model qualify --model <alias> --role <role>` on a copy of the baseline operator config in which only that role's binding differs, then `kriya model status`. Cases are exactly `required_capabilities` for the role. Then a second cheap tier from recorded V2 requests (section 5), measured outside Kriya by a driver: replay N recorded role requests (Planner: JSON plan schema validity through the real plan parser/validator; Developer: real response parser/`developer.parse` only for same-profile models; verifier roles: JSON parse) and record PASS/FAIL/PARTIAL/MALFORMED rate. Rules: a model that needs a parser/template/prompt change = INELIGIBLE for that role; a binding the production profile refuses (unqualified) is not worked around. deepseek-r1 and the small models run the same gate; failing is a result.

## 9. Challengers per role (K)
| Role axis | Baseline | Viable distinct challengers (installed) | Class |
|---|---|---|---|
| Planner | M1 qwen3.6 | M3 coder (already QUALIFIED planner /8); M2 qwen3.8 (needs planner qualification) | STRONG (2) |
| Developer attempt 1 (+ localization) | M2 qwen3.8 | M3 coder (QUALIFIED developer /8), M1 qwen3.6 (QUALIFIED developer) | STRONG (2) |
| Developer retry / "repair" | M1 qwen3.6 (`llm_chain[0]`) | M3 coder (QUALIFIED), M2 qwen3.8 (QUALIFIED developer) | STRONG (2); but expressible only as chain order, not a separate role |
| reviewer / run_verifier / spec_compliance | M3 coder | M2, M1 (need role qualification) | CANDIDATES EXIST but no evidence these cause errors; their opinion is never verification evidence -> exclude unless baseline attribution implicates them (Phase 22) |
| architect, skill_gap | M3 coder | - | 0 calls in V2: exclude |
Caveat (honest): the baseline already assigns a different one of these three models to each of Planner/Developer/retry, and the three are the strongest installed; the experiment is a permutation test over three same-lineage models plus one unqualified outlier. Expected upside is uncertain. No download is requested.

## 10. Load/swap cost design (L)
Measure separately per role call: MODEL_LOAD_OR_SWAP_TIME (first-token latency of the first request after a different model was resident, from `ollama ps` before/after and request timings in the recorder `t_wall`/`t_mono_ms`), INFERENCE_TIME, TOTAL_ROLE_WALL_TIME. The baseline already swaps three models (23.9 + 17.7 + 18.6 GB) within a run (MEASURED roles above; `ollama_ps_before/after.txt` of V2 runs show only the last-used model plus the embedder resident, default keep-alive about 5 minutes). Machine: M1 Max, 64 GB unified memory; `OLLAMA_MAX_LOADED_MODELS` and `OLLAMA_KEEP_ALIVE` are unset (default), `OLLAMA_CONTEXT_LENGTH=65536` is set in the server environment (Kriya sends its own `num_ctx` 32768 explicitly; see the 2026-08-13 context-leak incident memory). Whether two large models can stay resident was NOT tested (owner: no concurrent loading). Future routing metric: swaps per run.

## 11. Revised statistical design (M-P; pre-registered, owner decisions 2026-10-13)
- ROUTING-DEV baseline: 2 complete runs of the same 10 tasks. Candidate (if promoted): 2. FINAL-HOLDOUT baseline: 2. Candidate: 2. No favorable reruns.
- Role replay: baseline and every challenger get ALL role inputs of the dev set (baseline-correct and baseline-incorrect), same repetitions (normally 2); a third repetition only for a predeclared exact tie/variance question.
- Decision rule: clear solve-rate improvement iff `Cmin > Bmax` over the two runs, with no FALSE_SUCCESS / authority / corruption / leak and no role-protocol regression; overlap = INCONCLUSIVE; no hardcoded "+3"; no significance claims.
- Power note (INFERRED, to be tested by the baseline noise run): V2 moved 8/12 -> 6/12 on identical setup, so for n=10 the rule needs roughly a 3-task gap between non-overlapping ranges; expect many INCONCLUSIVE outcomes. This is accepted by the owner.
- Stability subset (if reached): baseline AND candidate on the same preselected subset with the same repetition count.
- Cost estimate (MEASURED basis: the 12-case V2 closing cohort ran 06:50-09:33 = 2 h 43 min, about 13.6 min/case incl. 120 s cool-down): one 10-task cohort run is about 2 h 15 min; the pre-registered design needs 8 cohort runs (about 18 h serial wall time) plus replay shootouts, if every stage is reached. The stop rules (no dev improvement -> no holdout) cap the likely spend at the first 4 runs (about 9 h) plus replay.

## 12. Items found that bear on the later phases (not fixed; Kriya frozen)
1. `localization` rides the Developer primary (section 3): Developer-axis results include it.
2. Developer prompts are profile-rendered per model (section 5): replay across profiles is not same-input.
3. Qualification is a protocol gate, not a quality measure (section 4).
4. `FIX ANALYSIS` text may influence retry attribution (section 6, to confirm).
5. Candidate pool is one lineage; baseline already splits roles across the three best installed models.
6. `origin/main` is not yet updated (owner push decision pending).

## 13. Stage 0 decision
**A. PROCEED WITH INSTALLED MODELS** (conditional): per-role routing needs no product code (Planner, Developer attempt 1, Developer retry/llm_chain, verifier roles); the existing qualification mechanism is usable for eligibility; exact role inputs are recorded for every role without product changes; three distinct credible large challengers exist for Planner and Developer. Not an endorsement of expected benefit: the candidate pool is narrow (section 9 caveat). No model download is requested. Task-bank construction (Phases 7-11) is justified only if the owner accepts the Stage 1 plan: (a) role qualification runs for M2 (planner) and any other chosen candidate, (b) a replay-driver feasibility check on the recorded V2 stores (no new model cost beyond probes), (c) resolving item 4 above. Nothing else was run.
