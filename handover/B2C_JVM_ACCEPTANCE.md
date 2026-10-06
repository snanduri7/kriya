# FS-1C2 B2-c: operator JVM acceptance evidence (Maven + JUnit 5/Surefire)

**Branch:** `feature/lr-r1-b2a` from the certified B2-COV tip `74d745f`. Implementation `d1f58b0` + `02c7e04` +
`43fad89` (certified revision). No live model run. Nothing pushed or merged. PLAN-R1 (new-file ownership redirection)
untouched, deferred. **Labels:** MEASURED, TRACED, INFERRED.

## Supported shape (v1)

Maven project with `pom.xml` at the root, JUnit 5 run by Surefire, candidate change confined to main sources (build/test
trust surface equal to the run base), candidate main code compiles. Refused: Gradle-only or non-Maven projects
(`ACCEPTANCE_RUNNER_UNSUPPORTED`), changed trust surface (`ACCEPTANCE_TRUST_SURFACE_CHANGED`), no base revision
(`ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE`), injection-path collision (`ACCEPTANCE_PATH_COLLISION`).

Artifact: one `.java` file, one package, one class, every `@Test` preceded by `// kriya_requirement: REQ-n`; refused
before generation: unknown/scope-only requirement ids, unmarked tests, nested types, skip/repeat/parameterize/extend
annotations. Repositories that enforce source headers (Apache RAT) need the repository's header in the operator file
(MEASURED: without it RAT fails the build before any test runs -> INDETERMINATE naming the goal).

## Design (TRACED) - see `kriya/workflow/acceptance_jvm.py` docstring

Trust surface = C0's `OracleSurface` (build files and parent/module poms, `.mvn/`, wrapper, every non-main source set,
`junit-platform.properties`, `META-INF/services/`, output-root writes) vs the run base. Execution in a Kriya-owned copy
(`<state>/acceptance-runs/<uuid>/workspace`): candidate main compile first (`ACCEPTANCE_CANDIDATE_COMPILE_FAILED` =
ordinary candidate failure, never evidence), operator class injected create-exclusive, run alone through the ordinary
Maven test gate (`-Dtest=<Class>`, containment/offline policy and FS-1A fresh report unchanged), case detail re-read from
the digest-verified Surefire XML (MEASURED shape: `name` = bare method, `classname` = FQCN; assertions `<failure>`,
exceptions `<error>` with stack text). VIOLATED only for an assertion-type failure in a class referencing candidate code
or an error with a candidate main-class frame (linkage errors excluded); harness compile failure, missing identity,
incomplete/stale/tampered report: INDETERMINATE. Closure, B2-COV strength rules, supersession and resume binding are
B2-a's (`close_requirements_with_acceptance`), unchanged.

## Certification (MEASURED)

| Gate | Result |
|---|---|
| pre-B2-c pin (`74d745f`) | Java artifact refused: `ACCEPTANCE_ARTIFACT_INVALID` (`evidence/b2c/pre_b2c_pin_74d745f.txt`) |
| focused `tests/test_b2c_jvm_acceptance.py` | 52 passed (real Maven/Surefire): J1-J10 and the owner's 26 required tests |
| mutation run 1 (`02c7e04`) | 17 run, 15 killed, 2 survived: collision guard (weak test - refusal must precede any build; fixed) and the read-side FS-1B guard (test-set selection - write side already downgrades; split write/read, PRD-020 lineage tests added) |
| mutation run 2 (`43fad89`) | **18 run, 18 killed** |
| adjacent (B2-a, B2-COV, FS-1C1, PRD-020, D8, C0, FS-1A/B, structural tripwires) | 792 passed |
| ruff / pylint | 0 / exit 0 |
| full suite (`full_suite_43fad89.txt`) | **9076 passed, 0 failed, 0 errors** (covers M1, P1/P4/P5, P2, P3-A/B/C, I-2) |

## A3/A4/A5 no-model precheck (`evidence/b2c/a345_precheck_*.json`; neutral probe: one case asserting only that the target class loads - infrastructure, never an oracle)

| | repository | Maven/JUnit 5 | staging | trust surface untouched by intended change | claim | can express | eligible |
|---|---|---|---|---|---|---|---|
| A3 | commons-lang @ 4ee346e59 | yes | yes, with RAT header (22.3 s; without: rejected by RAT) | yes (`NumberUtils.java` only) | GENERAL ("otherwise", no example) | examples/counterexamples only | safety sentinel only (can VIOLATE or stay UNVERIFIED; cannot close) |
| A4 | commons-cli @ d95484f | yes | yes, with RAT header (9.0 s; without: rejected by RAT) | yes (`CommandLine.java` only) | GENERAL ("at least", "none", no example) | examples/counterexamples only | safety sentinel only |
| A5 | spring-framework-petclinic @ 09351b3 | yes | yes (5.5 s) | yes (`PetTypeFormatter.java` only) | GENERAL ("matches", no example call) | examples/counterexamples only | safety sentinel only |

Not measured: a contained (OCI) JVM acceptance run; the probes ran host-mode against the local Maven repository.

## Process notes

- The brief asked for the J1-J10 fixtures before production code; I drafted the code first, then the fixtures (the
  pre-B2-c pin records the prior behaviour).
- One mutation invocation used a wrong commit hash: it failed at checkout, left one temp clone (removed); the real run 2
  is the one reported.
