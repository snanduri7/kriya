# LR-R1-P1: FALLBACK_MODEL_INCOMPATIBLE investigation

**Status:** root cause CONFIRMED by a deterministic reproducer. **No fix implemented** (task instruction: stop before
fix).
**Code basis:** `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03`, worktree branch `investigation/lr-r1-reliability`.
**Live evidence:** the frozen CAGC-v2 80-run evidence, `~/kriya-cagc-v2/REVIEW/06*` (read only).
**Reproducer:** `tests/test_lr_r1_p1_fallback_incompatible_reproducer.py` (2 tests, pass on current code: they pin
the live mechanism).
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED, UNKNOWN.

## 1. OBSERVE

- **Terminal in 21 of the 57 failed runs** (MEASURED, `06a` census): Java 6, Spring Boot 3, Spring XML 1, Python 11;
  Arm A 8, Arm B 13.
- **The reason is identical in every run** (MEASURED): `phase: call`, `selected: null`, one rejected candidate,
  - the fallback `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a`, with
  - the reason "the attempt may only patch `<file>` (no complete current source was shown), but `<fallback>`'s
    capability profile (`unverified_conservative_default`) returns whole files only (edit protocol `full_file`)".

## 2. MEASURE

| Fact | Value | Source |
|---|---|---|
| Configured Developer fallbacks | exactly one: `llm_chain[0].model = qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a`; keys `api_key, base_url, context_window, extra_body, inference_runtime, model, temperature`; **no `capabilities`** | `REVIEW/CONFIG_SNAPSHOTS/provider-contract-v5-production.yaml` |
| Primary binding | `qwen3-coder:30b-kriya-e52213655394` with an explicit `capabilities` block (`preferred_edit_protocol: small_native_tools`) | same file |
| Resolved profiles (live log, every run) | primary: `Source: explicit_primary, preferred_edit_protocol=small_native_tools`. Fallback: `Source: unverified_conservative_default, native_tool_calls=False, json_mode=False, preferred_edit_protocol=full_file` | e.g. `B/evidence/python-symbol-valuechain.r7/...generate.log:30,50` |
| The fallback's own Developer qualification | record `b0ed2c61...` (runtime `26cb2deb...`, settings `482067b2...`, policy `/8`, `roles: [developer]`): **16 PASS, 4 UNAVAILABLE, 0 FAIL**. `anchored_edit_protocol` **PASS**; `full_file_raw_content`, `structured_json`, `multiline_json`, `malformed_output_recovery` PASS; native tool calls ×3 and endpoint restart UNAVAILABLE | `~/.kriya/qualifications/b0ed2c615189....json` (read only) |
| Runs where the fallback DID serve (whole-file attempts allowed) | 96 Developer calls in 22 runs; 2 of those runs succeeded (valuechain A r4, A r5); whether a fallback call produced the accepted candidate is NOT_RECORDED | `06` §8 |
| Typical path before the refusal | median 4 failed attempts with generation in the stopping subtask (range 1–10). The most frequent shape (8 runs): anchored edit fails → `ANCHOR_CONTEXT_NOT_ESCALATED` ×2 (refused with no model call) → `REPEATED_ACTION` strategy transition → `fallback_targeted` → refused | `06` §2, §8 |
| Size of the refused targets | `httpx/_exceptions.py` 8.5 KB, `OwnerController.java` 6.6 KB, `JdbcPetRepositoryImpl.java` 4.2 KB (9 of 21 refusals); `more.py` 173 KB, `StringUtils.java` 396 KB, `Fraction.java` 34 KB, `FractionTest.java` 41 KB (12 of 21) | `wc` on the experiment's task workspaces |

## 3. TRACE (61a867f)

```
configured fallback (llm_chain[0]: pinned derived tag, no capabilities)
  -> qualification identity: runtime digest + inference settings -> record b0ed2c61 (QUALIFIED, anchored_edit_protocol PASS)
  -> ModelCapabilities resolution: kriya/core/model_capabilities.py::resolve_model_capability_profile
       llm_chain binding found -> _resolve_for_binding(capabilities.model_fields_set == {}) ->
       KNOWN_MODEL_PROFILES.get("qwen3.6:35b-a3b-q4_k_m-kriya-620d4d5ce36a") = None   (exact match only, MODEL-001)
       -> UNVERIFIED_MODEL_CONSERVATIVE_PROFILE (preferred_edit_protocol="full_file"), source unverified_conservative_default
       (qualification records are never read here)
  -> ModelRequestProfile: attempt._developer_request_profile -> edit_protocol "full_file"
  -> attempt mutation authority: D1 completeness gate (attempt._completeness_gated_operation): no complete exact
       current source shown -> REPAIR_WITH_PATCH mandatory (attempt._patch_required_files)
  -> retry policy: retry_strategy.handle_attempt_failure -> retry_policy.force_strategy_transition(
       has_fallback_model=bool(ctx.chain)) after 2 consecutive no-progress attempts: closes the primary's targeted
       budget, sets fallback_targeted_requested; decide_attempt_mode -> FALLBACK_TARGETED
  -> call: attempt._enter_developer_model -> model_transition.fallback_incompatibilities(patch_required_files=[...])
       -> "edit protocol full_file" in FULL_FILE_ONLY_PROTOCOLS -> _substitute_for_required_patch: no later chain entry
       -> _raise_fallback_incompatible -> QualityGateFailure(fallback_incompatible) -> recovery: terminal (PRD-017
       documented policy: no route back to the primary's unused budget)
```

