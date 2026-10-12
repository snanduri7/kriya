# 08 - Root cause matrix, ranking and the four changes of this cycle

## Root-cause matrix (Phase 2). Y/N are MEASURED or TRACED unless marked (I) = INFERRED
| CASE | OUTCOME | FIRST WRONG DECISION | ROLE | PHASE / FAMILY | MISSING DETERMINISTIC FACT | AVAILABLE TO KRIYA / GIVEN TO MODEL | MODEL HAD FACTS / STILL WRONG | PLAN / GEN / VALID / REPAIR / ORDER ERROR | VARIANCE | GENERAL FIX POSSIBLE | CONFIDENCE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T2 | safe fail | mutate the delegate `Node.absUrl`, callee preserved | Planner | planning / B,C | "absUrl returns StringUtil.resolve(...)" | Y / N (before this cycle) | N / n.a. | PLAN yes; GEN yes (consequence); others no | low (3 runs same file) | yes (fix 3) | high for cause (CONFIRMED by the failing oracle test subject), medium for the effect |
| T3 | safe fail | `Character.isWhitespace` for a goal that shows U+00A0 | Developer | generation / G (+shared gap) | executable form of the goal's examples | Y (goal text) / Y | Y / YES | GEN yes | HIGH (passed once with `\p{Z}\p{C}`) | partial: example compiler for `assertThat` (deferred) | high |
| S3_A | safe fail | candidates leave StrBuilder untouched; gate noise hid the signal | Developer; gate (noise) | generation + validation / G, I, K | instance-expression examples; stable failure identity | Y / partly | Y (goal names StrBuilder) / YES | GEN yes; VALID noise yes; REPAIR repeats yes | n/a | yes for I (fix 2); G deferred | high (I), high (G) |
| S4_B | safe fail | plan MODIFYs a goal-frozen test, three times | Planner | planning / D,E,K | none missing (the path was named by the validator) | Y / Y | Y / YES (re-described, same path) | PLAN yes; REPAIR yes | n/a | yes (fix 2) | high |
| S4_A | FALSE_SUCCESS | widen a catch to Throwable | Developer | generation / H | n/a | Y / n.a. | n.a. | GEN yes; VALID gap yes | n/a | yes (fix 1) | CONFIRMED |
| T4 | genuine (flipper) | (earlier) delegating setup.py | Planner | planning / B,C | delegating-shim fact | fixed previous cycle | n.a. | PLAN (earlier) | medium | done | medium |
| S1_A | genuine (flipper) | (earlier) edit of a protected test; incomplete candidate | Planner / Developer | D,E / G | n/a | n.a. | n.a. | PLAN (earlier) | medium | partly (fix 2 class) | medium |
| T1,T5,S2_A | genuine | - | - | - | - | - | - | - | low | - | - |
| T6,S6_A | genuine no-op | - | - | - | - | - | - | - | none | - | - |

## Ranking (Phase 13). Score = cases affected x severity x confidence x generality, minus risk
| rank | candidate | cases | severity | causal confidence | generality | safety risk | size | live dependence | decision |
|---|---|---|---|---|---|---|---|---|---|
| 1 | S4_A guard (UNSAFE_EXCEPTION_MASKING) | S4_A | highest (false success) | CONFIRMED | any Java repair | low (fail closed, delta only) | small | none | IMPLEMENTED (mandatory) |
| 2 | Goal-derived executable examples for instance/matcher/constant forms | T3, S3_A, S4_B, S4_A | high | medium (INFERRED that it converts silent wrong answers into visible retries) | high | low-medium (new generated test class, existing boundary) | large | high | DEFERRED: highest-ranked next item |
| 3 | Container-id host not failure identity | S3_A (+ any contained Java/Maven project with a getLocalHost test) | medium | CONFIRMED | high | low | small | none | IMPLEMENTED |
| 4 | Immutable-test MODIFY -> new sibling file | S4_B, S4_A(att 0), S1_A class | high at planning | CONFIRMED (3 identical drafts) | high | low-medium (a plan rewrite by Kriya) | small | none | IMPLEMENTED |
| 5 | Delegate-return fact | T2 | high | high for the cause | high (Java member -> callee) | low (advisory text) | small | medium | IMPLEMENTED |
| 6 | Semantic retry equivalence (same digest re-proposed) | S3_A, T2 | medium | medium | high | low | medium | none | DEFERRED (evidence recorded) |
| 7 | Fix-then-test ordering | none demonstrated | - | none | - | - | - | - | NOT justified |
| 8 | Cross-role provenance changes | none demonstrated | - | none | - | - | - | - | NOT justified |
| 9 | Sampling / routing (T3 variance) | T3, T4, S1_A | medium | UNKNOWN | - | - | - | high | recorded as a future experiment |

