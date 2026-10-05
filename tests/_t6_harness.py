"""LR-R1-M1 T6 harness: deterministic runs through the real pipeline with
only the model runtime scripted (tests/_chaos_harness.py), read back through
the evidence reader and ``explain`` - never from logs.

``direct_run`` drives ``WorkflowEngine.run_generation_workflow`` (direct
goal). ``enforce_run`` drives the real ``WorkflowController`` enforce loop:
the Planner is really called through LLMClient (so its calls are recorded),
and only its structured-output parse is stubbed to the scripted plan(s);
gates run for real (pytest over the workspace's own tests).
"""
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from unittest.mock import patch as mock_patch

from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_config, chaos_engine, git_workspace, run_direct

from kriya.core.attempt_evidence import reader
from kriya.core.attempt_evidence.explain import explain_run
from kriya.core.state_paths import ENV_STATE_DIR


@dataclass
class Observed:
    result: Any
    explained: Dict[str, Any]
    records: List[Dict[str, Any]]
    runtime: ChaosRuntime
    workspace: Path
    run: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def attempts(self) -> List[Dict[str, Any]]:
        return self.explained["attempts"]

    def attempt(self, number: int, unit: Optional[str] = None) -> Dict[str, Any]:
        found = [a for a in self.attempts() if a["attempt"] == number and (unit is None or a["unit_id"] == unit)]
        assert len(found) == 1, (number, unit, [(a["unit_id"], a["attempt"]) for a in self.attempts()])
        return found[0]["answers"]

    def of(self, kind: str, **identity: Any) -> List[Dict[str, Any]]:
        return [r for r in self.records if r["kind"] == kind
                and all(r.get(key) == value for key, value in identity.items())]


def _read(state: Path) -> tuple:
    runs = reader.list_runs(str(state))
    assert len(runs) == 1, runs
    run = reader.open_run(str(state), runs[0])
    assert run.verify().status == reader.VERIFIED
    return explain_run(str(state), runs[0]), list(run.records()), run


def direct_run(tmp_path: Path, monkeypatch, responder: Callable, files: Mapping[str, str], *,
               goal: str = "add sub to calc.py", cfg=None, runtime_class=ChaosRuntime) -> Observed:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, files)
    runtime = runtime_class(responder)
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg or chaos_config()), goal, workspace)
    explained, records, run = _read(state)
    return Observed(result, explained, records, runtime, workspace, run)


def enforce_run(tmp_path: Path, monkeypatch, responder: Callable, files: Mapping[str, str], goal: str,
                plans: Sequence[Callable[[], Any]], *, cfg=None, tools: Mapping[str, Any] = ()) -> Observed:
    from kriya.workflow.workflow_controller import WorkflowController

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, files)
    cfg = cfg or chaos_config()
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    runtime = ChaosRuntime(responder)
    with RuntimeRegistration(runtime):
        engine = chaos_engine(cfg)
        for name, tool in dict(tools).items():
            engine.kernel.registry.register("tool", name, tool)
        with mock_patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
                        return_value=(object(), None)), \
             mock_patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
                        side_effect=[build() for build in plans]):
            result = asyncio.run(WorkflowController(engine).execute(goal, str(workspace), migration_mode="enforce"))
    explained, records, run = _read(state)
    return Observed(result, explained, records, runtime, workspace, run)


def assert_answered(answer: Mapping[str, Any]) -> None:
    """Every T6 cell is RECORDED with content, or a typed NOT_APPLICABLE /
    NOT_RECORDED with a reason - never blank."""
    status = answer.get("status")
    assert status in ("RECORDED", "NOT_APPLICABLE", "NOT_RECORDED"), answer
    if status == "RECORDED":
        assert answer.get("items") or any(answer.get(k) for k in answer if k not in ("status", "items")), answer
    else:
        assert isinstance(answer.get("reason"), str) and answer["reason"].strip(), answer


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
