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
    gate_outcomes: List[Any] = field(default_factory=list)                                # every gate outcome recorded on the generation state (what runs.gate_outcomes persists)
    grader_prompts: List[str] = field(default_factory=list)                               # user prompts sent to the run-verification grader (the captured-output evidence it judged)
    builds: List[Any] = field(default_factory=list)                                       # ("compile"|"tests", on_build() result) for every build/test gate execution, in order
    developer_calls: List[Optional[str]] = field(default_factory=list)                    # the unit of EVERY Developer request, in order (a replayed verifier makes none)
    developer_requests: List[Tuple[Optional[str], str, str]] = field(default_factory=list)  # (unit, the file the request was answered for, the system prompt's first line)


def run_enforce(tmp_path, *, plan: EngineeringPlan, units: Dict[str, Tuple[str, str]], goal: str = "Change the page size", mode: str = "human-in-the-loop",
                answer: Any = True, run_verification: bool = False, judge_reply: Optional[Dict[str, Any]] = None,
                base_files: Optional[Dict[str, str]] = None, app_output: str = "ok\n", callback: Optional[Callable] = None,
                record_steps: bool = False, on_execute: Optional[Callable[[Any], None]] = None, on_build: Optional[Callable[[], Any]] = None,
                run_id: Optional[str] = None, on_workspace: Optional[Callable[[Any], None]] = None, init_git: bool = True,
                on_tests: Optional[Callable[[Any], Dict[str, Any]]] = None) -> Run:
    """``units``: a source may be a callable (called once per Developer request of that unit). ``on_tests(validator)``: the tests gate's answer for that
    validator's tree (default: PASSING)."""
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
    if init_git:      # W3-3 U4: False leaves the folder as a plain NO_GIT directory (the production admission cases)
        git(workspace, "init", "-q")
        git(workspace, "add", "-A")
        git(workspace, "commit", "-qm", "base")
    if on_workspace is not None:  # the folder exists and is a committed repository; a caller may open a launch session on it
        on_workspace(workspace)

    active: Dict[str, Optional[str]] = {}
    run = Run(workspace=workspace, events=[], result=None)

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        scripted = units.get(active.get("id") or "", ("", ""))
        if isinstance(scripted, dict):      # a unit that owns several files: {path: source}; the request's own text names the one file it is for
            text = (system_prompt or "") + "\n" + (user_prompt or "")
            named = [p for p in scripted if p in (system_prompt or "")] or [p for p in scripted if p in (user_prompt or "")]
            path = named[0] if len(named) == 1 else min(scripted, key=lambda p: text.find(p) if p in text else len(text))
            source = scripted[path]
        else:
            path, source = scripted
        if "File List Planner" in first:
            content = json.dumps({"files": list(scripted) if isinstance(scripted, dict) else [path]})
        elif "Developer Agent" in first:
            run.developer_calls.append(active.get("id"))
            run.developer_requests.append((active.get("id"), path, first))
            text_or_pair = source() if callable(source) else source
            if isinstance(text_or_pair, tuple):     # (another path, content): the scripted model answers for a file it was not asked for
                path, text_or_pair = text_or_pair
            content = sentinel(path, analysis="edit.", content=text_or_pair)
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
        if on_execute is not None:
            on_execute(commands)
        result = {"success": True, "returncode": 0, "output": app_output, "timed_out": False}
        if record_steps:  # the executor's own per-command record (what ApplicationOutcome reads): argv, exit code, stdout, stderr
            result["steps"] = [{"command": list(c), "exit_code": 0, "stdout": app_output, "stderr": "", "timed_out": False} for c in commands]
        return result

    def compile_gate(*_a, **_k):
        run.builds.append(("compile", on_build() if on_build is not None else None))
        return {"success": True, "output": "ok"}

    def test_gate(validator, *_a, **_k):
        run.builds.append(("tests", on_build() if on_build is not None else None))
        return on_tests(validator) if on_tests is not None else PASSING

    real_record = GenerationState.record_event
    real_gate = GenerationState.record_gate_outcome

    def record_gate(state, outcome, *args, **kwargs):
        run.gate_outcomes.append(dict(outcome))
        return real_gate(state, outcome, *args, **kwargs)

    def record(state, event):
        run.events.append(event)
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value="structured plan")
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch.object(GenerationState, "record_gate_outcome", new=record_gate), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=run_unit), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=compile_gate), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=test_gate), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", new=run_app), \
         patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan):
        run.result = asyncio.run(WorkflowController(engine).execute(goal, str(workspace), migration_mode="enforce", approval_callback=callback or default_callback, **({"run_id": run_id} if run_id else {})))
    return run


def decisions(events) -> List[Optional[str]]:
    return [e.details.get("outcome") for e in events if e.kind == "approval.decision"]


def gate_types(events) -> List[Any]:
    return [(e.details.get("type") if isinstance(e.details, dict) else None) for e in events if e.kind == "gate.outcome"]
