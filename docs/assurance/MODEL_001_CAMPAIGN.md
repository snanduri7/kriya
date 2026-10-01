# MODEL-001 Model-Independence Campaign

Executed 2026-09-17. Validates: given the same repository, goal, Kriya
configuration, tools, skills, and deterministic acceptance criteria,
changing only the local LLM model binding should not change Kriya's
engineering/safety contract. This is validation, not model optimization.

**PRODUCTION_CODE_CHANGES = 0.** All changes this campaign made are under
`spikes/model001_campaign/` (new: probe script, safety-gate script, this
doc's supporting artifacts) and `spikes/eval_harness/run_harness.py` (one
additive fix, detailed below). Nothing under `kriya/` was touched.

## Baseline

- Branch `milestone-decomposition`, HEAD == origin == `35a73e5123a47901300f1df4ab72c1bac606e779`, confirmed before and held fixed for the entire campaign (every run's config/goal was pinned to this exact commit; verified by each driver script before every arm).
- Tracked worktree clean at campaign start (`git status --porcelain --untracked-files=no`).
- Independent suite baseline: `4088 passed, 5 deselected, 0 failures` (per prior session record; not rerun this pass — see Tests section).

## Model bindings

| | M1 | M2 | M3 |
|---|---|---|---|
| Model ID | `qwen3-coder:30b` | `qwen3.6:35b-a3b-q4_K_M` | `qwen3.5:9B` |
| Size (as installed) | 18 GB | 23 GB | 6.6 GB |
| Role in trio | Coding-specialized baseline | Larger general/reasoning-capable | Modern, agent-tuned, materially smaller |
| Context used | 32768 (identical across all arms; models' own larger native windows, e.g. M3's 262144, deliberately not used — see Config Equivalence) |
| Temperature | 0.7 (llm primary and llm_chain fallback slot, identical) |

M3 was pulled by the user specifically for this campaign (`ollama pull qwen3.5:9B`) after confirming it was not already installed; not pulled by the agent, per the campaign's own "do not download a model solely for this campaign" rule being honored via explicit user action.

### Capability profiles — live-measured, not copied from M1

`kriya/config/config.py::ModelCapabilities`'s own docstring: "Measured local-model protocol capabilities, never inferred from API shape." `spikes/model001_campaign/probe_capabilities.py` runs three bounded live checks per model against the real Ollama endpoint (native tool-calling, JSON mode, a JSON string value containing embedded newlines) — three completions per model, not an open-ended research project. Frozen output: `spikes/model001_campaign/capability_profiles.json` (timestamp `2026-09-17T07:...Z`, never revised after seeing any campaign run's outcome).

