# BACKEND-READINESS-V2 / 01 - Why Kriya is still 4/12 GENUINE

Baseline: tag `kriya-backend-readiness-baseline-4of12` = `c98b18d` (product tree identical to the certified executable `3844540`).
Branch: `backend/readiness-v2`. Written 2026-10-11 BEFORE any product change. Evidence root:
`~/kriya-m1-live/backend-final-closure-005/` (the `-c3` directories are the 4/12 run; `-final`, `-r2` and base directories are earlier runs of the
same frozen tasks). Labels follow `handover/ENGINEERING_RULES.md`: MEASURED (read from files/commands), TRACED (read from the producing code),
INFERRED (consistent, not proven), CONFIRMED (discriminating evidence), UNKNOWN.

## 0. Review of the brief against the evidence (CLAUDE.md "review first")

| Claim in the brief | Verdict | Evidence |
|---|---|---|
| 4 GENUINE / 8 SAFE_FAILURE / 0 FN / 0 FS, authority 0, corruption 0, leaks 0 | VERIFIED | `repair-003/FINAL_GATE_c3.md` (executable 3844540) |
| main = c98b18d, tree clean | VERIFIED | Phase 0 |
| "4/12 solver" | PRECISE ONLY WITH A CAVEAT | Two of the four GENUINE (T6, S6_A) are false-premise no-ops: **0 model calls** (`NO_MUTATION_REQUIRED`, decided by the operator-sealed verification authority on the untouched baseline). Code-writing GENUINE = **2 of 10** (T1, T5). The readiness metric is not changed here; it is reported so the gain target is read correctly. |
| "repeated iterations still produced 4/12" | PARTLY WRONG | Same frozen tasks, same model profile: 557035d run = 6/12, c3 = 4/12 (T4 and S1_A flipped GENUINE -> SAFE_FAILURE with no relevant code change). The score is dominated by run-to-run variance on two tasks plus six tasks that have **never** been GENUINE in any run. See section 2. |
| A/B success signal "at least 2 prior SAFE_FAILURE become GENUINE" | NEEDS TIGHTENING | T4 and S1_A are flippers (GENUINE in 2 of 3 and 1 of 3 earlier runs); a single run of either is not evidence. Live A/B must be judged on never-solved tasks (T2, T3, S3_A, S4_A, S4_B, S2_A) plus deterministic replay of the flippers' failing mechanism. |
| "add typed obligations / plan consistency gate / repair loop with exact contradiction" (Phases 6, 8, 9) | LARGELY ALREADY EXISTS | `plan_validation.py` (1276 lines, closed reason-code table), `planner_repair.py` (MUST PRESERVE / MUST FIX prompts, 2-repair bound), `obligations.py`, `planned_files[].preserved_references`, `PLAN_EDITS_IMMUTABLE_TEST`, `MISSING_GROUNDED_PRODUCTION_ARTIFACT`, Code Intelligence localization + structural evidence + owner candidates injected into the Planner request. The gaps are specific (section 4), not a missing subsystem. Phases 4/12 (a new canonical state object) are therefore NOT justified by this evidence. |
| Owner disposition 2026-10-10: "no fourth repair cycle; future change requires a newly demonstrated general correctness defect with deterministic reproduction" | COMPATIBLE | This workstream is the newer owner directive; every change below must still ship with a deterministic reproducer that fails on the baseline. |

No hard-stop condition is met. One item to watch: the S3_A finding (section 3, S3_A) concerns a **gate**. The brief allows touching a gate only with
external-oracle evidence that a rejected candidate was acceptable; section 3 records that evidence and how narrowly it applies.

Measurement limits (state once): (a) `planning-diagnostics` truncates `planner_request` at ~20,000 chars, so for large repositories (T2, T3, S3_A, S4_A,
S4_B) the structural-evidence / owners / localization sections of the real planner request are UNKNOWN; (b) one run per task per label.

## 1. Case outcomes and run history (MEASURED)

GENUINE count per task across all recorded runs of the same frozen task (base / r2 / final / c3):

