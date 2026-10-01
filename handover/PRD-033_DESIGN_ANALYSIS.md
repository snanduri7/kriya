# PRD-033 — Production Telemetry and False-Success Metrics: Design Analysis

**Wave:** 7. **Spec:** `tasks/PRD-033_Production_Telemetry_and_False-Success_Metrics.md` (P1, Live test REQUIRED, depends on PRD-032), plus the Wave 7 directive §5, which is more detailed and wins where the two differ.

## 1. The source-of-truth rule

Telemetry is derived, never a second truth:

`persisted evidence (traces.db runs rows, RunRecords) + operator adjudications → deterministic MetricDeriver → MetricReport (JSON + Markdown, content-digested) → threshold evaluator`

Nothing below changes what a run decides.

**Production code gains exactly three typed events.** Each records a decision or terminal condition that currently leaves no structured evidence (Global Contract rule 9):

1. `workspace_commit.failed`, in PRD-032's `_raise_terminal_commit_stop`: the reason code (for example `STATIC_ANALYSIS_EVIDENCE_STALE`) otherwise exists only as free text.
2. `review.pre_approval_unattached`: the pre-approval Reviewer failed or was refused, and the approval went ahead with no automated review. This closes PRE-APPROVAL-REVIEW-REFUSAL-001: the event plus a line in the approval context naming the reason.
3. `approval.decision`, at the human-approval gate: `approved`, `rejected` or `unavailable`, together with why approval was required (the mode, a sensitive-path match or diff size). Human escalation otherwise has no structured record.

## 2. What evidence exists (source analysis)

| Store | Content used | Never read |
|---|---|---|
| `traces.db` `runs` (`trace_db_path`) | `status`, `failure_category`, `duration_sec`, `generation_metrics` (llm calls, tokens, validator wall time, retry counters, engineering route), `failure_report` (typed failure families), `gate_outcomes` (only `type`, `attempt`, `success`), `run_events` (typed kinds and details), `milestone_group_id` | `goal`, `prompt_rendered`, `retrieved_chunks`, gate `output`, `failed_content`, `attempted_edits`, `file_locations` (proprietary content) |
| `model.role_metrics` events (PRD-018) | per (role, model, exact runtime digest, inference settings digest): calls, protocol failures, schema failures, structured outcomes, tokens, attempts, first-pass | |
| RunRecords (`<workspace>/.kriya/control/runs`) | lifecycle, commit results, `resume_decision` (invalidated stages), RECOVERED | run content beyond ids and codes |
| Adjudication store (new, operator-written) | human or deterministic verdicts per trace run id | |
| Chaos report (optional input, PRD-032) | per-scenario verdicts | |

## 3. Metrics (each names its source; UNAVAILABLE when the evidence does not exist)

**Run outcomes** (per task class and per Developer runtime identity):
- final verified success: `status == success`;
- first-pass compile: attempt 1's compile gate outcome;
- targeted and full retries;
- no-progress termination;
- failure categories;
- wall time;
- verification share (validator wall time / total wall time);
- containment failures (`containment_setup_failed`);
- context insufficiency (`CONTEXT_BUDGET_UNSATISFIABLE` / `OUTPUT_BUDGET_UNSATISFIABLE` failure families plus `context.request_fit` reductions);
- authority/D1 rejections (`operation_authority.rejected`, `unauthorized_generation_target`, `authority.expansion` non-granted);
- fallback frequency (`model.transition`, `model.fallback_selection`);
- human escalation (`approval.decision`);
- unattached pre-approval review.

**Model protocol** (per exact runtime + inference settings + role + task class, from the role-metric rows):
- LLM calls;
- input and output tokens (with the estimated-token share);
- latency;
- tool-call and protocol failures (`protocol_failures`);
- structured-output failures (`schema_failures`, typed structured outcomes);
- first-pass success.