## Phase-14 blocks

### Fix 1 - UNSAFE_EXCEPTION_MASKING (mandatory)
ROOT CAUSE: the candidate converted the goal's StackOverflowError into a recoverable InvalidPathException; the failing path still ran. AFFECTED: S4_A. BASELINE FAILURE: the exported/applied S4_A file was accepted by every gate. GENERAL FIX: `kriya/workflow/exception_masking.py`
(Java; catastrophic = Throwable / Error / known or `*Error`-named Error subtypes; a clause propagates only if its body ends in `throw <caught var>;` or `throw new <Error type>` with no return/break/continue/yield; delta only, per sorted caught-type tuple vs the run-start file), hooked at both
staging branches (anchored edit and full file) before the compile gate, on every attempt including repairs; failure type `unsafe_exception_masking` whose retry text names the rule. NO PERMISSION INPUT: no operator-authority source exists for permitting an Error swallow, so none is invented (fail closed).
SAFETY IMPACT: adds a refusal only. TOKEN/LATENCY: none. ROLLBACK: remove the two calls. TESTS: 32 (matrix of 11 reject / 8 allow shapes, pre-existing clause not by presence, comments/strings, test-side AssertionError, real S4_A hunk and the real recorded file bytes, three end-to-end workflow tests incl. the anchored-edit path and a repair that removes the widening). MUTATION: 15/15 + 4/4 call-site mutants killed.
A defect in my own first version was found by replay (a regression test's `catch (java.lang.Error e) { throw new AssertionError(.., e); }` was flagged) and fixed with a permanent test. REPLAY over 23 recorded Java candidates plus the applied S4_A tree: only the S4_A `PathCompiler` hunk fires. Reports: `unsafe_exception_masking` is UNCLASSIFIED in failure-category reports, like `prose_contamination` (the pinned-vocabulary test forbids silent additions; registering it is a separate reporting change).
Not covered: Python (`except BaseException`), Kotlin/Scala/Groovy catch syntax, and catch-and-swallow of Exception subclasses that mask a logic defect.

### Fix 2 - redirect_immutable_test_edits (S4_B)
ROOT CAUSE: the Planner repeated an existing frozen test path through every bounded repair. AFFECTED: S4_B, S4_A attempt 0. BASELINE FAILURE: recorded draft -> PLAN_EDITS_IMMUTABLE_TEST x3. GENERAL FIX: `plan_normalization.redirect_immutable_test_edits` in the existing normalisation pipeline (next to coalesce_same_file_owners):
a MODIFY of a path in the immutable test set becomes a CREATE of a sibling file that keeps the runner's naming convention (`FooTest.java -> FooRegressionTest.java`, `test_x.py -> test_x_regression.py`, `x.test.ts -> x.regression.test.ts`), numbered if taken (on disk or in the plan), same target for every unit that edited the same test, reason recorded on the entry, event `immutable_test_redirects` in `structured_plan_normalized`.
DELETE is not rewritten; unknown conventions are left to the validator; the result is fully re-validated. SAFETY: the rewritten plan only performs the action the goal allows; the terminal test-immutability closure still judges the real candidate. TOKEN/LATENCY: saves up to two Planner repair calls. ROLLBACK: remove the call. TESTS 19; MUTATION: 14/16 killed, 2 equivalent (suffix tuple order - disjoint suffixes; the empty-records early return only skips a rebuild and is observable only by identity, which a test now asserts).
REPLAY over 20 recorded plans: changes S4_A att 0 and S4_B att 0-2 (and T2 only under my test-set approximation: T2's goal has no frozen-tests claim, so it is NOT redirected in production).
Honest limit: Kriya now authors part of the plan. If the Developer writes a test that encodes wrong behaviour it is a candidate-written test, which the named-test closers already refuse as verification evidence.

### Fix 3 - container-id host name is not failure identity (S3_A)
ROOT CAUSE: the fingerprint included the contained run's own hostname. AFFECTED: S3_A (and any contained Maven project with a host-resolution test). BASELINE FAILURE: replay of the stored gates -> blocking, 16 CHANGED_FAILURE. GENERAL FIX: `validation_baseline._LOCAL_HOST_FAILURE` strips only a repeated 12-hex host in `UnknownHost[Exception:] <id>: <id>:`; BASELINE_COMPARISON_VERSION 2 -> 3 so a stored result from the old rules is not reused (part of the verification-policy identity; asserted by a test).
SAFETY: a different host, a different reason, a new failing test, or a single/unequal id all still differ (tested); the policy "baseline already broken => identical failures are pre-existing" is unchanged and NEVER accepts a changed or new failure. TOKEN: removes unactionable retry feedback. ROLLBACK: restore the version and drop the regex. TESTS 6; MUTATION 6/6 (one equivalent mutant removed by simplification).
REPLAY over 67 stored PRE/POST gate pairs (v2 and v2f stores): exactly 4 change, all S3_A (seq 28 - a baseline-vs-baseline pair of the unchanged tree - plus attempts 1, 4, 5): blocking True -> False, 18 PRE_EXISTING. Nothing else moves.

### Fix 4 - delegate-return facts (T2)
ROOT CAUSE: the delegate relation was not derived; the Planner chose the delegate and preserved the callee. AFFECTED: T2. BASELINE: real jsoup index, before: no line; after: `Node.absUrl (Node.java:260) returns the result of StringUtil.resolve(...); that method is declared at StringUtil.java:324, :308`.
GENERAL FIX: `goal_facts.delegate_facts` (Java; goal-named `Owner.member` with exactly one declaration; the LAST `return` expression of its own source calls `Type.method(` on a project type declaring `method` in another file), rendered in the GOAL-NAMED FACTS block (DERIVED_DETERMINISTIC, bounded, states where, never what to change). Overloaded names (a setup call such as `Jsoup.parse`, 15 overloads) are skipped - on the real index they otherwise produced noise lines.
SAFETY: advisory text in the Planner request only. TOKEN: +~60 tokens. ROLLBACK: drop the field. TESTS 17 + the end-to-end Planner request capture; MUTATION 13/14 (the Java-only filter is equivalent for Python because the return regex needs a `;`; kept as an explicit guard and now observable with a Python body that has one).
Whether the Planner uses it is the live question (focused run below).

## Deferred (open backlog; nothing implemented)
1. Goal-derived executable examples for instance expressions, matchers and constants (T3, S3_A, S4_B, S4_A) - highest value: gives the solver a visible behavioural signal.
2. Semantic retry equivalence: refuse to spend an attempt that re-proposes a previously refused/failed digest (S3_A attempts 3, 6, 7; T2).
3. Plan-form short-circuit "repair returns the same canonical invalid plan" (the brief's S4_B proposal; fix 2 removes the S4_B instance instead).
4. Register `unsafe_exception_masking` in failure reports; Python/other-language masking rules.
5. T3/T4/S1_A variance: sampling/routing experiment (not combined with architecture changes).
6. Fix-then-test ordering and cross-role provenance: no evidence of a defect.
