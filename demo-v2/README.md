# Demo V2

Repository-owned, versioned source for the Kriya demonstration scenarios. It replaces the loose,
frozen-runtime packages under `~/kriya-live-demo/` (kept there for provenance only; see `docs/ASSET_INVENTORY.md`).

**Product baseline:** tag `kriya-v1.2.0-pre-modularization` (`1064b9e`). Backend Readiness V2 is unchanged by this work:
`BACKEND SOLVER READINESS: FAIL`, `SOLVER REPEATABILITY: NOT ESTABLISHED`. Demo V2 does not qualify the solver and does not
claim production readiness.

**Owner rehearsal:** start with `OWNER_RUNBOOK.md` (one prepare command, copy/paste steps for A, B and C, one reset command).

## Scenarios

| ID | Title | Model | Network | Status |
|---|---|---|---|---|
| `01-normal-coding` | Normal GUI coding flow (brownfield bug fix) | live local model | none | **REHEARSED ONCE, PASS** (live, 2026-10-14) |
| `02-knowledge-skill` | Terminal acquisition, GUI skill view, offline task run (terminal) | live local model | acquisition step only | **Rehearsed once from empty state: pass, with caveats** (acquired content varies; see `handover/DEMO-V2/FINDINGS.md` F11, F12) |
| `03-controlled-repair` | Controlled repair of a wrong model candidate | **scripted stub (CONTROLLED)** | none | **REHEARSED, PASS, REPRODUCIBLE** (2026-10-14) |

The status column is the truth. A scenario is only "validated" after the checks in its own `SUCCESS CRITERIA` were measured
on a recorded run; nothing here is validated by having been written down.

## Layout

```
demo-v2/
  README.md                 this file
  demo-manifest.yaml        machine-readable index: baseline, scenarios, fixtures, scripts
  scenarios/<id>/SCENARIO.md  the contract for one scenario (every field below is mandatory)
  scenarios/<id>/...        goal text, configuration template, scenario scripts
  fixtures/<name>/fixture.env  identity of a fixture (commit, target-file hash, provenance); the fixture itself is NOT in git
  expected/                 expected-evidence descriptions that a verify step checks
  scripts/prepare_demo.sh   materialise fixtures into the work area and render configuration
  scripts/reset_demo.sh     return one scenario to its pristine starting point
  scripts/verify_demo.sh    check deterministic prerequisites (pre) and outcomes (post)
  scripts/rehearse_gui.sh   LIVE rehearsal of scenario 01 through the desktop app (real model; minutes); never run by the other scripts
  docs/                     asset inventory, extension contract, client boundary
```

## What is and is not in git

In git: contracts, goal text, configuration templates, fixture identities, scripts. Not in git, by design: cloned
repositories, build output, virtualenvs, dependency caches, model files, run logs and evidence stores. They are
materialised under a work area (`DEMO_V2_WORK`, default `~/kriya-demo-v2-work`) outside the repository.

## Mandatory fields of every `SCENARIO.md`

`ID`, `TITLE`, `PURPOSE`, `STATUS`, `PRECONDITIONS`, `PRODUCT HEAD/TAG COMPATIBILITY`, `MODEL REQUIREMENT`,
`NETWORK REQUIREMENT`, `RESET`, `RUN`, `EXPECTED EVENTS`, `EXPECTED EVIDENCE`, `SUCCESS CRITERIA`, `FAILURE DIAGNOSIS`.
`tests/test_demo_v2_assets.py` fails if a scenario omits one or if the manifest and the files disagree.
Nothing a presenter needs may live only in speaker notes.

## Quick start (scenario 01 only, no model involved yet)

```bash
demo-v2/scripts/prepare_demo.sh --scenario 01      # materialise + verify the fixture identity
demo-v2/scripts/verify_demo.sh  --scenario 01 --phase pre
```

Live phases (a model run, the GUI) are in each scenario's `RUN` section and are never started by these scripts.
