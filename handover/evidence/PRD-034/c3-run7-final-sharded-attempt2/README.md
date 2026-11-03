# PRD-034 final local sharded C3, attempt 2 (c3-run7-final-sharded-2): 19,853 passed, TWO failures, both "container-created venv not usable" -> NOT_CERTIFIED

Run at `ad1a795` (code = attempt 1 + a failure-only diagnostic). Wall **1h53m54s** (17:59:11Z-19:53:05Z). Merge audit `problems: []` (population, disjointness, tiers, exit statuses); 0 forbidden docker calls in the ordinary shards;
ordinary shards 19,458 + exclusive 394 passed, **2 failed (both in the exclusive Docker shard)**, 1 skipped (Linux-host pending). Other stages as attempt 1 (scanner, images, release, served logs PASS).

| Failure | What the test saw |
|---|---|
| `test_validate_oci::test_contained_python_validation_without_manifest_runs_tests_from_a_pytest_venv` (same test as attempt 1) | new forensics: `.kriya` has control/checkpoints/test-reports, **`.kriya/venv` absent on the host**, `venv_install_attempts=1`, `venv_recreations=0`, no creation error; the contained pip step exited **127** (`.kriya/venv/bin/python: not found`) |
| `test_prd032_chaos_commit::test_resume_from_an_uncertain_workspace_is_refused` (different test; attempt 1 passed it) | `python3 -m venv` in the contained gate **exited 0** but `.kriya/worktree/.kriya/venv/bin/python` was not present on the host -> PYTHON_ENVIRONMENT_UNAVAILABLE |

## What is established (MEASURED / TRACED) and what is not
* Common mechanism in 3 failures over 2 runs (attempt 1: venv present after creation, absent before the marker write): **a venv a container wrote under the bind-mounted workspace is not visible where the product next looks**. The product fails closed with a typed refusal (correct); the tests expect success.
* NOT product logic: the recreate/`rmtree` branch did not run (`venv_recreations=0`); pip/index not involved (all 322 requests 200 in attempt 1; the attempt-2 failures happen before/without pip output); other shards were finished (both failures occur in the exclusive shard's solo tail, ~1h21m-1h50m); `kriya/` unchanged.
* NOT reproduced in 24 focused attempts (3 alone, 20 alone with the frozen servers, 1 with the reclassified files + the whole of `test_validate_oci.py`). Run 4 (serial) and run 5 (sharded, exclusive 335) passed both tests.
* Differences between the clean run 5 and the failing runs 6/7: the exclusive shard now also holds 61 tests (three reclassified OCI test files running before and between these tests) and the ambient conditions (time of day, Docker VM uptime). Whether either matters is **UNKNOWN**; the leading hypothesis is a Docker Desktop (macOS virtiofs) file-sharing coherence fault under host churn (fseventsd/Spotlight were observed at 30-75% CPU / 3.5 GB), INFERRED, NOT proven.
