# PRD-034 local closure checklist (owner ruling 2026-11-04, section 5)

**Status: `LOCAL_COMPLETE / HOSTED_LINUX_REQUIRED` - NOT CLOSED.** The local state is the owner's `INCOMPLETE_PLATFORM/ENVIRONMENT EVIDENCE` shape (ruling section 3), not a clean local C3: no single local run produced a CERTIFIED sharded C3 (attempt 1: 19,854/19,855; attempt 2: 19,853/19,855 - both failures in the exclusive Docker shard, one mechanism, root cause UNKNOWN, fail-closed). PRD-034 closes only after hosted Linux evidence is consumed (ruling section 9).

Executable revision for everything below: `9d88ff820e24c2c6a811ef3b35a9cf4316811c68` (branch `arch/containment-rebuild-v2`); the commits after it change only `handover/` (this file, registry rows, evidence). `kriya/` last changed in `139eba9` (`service_runtime._cleanup`). `refs/kriya/verified` = `7eddc5d6e255`. Nothing is pushed.

## A. Requirements and their terminal disposition
| # | Requirement | Disposition | Evidence |
|---|---|---|---|
| 1 | Complete census + shard topology proven | **DONE** | final census **19,858** (`-m "not live_model"`, collect-only), sha256 `98d8a2f26e33acb01afbba830bd0e4a20aaf2c21c0b491a14b77b8afe58e3e4f`; plan `d041bc6e4beff3f7` (digest `d041bc6e4beff3f767dd49484a6b625c45b226ca4dd75cd97401591a2b5d2416`): exclusive 396 (ids `44420fa7f73e`), ordinary-1 6,376 (`893cd0a78282`), ordinary-2 6,714 (`7140ac2e0bbc`), ordinary-3 6,372 (`d2a0cba5bd0c`). Reconciliation to the frozen 19,855: **+3**, all `tests/test_service_runtime.py` (the cleanup regression), all in ordinary-2; exclusive/ordinary-1/ordinary-3 ids digests identical to the frozen plan. Run 4 (19,760) -> 19,855 is in `FINAL_LOCAL_C3_FREEZE.md`. |
| 2 | Ordinary shards clean | **DONE (on the pre-`139eba9` tree)** | c3-run6 and c3-run7: every ordinary shard 100% passed, merge audit `problems: []`. `139eba9` touches `service_runtime`/`test_service_runtime` only (ordinary-2); focused + adjacent proof: 811 passed. Hosted Linux certifies the final HEAD. |
| 3 | Merge / tier audits clean | **DONE** | c3-run7 `shard-audit.json`: union == census, disjoint, one terminal result per node, tier counts equal, progress finished. |
| 4 | Zero forbidden Docker mutation in ordinary shards | **DONE** | 0 refused docker calls in run 6 and run 7; guard trace (no bypass) in `FINAL_LOCAL_C3_FREEZE.md`; the three real-cleanup OCI files are in the exclusive shard. |
| 5 | Acquisition determinism (cold-runner parity) | **DONE locally** | Maven repo 1,773 files digest `c557384d...`; wheelhouse 70 files `749cb006...`, lock `b4583335...`; stages `maven_served`/`python_served`/`images_unchanged` PASS in run 7 (Central/PyPI denied); `COLD_RUNNER_PARITY.md`. Hosted cold-runner provisioning: pending. |
| 6 | Run-4/5 harness defects D1-D5, shared port, Docker guard, tooling | **DONE** | registry rows CERT-KUP-PATH-LENGTH-001 ... CERT-SHARD-PROGRESS-TOOLING-001 all CLOSED. |
| 7 | Concurrency stress proof | **DONE** | 9 of 9 copies green (943 passed, 1 skipped) on `55b6003`, `FINAL_LOCAL_C3_FREEZE.md`. |
| 8 | Stock live-fixture identity proofs (PRD-027 / PRD-028 modules) | **DONE** | one live pass each at HEAD `9d88ff8`: batch6 8 passed, batch5 5 passed; `stock-live-fixtures/README.md`; LIVE-STOCK-FIXTURE-IDENTITY-001 CLOSED. |
| 9 | All known product failures fail closed; no false success | **DONE** | both venv failures were typed refusals; product defect found in the investigation (`_cleanup` early return) was a cleanup omission, fixed. |
| 10 | SEC-009 | **CURRENT, no uncertainty** | read-only `authority inspect` at the stock proofs: trust file `5fff250e61e7...` CURRENT, covers all pending fields; `kriya authority approve` never run. |
| 11 | A final local C3 that is CERTIFIED | **NOT ACHIEVED - replaced by hosted Linux per ruling sections 3, 5, 8** | c3-run6-final-sharded-attempt1, c3-run7-final-sharded-attempt2 (failures preserved). |
| 12 | WAVE5 real-Maven test owned by `real_toolchain` | **DONE (registry closed this commit)** | marker `real_toolchain` (`f679e3b`), in the census, PASSED in run 6 and run 7. |

