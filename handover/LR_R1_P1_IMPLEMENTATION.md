# LR-R1-P1 implementation: qualification-derived capabilities and patch-only fallback routing

**Branch:** `feature/lr-r1-p1`. It is based on `feature/lr-r1-m1` (certified M1 executable `ceb9943`, plus its
evidence-only commits `4a245e6`, `1d20a98`, `2049976`).
**Implementation commit:** `ec24d78`. **Not pushed, not merged.**
**Owner decisions applied:** Option (b) full-set routing; patch-only scope (run-wide incompatibility unchanged); the
size lower bound decides patch-only; 2026-10-05.
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED.

## 1. Root cause (from `LR_R1_P1_FALLBACK_INCOMPATIBILITY_INVESTIGATION.md`, CONFIRMED)

1. Capability resolution never read the qualification record. A pinned, derived fallback tag with no `capabilities`
   block therefore resolved to the conservative whole-file profile, although its own current Developer
   qualification PASSed `anchored_edit_protocol`.
2. The retry policy treated "a fallback is configured" (`bool(chain)`) as "a usable fallback exists". A patch-only
   attempt therefore spent its strategy transition on a fallback that could never serve it.

The live Stage-1 run `20261005T133256-87d73817` reproduced this exact trajectory on ceb9943
(`LR_R1_M1_LIVE_VALIDATION.md`).

## 2. What changed

### A. Capability derivation (`kriya/core/model_capabilities.py`)

- **Precedence.** The order is:
  1. explicit operator declaration;
  2. qualification-derived (new, `qualification_derived_profile`);
  3. known profile;
  4. conservative default.

  Levels 1, 3 and 4 are `declared_capability_profile` (the old logic, unchanged).
- **Evidence the derivation reads.** Derivation reads only the binding's own record for its exact runtime digest, its
  own Developer inference settings and the current qualification policy. That is `load_record` plus
  `record_is_current`, the same check `assess` uses.
  - A missing or STALE record, another runtime identity, other inference settings or another policy proves nothing.
    Levels 3 and 4 then apply, which is the conservative behaviour kept.
  - The overall QUALIFIED status is never read.
- **Mapping** (`CAPABILITY_PROOF_CASES`, version `qualification-derived/1`):

  | Capability | Proven only by |
  |---|---|
  | patch/text edit | `anchored_edit_protocol` PASS |
  | whole file | `full_file_raw_content` PASS |
  | native tools | `native_tool_calls`, `multiple_tool_calls` and `tool_argument_integrity` all PASS |
  | JSON | `structured_json` PASS |
  | multi-line JSON | `multiline_json` PASS |
  | streaming | `streaming_assembly` PASS |

  - FAIL, UNAVAILABLE and a case that was never run leave the capability at the conservative default.
  - A patch is the `text_markers` protocol, or `small_native_tools` only when the tool cases also PASS. A patch never
    implies tools.
- **No inheritance.** A derived (pinned) tag inherits nothing from its base tag's known profile.
- **Provenance.** `ResolvedCapabilityProfile.evidence` records the mapping version, role, runtime, settings and policy
  digests, the record key, the case statuses read, and which capabilities were proven. It travels into every routing
  record.
- **Fingerprint (design point for review, TRACED).** The runtime fingerprint includes the resolved capability profile
  (`kriya_protocol_identity`), and records are keyed by that fingerprint. A derived profile in the fingerprint would
  be a cycle: the record found by the fingerprint would change the fingerprint. So
  `kriya_protocol_identity` now reads `declared_capability_profile`.
  - For every configuration this is byte-identical to before. Derivation did not exist, so the old resolution *was*
    the declared one. No fingerprint changes and no record goes stale; the live fallback's record stays current and
    QUALIFIED (MEASURED in the reproducer).
  - Record lookup is still exact on runtime, settings and policy. **Qualification identity matching is not
    weakened.**
  - What changes: for a binding with a derivable record, the protocol actually used can differ from the
    fingerprint's protocol component. The difference is a deterministic function of that same identity's record and
    the mapping version, and both are recorded.
- **Consumers.** Every consumer resolves through the same function: `model_transition.resolve_request_profile`, the
  agent's `generation_protocol_for_model`, `attempt._lower_output_protocol_retry`, LLMClient's tool gate, and
  `required_capabilities`. A tripwire test pins that only the fingerprint reads the declared profile.
