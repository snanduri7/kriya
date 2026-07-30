import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.workflow import (
    WorkflowEngine,
    extract_expected_files,
    find_missing_expected_files,
    normalize_written_filepath,
)


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

def test_normalize_written_filepath_passes_through_relative_path():
    assert normalize_written_filepath("src/app.py", "/workspace") == "src/app.py"

def test_normalize_written_filepath_makes_absolute_path_relative():
    assert normalize_written_filepath("/workspace/app.py", "/workspace") == "app.py"

def test_normalize_written_filepath_rejects_path_escaping_workspace():
    # An absolute path outside the workspace root must not resolve to a "../.." path
    # that would write outside the sandbox - reject it outright.
    assert normalize_written_filepath("/elsewhere/secret.py", "/workspace") is None

def test_normalize_written_filepath_rejects_empty():
    assert normalize_written_filepath("", "/workspace") is None

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


@pytest.mark.asyncio
async def test_workflow_full_regression_check_tests_the_applied_change_not_stale_worktree(tmp_path):
    """The full regression check runs after the worktree sandbox has already been
    git-clean'd back to its pre-change HEAD state (once files are copied out to the
    real workspace). It must test the real workspace - which has the just-applied
    change - not the now-reverted worktree, or it silently reports a false pass based
    on stale, pre-change content.

    Reproduces this with a real git repo: the committed (pre-change) calc.py has a
    deliberately WRONG add() implementation that test_calc.py's existing, untouched
    assertion would fail against. The Developer Agent "fixes" calc.py in this run. If
    the regression check were still testing the reverted worktree (the bug), it would
    run test_calc.py against the wrong, pre-change calc.py and fail; testing the real
    workspace (the fix) runs it against the corrected calc.py and passes.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # deliberately wrong
    (tmp_path / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial (buggy) commit"], cwd=tmp_path, check=True)

    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Fix calc.py",                # Planner
        "Design: Fix add() in calc.py",        # Architect
        '[{"filepath": "calc.py", "content": "def add(a, b):\\n    return a + b\\n"}]',  # Developer
        "Review: Approved"                     # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)

    res = await we.run_generation_workflow(
        goal="Fix the add function in calc.py",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    with open(tmp_path / "calc.py") as f:
        assert "a + b" in f.read()


@pytest.mark.asyncio
async def test_workflow_passing_run_verification_marks_active_skill_verified(tmp_path):
    """A passing Runtime Verification Gate run is exactly the proof the skill-gap
    check needs - any skill that contributed to a run whose generated app actually
    ran and did what the goal asked should come out of that run marked verified,
    so future runs stop asking about it."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Widgets must be printed in uppercase.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_list_response = json.dumps([{"filepath": "app.py", "content": "print('[SUCCESS] WIDGET')\n"}])
    judge_response = json.dumps({
        "should_run": True,
        "run_command": [sys.executable, "app.py"],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected line."})

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
        goal="Run with python app.py; the widgetlib==2.0 skill applies here",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is True
    assert skill.verified_context == "widgetlib 2.0.0"
    assert skill.verified_at is not None


@pytest.mark.asyncio
async def test_workflow_skill_gap_refuses_url_fetch_under_local_only_egress(tmp_path):
    """A supplied URL must not be fetched when autonomy.egress_policy is local_only -
    that's a new outbound-network capability this feature adds, and it should get the
    same guarantee the rest of Kriya already gives. File/text answers are unaffected -
    only the URL branch is gated."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.egress_policy = "local_only"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    def skill_gap_cb(reason, names):
        return "https://example.com/widgetlib-docs"

    we = WorkflowEngine(kernel, llm)

    with patch("kriya.tools.web.fetch_url_text", new_callable=AsyncMock) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
        )
        mock_fetch.assert_not_called()

    assert res["quality_gates_passed"] is True
    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is False
    assert skill.rules == ["Existing rule."]


@pytest.mark.asyncio
async def test_workflow_skill_gap_decline_marks_acknowledged_and_proceeds(tmp_path):
    """Declining the skill-gap ask (callback returns None) must not fail generation -
    it proceeds with the skill as-is, same as today's baseline - and must remember the
    decline so future runs don't keep re-asking about the same skill."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    calls = []
    def skill_gap_cb(reason, names):
        calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something with the widgetlib skill",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
    )

    assert res["quality_gates_passed"] is True
    assert calls == [["widgetlib"]]

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    assert se.get_skill("widgetlib").verification_gap_acknowledged is True

@pytest.mark.asyncio
async def test_workflow_skill_gap_not_reasked_once_acknowledged(tmp_path):
    """A second run must not re-invoke the callback for a skill already acknowledged
    by a prior decline."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text(
        "name: widgetlib\ndescription: Test\ntags: [widgetlib]\nverification_gap_acknowledged: true\n"
    )
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    calls = []
    def skill_gap_cb(reason, names):
        calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something with the widgetlib skill",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
    )

    assert res["quality_gates_passed"] is True
    assert calls == []


@pytest.mark.asyncio
async def test_workflow_normalizes_absolute_filepath_from_developer_agent(tmp_path):
    """Reproduces a real observed failure: the Developer Agent sometimes returns an
    absolute path instead of a relative one. os.path.join(worktree_path, filepath)
    silently discards worktree_path when filepath is absolute, so the file would land
    directly in the real workspace (bypassing the sandbox) and later crash with
    shutil.SameFileError when the apply step tries to copy worktree -> workspace with
    source == destination. Needs a real git repo so a real worktree gets created
    (worktree_path != workspace_path) - the bug doesn't manifest in the fallback path
    where they're already the same directory.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("placeholder\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    absolute_filepath = str(tmp_path / "app.py")
    file_list_response = json.dumps([{"filepath": absolute_filepath, "content": "print(1)\n"}])

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        file_list_response,
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create a small script",
        workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert res["files"] == ["app.py"]
    assert (tmp_path / "app.py").exists()
    with open(tmp_path / "app.py") as f:
        assert f.read() == "print(1)\n"

