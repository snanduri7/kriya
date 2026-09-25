# PRD-014 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 3: PRD-013 to PRD-016, one pytest stop for the whole batch; the PRD-016
allocator/dispatch reconciliation and adaptive budget are in - see the PRD-016 handover).

## Scope
Versioned qualification campaign keyed by the PRD-013 fingerprint (`kriya/core/model_qualification.py`,
`kriya model qualify|status`).

- **19 cases** (every one the spec lists, plus `context_capacity` for PRD-016 context tiers): plain completion, stop finish reason, structured JSON, multi-line JSON,
  native tool calls, multiple tool calls, tool-argument integrity (quotes, backslash, newline, tab, unicode),
  streaming assembly (deltas vs normalized content), output truncation, reasoning/hidden-think behaviour, raw
  full-file content, the anchored SEARCH/REPLACE protocol (parsed with the real `_split_fix_analysis_edit` and
  applied with the real `apply_anchored_edits`), malformed-output recovery (the real `_extract_json_value`), timeout,
  cancellation (cancel mid-stream, recorded, endpoint healthy afterwards), endpoint error semantics, endpoint restart
  (always UNAVAILABLE live: restarting the operator's server is out of scope; fixture-covered), tokenizer measurement,
  and near-window context capacity. That case measures its filler's real token rate on two small probes, then sends
  one request of about (window - 384) real tokens straight to the endpoint with the binding's `num_ctx`. Markers
  sit in the system message and at the end, and both must come back (a server that drops the front of an over-long
  prompt loses the first one).
- **Context tiers (PRD-016)**:
  - `kriya model qualify --context-window N` qualifies the same model at `num_ctx` N. That is its own fingerprint;
    requests carry the model binding's own options, and the run's budget policy is strict.
  - A tier is offered to the adaptive budget only with a current record passing `context_capacity` and every case
    the model's roles need (`context_tier_requirements`).
  - Adding the case bumped `QUALIFICATION_POLICY_VERSION` to `kriya-qualification/2`, so earlier records are stale.
  - Qualification requests now carry the qualified binding's own `extra_body`. Before this, a chain model was
    qualified with the primary's request options while its fingerprint used its own.
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
runtime-protocol cases (completion, truncation, endpoint error, timeout, cancellation, tokenizer, context capacity at
8192) and records the rest; whether the model then qualifies for the Developer role is evidence, not asserted.
To qualify your real deployment afterwards: `kriya model qualify --out report.json` for each role model
(`kriya model status` lists them).

## Residuals
- Endpoint restart is never exercised live (UNAVAILABLE by design; never required).
- Qualification is per model; a deployment with a long escalation chain needs each chain model qualified.
