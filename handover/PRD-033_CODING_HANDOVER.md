# PRD-033 Coding Agent Handover — Production Telemetry and False-Success Metrics

## Status

**LOCALLY_VERIFIED / READY_FOR_USER_REVIEW** (Wave 7, local and unpushed). Tracker: `READY_FOR_PYTEST_VERIFICATION`.

## Source identity

- **Base:** `f86190e` (PRD-032 closure).
- **Commits:**
  - `16bc723`: PRD-032 fix 3, found while building PRD-033's live tier; see below.
  - `5562d05`: the implementation.
  - The closure commit.

## Scope implemented

**Instruction:** `tasks/PRD-033_Production_Telemetry_and_False-Success_Metrics.md`, Wave 7 directive §5, and the design in `handover/PRD-033_DESIGN_ANALYSIS.md`.

1. **Metrics from existing evidence.** Each is derived from persisted evidence; UNAVAILABLE when the evidence does not exist:
   - first-pass compile;
   - final verified success;
   - false and wrong success (adjudicated);
   - regression escape (adjudicated);
   - D1 and authority rejections;
   - context insufficiency;
   - tool-call/protocol parse failures;
   - structured-output failures;
   - targeted and full retries;
   - no-progress termination;
   - fallback frequency;
   - LLM calls;
   - tokens in and out;
   - wall time;
   - verification share (compile and runtime-verification timings; tests are untimed, and the metric says so);
   - containment failures;
   - resume invalidation (RunRecords via `--workspace`);
   - human escalation.

   Metrics added since the original PRD:
   - static-analysis outcome counts, including ACCEPTED_RISK;
   - incomplete coverage;
   - stale or missing static-analysis authorization at commit;
   - waiver use;
   - chaos invariant failures (from a PRD-032 report).
2. **Keys.** Model metrics are keyed by exact runtime digest, inference-settings digest, role and task class. Run outcomes are also keyed by the Developer identity and task class.
3. **No proprietary content.** Projections select only typed fields and whitelisted event keys; a canary test covers both the projection and the report.
4. **Local CLI.** `kriya metrics report|adjudicate|adjudications` produces deterministic JSON and Markdown with a content digest.
5. **Thresholds.** Evaluation mechanics are provided with no shipped values: NOT_CONFIGURED; PASS, FAIL, INSUFFICIENT_EVIDENCE or UNAVAILABLE per threshold; FAIL, INCONCLUSIVE or PASS overall. The file must be the operator's and lie outside the workspace. PRD-036 consumes this.
6. **Three typed events**, each for a decision or terminal condition that had none:
   - `workspace_commit.failed`;
   - `approval.decision`;
   - `review.pre_approval_unattached`, which closes **PRE-APPROVAL-REVIEW-REFUSAL-001**.
7. **Failure-report categories** for `static_analysis_blocked|unknown|unavailable` (PRD-031A had left them unclassified) and `workspace_commit`.

**Not implemented (with reason):**
- A deterministic false-success producer: none exists in Kriya. The `deterministic` source is reserved and disclosed.
- LEGACY-TRACES-MIGRATION-001 stays an operator action per its registry target. The report names a legacy database (`generated.legacy_trace_db_present`) and never reads it.

## PRD-032 fix 3 (found by the live tiers while building this; separate commit `16bc723`)

- **Defect:** the Planner prompt showed the closed plan vocabularies only by example. The real model invented `verifier_kind: "file_check"` and was refused (`planner_output_schema_invalid`).
- **Fix:** the prompt now states every closed vocabulary, derived from the `plan_schema` enums.
- **Regression test:** `tests/test_prd032_planner_vocabulary.py` fails without the fix.
- **Live effect:** with fix 2 (`root_path`), 8 of 8 sampled real runs planned successfully, where 2 of 5 and 2 of 3 had been refused before.

## Deriver defects found by reading the live report (fixed before commit)

- **Fallback transitions.** `model.transition` also fires for the initial request; only `fallback: true` counts now, and skips are reported separately.
- **Verification share.** A measured `0.0` without timed validators was misleading. It is now UNAVAILABLE without timings, and its coverage is labelled.

## Findings recorded (registry P3)

- **TRACE-ENFORCE-SUBTASK-LINKAGE-001:** enforce subtask rows are not linked to their enforce run; the report never mixes the populations.
- **FINAL-REVIEW-BACKEND-ERROR-001:** a backend error in the post-commit final review raises instead of producing a typed outcome.

## Files changed

- **Production:**
  - `kriya/metrics/{__init__,evidence,adjudication,derive,thresholds,report}.py` (new);
  - `kriya/cli.py` (the `metrics` group);
  - `kriya/workflow/workflow.py` (3 events);
  - `kriya/workflow/failure_reporting.py`;
  - `kriya/agents/agent.py` (fix 3).
- **Tests:**
  - `tests/test_prd033_metrics.py` (20);
  - `tests/test_prd033_events_and_cli.py` (9);
  - `tests/test_live_prd033_metrics.py` (live);
  - `tests/test_failure_reporting.py` (pinned vocabulary extended with the 4 real types);
  - `tests/test_prd032_planner_vocabulary.py`.
- **Docs:** `docs/design.md` §2.9f, `docs/user_guide.md` §2.1i, `CLAUDE.md`.
- **Registry:** 1 closed, 2 added.

## Tests run by the coding agent

| Command | Result |
|---|---|
| PRD-033 unit + event/CLI tests | 29 passed |
| Mutation check (10 mutations: event omissions, rejected/unavailable outcome, UNAVAILABLE→0, first-pass semantics, adjudication window, a leaked detail key, the SUCCESS-only refusal, the INCONCLUSIVE rollup) | 10/10 caught (3 survived at first; the tests were strengthened) |
| Adjacent suites (cli smoke, failure reporting, traces, approval audit, review, prompt-fit 001c, prd032/033, workflow, prd030, prd031a, bootstrap, qual identity, network inventory, strict doubles) | 1298 + 43 passed (the pinned-vocabulary test was updated for the new types) |
| Planner/prompt/enforce suites after fix 3 | 767 passed |
| Live: `KRIYA_METRICS_EVIDENCE_DIR=… pytest -m live_model tests/test_live_prd033_metrics.py` | passed. 3 real runs; the report derived twice from the persisted rows has the same digest (`82b7f3a7…`); keyed by runtime `ea90552d…` |
| Full `.venv/bin/pytest` at `5562d05` | see `handover/evidence/PRD-033/pytest.md` |
| ruff / pylint | 0 findings |

## Evidence artifacts

`handover/evidence/PRD-033/`:
- `live/metrics-report.{json,md}`;
- `pytest.md`.

## Verification-agent handoff

**Pytest:**
- `.venv/bin/pytest tests/test_prd033_metrics.py tests/test_prd033_events_and_cli.py tests/test_failure_reporting.py`
- the full suite.

**Live:** the command above. Re-deriving the report from the same state directory must give the same digest; counts must match the runs made; the model rows must carry the exact runtime and settings digests.
