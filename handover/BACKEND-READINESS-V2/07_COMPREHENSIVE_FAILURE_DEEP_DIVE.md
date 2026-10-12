# 07 - Comprehensive failure deep dive (after the closure audit, HEAD 140c966 baseline of this cycle)

Authoritative qualification going in: GENUINE 7/12, SAFE_FAILURE 4 (T2, T3, S3_A, S4_B), FALSE_SUCCESS 1 (S4_A), FN 0. BACKEND SOLVER READINESS: FAIL. The
earlier 8/12 PASS is superseded and is not quoted as current readiness anywhere.

Process disclosure: the S4_A guard and the three fixes were implemented BEFORE this write-up was assembled (the analysis was done from the stored evidence
first, case by case, then each fix was built reproducer-first; this document records the analysis, the order of writing is not the order of discovery).
Evidence labels: MEASURED / TRACED / INFERRED / CONFIRMED. Evidence sources: `~/kriya-m1-live/backend-readiness-v2/{primary,cohort-002}/*-v2f`, the sealed
stores under `backend-readiness-001/state/attempt-evidence/<run>`, the planning diagnostics in each task workspace, the frozen external oracle outputs.

## Corrections to earlier statements (all MEASURED)
1. S3_A was recorded as "ERROR -> FAIL baseline flip". The structured reports of the sealed store show **ERROR -> ERROR** for all 18 tests, with the same
   exception type. The label that changed was the failure FINGERPRINT (see S3_A).
2. S3_A was recorded as "a seemingly correct candidate blocked by the gate". Both exported candidates (attempt 1 and attempt 5) FAIL the frozen oracle:
   `StrBuilder` (named by the goal) was never touched, so the gate's block was unactionable noise, but the candidates were not correct.
3. T2 was recorded as "right file, incomplete fix". The file is the delegate; the behaviour lives in its callee (see T2).

## Per-case sections