- **Consequence (MEASURED, intended).** `required_capabilities(developer)` for the derived profile adds the
  protocols it enables (`structured_json`, `multiline_json`). They PASSed, so the live fallback stays QUALIFIED.
- **D1 is unchanged.** A capability never grants write authority: the completeness gate still decides what may be
  written, and the call-time check is untouched.

### B. One compatibility decision for routing (`kriya/workflow/model_transition.py`)

- **The resolver.** `resolve_fallback_compatibility` is the single decision source. It walks
  `chain[min(retry_count-1, len-1):]`, exactly `resolve_fallback_model`'s ladder, never reordered, and classifies
  each candidate as COMPATIBLE, PATCH_INCOMPATIBLE, RUN_WIDE_INCOMPATIBLE or NOT_EVALUATED. For each candidate it
  records:
  - model and exact runtime digest;
  - qualification status;
  - capability source and derivation evidence;
  - reasons and patch-only files;
  - the file allocation.
- **The patch-only proof.** A target is patch-only for a candidate only when
  `ceil(bytes / bytes_per_token_ceiling) > request_capacity(candidate).tokens`.
  - `request_capacity(candidate).tokens` is the candidate's whole prompt room: budget window minus its own output
    reserve, framing and safety margin, never the nominal window.
  - The ceiling is the candidate's qualified ceiling, never below the provider-contract default of 8.0.
  - Every dispatch counter counts at least `bytes / ceiling` tokens (TRACED). So when the bound holds, the dispatch
    check would refuse any whole-file prompt: whole-file representation is impossible. Equal is not proof.
  - Otherwise the result is NOT_DETERMINED: nothing is pre-rejected, history is never used as proof, and call-time
    D1 stays the authority.
- **Availability** (`FallbackCompatibility.available`): every remaining candidate except PATCH_INCOMPATIBLE ones.
  Run-wide incompatible candidates count as configured, which keeps PRD-017's semantics.
- **Every routing consumer reads it.** `fallback_routing_for_context` / `fallback_routing_for_state`; the remaining
  `bool(ctx.chain)` is only the "configured" comparison used for the terminal decision. The consumers are:
  - `retry_strategy`'s `force_strategy_transition`;
  - `recovery_coordinator.conclude_attempt_failure` → `decide_for_state` (the ordinary FALLBACK_TARGETED rule);
  - `attempt`'s `decide_attempt_mode` and `_request_fallback_for_rejected_authoritative_target`;
  - `best_of_n`;
  - the workflow loop.

### C. Routing semantics (Option b, patch-only scope)

```text
PATCH-ONLY INCOMPATIBILITY:
compatible fallback absent
→ fallback treated as unavailable for that transition
→ primary full-set route may continue if valid

RUN-WIDE INCOMPATIBILITY:
existing PRD-017 terminal semantics retained
```

- **Strategy transition.** It requests the fallback only when one is available. The targeted budget still closes.
- **Retry decision** (`recovery_coordinator._route_around_patch_incompatible_fallbacks`): whenever a candidate is
  patch-excluded, the decision is recorded. That covers both outcomes:
  - routed on: `resulting_strategy` = full_set, targeted or fallback_targeted (a later compatible candidate), with
    `other_route_remained: true`;
  - terminal: `FALLBACK_MODEL_INCOMPATIBLE` with `other_route_remained: false`.

  The terminal is emitted only when the remaining route stops (`STOP_EXHAUSTED`) and the configured chain would have
  continued on a fallback (fallback-targeted or the reserved allowance). It is decided there with no model
  invocation.
- **Full-set escalation** (`attempt._select_developer_fallback(targets=architect files, allow_primary=True)`). A
  candidate that cannot serve this attempt's patch-only targets is skipped for this attempt; it is not a run-wide
  verdict and is never added to `incompatible_fallbacks`. Then:
  - the next compatible candidate is taken in order;
  - when all were skipped for that reason alone, the attempt stays on the primary (`primary_route`);
  - a run-wide rejection among them keeps PRD-017's terminal.
- **Fallback-targeted escalation** uses the implicated files and has no primary route.
- **Unchanged:** the call-time backstop (`_enter_developer_model` → `_substitute_for_required_patch`) and the
  NOT_QUALIFIED refusal of an exact identity under production.

### D. M1 evidence

