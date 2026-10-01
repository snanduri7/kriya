# Pre-Graphify memory/resource-leak audit

**LEAK_AUDIT_VERIFIED = true** (2026-09-29): no confirmed unresolved product leak or correctness defect.

- Baseline: tag `state-machine-hardening-verified` → f4bb39e, branch head a86af41.
- Audited, fixed product: 454af7b. The final full pytest ran on it.
- Runtime: Ollama 0.34.4, qwen3-coder:30b, operator config `~/.kriya/operator/prd036-production.yaml`, macOS (Apple silicon).

Evidence labels follow `handover/ENGINEERING_RULES.md`:

- **MEASURED**: seen in command output, logs or samples.
- **TRACED**: the producing code path was read.
- **INFERRED**: consistent with the evidence, but not proven.
- **CONFIRMED**: a discriminating check established it.
- **UNKNOWN**: not enough evidence.

The raw evidence is local-only, under `handover/leak_audit/results-20260929-1116/` (steps 1–2) and `handover/leak_audit/results-20260929-351888e/` (steps 3–5 and the full pytest). Its README says it is excluded from git through `.git/info/exclude`.

## Acceptance criteria and results

| Criterion | Result | Status |
|---|---|---|
| Steps 1–2: in-process repeated workflows are bounded | Every series BOUNDED on the fixed code. The analyzer-cache growth is gone (30 → 1 entries; tracemalloc slope 7.06 → 5.14 KiB/iteration, equal to the reuse baseline). | MEASURED |
| Step 3: repeated pytest batches are stable | 5 separate processes: 584 passed each, peak RSS 255–258 MB, all before/after counts equal, TEMP_DIR_GROWTH none. 5 in-process repeats: BOUNDED (17.6 MB < 20 MB limit), fd/thread/child counts flat, no watched types retained. | MEASURED |
| Step 4: normal, failure and SIGINT container paths are clean | pass, fail_after_body and sigint were clean in all 4 tests. raise_in_communicate exposed LEAK-OCI-EXCEPTION-RACE-001, which is fixed: 10/10 clean afterwards. | MEASURED + CONFIRMED |
| Step 4: SIGKILL residue measured and classified | Residue is a crash-recovery gap. The next run is safe and independent (see below). Deferred with the user's approval as LEAK-SIGKILL-RECOVERY-GAP-001. | MEASURED |
| Step 5: 3 production runs with no orphans, zombies or Docker growth, and no monotonic Kriya growth | 3/3 success. Docker, zombie, orphan, worktree and temp counts equal before-all after every run. Kriya's own RSS was 218/199/197 MB, fds 20, threads 4. | MEASURED |
| Step 5: Ollama reaches bounded residency | Accepted by the user as OLLAMA_RUNTIME_RESIDENCY. One runner grew 26.3 → 27.4 → 28.9 GB and released everything at unload. The plateau within one loaded session is UNKNOWN. | MEASURED |
| Full pytest at the batch boundary | 7307 passed, 0 failed, 0 skipped (32.5 min). `kriya-strict-config-*` 0 → 0. Kriya Docker objects 0 → 0. | MEASURED |
| Lint | ruff and pylint both at zero. | MEASURED |
| Linux: focused containment/resource verification (`scripts/linux-repro/run.sh 454af7b`) | Ubuntu, kernel 6.12.5-linuxkit aarch64, non-root uid 1001, native inner Docker 29.1.3, Python 3.12.3. The inner daemon started clean (0 containers, default networks only). 57 passed / 0 failed / 0 skipped in 2 min 3 s across `tests/test_process_controller.py`, `test_containment_oci.py`, `test_service_runtime_oci.py` and `test_platform_process_control_contract.py`, including the 4 new lifecycle tests. Covered paths: normal completion (timeout and registry-teardown tests), early exception (container and registry network) and cancellation (host `run_async`). Afterwards: 0 Kriya containers, default networks only, no `docker run` or test processes. | MEASURED |

## Defects found and fixed (all CLOSED in `handover/BACKLOG_REGISTRY.csv`)

