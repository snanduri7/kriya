# PRD-034 - bounded differential investigation of the exclusive-shard venv-visibility failures (owner ruling 2026-11-04, option 1)

**Status: NOT REPRODUCED. Root cause UNKNOWN. No product or test code was changed; `kriya/` and `tests/` are byte-identical to `c915e9c`.**

Subject: C3 final-run attempts 1 and 2 (`c3-run6-*`, `c3-run7-*`): `test_validate_oci::...pytest_venv` (both), `test_prd032_chaos_commit::test_resume_from_an_uncertain_workspace_is_refused` (attempt 2).

## Method (read-only instrumentation; nothing waits inside the product, no docker argument is changed)
* Revision `c915e9c830f9` (tree identical for product/tests), frozen Maven repo + PyPI index servers, `--certification`, the exclusive shard's own id list in its own order.
* **A** = `test_containment_oci_registry_scoped_unit` + shard files 26..57 (`prd011_toolchain_identity` ... `w38`; includes the reclassified `prd011_toolchain_identity` and `s7`): 196 tests, 24m15s, **196 passed**.
* **B** = the same without the three reclassified files: 132 tests, 25m29s, **132 passed**.
* Tooling (`d6tool.py.txt`): a PATH `docker` shim logs every call (argv, mounts, rc, times) and delegates unchanged; for each venv-related `docker run` a sidecar `docker exec ... [ -e ]` looks INSIDE the running container every ~50 ms; the shim takes the 0 ms host sample in-process at client return and a detached sampler takes 50/100/250/500/1000/2000/5000 ms samples; a 20 ms host watcher logs every transition of `<ws>/.kriya[/venv[/bin/python]]`; `docker events`, 10 s machine-load samples, docker state before/after.

## Results (MEASURED)
| | A (with reclassified) | B (without) |
|---|---|---|
| venv-create containers (`python3 -m venv .kriya/venv`) | 11, all rc 0, wall 3.09-3.27 s | 11, all rc 0, wall 3.03-3.17 s |
| host visibility of venv + bin/python + pyvenv.cfg at 0 ms | 11/11 | 11/11 |
| same at 50 ms ... 2 s | 11/11 at every offset | 11/11 |
| in-container: `bin/python` seen before container exit | 11/11 (symlink -> python3) | 11/11 |
| pip-step containers (`.kriya/venv/bin/python -m pip install`) | 11, all rc 0 | 11, all rc 0 |
| pip-step host state after exit vs 0 ms | no change within 2 s | no change within 2 s |
| leftover containers / volumes | none (4 pre-existing exited containers) | same |
| leftover temp entries | 6 `kriya-authority-mask-*` | 6 `kriya-authority-mask-*` (not from the reclassified files: present in B too) |
| attribution of the 6 (docker argv mount sources) | 2 from `test_service_runtime_oci` (`test_contained_managed_service*`), 2 from tests using the per-test `kriya-state` dir (test not resolved), 2 prepared but never run by docker | same shape |
| are they a failed cleanup? | NO: all 12 are EMPTY, owner-writable (`drwx------`), carry NO ACL entries - `shutil.rmtree` would have removed them; `cleanup()` was never invoked | same |
| rc 127 runs | 2, both deliberate host-interpreter-in-container negative controls | same |

Ambient (both): Docker VM up 11 days; `com.apple.Virtualization` peaks 240-260 % CPU; `fseventsd` 103-129 %, `mds_stores`/`corespotlightd` 126-147 % at times.
The disappearance of the venv ~15 s after the test (watcher) is the test's own tmp teardown, observed in the smoke run too.

