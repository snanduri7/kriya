# PRD-034 final local sharded C3, attempt 1 (c3-run6-final-sharded): 19,854 of 19,855 PASSED, ONE unexplained intermittent failure -> NOT_CERTIFIED

Code under test `8dc5f96` (tree `b40ff33c...`; this evidence commit and the forensic helper came after), run at `196a68d`. Wall **1h53m44s** (15:51:16Z-17:45:00Z); exclusive shard 396 tests, ordinary 6,376 / 6,711 / 6,372.

| Check | Result |
|---|---|
| Population | census 19,855 == frozen census digest `2bab337b...`; per-shard id digests identical to the frozen plan (exclusive `44420fa7...`, ordinary-1 `893cd0a7...`, -2 `426a7b1a...`, -3 `d2a0cba5...`); union == census, disjoint, one result per node: audit `problems: []` |
| Docker guard | **0 forbidden docker calls in every ordinary shard** (the run-5 defect is closed by classification) |
| Outcome | 19,854 passed, 1 skipped (Linux-host-only, pending), **1 failed**, 0 errors; tier counts of what ran == census |
| Other stages | collection, perf_probe, maven_served (0 bytes-differ), python_served (322 x 200), images_unchanged, scanner, images, release PASS; thresholds, doctor RECORDED |
| Verdict | NOT_CERTIFIED: `pytest` FAIL (the failed node) and `tier_execution` FAIL (the same node is in the Docker tier) |
| Note | the digest printed as `plan_digest` differs from the offline one because it hashes the input FILE PATHS; the content comparison above is the identity check |

## The one failure (UNKNOWN, intermittent) - `tests/test_validate_oci.py::test_contained_python_validation_without_manifest_runs_tests_from_a_pytest_venv`
MEASURED: the test's own log shows `Project venv marker not written ([Errno 2] ... .kriya/venv/.kriya-installed.json)` and the next contained step `kriya-launch: exec: .kriya/venv/bin/python: not found`.
TRACED (kriya/tools/validate.py `_ensure_project_venv_impl`): the marker is written only AFTER `python3 -m venv` succeeded (`bin/python` existed on the host) and `pip install` returned 0, so `.kriya/venv` was present, then ABSENT on the host before the marker write.
MEASURED: pip was not the cause - the frozen index served that test 10 requests, all 200 (322/322 in the run); no other shard was running (the three ordinary shards had finished); the exclusive shard executes serially.
Docker Desktop logs for that second hold no error-level event; no product code on that path deletes the directory except the recreate branch (not reached).
**Reproduction attempts, all PASS: 3x alone; 1x with the frozen servers + `--certification` after the three reclassified files and the whole of `test_validate_oci.py` (70 tests); 20x alone with the frozen servers (200 pip requests, all 200).** Run 4 and run 5 also passed it.
Classification: **UNKNOWN - a ~1-in-28 intermittent not reproduced outside the 1h40m exclusive shard**; not claimed as a harness or environment defect without proof.

Action taken (diagnostic only, no assertion weakened, census unchanged): the test's failure message now carries `.kriya` state on the host, the validator's venv counters and the validator log warnings, built only when the assertion fails, so a recurrence is explainable.

## Why a second full local run, against the anti-loop rule
NEW UNCERTAINTY: does the failure recur in the full exclusive shard (a real order/state dependency) or was it a one-off; a clean result is also what the owner's conditional-push precondition needs ("final local C3 clean").
WHY FOCUSED EVIDENCE CANNOT RESOLVE IT: 24 focused/partial attempts pass; the failing conditions (the whole 1h40m Docker shard in one process and VM) are not reproducible cheaply.
DECISION THAT CHANGES: a clean run permits the conditional hosted-Linux push; a recurrence is now captured by the forensics and becomes a defect to fix before anything else.
Owner ruling 7 lists "the final local C3 exposes an unexplained flake" as a condition that permits another full local C3.
