# PRD-034 C3 run 4 - FAILED CERTIFICATION / VALID SERIAL CHARACTERIZATION BASELINE

**Status: NOT CERTIFIED. This run is never called a pass.** It is preserved as the serial reference for the runtime decomposition and for the static-sharding equivalence proof
(owner rulings, "C3 RUNTIME DECOMPOSITION" and "Continue run 4"). Nothing here is edited after the fact; the fixes it motivated are separate commits.

| | |
|---|---|
| Revision | `1fa0af5` (tree `381a2d0c74c6fea1d2da0199313bb7c4172412ed`), clean; `refs/kriya/verified` = `7eddc5d6e255` (unmoved) |
| Command | `pytest -o addopts= -m "not live_model" -p no:cacheprovider -q --certification --certification-report=... --collection-census=... --junitxml=... --durations=50` (see `command.txt`) |
| Wall | 4h57m58s (02:53:53Z-07:51:50Z); pytest alone 4:54:55; stages outside tests 78 s |
| Population | 19,760 tests; 19,757 passed, **2 failed**, 1 skipped, 45 deselected (`live_model`) |
| Stages | all PASS except `pytest` (2 failures) and `tier_execution` (see D3) |
| Live-model inference | none (C3) |
| Temp-area behaviour | basetemp flat at 15-29 entries (the run-3 fix held); the run's own TMPDIR grew to 780 entries by 50% (see D5) |

## Time by tier (junit, serial)
Ordinary 19,455 tests / 11,828 s (66.8%); JVM/Maven 83 / 3,829 s (21.6%); Docker 201 / 1,626 s (9.2%); real toolchain 5 / 185 s; Gradle container 5 / 95 s; Semgrep 11 / 53 s.
Ordinary tier: median test 5 ms, p99 9.5 s; 683 tests over 5 s cost 6,959 s; 18,772 tests of 5 s or less cost 4,869 s. Top-25 files/tests: `run4-junit-decomposition.json`.
Kernel/process: 490,272 spawns in the ordinary tier, 476,666 `git` (98% product-originated; `kriya/repository/git.py:_run` 61%). Performance EVIDENCE only
(`PRODUCT-GIT-SUBPROCESS-COST`): no product Git change is made inside PRD-034 for certification speed (owner ruling 8).

## The two failures and the defects found (all TEST-HARNESS, classified before any rerun)
| # | Node / symptom | Classification | Fix |
|---|---|---|---|
| D1 | `test_kup_project_context::test_a_path_too_long_...` - `OSError: [Errno 63] File name too long` raised by the TEST's own `mkdir` (fixture sized by segment count; certification root is longer than the default one) | TEST HARNESS: path construction depends on temp-root length | size from the actual root, final 900-1000 chars |
| D2 | `test_linux_oci_workspace_identity::...outputs_belong_to_the_host_owner` - `(501, 0) != (501, 20)` | TEST HARNESS: group semantics depend on the temp root's group (BSD inherits the directory group; `/tmp` is wheel). CONFIRMED by `gid_experiment.py.txt`: the container always runs as `501:20`; its output has the same group as a file the host user creates in the same workspace, in a wheel-group AND a group-20 workspace. NOT a product UID/GID defect | compare with a host control file; UID strict |
| D3 | tier audit: 8 `test_b2c_jvm_acceptance::test_2...` nodes "missing" | TEST HARNESS: `base_nodeid` cut the id at its last `@` even without xdist | strip only the exact `@<group>` suffix, only under `--dist loadgroup` |
| D4 | `certify.sh` cleanup `rm: ...: Permission denied` leaving `/tmp/kriya-cert.*` | Docker Desktop leaves `user:<me> deny delete` on bind-mount targets when a container renames their parent (reproduced with plain `docker run`, no Kriya code) | negative controls release only the directories they moved |
| D5 | run TMPDIR growth: `kriya-strict-config-*` 760, `kriya-milestone-candidate-*` 13, `kriya-authority-mask-*` 8 | test-owned: strict-config released only at session end; milestone candidates left by the harness's `os._exit` crash child; mask scratch dirs left by tests that call `prepare()` and never the returned `cleanup` (the product lifecycle is correct) | module-boundary release; child TMPDIR inside the test tree; tests call `cleanup` |

## Files
`run4-junit-decomposition.json` (tier/file/test timing), `cost-by-file.json` (CPU/spawn per file), `shared-state-static.json` + `docker-touch-static.json` (static survey),
`docker-events.txt` (the three unflagged Docker-user files, measured), `gid_experiment.py.txt`, `tier-execution.*`, `stages.jsonl`, `failures.txt`, `health.log`, `command.txt`, `environment.json`.
The 3.9 MB per-test row dump and the raw junit stay in `~/kriya-cert-2026-11-02/c3-run4/` (outside the repository).

## What this run still proves
The ordinary tier, the Docker tier, the JVM/Maven tier (frozen repository, Central and PyPI denied), the frozen wheelhouse, the product-image guard, the scanner, the release stage and
the doctor all passed on this tree. The valid outcome of every node except the two failures is the serial reference for the sharded equivalence proof. Hosted-Linux evidence
(CERT-LINUX-HOST-EVIDENCE-001) is not supplied by this run.
