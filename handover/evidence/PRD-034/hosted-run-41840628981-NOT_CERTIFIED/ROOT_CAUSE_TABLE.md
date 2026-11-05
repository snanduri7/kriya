# Hosted run 41840628981 (workflow_dispatch, HEAD 1de8732, tree a179cfe3) - Production certification NOT_CERTIFIED

The one authorised dispatch after owner ruling D (Maven 3.10.0 sole authority). No rerun was taken. Created 2026-11-04 ~13:45Z.

## Job outcomes (MEASURED)
Success: Static check (pylint), Platform guard (Windows), Verify dependency lock file, sdist/wheel install, Lint (ruff), Test 3.12 / 3.13 / 3.14. Skipped: both live-model jobs. Failure: **Production certification** only.

## Production certification (artifact/, logs/cert.log)
`pytest: FAIL  2 failed, 19985 passed, 3 skipped, 0 errors across 4 shards (merge audit OK)`; tier_execution PASS (jvm 88 passed); maven_toolchain PASS; maven_repository PASS; collection PASS (census 19,990 selected / 45 deselected, digest 0c05344f...). Plan: exclusive 397, ordinary 6255 / 7044 / 6294.

HOSTED-CONFIRMED by this run: the Apache Maven 3.10.0 authority (tree-sha256 45d9be39...), the frozen repository manifest (8b12496d..., 1999 files), both demo-v2 post-check tests and the whole jvm tier, the S3 named-test-base fixture fix.

| # | Test | Class | Cause (evidence) |
|---|---|---|---|
| 1 | `tests/test_s19e_honest_refusal.py::test_a_dubiously_owned_repository_is_the_ownership_refusal_and_nothing_is_written[committed]` | TEST_HARNESS, CONFIRMED | `_tree(repo) == before` differed by `.git/objects/maintenance.lock` (present in `before` only; artifact/pytest/failures-extract.txt). Producer: `test_s19e_honest_refusal._git` ran an unguarded `git commit`; git 2.56.0 starts a detached `git maintenance run --auto` that holds that lock. Same mechanism as runs 41785193323 / 41812207261. Reproduced on Linux git 2.56.0 (guards stripped: 2 failures per run among s19e + logging_location in 3 of 3 runs). |
| 2 | `tests/test_prd034_maven_authority_001.py` (decoy-Maven test, my own new test) | TEST_HARNESS, CONFIRMED | exit 141 after the first banner line. `demo-v2/scripts/_common.sh` has `set -euo pipefail`; `fake-mvn | head -1` is killed by SIGPIPE when head exits first. Real Maven (JVM) ignores SIGPIPE. Fix: `sed -n 1p`; the fake mvn pauses 0.3 s so the old form returns 141 every time (mutation verified). |

Neither failure is a Kriya product defect. Both were MISSED by my earlier by-hand sweep (method failure, accepted by the owner's correction): the class is closed mechanically in ../git-maintenance-class-census-2026-11-05/README.md.

Original run evidence is preserved unmodified; this table supplements it.
