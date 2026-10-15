"""A scripted enforce run through the REAL WorkflowController and workflow: only the model transport and the toolchain gates are stubbed (the LR-R1-P4 reproducer harness,
generalised). Shared by the approval-gate and Greenfield runtime-verification reproducers. ``units`` maps a unit id to the (path, source) its scripted Developer writes."""
import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

from _protocol_responses import sentinel

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

PASSING = {"success": True, "output": "tests/test_shop.py .\n1 passed in 0.01s"}


def git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True, capture_output=True)


@dataclass
class Run:
    workspace: Any
    events: List[Any]
    result: Any
    approvals: List[Tuple[Optional[str], List[str], str]] = field(default_factory=list)   # EVERY approval-callback call: (unit, file paths, reason)
    judge_calls: List[Optional[str]] = field(default_factory=list)                        # units whose run-verification judge was consulted
    executed: List[Any] = field(default_factory=list)                                     # command sequences handed to the runtime executor
    grader_prompts: List[str] = field(default_factory=list)                               # user prompts sent to the run-verification grader (the captured-output evidence it judged)


def run_enforce(tmp_path, *, plan: EngineeringPlan, units: Dict[str, Tuple[str, str]], goal: str = "Change the page size", mode: str = "human-in-the-loop",
                answer: Any = True, run_verification: bool = False, judge_reply: Optional[Dict[str, Any]] = None,
                base_files: Optional[Dict[str, str]] = None, app_output: str = "ok\n", callback: Optional[Callable] = None) -> Run:
    from kriya.workflow.workflow_controller import WorkflowController

    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = mode
    cfg.autonomy.run_verification_enabled = run_verification
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    for name, text in (base_files or {"README.md": "base\n"}).items():
        (workspace / name).parent.mkdir(parents=True, exist_ok=True)
        (workspace / name).write_text(text)
    git(workspace, "init", "-q")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "base")

    active: Dict[str, Optional[str]] = {}
    run = Run(workspace=workspace, events=[], result=None)

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        path, source = units.get(active.get("id") or "", ("", ""))
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % path
        elif "Developer Agent" in first:
            content = sentinel(path, analysis="edit.", content=source)
        elif "Run Verification Judge" in first:
            run.judge_calls.append(active.get("id"))
            content = json.dumps(judge_reply or {"should_run": False, "execution_mode": "finite_command", "run_commands": None, "managed_service": None,
                                                 "command_source": "goal_explicit", "input_channel": "none", "success_criteria": "", "reasoning": "none"})
        elif "Run Verification Grader" in first:
            run.grader_prompts.append(user_prompt or "")
            seen = app_output.strip() in (user_prompt or "")       # PASS only on evidence actually present in the captured output it was shown
            content = json.dumps({"passed": seen, "verdict": "PASS" if seen else "FAIL", "reasoning": "scripted grader", "likely_files": []})
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5, "finish_reason": "stop", "provider_metadata": {}}

    real_workflow = WorkflowEngine.run_generation_workflow

    async def run_unit(self, *args, **kwargs):
        active["id"] = kwargs.get("current_subtask_id")
        return await real_workflow(self, *args, **kwargs)

    def default_callback(files, reason):
        run.approvals.append((active.get("id"), [f["filepath"] for f in files], reason))
        return answer

    def run_app(_self, commands, **_kwargs):
        run.executed.append(commands)
        return {"success": True, "returncode": 0, "output": app_output, "timed_out": False}

    real_record = GenerationState.record_event

    def record(state, event):
        run.events.append(event)
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value="structured plan")
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=run_unit), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: PASSING), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", new=run_app), \
         patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan):
        run.result = asyncio.run(WorkflowController(engine).execute(goal, str(workspace), migration_mode="enforce", approval_callback=callback or default_callback))
    return run


def decisions(events) -> List[Optional[str]]:
    return [e.details.get("outcome") for e in events if e.kind == "approval.decision"]


def gate_types(events) -> List[Any]:
    return [(e.details.get("type") if isinstance(e.details, dict) else None) for e in events if e.kind == "gate.outcome"]
