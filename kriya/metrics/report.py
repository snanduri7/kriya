"""The metrics report (PRD-033): deterministic JSON plus Markdown.

``content`` is derive_metrics' output plus the threshold evaluation. It
carries no generation time: the window is the evidence's own timestamps,
so the same persisted evidence always gives the same ``content_digest``.
``generated`` (when, from where) is kept apart and never digested.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from kriya.metrics.adjudication import AdjudicationStore
from kriya.metrics.derive import derive_metrics
from kriya.metrics.evidence import TraceRun, WorkspaceFacts, canonical_digest
from kriya.metrics.thresholds import Threshold, evaluate_thresholds


def build_report(
    runs: Sequence[TraceRun], *, adjudications: Optional[AdjudicationStore] = None,
    run_records: Optional[WorkspaceFacts] = None, chaos: Optional[Mapping[str, Any]] = None,
    thresholds: Optional[Tuple[Threshold, ...]] = None, thresholds_digest: Optional[str] = None,
    generated: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    content = derive_metrics(runs, adjudications=adjudications, run_records=run_records, chaos=chaos)
    content["thresholds"] = evaluate_thresholds(content, thresholds, thresholds_digest)
    return {
        "content": content, "content_digest": canonical_digest(content),
        "generated": {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **(generated or {})},
    }


def _value(metric: Any) -> str:
    if not isinstance(metric, Mapping):
        return str(metric)
    status = metric.get("status")
    if status == "MEASURED" and "value" in metric:
        suffix = f" ({metric['numerator']}/{metric['denominator']})" if "denominator" in metric else ""
        return f"{metric['value']}{suffix}"
    if status == "MEASURED" and "mean" in metric:
        return f"n={metric['count']} mean={metric['mean']} median={metric['median']} max={metric['max']}"
    if status in ("UNAVAILABLE", "NOT_PROVIDED", "NOT_CONFIGURED"):
        return f"{status}" + (f": {metric['reason']}" if metric.get("reason") else "")
    return json.dumps({k: v for k, v in metric.items() if k != "status"}, sort_keys=True)


_OUTCOME_ROWS = (
    ("runs", "Runs"), ("final_verified_success", "Final verified success"),
    ("first_pass_compile", "First-pass compile"), ("no_progress_termination", "No-progress termination"),
    ("context_insufficiency", "Context insufficiency (budget refusals)"),
    ("context_request_fit_reductions", "Prompt-fit reductions"), ("authority_rejections", "Authority/D1 rejections"),
    ("containment_failures", "Containment failures"), ("fallback_transitions", "Fallback transitions"),
    ("fallback_selection_skips", "Fallback skips (incompatible)"),
    ("human_escalations", "Human escalations"), ("pre_approval_review_unattached", "Unattached pre-approval reviews"),
    ("workspace_commit_failures", "Commit failures (reason)"), ("llm_calls", "LLM calls"),
    ("wall_time_seconds", "Wall time (s)"), ("verification_share", "Verification share (compile + runtime verification timings; tests untimed)"),
)


def _outcome_table(block: Mapping[str, Any]) -> List[str]:
    lines = ["| Metric | Value |", "|---|---|"]
    for key, label in _OUTCOME_ROWS:
        lines.append(f"| {label} | {_value(block.get(key))} |")
    retries = block.get("retries") or {}
    lines.append(f"| Retries (full-set / targeted) | {_value(retries.get('full_set'))} / {_value(retries.get('targeted'))} |")
    lines.append(f"| Failure categories | {json.dumps(block.get('failure_categories', {}), sort_keys=True)} |")
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    content = report["content"]
    window = content["window"]
    lines = [
        "# Kriya production metrics (PRD-033)", "",
        f"- Content digest: `{report['content_digest']}`",
        f"- Evidence window: {window['trace_rows']} trace rows, {window['first']} .. {window['last']}",
        f"- Thresholds: **{content['thresholds']['status']}**", "",
        "Derived only from persisted evidence. False success comes only from deterministic or human "
        "adjudication, never from a model. UNAVAILABLE means the evidence does not exist (never zero).", "",
        "## Generation runs", "", *_outcome_table(content["outcomes"]["generation"]), "",
    ]
    enforce = content["outcomes"]["enforce"]
    lines += ["## Enforce terminal outcomes", ""]
    lines += _outcome_table(enforce) if enforce.get("status") != "UNAVAILABLE" else [_value(enforce)]
    lines += ["", "## By task class", "", "| Task class | Runs | Final verified success | First-pass compile |",
              "|---|---|---|---|"]
    for name, block in content["by_task_class"].items():
        lines.append(f"| {name} | {block['runs']} | {_value(block['final_verified_success'])} | "
                     f"{_value(block['first_pass_compile'])} |")
    lines += ["", "## By Developer runtime identity (runtime digest | inference settings digest)", "",
              "| Identity | Runs | Final verified success | First-pass compile |", "|---|---|---|---|"]
    for name, block in content["by_developer_identity"].items():
        lines.append(f"| `{name}` | {block['runs']} | {_value(block['final_verified_success'])} | "
                     f"{_value(block['first_pass_compile'])} |")
    lines += ["", "## Model protocol (exact runtime, inference settings, role, task class)", "",
              "| Runtime | Settings | Role | Task class | Calls | Protocol/tool-call failures | Structured-output failures "
              "| Tokens in/out | First-pass |", "|---|---|---|---|---|---|---|---|---|"]
    for row in content["model_protocol"]:
        lines.append(
            f"| `{row['runtime_digest'][:12]}` | `{row['inference_settings_digest'][:12]}` | {row['role']} | "
            f"{row['task_class']} | {row['calls']} | {_value(row['protocol_failure_rate'])} | "
            f"{_value(row['structured_output_failure_rate'])} | {row['prompt_tokens']}/{row['completion_tokens']} | "
            f"{_value(row['first_pass_success'])} |")
    for section, title in (("static_analysis", "Static analysis"), ("adjudication", "Adjudicated outcomes"),
                           ("chaos", "Chaos invariants"), ("run_records", "RunRecords"), ("thresholds", "Thresholds")):
        lines += ["", f"## {title}", "", "```json", json.dumps(content[section], indent=2, sort_keys=True), "```"]
    return "\n".join(lines) + "\n"


def write_report(directory: str, report: Mapping[str, Any]) -> Tuple[str, str]:
    os.makedirs(directory, exist_ok=True)
    json_path = os.path.join(directory, "metrics-report.json")
    md_path = os.path.join(directory, "metrics-report.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    return json_path, md_path