TRACED side facts:
- `model_qualification.required_capabilities(cfg, "developer", m)` always includes `anchored_edit_protocol`
  (`_ROLE_REQUIREMENTS["developer"]`). Under `runtime_profile: production`, `fallback_incompatibilities` refuses
  any fallback that is not QUALIFIED.
  - Together: in production, a Developer fallback that reaches the whole-file-only check has **already passed**
    `anchored_edit_protocol`. The FAIL case is refused on its own reason first.
- `preferred_edit_protocol` has exactly two consumers. One is `agent.py:1523`, which turns a requested patch into a
  whole file for `full_file`/`full_file_text`. The other is the `FULL_FILE_ONLY_PROTOCOLS` check.
  - A patch is the sentinel `<<<KRIYA:EDIT>>>` SEARCH/REPLACE text protocol, not a native tool call. The
    `small_native_tools` label does not require native tools.
- `KNOWN_MODEL_PROFILES["qwen3.6:35b-a3b-q4_k_m"]` declares `native_tool_calls=True`. The live qualification of the
  pinned identity measured native tool calls UNAVAILABLE.
  - So simply widening the known-profile match to derived tags would assert a capability the pinned runtime was not
    measured to have.

## 4. HYPOTHESES

| # | Hypothesis | Status |
|---|---|---|
| H1 | Missing qualification fact: the fallback was never shown to support patches | **Rejected** (MEASURED): `anchored_edit_protocol` PASS in its own current record |
| H2 | Routing defect: the fallback is picked or refused by the wrong rule | **Rejected as stated**: selection and refusal follow PRD-017 exactly, and the refusal is correct for the profile it is given |
| H3 | Intentionally conservative fallback: a binding with no declared and no known capabilities is treated as whole-file only (MODEL-001 fail-closed) | **True by design** (TRACED) |
| H4 | **Evidence-linkage gap.** Two independent identity systems disagree. Qualification proves anchored edits for the exact runtime + settings identity. Capability resolution keys on the binding's name and never consults qualification. A pinned derived tag (the PROVIDER-CONTRACT-001 recommended `kriya model pin` practice) can therefore never inherit measured evidence, and no operator-declared capabilities exist for it | **CONFIRMED** (reproducer test 1) |
| H5 | The retry policy schedules a fallback that is deterministically dead: `force_strategy_transition`/`decide_attempt_mode` use `has_fallback_model=bool(ctx.chain)`, never "a fallback that can serve this attempt" | **CONFIRMED** (TRACED + reproducer test 2) |

## 5. DISCRIMINATING CHECK

- **Reproducer test 1** (`..._although_its_qualification_passes_anchored_edits`). It uses the live names and
  capability blocks and a QUALIFIED Developer record with the live case statuses.
  - The fallback resolves to `unverified_conservative_default` / `full_file`.
  - The record shows `anchored_edit_protocol` PASS.
  - The required case set for that role includes `anchored_edit_protocol`.
  - This separates H1 (false) from H4 (true).
- **Reproducer test 2** (end to end, real pipeline, only the runtime port scripted). It reproduces modes
  `full_set, targeted×3, fallback_targeted` and failures `anchored_edit ×2, no_progress_retry ×2,
  fallback_incompatible`, with the `REPEATED_ACTION` transition, `phase: call`, `selected: null` and the **live
  reason string verbatim**. The fallback is sent nothing.
  - Stated difference from the live runs: here attempt 2 also reaches the model; live, attempt 2 was already
    refused.

## 6. CONFIRMED ROOT CAUSE

The terminal is the correct fail-closed refusal of a profile that is wrong for the evidence Kriya holds:
1. **Capability resolution never consults the qualification record.** It keys on the binding's name. The live
   fallback is a pinned derived tag with no `capabilities` block, so it resolves to the conservative whole-file
   profile although its own current Developer qualification PASSed the anchored edit protocol (CONFIRMED).
2. **The retry policy hands no-progress attempts to the fallback by chain presence alone.** It is not told whether
   the fallback can serve the attempt. A patch-only attempt therefore spends its strategy transition on a fallback
   that is deterministically unservable, and the run ends (CONFIRMED).

## 7. Answers to the required questions

