# 14 - Failure forensics (Stage B; Phases 4, 5, 10, 13 merged)

Read-only analysis of sealed evidence (`~/kriya-m1-live/backend-readiness-001/state/attempt-evidence/<run>/`, `records.jsonl` + zlib blobs). No Kriya run, no model call. Hidden-oracle files were opened ONLY to classify a finished failure (never fed to a solver). Evidence labels per `ENGINEERING_RULES.md` (MEASURED / TRACED / INFERRED / CONFIRMED / UNKNOWN).

Chain checked for every case (a "model limitation" label is allowed only when every earlier link is proven sound):
goal contract -> facts derivable -> facts derived -> facts SENT to the role that needs them -> plan -> candidate -> applied -> validated -> repair retains obligations.

## 1. T2 jsoup (runs P5-T2-v2f `20261012T001423-eed15889`, P5-T2-v3a `20261012T073006-a2aca7d1`)

| Link | Finding | Status |
|---|---|---|
| Goal -> derivable | `Element.absUrl` is inherited from `Node.absUrl` (Node.java:260), which returns `StringUtil.resolve(...)` (StringUtil.java:324, :308) | MEASURED |
| Derived | The GOAL-NAMED FACTS block states exactly that, including both `StringUtil.resolve` declaration lines | MEASURED (v3a Planner `model.request` seq 4) |
| Sent to Planner | yes | MEASURED |
| **Sent to Developer** | **NO.** The Developer request (v3a seq 38, 25,391 chars) has no GOAL-NAMED FACTS block, no body of `StringUtil.resolve`, and the collaborator list `#### Collaborator org.jsoup.internal.StringUtil (signatures)` contains 12 signatures (`join`, `padding`, `isBlank`, ...) **without `resolve`**, although the owner method calls it | MEASURED |
| Why `resolve` is absent | `kriya/code_intel/packing.py` `build_package`: `signatures = [... for s in store.by_path(...) if s.parent_id == type and s.is_callable][:12]` - the first 12 callables in file order; the callee the member actually calls is not ranked | TRACED (code) + MEASURED (output) |
| Plan | Planner again scoped s1 to `Node.java` only (and added s2 = write tests in `ElementTest.java`) although it was shown both `StringUtil.resolve` locations. Facts are explicitly "not instructions" | MEASURED |
| Developer freedom | system prompt: "Repair exactly one existing file ... do not touch or return content for any other file". The response protocol has only EDIT / NO_CHANGE: no way to say "the defect is in the callee". The Developer's own analysis says "absUrl delegates directly to StringUtil.resolve, which doesn't properly handle query-only relative URLs" and it then patches the caller | MEASURED |
| Candidate (v3a s1) | Node-level pre-processing. Run through the frozen ACCEPT oracle (this session, `gate-cert/logs/T2-s1-node-only-accept.txt`): hidden NodeTest.absHandlesRelativeQuery FAILS (`expected ...?foo but was ...`), StringUtilTest.resolvesRelativeUrls FAILS (fragment). Cause inside the candidate: `rel.substring(1, Math.max(1, hashIdx))` is `substring(1,1)` = "" whenever there is no `#`, so the query is always dropped | CONFIRMED (measured failure + traced expression) |
| Validated by Kriya | the candidate passed Kriya's compile + visible-suite gates (existing suite has no such case); the goal's own example 1 (`?page=2`) would have exposed it but no goal-derived check exists (doc 07 "shared gap": T2 examples are instance-expression forms, not compilable) | MEASURED |
| Run outcome | v3a: s1 completed, then s2 (`ElementTest.java`, no authoritative source in context) -> `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`, `stop_environment`, run FAILED and the (wrong) s1 candidate was rolled back. v2f: 7 attempts, all on `Node.java`, the same 34-line candidate staged twice (attempt 2 and 5), `stop_exhausted` | MEASURED |

**Question asked by the owner: did the incorrect implementation occur despite correct context?** No. CONFIRMED: the Developer did NOT receive the delegate facts or the callee, and was prohibited from editing the callee. The earlier wording in doc 09/10 ("Developer sees the delegation and still patches the delegate") is imprecise: the Developer saw only the call site and inferred the callee's deficiency from the call; it never saw `StringUtil.resolve`.

