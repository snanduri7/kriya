# PRD-032 (Wave 7) evidence - current lineage, executable base 0621277

## P2: ARTIFACT-DERIVATION-INDETERMINATE-MANIFEST-001 (task 1)

Pre-fix (executable 0621277, real engine, only the model scripted; scratch `p2_real_engine_scratch.py.txt`, never a committed test):
- `p2_prefix_real_engine.json` - SUPERSEDED, kept as pre-fix evidence. Its mid-run cases (B2/B4/B6) are scripting artifacts: milestone 2's scripted
  Developer wrote only the manifest, so it never added `div` and was refused for an unrelated reason. Do not read B2/B4 there as manifest behaviour.
- `p2_prefix_real_engine_v2.json` - corrected scripting for the from-start cases; B2/B4 mid-run still did not carry the manifest write.
- `p2_prefix_real_engine_v3.json` - M2 writes calc.py AND the manifest (Architect/Planner name it). MEASURED:
  - B3 npm `package.json` not valid JSON from the start: status `success`, M1 and M2 completed, registry EMPTY, workspace changed (DEFECT).
  - B4 npm: M2 commits a malformed `package.json`: status `success`, M2 completed with 0 records while M1's record stayed (DEFECT).
  - A (no manifest), B7 (pyproject without `[project]`), B8 (package.json without `name`): success, empty registry (VALID: not applicable).
  - B1 Python / B5 Maven malformed from the start: `milestone_failed`, `CONTAINMENT_SETUP_FAILED` from the toolchain resolver (a DIFFERENT owner that
    runs first under this configuration; contained by that owner, reported under the environment category, NOT by the artifact derivation).
  - B9 npm `[]`: the pre-existing exception path fired (AttributeError outside the deriver's try), bounded retry, `quality_gates_exhausted`.
  - B2 Python mid-run: the manifest write did not land (cause not traced; NOT claimed). B6 Maven mid-run: environment artifact (cannot create the
    container Maven cache), mid-run Maven UNMEASURED through the real engine.
- `p2_postfix_real_engine.json` - the same scratch after the first fix revision: B3 and B4 `milestone_failed` at the pre-commit gate, B4 keeps M1 complete with
  1 record and its `package.json` byte-identical; A, B7, B8 unchanged. (Captured before the Maven rule was narrowed; the narrowing does not touch these cases.)

Final proof is the committed `tests/test_p2_artifact_derivation_indeterminate.py` (real engine for npm; derivation level for Python and Maven) with 7 mutants
killed by the intended tests (expected-artifact check bypassed per ecosystem, registry swallowing the indeterminate result, the milestone gate ignoring it,
`validate()` made strict, the category made an environment fault, pom without artifactId).

Documented limit: a manifest that PARSES but carries no identity stays NOT APPLICABLE for Maven (a well-formed pom without `artifactId`, a skeleton `<project/>`)
and npm (no `name`); a skeleton pom is used as a marker by ~30 test files, and Maven's own build rejects it. Only an unparseable pom is Maven-indeterminate.
Also not covered: a `<module>` declared by a pom whose directory/pom is absent (skipped by `_find_pom_files`).

## Final review addendum: parseable-but-incomplete descriptor (pom_boundary_*.json, pom_boundary_scratch.py.txt)

Real engine, Maven compile/test/pom gates patched (a container Maven cache is not the question). Case A (bare pom, nothing recorded): success, empty registry - valid. Case B (M1 records the Maven
artifact; M2 commits a parseable pom without `artifactId`): PRE-FIX `success`, M2 with 0 records, M1's record stale (`ARTIFACT_DRIFT` only if a consumer validates it later) - silent completion, so P2 was
not fully closed. Post-fix (`pom_boundary_postfix.json`): B `milestone_failed` at the pre-commit gate, pom byte-identical, M1 record intact, no drift; A unchanged. Also corrected in the same commit: the python
`[project]`-without-`name` rule (my first P2 commit) made a skeleton pyproject indeterminate and failed `tests/test_workflow_controller_enforce.py::test_enforce_generic_non_maven_shape_permits_a_second_distinct_recovery`;
parseable-but-identity-less is now "not applicable unless a recorded artifact expects it" for all three ecosystems.
