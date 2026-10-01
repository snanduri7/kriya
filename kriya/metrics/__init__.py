"""Production telemetry and false-success metrics (PRD-033).

Metrics are derived, never a second source of truth: persisted run evidence
(``traces.db`` rows, RunRecords) plus operator adjudications go through one
deterministic deriver into a content-digested report. Nothing here changes
what a run decides, and ``kriya/workflow`` never imports this package.

- ``evidence``: read-only, content-free projections of the persisted stores
  (proprietary fields - goal, prompts, gate output, file content - are never
  read into a projection).
- ``adjudication``: the trusted, operator-written verdict store (false
  success, regression escape, confirmed success).
- ``derive``: the metrics.
- ``thresholds``: release-threshold evaluation mechanics (no values ship).
- ``report``: the reproducible JSON/Markdown report.
"""