| Task | Runs | GENUINE | Notes |
|---|---|---|---|
| T1 cachetools | 4 | 4 | stable |
| T5 jmespath | 4 | 3 | base run lost to an already-fixed defect |
| T6 commons-csv (false premise) | 3 | 3 | 0 model calls |
| S6_A toolz (false premise) | 3 | 2 | base run lost to an already-fixed defect |
| T4 python-slugify | 3 | 2 | c3 flipped: planner chose `setup.py` |
| S1_A xmltodict | 3 | 1 | c3 flipped: planner unit edited a protected test; base: a genuinely incomplete candidate |
| T2 jsoup | 4 | **0** | |
| T3 JavaHamcrest | 4 | **0** | |
| S2_A toolz packaging | 3 | **0** | |
| S3_A commons-text | 3 | **0** | |
| S4_A JsonPath 7.4.2 | 3 | **0** | |
| S4_B JsonPath tip | 3 | **0** | |

Consequence: the stable core is T1, T5, T6, S6_A (4/12). T4 and S1_A each convert in a minority-to-majority of runs. Reaching >=8/12 requires converting
at least two of the six never-solved tasks AND stabilising the two flippers. Plan validation fired in 28 of the 44 recorded runs (MEASURED, `generate.log`
"structured plan validation failed"); `REFACTOR_BASELINE_MISSING` fired in every T3 and S3_A run and `MISSING_GROUNDED_PRODUCTION_ARTIFACT` in every T2 run.

## 2. Per-case reconstruction (the eight non-GENUINE cases in full; the four GENUINE briefly)

Roles in every run (MEASURED, `generate.log`, `explain.json`): Planner = `qwen3.6:35b-a3b` (JSON mode); Developer = `qwen3.8:27b`, falling back to
`qwen3.6` after repeated failures (T2 attempts 4-8); run_verifier / spec_compliance / reviewer = `qwen3-coder:30b`. Developer temperature 0.7.
Routing is static per role, identical across task classes.

### T4 python-slugify - SAFE_FAILURE
1. Intended change: make `text-unidecode` a mandatory dependency (fresh `pip install -e .`).
2. Module: build metadata. `pyproject.toml` has `dependencies = []` (line 14) and the optional extra `text-unidecode` (line 29).
3. Models: Planner qwen3.6 (4396 in / 850 out tokens); 7 model calls in the whole run (Planner, spec/triage, Developer, run_verifier, spec_compliance, reviewer, terminal verifier).
4. Planner context (MEASURED, `planning-diagnostics`, 13,589 chars, complete): goal; a list of the 35 existing paths that includes `setup.py` and `pyproject.toml` by name only; structural
   evidence (test -> module references only); "existing owners" (`slugify/_legacy.py`, `slugify/slugify.py`, `tools/*`); localization candidates (ranked **test methods**, e.g.
   `test_legacy_goldens_and_differential`, `test_cyrillic_text`). **No manifest/config content, no manifest candidate.**
5. Plan: one unit, `setup.py` modify; acceptance criterion "text-unidecode is listed in install_requires in setup.py" typed `judgment`; verification `compile`.
6. Available, not supplied (MEASURED): `setup.py` is a 4-line shim whose own docstring says "Compatibility shim; metadata and build configuration live in pyproject.toml" and whose body is `setup()`.
   The token `text-unidecode` occurs in `pyproject.toml:29`.