## What the existing failure evidence says (MEASURED / TRACED)
* **TRACED (corrected after review):** TWO product paths delete the project venv, and only one is counted: (1) `kriya/tools/validate.py:923` (`shutil.rmtree`, counted by `venv_recreations`; 0 in the failing test); (2) `kriya/tools/dependency_cache.py:84 discard_project_venv`, called by `PolymorphicValidator._discard_project_venv` on ENTRY (required) and EXIT (best effort) of the OUTERMOST `dependency_cache_scope` (validate.py:666-684) - NOT counted by `venv_recreations`. The test harness holds that scope open (`hold_dependency_scope`). So `venv_recreations=0` does NOT exclude product-side deletion. What disfavours (but does not prove) scope-exit deletion as the explanation: the pip container reported `.kriya/venv/bin/python: not found` BEFORE the test's forensics ran, and the held scope has not exited at that point. `gate_view.py:105` removes only a view path. A full repo census of file-removing calls (`rmtree/remove/unlink/rmdir/rename/replace/git clean`) lists ~40 modules; reachable from `run_tests()` are only: validate.py:923, dependency_cache.py (venv, ephemeral caches), dependency_execution.py:630-635 (Gradle/Maven distribution target, not `.kriya`), test_execution.py:437/510 (stale pytest report files), gate_view.py:105, containment_oci.py:355 (mask scratch dirs). No candidate that removes `<ws>/.kriya/venv` alone other than (1) and (2) was found; the test-harness modules were not separately censused (see Limits).
* **MEASURED (run 7 forensics):** creation reported rc 0 and the product's `os.path.lexists(<venv>/bin/python)` check passed (otherwise the creation-error branch would have fired), then the pip container reported `.kriya/venv/bin/python: not found` (rc 127), and at forensics time the host `.kriya/venv` directory did not exist. That is *materialised, then absent* - not "never materialises" and not "late visibility" (neither CASE B nor CASE C as defined in the ruling is established).
* The chaos failure differs: `python3 -m venv` rc 0 but `lexists(.../bin/python)` was already False at the product's immediate check.
* Both failures are fail-closed typed refusals (`PYTHON_ENVIRONMENT_UNAVAILABLE` / pip step failure); no false success exists in either.

## Classification (ruling section 2)
* CASE A (test order / shared-state): **not supported** - B behaves like A; the reclassified files leave no state beyond what B also leaves (6 empty mask dirs from other tests - a TEST-HARNESS cleanup omission, EMPTY and removable, not a product cleanup failure).
* CASE B (eventual host visibility): **not supported** - 22 creations, visibility 100 % at 0 ms, no delay observed.
* CASE C (output never materialises / Docker Desktop fault): **not established** - no failure occurred, so no Kriya-independent bind-mount reproduction could be built; it remains a candidate, **not** a finding.
* **Root cause: UNKNOWN.** First wrong owner: not identified. Required correction: none identified; nothing was changed.

## Limits of this evidence (stated, not hidden)
* Only 2 of the 11 creations per sequence have the in-workspace, under-the-anchor shape of the `pytest_venv` failure (9 are the gate-view/venv-mount shape); for the exact failing mechanism n = 4 across A+B. The replays never reached the 5,000-6,500 s point at which the failures occurred.
* The harness modules (`conftest.py`, `_strict_doubles.py`, `_acl_release.py`, `_milestone_proof_harness.py`) were not censused for deleters of `<ws>/.kriya/*`.
* One pass each (n = 11 creations per sequence). The failure rate is low and intermittent (run 4 and run 5 passed these tests; runs 6 and 7 each failed 1-2 of the exclusive shard's 396 tests), so a clean replay does not exclude the mechanism, and it excludes the *reclassification* as the sole trigger only weakly (the replay starts without the ~5,000-6,400 s of preceding exclusive-shard activity that preceded the failures).
* The failing tests ran at 5,007 s and 6,402-6,485 s into the exclusive shard; the replays reach them at ~1,300 s. A long-accumulation effect (Docker VM state, thousands of prior containers/mounts) is untested and untestable without a ~2 h population.
* The sidecar `docker exec` adds ~7 execs/s of load while a venv container runs.
