# PRD-034 final local sharded C3 - frozen inputs, census reconciliation, guard trace, stress result (owner sign-off 2026-11-03)

Code under test: commit `8dc5f96` (tree `b40ff33c5c42f6d875f7be2141273db06fee5776`); the commit that carries this document changes only `handover/`. `kriya/` is unchanged since `e1041bc`
(0 diff lines); `refs/kriya/verified` = `7eddc5d6e255`; nothing pushed.

## Frozen population and assignment (offline, no run)
| | |
|---|---|
| Authoritative census (`-m "not live_model"`, collect-only) | **19,855** nodes, sha256 of the sorted id list `2bab337b78d4d5e260d932a5f5de31f21b5888c07ed30e963bafba4ced238997` |
| Shard plan (3 ordinary shards) | digest `590fd3e2da5dd127ca0d275f9fe06ca4c66c22eab9d5b561dbf157c89fa9f1f7` : exclusive **396**, ordinary-1 6,376, ordinary-2 6,711, ordinary-3 6,372 |
| Checks | union == census; pairwise disjoint; every node assigned once; every tiered node and every measured Docker-touching file is in the exclusive shard (396 == tier/measured set) |
| Inputs | `tests/certification/shard_exclusive_files.json` (6 files: 3 measured container users + 3 real-cleanup OCI test files), `tests/certification/shard_timings.json` (run-4 per-file seconds) |
| Acquisition | frozen Maven repository content digest `c557384d...` (1,773 files), wheelhouse `749cb006...`, lock `b4583335...`; pre-built product images guarded by `images_unchanged` |
The run's own `collection` stage regenerates the census and `plan.json`; the post-run check is that its plan digest equals the one above.

## Census reconciliation: run 4 (19,760) -> final (19,855)
103 added, 8 removed. The 8 removed are the truncated Java ids of `test_b2c_jvm_acceptance::test_2...` (D3); the 8 corresponding additions are the same nodes with their real ids: **net 0**.
The other **95 additions are new harness tests, all owned by the documented commits** (no product test disappeared, no product test was added):
| file | added | commit / reason |
|---|---|---|
| `tests/test_cert_acl_release.py` | 2 |
| `tests/test_cert_nodeid_group_suffix.py` | 10 |
| `tests/test_cert_shared_lock.py` | 5 |
| `tests/test_cert_tmp_growth_001.py` | 3 |
| `tests/test_kup_project_context.py` | 37 |
| `tests/test_prd034_shards.py` | 38 |
`test_kup_project_context` +37 (D1 sizing + negative control), `test_cert_nodeid_group_suffix` +10 (D3), `test_cert_acl_release` +2 (D4), `test_cert_shared_lock` +5 (shared fixed port), `test_cert_tmp_growth_001` +3 (D5; `ac6319d`/`55b6003`),
`test_prd034_shards` +38 (sharding `917fb06` +36, watchdog option test `890b828`, exit-propagation regression `8dc5f96`). Run 5's 19,853 = 19,760 + 93 (before the last two additions).

## Mandatory-audit exit propagation: VERIFIED (not a defect)
c3 run 5: merge audit failed -> stage `pytest` FAIL -> `NOT_CERTIFIED` -> `certify.sh` exit 1. A regression test keeps it verified (a failed node and a vanished shard artifact both make the runner/merge exit 1 with `[shards] FAIL` last).

## Docker guard blind-spot trace (static): no bypass
Every docker resolution in `kriya/` is `shutil.which("docker")` or an argv already resolved from it (PATH of the shard process, which carries the guard); the restricted environments keep PATH; no absolute docker path is executed
(the few in tests are patched/fake runners); no Docker SDK or socket use; spawns with a stripped PATH in tests (demo-v2 scripts) never invoke docker; the guard fails closed on global flags (`-H ... run`) and unknown subcommands.

## Targeted concurrency stress (3 repetitions x 3 concurrent copies of 944 ordinary tests in 59 shared-resource files: authority/config, run locks, d1/d2, demo stub port, mirror/wheels/progress/merge, shard tooling)
First pass found two things, both fixed and re-measured: (a) `test_authority_home_isolation::test_a_stalled_inner_session...` failed in all 9 copies because its `pgrep -f` name came from the tmp_path name that identical concurrent copies share (an artifact of identical copies; name now unique per invocation);
(b) 10 `kriya-strict-config-*` directories per copy from the D1 crash children (`os._exit`; now a test-owned TMPDIR). Final pass on `55b6003`: **9 of 9 copies green (943 passed, 1 skipped), 0 collision/race failures, 1 entry left in each temp root (pytest's own)**.
The three reclassified OCI test files are not in the stress population and their change (real cleanup, exclusive shard) does not touch a shared-resource assertion: the stress evidence stands without a rerun.