## B. Items that stay OPEN (the expected remainder) and what closes each
| Item | Why open | Closes with |
|---|---|---|
| CERT-LINUX-HOST-EVIDENCE-001 | `/proc` Linux-host measurement cannot run on macOS (PENDING by design, summary `INCOMPLETE_PLATFORM`) | hosted run's `tier-execution.json` shows it PASSED and `platform_pending` empty |
| CERT-DOCKER-VENV-VISIBILITY-001 (= `UNKNOWN-MACOS-DOCKER-VENV-VISIBILITY`) | intermittent venv disappearance in the macOS exclusive Docker shard, root cause UNKNOWN; not reproduced in the bounded differential; static lifecycle trace found no reachable active-use race | hosted Linux decision matrix (ruling section 7): A pass -> platform-specific, carry any macOS reliability claim to Gate G/Wave 8; B reproduces -> PRD-034 stays OPEN, first wrong owner before any code change; C infra -> classified separately |
| Hosted cold-runner certification itself | not run | the hosted `Production certification` job on the final HEAD |
| CERT-TMP-GROWTH-001 (P2) | its closure text says "no THROUGHPUT_COLLAPSE / KERNEL_BOUND in the canonical run's health log"; the sharded runs' ordinary shards legitimately report KERNEL_BOUND/THROUGHPUT_COLLAPSE (git-spawn heavy tests, `PRODUCT-GIT-SUBPROCESS-COST-001`), so the literal condition cannot hold under sharding | **owner to confirm a restated condition** (e.g. temp-root growth bounded and `perf_probe` PASS, both already evidenced) - not closed by Claude |
| MAVEN-CENTRAL-CERTIFICATION-NONDETERMINISM-001 | its text says "closed by a C3 run that needs no live Central" | the runs 5-7 `maven_served` stage (Central denied) is evidence; the hosted run closes it on a cold runner |
| CERT-TMP-MASK-RESIDUE-EXCLUSIVE-001 (P3) | 2 empty scratch dirs per exclusive replay, creator test unattributed (the other 4 were the product defect, closed) | post-PRD-034 harness hygiene; not a blocker (empty, no authority impact) |
| PRODUCT-GIT-SUBPROCESS-COST-001, PRODUCT-CANONICAL-SPELLING-LISTING-001 (P3) | post-PRD-034 performance items | not blockers |

