import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.workflow import WorkflowEngine, extract_expected_files, find_missing_expected_files


def test_extract_expected_files_from_design_tree():
    design = """
    src/main/java/com/example/
              |-- App.java
              |-- BrokerServer.java
          |-- ignite-config.xml

    Maven Dependencies (pom.xml)
    """
    expected = extract_expected_files(design)
    assert expected == {"App.java", "BrokerServer.java", "ignite-config.xml", "pom.xml"}

def test_extract_expected_files_empty_for_no_design():
    assert extract_expected_files("") == set()
    assert extract_expected_files("Just a plain description, no filenames here.") == set()

def test_find_missing_expected_files_matches_by_basename():
    expected = {"App.java", "BrokerServer.java", "pom.xml"}
    written = {"pom.xml", "src/main/java/com/example/App.java"}
    assert find_missing_expected_files(expected, written) == ["BrokerServer.java"]

def test_find_missing_expected_files_none_missing():
    expected = {"pom.xml"}
    written = {"pom.xml"}
    assert find_missing_expected_files(expected, written) == []

def test_find_missing_expected_files_excludes_unrequested_test_file():
    expected = {"App.java", "MessageServiceTest.java", "pom.xml"}
    written = {"App.java", "pom.xml"}
    goal = "Build a messaging app that sends and reads messages."
    assert find_missing_expected_files(expected, written, goal=goal) == []

def test_find_missing_expected_files_keeps_test_file_when_requested():
    expected = {"App.java", "MessageServiceTest.java", "pom.xml"}
    written = {"App.java", "pom.xml"}
    goal = "Build a messaging app with unit test coverage for the message service."
    assert find_missing_expected_files(expected, written, goal=goal) == ["MessageServiceTest.java"]

def test_find_missing_expected_files_excludes_readme_unless_requested():
    expected = {"App.java", "README.md"}
    written = {"App.java"}
    assert find_missing_expected_files(expected, written, goal="Build an app") == []
    assert find_missing_expected_files(expected, written, goal="Build an app with documentation") == ["README.md"]

@pytest.mark.asyncio
async def test_workflow_successful_run(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    res = await we.run_generation_workflow(
        goal="Create math library",
        workspace_path=str(tmp_path)
    )
    
    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    assert os.path.exists(os.path.join(tmp_path, "math.py"))
    assert res["review"] == "Review: Approved"

@pytest.mark.asyncio
async def test_workflow_syntax_error_auto_debugging_loop(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b)\\n    return a+b"}]',
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    res = await we.run_generation_workflow(
        goal="Create math library with auto-debugging",
        workspace_path=str(tmp_path)
    )
    
    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    
    # Check that file was rewritten with correct code
    with open(os.path.join(tmp_path, "math.py"), "r") as f:
        content = f.read()
    assert "def add(a,b):" in content


@pytest.mark.asyncio
async def test_workflow_incomplete_generation_triggers_retry(tmp_path):
    """A Developer Agent that only writes a subset of the design's planned files
    (e.g. only pom.xml instead of pom.xml + 6 source files) must not be accepted as
    a passing run just because what little it wrote happens to compile."""
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py and helper.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}, '
        '{"filepath": "helper.py", "content": "def helper():\\n    pass"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Create math library with a helper module",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    assert "helper.py" in res["files"]
    assert os.path.exists(os.path.join(tmp_path, "helper.py"))


@pytest.mark.asyncio
async def test_workflow_fallback_chain(tmp_path):
    from kriya.config import FallbackModelConfig
    cfg = AppConfig()
    cfg.llm_chain = [
        FallbackModelConfig(model="fallback-1"),
        FallbackModelConfig(model="fallback-2")
    ]
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    model_overrides = []
    
    async def mock_complete(*args, **kwargs):
        model_overrides.append(kwargs.get("model_override"))
        if len(model_overrides) == 1:
            return "Step 1: Write code"
        elif len(model_overrides) == 2:
            return "Design: Write math.py"
        elif len(model_overrides) == 3:
            return '[{"filepath": "math.py", "content": "def add(a,b)\\n    return a+b"}]'
        elif len(model_overrides) == 4:
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        elif len(model_overrides) == 5:
            return "Avoid missing colon in function definition."
        else:
            return "Review: Approved"
            
    llm.complete = mock_complete
    
    we = WorkflowEngine(kernel, llm)
    cfg.paths.skills = str(tmp_path / "skills")
    
    res = await we.run_generation_workflow(
        goal="Create math library with fallback chain",
        workspace_path=str(tmp_path)
    )
    
    assert res["quality_gates_passed"] is True
    assert "fallback-1" in model_overrides
    
    repo_slug = os.path.basename(tmp_path).lower().strip(".")
    if not repo_slug:
         repo_slug = "root"
    rules_file = os.path.join(tmp_path, "skills", f"auto-{repo_slug}", "staged_rules.txt")
    assert os.path.exists(rules_file)
    with open(rules_file, "r", encoding="utf-8") as f:
        rules_content = f.read()
    assert "Avoid missing colon in function definition." in rules_content


