"""Compare legacy and controller runs captured from the same snapshot/goal.

Inputs are local JSON summaries. This tool performs no lookup or egress and
refuses comparisons whose snapshot or goal fingerprints differ.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


METRICS = (
    "final_success", "first_attempt_success", "compile_success", "test_success",
    "llm_calls", "context_tokens", "files_planned", "files_touched",
    "undeclared_touches", "subtask_local_retries", "full_workflow_retries",
    "wall_clock_seconds", "regression_failures", "policy_interventions",
)


def load_summary(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in ("snapshot_fingerprint", "goal_fingerprint", *METRICS) if key not in payload]
    if missing:
        raise ValueError(f"{path} is missing required fields: {missing}")
    return payload


def compare(legacy: Dict[str, Any], controller: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("snapshot_fingerprint", "goal_fingerprint"):
        if legacy[key] != controller[key]:
            raise ValueError(f"refusing non-identical benchmark inputs: {key} differs")
    return {
        metric: {
            "legacy": legacy[metric],
            "controller": controller[metric],
            "delta": (
                controller[metric] - legacy[metric]
                if isinstance(legacy[metric], (int, float)) and isinstance(controller[metric], (int, float))
                else None
            ),
        }
        for metric in METRICS
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", required=True, type=Path)
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(load_summary(args.legacy), load_summary(args.controller))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
