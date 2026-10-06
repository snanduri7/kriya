# GR-R0: final Graphify readiness audit

**Branch:** `feature/lr-r1-gr0`, from `8670883` (the P3-D/A3-A5 evidence tip; certified executable `bcb0c1d`).
**Commits:**
- `e776953`: RETRY-NO-INFORMATION-GAIN fix.
- `e9020ea`: NEGATIVE-MODEL-AUTHORITY fix.
- `15c990d`: tests and evidence.
- `55c61f9`: survivor test.
- `c0a3e91`: D8 test premise update. **This is the certified executable revision of this increment**; its production code is identical to `55c61f9`.
- An evidence-only commit follows.

No live model. No Graphify candidate generation. Nothing pushed or merged.

**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED.

## Verdict

**GR-R0: BLOCKED. `GRAPHIFY READY = NO`.**

Criteria 1-8 and 10 hold. The blocker is new and outside both fixes: **REQ-DERIVE-ISSUE-NARRATIVE** (§4). It needs an owner decision.

## 1. A5 retry investigation: RETRY-NO-INFORMATION-GAIN (CONFIRMED, fixed `e776953`)

**Evidence.** MEASURED from the M1 store of run `20261006T175548-2b834bc0`, s1. These fields were identical across attempts 1-3:
- model: qwen3-coder;
- requested operation: `repair_with_patch`;
- operations: `[anchored_edit]`;
- target: `PetTypeFormatter.java`;
- capability digest: `7dc1f207`;
- revision: `3aa21641`.

| Attempt | Model call | Context | Failure | Recovery decision |
|---|---|---|---|---|
| a1 | yes | level 0, no loci | INDENTATION_STYLE_MISMATCH | targeted |
| a2 | **no** | level 1, loci 55-63, all inside the shown member span | ANCHOR_CONTEXT_NOT_ESCALATED | **targeted, same model** |
| a3 | **no** | same | ANCHOR_CONTEXT_NOT_ESCALATED | fallback_targeted |

Every attempt recorded `information_gain = UNKNOWN`: a2 and a3 had no Developer request to compare.

**Answers:**
- Did attempt 2 make a candidate possible that attempt 1 could not? **NO.**
- Did attempt 3 make one possible that attempt 2 could not? **NO.**

**Producer (TRACED).**
- `attempt._decide_edit_capabilities` raises the refusal when (capability digest, model, requested operation) equals that of the last edit-protocol failure. That means the identical request would be resent.
- `retry_strategy.handle_attempt_failure` counted the refusal as one no-progress attempt.
- `retry_policy.force_strategy_transition` fires only at `STRATEGY_TRANSITION_AFTER_NO_PROGRESS = 2`. So one more identical refused attempt was always spent before the fallback.

**Same family as Graphify (CONFIRMED from the decision producer and digests, not from text).** The offline Graphify replay (`graphify-canonical-6e0d711-diagnostics/replay/edit_protocol/after.json`) shows the same pattern:
- a3 and a4 were both refused, with the same model and the same digest `4a975bc686`;
- the transition came at a4.

The generic synthetic reproducer (`tests/_edit_protocol_harness.py`, no benchmark code) shows the same shape (`handover/evidence/gr0/retry_trajectory_prefix.json`):
- **with a fallback:** two identical refusals, then the fallback at a5;
- **without a fallback:** three identical refusals, then the no-progress stop.

**Fix.**
- A refusal changes strategy at once: `force_strategy_transition(immediate=True)`, to the fallback when it can serve, else the full-set route.
- The same refusal key (path, digest, model, requested operation) seen again after the strategy change (`GenerationState.edit_refused_capabilities`) reaches the no-progress limit at once. That is a typed `no_progress` stop.
- Not special-cased by language, file or benchmark.
- A retry whose capability changed is untouched (negative control).
- Edit authority is unchanged.

**Post-fix (MEASURED, `retry_trajectory_postfix.json`):**
- **with a fallback:** one refusal, then the fallback at a4, which repairs;
- **without a fallback:** one refusal, then one identical repeat, then a typed stop at a4.

**Behaviour change to note.** A model-"missing" requirement that names existing tests is now eligible for named-test closure: those tests are run and may close it, where before nothing was built. This is intended (deterministic evidence outranks a model claim).

