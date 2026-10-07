# Graphify local-model E2E milestone — verification of `main` be3cbb2

Milestone tag `kriya-graphify-e2e-local-v1` (annotated, tag object 7f3e320) → **be3cbb282dcbcb98a277b4e4bd6e1de1a21f1c4b**.
This evidence lives on the separate branch `evidence/graphify-e2e-local-v1` (created from be3cbb2) so the certified
executable commit and its tag are not moved. Model calls during all verification below: **0**.

## Integrated revision identity

| Item | Value |
|---|---|
| origin/main before | 61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03 |
| Success branch | feature/lr-r1-gr1, tip 2b50521a514765bdab197c72d33e1b7e3b5a1fbc (contains 56ae8d3 REG-R2 implementation, e815cdd certification, 784b91f post-REG-R2 comparison evidence) |
| Integration | `integration/graphify-e2e-success` from origin/main, `git merge --no-ff feature/lr-r1-gr1` (repository merge convention) |
| Merge commit | be3cbb282dcbcb98a277b4e4bd6e1de1a21f1c4b, parents 61a867f + 2b50521 |
| Tree | identical to the success tip's tree (origin/main was its ancestor; no conflicts) — the integrated code is exactly the certified 56ae8d3 code plus evidence-only commits |
| Side-branch commits incorporated | none (10 side commits: 4 patch-equivalent in gr1; 6 evidence-only / investigation-only / superseded pre-fix reproducers) |
| main update | fast-forward push origin/main 61a867f..be3cbb2 (no force, no second empty merge) |

## Gates on be3cbb2

| Gate | Result | Evidence |
|---|---|---|
| Full suite (`pytest -q -n 8 --dist loadgroup`, cwd = integration worktree, `kriya` imported from it) | **9292 passed, 0 failed** (559.7 s) | integration_full_suite.txt.gz |
| Focused certified groups (63 files: tests/test_{reg_r1,reg_r2,validation_baseline,baseline_surefire,regression_attribution,p2_,p3,lr_r1,fs1,b2,b3_,checkpoint,resume,capability_adapters,provider_contract,inf001,file_integrity,prd024}*) | **1515 passed, 0 failed** (131.7 s) | count only (summary line recorded at run time) |
| ruff (`ruff check .`) | **PASS** (All checks passed) | — |
| pylint (`pylint kriya plugins/core_tools tests`) | **PASS** (exit 0) | — |
| Post-push re-check on the pushed main commit | ruff PASS, pylint PASS, REG-R1/REG-R2/validation-baseline/Surefire 141/141, tracked tree clean | — |

## Deterministic Graphify replay (no model call) — milestone_replay.py

Run 20261007T093122-df65c5d3 (Kriya 56ae8d3, qwen3.8 Developer), sealed M1 store VERIFIED.

| Check | Result |
|---|---|
| Final applied candidate rebuilt from sealed content-addressed blobs (engine.py 9cb72f8a…, tests/test_csharp_call_site_generic_args.py f4409083…) | byte-identical to the frozen applied workspace |
| External Graphify evaluator (executed, never read) | **5/5** — A, B, C, D, E PASS (milestone_candidate.external.txt) |
| Reviewed acceptance | **5/5** (milestone_candidate.acceptance.txt) |
| Graphify regression (76 canonical tests) | **76/76** (milestone_candidate.regression76.txt) |
| REG-R2 decisions re-decided by be3cbb2 code | identical to the live sealed verdicts — see REG-R2-REPLAY.md |
| False success | none (Kriya SUCCESS ⇔ external 5/5) |

During this replay a defect in the replay tooling was found and corrected — see REPLAY-SCRIPT-INCIDENT.md.

## Also recorded here

- RETAINED_WORKTREES.txt + retained-*.sha256: integrity manifests of the ignored evidence that exists only in the four
  retained worktrees (p3d, b2a, lr-r1-m1, fs1c1).
- ARCHIVE_INVENTORY.txt: ~/kriya-wt/_archive tarballs preserved from removed worktrees.
- TEST-WORKTREE-POLLUTION-001.md: test-suite side effect observed during this verification (recorded, not fixed).
- MANIFEST.sha256: digests of every file in this directory.