### T2 jsoup - SAFE_FAILURE (7 attempts, 910 s, 88k prompt tokens)
1. Goal: `Element.absUrl` / `attr("abs:href")` lose a query-only href and the `#fragment`. 2. Repo: jsoup (Java, Maven). 3. Roles: Planner + localization, Developer (qwen3.8),
   run_verifier, spec_compliance, reviewer. 4. Facts known before planning (TRACED): `Element` does not declare `absUrl`; `Node.absUrl` (Node.java:260) does and ends in
   `return StringUtil.resolve(baseUri(), attr)`; `StringUtil.resolve(URL,String)` (StringUtil.java:308) builds the result with `new URL(protocol, host, port, fixedFile)`, which drops the reference
   (the `#fragment`). 5. Supplied: the inherited-member fact (Element -> Node.absUrl); NOT the delegate fact. 6-8. Planner draft: s1 = `Node.java` modify with `StringUtil.java` listed in
   `preserved_references` (the Planner explicitly marked the callee "stays as it is"), s2 = edit existing `ElementTest.java`, s3 verification; one repair (MISSING_GROUNDED_PRODUCTION_ARTIFACT) converged.
   10-11. Developer was handed `Node.java`; candidates reimplemented URL resolution inside `absUrl` (attempt-5 export: 34 lines). 12. Delta: the final candidate breaks 8 existing tests
   (`ElementTest.testRelativeUrls`, `CleanerTest.*`, ... "expected http://example.com/bar/ but was <empty>"); the earlier A/B candidate fixed query-only but left the fragment (`StringUtilTest.resolvesRelativeUrls`).
   13. Gates: regression_test x2, operation_contract x2, attribution_rejected x2, diagnosis_mismatch. 17. Oracle: accept 1 / regress 1 (candidate); HIDDEN failing test is a StringUtil test.
   18. First objectively wrong decision (CONFIRMED by the oracle's failing test subject + the source read): the Planner's choice of `Node.java` as the mutation target with `StringUtil` preserved.
   19. Earliest prevention: planning, from a deterministic fact (the declaration's returned value is computed by StringUtil.resolve). 20. Deterministic replacement: yes - the delegate relation is read from the member source + index.
   Family: B (impact/reference analysis) + C (context). Fix: `delegate_facts` (see 08, fix 3). Not "a model-quality" failure: the model was given a relation that was derivable and withheld.

### T3 JavaHamcrest - SAFE_FAILURE (single attempt, 378 s; succeeded in the focused A/B)
Side by side (MEASURED): success candidate: `replaceAll("^[\p{Z}\p{C}]+|[\p{Z}\p{C}]+$","")` + `replaceAll("[\p{Z}\p{C}]+"," ")`; failed candidate: a loop on
`Character.isWhitespace`. The goal text contains U+00A0 and U+2003 and says "any Unicode separator or control character". A one-line JDK probe:
`isWhitespace(U+00A0)=false, isWhitespace(U+2003)=true, isSpaceChar(U+00A0)=true` - so the failed candidate cannot satisfy the NBSP example. Plans/context did not differ materially
(same Planner facts; the divergence is the implementation strategy chosen by the Developer: regex vs `isWhitespace`). The model's fix analysis said the matcher "correctly handles Unicode whitespace by using Character.isWhitespace()";
the goal-spec reviewer and the reviewer both PASSED it. The run stopped on the sealed external authority (REQUIREMENTS_UNRESOLVED, REQ-1 violated) with no retry: by design the operator authority gives no feedback to the solver.
The visible suite cannot expose it (no NBSP case); the goal's examples are `assertThat(..., matcher(...))` lines, none compilable by the example compiler (see "Shared gap").
First wrong decision: Developer implementation strategy (G). Prevention: a derived behavioural check from the goal's own examples (Kriya-side), not a model prompt. Classification: G + shared gap; it is the
variable case with M (sampling) as the divergence source. Gradle per-test path (Phase 5): the new structured per-test attribution is exercised deterministically by `test_backend_readiness_v2_gradle_regression.py`
(failing regression -> exact JUnit exception text -> blocking evidence rendered with the exception; fail-closed controls). It did not fire live here: the candidate passed the visible suite, so there was no regression to attribute. That path therefore still has no live evidence.

### S3_A commons-text - SAFE_FAILURE (7 attempts, 1087 s, 120k tokens)
Discriminating experiment (the one specified in 01, executed on stored artifacts, no model call): rebuilt `build_validation_outcome` from the stored gate blobs.
Two baseline gates of the SAME unchanged tree (seq 2 and 28) already differ: the 16 DNS-dependent errors read `UnknownHost <12-hex id>: <id>: Temporary failure in name resolution` and the id is the contained run's hostname
(9018ce6664da, 3af07587f48d, 09f035760c7f). Statuses are ERROR -> ERROR; PRE-vs-POST fingerprint differed only by that token. Result: 16 CHANGED_FAILURE, blocking, on every attempt; the diagnosis was
`regression_test`, low confidence, TEST_DEFECT, no file locations - an unactionable signal that cost attempts 1, 4, 5. CONFIRMED: environmental volatility in the failure representation, not a behaviour change.
Normalised at the comparison side (the fingerprint), not by fixing the hostname in containment: the hostname is produced by the OCI containment (a security-bearing mechanism whose arguments are identity-bound), the comparison is the layer that decides what counts as failure identity.
Separately CONFIRMED: the candidates are wrong: `StrBuilder` (named in the goal as shipped and requiring the same answers) was never edited; the oracle fails `strBuilderExamples` (expected 4, was 3) and the updated `testLastIndexOf_String`.
Retry quality (Phase 12): attempt 3's refused proposal has the SAME digest as attempt 1's staged candidate; attempts 6 and 7 re-propose attempt 5's digest. The fix does not make S3_A pass; prediction: S3_A stays SAFE_FAILURE unless the Developer also edits StrBuilder.
Family I (validation attribution) for the blocks; G + shared gap for the final wrongness (instance-expression examples `new TextStringBuilder("abab").lastIndexOf("") -> 4` are not compiled).

### S4_B JsonPath tip - SAFE_FAILURE at planning (3 Planner calls, 142 s, nothing written)
All three recorded drafts (MEASURED, planning diagnostics): s1 "Add a test case to DeepScanTest" with `DeepScanTest.java` action=modify, an EXISTING test the goal froze ("Every existing test must keep passing unchanged").
Validator: PLAN_EDITS_IMMUTABLE_TEST, with the exact path and "add new tests in a new test file". Repair 1: description changed to "Create a new test file DeepScanTest.java", path and action unchanged; repair 2: same, and another unit regressed
(SEMANTIC_CONTRACT_REGRESSION_REJECTED). Hypotheses (Phase 8): semantic misunderstanding no (the repair prompt had the path and the rule); representation variance no; duplicate/equivalent paths no (byte-identical path); contradictory obligations no;
**repair repeatedly producing the same semantic plan with new prose** yes (CONFIRMED). The test was not asked for by the goal at all (the Planner added it). S4_A hit the same code at attempt 0 and converged on attempt 2: 2 of 2 JsonPath plans put an edit on an immutable test.
Family D/E/K. First wrong decision: Planner (plan representation). Prevention: deterministic. Fix: `redirect_immutable_test_edits` (08, fix 2). Honest scope: this is a plan REWRITE authored by Kriya (modify -> create at a free sibling path), not canonicalisation, and the proposed "same canonical invalid plan -> do not spend another repair" short-circuit was not built.

### S4_A JsonPath 7.4.2 - FALSE_SUCCESS (closure audit)
`catch (Exception)` -> `catch (Throwable)` in `PathCompiler.compile`: the StackOverflowError is raised and swallowed (MEASURED). Family H (candidate semantic safety). First wrong decision: Developer (G); earliest prevention: candidate pre-flight (deterministic). Fix: `UNSAFE_EXCEPTION_MASKING` (08, fix 1).
Hidden-oracle limit stays: the oracle cannot see this class; the guard is the only protection.

### T4, S1_A (variable) - both GENUINE in v2f
Earliest-divergence analysis from the recorded runs (01 sections 1-2): T4's flip was the Planner choosing the delegating `setup.py` (addressed in the previous cycle, plan-target grounding; v2f plan had 0 repair rounds, 270 s). S1_A's c3 flip: "planner unit edited a protected test" (the same class the new redirect rewrites) and a genuinely incomplete candidate on other runs (v2f: 3 attempts, regression_test + diagnosis_mismatch before success). Variance classes (Phase 10): A (different target) and B (different strategy) dominate; neither is G (pure sampling) in the recorded divergences. Sampling configuration was not changed: both runs use the same profile; deterministic decoding was NOT evaluated and is recorded as a future routing experiment only.

### T6, S6_A (zero-model-call successes)
0 model calls, NO_COMMIT, tree unchanged; counted in the formal gate, excluded from code-writing reliability.

### T1, T5, S2_A (controls)
T1: 1 attempt, 456 s, 44k tokens. S2_A: 1 attempt, 390 s, 27k. T5: 3 attempts, 1261 s, 73k (2 refused proposals, then success).

## Shared gap: no behavioural check derived from the goal (MEASURED over the 10 non-trivial goals)
Arrow-form examples vs. what the Java example compiler accepts (`static Type.method(literal...) -> literal|raises`): T3 4 lines, 0 compilable (`assertThat(.., matcher(..))`); S3_A 9 lines, 0 (instance expression `new X(..).m(..)`); S4_B 3 lines, 0 (non-literal `BaseTest.JSON_DOCUMENT`);
S4_A 1 line, 1 recognised-shape but its "-> compiles (no exception)" result is not a literal; T2 0 arrow lines. Four of the five failures/false successes therefore have NO visible, goal-derived behaviour check, and the hidden authority gives no feedback. This is the common thread under G.
Not implemented in this cycle (cap on fixes; it is the highest-ranked deferred item in 08).

## Success-vs-failure comparison (Phase 3; correlation only, no causal claim)
| case | outcome | planner repair rounds | attempts | files staged | refused proposals | model calls (dev) | prompt tok | out tok | wall s |
|---|---|---|---|---|---|---|---|---|---|
| T1 | success | 1 | 1 | 1 | 0 | 1 | 44176 | 4932 | 456 |
| T4 | success | 0 | 1 | 1 | 0 | 1 | 21100 | 2518 | 270 |
| S2_A | success | 1 | 1 | 1 | 0 | 1 | 26917 | 4817 | 390 |
| S1_A | success | 0 | 3 | 1 | 1 | 3 | 71263 | 4583 | 728 |
| T5 | success | 2 | 3 | 2 | 2 | 4 | 73387 | 9202 | 1261 |
| S4_A | FALSE_SUCCESS | 2 | 2 | 2 | 1 | 3 | 72185 | 9125 | 800 |
| T3 | safe fail | 1 | 1 | 1 | 0 | 1 | 28205 | 3324 | 378 |
| T2 | safe fail | 1 | 7 | 1 | 3 | 10 | 87978 | 22431 | 909 |
| S3_A | safe fail | 1 | 7 | 1 | 4 | 10 | 120397 | 20985 | 1087 |
| S4_B | safe fail | 2 (exhausted) | 0 | 0 | 0 | 0 | 18758 | 4209 | 142 |
Observations: every genuine code-writing success finished within 3 attempts; both multi-attempt failures (T2, S3_A) ran to 7 attempts with 10 Developer calls and repeated candidates (S3_A: three repeats; T2: two attribution_rejected). The single-attempt failure (T3) is a wrong first answer with no signal.
Factors correlating with success: a first plan with <= 1 repair round; a target file that is the behaviour owner. Not established as causal.

## Cross-role provenance (Phase 11)
The Planner's `preserved_references` (T2: StringUtil) and `provides/requires` claims are MODEL_CLAIMED and reach the Developer inside the plan text; no deterministic fact contradicts them today (the delegate fact is the first such counter-evidence). The goal-named facts block is labelled DERIVED_DETERMINISTIC; localization candidates are structural. A model-claimed preservation does not reduce the Developer's authority, but it removed the callee from the Developer's context in T2. Not changed further (no case lost a deterministic constraint across roles).

## Fix-then-test ordering (Phase 9)
Affected: S4_B (s1 test precedes the fix s2), S4_A (test subtask s2 after the fix), T2 (s2 edits ElementTest after s1). In none of the recorded failures did a test-first ordering produce a test encoding a wrong behaviour; the S4_B defect is the immutable-test edit, not the order. Conclusion: no ordering rule is justified by the evidence; not implemented.