- **Records.** `fallback.decision` phase `routing` carries the requirement, targets, candidates (all fields above),
  decision point, whether another route remained, and the resulting strategy. The escalation record adds
  `patch_rejected`. The run event is `model.fallback_routing`; a routing terminal also emits
  `model.fallback_incompatible` (phase `routing`).
- **Explain.**
  - Q8 shows each routing decision's requirement, candidates, decision point, `other_route_remained` and
    `resulting_strategy`.
  - Q9 adds `last_fallback_routing`.
  - Together they distinguish "fallback incompatible → continued with full-set" from "→ no recovery path remained →
    terminal".

## 3. P1 reproducer (flipped before the fix)

`tests/test_lr_r1_p1_fallback_incompatible_reproducer.py`, brought from investigation commit `08b5c1f`:
- **Unmodified at this base:** 2 passed (MEASURED), so it still pinned the defect at `ceb9943`.
- **Flipped, against the unfixed code** (with behaviour-neutral name scaffolding): 2 failed, on behaviour (MEASURED).
  The fallback resolved `unverified_conservative_default`, and the transition still requested the fallback.
- **Against the fix:** 2 passed (MEASURED). The trajectory is:

  | Step | What happened |
  |---|---|
  | Attempts 1–4 | `full_set`, then `targeted` ×3 on the primary |
  | Transition | `REPEATED_ACTION`, `fallback_targeted_requested: false`, routing `PATCH_ONLY_PROVEN` |
  | Attempt 5 | `full_set` on the primary: the escalation skipped the fallback (`primary_route`) |
  | Terminal | the primary's own `no_progress` |

  - The fallback received zero requests, and nothing was refused at the call.
  - Test 1: the live pinned fallback (live case statuses) resolves `qualification_derived`. It gets a patch
    protocol, no native tools, and JSON, multi-line JSON and streaming; its runtime identity is unchanged and it
    stays QUALIFIED.

## 4. Tests

`tests/test_lr_r1_p1_capability_routing.py` has 40 tests, all passing (MEASURED):

| Required coverage | Tests |
|---|---|
| patch PASS / FAIL / UNAVAILABLE / NOT_RUN | `test_the_patch_capability_comes_only_from_a_passing_anchored_edit_case` (×3), `test_a_patch_case_that_was_never_run_proves_nothing` |
| whole-file independent | `test_whole_file_and_patch_are_derived_independently` |
| native tools need their own cases; a patch never implies tools | `test_native_tools_need_every_tool_case_and_a_patch_never_implies_them` (×4) |
| missing / stale / wrong runtime / wrong inference identity → conservative | `test_no_record_keeps_the_declared_profile`, `test_a_stale_record_proves_nothing`, `test_a_record_of_another_runtime_identity_proves_nothing`, `test_a_record_of_other_inference_settings_proves_nothing` |
| explicit declaration wins; QUALIFIED alone gives no upgrade | `test_an_explicit_declaration_wins_over_derivation`, `test_qualified_alone_grants_no_capability` |
| a derived tag inherits nothing | `test_a_derived_tag_inherits_nothing_from_its_base_tag` |
| every edit-protocol consumer uses the same profile | `test_every_edit_protocol_consumer_reads_the_same_resolved_profile`, tripwire `test_only_the_fingerprint_reads_the_declared_profile` |
| fingerprint unchanged | `test_the_runtime_fingerprint_keeps_the_declared_profile` |
| size bound: large file PROVEN / small NOT_DETERMINED / history not proof / boundary / per-candidate / real allocation / call-time D1 authoritative | `test_a_file_too_large_…`, `test_a_file_that_could_fit_…`, `test_an_earlier_patch_required_attempt_is_not_proof`, `test_the_bound_equal_to_the_allocation_is_not_proof`, `test_the_same_file_can_be_patch_only_for_one_fallback_and_not_another`, `test_the_bound_uses_the_candidates_real_allocation_not_its_nominal_window`, `test_the_call_time_d1_check_stays_authoritative_when_not_proven` |
| Option-b cases 1–7 | `test_case1_…`, `test_case2_…`, `test_case3_…` (+ `…_a_valid_primary_route_is_never_terminal`), `test_case4_…`, `test_case5_…`, `test_case6_…`, `test_case7_…` |
| run-wide PRD-017 kept | `test_a_run_wide_rejection_keeps_prd017_semantics_in_routing`, `test_a_run_wide_rejection_at_escalation_stays_terminal_even_on_the_full_set_route`; existing `tests/test_prd017_fallback_transition.py` **unchanged** and passing (including `test_a_required_fallback_that_none_can_serve_ends_typed_with_primary_capacity_unused`) |
| order kept | `test_the_resolver_keeps_resolve_fallback_model_order` |
| M1 Q8/Q9 (real recorder, real store, real `explain_run`) | `test_q8_and_q9_explain_both_routing_outcomes` |

