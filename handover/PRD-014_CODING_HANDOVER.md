# PRD-014 Coding Agent Handover

## Status
IN_PROGRESS: the PRD-016 allocator/dispatch reconciliation is open (see PRD-016 handover); not ready for pytest. (batch 3).

## Scope
Versioned qualification campaign keyed by the PRD-013 fingerprint (`kriya/core/model_qualification.py`,
`kriya model qualify|status`).

- **18 cases** (every one the spec lists): plain completion, stop finish reason, structured JSON, multi-line JSON,
  native tool calls, multiple tool calls, tool-argument integrity (quotes, backslash, newline, tab, unicode),
  streaming assembly (deltas vs normalized content), output truncation, reasoning/hidden-think behaviour, raw
  full-file content, the anchored SEARCH/REPLACE protocol (parsed with the real `_split_fix_analysis_edit` and
  applied with the real `apply_anchored_edits`), malformed-output recovery (the real `_extract_json_value`), timeout,
  cancellation (cancel mid-stream, recorded, endpoint healthy afterwards), endpoint error semantics, endpoint restart
  (always UNAVAILABLE live: restarting the operator's server is out of scope; fixture-covered), tokenizer measurement.
- **Evidence**: PASS/FAIL/UNAVAILABLE per case with evidence and measured limits (verified tool-argument size,
  reasoning tokens, ASCII and non-ASCII bytes-per-token floors). UNAVAILABLE is never PASS; a crashing case is FAIL.
  Model output is never executed on the host (structural checks only).
- **Binding and staleness**: record = fingerprint digest + `MODEL_PROTOCOL_ADAPTER_VERSION` +
  `QUALIFICATION_POLICY_VERSION` + schema. Any drift makes it STALE (or MISSING for a new identity); measured limits
  are used only from a current record. A non-exact runtime cannot be qualified (`RUNTIME_NOT_EXACT`).
- **Store**: outside any workspace (`~/.kriya/qualifications/<digest>.json`, `KRIYA_QUALIFICATION_HOME`), refused
  inside the workspace, like SEC-009/TOOL-002 approvals.
- **Role requirements**: base (completion, stop, truncation, reasoning, endpoint errors) + role (Developer: full-file,
  anchored edit, malformed-output recovery; Planner: malformed-output recovery) + every protocol the resolved
  capability profile enables (tools, JSON, multi-line JSON, streaming). No vendor/model family is hard-coded.
- **Production gate**: `doctor --production` `model.runtime_fingerprint` PASSes for an exact runtime;
  `model.qualification` checks every model of every role (binding + escalation chain) - FAIL on missing/stale/failed
  (`MODEL_NOT_QUALIFIED`, `QUALIFICATION_STALE`), UNAVAILABLE when a runtime cannot be identified. Production
  readiness is now reachable (proven in a test) once every role is qualified.
- **Separation**: offline fixture conformance (`model_capabilities`, this module's evaluator fixtures) is separate from
  live qualification, which only `run_qualification` against a real endpoint produces. A `--case` subset is reported
  but never saved as a record.

## Files
- New: `kriya/core/model_qualification.py`
- Changed: `kriya/cli.py` (`model` group), `kriya/production_doctor.py`
- Tests: `tests/test_prd014_model_qualification.py` (new), `tests/test_production_doctor.py` (qualification tests
  rewritten for the new semantics: a campaign name is still never a qualification; PASS only with a current record)

## Evidence (plain runner)
- `test_prd014_model_qualification`: all pass (each case's PASS and FAIL state, UNAVAILABLE states, store refusal,
  every staleness input, role requirements, CLI).
- `test_production_doctor`: all pass run individually (the plain runner shares one qualification store across tests;
  pytest's conftest isolates it per test).
- `kriya model status` against the local Ollama (metadata only): every role MISSING, exit 1.

## Live test (user's terminal; real model; ~5-15 minutes)
```bash
set -o pipefail
mkdir -p handover/evidence/BATCH3/user-live
KRIYA_BATCH3_EVIDENCE_DIR=handover/evidence/BATCH3/user-live \
KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \
.venv/bin/pytest -m live_model -ra -s tests/test_live_prd013_016_model_runtime.py tests/test_live_production_doctor.py \
  2>&1 | tee handover/evidence/BATCH3/user-live/batch3-live.log
```
The PRD-014 test writes the full record to `prd014-qualification.json` (the report to hand over). It asserts the
runtime-protocol cases (completion, truncation, endpoint error, timeout, cancellation, tokenizer) and records the
rest; whether the model then qualifies for the Developer role is evidence, not asserted.
To qualify your real deployment afterwards: `kriya model qualify --out report.json` for each role model
(`kriya model status` lists them).

## Residuals
- Endpoint restart is never exercised live (UNAVAILABLE by design; never required).
- Qualification is per model; a deployment with a long escalation chain needs each chain model qualified.
