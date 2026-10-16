# Kriya Demo V2 — Final Validation

## Status

**VALIDATED**

Owner visual inspection: **PASS**

No known release blocker remains for the Demo V2 milestone.

## Repository

- Branch: `demo/v2`
- Pre-validation-record repository HEAD: `94bb673`
- Validated product-code HEAD: `b93f0d9`

- `b93f0d9` is the product-code / app-build commit on which the final canonical suite ran.
- `94bb673` contains documentation-only additions after `b93f0d9`. The delta `b93f0d9..94bb673` was inspected: it adds exactly two files, `handover/DEMO-V2/RELEASE-READINESS-AUDIT.md` and `handover/DEMO-V2/SPRING-IGNITE-PROTOCOL-GREENFIELD-PLAN.md`, and changes nothing else.
- The final validation-record commit itself is also docs-only (this file).
- Therefore no additional canonical suite is required solely for the validation-record commit.

## Canonical suite

```
11275 passed
15 warnings
2381.66s (0:39:41)
```

Suite command:

```
ulimit -n 256
.venv/bin/pytest -q -n 8 --dist loadgroup
```

Run at `b93f0d9`. The owner's earlier baseline at `a743d9f` was 11269 passed; the difference of 6 is the new reopened-project history tests (`tests/test_reopened_project_history.py`).

## Live Greenfield Demo 00

Run: `20261015T200748-08db1057`

Result: **PASS**

Objective evidence:

- Greenfield Java 17 Maven application generated.
- Exact source review completed by the owner.
- Build passed.
- 1 / 1 real test passed.
- Runtime command reviewed and approved.
- Application executed successfully.
- Observed output included `Hello from Kriya`.
- Final change committed (commit status `COMMITTED`).
- Result UI visually inspected by the owner after the rebuild.
- Owner visual inspection: **PASS**.

Preserved evidence: `handover/DEMO-V2/evidence/live-greenfield-success/`

## Authority / safety invariants validated

All PASS (detail and enforcing tests: `handover/DEMO-V2/RELEASE-READINESS-AUDIT.md`):

- Source review before executable candidate behavior.
- Reviewed bytes bound through build, test, runtime and final apply.
- A required runtime rejection cannot succeed or apply.
- Desktop runtime command review and native confirmation.
- Tool / egress confirmation remains fail-closed.
- Renderer authority boundary preserved.
- Provider abstraction qualification preserved.
- Result truth separates engineering evidence from the formal requirement ledger.
- Recent Runs works within the active Kriya state-directory model.

## Recent Runs architecture

Recent Runs is **state-directory scoped**.

- Authoritative source: the trace-store snapshot under the active Kriya state directory.
- Selection: `KRIYA_STATE_DIR` when provided, otherwise `~/.kriya/state`.
- It is **not** reconstructed from `<project>/.kriya/control/runs`.
- Scenario launchers can intentionally use isolated state directories. That isolation is by design and is not a defect.

Details: `handover/DEMO-V2/REOPENED-PROJECT-RUN-HISTORY-001.md`.

## Known non-blocking limits

- Requirement-ledger runtime closure remains conservative.
- Held-gate ordering does not yet cover every other approval trigger / resume path.
- Managed-service HITL verification fails closed.
- Planned tools requiring review stop at `NEEDS_REVIEW`.
- History is scoped to the active state directory.
- Published history snapshots can become stale until refresh.
- Expected non-zero runtime exits have a presentation limitation.
- Registered backlog remains tracked in `handover/BACKLOG_REGISTRY.csv`.

None of these is classified as a Demo V2 release blocker.

## Spring / Ignite next demo

Reference: `handover/DEMO-V2/SPRING-IGNITE-PROTOCOL-GREENFIELD-PLAN.md`

State: planning complete. First rehearsal not yet ready. Remaining preparation is demo / scenario work, not a reason to delay the Demo V2 milestone.

## Final owner decision

- Owner visual inspection: **PASS**
- Demo V2 validation: **PASS**
- Ready to freeze / tag: **YES**