**Static analysis** (from `static_analysis.result` events and `workspace_commit.failed`):
- outcome counts (PASS / PASS_WITH_WARNINGS / BLOCKED / UNKNOWN / UNAVAILABLE / ACCEPTED_RISK);
- incomplete coverage;
- stale or missing authorization at commit;
- waiver usage.

**Adjudicated:**
- false (wrong) success;
- regression escape;
- confirmed success.

These come only from adjudication records whose source is `human` or `deterministic`. The rates are over adjudicated runs, and the report shows adjudication coverage. With nothing adjudicated they are UNAVAILABLE, never 0%. Reviewer or LLM opinion is never a source.

**Chaos:** invariant failures per family, when a chaos report is supplied.

**RunRecord** (with `--workspace`):
- lifecycle and commit-result distribution;
- resume invalidation (a `resume_decision` with invalidated stages);
- refused resumes;
- RECOVERED runs.

**Task class:** the run's engineering route kind (`generation_metrics.engineering_route.kind`), else `unclassified`. Milestone-plan rows count as `milestone_plan`, enforce rows as `enforce`.

## 4. Adjudication store (trusted, outside the workspace)

- **Location:** `~/.kriya/adjudications/adjudications.json` (`KRIYA_ADJUDICATION_HOME`), written only by `kriya metrics adjudicate`. This mirrors the PRD-031A waiver store: an atomic write, a record digest, and a store that fails closed as a whole on tampering.
- **Record:** trace run id, verdict (`false_success` | `regression_escape` | `confirmed_success`), source (`human` | `deterministic`), adjudicator, evidence reference and time.
- **Refusals:** a verdict for an unknown run id, and `false_success` / `regression_escape` for a run that did not end SUCCESS.
- **Limitation:** Kriya has no deterministic producer yet, so `deterministic` is reserved for tooling and disclosed.

## 5. Thresholds (mechanics only; no values invented)

- **File:** `--thresholds FILE`, a local, operator-owned JSON/YAML file that must lie outside the workspace. It lists `{metric, comparator (max|min), value, min_samples}`.
- **Evaluation:** each threshold is PASS, FAIL, INSUFFICIENT_EVIDENCE (below `min_samples`) or UNAVAILABLE (the metric has no evidence).
- **Without a file:** the status is `NOT_CONFIGURED`. No release threshold values are user-approved, so none ship. PRD-036 consumes this.

## 6. Surface

- `kriya metrics report [--since ISO] [--workspace DIR]... [--chaos-report FILE] [--thresholds FILE] [--out DIR] [--json]` writes `metrics-report.json` and `.md` (content digest, run window, no timestamps in content). The exit code is 0, or 1 when a configured threshold FAILs.
- `kriya metrics adjudicate RUN_ID --verdict ... --evidence ... --adjudicator ...` asks for confirmation (`-y`).
- `kriya metrics adjudications`.
- **Module:** a new package `kriya/metrics/` (`derive.py`, `adjudication.py`, `thresholds.py`, `report.py`). `kriya/workflow` never imports it; it only reads the stores.

## 7. Backlog

- PRE-APPROVAL-REVIEW-REFUSAL-001 is closed here.
- LEGACY-TRACES-MIGRATION-001 stays an operator action. The report names a legacy database when one exists (the existing `historical_default_trace_db`), and never reads or migrates it.

## 8. Non-goals

- No remote telemetry.
- No new truth about run outcomes.
- No LLM-derived verdicts.
- No threshold values.
- No change to routing or qualification.

## 9. Tests

- **Deriver:** unit tests over synthetic trace rows for every metric, the UNAVAILABLE and NOT_CONFIGURED paths, key isolation and proprietary-field exclusion (a canary goal/prompt/output string never appears in a report).
- **Adjudication:** store tamper, refusals and the CLI.
- **Thresholds:** evaluation.
- **Reproducibility:** the same rows give the same digest.
- **Event tests:** for each of the three events, on success and on refusal (quality bar rule 2).
- **Live:** a small real task set into an isolated state directory; derive twice and require the same digest; the counts match the runs made.
