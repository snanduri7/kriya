# 10 - Phase 17 decision and the below-8 frontier

## Decision: the frozen 12 is NOT run this cycle
Phase 17 required: S4_A class blocked (yes), gates green (ruff/pylint 0, targeted suites green; canonical pytest below), AND at least one previously persistent failure causally improved, with no new FS/authority/corruption/leak.
MEASURED (09): S3_A's gate is now non-blocking as predicted, but the outcome is unchanged (candidate wrong); S4_B's redirect was not exercised (Planner variance); T2's target did not change. The S4_A guard removes the Throwable-catch path to GENUINE, so 8/12 would need two real flips and none is evidenced. Running the cohort would only sample variance, which the mission forbids.

## Current qualification (unchanged, authoritative)
GENUINE 7/12, SAFE_FAILURE 4 (T2, T3, S3_A, S4_B), FALSE_SUCCESS 1 (S4_A, class now blocked by a deterministic guard but NOT re-measured live), FN 0, authority 0, corruption 0, leaks 0.
BACKEND SOLVER READINESS: FAIL. SOLVER REPEATABILITY: NOT ESTABLISHED. The earlier 8/12 is superseded.

## What changed in the product (all general, no case ids)
1. UNSAFE_EXCEPTION_MASKING pre-flight (newly catching Error/Throwable without rethrow) - replay: 1 hit in 23 recorded Java candidates (the S4_A hunk).
2. IMMUTABLE_TEST_EDIT_REDIRECTED plan normalisation - replay: S4_A attempt 0 and S4_B attempts 0-2.
3. Local-container-hostname normalisation in failure fingerprints (BASELINE_COMPARISON_VERSION 3) - replay: only the 4 S3_A pairs of 67 change; live-confirmed.
4. Delegate facts in the GOAL-NAMED FACTS block (Java) - deterministic only; live effect on T2 unproven.

## Model-capability / unresolved ceilings (classification, not gate changes)
- T3: Developer reasons wrongly about Character.isWhitespace vs the \p{Z}\p{C} regex and the Reviewer passes it. Model behaviour.
- T2: Developer sees the delegation and still patches the delegate. Model behaviour + planning target choice.
- S3_A: candidate does not edit the class the goal names. Model behaviour.
- S4_B: Developer produces non-compiling Gradle edits and failing anchors across 9 attempts. Model behaviour.
- Planner variance (S4_B plan valid on one draft, invalid on others) is the reason repeatability cannot be established from a single run each.

## Highest-ranked deferred item (not implemented; cap of mandatory + 3 general fixes reached)
Goal-example compiler coverage: the Java example compiler accepts none of the goal examples for T3 (assertThat), S3_A (instance calls), S4_B (non-literal args); S4_A's one example has a non-literal result. Registered in BACKLOG_REGISTRY.
Other open: repair-equivalence (S3_A re-proposes identical digests on attempts 3/6/7); fix-then-test ordering and cross-role provenance (no justification found).

## Disclosed
TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001 remains an open known timing flake; it is not rerun.