@pytest.mark.asyncio
async def test_workflow_cumulative_sandbox_sync(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    # We want attempt 1 to generate file1.py, which compiles fine, but we'll mock compile check or test to fail once.
    # Attempt 2 will generate file2.py.
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write files",
        # Attempt 1: Generates file1.py
        '[{"filepath": "file1.py", "content": "def func1():\\n    return 1"}]',
        # Attempt 2: Generates only file2.py
        '[{"filepath": "file2.py", "content": "def func2():\\n    return 2"}]',
        "Avoid test failure.",
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    # Mock validator compile and test checks to trigger a retry
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests") as mock_test:
         
         # Attempt 1 compile succeeds, but test fails
         # Attempt 2 compile succeeds, test succeeds
         mock_compile.return_value = {"success": True, "output": ""}
         mock_test.side_effect = [
             {"success": False, "output": "Test failed"}, # Attempt 1
             {"success": True, "output": ""} # Attempt 2
         ]
         
         res = await we.run_generation_workflow(
             goal="Create library with multiple files",
             workspace_path=str(tmp_path)
         )
         
         assert res["quality_gates_passed"] is True
         # Both files should be in the returned file list
         assert "file1.py" in res["files"]
         assert "file2.py" in res["files"]
         
         # Both files should actually exist in the workspace
         assert os.path.exists(os.path.join(tmp_path, "file1.py"))
         assert os.path.exists(os.path.join(tmp_path, "file2.py"))


@pytest.mark.asyncio
async def test_workflow_run_verification_goal_explicit_passes_without_confirmation(tmp_path):
    """A goal-text-explicit run command is pre-authorized by the user and must proceed
    without needing the human-in-the-loop confirmation gate, even though that's the
    default autonomy mode."""
    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_list_response = json.dumps([{"filepath": "app.py", "content": "print('[SUCCESS] it worked')\n"}])
    judge_response = json.dumps({
        "should_run": True,
        "run_command": [sys.executable, "app.py"],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected [SUCCESS] line."})

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        file_list_response,       # Developer
        judge_response,           # RunVerifier.judge
        grade_response,           # RunVerifier.grade
        "Review: Approved"        # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Run with python app.py; it should print [SUCCESS]",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert "app.py" in res["files"]


@pytest.mark.asyncio
async def test_workflow_run_verification_declined_still_passes_on_compile_alone(tmp_path):
    """Declining the judgment-triggered confirmation must only skip the run-verification
    step itself, not fail the whole generation - compile passing is still a valid pass.
    The same approval_callback also gates the (separate) pre-apply diff approval, which
    must still be asked and approved independently."""
    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_list_response = json.dumps([{"filepath": "app.py", "content": "print('hello')\n"}])
    judge_response = json.dumps({
        "should_run": True,
        "run_command": [sys.executable, "app.py"],
        "command_source": "inferred",
        "success_criteria": "Output contains hello"
    })

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        file_list_response,       # Developer
        judge_response,           # RunVerifier.judge (grade must NOT be called after this)
        "Review: Approved"        # Reviewer
    ])

    def approval_cb(files, reason):
        # Run-verification's own confirmation is called with an empty diff list;
        # the pre-apply diff-approval gate is called with the real diffs. Decline
        # only the former to isolate what's being tested.
        return bool(files)

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Create a small script",
        workspace_path=str(tmp_path),
        approval_callback=approval_cb,
    )

    assert res["quality_gates_passed"] is True
    assert "app.py" in res["files"]
    # llm.complete's side_effect list has exactly 5 entries - if grade() had been
    # called despite the decline, AsyncMock would have raised StopAsyncIteration.
    assert llm.complete.await_count == 5

