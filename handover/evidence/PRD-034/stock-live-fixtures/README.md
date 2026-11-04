# PRD-034 stock live-fixture identity proofs - LIVE-STOCK-FIXTURE-IDENTITY-001 (owner ruling 2026-11-04, section 4)

Identity/fixture certification ONLY (not PRD-035). Exactly ONE live pass per stock module, no reruns, both on the same tree.

| | |
|---|---|
| Branch / HEAD / tree | `arch/containment-rebuild-v2` / `9d88ff820e24c2c6a811ef3b35a9cf4316811c68` / see `revision.txt`; working tree clean (0 status lines) before, between and after; `kriya/` unchanged during both runs; `refs/kriya/verified` = `7eddc5d6e255` |
| Operator config | `~/.kriya/operator/graphify-qwen3coder-native-production.yaml` via `KRIYA_RELEASE_CONFIG` (SEC-009: read-only `authority inspect` re-run at the time of these proofs: "Trust file ...5fff250e61e7... is CURRENT and covers all pending fields"; `kriya authority approve` was never run) |
| Command | `scripts`: `run_stock.sh.txt` - `pytest -o addopts= -m live_model -p no:cacheprovider -ra -s --junitxml=... <module>` with `KRIYA_RELEASE_CONFIG` and the module's evidence dir |

## Identity proven BEFORE inference (read-only pre-check, then each module's own fail-closed `verify_identity` preflight, persisted as `preflight_identity.json` in both evidence dirs)
| Requirement | Result (MEASURED) |
|---|---|
| model | `qwen3-coder:30b-kriya-e52213655394` |
| adapter | `kriya-ollama-native/3` |
| effective context | 32768 (config `num_ctx` 32768; no 16384/4096 override: the stock fixture's old overrides are gone, `max_tokens` is the operator's own 16384 output budget) |
| qualification | QUALIFIED (real store `~/.kriya/qualifications`) |
| ModelRuntimeFingerprint | `7a02e748b2c53a8e02ef3174149a8dc076797e00e1f9d63b0d8fe4974bdbab3c`, exact = True |
| inference settings digest | `sha256:482067b2e0b4d3b7d4ebdfbf83ef8aeef79c3f07ec9d3b0e2e38298eb74a3b15` |
| valid git baseline | every fixture repo is `git init` + seed commit (`_git_baseline` / `_repo`); no plain-directory refusal occurred |
| config/fingerprint persisted | `batch5/evidence/preflight_identity.json`, `batch6/evidence/preflight_identity.json` |

## PRD-027 module - `tests/test_live_prd025_029_batch6.py`: 8 passed in 394 s (single attempt)
All eight evidence cases `LIVE_EXERCISED` (none NOT_LIVE_EXERCISED). The model SUCCEEDED, no fail-closed branch was needed:
PRD-027 real-embedding certification record `certification_status = CERTIFIED`, `live_status = LIVE_EXERCISED`, precision 0.5581 (target 0.5); the Developer prompt carried the golden evidence for all 7 Java files; PRD-025/026/028/029 cases LIVE_EXERCISED (PRD-029: quality gates passed, public_api contract record created; git base == head in the evidence field).

## PRD-028 module - `tests/test_live_prd020_024_batch5.py`: 5 passed in 302 s (single attempt)
PRD-020 lineage kept, gates passed; PRD-021 planner saw the grounded owner, no findings; PRD-024 `required` policy: pre-existing failure not classified new, `auto`: docstring edit not baselined; gates passed.
PRD-022 (NOT an additional run): actual branch recorded as `no_ownership_violation_retry` - the model did not take the duplicate-owner path in this single attempt, so `ownership_findings` is None; the fixture records the key (original defect (4)), and the branch that was actually taken is recorded here rather than assumed.

## Disposition
LIVE-STOCK-FIXTURE-IDENTITY-001 closed: the stock path resolves the qualified identity, asserts QUALIFIED/exact/32768/adapter in every module, git-baselines its fixture repos, and persisted one live pass per module. No product defect was exposed, no failure to classify, no rerun.
Not claimed: that the model takes every branch (PRD-022's duplicate-owner retry was not exercised), nor any PRD-035 matrix result.
