# PRD-010 Pytest and Live Verification

## Verdict
PYTEST_VERIFIED, LIVE_VERIFIED

## Scope
- Reopen commits: d22cb8e, de260b1 and 0aa946a.
- Live-CLI fixes: edef49e.
  - `model.qualification` is keyed on campaign identity.
  - `doctor --production` uses console-only logging.

## Results (user-reported, 2026-09-25, at edef49e)
- **Focused command** (PRD-009/010 union, including the two real-Docker doctor tests): PASSED.
- **Full suite** `.venv/bin/pytest`: PASSED.
- **Live gate:** PASSED, against real Ollama `qwen3-coder:30b`. Command:
  `KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b .venv/bin/pytest -m live_model -ra -s tests/test_live_production_doctor.py`
  - The fingerprint was stable across two probes.
  - Both model-binding checks reported UNAVAILABLE.
  - The config was built through `load_config()`.
- **Real CLI run** (`~/kriya-live-validation/prd010-doctor-live`, Maven/JDK 17, `runtime_profile: production`, SEC-009-approved):
  - `exit=1`, the JSON parsed, and every infrastructure check passed.
  - It surfaced the two defects fixed in edef49e.
  - A post-fix CLI re-run was not separately reported.
- Exact test counts were not reported.

## Conclusion
PRD-010 is VERIFIED. `production_ready` stays false by design until PRD-013/014 bind qualification to an exact
runtime.

Residual (pre-existing, not fixed): the packaged `logging.file` resolves against the CWD for every non-doctor command.
See the handover.