Classification (primary -> contributing):
- KRIYA architecture/context defect (generic): (a) collaborator signatures chosen by file order, not by what the member calls; (b) deterministic delegate facts reach the Planner but not the Developer; (c) no path for the Developer to report that the defect lies outside the planned file.
- Model limit (contributing, separate): the Planner ignored two deterministic locations; the Developer wrote an off-by-one that returns an empty query.
- Tooling/plan defect (v3a only): a test-writing subtask over a file with no authoritative source discards a finished, gate-passing s1. (It did not change the oracle verdict here: the s1 candidate was wrong anyway.)
- NOT a qualification defect: the gate demands the fix in `StringUtil.resolve` via `StringUtilTest`; the goal asks to "fix absolute URL resolution" for `absUrl`, and `Node.absUrl` + `StringUtil.resolve` are one resolution path. A Node-only workaround that is behaviourally correct would pass NodeTest and fail StringUtilTest. Recorded as a gate-strength limit (location-coupled oracle), not a validity defect; whether a correct Node-only fix should be accepted is an owner question and is NOT assumed.

Predicted effect of a fix for (a)+(b): the Developer sees `StringUtil.resolve` source. It would still be constrained by plan scope (Planner), so the T2 outcome change is UNKNOWN; (c) would be the part able to change it.

## 2. S4_B JsonPath (runs C2-S4_B-v2f `20261012T022352-1f5ffa09`, C2-S4_B-v3a `20261012T064017-0b79cc4f`)

Upstream fix (reverse of the task mutation, `CANDIDATE_S4_B.mutation.patch`): add `cleanWildcardPathToken()` to `FunctionPathToken.evaluate`, which removes the wildcard token that `CompiledPath.invertScannerFunctionRelationship` inserts into the cached path after a function parameter path is evaluated. (CONFIRMED by gate-cert known-good 930 tests / 0 failures.)

| Link | Finding | Status |
|---|---|---|
| v2f | never reached a Developer: 3 Planner calls, plan invalid, run FAILURE | MEASURED |
| v3a attempts 1/3 | Developer rewrote **`CompiledPath.java`** as a full file (wrong file for the final fix) | MEASURED |
| v3a attempt 2 | correct file (`FunctionPathToken.java`), correct application (STAGED 11/12 lines), **wrong hypothesis**: "`evaluateParameters` mutates shared `Parameter` objects by setting `evaluated=true`" | MEASURED |
| Attempts 4-8 | five consecutive compile failures: the model used `ParamType`, then `Parameter.Type`, `Parameter.PATH`, bare `PATH`/`JSON` - names guessed because `Parameter` / `ParamType` were not in any Developer request (grep of requests: `Parameter.java` 0, `enum` 0); the compile diagnostics ("symbol: variable Type location: class Parameter") were present but not turned into declarations. `ParamType` is a real enum (imported by `CompiledPath`, shown in attempt 1) | MEASURED |
| Attempts 8-11 | ANCHOR_NOT_IN_FILE (anchored edit refused), CONFLICTING_DEVELOPER_RESPONSE, INVALID_EDIT_PROTOCOL under the fallback model; `stop_exhausted` at the global attempt bound | MEASURED |
| Any attempt with the correct semantics? | **No.** No response ever mentions the wildcard token, `cleanWildcardPathToken`, or `invertScannerFunctionRelationship` as the thing to change | MEASURED (response scan) |