**One pre-existing test updated.** `test_lr_r1_p1_fallback_incompatible_reproducer` pinned the live trajectory, including the duplicate refusal: modes `full_set, targeted×3, full_set`. It now pins `full_set, targeted×2, full_set` and transition reason `NO_PROGRESS`, with `refused_before_inference` true. The test's intent is unchanged: the unservable fallback is skipped, the primary's full-set route continues, and the run ends on `no_progress`.

## 2. NEGATIVE-MODEL-AUTHORITY (CONFIRMED, fixed `e9020ea`)

**Pre-fix (MEASURED, `negative_model_authority_prefix_e776953.txt`, verdict injected through the B2-a `_ledger` seam):** 5 of 9 controls failed.
- An exact B2 PASS with model "missing" was **VIOLATED**.
- A GENERAL B3 HUMAN_ACCEPTED with model "missing" was **VIOLATED**.
- Model "missing" with no evidence was VIOLATED.

**Producer (TRACED).**
- `record_requirement_verdicts` recorded "missing" as the VIOLATED outcome. Only "satisfied" was turned into UNVERIFIED (FS-1B).
- `requirement_outcomes` applied trusted closure only to an UNVERIFIED/SATISFIED verdict.

**Fix.**
- Both model verdicts are recorded UNVERIFIED. The model's verdict, reason and detail are kept as provenance.
- A pre-fix VIOLATED verdict record read back (for example, on resume) is treated the same.
- Deterministic or human-bound evidence decides the outcome:
  - counter-evidence makes it VIOLATED;
  - closure makes it CLOSED_BY_EVIDENCE or HUMAN_ACCEPTED, and outranks a model "missing".
- An unrefuted model "missing" still blocks success under every policy. Fail closed, never success.
- Deterministic contradiction semantics are unchanged.

**Controls, post-fix (10/10, `tests/test_gr0_negative_model_authority.py`):**

| Control | Outcome |
|---|---|
| B2 exact PASS + model negative | CLOSED_BY_EVIDENCE, not blocked |
| B3 GENERAL PASS + approval + model negative | HUMAN_ACCEPTED, not blocked |
| B2/B3 FAIL + model positive | VIOLATED, blocks |
| No acceptance evidence + model positive | UNVERIFIED (blocks only under the production policy, unchanged) |
| No acceptance evidence + model negative | UNVERIFIED, blocks under every policy |
| Deterministic violation + model positive | VIOLATED |

**Five PRD-020 lineage tests relabelled.** They asserted the old veto label (VIOLATED). Each still asserts that the requirement blocks; the label is now UNVERIFIED.

**Residual (TRACED, not changed; owner decision).** The attempt-level `goal_spec_compliance` gate still turns a model "missing" claim into an attempt failure and a retry, before terminal acceptance runs:
- `attempt.py` around line 10524;
- acceptance runs only at the terminal gates.

It cannot create VIOLATED or SUCCESS. It can spend retries on a correct candidate, so the run ends NO_SUCCESS (fail closed).

## 3. Graphify no-model preflight (MEASURED, current code; `graphify_preflight_e9020ea.json`)

**Set-up.**
- The real direct workflow, with nothing sent to any model.
- The frozen goal (`f96bf5a3…`) and the frozen `engine.py` (`67f99bd`, 331,064 bytes, 6,318 lines, revision `1158691a…`).
- The historical single-target plan.
- The v5 Developer bindings: 32,768-token window, 16,384 output tokens, the qwen3.6 fallback.
- The canonical run's real attempt-1 answer.

**Fidelity.** The attempt-1 capability digest `a79464a2…` equals live run #2's attempt 1.

