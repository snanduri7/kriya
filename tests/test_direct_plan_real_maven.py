"""Demo V2 (owner decision 2026-10-15): the bounded, deterministic REAL-toolchain check of the direct plan. NO MODEL: the transport is scripted exactly as in
tests/test_greenfield_direct_plan_runtime.py, but nothing else is stubbed - POM validation, the build, the tests and the runtime command run on the real JDK and the real Maven
against a curated tiny Java 17 candidate (one pom, one class, one JUnit 4 test) in Kriya's isolated worktree, offline (Maven's local repository must already hold the plugins and
junit 4.13.2; otherwise the test skips instead of reaching for the network).

Proved with the real tools, in order: source review pending/decided -> real `mvn validate` -> real build -> real tests with a NONZERO count -> runtime-command review -> the exact reviewed
command executed -> real stdout "Hello from Kriya" -> ApplicationOutcome EXECUTED; the same bytes were reviewed, built, tested, executed and applied. A rejected runtime review runs nothing
and applies nothing. The ledger stays pending (owner decision on REQ-LEDGER-RUNTIME-CLOSURE-001).

Tier: `real_toolchain`, excluded by default (needs a JDK 17 and Maven on PATH); run with `pytest -m real_toolchain tests/test_direct_plan_real_maven.py`. Set KRIYA_REAL_MAVEN_EVIDENCE to a file path
to write the measured toolchain versions and per-step facts as JSON.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _protocol_responses import sentinel
from test_greenfield_direct_plan_runtime import GOAL, MARKDOWN_PLAN, _succeeded
from test_greenfield_runtime_end_to_end import Person, _callback, _fast  # noqa: F401 - _fast is an autouse fixture
from test_launch_approval import _session

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.kup.application_outcome import STATUS_EXECUTED, application_outcome
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

pytestmark = pytest.mark.real_toolchain

POM = "pom.xml"
MAIN_FILE = "src/main/java/hellokriya/App.java"
TEST_FILE = "src/test/java/hellokriya/AppTest.java"
REAL_FILES = {
    POM: """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>dev.kriya</groupId><artifactId>hello-kriya</artifactId><version>1.0</version>
