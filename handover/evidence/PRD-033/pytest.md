# PRD-033 test evidence (coding agent, Wave 7, 2026-09-27)

| Run | Revision | Result |
|---|---|---|
| Full `.venv/bin/pytest -q` (venv on PATH) | `5562d05` | **6808 passed, 0 failed**, 61 deselected (28:09) |
| `tests/test_prd033_metrics.py` + `tests/test_prd033_events_and_cli.py` | `5562d05` | 29 passed |
| Mutation check | — | 10/10 caught (after the tests were strengthened for M6/M7/M8) |
| Live: `KRIYA_METRICS_EVIDENCE_DIR=handover/evidence/PRD-033/live pytest -m live_model tests/test_live_prd033_metrics.py` | `5562d05` working tree | passed. 3 real runs (qwen3-coder:30b, runtime `ea90552d…` QUALIFIED). The report was derived twice from the persisted rows with the same content digest `82b7f3a7…`; generation runs 3, final verified success 2/3 (the third run is the injected always-rejecting gate), first-pass compile 2/2 (`live/metrics-report.{json,md}`) |
| PRD-032 fix 3 (`16bc723`): planner/prompt/enforce suites | `16bc723` | 767 passed |
| ruff / pylint | every commit | 0 findings |
