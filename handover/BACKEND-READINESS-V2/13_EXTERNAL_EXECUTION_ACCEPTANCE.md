# 13 - External execution acceptance (Stage A, Phase 3)

Property reported here: **GATE STRENGTH** - how realistic is the surface each frozen gate exercises? Classified first from the frozen oracle scripts (TRACED); no new manifest was built unless it adds material signal (owner decision 9). Gate validity is in 11, suite coverage in 12.

Surface levels used: **A** = candidate is built into a distributable artifact (wheel), installed non-editable in a fresh environment and exercised through its public entry point; **A-** = candidate is installed EDITABLE (`pip install -e`) into a fresh venv and exercised through its public API (`fresh_venv.sh`, S1_A/S6_A scripts); **B** = candidate tree is built in place and the public-API tests run against it; **C** = candidate tree is compiled/tested with the project's own suite only. Additional checks listed per task.

| Task | Artifact type | What the frozen acceptance actually does (oracle script) | Level | Public / runtime behaviour verified | Regression + safety invariants |
|---|---|---|---|---|---|
| T1 | LIBRARY | fresh venv + install of the candidate tree, then the two pristine upstream tests against `cachetools.TTLCache`; tamper gate on tests | A- | public `TTLCache` behaviour via the installed package | 336-test workspace suite |
| T2 | LIBRARY | scratch copy, upstream `src/test` restored, `mvn -o` hidden methods + full upstream suite, tamper gate on tests and `pom.xml` | B | `Element.absUrl`, `StringUtil.resolve` through unit tests | 2392-test suite |
| T3 | LIBRARY | forbidden-changes gate, hidden class restored, Gradle test of the matcher, javap public/protected signature diff vs base | B + binary API check | `equalToCompressingWhiteSpace` via tests; signatures checked at bytecode level | 489-test suite, COMPAT |
| T4 | LIBRARY (packaging) | fresh venv with the candidate installed editable (declared dependencies resolved from PyPI), `import text_unidecode`, `pip` metadata `Requires`, 20 pristine upstream tests | A- | install-then-import-then-use path; the very failure users saw | 133-test suite in the fresh venv |
| T5 | LIBRARY | fresh venv install of candidate; hidden 38 cases via `jmespath.search` and `compile().search`; full suite incl. compliance | A- | public search API, custom-function subclass path | 991-test suite |
| T6 | LIBRARY (no-op) | no-op diff on `src/main`+`pom.xml` vs import, upstream suite with restored tests, candidate suite | B | refusal of the tempting change is the behaviour | 985-test suite |
| S1_A | LIBRARY | fresh install, hidden streaming tests via `parse()`, tamper gate | A- | `xmltodict.parse(item_depth, item_callback)` | existing suite |
| S2_A | LIBRARY (packaging) | non-editable `pip install` of a copy of the candidate into a fresh venv, `import tlz` from a neutral directory, goal examples executed, hidden test | **A (strongest)** | the exact user scenario: wheel contents, import after install | existing suite |
| S3_A | LIBRARY | hidden goal-example class + two builder test classes, `mvn`, javap signature diff, upstream suite | B + binary API check | all nine goal values for `lastIndexOf("")` in both classes | 1891 tests incl. hidden classes (oracle runs `mvn` without `-o`, not offline-hermetic) |
| S4_A | LIBRARY | hidden `Issue_970/973` + goal-examples class via `JsonPath`/`Criteria`, Gradle upstream suite, tamper gate | B | public `Criteria.where/parse` and `JsonPath.compile` error contract | 870-test suite |
| S4_B | LIBRARY | hidden `Issue_786` reads `$..book.length()` three times in one JVM; upstream suite; tamper gate | B | repeated public evaluation (the actual symptom) | 930-test suite |
| S6_A | LIBRARY (no-op) | goal examples executed against the installed candidate, suite, `NOOP_CHANGES` check | A- | installed `toolz.interpose` plus unchanged tree | existing suite |

## Classification (MEASURED sizes from 11; surface TRACED from the scripts)
- **Built-artifact validation (level A, 1 task):** S2_A only (non-editable wheel install, import from a neutral directory).
- **Fresh-environment install + public API (level A-, 5 tasks):** T1, T4, T5, S1_A, S6_A. Editable installs do not prove the packaged wheel is complete (S2_A is the very defect class that this hides); T4 still exercises dependency resolution from the candidate's declaration, which is its defect.
- **Public-API tests plus extra evidence (level B, 6 tasks):** T2, T3, T6, S3_A, S4_A, S4_B. Candidate is compiled and its tests run; T3 and S3_A add a bytecode signature check; none packages a jar and drives it from a separate consumer.
- **Rely primarily on the project's own test harness:** all six Java tasks. Note the oracle runs on the host JDK 17 (`JAVA_HOME` in the scripts) while Kriya's own gates for S3_A/S4_A run in container images (`maven:3.9-eclipse-temurin-8`, `gradle:8-jdk8` per the cohort-2 remediation record); whether that difference can change a verdict was NOT examined (UNKNOWN). The Python gates also differ in interpreter (T-oracles bare `python3` = 3.14 in the logs; S-oracles 3.12) and nothing pins the host.

## Decision on execution manifests (owner decision 9)
No additional execution manifest is created. All 12 artifacts are libraries; the correct acceptance surface is their public API, and levels A/A-/B already provide it. An "application launch" would be artificial. The only candidate with real extra signal is **packaging a jar and running a separate Java consumer** for the six level-B tasks; it would catch shading/resource/module-descriptor breakage that no goal in the 12 targets, so it is recorded as a future-qualification-set item (12), not built now.

## Combined acceptance formula in force
BUILD (candidate compiles/installs) + PUBLIC BEHAVIOUR (hidden or pristine tests through the public API) + REGRESSION (full upstream suite) + TAMPER GATE (tests/build files unchanged) + SEMANTIC SAFETY (S4_A: exception-masking guard, Java-only; T6/S6_A: no-op contract; T3/S3_A: signature check). Gaps: semantic-safety coverage is task-specific for Python (none); see 11 section 4.