<properties><maven.compiler.source>17</maven.compiler.source><maven.compiler.target>17</maven.compiler.target><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
<dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency></dependencies>
</project>
""",
    MAIN_FILE: ('package hellokriya;\npublic class App {\n  public static String message() { return "Hello from Kriya"; }\n'
                '  public static void main(String[] a) { System.out.println(message()); }\n}\n'),
    TEST_FILE: ('package hellokriya;\nimport org.junit.Test;\nimport static org.junit.Assert.assertEquals;\npublic class AppTest {\n'
                '  @Test public void greets() { assertEquals("Hello from Kriya", App.message()); }\n}\n'),
}
JUDGE = {"should_run": True, "execution_mode": "finite_command", "run_commands": [["mvn", "exec:java", "-Dexec.mainClass=hellokriya.App"]], "managed_service": None,
         "command_source": "goal_explicit", "input_channel": "none", "success_criteria": "The command output contains 'Hello from Kriya'",
         "reasoning": "the goal asks for a runnable application"}


def _version(command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout + done.stderr).strip()


def _offline_toolchain_ready(tmp_path) -> bool:
    """A JDK 17+, Maven, and the plugins/junit this candidate needs in the local repository: proven by building the fixture once offline in a scratch copy."""
    if shutil.which("java") is None or shutil.which("mvn") is None:
        return False
    scratch = tmp_path / "probe"
    for relpath, content in REAL_FILES.items():
        (scratch / relpath).parent.mkdir(parents=True, exist_ok=True)
        (scratch / relpath).write_text(content)
    done = subprocess.run(["mvn", "-o", "-q", "test"], cwd=scratch, capture_output=True, text=True, timeout=600, check=False)
    if done.returncode != 0:
        return False
    shutil.rmtree(scratch)
    return True


def _run(tmp_path, *, review_plan=None):
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model, cfg.llm.context_window, cfg.llm.extra_body, cfg.llm_chain = "dev-model", 32768, {}, []
    cfg.autonomy.mode = "human-in-the-loop"
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills, cfg.paths.memory = str(tmp_path / "skills"), str(tmp_path / "memory")
    cfg.logging.file_enabled = cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("base\n")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True, capture_output=True)
    canonical, session = _session(workspace)
    person = Person(canonical, review_plan)
    person.start()
    facts = {"steps": [], "gate_outcomes": []}

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": list(REAL_FILES)})
        elif "Developer Agent" in first:
            target = re.search(r"requested file path '([^']+)'", system_prompt).group(1)
            content = sentinel(target, analysis="create.", content=REAL_FILES[target])
        elif "Run Verification Judge" in first:
            content = json.dumps(JUDGE)
        elif "Run Verification Grader" in first:
            seen = "Hello from Kriya" in (user_prompt or "")
            content = json.dumps({"passed": seen, "verdict": "PASS" if seen else "FAIL", "reasoning": "scripted grader", "likely_files": []})
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5, "finish_reason": "stop", "provider_metadata": {}}

    def spy(step, real):
        def wrapper(self, *args, **kwargs):
            before = {path: (Path(self.workspace_path) / path).read_text() for path in REAL_FILES if (Path(self.workspace_path) / path).exists()}
            entry = {"step": step, "log": person.log[:], "bytes_are_the_candidate": before == REAL_FILES, "success": None}
            facts["steps"].append(entry)        # a spied method may call another spied one: each entry is updated through its own reference
            result = real(self, *args, **kwargs)
            entry["success"] = bool(result.get("success")) if isinstance(result, dict) else None
            return result
        return wrapper

    real_gate = GenerationState.record_gate_outcome

    def record_gate(state, outcome, *args, **kwargs):
        facts["gate_outcomes"].append(dict(outcome))
        return real_gate(state, outcome, *args, **kwargs)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value=MARKDOWN_PLAN)
    result = None
    try:
        with patch.object(LLMClient, "_request_once", new=transport), \
                patch.object(GenerationState, "record_gate_outcome", new=record_gate), \
                patch.object(PolymorphicValidator, "run_pom_validate", new=spy("pom_validate", PolymorphicValidator.run_pom_validate)), \
                patch.object(PolymorphicValidator, "run_compile_check", new=spy("compile", PolymorphicValidator.run_compile_check)), \
                patch.object(PolymorphicValidator, "run_tests", new=spy("tests", PolymorphicValidator.run_tests)), \
                patch.object(PolymorphicValidator, "run_app_sequence", new=spy("run_application", PolymorphicValidator.run_app_sequence)):
            result = asyncio.run(engine.run_generation_workflow(GOAL, str(workspace), approval_callback=_callback()))
    finally:
        person.stop()
        session.finish(0, None, None)
    return workspace, person, result, facts


@pytest.fixture(scope="module")
def toolchain(tmp_path_factory):
    probe = tmp_path_factory.mktemp("probe")
    if not _offline_toolchain_ready(probe):
        pytest.skip("a JDK and Maven with the plugins and junit 4.13.2 in the local repository are required (the check never reaches for the network)")
    return {"java": _version(["java", "-version"]), "maven": _version(["mvn", "-version"])}


def test_real_jdk_and_maven_build_test_and_run_the_reviewed_candidate_in_order(tmp_path, toolchain):
    workspace, person, result, facts = _run(tmp_path)
    steps = {s["step"]: s for s in facts["steps"]}
    source_decided = [("pending", "change_approval"), ("deciding", "change_approval")]

    assert [p["kind"] for p in person.seen] == ["change_approval", "runtime_command"]                      # source first, then the runtime review
    for name in ("pom_validate", "compile", "tests"):                                                       # every executing gate ran, for real, after the source decision
        assert steps[name]["log"][:2] == source_decided and steps[name]["success"] is True, (name, steps[name])
    assert steps["run_application"]["log"] == source_decided + [("pending", "runtime_command"), ("deciding", "runtime_command")]
    assert all(s["bytes_are_the_candidate"] for s in facts["steps"])                                       # reviewed = validated = built = tested = executed bytes
    assert {path: (workspace / path).read_text() for path in REAL_FILES} == REAL_FILES                    # = the applied bytes
    assert _succeeded(result)

    tests = next(g for g in facts["gate_outcomes"] if g.get("type") in ("test", "targeted_test") and g.get("success") is True)
    count = re.search(r"Tests run: (\d+)", str(tests.get("output")))
    assert count and int(count.group(1)) >= 1                                                               # a real, nonzero test count
    outcome = application_outcome(facts["gate_outcomes"])
    assert outcome["status"] == STATUS_EXECUTED and outcome["succeeded"] is True and outcome["exit_code"] == 0
    assert "Hello from Kriya" in outcome["stdout"]["text"]                                                 # the real process printed it
    assert result["requirements"]["outcomes"] == {"REQ-1": "pending", "REQ-2": "pending"}                  # an honest ledger: the run succeeded, the request is not 'verified'

    evidence_path = os.environ.get("KRIYA_REAL_MAVEN_EVIDENCE")
    if evidence_path:
        Path(evidence_path).write_text(json.dumps({
            "java": toolchain["java"], "maven": toolchain["maven"], "order": [(s["step"], s["log"][-2:], s["success"]) for s in facts["steps"]],
            "tests_run": int(count.group(1)), "command": outcome["commands"], "stdout": outcome["stdout"]["text"], "exit_code": outcome["exit_code"],
            "applied": sorted(p for p in REAL_FILES if (workspace / p).exists()),
        }, indent=2))


def test_real_toolchain_rejecting_the_runtime_review_runs_nothing_and_applies_nothing(tmp_path, toolchain):
    workspace, person, result, facts = _run(tmp_path, review_plan={"runtime_command": ("reject", 0.0)})
    steps = [s["step"] for s in facts["steps"]]
    assert [p["kind"] for p in person.seen] == ["change_approval", "runtime_command"]
    assert steps == ["pom_validate", "compile", "tests"]                                                    # the real build and tests ran; the application never did
    assert not _succeeded(result)
    assert not any((workspace / path).exists() for path in REAL_FILES)
    assert application_outcome(facts["gate_outcomes"])["status"] != STATUS_EXECUTED


def test_real_toolchain_rejecting_the_source_runs_no_real_tool_at_all(tmp_path, toolchain):
    workspace, person, result, facts = _run(tmp_path, review_plan={"change_approval": ("reject", 0.0)})
    assert [p["kind"] for p in person.seen] == ["change_approval"]
    assert facts["steps"] == []                                                                             # no mvn validate, no build, no test, no run
    assert not _succeeded(result) and not any((workspace / path).exists() for path in REAL_FILES)
