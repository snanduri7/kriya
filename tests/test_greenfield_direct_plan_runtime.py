"""Phase 6 (live Greenfield failure, run 20261015T152407-5f87716a): the LIVE plan shape - ONE direct work unit writing pom.xml, App.java and
AppTest.java, no decomposition and no verification-only unit - reaches build, tests, the runtime-command review and execution.

Why this shape matters (TRACED): with the packaged default (`workflow_controller.enabled` False) `kriya generate` is a pass-through to
`WorkflowEngine.run_generation_workflow`, which runs the goal as a ONE-UNIT direct ExecutionPlan (plan_adapters.direct_execution_plan);
the Planner's markdown plan is only prompt context. tests/test_greenfield_runtime_end_to_end.py proves the chain for the four-unit
structured plan (enforce mode). This proves the same chain WITHOUT that decomposition, through the real engine, the real launch-approval
layer and the same decision functions a desktop uses. Stubbed: the model transport, the toolchain gates and process execution itself
(`run_app_sequence`: no Maven in a unit test); the argv handed to the executor is compared with the argv the person reviewed.
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from _protocol_responses import sentinel
from test_greenfield_runtime_end_to_end import Person, _callback, _fast  # noqa: F401 - _fast is an autouse fixture
from test_launch_approval import _session

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.kup.application_outcome import STATUS_EXECUTED, application_outcome
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

GOAL = ('Create a small Java 17 Maven application named hello-kriya that prints "Hello from Kriya" and includes a unit test.\n'
        'Make the application runnable.')
POM, MAIN_FILE, TEST_FILE = "pom.xml", "src/main/java/hellokriya/App.java", "src/test/java/hellokriya/AppTest.java"
FILES = {
    POM: "<project/>\n",
    MAIN_FILE: ('package hellokriya;\npublic class App {\n  public static String message() { return "Hello from Kriya"; }\n'
                '  public static void main(String[] a) { System.out.println(message()); }\n}\n'),
    TEST_FILE: ('package hellokriya;\nimport org.junit.Test;\nimport static org.junit.Assert.assertEquals;\npublic class AppTest {\n'
                '  @Test public void greets() { assertEquals("Hello from Kriya", App.message()); }\n}\n'),
}
RUN_COMMANDS = [["mvn", "exec:java", "-Dexec.mainClass=hellokriya.App"]]
JUDGE = {"should_run": True, "execution_mode": "finite_command", "run_commands": RUN_COMMANDS, "managed_service": None,
         "command_source": "goal_explicit", "input_channel": "none", "success_criteria": "The command output contains 'Hello from Kriya'",
         "reasoning": "the goal asks for a runnable application"}
MARKDOWN_PLAN = ("# Plan\n1. Create pom.xml (Java 17, JUnit).\n2. Create src/main/java/hellokriya/App.java printing the greeting.\n"
                 "3. Create src/test/java/hellokriya/AppTest.java.\n4. Verify: mvn compile, mvn test, mvn exec:java -Dexec.mainClass=hellokriya.App\n")
TESTS_PASSED = {"success": True, "output": "[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\n[INFO] BUILD SUCCESS"}


@dataclass
class DirectRun:
    workspace: Any
    result: Any = None
    executed: List[Any] = field(default_factory=list)
    gate_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    judge_calls: int = 0
    compile_calls: int = 0
    test_calls: int = 0
    log_at_execution: List[Any] = field(default_factory=list)
    applied_at_execution: List[Dict[str, bool]] = field(default_factory=list)
    person: Any = None
    canonical: Any = None


def run_direct(tmp_path, *, plan=None, files=FILES, judge=JUDGE, goal=GOAL, mode="human-in-the-loop"):
    """`kriya generate` with the packaged defaults: the engine called directly (no controller), one direct unit."""
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model, cfg.llm.context_window, cfg.llm.extra_body, cfg.llm_chain = "dev-model", 32768, {}, []
    cfg.autonomy.mode = mode
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills, cfg.paths.memory = str(tmp_path / "skills"), str(tmp_path / "memory")
    cfg.logging.file_enabled = cfg.logging.run_file_enabled = False
    assert cfg.workflow_controller.enabled is False and cfg.autonomy.run_verification_enabled is True   # the packaged defaults the live run used
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("base\n")
    import subprocess  # noqa: PLC0415
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True, capture_output=True)
    run = DirectRun(workspace=workspace)
    run.canonical, session = _session(workspace)
    run.person = Person(run.canonical, plan)
    run.person.start()

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": list(files)})
        elif "Developer Agent" in first:
            target = re.search(r"requested file path '([^']+)'", system_prompt).group(1)
            content = sentinel(target, analysis="create.", content=files[target])
        elif "Run Verification Judge" in first:
            run.judge_calls += 1
            content = json.dumps(judge)
        elif "Run Verification Grader" in first:
            seen = "Hello from Kriya" in (user_prompt or "")
            content = json.dumps({"passed": seen, "verdict": "PASS" if seen else "FAIL", "reasoning": "scripted grader", "likely_files": []})
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5, "finish_reason": "stop", "provider_metadata": {}}

    def run_app(_self, commands, **_kwargs):
        run.executed.append(commands)
        run.log_at_execution.append(run.person.log[:])
        run.applied_at_execution.append({path: (workspace / path).exists() for path in files})
        return {"success": True, "returncode": 0, "output": "Hello from Kriya\n", "timed_out": False,
                "steps": [{"command": list(c), "exit_code": 0, "stdout": "Hello from Kriya\n", "stderr": "", "timed_out": False} for c in commands]}

    def compile_gate(*_a, **_k):
        run.compile_calls += 1
        return {"success": True, "output": "[INFO] BUILD SUCCESS"}

    def test_gate(*_a, **_k):
        run.test_calls += 1
        return TESTS_PASSED

    real_gate = GenerationState.record_gate_outcome

    def record_gate(state, outcome, *args, **kwargs):
        run.gate_outcomes.append(dict(outcome))
        return real_gate(state, outcome, *args, **kwargs)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value=plan or MARKDOWN_PLAN)
    try:
        with patch.object(LLMClient, "_request_once", new=transport), \
             patch.object(GenerationState, "record_gate_outcome", new=record_gate), \
             patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=compile_gate), \
             patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=test_gate), \
             patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", new=lambda *a, **k: {"success": True, "output": "ok"}), \
             patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", new=run_app):
            run.result = asyncio.run(engine.run_generation_workflow(goal, str(workspace), approval_callback=_callback()))
    finally:
        run.person.stop()
        session.finish(0, None, None)
    return run


@pytest.fixture(scope="module")
def approved_run(tmp_path_factory):
    return run_direct(tmp_path_factory.mktemp("direct"))


def _succeeded(result):
    return bool(result["quality_gates_passed"] and result["overall_attempt_passed"] and result["candidate_gates_passed"])


def test_the_live_shape_is_one_direct_unit_with_three_files_all_accepted_and_applied(approved_run):
    assert _succeeded(approved_run.result), approved_run.result.get("last_failure")           # 1: all three files accepted
    assert sorted(approved_run.result["files"]) == sorted(FILES)
    assert {path for path in FILES if (approved_run.workspace / path).exists()} == set(FILES)  # ...and applied after the person approved them


def test_source_changes_are_one_multi_file_review_never_an_empty_one(approved_run):
    kinds = [p["kind"] for p in approved_run.person.seen]
    assert sorted(kinds) == ["change_approval", "runtime_command"], kinds                      # 2: exactly one source review and one runtime review
    change = next(p for p in approved_run.person.seen if p["kind"] == "change_approval")
    assert sorted(f["filepath"] for f in change["files"]) == sorted(FILES)


def test_build_and_tests_ran_with_a_nonzero_test_count(approved_run):
    assert approved_run.compile_calls == 1 and approved_run.test_calls >= 1                    # 3/4: the build and test gates ran (toolchain stubbed)
    evidence = {item["type"]: item for item in approved_run.result["deterministic_gate_evidence"]}
    assert evidence["compile"]["passed"] is True and evidence["targeted_test"]["status"] == "PASS_WITH_TESTS"


def test_the_runtime_obligation_survives_and_the_review_is_reachable(approved_run):
    assert approved_run.judge_calls == 1                                                      # 5: the runtime judge was consulted on the direct unit
    runtime = next(p for p in approved_run.person.seen if p["kind"] == "runtime_command")
    assert runtime["status"] == "PENDING" and runtime["unit_id"] is None                       # 6/7: a typed subject; a direct plan has no unit id to bind
    assert runtime["commands"] == [["mvn", "-e", "exec:java", "-Dexec.mainClass=hellokriya.App"]]   # the judge's argv as Kriya resolved it
    assert runtime["candidate_digest"] == runtime["command_fingerprint"]
    assert runtime["working_directory_kind"] == "ISOLATED_WORKTREE" and runtime["stdin_present"] is False
    assert "files" not in runtime                                                               # 8: the typed contract represents it; not shaped as files


def test_the_approved_commands_execute_once_and_only_after_the_decision(approved_run):
    runtime = next(p for p in approved_run.person.seen if p["kind"] == "runtime_command")
    assert approved_run.executed == [runtime["commands"]]                                     # 9: exactly the reviewed argv, once
    [log] = approved_run.log_at_execution
    assert log[-2:] == [("pending", "runtime_command"), ("deciding", "runtime_command")]       # nothing ran before the person decided


def test_CHARACTERISATION_the_runtime_review_precedes_the_source_review_on_the_direct_plan(approved_run):
    """MEASURED, pinned so an owner decision flips it deliberately: the legacy/direct pipeline runs run-verification inside the candidate
    gates, BEFORE the final change approval. So the person is asked to run commands for source they have not yet been shown, and at
    execution time nothing has been applied to their folder (the candidate runs in the isolated worktree). The structured (enforce)
    plan orders it the other way (tests/test_greenfield_runtime_end_to_end.py)."""
    assert [p["kind"] for p in approved_run.person.seen] == ["runtime_command", "change_approval"]
    [applied] = approved_run.applied_at_execution
    assert not any(applied.values())


def test_application_outcome_is_executed_with_the_printed_output(approved_run):
    outcome = application_outcome(approved_run.gate_outcomes)
    assert outcome["status"] == STATUS_EXECUTED and outcome["succeeded"] is True and outcome["exit_code"] == 0   # 10/11
    assert outcome["commands"] == ["mvn -e exec:java -Dexec.mainClass=hellokriya.App"]
    assert "Hello from Kriya" in outcome["stdout"]["text"] and outcome["stderr"]["text"] == ""
    run_verification = [g for g in approved_run.gate_outcomes if g.get("type") == "run_verification"]
    assert run_verification and run_verification[-1]["success"] is True                         # the grader judged the CAPTURED stdout


def test_CHARACTERISATION_declining_the_runtime_review_on_the_direct_plan_still_reports_success(tmp_path):
    """MEASURED product defect, pinned so the owner's decision flips it deliberately (STOP condition 'direct plans silently lose runtime
    verification', handover/DEMO-V2/LIVE-GREENFIELD-FAILURE-ROOT-CAUSE.md Phase 6). On the structured plan a declined required runtime check
    is a recorded `verification_infrastructure_failure` and the run is not a success (tests/test_greenfield_runtime_end_to_end.py). On the
    direct plan, declining only sets run_verification_declined: nothing executes, no run_verification outcome is recorded, every gate
    passes, and the change is still offered and applied - while the goal ('prints Hello from Kriya') required runtime behaviour."""
    original = Person.__init__

    def scripted(self, canonical, plan=None):
        original(self, canonical, {"runtime_command": ("reject", 0.0)})

    with patch.object(Person, "__init__", scripted):
        run = run_direct(tmp_path)

    assert run.executed == []                                                                    # nothing was executed
    assert [p["kind"] for p in run.person.seen] == ["runtime_command", "change_approval"]
    assert not any(g.get("type") == "run_verification" for g in run.gate_outcomes)               # no runtime verdict of any kind was recorded
    assert application_outcome(run.gate_outcomes) == {"status": "NOT_APPLICABLE", "reason": "no_runtime_execution_recorded"}
    assert _succeeded(run.result)                                                                # ...and the run still reports success (the defect)
    assert {path for path in FILES if (run.workspace / path).exists()} == set(FILES)


def test_CHARACTERISATION_the_direct_plan_never_closes_the_goal_requirements(approved_run):
    """MEASURED: even with the application executed and graded PASS, the direct plan's requirement ledger stays pending/NOT_YET_VERIFIED
    (requirement closure and `requirements_verified` belong to the enforce terminal gate). 'Full-goal verification true' is therefore
    not reachable on this plan shape today - an owner decision, not something this test can make true."""
    requirements = approved_run.result["requirements"]
    assert requirements["outcomes"] == {"REQ-1": "pending", "REQ-2": "pending"}
    assert {v["reason_code"] for v in requirements["verdicts"].values()} == {"NOT_YET_VERIFIED"}
