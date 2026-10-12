# 12 - Qualification coverage and complexity (Stage A, Phase 2)

Property reported here: **SUITE COVERAGE** - do the frozen 12 span enough engineering dimensions? Separate from GATE VALIDITY (11) and GATE STRENGTH (13). The frozen set and the >=8/12 threshold are NOT changed; empty cells are future-set gaps, not a reason to edit the 12.

Source: task `meta.json`/`goal.txt`, prep patches and oracle scripts (read-only). X = exercised by that task, (x) = partly, blank = not exercised. Cells are classifications from the goal text and the fix patch (TRACED), not run results.

| Dimension | T1 | T2 | T3 | T4 | T5 | T6 | S1_A | S2_A | S3_A | S4_A | S4_B | S6_A | Count |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Language: Python | X | | | X | X | | X | X | | | | X | 6 |
| Language: Java | | X | X | | | X | | | X | X | X | | 6 |
| Build: pip/pytest | X | | | X | X | | X | X | | | | X | 6 |
| Build: Maven | | X | | | | X | | | X | | | | 3 |
| Build: Gradle | | | X | | | | | | | X | X | | 3 |
| Direct definition localization | X | | X | | | | X | | | X | | | 4 |
| Delegate / indirection | | X | | | | | | | | | X | | 2 |
| Overload family | | | | | | | | | X | | | | 1 |
| Inheritance / override | | | | | | | | | (x) | | | | 0-1 (S3_A: two parallel classes, not a hierarchy) |
| Single-file edit | X | X | X | X | | | X | X | | X | X | | 8 |
| Multi-file edit | | | | | X | | | | X | | | | 2 |
| Bug fix | X | X | X | X | | | X | X | X | X | X | | 9 |
| Feature work | | | | | X | | | | | | | | 1 |
| Build / configuration task | | | | X | | | | X | | | | | 2 |
| False-premise / no-change task | | | | | | X | | | | | | X | 2 |
| Test-related goal (write/extend tests) | | | | | | (x) | | | | | | | 0 (T6 asks for a test but the correct answer is no change) |
| Explicit behavioural examples in goal | X | X | X | X | X | X | X | X | X | X | X | X | 12 |
| Implicit API contract | | | X | | X | | | | X | | | | 3 |
| Public API preservation checked | | | X (javap) | | X | | | | X (javap) | X (error contract) | | X | 5 |
| Compile validation | | X | X | | | X | | | X | X | X | | 6 |
| Targeted tests | X | X | X | X | X | X | X | X | X | X | X | X | 12 |
| Regression / full upstream suite | X | X | X | X | X | X | X | X | X | X | X | X | 12 |
| Runtime / application / CLI / service launch | | | | | | | | | | | | | 0 |
| Packaging / install-then-import | | | | X | | | | X | | | | (x) | 2-3 |
| Unhealthy baseline (suite red before fix) | | | | X | | | | | (x) | | (x) | | 1-3 (S3_A network tests; S4_B R2-F8 cache pollution) |
| Repair-required (first candidate usually fails) | | X | X | | | | | | X | | X | | 4 (observed historically, INFERRED as a property of the task) |
| Semantic-safety failure possible | | | | | | X | | | | X | | X | 3 |
| Model-tool interaction (investigation tools) | | | | | | | | | | | | | not task-determined; every run may use tools, none requires them |
| Approval / authority-sensitive path | | | | X (pyproject) | | | | X (pyproject) | | | | | 2 (build files are sensitive paths) |
| Multi-module build | | | | | | | | | | X | X | | 2 |

## Complexity profile (MEASURED sizes; INFERRED difficulty)
- Fix size is small everywhere: 1 hunk (T1, T2, T3, S1_A, S2_A, S4_A), 2 hunks (T4, S4_B), 6 hunks across 2 files (S3_A), a new feature of about 12 lines (T5). No task needs a design decision across more than 2 files.
- Goal text is 0.7-2.5 KB; every goal carries concrete examples (12/12). The solver is graded on goals whose expected behaviour is fully specified; there is no ambiguous-requirements task.
- Task count by role: 10 code-writing tasks and 2 no-change tasks (T6, S6_A; T6 consumed zero model calls). **A pass of 8/12 therefore needs only 6 of the 10 code-writing tasks when both no-ops pass** (60%), not 67%. Both no-op tasks are passable by an empty diff (accept 0 by construction); the 12 gates are still valid because they refuse the tempting change (doc 11).
- Repositories: 10 distinct (cachetools, jsoup, Hamcrest, python-slugify, jmespath, commons-csv, xmltodict, toolz x2, commons-text, JsonPath x2); sizes from small libraries to jsoup (2392 tests).

## Findings (what the 12 do and do not span)
- **Well covered:** single-file defect repair in Python and Java across all three build systems; goal-embedded examples; regression-suite preservation; build/packaging config fixes (T4, S2_A); false-premise refusal (T6, S6_A); public-API/signature preservation (T3, S3_A).
- **Thinly covered (1-2 tasks):** feature work (T5 only), multi-file edits (T5, S3_A), overload families (S3_A only), delegate/indirection (T2, S4_B), semantic-safety (S4_A, plus the two no-ops).
- **Not covered:** inheritance/override; test-writing as the primary goal; any runtime/CLI/service/application execution; ambiguous or underspecified requirements; larger multi-file refactors; languages beyond Python and Java; dependency upgrades; concurrency/timing defects; a task whose defect only manifests at runtime of a packaged artifact.
- **Overlap risk:** S4_A and S4_B share the JsonPath repository and S6_A shares toolz with S2_A; per-repo diversity is 10 for 12 tasks, acceptable.

## Separation of purposes
- FORMAL QUALIFICATION: the 12 frozen tasks and >=8/12, unchanged.
- GENERALIZATION/STABILITY COVERAGE: a future set should add (priority order, justified by the gaps above) feature work with multi-file edits, an inheritance/override defect, a runtime-launch task (CLI or service), a test-writing task, an underspecified goal, a third language, and repeated runs per task for stability. None is added now.
