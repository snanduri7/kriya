# PRD-034 C3 run 5 - first STATIC-SHARDED run: NOT_CERTIFIED by its own merge audit (3 mis-classified docker calls); every test passed

Revision `917fb06` (tree `13f59570863b144bea54b35351b3067dda00b6fe`), clean; same `certify.sh` command as run 4 (operator doctor config, SEC-009 CURRENT, frozen Maven repository
`c557384d...`, wheelhouse `749cb006...`, product images guard). `CERT_TOPOLOGY` default = sharded: exclusive + 3 ordinary shards (plan digest in `plan.json`).

| | |
|---|---|
| Wall | 13:12:31Z - 14:58:05Z = **1h45m34s** (run 4 serial: 4h57m58s) = **2.8x faster** |
| Shards | exclusive 335 tests 1:42:29 (the long pole; serial floor estimate 1:39); ordinary-1 6,653 in 1:16:43; ordinary-2 5,569 in 1:04:06; ordinary-3 7,296 in 1:10:17 |
| Outcome | 19,853 nodes: **19,852 passed, 1 skipped (the Linux-host-only measurement, platform-pending), 0 failed, 0 errors** |
| Tiers (census == what ran) | docker 285, jvm 88, gradle_container 5, real_toolchain 5, live_static_analysis 11 - `tier_execution` PASS |
| Other stages | static, maven_repository, python_wheelhouse, product_images, collection, perf_probe, maven_served (36,971 requests, 0 bytes-differ), python_served, images_unchanged, scanner, images, release: PASS; thresholds, doctor: RECORDED |
| Temp area | the run's `/tmp/kriya-cert.*` root was removed by `certify.sh`; no leftover; `refs/kriya/verified` unmoved |
| Verdict | **NOT_CERTIFIED** - stage `pytest` FAIL: the merge audit named one problem |

## The audit failure (classified before any rerun)
`ordinary-3: 3 container-mutating docker call(s) in an ordinary shard, first from tests/test_s7_resource_limit_replacement.py::...[denied-network]: rm -f kriya-oci-...`.
TRACED: the test's helper runs the prepared command's own `cleanup()` after `prepare()`; on the denied-network path nothing was created, so the teardown's `docker rm -f <never-created name>`
is a no-op daemon call that the ordinary shard's docker guard refused (exit 125; the best-effort cleanup tolerated it, so the test passed). It is the mis-classification the guard exists
to catch (3 refused calls in the whole run, all from this test; 220 read-only calls passed through). TEST HARNESS: fixed by stubbing the teardown's daemon call on the denied path.
Two tooling defects were found in the same run (not test failures): the aggregate progress file ended at 19,852/19,853 unfinished (the loop stopped between samples -> a spurious
`PROCESS_GONE` from the watchdog), and `certification_watch.py` mis-parsed its own `--interval/--step-percent` values. Both fixed with tests.

This run is development evidence for the sharded topology and the Docker guard. It is NOT one of the two equivalence-proof runs (its audit failed and the code changed after it).
