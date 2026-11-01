# Wave-5 live-test wrappers (evidence copies, NOT part of the test suite)

These are the exact wrapper modules used on 2026-11-01 to run the repo's signed live tests (tests/test_live_prd020_024_batch5.py,
tests/test_live_prd025_029_batch6.py) UNCHANGED at the Gate-E qualified identity. Only the `cfg` model binding, per-test state
isolation and (PRD-027 b, PRD-028) a git baseline differ from the stock fixtures.

They are stored with a `.txt` suffix so ruff and pytest never pick them up (they are evidence, not lint-clean test code; the
quality bar requires `ruff check .` to exit 0, and the first commit of these files as `.py` broke that). To re-run, copy
`*.py.txt` to a scratch directory under their original `.py` names and use the `run-a4*-live.sh` scripts here
(they reference `~/kriya-gate-e-2026-11-01/scripts/live_a4`).
Rows that own making the stock fixtures not need this: LIVE-STOCK-FIXTURE-IDENTITY-001 (due before PRD-034 completes).