Owner taxonomy: intended edit wrong -> **model coding/diagnosis limit (primary)**; intended edit correct but application wrong -> not observed (the one refused anchored edit was already semantically wrong); semantics right but protocol mangled -> **not observed** (the three protocol failures at attempts 9-11 each carried the same wrong hypothesis). Contributing Kriya defect (generic, efficiency not outcome): attempts 4-8 were wasted on guessable-by-lookup symbols. Fixing it makes the loop cheaper; it does NOT supply the missing insight, so S4_B outcome change would be UNKNOWN. **Plan normalization is not indicated** (raw evidence shows no plan representation defect: v3a plan valid on first draft; v2f's invalid plans are earlier-cycle evidence, doc 08).

## 3. S4_A guard write-path coverage (owner decision 2026-10-12 item 6) - product change, not counted in the 3-change cap (S4_A guard is excluded)

Observation (TRACED): the exception-masking rule was called at exactly two sites, both inside `attempt.py` staging (`_reject_exception_masking`, anchored-edit branch and full-file branch). It judged what the Developer response staged, not the final candidate.
Routes into the isolated candidate and what saw them BEFORE this change:

| Route | Writer | Per-write guard | Evidence |
|---|---|---|---|
| Developer full-file / whole-file replacement (first attempt and repairs) | attempt.py full-file staging | yes | TRACED + existing e2e (guard fires on attempt 1 and on the repair) |
| Developer anchored edit (`repair_with_patch`) | attempt.py anchored staging | yes | TRACED + existing e2e |
| Opt-in self-correction micro-loop (`autonomy.self_correction_loop_enabled`, default false, false in every cohort config) | `self_correction.py:546` `AuthorizedFileWriter.commit_file` | **no** | TRACED: no reference to the rule outside attempt.py |
| TOOL subtask / shell command writing a planned Java file in the candidate | tool plugins / `ShellTool` | **no** (and not traceable to one writer) | TRACED: no reference |
| direct / milestone pre-apply boundary (`run_generation_workflow`) | n/a | staging guard only | TRACED |
| enforce terminal commit boundary | `TerminalGateService` | **none** | TRACED |

Original symptom reproduced pre-fix (MEASURED, deterministic, no model): the real terminal gate, fed a candidate that newly `catch (Throwable e)` produced by each of 4 writers (whole-file write, production anchored-edit engine, `AuthorizedFileWriter.commit_file`, a real `sh` redirect), reported `commit_eligible=True` for all 4.

Predicted fix effect: every route yields `commit_eligible=False` with `semantic_safety_gap` naming the file; controls (real fix, kept pre-existing clause, rethrow, non-Java, deleted file) unchanged.

Change: a final-state gate on the exact batch each commit boundary writes, one shared decision (`exception_masking.find_masking_in_batch`):
- enforce: new terminal gate `semantic_safety` (after static_analysis, before terminal_obligations), baseline = the real workspace file the write replaces (new file: none), fail-closed INDETERMINATE on any error;
- direct/milestone: `_run_semantic_safety_gate` before the checkpoint/apply, baseline = the bytes the run first read (an in-place run has already overwritten the workspace file).
The staging guard stays as early, actionable feedback to the Developer. The cohort path (`kriya generate`) is the enforce path (MEASURED: S4_A v2f has the 7 terminal `gate.result` records and `WorkflowController enforce run` in its log).

Verification: `tests/test_backend_readiness_v2_terminal_masking_gate.py` (24 tests): 4 routes x enforce gate, 4 routes x direct gate, controls, multi-file report, new file, delete/non-Java, indeterminate, gate order, an end-to-end direct run with the staging guard disabled (final gate alone refuses the widening, accepts an ordinary edit). Mutation: 9 mutants (not in commit_eligible, no workspace baseline, indeterminate-as-pass, gate skipped, report-first-only [initially SURVIVED, killed by the multi-file test], direct call removed, direct baseline none, direct baseline from the overwritten file) all killed. Adjacent: terminal-service, controller-enforce (updated gate matrix and event list), static-analysis, commit-failure/transaction, chaos-commit and attempt-evidence suites; state-machine tier 587 passed. Ruff and pylint exit 0. Full suite deferred to the Stage D canonical run.

Reviewer finding applied: an in-place enforce run (no sandbox, `candidate_root == workspace_path`) has no pre-run text, so a workspace-file baseline would hide a widening; the gate then judges with no baseline (any masking clause fails closed; test + mutant). Known cosmetic limit: when the batch cannot be materialized the enforce gate emits `semantic_safety: passed` while the commit is refused downstream (`VERIFIED_CANDIDATE_EVIDENCE_MISSING`).

Remaining limits (UNKNOWN, disclosed): the rule is Java-only and covers the Error/Throwable class only; `kriya fix`/other entry points were not separately traced; a write to a path the plan does not declare is never committed (so never judged) - by design.

## 4. T3 Hamcrest (P5-T3-v2f `20261012T003315-e4b13153`)
| Link | Finding | Status |
|---|---|---|
| Goal -> facts | the target `IsEqualCompressingWhiteSpace.stripSpaces` is a direct definition; nothing indirect | MEASURED |
| Sent to Developer | the goal text including the literal U+00A0 and U+2003 characters and "any Unicode separator or control character" is in the Developer request (18,576 chars) | MEASURED (character scan) |
| Candidate | `Character.isWhitespace(c)` loop; its stated analysis claims it "covers Unicode whitespace". `Character.isWhitespace(U+00A0)` is false (doc 07 JDK probe), so the goal's own NBSP example cannot pass | MEASURED + TRACED |
| Validation | visible suite has no NBSP case; goal examples are `assertThat(lit, equalToCompressingWhiteSpace(lit))  -> fails, used to pass` (not compilable by the example compiler); the sealed operator authority is deliberately feedback-free | MEASURED |
Classification: context was complete and correct; the implementation is wrong -> **model coding limit with a Kriya validation gap** (a goal-derived check would reject it deterministically and give the repair a concrete failing example). PITFALL for any compiler of these lines: "-> fails, used to pass" states the *regression*; the expected outcome is PASS. Reading it as expected-fail would falsely reject every correct candidate.

## 5. S3_A commons-text (C2-S3_A-v3a `20261012T071018-527b5fc6`; v2f 7 attempts)
Real v3a candidate replayed through the frozen ACCEPT oracle this session (MEASURED): hidden 4 tests / 2 failures, upstream 1891 / 2 failures, COMPAT pass (signatures identical). Cause (TRACED against the diff and the goal): TextStringBuilder was fixed (no-arg overload passes `size`, 2-arg overload special-cases the empty string); StrBuilder got only the one-line no-arg change (`size` instead of `size - 1`) and its 2-arg overload still clamps to `size - 1`, so goal example `new StrBuilder("abab").lastIndexOf("") -> 4` yields 3. The Developer's stated analysis ("the existing clamp logic ... will handle both cases") is the wrong step. Facts/plan/localization were correct (both files planned and edited, first-attempt diagnosis already named TextStringBuilder). Kriya's terminal `original_requirements` gate (model verifier) refused the commit and the run rolled back: SAFE_FAILURE, correctly. Classification: **model coding limit** (incomplete fix of a parallel class) + the same goal-example validation gap: 2 of the 9 goal examples fail on this candidate and an example check would have said so during attempt 1 of s2 instead of at the terminal gate. The earlier PRE_EXISTING fingerprint defect is fixed (doc 09) and not at issue here.

## 6. S4_A, T4, S1_A and controls
- S4_A (FALSE_SUCCESS in the frozen 12): cause is the oracle blind spot plus a missing semantic guard, both now handled (doc 11 section 2, section 3 above). Not re-forensed: no new evidence needed.
- T4 (variable) and S1_A (variable), T1, T5, S2_A (controls), T6, S6_A: successes in the frozen run; success paths were not re-forensed this stage (no failure to explain). The variability record in doc 07 stands; repeatability remains NOT ESTABLISHED.

## 7. Cross-case measurements (Phase 13 comparative findings merged here)
- GOAL-NAMED FACTS block reach (MEASURED over all 15 sealed v2f/v3a runs): at least one Planner request carries it in 7 of 15 runs (S2_A, S4_A, S4_B x2, T2 x2, T4; the other runs derived no fact); **Developer requests carrying it: 0 of 52** (a 52-request count; an earlier tally said 53). By construction: `goal_facts_block` is appended only to the Planner request (`workflow_controller.py:4662`).
- Collaborator signature selection is first-12-in-file-order (`packing.py:160`), so the callee a member actually calls is not guaranteed to appear (T2).
- Goal-example forms (MEASURED by reading the 6 non-trivial goals):
| Form | Goals | Lines | Compilable today | Notes |
|---|---|---|---|---|
| `new T(lit).m(lit[,int]) -> lit` (instance expression) | S3_A | 9 | no | simple, fully literal: SAFE to support |
| `assertThat(lit, factory(lit)) -> fails, used to pass` | T3 | 4 | no | outcome inverted vs the arrow (expected PASS) |
| `Type.read(CONSTANT, "lit") -> lit` with repeat markers | S4_B | 3 | no | constant lives in a test base class; repeated calls in one JVM |
| `Type.compile("lit") -> compiles (no exception)` | S4_A | 1 | shape recognised, result not literal | |
| prose `returns "x" but should be "y"` with multi-line `Jsoup.parse(...)` chain | T2 | 2 | no | UNSUPPORTED (must never reject) |
Predicted value: support for forms 1-2 would have rejected the real T3 candidate (NBSP) and the real S3_A candidate (StrBuilder example) during repair; S4_B and T2 would not change (their cause is upstream of validation).

## 8. Model-limitation stop rule (Phase 14) - verdict per failing case
| Case | Chain proven sound up to | First failing link | Label |
|---|---|---|---|
| T2 | goal, facts derived | facts not SENT to the Developer; callee absent; Planner ignored locations | **Kriya context defect** (not a model limit) + model errors |
| T3 | everything through candidate application | Developer implementation | model limit, gated by a missing goal-derived check |
| S3_A | everything through candidate application | Developer completeness of the parallel class | model limit, gated by the same missing check |
| S4_B | goal, facts, plan, application | Developer diagnosis (never reached the wildcard-token cause) | model limit; contributing wasted attempts from missing symbol context |
| S4_A | n/a (false success) | oracle blind spot | closed by the guard |
Only T3, S3_A and S4_B may carry the model-limit label, and T3/S3_A only together with the validation gap.

## 9. Ranking input for Stage C (not yet decided)
1. Goal-example verification, SHADOW first (graduation needs zero false rejections on all recorded GENUINE candidates and independent known-good controls): predicted flips T3, S3_A (outcome-bearing). 2. Developer receives the derived goal-named facts and the source of the callee a member delegates to, ranked by what the member calls (T2; outcome effect UNKNOWN, plan scope still binds). 3. Compile-failure symbol context (S4_B; efficiency, not outcome). Also noted: a test-writing subtask over a file with no authoritative source discards a finished gate-passing s1 (T2 v3a). Cap: at most three of these.

