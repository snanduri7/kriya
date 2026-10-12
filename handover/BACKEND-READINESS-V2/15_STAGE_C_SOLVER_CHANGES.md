# 15 - Stage C: deterministic solver changes (docs 16 merged here)

Executable under test: branch `backend/readiness-v2` (Stage C working tree, see the commit list at the end). No cohort and no live model run in this stage. Slots used: TWO of three (change 1 goal-example verification, change 2 Developer context). Slot 3 is reserved (section 6). The S4_A semantic-safety invariant (section 5) is excluded from the cap by the owner.

## 1. Change 1 - goal-example verification (`kriya/workflow/goal_example_forms.py`)

Pure classifier over the RAW goal: every arrow line becomes `COMPILED` / `AMBIGUOUS` / `UNSUPPORTED` with provenance (line, character span, exact text, reason, bound requirement id). No model output, no task identifier, no hidden-oracle content (examples come only from the goal text; the replay never reads a hidden file). UNSUPPORTED and AMBIGUOUS never reject.

| Form | Example in the frozen goals | Classified on the 12 frozen goals |
|---|---|---|
| `java_instance_value` / `_raises` / `_no_throw` | S3_A `new TextStringBuilder("abab").lastIndexOf("") -> 4` | S3_A: 9 COMPILED |
| `java_matcher_pass` | T3 `assertThat("a b", equalToCompressingWhiteSpace("a b")) -> fails, used to pass` | T3: 4 COMPILED |
| `java_static_no_throw` | S4_A `JsonPath.compile(...) -> compiles (no exception)` | S4_A: 1 COMPILED |
| `java_static_value` / `_raises` | (enforced earlier by `example_oracle_java`) | classified for completeness |
| everything else | T2 prose, S4_B `BaseTest.JSON_DOCUMENT` argument, T5/S2_A Python lines | UNSUPPORTED (T5 9, S2_A 4, S4_B 3; T2 has no arrow line) |

