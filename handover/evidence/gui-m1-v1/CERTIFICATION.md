# Kriya GUI M1 integrated milestone — verification of `main` d842c27

Milestone tag `kriya-gui-m1-v1` (annotated, tag object 4c653e6) → **d842c27c506f4f4d26b62ce185ab83ec1c5b1444**.
Built on the Graphify E2E milestone `kriya-graphify-e2e-local-v1` → be3cbb2 (unchanged). This evidence lives on the
separate branch `evidence/gui-m1-v1` (from d842c27); the certified commit and both tags are not moved.
Model calls during all verification below: **0**.

## Merge provenance

| Item | Value |
|---|---|
| GUI source checkout | `…/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2`, branch `codex/fix-demo1-attribution` @ 75e3936 (44 GUI/KUP commits on base 61a867f; the same long-lived branch whose earlier attribution work, incl. c0384ef, was already in main) |
| GUI design authority | 2893b6f `docs(gui): preserve GUI M1 design authority` — handover/GUI-DESIGN-DISCUSSION/ (10) + handover/GUI-D4-READ-STRATEGY/ (5), previously untracked-only, cited by 12 committed files |
| GUI branch | `feature/gui-m1` @ 2893b6f on GitHub (45 commits beyond 61a867f), pushed via the canonical GitHub remote (the checkout's local `origin` points at the local AntiGravity repo and was not used) |
| Integration | `integration/gui-m1` from origin/main be3cbb2; `git merge --no-ff origin/feature/gui-m1` → **0760777** (parents be3cbb2 + 2893b6f) |
| Conflict | one, `handover/FILE_INTEGRITY_CONTRACT_001.md`: kept main's extended SAFE_NON_SOURCE row + the GUI's new KUP row (GUI side verified to equal the base row; result = main +1 line) |
| Auto-merge audit | `tests/test_file_integrity_contract_001.py` audit list holds the KUP site and all 7 REG-R1/M1/FS-1/B2/B3/GR-R1A sites exactly once; `kriya/cli.py` merged-vs-main == the UI's 142 changed lines and merged-vs-UI == main's 253 |
| Integration fix | **d842c27** GUI-INTEGRATION-ENV-001 (see GUI-INTEGRATION-ENV-001.md) |
| Backend core changed | NO — outside `ui/` only `kriya/kup/*` (new), `kriya/cli.py`, tests, pyproject marker, handover docs |
| main | fast-forward be3cbb2..d842c27 (no force, no extra merge commit); `origin/main^{tree}` == `integration/gui-m1^{tree}` = 290729bd |

Not carried into the merge (left in the GUI checkout, inventoried in UI_CHECKOUT_UNTRACKED_INVENTORY.txt, 82
files): old backend evidence (65), Archive.zip, process_boundary_gate.patch, incidental (2), test pollution (13).

## Environment

Fresh `.venv` in the integration worktree: Python 3.14.6, pip 26.2.1, `pip install -r requirements.txt` (pip-compile
lock, dev extras, 69 pins — installed set == lock) + `pip install --no-deps -e .`; `pip check` clean. Node v26.0.0,
npm 11.12.1, `npm ci` from `ui/package-lock.json` (v3) into an absent `ui/node_modules`: 288 packages. JDK 17.0.10
for `java:check`. The stale `Kriya-main-demo/.venv` and the GUI checkout's `.newvenv` were not used.

## Gates

| Gate | Revision | Result | Evidence |
|---|---|---|---|
| KUP targeted (test_kup_{acquisition,cli,host_environment,write_boundary}, file_integrity_contract_001, prd034_certification) | 0760777; re-run on main d842c27 | **392/392** both times | — |
| ruff 0.16.0 / pylint 4.0.9 | 0760777; re-run on main | **PASS / PASS** both times | — |
| Full Python suite (`-n 8 --dist loadgroup`) | 0760777 (d842c27 changes only ui/ and three fixture docstrings) | **9345 passed / 0 failed / 0 skipped**, 556.95 s | full_python_suite.txt.gz |
| npm ci | d842c27 | **PASS** | — |
| npm run check (fixtures, check:generated 42 files, typecheck, lint, check:shared-deps, test:scripts, workspace tests, java:check 40 sources) | d842c27 | **PASS** | npm_run_check.txt.gz |
| Vitest (kup 66, shared 66, test-host 36+2 skipped, standalone 45) | d842c27 | **213 passed / 2 skipped** — the skips are the opt-in real-store acceptance suite (`describe.skip` unless a real-store export dir is supplied) | npm_run_check.txt.gz |
| node:test GUI-INTEGRATION-ENV-001 | d842c27; re-run on main | **3/3** | — |
| fixtures:serializer:check (`KRIYA_PYTHON=.venv/bin/python`) | d842c27 | **PASS** — run_events (4, incl. model.transition with a real ModelRequestProfile), gate_outcomes (10), evidence_records (2) all current | — |
| REG-R1/R2 + validation baseline + Surefire | d842c27; re-run on main | **141/141** both times | — |
| Graphify deterministic replay (frozen candidate of run 20261007T093122-df65c5d3, sealed M1 observations) | d842c27 and main | acceptance **5/5**, external **5/5** (A–E), regression **76/76**; candidate byte-identical to the frozen applied workspace; both sealed REG-R2 decisions re-decided identically (s1 FLAKY_PREEXISTING, s2 RESOLVED_FAILURE, non-blocking) | graphify_replay_on_main.json, graphify_candidate.*.txt |

`npm run check` does not run `fixtures:serializer:check` (it needs a Kriya Python); it was run separately. Whether it
should join a canonical cross-language gate is the open decision GUI-CI-GATE-001 (not a defect).

## Contract compatibility (UI ↔ Kriya, CLI JSON only)

| Contract | Status | Basis |
|---|---|---|
| gate-outcome (type/success/output) | **COMPATIBLE** | serializer fixture byte-current on integrated code (compile, test, targeted_test, regression_test, run_verification, goal_spec_compliance, test_selection) |
| model.transition / verification events | **COMPATIBLE** | model.transition with a real ModelRequestProfile and the verification gates byte-current |
| run-event contract (kind/created_at/details) | **COMPATIBLE** | run_events fixture current; run_events.py unchanged |
| traces.db consumption | **COMPATIBLE** | core/trace.py unchanged since 61a867f; KUP tests pass |
| KUP CLI: snapshot.acquire / list --verify / history.list / history.detail / snapshot.verify / snapshot.prune | **COMPATIBLE** | live smoke with the integrated `kriya` on a store seeded by `tests/_kup_fixtures.seed_store` (7 runs): every operation returned a v1 envelope, no error; history paged 3 + next_cursor; detail carried gate_outcomes/evidence_records/failure_report; verify reported digest_verified |
| `kriya runs status --json --kup-version 1` | **COMPATIBLE** | live smoke: v1 workspace.status envelope (CLEAN) |

## Observed, deferred

TEST-WORKTREE-POLLUTION-001 (recorded on evidence/graphify-e2e-local-v1): reproduced again — the integration
worktree had no untracked files before the full Python suite and `node_modules/`, `package.json`,
`package-lock.json` after it. Not fixed (deferred by owner decision).
