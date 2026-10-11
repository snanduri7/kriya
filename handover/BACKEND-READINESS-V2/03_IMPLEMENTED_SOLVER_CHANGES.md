# BACKEND-READINESS-V2 / 03 - Implemented solver changes

Branch `backend/readiness-v2` over baseline tag `kriya-backend-readiness-baseline-4of12` (c98b18d). Four product commits; every one has a reproducer
that fails on c98b18d, a mutation set run against a green baseline, and an original-symptom re-measurement on the real artifacts.

| # | Commit | Change |
|---|---|---|
| 1 | 87383a1 | PLAN-NAMED-TEST-IMMUTABILITY-001 |
| 2 | 6fb76ae | PLAN-TARGET-GROUNDING-001 (a): localization |
| 3 | 3cd85e2 | PLAN-TARGET-GROUNDING-001 (b)+(c): goal-named facts, delegating-shim plan check |
| 4 | 72cb2db | GRADLE-REGRESSION-DELTA-PER-TEST-001 |

## 1. PLAN-NAMED-TEST-IMMUTABILITY-001
- ROOT CAUSE: the plan-time test-immutability rule fired only for a TEST_IMMUTABILITY claim; a goal naming test files compiles to a pure REGRESSION_PRESERVATION entry enforced only at the terminal closure.
- CASES AFFECTED: S1_A (c3: correct fix, hidden 7/7, lost); S4_A/T5 in earlier cycles hit the blanket form.
- BASELINE REPRODUCER: `tests/test_backend_readiness_v2_plan_grounding.py` (fails on c98b18d); replay of the recorded S1_A c3 plan: valid before, `PLAN_EDITS_IMMUTABLE_TEST` after.
- CHANGE: `named_preserved_test_files(contract)` reads the sealed named-tests binding of entries whose single required claim is REGRESSION_PRESERVATION; `immutable_test_files` unions it with the blanket set.
- EXPECTED BENEFIT: removes one of two measured S1_A failure mechanisms (the other, an incomplete candidate in the base run, is a model matter).
- SAFETY: strengthens an existing plan gate to match a terminal rule; TEST_ADDITION and compound statements untouched (negative controls). LATENCY/TOKENS: none. ROLLBACK: revert the commit.

## 2. PLAN-TARGET-GROUNDING-001 (a) localization
- ROOT CAUSE: `Owner.member` declared only by a supertype resolved to nothing; every overload of a qualified match was credited in full, so 15 overloads of `Jsoup.parse` held ranks 1-15 and `Node.absUrl` ranked 17 against `LOCALIZATION_LIMIT=12`.
- CASES AFFECTED: T2 (Element.java planned in 4/4 recorded runs).
- BASELINE REPRODUCER: `tests/test_backend_readiness_v2_localization.py`; real jsoup index: `Node.absUrl` rank 17 -> 1, `StringUtil.resolve` rank 8.
- CHANGE: `CodeIntelligenceService.inherited_members` (structural extends/implements, nearest declaring level, bounded, visited set) credited as `inherited_member`; `fold_overloads` at the Planner boundary only (pool = 4x limit, one entry per qualified name with the overload count).
- SAFETY: localization is evidence, never write authority; Developer retrieval path unchanged. COST: index queries only. ROLLBACK: revert.

## 3. PLAN-TARGET-GROUNDING-001 (b)+(c) goal-named facts and shim check
- ROOT CAUSE: the Planner chose files from names only; it never saw that setup.py is a four-line shim or which supertype declares a method.
- CASES AFFECTED: T4 (setup.py planned in c3), T2.
- BASELINE REPRODUCER: `tests/test_backend_readiness_v2_goal_facts.py` (7 tests incl. a capture of the real enforce Planner request); real T4 workspace: recorded setup.py plan refused with `PLAN_TARGETS_DELEGATING_SHIM`, the earlier pyproject.toml plan accepted.
- CHANGE: `kriya/workflow/goal_facts.py` - a bounded (<=1800 chars) block labelled DERIVED_DETERMINISTIC (declaring supertype, package-name occurrences in manifests with metadata/comment/link lines and non-whole-token matches excluded, delegating shims); one new plan rejection (a shim edit whose delegate contains a goal name and is unplanned), registered in the closed reason-code tables with repair guidance.
- SAFETY: facts state what was found and never what to change; the rejection is bounded plan repair, not a gate weakening. COST: ~500 prompt tokens, a depth-2 manifest walk. ROLLBACK: revert.
- OWN BUG caught before commit: an unguarded `kernel.config` access failed 119 existing controller tests; fixed with the already-derived `kernel_config`.

## 4. GRADLE-REGRESSION-DELTA-PER-TEST-001
- ROOT CAUSE: per-test outcomes came only from Surefire console blocks or pytest output; a Gradle regression ended REGRESSION_UNATTRIBUTED with no retry, no test names and a message claiming replays that never ran.
- CASES AFFECTED: T3 (3 of the edited class's own tests failed with `PatternSyntaxException: Unknown character property name {Whitespace}`); any Gradle regression.
- BASELINE REPRODUCER: `tests/test_backend_readiness_v2_gradle_regression.py`; real T3 candidate XML (489 cases): 3 regressions confirmed with the exception text.
- CHANGE: a COMPLETE JVM TestExecutionReport supplies outcomes, counts and a structured Level-1 fingerprint when the console has no parser; failing cases carry a bounded exception head used in the Developer-facing evidence; the unattributed message says "no per-test evidence" instead of claiming replays. Incomplete, inconsistent, foreign-runner, unknown-status reports and failed gates naming no failing test stay fail-closed.
- SAFETY: consumes the XML FS-1A already treats as authoritative; no verdict is loosened (blocking is unchanged, attribution and feedback improve). COST: none new (XML already parsed). ROLLBACK: revert.

## Deliberately not implemented
S3_A ERROR->FAIL baseline flip (gate; cause INFERRED, parity check specified in 01 section 6); plan-form normalisation; reproducer-first decomposition; a canonical cross-role state object and failure ledger (no case lost a constraint across roles); model/role routing changes; retry or context increases.