An adjacent batch of 623 tests passed before the commit (MEASURED). It covered:
- PRD-017, PRD-014, PRD-013, PRD-019;
- model capabilities, retry policy, the state-machine tier and best-of-N;
- PRD-031 coordinators, failure reporting and CONTEXT-EDIT-PROTOCOL;
- all `test_lr_r1_m1_*` and the P1 files.

## 5. Mutation campaign

`handover/evidence/lr-r1-p1/p1_mutation_campaign.py` → `p1_mutation_results.json` (log
`p1_mutation_campaign.txt`), at `ec24d78`, over the P1 test set. **28 targets, 28 killed:**

- **First pass:** 27 KILLED, 1 SURVIVED (`ignore-runtime-identity`).
  - The survivor was inert (MEASURED): it searched for `alias` at the record's top level, but records keep it under
    `fingerprint.alias`, so it never changed behaviour. This was a campaign-script defect, not a test gap.
  - The first-pass result is preserved.
- **Corrected re-run** (`p1_mutation_rerun_runtime_identity.py` → `p1_mutation_rerun_runtime_identity.json`):
  KILLED.

| Group | Mutants |
|---|---|
| Derivation (10) | QUALIFIED ⇒ all capabilities; UNAVAILABLE ⇒ PASS; ignore stale record; ignore runtime identity (corrected); ignore inference identity; patch ⇒ native tools; inherit base-tag capabilities; skip one edit-protocol consumer; explicit loses to derived; fingerprint reads derived profile |
| Routing (16) | configured == compatible; ordinary FALLBACK_TARGETED uses `bool(chain)`; only `force_strategy_transition` fixed; transition reverts to `bool(chain)`; ignore patch-only targets; remove call-time backstop; reorder fallback chain; full-set escalation invokes incompatible candidate; first incompatible stops the scan; unknown protocol treated as incompatible; primary full-set path skipped; terminal while a primary route remains; boundary equal is proof; nominal window instead of allocation; run-wide rejection becomes primary route; full-set escalation without targets |
| Evidence (2) | Q8 drops routing fields; routing decision unrecorded |

## 6. Gates

| Gate | Result |
|---|---|
| P1 reproducer | 2/2 PASS against the fixed behaviour (failed before the fix) |
| Focused P1 tests | 40/40 |
| I-2 (`test_lr_r1_m1_equivalence*.py`) | 12 passed (MEASURED) |
| Ruff | All checks passed |
| Pylint (`kriya plugins/core_tools tests`) | exit 0 |
| Full suite | 8700 passed / 0 failed / 0 errors |

## 7. Full suite

**8700 passed, 0 failed, 0 errors** (14 warnings), 792 s, at `ec24d78` with a clean tree (MEASURED,
`handover/evidence/lr-r1-p1/full_suite.txt`). M1's certification ran 8658, and this run adds the 42 new P1 tests;
no test was excluded or marked xfail. The run used `-n 8 --dist loadgroup`, once, in the background.

## 8. Out of scope / unchanged

- **P4 / P5:** not implemented. Their reproducers stay unchanged on `investigation/lr-r1-reliability` (`5576bc3`,
  `4283539`) and are not on this branch.
- **P2 / P3:** deferred.
- **Live confirmation:** none run. A tiny P1 live confirmation (the Stage-1 Run-2 goal on commons-lang) can be
  proposed. With derivation, the live qwen3.6 fallback now resolves patch-capable from its own record, so the live
  path would select it rather than bypass it.

## 9. For review

- **The fingerprint/derivation split (§2A):** I judge that it does not weaken identity matching; a reviewer should
  confirm.
- **Routing decisions are recorded at every retry decision while a candidate is patch-excluded,** including decisions
  where the route did not change (e.g. `targeted`). They are complete but repetitive.
- **The strategy transition reads the targets known at that moment** (the previous attempt's implicated files). The
  retry decision right after re-reads the resolver with this failure's attributed targets and is the deciding one.
