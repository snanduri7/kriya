#!/usr/bin/env python
"""Deterministic run_events fixture produced by Kriya's OWN serializer and read back through the KUP adapter.

Builds real RunEvent objects (kriya/workflow/run_events.py) for the three kinds the UI interprets - a context record
(context.known_target_package), a Developer prompt composition (developer.prompt_composition, details from
kriya.workflow.prompt_composition.prompt_composition) and model metrics (model.role_metrics, rows from
RoleRuntimeMetrics.to_dict) - plus a model.transition carrying a real ModelRequestProfile. They are written into a
temporary trace store with TraceLogger.log_run, acquired with kriya.kup.acquire and read with
kriya.kup.inspect.history_detail, so the committed JSON is what the adapter hands a host, not a hand-written guess.

Run from ui/ with a Python that has Kriya installed: `$KRIYA_PYTHON fixtures/serializer_events.py` (or
`npm run fixtures:serializer`); `--check` fails when the committed file differs from a fresh generation. Fixture only:
temporary directories, no protected store, no model.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.dont_write_bytecode = True

from kriya.core.role_metrics import RoleRuntimeMetrics  # noqa: E402 - after dont_write_bytecode, like the CLI
from kriya.core.trace import TraceLogger  # noqa: E402
from kriya.kup.acquire import acquire_snapshot  # noqa: E402
from kriya.kup.inspect import history_detail  # noqa: E402
from kriya.workflow.model_transition import ModelRequestProfile  # noqa: E402
from kriya.workflow.prompt_composition import prompt_composition  # noqa: E402
from kriya.workflow.run_events import EventAuthority, RunEvent  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serializer", "run_events.json")
RUN_ID = "run-serializer-events"
CODE_CONTEXT = (
    "### src/mod1/a.py (tier=member_exact, lines 10-14, revision sha256:ab)\n"
    "def run(self):\n    audit('run')\n    return 1\n"
)


def real_events() -> list:
    profile_from = ModelRequestProfile(
        model="qwen2.5-coder:7b", endpoint="http://localhost:11434/v1", runtime_digest="sha256:" + "11" * 32, runtime_exact=True,
        qualification="QUALIFIED", failed_cases=(), capability_source="qualification", native_tool_calls=False, json_mode=True,
        reliable_multiline_json=True, streaming=False, edit_protocol="kriya_sentinel_v1", reasoning=False, context_window=8192,
        allocation_window=6400, output_tokens=2048, context_policy="fixed")
    profile_to = ModelRequestProfile(
        model="qwen2.5-coder:14b", endpoint="http://localhost:11434/v1", runtime_digest="sha256:" + "22" * 32, runtime_exact=True,
        qualification="QUALIFIED", failed_cases=(), capability_source="qualification", native_tool_calls=False, json_mode=True,
        reliable_multiline_json=True, streaming=False, edit_protocol="kriya_sentinel_v1", reasoning=False, context_window=16384,
        allocation_window=13000, output_tokens=2048, context_policy="fixed")
    rows = [
        RoleRuntimeMetrics("developer", "qwen2.5-coder:7b", "sha256:" + "11" * 32, True, "settings:" + "33" * 8, calls=3, protocol_failures=1,
                           latency_seconds=12.3456, prompt_tokens=12570, completion_tokens=2436, attempts=2, attempts_passed=1).to_dict(),
        RoleRuntimeMetrics("planner", "qwen2.5-coder:14b", "sha256:" + "22" * 32, True, "settings:" + "44" * 8, calls=1,
                           latency_seconds=4.5, prompt_tokens=3100, completion_tokens=420).to_dict(),
    ]
    return [
        RunEvent(kind="context.known_target_package", attempt=1, source="attempt.run_attempt", authority=EventAuthority.ADVISORY,
                 message="Known-target context package built for the attempt-1 owner-contract replacement.",
                 details={"known_target_files": ["src/mod1/a.py", "src/mod1/b.py"], "unit_count": 2,
                          "tiers": [{"path": "src/mod1/a.py", "member_id": "Mod1.run", "tier": "member_exact"},
                                    {"path": "src/mod1/b.py", "member_id": None, "tier": "skeleton"}],
                          "omitted": [{"path": "src/big.py", "reason": "budget_exhausted"}], "package_hash": "sha256:" + "cd" * 32,
                          "member_hint_paths": ["src/mod1/a.py"]},
                 created_at=1791097200.25),
        RunEvent(kind="developer.prompt_composition", attempt=1, source="developer", authority=EventAuthority.ADVISORY,
                 message="Developer prompt composition (estimated per section; provider-reported total).",
                 details=prompt_composition(CODE_CONTEXT, "## Skill: audit-log\nAlways write an audit entry.\n", prompt_tokens_reported=4190,
                                            provider_metadata={"prompt_eval_ms": 1830, "load_ms": 12},
                                            prefix_reuse={"prefix_shared_chars": 1600, "prefix_break": "goal"}),
                 created_at=1791097201.5),
        RunEvent(kind="model.transition", attempt=2, source="attempt._run_developer_generation", authority=EventAuthority.ADVISORY,
                 message="Developer request profile for qwen2.5-coder:14b (was qwen2.5-coder:7b): allocation_window, context_window, model, runtime_digest changed",
                 details={"initial": False, "fallback": True, "from": profile_from.to_dict(), "to": profile_to.to_dict(),
                          "changes": ["allocation_window", "context_window", "model", "runtime_digest"]},
                 created_at=1791097230.0),
        RunEvent(kind="model.role_metrics", attempt=2, source="workflow", authority=EventAuthority.AUXILIARY,
                 message="per-role model metrics of this run (observations, not verification evidence)",
                 details={"rows": rows}, created_at=1791097261.75),
    ]


def through_the_adapter(events: list) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "state", "traces.db")
        os.makedirs(os.path.dirname(store))
        TraceLogger(store).log_run(run_id=RUN_ID, goal="serializer fixture", duration_sec=61.23, attempts=2, status="SUCCESS",
                                   files_modified=["src/mod1/a.py"], run_events=[e.to_dict() for e in events])
        snapshot = acquire_snapshot(store, os.path.join(tmp, "state", "kup-snapshots"))["snapshot"]
        return history_detail(snapshot, RUN_ID)["run_events"]


def main() -> int:
    events = real_events()
    section = through_the_adapter(events)
    assert section["data"] == json.loads(json.dumps([e.to_dict() for e in events])), "the adapter must return the serializer's events verbatim"
    doc = {
        "generated_by": "ui/fixtures/serializer_events.py: kriya/workflow/run_events.py::RunEvent.to_dict -> TraceLogger.log_run -> "
                        "kriya.kup.acquire.acquire_snapshot -> kriya.kup.inspect.history_detail",
        "serializer_keys": ["kind", "attempt", "source", "authority", "message", "failure_type", "operation", "details", "created_at"],
        "section": {"availability": section["availability"], "provenance": section["provenance"], "reason": section["reason"]},
        "run_events": section["data"],
    }
    text = json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        committed = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if committed != text:
            print(f"{OUT} differs from a fresh generation; run fixtures/serializer_events.py", file=sys.stderr)
            return 1
        print(f"{OUT} is current ({len(doc['run_events'])} events)")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {OUT} ({len(doc['run_events'])} events through the adapter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