## C. Conditional-push preconditions - STATUS: **NOT ALL HOLD; NOT PUSHED**
| Precondition | State (MEASURED) |
|---|---|
| local PRD-034 prerequisites complete; local state valid | yes (sections A/B; the INCOMPLETE_PLATFORM shape per ruling section 3) |
| clean repo; exact HEAD/tree recorded | yes (final values in the session report) |
| no unresolved P0/P1 | yes (no open P0/P1 in the registry for this scope) |
| no SEC-009 uncertainty | yes |
| push needed solely for hosted-Linux/CI evidence | **No hosted run would start**: `.github/workflows/ci.yml` triggers only on `push: branches: [main]`, `pull_request`, `workflow_dispatch`, and the nightly `schedule`. A push of `arch/containment-rebuild-v2` alone starts nothing; a run needs a pull request or a manual dispatch (`gh workflow run ci.yml --ref arch/containment-rebuild-v2`), neither authorized. |
| "the remote being the existing expected branch" | **No**: `git ls-remote --heads origin` lists `backend/readiness-v2`, `demo/*`, `gui/*`, `main` - `arch/containment-rebuild-v2` does not exist on `origin` (no upstream configured). The authorized command `git push origin arch/containment-rebuild-v2` would CREATE a remote branch (868 commits ahead of `origin/main`, which is an ancestor). |
| no tag/ref other than the branch changes | would hold |

Expected hosted jobs once triggered (from `ci.yml`): `Test (Python 3.10-3.14)`, `Platform import and architecture guard (Windows)`, `Lint (ruff)`, `Static check (pylint)`, `Verify dependency lock file`, `Source distribution and clean wheel install`, **`Production certification`** (the discriminating job; ubuntu-latest, non-root, pinned images, frozen Maven repo + wheelhouse provisioned in bootstrap steps), `Primary live model regression` (Ollama smoke, `live_model and not live_target`).
Exact command, if the owner authorizes creating the branch: `git push origin arch/containment-rebuild-v2`; to start the certification: `gh workflow run ci.yml --ref arch/containment-rebuild-v2` (or a PR).

## D. Pre-push record - acquisition / plan input digests at HEAD (full sha256)
| File | sha256 |
|---|---|
| `tests/certification/maven_repository.manifest.json` | `852776379e32cc7fa1a891c27b23c1a6a6c8cee77e44de33e6caae3f49dded37` |
| `tests/certification/requirements.release.hashes.txt` | `b4583335692ccfa8aa19e9387f8c8beb186f83745e1057654195e13eb710fd58` |
| `tests/certification/pinned_images.txt` | `eb7a9ee4b68266a428626dbe630b1067fc36b3f0a0221d15ab3aec857f62dd25` |
| `tests/certification/shard_exclusive_files.json` | `9bc742718525fd94ff6663ff3e6823df0e0e49920a34e3d84108806272756459` |
| `tests/certification/shard_timings.json` | `5377605edf9febfa5eb9fcc18e70d39edb3086c680a77fb4c933c35418699a47` |
| `tests/certification/skip_allowlist.yaml` | `4ad86ffc12c961f20006cc788903e93cd81b04bb11d06caf323d4141b44c3484` |
Frozen content digests: Maven repo `c557384d...` (1,773 files), wheelhouse `749cb006...` (70 files).

## E. What the hosted `Production certification` job is, and whether it can reach the exclusive-shard tail (MEASURED from `ci.yml`; capacity INFERRED)
* `runs-on: ubuntu-latest`, `timeout-minutes: 300`; the repository is PUBLIC, so the standard hosted runner (4 vCPU / 16 GB / ~14 GB disk, INFERRED from GitHub's documented public-repo size) applies. `scripts/certify.sh` defaults to the SHARDED topology (`CERT_TOPOLOGY` unset -> sharded, 3 ordinary shards): 4 concurrent pytest processes on 4 vCPU.
* Local timings (10-core Mac): sharded wall 1h54m, of which the exclusive Docker shard alone is 1h50m (6,630 s of test time), and the serial reference took 4h58m. The `ci.yml` comment still says "serial by design ... about 2.7 hours locally" - STALE (documentation only; the job runs the sharded default).
* **Risk (INFERRED, unmeasurable before a run):** on 4 vCPU with cold image pulls and Maven/wheelhouse provisioning, the job may take well over the local 1h54m; if it exceeds 300 minutes the run is cancelled before or inside the exclusive shard tail, which is ruling-section-7 Outcome C (infrastructure) and says nothing about the venv issue. Serial would not fit at all. No timeout change is made here ("Reduce only on measured hosted durations").