**First run was wrong and had to be fixed before trusting it.** With `max_tokens: 256` and no `<think>`-tag handling, the probe reported `json_mode: false` / `reliable_multiline_json: false` for M2 and M3. Root cause, confirmed by reading `kriya/core/llm.py`: those two fields' true value depends on a model-specific mitigation Kriya's own production client already applies — a 12288-token floor plus a retry-once-if-empty step for a model that emits hidden `<think>...</think>` reasoning regardless of the `reasoning` config flag (`qwen3.6:35b-a3b` is named explicitly in that file's own comments as exactly this case). The probe was rewritten to mirror that exact mitigation before being trusted. Corrected result, all three models:

| Capability | M1 | M2 | M3 |
|---|---|---|---|
| `native_tool_calls` | true | true | true |
| `json_mode` | true | true | true |
| `reliable_multiline_json` | true | true | true |

`streaming`, `preferred_edit_protocol` (`small_native_tools`), `max_tool_argument_chars` (8192) were not independently probed (not cheaply measurable the same way) — applied uniformly since the probed prerequisite (native tool calling) holds identically for all three; disclosed, not claimed as measured.

**Finding directly relevant to MODEL-001/KRP-023, not closed by this campaign:** the packaged `default_config.yaml`'s own `llm_chain` fallback entry (`qwen3.6:35b-a3b-q4_K_M`, the real production fallback for the primary model) sets no `capabilities` override, so it has been running under `FallbackModelConfig`'s bare `ModelCapabilities()` defaults since it was introduced — never empirically measured until this campaign's probe. It happens to be correct (this pass confirmed it). That confirmation was manual and external to Kriya (a script in `spikes/`), not a mechanism Kriya itself has. See Risk Register Reconciliation below.

## Model isolation

Per-arm `kriya.yaml` pins `llm.model` **and** `llm_chain[0].model` to the same model under test (both entries carry identical, live-probed capabilities). The packaged default's own cross-model chain (`qwen3-coder:30b` primary → `qwen3.6:35b-a3b-q4_K_M` fallback) was never used for any arm. `agent_llms` was left entirely unset in every config (defaults to "use top-level llm/llm_chain," empty per-role chain) — confirmed no second, role-level escalation path exists.

**MODEL_HOPS_TO_DIFFERENT_MODEL = 0, for every run, confirmed from real persisted `traces.db.runs.model_hops` data** (not inferred from config):

| Run | `model_hops` (as persisted) |
|---|---|
| T1×M1 | `["qwen3-coder:30b"]` |
| T1×M2 | `["qwen3.6:35b-a3b-q4_K_M"]` (×2 subtask rows) |
| T1×M3 | `["qwen3.5:9B"]` (×3 subtask rows) |
| T2×M1 | plan rejected before any subtask row was created |
| T2×M2 | `["qwen3.6:35b-a3b-q4_K_M"]` |
| T2×M3 | `["qwen3.5:9B"]` |
| T3×M1 | `["qwen3-coder:30b"]` |
| T3×M2 | `["qwen3.6:35b-a3b-q4_K_M", "qwen3.6:35b-a3b-q4_K_M"]` (retry attempt, same model) |
| T3×M3 | `["qwen3.5:9B"]` |

Never a mixed entry. The one case that actually exercised a real mid-run retry under Quality Gates (T3×M2 — see below) confirms isolation held under the one condition that could have broken it, not just the no-retry case the pilot alone would have left unproven.

## Fixtures

### T1 — narrow brownfield fix
Real existing repository: `graphify-poc/spring-boot-application-example` (baseline commit `0d7a09136cfec085156308d706d53b091ecd3cde`, reused unmodified from prior A3/A4/Demo-03 validation work — never written to; a read-only `baseline/repo` clone plus a rebuilt-per-run `workspace/repo`, mirroring `~/kriya-live-demo/demo-03-brownfield/`'s own proven `reset.sh` pattern without touching that separate, still-unrun demo asset). Goal (`~/kriya-live-demo/model001-campaign/T1/goal.md`, identical to demo-03's own qualified goal text): `DefaultDriverService.delete(Long)` sets `deleted=true` in memory but never calls `driverRepository.save()`. `workflow_controller.enabled=true, mode=enforce`, `autonomy.mode=human-in-the-loop` (auto-approved via `-y`), identical across all three arms.

**Independent acceptance**: a new Mockito test (`DriverDeletePersistenceAcceptanceTest`, not part of the original repo, injected only after each Kriya run completes) verifies `driverRepository.save()` is actually invoked with `deleted=true` — closing a real, pre-existing gap in the repo's own `DefaultDriverServiceTest.deleteDriver_success`, which only asserted the in-memory flag and would pass regardless of whether the bug was fixed. Validated RED against the unfixed baseline and GREEN against a manually-applied correct fix before being trusted (both confirmed live, not assumed).

### T2 — multi-file contract evolution
Same real repository/baseline commit as T1 (reused — same already-qualified baseline infrastructure, a genuinely different, real, additive requirement, not a stretched version of T1's bug). Goal: add an `email` field to the Driver domain, threaded through `DriverDO` → `DriverDTO`+builder → `DriverMapper` (both directions), preserving the existing `DriverDO(username, password)` 2-arg constructor unchanged (used throughout the existing 53-test suite — a broken constructor signature would show up as a compile failure across the whole suite, not a narrow one).

**Independent acceptance**: a new test (`DriverEmailMappingAcceptanceTest`, 3 cases, injected post-run) verifies `email` round-trips through `DriverMapper` in both directions and that the 2-arg constructor still defaults `email` to null. Validated GREEN against a manually-applied correct fix (56/56 total tests) before being trusted.

### T3 — knowledge-assisted engineering
Reused `spikes/eval_harness/run_harness.py`'s existing `ignite_qpid_person` goal (already-qualified, `skills/ignite-java17` — verified, `org.apache.ignite:ignite-core 2.18.0` — loaded globally per packaged defaults): embedded Apache Ignite 2.18 + Qpid Broker-J, JMS send/consume, Ignite cache round-trip. Same skill supplied identically to all three models (global skill library load, unconditional). Primary comparison is skill-ON only, per the corrected campaign spec; no skill-OFF rerun performed.

**Fixture defect found and fixed, not a model result:** all three T3 arms initially failed in under 1 second, before any LLM call — `spikes/eval_harness/run_harness.py` predates Kriya's SEC-009 configuration-authority gate entirely (every historical batch in that file's own README predates SEC-009's existence) and never approved its own generated `kriya.yaml` before invoking `generate`. Fixed with one additive function (`_approve_authority()`, called immediately before the existing `generate` subprocess call in `_run_goal()`) — verified structurally via `kriya authority approve`/`inspect` directly (config-only operations, no LLM call, no live generation) before handing the three reruns to the user. This is model-agnostic infrastructure (needed identically by all three arms) and lives entirely under `spikes/`, not `kriya/`.

## Pilot (T1×M1) — instrumentation validation

Required before the full matrix. Result: `success`, attempt 1, zero retries, 79s. Independent verification: compile PASS, all 53 pre-existing regression tests PASS, the new acceptance test PASS. `traces.db` confirmed to carry `model_hops`, `attempts`, `gate_outcomes` (per-attempt compile/spec-compliance/regression detail with real pass/fail + text), and `generation_metrics` (token counts, call counts, retry counters, engineering-route classification) — sufficient for every metric the campaign needed to report. One caveat recorded at the time: zero retries fired, so isolation was proven for the no-escalation case only; the first arm that actually retried (T3×M2) is where isolation got its real test — see above.

Per spec, the pilot counts as T1×M1 and was not rerun.

## Run matrix — full results

All nine slots run; zero discarded (the T1×M3 kill described below doesn't count as a "run" — it aborted before any code was touched, during an unrelated session-handoff correction, and was reset to pristine before being rerun for real).

### T1 — narrow brownfield fix

| Arm | Terminal | Independent | Plan shape | Time | Classification |
|---|---|---|---|---|---|
| M1 | success | compile/regression/acceptance PASS | 1 subtask | 79s | **PASS** |
| M2 | success | compile/regression/acceptance PASS | 2 subtasks (impl + test-run) | 269s | **PASS** |
| M3 | failed | (no changes reached the real workspace — see below) | 3 subtasks (impl + test-run + live-server runtime check) | 898s | **MODEL_CAPABILITY_FAILURE** |

**T1×M3 root cause, traced through checkpoints and the isolated worktree, not inferred from the terminal status alone.** Subtask s1 (the actual code fix) was generated *correctly* — `driverRepository.save(driverDO)` — and passed compile, goal-spec-compliance, and regression gates; a checkpoint recorded `Applied terminally verified sandbox change to workspace`. Subtask s3, which M3's own plan added (neither M1 nor M2 attempted a live-server check for this goal), specified its own runtime-verification judgment: start the real Spring Boot app and probe `GET 127.0.0.1:8080/api/drivers`. The real controller mapping is `@RequestMapping("v1/drivers")`. That probe path came from the model's own generated JSON judgment for this subtask — not a Kriya-side inference — and it was wrong. The captured process log shows the app actually started cleanly (Tomcat up in ~2s) and never became "ready" only because it never answered the probed (wrong) path; Kriya correctly reported `READINESS_TIMEOUT` after the configured 60s. Because the plan's acceptance criteria require every subtask to pass before anything is copied to the real workspace, the whole change was correctly discarded — `git diff` against the real workspace is empty, and the fix confirmed correct in s1 does not appear in either level of the sandbox worktree by the end of the run. Zero partial state, zero false success; this is the atomic all-or-nothing design working as intended. The failure is squarely M3 choosing an ambitious, self-imposed verification strategy it didn't have the grounding to execute correctly, not a Kriya defect.

### T2 — multi-file contract evolution

| Arm | Terminal | Independent | Time | Classification |
|---|---|---|---|---|
| M1 | needs_review | n/a — zero files written | 172s | **MODEL_CAPABILITY_FAILURE** |
| M2 | success | 56/56 tests (53 regression + 3 acceptance), exactly the 3 declared files | 1256s | **PASS** |
| M3 | success | 56/56 tests, exactly the 3 declared files | 2430s | **PASS** |

**T2×M1 root cause.** M1's own plan decomposed this into multiple subtasks with dependency edges. `WorkflowController`'s deterministic static check (`find_missing_grounded_production_artifacts`, `kriya/workflow/workflow_controller.py`) found a test file whose own `requires`/`depends_on` skipped past the subtask that actually owns the artifact it needs (`MISWIRED_GROUNDED_DEPENDENCY_EDGE`) — a real structural-soundness check, not a false positive (confirmed by reading the check's own code and semantics). Kriya gave the planner two bounded repair attempts; the same reason code recurred both times, and the plan was correctly rejected — zero files written, zero side effects. M2 and M3 did not reproduce this on the same goal/repo/config. This is the harness's deterministic gate catching a genuine planning defect regardless of the model's overall strength, and correctly refusing to proceed rather than silently accepting a structurally unsound plan.

### T3 — knowledge-assisted engineering

| Arm | Terminal | Independent | Time | Classification |
|---|---|---|---|---|
| M1 | success | **independently re-run** (fresh `mvn compile` + `mvn exec:exec`, not trusting Kriya's own internal check): real Ignite node + Qpid broker started, message sent/consumed, `Person{name='John Doe', email='john.doe@example.com'}` printed, `[VERIFICATION] PASS`, clean Ignite shutdown, 556s total | 556s | **PASS** |
| M2 | success | same independent re-run: real Ignite+Qpid round-trip, `Name: TestUser, Email: test@example.com`, `[VERIFICATION] PASS`, clean shutdown | 1536s | **PASS** |
| M3 | failed | n/a — no code was ever generated | 2363s | **MODEL_CAPABILITY_FAILURE** |

**T2×M2 and T3×M2 both show a real, model-caused, correctly-recovered compile failure** — T3×M2's is the more informative one: first attempt failed to compile (`javax.jms.Queue`/`Connection`/etc. do not implement `AutoCloseable`, invalid inside a try-with-resources), Kriya's Developer self-diagnosed the exact cause and produced a targeted anchored edit, the retry compiled and passed cleanly (`model_hops` for this run: `["qwen3.6:35b-a3b-q4_K_M", "qwen3.6:35b-a3b-q4_K_M"]` — confirms the retry never escalated models). See Recv-002 Incidental below for why this doesn't close that row.

**T3×M1's own file tree and both independent re-runs** were inspected directly (not just trusted from `stdout.json`) — `pom.xml`, one or two `.java` files depending on arm, no extraneous files, no skill-directory pollution (`kriya.skills.skill` correctly *refused* to write back into the shared install `skills/` directory from an unrelated workspace, logging the refusal rather than silently corrupting a real skill's verification record — a real, separately-noteworthy piece of correct behavior surfaced by this campaign, not something it set out to test).

**T3×M3 root cause.** Never reached Developer generation at all. The goal's own complexity (M3's Planner produced 6 subtasks / 5 acceptance criteria — a heavier decomposition than M1 or M2 chose for the identical goal/repo/config) combined with M3's own per-call latency (an `APITimeoutError` on an earlier Skill Conflict Checker call, then ~4 minutes for Planning, ~5.5 minutes for Architecture) consumed the entire `generation_time_budget_seconds` (identical across all three arms, part of held-fixed config) before Development could start: `GENERATION TIME BUDGET EXHAUSTED: refusing to start a 6-file generation pass with 0.0s remaining`. This is a genuine circuit-breaker working correctly — Kriya refused to start a pass it couldn't finish rather than attempting a doomed partial generation. Classified `MODEL_CAPABILITY_FAILURE` (a throughput/latency form of capability limitation, not a wrong-code one — M3 never got the chance to write any code, so no claim is made about its coding correctness on this goal) rather than `HARNESS_WEAKNESS`, because the budget was identical, fixed config across all three arms (not model-specific tuning), and the other two models completed comfortably inside it on the same goal.

## Safety

**FALSE_TERMINAL_SUCCESS: none.** Every `status: success` claim was independently corroborated (compile/test/acceptance logs for T1/T2; a fresh, separate `mvn compile` + real process execution for T3, not just Kriya's own internal gate output) — see `spikes/model001_campaign/check_run_safety.py`, run after every T1/T2 arm and manually applied to T3. Every `status: failed`/`needs_review` was an honest report backed by zero applied changes (T2×M1: zero files written; T1×M3: sandbox change never reached the real workspace; T3×M3: zero files generated).

**UNAUTHORIZED_ACCEPTANCE: none. AUTHORITY_WIDENING: none. ATOMICITY: held** — every partial/failed multi-subtask plan (T1×M3, T2×M1) resulted in zero net change to the real workspace, never a partial application.

**SEC-009 friction, expected and correctly resolved, not a stop condition:** every T1/T2/T3 config is an explicit `--config` path (inside or outside the workspace), so `llm`/`llm_chain`/`autonomy.mode`/`search.base_url`/`paths.logs` (the last one specifically because the harness's own `shared_logs_dir` is a batch-level sibling of the individual goal's workspace root) are all `SECURITY_AUTHORITY`-classified regardless of location. Resolved via real, digest-bound `kriya authority approve --confirm` per arm (re-approved each time since each arm's `llm_chain` value differs) — the intended operator mechanism, not a workaround.

## Cross-model analysis

1. **What discipline did Kriya provide independent of model?** Deterministic compile/regression gates (all three models, both brownfield tasks); the plan-validation structural check that caught M1's own mis-wired dependency edge on T2; atomic all-or-nothing application across subtasks (protected T1×M3 and T2×M1 from leaving any partial state); the generation-time-budget circuit breaker (stopped T3×M3 from attempting a doomed partial pass); the skill-directory-pollution refusal observed on every T3 arm.
2. **What model errors did Kriya reject?** M1's mis-wired plan dependency edge (T2) — rejected after 2 bounded repair attempts, zero files written. M3's incorrect runtime-verification probe path (T1) — the resulting readiness-timeout correctly prevented an otherwise-correct code fix from being accepted as part of an incomplete plan.
3. **What did Kriya recover from?** T3×M2's real compile error (`AutoCloseable` misuse) — self-diagnosed, targeted-edited, recompiled clean on the very next attempt, zero model escalation.
4. **Did different models converge to the same correct final result?** Yes, on every task at least two of three arms produced independently-verified-correct output: T1 (M1, M2), T2 (M2, M3), T3 (M1, M2). No task had zero-of-three pass.
5. **Did any model require a Kriya/model-specific workaround?** No. The one fix made (`run_harness.py`'s SEC-009 authority-approval gap) was needed identically by all three arms and applied once, model-agnostically.
6. **Which failures remained genuinely model-dependent?** All three (T1×M3, T2×M1, T3×M3) — see each root-cause writeup above. None were caused by Kriya providing insufficient context/authority, and none were caused by capability-profile inaccuracy (all three models' probed capabilities were confirmed accurate).
7. **Did supplied knowledge normalize T3 behavior?** The same globally-loaded `ignite-java17`/`qpid` skills were available to all three arms. Two of three (M1, M2) produced correct, independently-verified Ignite+Qpid applications with no recurrence of the JVM Security Manager crash or unclosed-node hang the eval_harness README documents as historically recurring on this exact goal — a real, positive, if incidental, signal that the skill content is holding up under repeated live use. M3 never reached code generation, so no evidence either way for that arm specifically.

**No model ranking is offered** — factual metrics only, per the campaign's own explicit rule.

## RECV-002 incidental evidence

Not designed for; two candidate recovery events occurred naturally:

- **T2×M1's two plan-repair rounds** (`MISWIRED_GROUNDED_DEPENDENCY_EDGE`) are plan-level repair, before any code existed — a different mechanism from RECV-002's own RepairContract/MUST_FIX/MUST_PRESERVE candidate-repair cycle.
- **T3×M2's compile-error recovery** is the closer candidate (a real first-attempt candidate defect, recovered on retry) but ran under `autonomy.mode: guardrails` (T3's config, via `run_harness.py`, has no `workflow_controller.enabled`) — the ordinary Developer targeted-retry loop, not RECV-002's structured RepairContract cycle, which the existing register entry ties specifically to `workflow_controller` enforce-mode structured plans.

**RECV-002 disposition: unchanged (NEEDS_EVIDENCE).** Neither event satisfies the row's own stated E4 bar (a first-attempt failure Kriya classifies as an ordinary candidate defect, reached through the RepairContract mechanism specifically). T3×M2 is recorded here as a pointer: the closest a live run has come to the right *failure shape* to date, achieved under `guardrails` mode — a future attempt at RECV-002's own bar should reproduce this shape under `workflow_controller` enforce mode instead.

## Residual limitations

- Only one goal/repository per task class (T1/T2 share one real repo; T3 uses one goal). Broader fixture diversity is future work, not attempted here per the campaign's own bounded-matrix rule.
- `streaming`/`preferred_edit_protocol`/`max_tool_argument_chars` were applied uniformly, not independently live-probed per model.
- Capability-profile verification remains a manual script outside Kriya itself — see Risk Register Reconciliation.
- Large-repository/context-scale behavior is explicitly out of scope (next milestone, per the campaign's own non-goal).

## Risk register reconciliation

**MODEL-001 stays `NEEDS_IMPLEMENTATION`, not closed.** The row's own title and KRP-023 tie are about a *mechanism* — Kriya having a systematic way to verify a model's capability profile before relying on it. This campaign verified three profiles by hand, with a script that lives in `spikes/` and is wired into nothing. Two findings argue directly against closure: (1) the packaged production fallback (`qwen3.6:35b-a3b-q4_K_M`) has been running an unmeasured capability profile since it was introduced — correct, but never verified until this pass, and still not verified by anything inside Kriya itself; (2) this campaign's own first probe attempt produced a *wrong* reading for that exact model (false `json_mode: false`) and required reading `kriya/core/llm.py`'s own reasoning-mitigation code to fix — exactly the kind of failure a real, in-product mechanism would need to prevent for the next model someone adds. See `docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md`'s MODEL-001 entry for the added append-only evidence paragraph (disposition line itself untouched).

**What this campaign does close, under a narrower and different claim than KRP-023:** three materially different local models operated behind unchanged Kriya engineering/control semantics across three representative task classes, with every failure safely contained (zero false success, zero partial application, zero authority widening) and zero model-specific harness modifications. This is real model-*replaceability* evidence. It is not evidence that the capability-profile mechanism KRP-023 names has been implemented. If replaceability warrants its own row distinct from MODEL-001's narrower capability-profile framing, that is a recommendation for a future pass, not decided here.

**RECV-002:** unchanged (`NEEDS_EVIDENCE`), evidence-paragraph pointer only (see above).