| Item | Value |
|---|---|
| FULL FILE REQUIRED | **NO** (D1 full-file authority false) |
| FULL FILE INCLUDED | **NO** |
| AUTHORITATIVE EDITABLE CONTEXT | available: exact windows 5357-73, 5381-97, 5401-17 (loci 5365/5389/5409 from goal-quoted lines) |
| AUTHORIZED OPERATION | `anchored_edit` |
| TARGET MEMBER(S) | the C# call-site branch of the extractor walk around lines 5360-5410 (historical replay; the goal's two quoted lines) |
| FINAL REQUEST ESTIMATE | 31,965 bytes: 12,799 tokens (default counter); about 15,149 at qwen3-coder's qualified 2.110 bytes/token |
| SERVED CONTEXT | 32,768 (budget window); request capacity 16,096 |
| REQUEST FITS | **YES**, about 6% headroom at the conservative ratio. Retry requests are about 7.9K tokens. Live run #2: 13 Developer calls, no context refusal |
| P3-A FINAL AUTHORITY | present (every exact span's bytes are in the fitted request) |
| P3-B NEEDED | NO (request construction needs none; it applies only if a stitched anchor occurs) |
| P3-D NEW MEMBER REQUIRED | **NO** (insertion null; the goal asks to change existing branches; a diagnostic inline fix of 5 lines suffices, §4) |
| NEW IMPORT REQUIRED | **NO** (MEASURED for the diagnostic fix; INFERRED for all fixes) |

## 4. Graphify acceptance and authority preflight

| Item | Value |
|---|---|
| B2 PYTHON LAYOUT SUPPORTED | YES (root package, `pyproject.toml`) |
| ACCEPTANCE CAN RUN AGAINST CANDIDATE | YES, MEASURED (see below) |
| TRUST SURFACE PROTECTABLE | YES (see below) |
| CLAIM CLASS | GENERAL |
| B3 REQUIRED | YES |

**Acceptance can run (MEASURED).** Kriya's real B2-a runner on a scratch clone of `67f99bd`. Dependencies are installed from `pyproject.toml` into the project venv; run #2's gates ran this way too.
- **At base:** 3 failed (cases 1, 2, 5), 2 passed (controls 3, 4), ACCEPTANCE_VIOLATED. The suite is non-vacuous.
- **On a 5-line diagnostic reference fix** (scratch only, written from the goal text, never given to Kriya): 5/5, ACCEPTANCE_PASSED.

**Trust surface protectable.**
- The stored artifact lives outside the workspace.
- Kriya's own ini, `--noconftest`, a staged rootdir and importlib mode.
- FS-1A's in-process residual is disclosed (§5).

**Prepared, NOT approved** (`handover/evidence/gr0/graphify_prospective/`):
- `kriya_acceptance_graphify.py`: sha256 `ed8b90b1…`, covering REQ-15, written only from the goal's own reproducer and table and Graphify's public `extract()` API;
- `graphify_approval_TEMPLATE_unapproved.json`: `accept_suite_as_sufficient: false`.

No hidden-evaluator content was read. The run #2 evaluator output was not opened.

**BLOCKER: REQ-DERIVE-ISSUE-NARRATIVE (MEASURED; new; owner decision).** `derive_requirements` turns the frozen issue-report goal into **22** requirements, all GENERAL BEHAVIOR:
- markdown headings (REQ-2 "## Summary", REQ-6, REQ-14, REQ-17);
- a fenced code block (REQ-8), a command (REQ-9) and a table (REQ-10);
- the release notes (REQ-5);
- sentences that describe the defect itself. REQ-3 states that the call "never gets a `calls` edge", so a correct fix contradicts its literal text.

The production profile seals `requirement_unknown_policy`/`requirement_unverified_policy` to block. Any SUCCESS therefore needs all 22 closed:
- GENERAL requirements close only through B3;
- B3 would need the owner to approve a suite as sufficient for headings and for bug descriptions.

Since FS-1B, a model "satisfied" closes nothing. **Under current semantics, a canonical Graphify run cannot reach SUCCESS on any candidate.** It can only end NO_SUCCESS, which is fail closed, not false success.

Options for the owner, none of them taken here:
1. A versioned derivation contract that excludes markdown structure (headings, fences, tables) from requirements. This is generic, but changes the requirement set and digest. Narrative sentences remain.
2. A separate "operator-designated requirement set" input for issue-report goals: the goal unchanged, the requirement set chosen by the human.
3. Accept that Graphify can only measure "no false success", not success.

## 5. Known-issue relevance

| Item | Graphify relevance | Blocks | Why (evidence) |
|---|---|---|---|
| RETRY-NO-INFORMATION-GAIN | YES | NO | Fixed (`e776953`). The Graphify replay showed it, and the preflight now transitions at the first refusal |
| negative-model-authority (requirement level) | YES | NO | Fixed (`e9020ea`) |
| negative model at the attempt-level spec gate (new residual) | YES | NO | Fail closed: cannot create success or VIOLATED, but can spend retries on a correct candidate (§2) |
| **REQ-DERIVE-ISSUE-NARRATIVE (new)** | **YES** | **YES** | 22 GENERAL requirements incl. headings and bug descriptions; no success path under production policy (§4) |
| PLAN-R1 new-file redirect | NO | NO | Both canonical runs planned only the existing `engine.py` (MEASURED); the goal names that file |
| P3-D Python structural insertion unsupported | NO | NO | No new member is required (§3) |
| P3-D new-import authority limitation | NO | NO | No new import is required (§3) |
| B3-UX-1 trailing-newline binding | YES | NO | The goal must go by absolute-path `-f` (bytes as read); the template's `goal_sha256` was computed from the same file read |
| CLI-UX-1 relative `-f` path | YES | NO | Use an absolute path; fail closed |
| ENV-JVM-COLD-CACHE-1 | NO | NO | Graphify is Python |
| A5 null-input observation | NO | NO | Petclinic-specific candidate code |
| unattributed lessons model-call telemetry | NO | NO | Telemetry only |
| FS-1A residuals (candidate code in-process with the acceptance runner; venv built from candidate-declared dependencies) | YES | NO | Disclosed residual; requires a hostile candidate |
| Python packaging/environment (pip from `pyproject.toml`, `uv.lock` not honoured) | YES | NO | MEASURED: dependencies installed and both gates and acceptance ran (run #2 baseline; §4). Versions are not lockfile-pinned |

**UNKNOWN items on the Graphify path:** none.

## 6. Readiness criteria

| # | Criterion | Status |
|---|---|---|
| 1 | No open Graphify-relevant correctness defect | **NO**: REQ-DERIVE-ISSUE-NARRATIVE |
| 2 | No repeated-no-information retry defect on the path | YES |
| 3 | A model verdict cannot override stronger evidence | YES |
| 4 | No whole-file context | YES |
| 5 | A legal exact operation can be built | YES |
| 6 | The fitted request fits the served context | YES |
| 7 | No unsupported insertion or import authority is required | YES |
| 8 | Acceptance runs independently of candidate control | YES |
| 9 | B3 artifact ready for owner review | YES for REQ-15; meaningless for the other 21 (criterion 1) |
| 10 | The hidden evaluator stays external | YES |

## 7. Certification (`c0a3e91`)

| Gate | Result |
|---|---|
| Reproducers first | Part 1: pre/post trajectory JSON. Part 2: 5/9 failing before the fix |
| Focused | GR-R0 tests: 7 retry + 10 authority |
| Adjacent (retry/edit-protocol/P3/P4/recovery) | 395 tests: 394 passed; the 1 failure was the trajectory-pinning P1 reproducer, updated as described in §1 |
| Adjacent (requirements/acceptance/FS-1/B2/B3) | 499 tests: 494 passed; the 5 failures were the PRD-020 lineage tests, relabelled as described in §2 |
| Mutation | Run 1 at `15c990d`: 14/15 (survivor `refusal-key-ignores-model-and-operation`). Run 2 at `55c61f9` (production code identical to `c0a3e91`): **15/15 killed** |
| ruff | clean |
| pylint | exit 0 |
| Full suite | At `55c61f9`: 9,172 passed, 1 failed. The failure was D8 `test_a_named_test_whose_requirement_needs_no_closure_builds_no_validator[violated]`: its "never closable" requirement was a model "missing", now a model claim. The test now uses deterministic counter-evidence, keeping its intent (a VIOLATED requirement builds nothing). At `c0a3e91`: **9,173 passed, 0 failed, 0 errors** (536.6 s) |

## 8. Remaining uncertainty

- How a live model behaves on Graphify is not measured.
- The 6% headroom figure assumes qwen3-coder's qualified bytes-per-token floor. The live provider counts in run #2 were lower.
- Whether the owner wants option 1, 2 or 3 for REQ-DERIVE-ISSUE-NARRATIVE is an open decision.
