# Hosted run 41812207261 (workflow_dispatch, HEAD 680f3d9, tree 875bb83) - Production certification NOT_CERTIFIED

Created 2026-11-04T10:08:43Z; completed 2026-11-04 12:44Z. The one authorised decisive dispatch of owner ruling C. No rerun was taken.

## Job outcomes (final, MEASURED)
Success: Static check (pylint), Platform guard (Windows), Verify dependency lock file, sdist/wheel install, Lint (ruff), Test 3.12, Test 3.13, Test 3.14.
Skipped: both live-model jobs. Failure: **Production certification** only.

The exact-lock CI and the >=3.12 Python contract (ruling C) are therefore HOSTED-CONFIRMED: every ordinary job is green on the exact locked dependencies.

## Production certification (artifact/ and logs/cert.log)
`3 failed, 19952 passed, 3 skipped, 0 errors across 4 shards (merge audit OK)`; census 19958 / 45 unchanged; tier_execution `jvm failed 2` (the Maven pair), EXTERNAL_PRIVATE_FIXTURE_REQUIRED listed as designed, s2d timing PASSED (single sample; its earlier miss stays UNKNOWN).

| # | Test | Status | Cause |
|---|---|---|---|
| 1 | `tests/test_demo_v2_spring_ignite.py::test_post_check_passes_on_the_reference_and_leaves_the_project_untouched` | CONFIRMED | Two Mavens in one path. MVN-DIAGNOSTICS: the post-check's fixed PATH resolved `/usr/bin/mvn` -> `/usr/share/apache-maven-3.10.0` (Maven 3.10.0); the test process had the pinned 3.9.9 first. Maven 3.10.0 binds `maven-clean-plugin:3.5.0` / `maven-resources-plugin:3.5.0`; the offline `~/.m2` (765 files, seeded online by 3.9.9) held 3.2.0 / 3.3.1 -> `Cannot access central ... in offline mode and the artifact ...maven-clean-plugin:jar:3.5.0 has not been downloaded`. The reference pom pinned compiler/surefire/exec only. |
| 2 | `...::test_post_check_rejects_a_program_that_prints_the_line_without_ignite` | CONFIRMED | Same. |
| 3 | `tests/test_s3_execution_views.py::test_the_named_test_base_export_and_its_git_target_are_removed_on_every_path[failure]` | CONFIRMED mechanism (hosted event order INFERRED) | Second fixture with the closed S3 race: `tests/_named_test_oracle_harness.py::make_base` ran a bare `git commit`; MEASURED (git 2.54, trace2) an unguarded commit spawns `git maintenance run --auto --quiet --detach` (2 hits) and a `-c maintenance.auto=false -c gc.auto=0` commit spawns none (0). The detached maintenance holds `.git/objects/maintenance.lock`, moving the `.git/objects` mtime by ~1.2 ms between the test's two snapshots. |

## Original symptom reproduced pre-fix (Linux container, isolated repositories)
`Maven 3.10.0 -o clean test` against a repository seeded by Maven 3.9.9 -> `BUILD FAILURE ... maven-clean-plugin:3.5.0 ... could not be resolved ... offline mode` - the hosted message verbatim.

## Owner ruling D (2026-11-04) and what was done: see ../MAVEN_AUTHORITY_3_10_0.md