REGRESSION WORDING (owner rule): `fails, used to pass` describes the broken baseline; the repaired expected outcome is PASS. The word `fails` is never encoded as desired behaviour (test asserts the rendered source does not contain it); a bare `fails`/`throws` is AMBIGUOUS; `passes, used to fail` is AMBIGUOUS. Contradictory examples of the same call become AMBIGUOUS; repeated identical examples (S4_B's "first/second/third call") do not.

Defect found on the way (MEASURED): `example_oracle_java._LITERAL` is a raw string with doubled backslashes, so it accepts a string escape only when spelled with TWO backslashes; a real goal literal such as `"my\tfoo"` (T3's own second example) is silently rejected by the existing compiler (safe direction, but it hides the example). The new module uses a correct literal pattern. The old pattern was left untouched (its enforced forms are unchanged).

Rendering: JUnit 5; every literal is emitted as ASCII Java with `\uXXXX` escapes (a raw U+000A/U+000D can never end a literal; surrogate pairs as two units); the `// kriya_requirement:` marker line carries ids only (an end-to-end check against the real S3_A tree found that provenance text on that line broke the B2-c marker grammar; provenance now has its own comment line; regression test added).

### Shadow replay (mandatory) - `gate-certification/shadow_replay.py`, results `gate-cert/shadow_replay.json`
Each subject was built from the frozen task tree, the goal's examples compiled against that tree, injected into the module's test source set and run with the project's own build tool (host JDK 17, offline), two runs each.

| Task | Subject | Role | MATCH | MISMATCH | Runs identical |
|---|---|---|---|---|---|
| S3_A | good (upstream fix, independent) | correct | 9 | 0 | yes |
| S3_A | unrelated change | correct | 9 | 0 | yes |
| S3_A | base | wrong | 4 | 5 | yes |
| S3_A | near-miss StrBuilder-only | wrong | 6 | 3 | yes |
| S3_A | near-miss TextStringBuilder-only | wrong | 7 | 2 | yes |
| S3_A | RECORDED Kriya candidate (v3a, incomplete StrBuilder) | wrong | 7 | 2 | yes |
| T3 | good (upstream fix, independent) | correct | 4 | 0 | yes |
| T3 | unrelated change | correct | 4 | 0 | yes |
| T3 | RECORDED Kriya GENUINE candidate (P5-T3-v2, oracle accept 0) | correct | 4 | 0 | yes |
| T3 | base | wrong | 0 | 4 | yes |
| T3 | near-miss Unicode-only / trim-only | wrong | 2 | 2 each | yes |
| T3 | RECORDED Kriya failing candidate (v2f, `Character.isWhitespace`) | wrong | 3 | 1 (the NBSP example) | yes |
| S4_A | all four arms | - | harness build error: json-path uses JUnit 4 and the probe is JUnit 5; no result | | |

False rejections of correct candidates: **0 of 60 example executions** (S3_A 2 subjects x 9 x 2 runs = 36; T3 3 subjects x 4 x 2 runs = 24). Wrong candidates rejected: all 8 wrong subjects with at least one MISMATCH, including both recorded Kriya failures; the recorded T3 failure is rejected by exactly the example the goal states (U+00A0). Sweep of all 31 distinct recorded goals under `kriya-m1-live`: the new forms occur only in S3_A, T3 and S4_A, so no other recorded GENUINE candidate exists to replay.

### Graduation (per form, not the parser)
| Form | Decision | Reason |
|---|---|---|
| `java_instance_value` | **ENFORCED** | S3_A replay clean (0 false rejections, wrong candidates rejected, deterministic); the sealed class compiles, passes the B2-c parser (9 cases) and runs green on the known-good tree / red on base (end-to-end check on the real tree) |
| `java_matcher_pass` | SHADOW (replay clean) | Replay meets every graduation condition, but the sealed JVM acceptance runner (`acceptance_jvm.py`) injects at the project ROOT (`src/test/java/...`) and runs `test --tests <Class>` at the root; it cannot place or run a class in the Gradle subproject `hamcrest/`. Enforcing it would be inert (INDETERMINATE), not wrong. Needs module-aware injection (section 6) |
| `java_instance_raises`, `java_instance_no_throw` | SHADOW | no frozen example, so no replay evidence |
| `java_static_no_throw` | SHADOW / UNVERIFIED | the only project with the form (JsonPath) uses JUnit 4; the probe could not run |
Shadow-classified lines are recorded in the derived-examples report (`shadow_examples`) and never enter the sealed class.

Mutation (focused): 16 mutants across changes 1, 2 and the commit invariant (regression wording, bare `fails`, contradiction, graduation set, unbound enforcement, escaping, no-throw, ranking, callee source, priority names, line cap [first SURVIVED, killed by a dedicated test], strategy stripping, facts injection, commit check removed, fail-open, direct gate removed): all killed.

## 2. Change 2 - Developer context (T2)
Measured defect (doc 14 s1/s7): derived facts reached the Planner but never the Developer (0/52); collaborator selection kept the first 12 signatures in file order and dropped `StringUtil.resolve`; no callee source reached the Developer.

| | Before | After |
|---|---|---|
| Collaborator `StringUtil` signatures (real T2 workspace) | `join`, `padding`, `isBlank`, ... (12 in file order), no `resolve` | `resolve(URL,String)`, `resolve(String,String)` first, then called/goal-named, then the rest; still capped at 12 |
| Callee source | none | both `resolve` overloads (`StringUtil.java:308-316`, `:324-...`) with label `DERIVED_DETERMINISTIC: the target member returns the result of this method (its last return)`, <= 3 callees, <= 40 lines and 2,400 chars each, elision stated |
| GOAL-NAMED FACTS in the Developer request | absent | the same block (same function `goal_facts_block`, authoritative goal section only, so the Planner's own strategy words never become facts), <= 1,800 chars |
The callee source is admitted before the collaborator signatures, so a tight budget drops signatures first. The facts block is cached per (goal, workspace, index state). Selection is by deterministic relevance rank (delegate callee incl. every overload > goal-named > called by the member > declaration order), not by a larger limit. The thin-delegate relation has ONE owner (`kriya/code_intel/delegation.py`), used by both the Planner facts and the Developer package (layering kept: `code_intel` imports nothing from `workflow`).
Reproducers (baseline-failing, 7 failed before the change): delegate survives truncation and declaration order; extra unrelated signatures do not change the selected facts; bounded source with provenance; no source for a non-delegate member; budget strict; Developer facts equal Planner facts; end-to-end real direct workflow where the actual Developer request carries both the facts block and the callee source.
Enforced-form limits (reviewer findings): (1) types resolve under any `src/main/java`, but the sealed runner injects at the project root, so a goal whose type lives in a Gradle/Maven SUBMODULE compiles to a class the harness cannot build; that is `ACCEPTANCE_HARNESS_COMPILE_FAILED`, never a verdict (no false rejection, but INDETERMINATE: the enforced instance form gives S3_A (single-root Maven) coverage and nothing on multi-module layouts); (2) only int/boolean/String/null expected literals are enforced for the instance form - long/float/double/char literals are AMBIGUOUS because `assertEquals` overload resolution depends on the unknown return type; (3) a trailing parenthetical is stripped only if it is a repeat note (`first call`, `second call, same JVM`, ...); any other (`(bug: should be 5)`) makes the line AMBIGUOUS; (4) a constructor/method that throws a checked exception fails to compile in the generated class (inert, not a verdict).
Known limit: when the owner file is small enough to be shown in full (tier `full`) no Code-Intelligence package exists, so the callee source is not added there (the facts block still names the callee and its location).

### One-file lock (traced; unchanged)
The lock is two layers: (a) per-file generation protocol - `DeveloperAgent` is called once per planned file with "Repair exactly one existing file ... do not touch or return content for any other file" (`agents/agent.py:1877`, `structured_contract` allows EDIT/FILE/NO_CHANGE for that path only); (b) plan scope - the files a subtask may touch are its `planned_files`. For T2 nothing deterministic proves that `Node.java` alone is insufficient: the defect location is not derivable without running the tests, and editing the caller is a legitimate design. **The lock is retained.** The Planner (which saw the delegate and both locations) remains the only place that can widen scope; the Developer now has the evidence to reason about the callee. Predicted effect on T2: UNKNOWN; the Planner chose `Node.java` twice with the facts in front of it, so a flip is not predicted from this change alone.

## 3. Priority-3 - explicit non-actions (owner list)
Not implemented: compile-failure symbol context, plan normalization, fix-then-test ordering, repair-equivalence.

## 4. Semantic-safety invariant (S4_A; excluded from the cap)
See section 5 for the proof and tests.

## 5. Final-path proof: no sandboxed candidate reaches the workspace without semantic safety
All writes of candidate bytes into the REAL workspace go through exactly one function, `terminal_commit.commit_terminal_candidate` (grep: callers are `commit_service.commit_verified_candidate` for the enforce path and `workflow.py` terminal apply for the direct/milestone path). That function now runs `semantic_safety_refusal(writes, workspace)` - the same rule, on the exact bytes about to be written, fail-closed - BEFORE the commit intent or any write (structural test pins the order). Earlier layers remain as feedback and defence in depth: staging guard (attempt.py, per write), direct pre-apply gate `_run_semantic_safety_gate`, enforce terminal gate `semantic_safety` (part of `commit_eligible`). LIMIT (reviewer finding): the commit-time check judges against the workspace file, so a run whose candidate IS the workspace (no sandbox) is not protected by it (the enforce path returns before committing in that case, and the commit function cannot tell). The in-place direct run (no sandbox) writes the workspace during staging; there the staging guard and the pre-apply gate (baseline = bytes the run first read) raise before success and the abort path restores the originals.
Routes covered by tests (each a real writer): whole-file write, anchored edit (production engine), `AuthorizedFileWriter` (the self-correction/tool route), shell redirect; repair (existing e2e: guard fires on attempt 1 and the repair); direct pre-apply (4 routes + e2e with the staging guard disabled); commit function itself with a hand-built eligible batch.

## 6. Slot 3 - justified? (reserved, nothing implemented)
Candidate from measured evidence: **module-aware example runner** (inject into the owning module's `src/test/java`, run `:module:test --tests`), which would make `java_matcher_pass` enforceable and put T3 under a goal-derived check (replay: would reject the recorded failing T3 candidate, accept the recorded GENUINE one). It touches the sealed JVM acceptance contract (`JVM_ACCEPTANCE_CONTRACT_VERSION`, injection path, runner command) and therefore needs its own design review. Recommendation: justified, but only after change 1 and 2 have focused-live evidence; not started.
