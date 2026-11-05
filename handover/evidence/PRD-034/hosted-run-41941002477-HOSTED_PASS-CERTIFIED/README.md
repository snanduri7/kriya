# PRD-034 - hosted run 41941002477 + private F9b gate = CERTIFIED

Revision `230e54fa0eb0c6af60a94964b6f99be5fb26b63f` (tree `c738e61db29097cfbe857c8364144850506d2ce6`), branch `arch/containment-rebuild-v2`, `ci.yml` workflow_dispatch 2026-11-05. Run: https://github.com/snanduri7/kriya/actions/runs/41941002477

## Hosted (MEASURED from certification-summary.json)
- Summary status `HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED` (exit 4 by design); `problems: []`; `platform_pending: []`.
- pytest: 0 failed, 20,059 passed, 3 skipped, 0 errors, 0 unexpected skips across 4 shards (exclusive 397, ordinary 7630/5463/6572); merge audit OK. Collection 20,062 selected / 45 deselected (matches the local reconciliation, digest c43f0288...).
- Tiers: docker 286, jvm 88, gradle_container 5, live_static_analysis 11, real_toolchain 5 - all passed, none missing.
- Toolchain: git 2.56.0, Apache Maven 3.10.0 (tree sha256 45d9be39...), frozen Maven repository manifest 8b12496d... (36,971 requests, 0 bytes-differ refusals), semgrep 1.178.0, uid not root.
- All other CI jobs green: Test 3.12 / 3.13 / 3.14, Verify dependency lock file, Lint (ruff), pylint, Windows platform guard, sdist and clean wheel install. The live-model jobs are skipped by design on workflow_dispatch.
- Doctor: `profile.production` is reported failed under `packaged_defaults` mode; the stage is RECORDED, not gating, and `problems` is empty. Unchanged behaviour; not investigated here.

## Private F9b gate (run ONCE, on the frozen HEAD, clean tracked tree)
- Bundle sha256 `3534c5d6...cb8` verified before the run; fixture commit `0d7a09136cfec0851...`, tree `16188cde...`, target-file sha256 `a15b6ba2...`.
- Node `tests/test_demo_v2_owner_state.py::test_reset_all_is_idempotent_and_leaves_no_service`: 1 passed (22.58 s). Evidence digest `674900a8...`.
- `verify` against the hosted summary (sha256 `dbbf6fa9...`): `final: CERTIFIED`, `problems: []`.
- Only digests and plumbing metadata are stored here (`private-gate/`). No scenario-01 source, bundle or derived content was committed, pushed, uploaded or sent to a runner.

## Specific hosted proofs for registry rows (MEASURED from junit)
- `test_d2_m2_fd_inheritance::test_linux_a_child_cannot_reopen_the_waiters_socket_through_proc`: passed (CERT-LINUX-HOST-EVIDENCE-001).
- `test_validate_oci::..._runs_tests_from_a_pytest_venv` and `test_prd032_chaos_commit::test_resume_from_an_uncertain_workspace_is_refused`: passed (CERT-DOCKER-VENV-VISIBILITY-001 discriminator).
- `test_a_real_greenfield_maven_compile_passes_its_gate` and the demo-v2 `test_post_check_passes...`: passed.
- `test_idle_check_sees_a_running_stub`: passed (no xdist on hosted; the xdist issue is separate and unchanged).
- Mask residue (`mask-residue.jsonl`, exclusive shard) still shows the two empty scratch dirs after the SIGKILL owner-death test, so CERT-OWNER-DEATH-MASK-SCRATCH-001 stays OPEN P3.

## Not in this evidence
Large served-request logs (maven-repo-served.jsonl 12 MB, python-index-served.jsonl, release.txt) are kept in the Actions artifact only.
