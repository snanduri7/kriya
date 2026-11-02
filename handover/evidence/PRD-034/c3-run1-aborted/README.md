# PRD-034 C3 run 1 - ABORTED evidence, and the bounded diagnosis that followed (2026-11-02)

**This is not a certification result.** Nothing here counts as a PASS. Identity: HEAD `97df908d196e392edb739449c703c7dadb9408de` (tree `a3892a92...`),
operator config `graphify-qwen3coder-native-production.yaml`, canonical command in `run1/command.txt`, started `run1/c3-run1.started`
(06:36:04Z), interrupted by Claude `run1/c3-run1.interrupted` after 5 h 01 m (SIGINT to pytest so it wrote its summary), 9,730 of 19,672 tests done
(`9730 passed, 45 deselected, 1 warning, 1 error in 18049.92s`). `run1/ABORTED.txt` is the interrupt note. The `tier_execution` and `scanner` FAILs in
`run1/stages.jsonl` are consequences of the interrupt (98 Docker-tier tests never ran), not product failures.

## Findings

### 1. The one ERROR - a Linux-host-only mandatory test (CONFIRMED)

`tests/test_d2_m2_fd_inheritance.py::test_linux_a_child_cannot_reopen_the_waiters_socket_through_proc` is a MANDATORY LINUX-HOST measurement (owner
2026-10-23). It cannot run on macOS; certification mode correctly made its skip an UNEXPECTED SKIP (`run1/pytest.txt`). Owner ruling 2026-11-02:
no allowlist, no weakening, Docker-on-macOS is not equivalent without proof. Implemented as a certification-platform matrix (`requires_platform`
marker; tests/_certification.py, tests/_certification_collection.py `executed(... platform_pending)`, scripts/certification_summary.py):
the macOS cell records `REQUIRED LINUX-HOST EVIDENCE - NOT EXERCISED LOCALLY` and the summary is `INCOMPLETE_PLATFORM_EVIDENCE` (exit 3), never
`CERTIFIED`; it stays OPEN (`CERT-LINUX-HOST-EVIDENCE-001`) until a hosted Linux run executes it and it passes.

### 2. The slowdown (CONFIRMED root cause location; harness is the first wrong owner)

| Step | Status | Evidence |
|---|---|---|
| Throughput ~4 tests/min at the end, pytest 100 % CPU, 83 % of CPU time in the kernel (`ps -M`: 141 min system vs 28 min user), Docker 0 containers, no Ollama | MEASURED | session transcript, `run1/pytest.txt` |
| `sample(1)` of pytest: top of stack `__getdirentries64` (2,646 samples) via `os_listdir` | MEASURED | session transcript |
| Producer: `kriya/platform/filesystem_semantics.py:208` in `canonical_spelling()`, `os.listdir` of every ancestor of a path, ~345 calls per engine test | TRACED + MEASURED | `diagnosis/scanwatch_A_polluted_default_tmpdir.json`: 16,215 calls, 194.3 of 301.9 s, max 22,857 entries, on the system temp root |
| Same 47 tests: polluted default `$TMPDIR` 302 s wall / 218 s sys; fresh `$TMPDIR` 80 s / 32 s; fresh `$TMPDIR` + 20,000 entries in basetemp 269 s / 187 s | MEASURED | `diagnosis/scanwatch_C_*` and the numbers in `CERT-TMP-GROWTH-001` |
| Growth sources: pytest basetemp +5 entries per test (47,724 after 9,700), never released; the ambient temp root held 23,332 entries (16k hex-named `tmp*`) | MEASURED | session transcript |
| First wrong owner of the in-run `tmp*` growth: `tests/_milestone_proof_harness.py::_apply` left one `tempfile.mkdtemp()` per applied change (88 leaked over 47 tests) | CONFIRMED (leak attributed per test: `diagnosis/tmpdelta_leaked_dirs_by_test.txt`; 0 after the fix: probe temp root growth 89 -> 6) | `diagnosis/perf-probe_*` |

Fix (smallest, hermetic): per-test isolation directories released for a PASSED test (a failed test keeps all of them) and
`tmp_path_retention_policy = "failed"` (tests/conftest.py, pyproject.toml); the harness candidate directory is removed; `scripts/certify.sh` runs in its own
fresh `/tmp/kriya-cert.*` `TMPDIR` and removes only that directory; stage `perf_probe` gates the canonical run. Mutation-checked: seven mutants killed
(tests/test_cert_tmp_growth_001.py). The product's O(entries) listing is not changed (`PRODUCT-CANONICAL-SPELLING-LISTING-001`).

Same-slice before/after, 1,467 tests (`tests/test_[a-b]*.py`, fresh `$TMPDIR` both): before 33 m 22 s, sys 395 s, basetemp 7,249 entries; after 31 m 27 s, sys 313 s,
basetemp max 542. **Honest reading:** that slice is dominated by heavy JVM/Docker tests (kernel use inherent to fork/exec), so wall time barely moved; the slice
does not reach the 20k+ entry range where the crawl appears. The mechanism evidence is the 47-test A/B/C above and the bounded growth; the full-run proof is C3 run 2.

### 3. Maven Central rate limiting (MEASURED, environment)

At 13:44Z `repo.maven.apache.org` and `repo1.maven.org` returned HTTP 429 (Cloudflare) to this machine from host and container for every artifact tried.
Two real-Maven nodes failed with `Acquisition ... exited 1` / `MAVEN_ACQUISITION_INCOMPLETE`
(`tests/test_file_integrity_contract_001b.py::test_a_real_greenfield_maven_compile_passes_its_gate` MEASURED;
`tests/test_b2c_jvm_acceptance.py::...[throws]` in the 1,467-test baseline, INFERRED same cause - it passed in isolation twice under both temp roots). Classification:
ENVIRONMENT (external service), not a Kriya defect. Because every contained real-Maven test starts from an EMPTY ephemeral cache and acquires online, the
canonical run depends on Central staying unthrottled: `CERT-MAVEN-CENTRAL-DEPENDENCE-001` (owner decision before C3 run 2).

### 4. Not findings

`tests/_census_probe_unclassified.py::...` ERROR in `diagnosis/adjacent_suites_run.txt` is an artifact of my file glob (`tests/*census*.py` matched a helper that
is only run by a subprocess); it was not collected by any real run. The `[throws]` baseline failure is recorded above and was not re-run until it was
classified.

## What this evidence must not be used for

No tier, scanner, junit or stage in `run1/` is a certification input. C3 run 2 is a new run on a new frozen revision (this fix changes `tests/`, `scripts/`,
`pyproject.toml`; `kriya/` is still byte-identical to PRD-033 product `4a9273e`).
