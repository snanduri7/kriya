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


# -- the enforce shop fixture (tests/test_enforce_verified_no_change.py's shape) ------------------

SHOP_TEST = ("from shop.controller import populate_pet_types\n\n\n"
             "def test_pet_types():\n    assert populate_pet_types() == ('cat', 'dog')\n")


def shop_files():
    import test_enforce_verified_no_change as shape

    return {"shop/__init__.py": "", shape.SERVICE: shape.UNCACHED_SERVICE, shape.CONTROLLER: shape.CONTROLLER_SRC,
            "tests/__init__.py": "", "tests/test_shop.py": SHOP_TEST}


def shop_responder(role, request):
    """s1 caches the service; s2 (the controller) is answered NO CHANGE."""
    import test_enforce_verified_no_change as shape
    from _chaos_harness import benign_roles
    from _protocol_responses import sentinel

    system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
    user = next((m["content"] for m in request.messages if m["role"] == "user"), "")
    if role == "file_list":
        only_service = shape.SERVICE in user and shape.CONTROLLER not in user
        return '{"files": ["%s"]}' % (shape.SERVICE if only_service else shape.CONTROLLER)
    if role == "developer":
        if f'path="{shape.SERVICE}"' in system or "cache find_pet_types" in user:
            return sentinel(shape.SERVICE, analysis="cache it.", content=shape.SERVICE_SRC)
        return sentinel(shape.CONTROLLER, analysis="already calls the cached service.", no_change=True)
    return benign_roles(role, request)


def shop_enforce(tmp_path, monkeypatch, *, plans=None, responder=shop_responder, cfg=None, tools=()):
    import test_enforce_verified_no_change as shape

    return enforce_run(tmp_path, monkeypatch, responder, shop_files(), shape.GOAL,
                       plans or [lambda: shape._plan(shape.TOOL_CRITERION)], cfg=cfg, tools=tools)


# -- an enforce TOOL subtask ----------------------------------------------------------------------

def echo_tool(*, fail: bool = False):
    from pydantic import BaseModel

    from kriya.tools.tool import BaseTool

    class _Args(BaseModel):
        pass

    class EchoTool(BaseTool):
        name = "t6_echo"
        description = "echo (T6 test tool)"
        arguments_schema = _Args

        async def _run(self, args):
            if fail:
                raise RuntimeError("echo failed")
            return {"output": "echoed"}
    return EchoTool()


def tool_plan():
    import test_enforce_verified_no_change as shape

    from kriya.workflow.plan_schema import EngineeringPlan

    plan = shape._plan(shape.TOOL_CRITERION).model_dump()
    plan["subtasks"].insert(0, {"id": "s0", "description": "run the echo tool", "execution_method": "tool",
                                "tool_name": "t6_echo", "provides": ["echoed"],
                                "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [],
                                "verification": []})
    return EngineeringPlan.model_validate(plan)


# -- an output-budget refusal before dispatch (PRD-016) ---------------------------------------

BIG_CALC = "".join(f"def add{i}(a, b):\n    return a + b + {i}\n\n\n" for i in range(120))
RENAME_GOAL = "Rename add0 to plus0 in calc.py"


def output_budget_config(*, patch_capable: bool):
    """A whole-file rewrite of BIG_CALC needs more output than the hard
    ceiling allows: the request is refused before dispatch. With a
    patch-capable edit profile the Developer falls back to an anchored
    patch in the same attempt (PRD-016)."""
    cfg = chaos_config()
    cfg.llm.max_tokens = 1024
    cfg.llm.context_policy.max_output_tokens = 1024
    if patch_capable:
        cfg.llm.capabilities.preferred_edit_protocol = "small_native_tools"
    return cfg


def rename_responder(role, request):
    from _chaos_harness import benign_roles
    from _protocol_responses import sentinel

    if role != "developer":
        return benign_roles(role, request)
    system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
    if "MODE: REPAIR." in system or "KRIYA:EDIT" in system:
        return sentinel("calc.py", analysis="FIX ANALYSIS: rename.", edits=(("def add0(a, b):", "def plus0(a, b):"),))
    return BIG_CALC.replace("def add0", "def plus0")
