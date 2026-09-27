# Kriya production metrics (PRD-033)

- Content digest: `82b7f3a7e2f154d03d32b1fa66c51d9f79944bf697c2c78265dded325b02d4d9`
- Evidence window: 3 trace rows, 2026-09-27 21:10:58 .. 2026-09-27 21:12:24
- Thresholds: **NOT_CONFIGURED**

Derived only from persisted evidence. False success comes only from deterministic or human adjudication, never from a model. UNAVAILABLE means the evidence does not exist (never zero).

## Generation runs

| Metric | Value |
|---|---|
| Runs | 3 |
| Final verified success | 0.6667 (2/3) |
| First-pass compile | 1.0 (2/2) |
| No-progress termination | 0.0 (0/3) |
| Context insufficiency (budget refusals) | 0 |
| Prompt-fit reductions | 0 |
| Authority/D1 rejections | 0 |
| Containment failures | 0 |
| Fallback transitions | 0 |
| Fallback skips (incompatible) | 0 |
| Human escalations | UNAVAILABLE: no approval.decision events (runs without human approval, or traced before PRD-033) |
| Unattached pre-approval reviews | 0 |
| Commit failures (reason) | {} |
| LLM calls | 18 |
| Wall time (s) | n=3 mean=37.656 median=27.416 max=68.603 |
| Verification share (compile + runtime verification timings; tests untimed) | 0.0 |
| Retries (full-set / targeted) | 4 / 3 |
| Failure categories | {"quality_gates_exhausted": 1} |

## Enforce terminal outcomes

UNAVAILABLE: no enforce terminal rows

## By task class

| Task class | Runs | Final verified success | First-pass compile |
|---|---|---|---|
| task | 3 | 0.6667 (2/3) | 1.0 (2/2) |

## By Developer runtime identity (runtime digest | inference settings digest)

| Identity | Runs | Final verified success | First-pass compile |
|---|---|---|---|
| `ea90552d45f9c181a06512eb628adf89e138f25ce58b38ff54c0789e58264276|sha256:ed7bfc09816e127d6e9743c0b0ef2ca39e6161f6d3645a14e9137474a90cad7b` | 3 | 0.6667 (2/3) | 1.0 (2/2) |

## Model protocol (exact runtime, inference settings, role, task class)

| Runtime | Settings | Role | Task class | Calls | Protocol/tool-call failures | Structured-output failures | Tokens in/out | First-pass |
|---|---|---|---|---|---|---|---|---|
| `ea90552d45f9` | `sha256:ed7bf` | architect | task | 3 | 0.0 (0/3) | 0.0 (0/3) | 3644/269 | UNAVAILABLE: no first attempt was charged to this identity |
| `ea90552d45f9` | `sha256:ed7bf` | developer | task | 11 | 0.0 (0/11) | 0.0 (0/11) | 21653/741 | 0.6667 (2/3) |
| `ea90552d45f9` | `sha256:ed7bf` | planner | task | 3 | 0.0 (0/3) | 0.0 (0/3) | 8244/1415 | UNAVAILABLE: no first attempt was charged to this identity |
| `ea90552d45f9` | `sha256:ed7bf` | spec_compliance | task | 2 | 0.0 (0/2) | 0.0 (0/2) | 1892/181 | UNAVAILABLE: no first attempt was charged to this identity |

## Static analysis

```json
{
  "accepted_risk_runs": 0,
  "distinct_waivers": 0,
  "incomplete_coverage": 0,
  "outcomes": {
    "DISABLED": 2
  },
  "reason_codes": {
    "STATIC_ANALYSIS_NOT_CONFIGURED": 2
  },
  "runs_evaluated": 2,
  "stale_or_missing_authorization_at_commit": 0,
  "status": "MEASURED",
  "waiver_uses": 0
}
```

## Adjudicated outcomes

```json
{
  "adjudicated_successes": 0,
  "adjudication_coverage": {
    "denominator": 2,
    "numerator": 0,
    "status": "MEASURED",
    "value": 0.0
  },
  "false_success_rate": {
    "reason": "no SUCCESS run in the window is adjudicated (false success needs deterministic or human adjudication)",
    "status": "UNAVAILABLE"
  },
  "regression_escape_rate": {
    "reason": "no SUCCESS run in the window is adjudicated (false success needs deterministic or human adjudication)",
    "status": "UNAVAILABLE"
  },
  "sources": {},
  "status": "MEASURED",
  "store_status": "absent",
  "successful_runs": 2,
  "verdicts": {
    "confirmed_success": 0,
    "false_success": 0,
    "regression_escape": 0
  }
}
```

## Chaos invariants

```json
{
  "status": "NOT_PROVIDED"
}
```

## RunRecords

```json
{
  "status": "NOT_PROVIDED"
}
```

## Thresholds

```json
{
  "status": "NOT_CONFIGURED"
}
```