7. First incorrect decision: Planner chose the conventional file, not the file that declares the dependencies. In the `-final` and base runs the same Planner chose `pyproject.toml` (the GENUINE runs).
8. Origin: **context retrieval/selection** (localization ranks tests, ignores manifests for an install/dependency goal) + **plan construction** (no plan-time check that the planned file can hold the change).
9. Detection: only at the terminal gate (sealed authority, REQ-1 external acceptance FAIL) after the Developer unit ran (7 calls, 273 s).
10. Repair: none possible - the run ends at the terminal gate; the Developer's write scope was an allowlist `[setup.py]` (MEASURED `explain.json` Q2), so it could not move to `pyproject.toml` even though `setup.py` was shown in full with the docstring.
11. Deterministic prevention: YES. A manifest-owner fact (shim detection + declaration-site lookup for the goal's package token) is cheap and exact.
12. Recurs: same family as T2 (planned file does not hold the target) and S1_A (plan contradicts a deterministic repository/goal fact).

### T2 jsoup - SAFE_FAILURE (0 of 4 runs GENUINE)
1. Intended change: `absUrl`/`attr("abs:...")` resolution for query-only hrefs and fragments.
2. Module: `Node.absUrl(String)` (Node.java:260) -> `StringUtil.resolve(String,String)` (StringUtil.java:324). **`absUrl` occurs zero times in `Element.java`** (MEASURED, `git show HEAD:`).
3. Models: Planner qwen3.6; Developer qwen3.8 then qwen3.6; 14 calls, 1902 s.
4. Planner context: complete request not recoverable (diagnostics truncated, UNKNOWN). Lexical localization over the frozen index, replayed on a scratch copy (MEASURED): ranks 1-15 are the 15 overloads of `Jsoup.parse(...)` (the goal's example code), `Node.absUrl` is rank 17, `LOCALIZATION_LIMIT = 12` (`graph_retrieval.py:100`) - so the correct definition is cut off.
5. Plan: s1 modify `Element.java`; s2 modify `ElementTest.java`; s3 verification. Attempt 0 rejected (`MISSING_GROUNDED_PRODUCTION_ARTIFACT`: ElementTest references 15 production files nobody owns); the repair "satisfied" it by attaching those 15 files as `preserved_references` to the **test** file (MEASURED, attempt 1 plan).
6. Available, not used: symbol definition site of `absUrl` (Node.java), call target `StringUtil.resolve`.
7. First incorrect decision: Planner targeted `Element.java`. (The Developer's own reasoning, generate.log line 1054: "likely in Element.java or inherited".)
8. Origin: **context retrieval** (overload flooding + inherited-member miss) -> **plan construction**.
9. Detection: never. 8 attempts of `ANCHOR_NOT_IN_FILE` x4 / no-change refusals on a file that does not contain the method; the prompt itself told the Developer "Left out for the context budget, not shown above: Element.java" (MEASURED, generate.log 1070, 1417).
10. Repair did not help: it kept the same write scope; the Developer was shown member snippets `Element.html`, `selectFirst`, `attr` (MEASURED, attempt-1 prompt) - none is `absUrl`.
11. Deterministic prevention: YES (symbol-definition lookup; "planned modify file contains none of the goal-named symbols while another file defines them").
12. Recurs: T4, S4_B (INFERRED).

### T3 JavaHamcrest - SAFE_FAILURE (0 of 4)
1. Change: restore trim + Unicode-whitespace collapsing in `IsEqualCompressingWhiteSpace.stripSpaces`.
2. Planner chose the right file (`hamcrest/.../IsEqualCompressingWhiteSpace.java`).
3. Planner 2 calls (attempt 0 rejected `REFACTOR_BASELINE_MISSING`: it typed a bug fix as `kind=refactor`); Developer qwen3.8, 1 attempt.
5. Plan verification for s1: `compile` only.
7. First incorrect decision: **Developer** wrote `replaceAll("\\p{Whitespace}+", " ").trim()` - `\p{Whitespace}` is not a Java regex property (MEASURED, candidate diff). Compiles (it is a string); throws `PatternSyntaxException` at runtime; the external oracle: hidden 2/2 fail, 3 of the class's own existing tests fail (489 run).
8. Origin: **model capability** (invented API) ... then a **control-plane defect** removed the correction path.
9. Detected by the full-regression gate in the same attempt; but `REGRESSION_UNATTRIBUTED` -> `stop_environment`, **no Developer retry** (MEASURED; TRACED to `validation_baseline.py:716-723`: `build_validation_outcome` takes per-test outcomes only from Surefire `Results:` blocks or pytest output; Gradle console output matches neither, so level2 is unavailable and `_whole_output_delta` has nothing to attribute).
10. Retry never happened; the 3 failing tests' text ("Unknown character property name {Whitespace}") would have identified the defect in one line.
11. Deterministic prevention: partly. The class's own test suite is the check; Kriya ran it but could not use the result.
12. Recurs: every candidate-caused regression on a Gradle project ends the run without a retry (S4_A, S4_B are Gradle too).

### S1_A xmltodict - SAFE_FAILURE (1 of 3 GENUINE)
1. Change: make streamed items equal to `parse()` output (text, strip, force_cdata, postprocessor).
2. Module: `xmltodict.py` (`_DictSAXHandler`). Planner localization (MEASURED, complete 14,185-char request): `xmltodict.item_depth`, `parse`, `endElement`, streaming tests; owners: `xmltodict.py`; structural evidence listed.
3. Planner 3 calls (2 repairs); Developer 14 calls total.
5. Plan: s1 modify `xmltodict.py`; **s2 modify `tests/test_xmltodict.py`**; goal text: "Every existing test in tests/test_xmltodict.py and tests/test_dicttoxml.py must keep passing unchanged."
7. First incorrect decision: Planner scheduled an edit of the goal-protected test file.
8. Origin: **plan construction** - and a **control-plane gap**: `immutable_test_files()` (`workflow.py:1050`) returns files only when the contract carries a `TEST_IMMUTABILITY` claim; this goal produces a *named-test* claim whose violation is detected only at the terminal gate (`requirements.py:2165` "named test(s) written or changed by this candidate"). The same plan rule fired for S4_A in the same cohort (`PLAN_EDITS_IMMUTABLE_TEST`) because that goal said "every existing test".
9. Detected at the terminal gate after both units ran.
10. External oracle on the exported candidate: hidden **7/7 pass**, regression 0, TAMPERED `tests/test_xmltodict.py` -> reject. **The product fix was correct.** Oracle and Kriya agree on the rejection (not a false negative).
11. Deterministic prevention: YES - mirror the terminal rule at plan time.
12. Recurs: S4_A (earlier cycle), T5 (earlier cycle: `PLAN_EDITS_IMMUTABLE_TEST` caught at plan time).

### S2_A toolz packaging - SAFE_FAILURE (0 of 3)
1. Change: ship the `tlz` package in the wheel (pyproject/MANIFEST).
3. Planner 3 calls, no Developer call, 150 s.
5. Plan (all three attempts): s1 modify `pyproject.toml` + `MANIFEST.in` (**the right files, first time**), then 3 verification-only subtasks.
7. First incorrect decision: not about content. Attempt 0 rejected: `PLAN_VERIFICATION_SCOPE_UNJUSTIFIED` (application-runtime verification not requested), `SEMANTIC_DEPENDENCY_EDGE_MISSING` (s4 requires `wheel_with_tlz` from s1 without a `depends_on` edge). Attempt 1: acceptance criterion `ac3` not covered by any subtask. Attempt 2: schema invalid (`verification.type = 'application_runtime'` is not an enum member).
8. Origin: **plan construction (protocol/form)** + **workflow granularity** (a two-file edit planned as 4 subtasks = 4x the surface for form errors).
9/10. The repair prompt's MUST PRESERVE list is 14 lines of generic bookkeeping (invariant ids, owner counts) (MEASURED); it fixed one error and exposed the next each round; non-convergence after the 2-repair bound.
11. Deterministic prevention: partly. The missing `depends_on` edge is derivable (`requires` string equals exactly one `provides`); `kind` for bug fixes is derivable.
12. Recurs: planner non-convergence also in T2-r2 and S4_A-final (3 of 44 runs ended on it; each cost the case).

### S3_A commons-text - SAFE_FAILURE (0 of 3) - **gate-side**
1. Change: `TextStringBuilder.lastIndexOf("")` and `StrBuilder.lastIndexOf("")`.
5. Plan (right): s1 `TextStringBuilder.java`, s2 `StrBuilder.java`, s3 verification (`REFACTOR_BASELINE_MISSING` repaired once).
7. The Developer's s1 candidates (attempts 2, 3, 5) all fix `TextStringBuilder` only - which is s1's whole scope. **MEASURED (surefire XML of the external judge, a3 candidate): the only 2 hidden failures are `StrBuilderTest.testLastIndexOf_String` and `LastIndexOfEmptyGoalTest.strBuilderExamples` - the StrBuilder half that s2 would have done. Both TextStringBuilder hidden tests pass; external regression 0.** The earlier classification "every candidate wrong on the hidden tests" is therefore confounded: the run never reached s2.
8. Origin: **control-plane gate**. After each s1 candidate the full-regression gate blocked on the same 16 DNS/localhost tests: baseline `ERROR`, candidate `FAIL` (`classify_level2_delta`: both non-passing, fingerprints of the failure text differ -> `CHANGED_FAILURE`, blocking). The tests are unrelated to the change. TRACED to `validation_baseline.py:872-950`.
9. Repair signal to the Developer (MEASURED, generate.log 196-245): "16 test(s) newly fail ... FAILED DnsStringLookupTest... (failure detail unavailable in raw output)". Unrelated tests, no detail -> attempts 4-5 were NO_CHANGE answers.
10. External-oracle evidence that a rejected candidate was acceptable for its scope: YES (a3). INFERRED (not proven): that PRE and POST ran under different containment/network conditions - the cause of ERROR vs FAIL is UNKNOWN; discriminating check specified in section 6.
11. Deterministic prevention: the comparison is a deterministic classification (a test that was not passing in the baseline cannot have *regressed*).
12. Recurs: any Surefire project with environment-dependent tests; `SUREFIRE-STABILITY-ENVELOPE-001` is the registered deferral and now has four consecutive measured instances.

### S4_A JsonPath 7.4.2 - SAFE_FAILURE (0 of 3)
1. Change: stop `StackOverflowError` on malformed bracket input in the path compiler.
5. Plan after repair: s1 modify `PathCompiler.java` (`preserved_references`: `InvalidPathException.java`, `FilterCompiler.java`); s2 **create** `MalformedPathTest.java` (attempt 0 had edited `PathCompilerTest.java` -> `PLAN_EDITS_IMMUTABLE_TEST`, repaired to a new file); s3 verification. The repair also hung two unrelated files on the new test file as `preserved_references`.
7. First incorrect decision: s1 candidate (+3 lines) did not fix the behaviour: external oracle hidden 4/6 fail - the same as the untouched base. Developer/model, but the s1 gate was `compile` only.
8. Origin: **candidate generation** + **workflow ordering/granularity**: the fix unit has no behavioural check of its own; the tests that would exercise it are written by the *next* unit.
9. Detected only when s2's targeted tests failed (6 attempts); the failure grounds to `PathCompiler.java`, outside s2's write scope -> stop `NO_AUTHORIZED_REPAIR_TARGET`; no reopen of s1 (registered observation `RECOVERY-UPSTREAM-OWNER-ROUTING-001`).
11. Deterministic prevention: the goal states two literal reproducer inputs; no deterministic tool turns them into a runnable check without model-written code.
12. Recurs: S4_B, T2 (test-writing unit planned after the fix).

### S4_B JsonPath tip - SAFE_FAILURE (0 of 3)
1. Change: `$..book.length()` must be repeatable (a cached `CompiledPath` is mutated on first evaluation).
5. Plan: s1 modify `CompiledPath.java` (128 lines, 4.6 KB; `preserved_references` `Parameter.java`, `EvaluationContextImpl.java`); s2 create a test; s3 verification.
7. First incorrect decision: UNKNOWN. The Developer saw `CompiledPath.java` in full (MEASURED: "1 unit(s), 0 omission(s)") and still produced `ANCHOR_NOT_IN_FILE` x3, `ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT`, one no-change, and a final +111/-4 rewrite that does not compile. INFERRED: the mutable state lives in a class the Planner listed only as a read-only `preserved_reference`, i.e. the write scope may be wrong. Discriminating check (not yet run): enumerate fields assigned during evaluation in the classes reachable from `CompiledPath`.
8/9/10. Origin undetermined between plan scope and Developer capability; 11 attempts, two failure-family budget resets, no progress.
11. Deterministic prevention: UNKNOWN.

### GENUINE cases (for completeness)
- **T1** cachetools: 1 file, 8 calls, 409 s. Plan validation fired 0 times.
- **T5** jmespath: 4 units, 18 calls; plan repair fired once (`PLAN_EDITS_IMMUTABLE_TEST`, `VERIFICATION_EVIDENCE_PATH_MISSING`) and converged.
- **T6, S6_A** (false premise): 0 model calls. `NO_MUTATION_REQUIRED` is produced because the operator-sealed verification authority already passes on the untouched baseline (MEASURED, T6 generate.log line 16). These runs exercise the false-premise gate, not the solver.

## 3. 12-row matrix

| CASE | OUTCOME | FIRST WRONG DECISION | PHASE | MISSING FACT? | FACT AVAILABLE? | MODEL ERROR? | TOOL COULD PREVENT? | REPAIR RECOVERED? | ROOT-CAUSE FAMILY |
|---|---|---|---|---|---|---|---|---|---|
| T1 | GENUINE | none | - | no | - | no | - | - | - |
| T2 | SAFE_FAILURE | Planner targeted `Element.java` (no `absUrl` in it) | context retrieval -> plan | yes: definition site | yes (`Node.java:260`, rank 17 of lexical localization, limit 12) | contributing (inferred location) | yes (symbol lookup) | no (same scope for 8 attempts) | **A: plan contradicts deterministic repo facts** (+ localization overload flooding) |
| T3 | SAFE_FAILURE | Developer used non-existent `\p{Whitespace}` | candidate generation | no | n/a | yes | partly (own test suite) | **no - retry was removed by Gradle attribution gap** | **B: gate-side signal** (Gradle per-test) |
| T4 | SAFE_FAILURE | Planner chose `setup.py` shim | context -> plan | yes: manifest ownership | yes (shim docstring; `pyproject.toml:14,29`) | planner | yes | no (allowlist `[setup.py]`; run ends at terminal gate) | **A** |
| T5 | GENUINE | none (plan repaired once) | - | no | - | no | - | yes | - |
| T6 | GENUINE (no-op) | none | - | no | - | no | - | - | - |
| S1_A | SAFE_FAILURE | Planner unit edits protected `tests/test_xmltodict.py` | plan | no (goal names the file) | yes | planner | yes (mirror terminal rule) | no (found after both units) | **A** |
| S2_A | SAFE_FAILURE | Planner form errors; right files from attempt 0 | plan form | no | yes | planner protocol | partly (derivable edge, derivable kind) | no (non-convergence at bound 2) | **C: plan form/decomposition** |
| S3_A | SAFE_FAILURE | none by a model for s1; gate blocked a correct unit | validation gate | no | yes (oracle: Text half passes) | no (s1) | yes (deterministic delta rule) | no (feedback = 16 unrelated tests, no detail) | **B** |
| S4_A | SAFE_FAILURE | s1 fix incorrect; s1 gated by `compile` only | candidate generation / ordering | no | n/a | yes | no | no (`NO_AUTHORIZED_REPAIR_TARGET`) | D: no behavioural gate on fix unit |
| S4_B | SAFE_FAILURE | UNKNOWN (scope vs Developer) | UNKNOWN | UNKNOWN | UNKNOWN | likely | UNKNOWN | no | UNKNOWN |
| S6_A | GENUINE (no-op) | none | - | no | - | no | - | - | - |

Role of the first wrong decision (non-GENUINE, 8 cases): Planner 4 (T2, T4, S1_A, S2_A), Developer 2 (T3, S4_A), control-plane gate 1 (S3_A), UNKNOWN 1 (S4_B). In 3 of the 4 Planner cases (T2, T4, S1_A) a deterministic repository/goal fact contradicted the plan and was available.

## 4. Hypothesis verdicts (H1-H12)

- **H1 safety/solver imbalance - SUPPORTED, narrowly.** 6 of 8 SAFE_FAILURE cases had a deterministic signal available before or during generation (T2, T4, S1_A at plan time; S3_A and T3 in the gate; S2_A partly). Late rejection of a plan-time-visible mistake: T4, S1_A (terminal gate after full units), T2 (never). Kriya's gates were correct in every case (0 false verdicts) - the imbalance is *where* the checks sit, not their strictness.
- **H2 model doing deterministic reasoning - SUPPORTED (3 cases).** File-of-declaration (T4), definition-owner (T2), protected-file rule (S1_A) were all derivable.
- **H3 quality not quantity - SUPPORTED.** T4: localization ranked 8+ test methods, no manifest. T2: 15 overloads of one symbol filled the 12-slot list and the real definition fell off. Context window is not implicated (T2/T4 prompts are far below the 32k window).
- **H4 plan is prose, not contract - PARTLY REFUTED.** Typed obligations exist and fire (`MISSING_GROUNDED_PRODUCTION_ARTIFACT`, `PLAN_EDITS_IMMUTABLE_TEST`). The gaps are specific: acceptance criteria can stay `judgment` (T4 `ac1`), `preserved_references` is accepted whatever file carries it (T2: 15 production files hung on a test file; S4_A the same), and the named-test rule has no plan-time mirror (S1_A).
- **H5 error propagation across roles - SUPPORTED.** T4: the Planner's file became the Developer's allowlist; the Developer saw the shim docstring and could not redirect. T2: the Planner's scope persisted through 8 attempts with the file marked "left out".
- **H6 repair repairs symptoms - PARTLY SUPPORTED.** Plan repair carries MUST PRESERVE/MUST FIX with deterministic codes (a sound design) but the preserve list is bookkeeping (S2_A) and the repair can game the checker (T2/S4_A preserved_references). Code repair: S3_A feedback is 16 unrelated tests with "detail unavailable"; T3 has no feedback at all.
- **H7 tooling too late - SUPPORTED for candidate gates, REFUTED for planning.** Localization, structural evidence and owner candidates are injected before planning; behaviour checks on the fix unit come after (S4_A compile-only; test-writing unit planned after the fix).
- **H8 routing static/role-mismatched - NOT TESTABLE on this evidence; do not swap.** Planner/Developer/reviewer bindings are fixed; the first wrong decision is Planner in 4 of 8, Developer in 2 of 8. Recorded for a later controlled single-variable experiment.
- **H9 format reliability - SUPPORTED as a secondary cost.** Planner: invented enum `application_runtime` (S2_A), invented tool names `python`/`pytest` (S1_A), `kind=refactor` for bug fixes (T3, S3_A: 7/7 runs), Developer: fabricated anchors (T2 x4, S4_B x3+), one INVALID_EDIT_PROTOCOL (S4_A). Protocol failures cost rounds in 5 cases; they ended the run only in S2_A.
- **H10 hidden-test gap - REFUTED as a cause here.** In the two cases with correct code (S1_A, S3_A scope) the hidden tests passed; the sealed hidden oracle is an *operator* artefact that real users do not have (relevant to external validity, not to these failures).
- **H11 over-constraint - ONE INSTANCE (S3_A) with external-oracle evidence; all others REFUTED.** T4, S1_A, T3, S4_A, S4_B candidates were rejected by the external oracle too.
- **H12 granularity - SUPPORTED.** S2_A (2-file edit as 4 subtasks), S4_A (fix then test; fix unit un-gated by behaviour).

## 5. Root-cause families (counts over the 8 SAFE_FAILURE cases)

| Family | Cases | Confidence | Generic? |
|---|---|---|---|
| A. Plan contradicts a deterministic repository/goal fact (target file does not hold the symbol / is a shim / is a protected file) | T2, T4, S1_A (3) | high (MEASURED + TRACED) | yes: symbol-owner, manifest-owner, protected-file |
| B. Gate-side signal: Gradle candidate regressions give no retry (T3); ERROR->FAIL baseline flip blocks a correct unit and feeds the Developer unrelated tests (S3_A) | T3, S3_A (2) | high for the mechanism (TRACED), medium for the S3_A environment cause (INFERRED) | yes: all Gradle projects / all Surefire projects |
| C. Plan form / decomposition (derivable edges, kind, over-decomposition) | S2_A (+ rounds in T3, S3_A, T2, S1_A) | high | yes |
| D. Fix unit has no behavioural gate; test-after-fix ordering; no upstream reopen | S4_A (+S4_B, T2 partly) | medium | yes but large |
| E. Unknown | S4_B | - | - |

Two additional general defects found on the way (not tied to a single failing case): localization overload flooding (T2) and `preserved_references` accepted on unrelated files (T2, S4_A, S4_B).

## 6. Open discriminating checks (UNKNOWN / INFERRED items, to be run before the corresponding change is trusted)

1. **S3_A cause of PRE `error` vs POST `FAIL` on the same 16 tests (INFERRED: PRE and POST ran under different network/containment conditions).** Check: capture the Surefire
   `Results:` text of those tests from (a) the baseline-capture invocation and (b) a candidate-gate invocation of the identical tree, and compare the container network policy used
   by each. If they differ, the defect is environment parity (fix the parity); if they are identical and still differ, the ERROR/FAIL split is genuine volatility and the delta rule is the
   place to act. Either way a deterministic reproducer is required before touching `classify_level2_delta`.
2. **S4_B location of the mutable state (UNKNOWN).** Check: enumerate fields written during evaluation in classes reachable from `CompiledPath`; compare to the Planner's write scope.
3. **T2 real fused localization ranking (INFERRED from the lexical replay).** The persisted `planner_request` is truncated at ~20,000 chars; the real top-12 list for the Planner is not recoverable.
   Check: replay the fused query (vector channel included) on a scratch copy of the index and report the rank of `Node.absUrl`.
4. **T3 repair potential (INFERRED).** That a second Developer attempt given the three `PatternSyntaxException` test failures would have corrected the regex is plausible, not proven; the Unicode-separator
   requirement (U+00A0 is not matched by `\s` or `Character.isWhitespace`) remains a model-knowledge risk.