1. **Why does the qualified qwen3.6 fallback resolve to `unverified_conservative_default`?**
   - Its `llm_chain` binding has no `capabilities` block.
   - Its name is the pinned derived tag, which is not an exact (case-folded) key of `KNOWN_MODEL_PROFILES`.
   - Resolution then falls to the conservative default, and qualification is not an input to it (TRACED, MEASURED in
     the live log, CONFIRMED by test).
2. **A missing qualification fact, a routing defect, or intentional conservatism?**
   - Not a missing qualification fact: the fact exists.
   - Not a routing defect: routing applies PRD-017 correctly to the profile it receives.
   - It is intentional conservatism (H3) applied to an identity whose patch capability *was* measured. The defect is
     the missing link between qualification evidence and capability resolution (H4), plus an operator configuration
     that declares no capabilities for its only fallback.
3. **Earliest deterministic point Kriya can know the fallback is unusable.**
   - At **run start**: from configuration alone, resolution yields `full_file` for that binding. That is
     deterministic and known before any model call, so "this fallback can never serve a patch-only attempt" is
     known then.
   - Per attempt: whether the target is patch-only is decided by D1 before the request is built.
   - Today the incompatibility is evaluated only at the call (`phase: call`), after the strategy transition already
     closed the primary's targeted budget.
4. **Why are failed attempts commonly spent before the dead path?**
   - The attempts before it are ordinary primary retries. Most are anchored edits that do not apply.
   - `ANCHOR_CONTEXT_NOT_ESCALATED` refusals (no model call) count as no progress, so two of them trigger
     `force_strategy_transition`, which requests the fallback because the chain is non-empty (H5).
   - So the run ends after as few as 1–2 Developer generations, with the primary's full-set budget unused (MEASURED
     live; reproduced).
5. **Any currently configured compatible alternative?** No. `llm_chain` has one entry (MEASURED).
6. **Could the fallback legitimately support the patch protocol, and what would prove it?**
   - Yes, by measured evidence. Its current Developer qualification (policy `/8`, response protocol
     `kriya_sentinel_v1`, this exact runtime and inference settings) PASSes `anchored_edit_protocol`,
     `full_file_raw_content` and `malformed_output_recovery`.
   - The deterministic proof that exists today is that record. What is missing is a rule that lets a current,
     non-STALE QUALIFIED Developer record with `anchored_edit_protocol` PASS establish the *edit protocol* of that
     exact identity. Native tool calls are UNAVAILABLE for it, so tool-dependent fields must not be inherited from
     the base tag's known profile.
   - Alternatively, the operator config can declare the fallback's `capabilities` explicitly (SEC-009 operator
     authority). Qualification `/8` would then require the cases the declared protocols enable.
7. **Is conversion to whole-file authority ever safe here?** **No, not as a conversion.**
   - D1 (FILE-INTEGRITY / CONTEXT-EDIT-PROTOCOL) allows a whole-file answer only with authoritative, complete, exact
     current source shown. Converting a patch-only attempt to whole-file authority would let a model rewrite a file
     it never saw whole.
   - For 12 of 21 refusals the target is 34–396 KB, beyond any whole-file request in a 32,768-token window.
   - For the other 9 (4–9 KB targets) a whole-file request is possible only if the complete source is **shown**
     first. That is a context-packaging change, not an authority conversion, and whether it fits is UNKNOWN until
     measured per request.

## 8. PREDICTED FIX EFFECT (not implemented; options for authorization)

| Option | Change | Predicted measurable effect on the reproducer and the live census |
|---|---|---|
| A (evidence link) | Capability resolution derives the edit protocol for a binding with no declared capabilities from its current QUALIFIED Developer record (`anchored_edit_protocol` PASS → patch-capable text protocol; native tools only if those cases PASS) | Reproducer test 1: the fallback resolves to an evidence-backed source with a non-full-file protocol. Test 2: the fallback is selected and called (`selected` = the fallback, ≥1 fallback request). Live: the 21 terminal `FALLBACK_MODEL_INCOMPATIBLE` stops become fallback attempts. Their outcome is UNKNOWN (model behaviour) |
| B (policy) | The retry policy treats a fallback that cannot serve the attempt's patch-only targets as absent (`has_fallback_model` = a *compatible* fallback exists). The transition then routes to the primary's remaining budget instead of the dead path | Test 2: no `fallback_targeted` mode, and the primary's full-set budget is used. Live: the same 21 runs continue on the primary. This changes PRD-017's documented terminal policy, so it needs an explicit owner decision |
| C (config) | Declare `capabilities` for the fallback in the operator config | Same effect as A for this deployment only; no code change |

Each option must re-run the reproducer and the live-mechanism assertions first (rules §6). A mutation must show
that removing the change brings back `unverified_conservative_default` / the call-phase refusal.

## 9. Remaining uncertainty

- Whether the fallback, once called, would have produced correct patches for the 21 runs is UNKNOWN. That is model
  behaviour, and the raw responses were not recorded (LR-R1-M1 closes this for future runs).
- Whether showing complete source for the 9 small-target runs fits the fallback's allocation window is UNKNOWN until
  measured per request.
