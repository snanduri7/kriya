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
- **Post-fix real CLI re-run** (same workspace, at edef49e):
  - `exit=1`.
  - The non-PASS checks were exactly:
    - `model.runtime_fingerprint` UNAVAILABLE;
    - `model.qualification` UNAVAILABLE;
    - `models.role_independence` WARN;
    - `semantic.precision_boundary` WARN.
  - `ls -a` afterwards showed only `.git kriya.yaml pom.xml`: nothing was created, and log lines went to stderr only.
  - Both live-CLI defects are confirmed fixed on the real command path.
- Exact test counts were not reported.

## Conclusion
PRD-010 is VERIFIED. `production_ready` stays false by design until PRD-013/014 bind qualification to an exact
runtime.

The residual recorded here at edef49e (the packaged `logging.file` resolving against the CWD) is closed by the
logging/state cleanup below.

## Logging/state cleanup verification (user-reported, 2026-09-25, at ba86812)
- **Scope:** c7b5734, ed93938, 0ddf55f, e89ccb3, ba86812.
  - Logs: `KRIYA_LOG_DIR` > `logging.directory` > `~/.kriya/logs`.
  - State: `KRIYA_STATE_DIR` > `paths.state` > `~/.kriya/state`.
  - `paths.logs` and `logging.file` removed, with typed errors and a repository guard.
  - Explicit-only legacy `traces.db` migration.
- **Focused command** (23 files, listed in the handover): PASSED.
- **Full suite** `.venv/bin/pytest`: 5302 passed, 8 deselected, 143 warnings, 0 failed (1079.43s).
- **Verdict:** PRD-010 logging/state cleanup is VERIFIED at ba86812. It is not reopened without new failing evidence.
- **Operational, not a code blocker:** 17 configs under `~/kriya-live-validation` still name `paths.logs` or
  `logging.file`. Fix them before any live doctor or demo run.

