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

*(Sections for T3, S3_A, S4_A forensics, T4, S1_A, controls and the shared goal-example measurement follow in later slices.)*