| ID | Class | Root cause | Commit |
|---|---|---|---|
| LEAK-ANALYZE-CACHE-001 | KRIYA_PRODUCT_LEAK | `_ANALYZE_CACHE` kept one RepositoryModel per content revision and one per departed root, for example every removed candidate worktree. It now keeps one entry per live root and prunes departed roots. The first version was incomplete; the post-fix rerun caught this. | cd0bfc0 |
| LEAK-JDTLS-START-FAILURE-001 | KRIYA_PRODUCT_LEAK | A failed `JdtlsClient.start()` never released its data dir, the jdtls process or the reader task, because the caller never called `shutdown()`. | 6213205 |
| LEAK-OCI-EXCEPTION-RACE-001 (P1) | KRIYA_PRODUCT_LEAK | `ProcessController.run()`/`run_async()` killed the tree only on their own timeout. After an early exception or cancellation, `docker rm -f` ran before the still-live `docker run` client created the container. The container outlived the run, and for registry-scoped runs the network became permanent. CONFIRMED: the same exception once the container was live gave 6/6 clean runs; immediately gave 6/6 leaks. | 47c1bb3 |
| LEAK-STRICT-CONFIG-TMP-001 | TEST_HARNESS_LEAK | `tests/_strict_doubles.py` left one `kriya-strict-config-*` dir per test (6,639 accumulated). They are now removed at session end. | c715fd9 |
| LEAK-RELEASE-TMP-001 | OS_TOOLCHAIN_ARTIFACT (in-repo scripts) | `verify_release.sh` kept a ~200 MB venv per run and `certify.sh` never removed its doctor home. | 323d83e |

Each fix passed the rule-22 checks: original symptom re-measured, regression tests, isolated mutation checks, adjacent tests and lint.

## Deferred (user-approved)

**LEAK-SIGKILL-RECOVERY-GAP-001** (P2, DEFERRED, target: crash-recovery hardening before KRIYA_PRODUCTION_CERTIFICATION)

Nothing removes Docker objects left by a SIGKILLed Kriya process (TRACED).

MEASURED residue:
- registry proxy and network: permanent;
- managed-service container: persists until stopped;
- foreground `--rm` container: removes itself when its command ends;
- MCP: none.

Next-run characterization, run with that residue present and not cleaned (MEASURED):
- A Maven-only registry run got Maven 200 and PyPI 403 beside a stale PyPI-only proxy. So it adopted no authority. It removed only its own objects.
- A managed-service verification passed its own probe.
- No reuse, contamination or false success.

## Not Kriya

- **OLLAMA_RUNTIME_RESIDENCY.** Memory grew inside one persistent `llama-server`. It is CONFIRMED that Kriya never triggered a reload or changed the context, because the same pid ran throughout. The memory was released at unload. Observe the plateau opportunistically during Graphify.
- **Harness artifacts.**
  - About 5 KiB and 400 tuples per iteration in `inproc_workflows.py`: the top growth sites are harness lines (TRACED).
  - `kriya-state-*`: 5,814 dirs from an earlier session's scratch runner (TRACED). Cleaned; covered by a memory rule.

## Open uncertainties (not proven)

- **Managed-service SIGKILL residue.** It was 5/5 when step 4's harness killed pytest, but 0/3 when a plain Python driver was killed. The difference is UNKNOWN. It does not affect the safety verdict, which was tested against the reproduced residue.
- **Linux.** Done: the focused run is green (see the table above). What it does not prove: that the hosted CI runner behaves identically (this is a reproduction), the rest of the suite on Linux, cancellation mid-container (only host-process cancellation has a test), live-model paths or SIGKILL recovery.
- **jdtls live path.** In production runs jdtls was only checked for leftover processes: none were found. Its start-failure path is covered deterministically by tests.

## Corrections logged during the audit

Kept, not rewritten:

- The analyzer-cache misreading and its incomplete first fix.
- The `kriya-state-*` attribution by name.
- mtime read as creation time.
- `B-summary.json` reported as empty.
- A sample.py edit during a running measurement.
- Mutation runs contaminated by leftover Docker residue (redone in isolation).
- Two invalid SIGKILL-driver runs (renamed INVALID/INCONCLUSIVE).

These led to `handover/ENGINEERING_RULES.md`, which `CLAUDE.md` imports.

## Housekeeping after the Linux run (MEASURED)

- The idle `kriya-linux-ctr` container, up since 2026-09-28, was removed. `run.sh` recreates it on demand.
- `kriya-linux-repro:latest` (934eda67aaa7) and the volumes `kriya-linux-dind-lib` and `kriya-linux-runner-home` were kept, because `run.sh` reuses them by design.
- The superseded builds c9b3ef72f048 and a0de5effa3be were removed. No container, pinned list, script or `kriya/` file referenced them.
- Image store total: unchanged at 6.033 GB. The ~2.2 GB I expected was not freed, most likely because those builds shared layers with the current image (INFERRED).
- `784bbe1e00ff` belongs to the unrelated `cineflow-ai` Compose project and was left untouched.
