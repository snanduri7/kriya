import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest

import hashlib

from kriya.config import AppConfig, LLMConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.checkpoint import (
    checkpoint_path,
    compute_config_fingerprint,
    compute_workspace_fingerprint,
    find_latest_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from kriya.workflow.workflow import (
    WorkflowEngine,
    IncompleteGenerationError,
    _augment_error_with_live_lookup,
    _build_missing_files_retry_prompt,
    _build_targeted_retry_prompt,
    _is_near_duplicate_rule,
    _resolve_run_command,
    extract_error_search_terms,
    extract_expected_files,
    extract_implicated_files,
    find_missing_expected_files,
    normalize_written_filepath,
)


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("placeholder\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)


def _seed_checkpoint(tmp_path, cfg, goal, run_id, stage, **extra):
    save_checkpoint(str(tmp_path), run_id, {
        "stage": stage,
        "workspace_fingerprint": compute_workspace_fingerprint(str(tmp_path)),
        "config_fingerprint": compute_config_fingerprint(cfg.model_dump()),
        "goal_fingerprint": hashlib.sha256(f"{goal}\x00".encode("utf-8")).hexdigest(),
        **extra,
    })


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

def test_is_near_duplicate_rule_catches_real_observed_rephrasings():
    """Regression test using the actual duplicate pairs observed live: qpid/rules.txt
    accumulated ~11 near-duplicate rules across one session's repeated skill-gap
    prompts against overlapping reference material - each pair exact-string-distinct
    (so the pre-existing `r not in existing` check missed all of them) but restating
    the identical fact in shorter, differently-worded form."""
    original_model_version = (
        "The initial configuration JSON's \"modelVersion\" field is the broker's internal "
        "domain-model schema version, NOT the qpid-broker-core artifact/release version - "
        "do not set it to match the broker-core version (e.g. \"9.2\" for broker-core 9.2.1). "
        "Use \"8.0\", which is what qpid-broker-core itself ships as its own default "
        "initial-config.json across its 8.x/9.x/10.x releases. A mismatched modelVersion "
        "fails with \"IllegalConfigurationException: No phase upgrader for version X\" during "
        "SystemLauncher.startup() - this does NOT throw from startup() itself, so the broker "
        "silently never binds its AMQP port and any client connection attempt fails with a "
        "plain connection-refused error that gives no hint the root cause was the config file."
    )
    dup_model_version = (
        "Use modelVersion \"8.0\" in the initial configuration JSON, not the qpid-broker-core "
        "artifact version (e.g., do not use \"9.2\" for broker-core 9.2.1)."
    )
    assert dup_model_version not in [original_model_version]  # exact-string check would miss it
    assert _is_near_duplicate_rule(dup_model_version, [original_model_version])

    original_alias = (
        "The AMQP port in the initial configuration JSON must declare \"virtualhostaliases\" "
        "including a {\"type\": \"defaultAlias\"} entry. Without it, the client's AMQP "
        "Open-frame hostname (defaulting to whatever host is in the connection URI, e.g. "
        "\"localhost\") can never resolve to any virtualhost regardless of what that "
        "virtualhost is named - fails with \"JmsResourceNotFoundException: Unknown hostname "
        "in connection open\" even though the broker itself started and the port opened "
        "without error."
    )
    dup_alias = (
        "The AMQP port definition in the initial configuration JSON MUST include a "
        "virtualhostaliases entry with a defaultAlias, or client connections will fail to "
        "resolve the virtualhost."
    )
    assert _is_near_duplicate_rule(dup_alias, [original_alias])

    original_url = (
        "\"initialConfigurationLocation\" must be a real java.net.URL string, obtained via "
        "YourClass.class.getClassLoader().getResource(\"qpid-initial-config.json\")."
        "toExternalForm() (resolves to a file:/jar: URL the JDK understands). Do NOT build it "
        "as \"classpath:qpid-initial-config.json\" or new URL(\"classpath:...\") - "
        "\"classpath:\" is a Spring-only convention that plain java.net.URL does not recognize."
    )
    dup_url = (
        "The initial configuration JSON file must be loaded via a java.net.URL obtained using "
        "YourClass.class.getClassLoader().getResource(\"qpid-initial-config.json\")."
        "toExternalForm()."
    )
    assert _is_near_duplicate_rule(dup_url, [original_url])

def test_is_near_duplicate_rule_does_not_flag_genuinely_different_rules():
    rule_a = (
        "\"Red Hat Qpid MRG\" and \"Red Hat AMQ\" (classic messaging line) both refer to "
        "Apache Qpid - use genuine Apache Qpid Broker-J and qpid-jms, not ActiveMQ Artemis, "
        "even though both speak AMQP."
    )
    rule_b = (
        "Use org.apache.qpid:qpid-jms-client for the JMS client - version 1.16.0 for "
        "javax.jms (JMS 2.0), or 2.10.0 for jakarta.jms (Jakarta Messaging 3.1). Match "
        "whichever javax/jakarta convention the rest of the Spring project uses; do not mix "
        "the two."
    )
    assert not _is_near_duplicate_rule(rule_b, [rule_a])

def test_is_near_duplicate_rule_ignores_short_rules_below_min_words():
    assert not _is_near_duplicate_rule("Use version 9.2.1.", ["Use version 9.2.1 always."])

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

def test_resolve_run_command_substitutes_when_python_unresolvable():
    with patch("shutil.which", return_value=None):
        assert _resolve_run_command(["python", "main.py"]) == [sys.executable, "main.py"]

def test_resolve_run_command_leaves_command_alone_when_python_resolvable():
    with patch("shutil.which", return_value="/usr/bin/python"):
        assert _resolve_run_command(["python", "main.py"]) == ["python", "main.py"]

def test_resolve_run_command_ignores_non_python_commands():
    with patch("shutil.which", return_value=None):
        assert _resolve_run_command(["python3", "main.py"]) == ["python3", "main.py"]
        assert _resolve_run_command(["node", "main.js"]) == ["node", "main.js"]

def test_resolve_run_command_handles_empty_command():
    assert _resolve_run_command([]) == []

@pytest.mark.asyncio
async def test_workflow_uses_per_role_model_config(tmp_path):
    """Configured agent_llms overrides must actually reach each role's real
    llm.complete() call - proving the config flows from AppConfig through
    WorkflowEngine's constructed agents, not just that the config schema parses."""
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.agent_llms.planner.llm = LLMConfig(model="devstral-small-2:24b")
    cfg.agent_llms.reviewer.llm = LLMConfig(model="devstral-small-2:24b")
    # architect is deliberately left unset - should use the default call shape.
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    planner_kwargs = llm.complete.await_args_list[0].kwargs
    architect_kwargs = llm.complete.await_args_list[1].kwargs
    reviewer_kwargs = llm.complete.await_args_list[3].kwargs
    assert planner_kwargs.get("model_override") == "devstral-small-2:24b"
    assert "model_override" not in architect_kwargs  # unset role -> today's default call shape
    assert reviewer_kwargs.get("model_override") == "devstral-small-2:24b"

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
        n = len(model_overrides)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py"
        elif n in (3, 4, 5, 6):
            # Attempt 1 (full-set, primary model) then 3 targeted retries (also
            # primary model - targeted retries never escalate) - all still broken,
            # to exhaust the targeted budget before the fallback chain ever gets
            # a chance to run.
            return '[{"filepath": "math.py", "content": "def add(a,b)\\n    return a+b"}]'
        elif n == 7:
            # Targeted budget now exhausted - back to the full-set path, which
            # escalates to fallback-1 as retry_count > 0. Fixed this time.
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        elif n == 8:
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
    # The 3 targeted retries (attempts 2-4) must never escalate - only once that
    # budget is exhausted does the full-set path's fallback chain kick in.
    assert model_overrides[3] is None
    assert model_overrides[4] is None
    assert model_overrides[5] is None
    assert model_overrides[6] == "fallback-1"
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
async def test_workflow_run_verification_substitutes_unresolvable_python(tmp_path):
    """Reproduces a real observed failure: the Runtime Verification judge inferred a
    bare 'python' run command, which isn't on PATH on many real systems (Homebrew
    installs, Debian/Ubuntu without python-is-python3) - subprocess.run raised
    FileNotFoundError immediately, failing all retry attempts regardless of whether
    the generated code was actually correct. Kriya's own interpreter must be
    substituted so the run gets a real chance to prove the code works."""
    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    file_list_response = json.dumps([{"filepath": "app.py", "content": "print('[SUCCESS] it worked')\n"}])
    judge_response = json.dumps({
        "should_run": True,
        "run_command": ["python", "app.py"],  # the real observed inference - not sys.executable
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected [SUCCESS] line."})

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write app.py", file_list_response,
        judge_response, grade_response, "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)

    with patch("shutil.which", return_value=None):  # simulates no bare 'python' on PATH
        res = await we.run_generation_workflow(
            goal="Run with python app.py; it should print [SUCCESS]",
            workspace_path=str(tmp_path)
        )

    # If the substitution hadn't happened, this would fail with FileNotFoundError on
    # every retry attempt and quality_gates_passed would be False.
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
async def test_workflow_extracted_rule_unverified_then_promoted_by_passing_run(tmp_path):
    """A freshly extracted rule must be labeled distinctly as unverified in the SAME
    run's generation prompt (not blended in as equally authoritative as long-standing
    rules), and a passing Runtime Verification run must promote exactly that rule -
    not the skill's pre-existing untracked content, which was never flagged in the
    first place - to verified in the per-rule provenance file."""
    from kriya.skills.skill import SkillEngine, load_rule_provenance

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({
        "rules": ["The magic widget constant is 42."], "examples": {}, "conflicts": []
    })
    file_list_response = json.dumps([{"filepath": "app.py", "content": "print('[SUCCESS] WIDGET')\n"}])
    judge_response = json.dumps({
        "should_run": True,
        "run_command": [sys.executable, "app.py"],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains [SUCCESS]"
    })
    grade_response = json.dumps({"passed": True, "reasoning": "Output contains the expected line."})

    llm.complete = AsyncMock(side_effect=[
        extraction_response,   # SkillGapAgent.extract_skill_update for the human-supplied text
        "Step 1: Write code",  # Planner
        "Design: Write app.py",  # Architect
        file_list_response,    # Developer
        judge_response,         # RunVerifier.judge
        grade_response,         # RunVerifier.grade
        "Review: Approved"      # Reviewer
    ])

    def skill_gap_cb(reason, names):
        return "The magic widget constant is 42."

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Run with python app.py; the widgetlib skill applies here",
        workspace_path=str(tmp_path),
        skill_gap_callback=skill_gap_cb,
    )

    assert res["quality_gates_passed"] is True

    # The freshly-extracted rule was labeled unverified in the prompt sent to Planner.
    planner_prompt = llm.complete.call_args_list[1].args[1]
    assert "Unverified Rules:\n- The magic widget constant is 42." in planner_prompt

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is True  # skill-level flag, unchanged existing behavior

    provenance = {p["text"]: p for p in load_rule_provenance(skill.source_path)}
    assert provenance["The magic widget constant is 42."]["verified"] is True
    # The pre-existing, never-tracked rule has no provenance record at all - it was
    # never flagged unverified, so there's nothing for a passing run to "promote".
    assert "Existing rule." not in provenance


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


def _make_conflicting_skills_dir(tmp_path):
    # Deliberately fictional, non-colliding skill names - the real repo's own
    # skills/ directory (qpid, activemq-artemis, ...) is also loaded alongside a
    # project-local skills dir (SkillEngine defaults load_global=True), so reusing
    # those real names here would spuriously match real skills too.
    skills_dir = tmp_path / "skills"
    (skills_dir / "brokeralpha").mkdir(parents=True)
    (skills_dir / "brokeralpha" / "skill.yaml").write_text("name: brokeralpha\ndescription: Test\ntags: [brokeralpha]\n")
    (skills_dir / "brokeralpha" / "rules.txt").write_text("Broker must bind AMQP to port 5672.\n")
    (skills_dir / "brokerbeta").mkdir(parents=True)
    (skills_dir / "brokerbeta" / "skill.yaml").write_text("name: brokerbeta\ndescription: Test\ntags: [brokerbeta]\n")
    (skills_dir / "brokerbeta" / "rules.txt").write_text("Configure the broker to listen on port 5673 for AMQP clients.\n")
    return skills_dir

_ALPHA_RULE = "Broker must bind AMQP to port 5672."
_BETA_RULE = "Configure the broker to listen on port 5673 for AMQP clients."

# active_skills is sorted alphabetically before pairwise comparison, so
# "brokeralpha" (< "brokerbeta") is always passed as skill_a/rule_a here.
_CONFLICT_RESPONSE = json.dumps({
    "conflicts": [{
        "rule_a": _ALPHA_RULE,
        "rule_b": _BETA_RULE,
        "explanation": "Both skills configure the same embedded broker's AMQP port to a different value."
    }]
})

@pytest.mark.asyncio
async def test_workflow_skill_conflict_excludes_losing_rule_and_persists_resolution(tmp_path):
    """Two independently valid skills can still conflict when both are active for the
    same run (e.g. two broker skills each pinning a different port for what must be a
    single shared setting). A human-resolved 'prefer_a' must exclude the losing rule
    from THIS run's context, and remember the decision for future runs."""
    from kriya.skills.skill import load_conflict_resolutions

    skills_dir = _make_conflicting_skills_dir(tmp_path)

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        _CONFLICT_RESPONSE,       # SkillGapAgent.check_skill_conflicts
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    def conflict_cb(skill_a, rule_a, skill_b, rule_b, explanation):
        return "prefer_a"  # artemis (skill_a, alphabetically first) wins

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
        skill_conflict_callback=conflict_cb,
    )

    assert res["quality_gates_passed"] is True

    planner_prompt = llm.complete.call_args_list[1].args[1]
    assert _ALPHA_RULE in planner_prompt
    assert _BETA_RULE not in planner_prompt

    records = load_conflict_resolutions(str(skills_dir))
    assert len(records) == 1
    assert records[0]["resolution"] == "prefer_a"

@pytest.mark.asyncio
async def test_workflow_skill_conflict_remembered_resolution_skips_callback(tmp_path):
    """A conflict already resolved for this exact skill/rule pair on a prior run must
    be applied silently - the callback should not be re-invoked."""
    from kriya.skills.skill import record_conflict_resolution

    skills_dir = _make_conflicting_skills_dir(tmp_path)
    record_conflict_resolution(str(skills_dir), "brokeralpha", _ALPHA_RULE, "brokerbeta", _BETA_RULE, "prefer_b")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        _CONFLICT_RESPONSE,
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    calls = []
    def conflict_cb(skill_a, rule_a, skill_b, rule_b, explanation):
        calls.append((skill_a, skill_b))
        return "prefer_a"

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
        skill_conflict_callback=conflict_cb,
    )

    assert res["quality_gates_passed"] is True
    assert calls == []  # remembered resolution applied without asking again

    planner_prompt = llm.complete.call_args_list[1].args[1]
    # The remembered resolution was "prefer_b" (qpid wins) - qpid's rule must be the
    # one that survives, artemis's the one excluded.
    assert _BETA_RULE in planner_prompt
    assert _ALPHA_RULE not in planner_prompt

@pytest.mark.asyncio
async def test_workflow_skill_conflict_no_callback_response_does_not_persist(tmp_path):
    """A callback that returns nothing usable (e.g. -y auto-skip, or a callback error)
    must not exclude either rule and must not write a resolution to the registry -
    only an explicit human decision should be remembered."""
    from kriya.skills.skill import load_conflict_resolutions

    skills_dir = _make_conflicting_skills_dir(tmp_path)

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        _CONFLICT_RESPONSE,
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    def conflict_cb(skill_a, rule_a, skill_b, rule_b, explanation):
        return None

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
        skill_conflict_callback=conflict_cb,
    )

    assert res["quality_gates_passed"] is True

    planner_prompt = llm.complete.call_args_list[1].args[1]
    assert _ALPHA_RULE in planner_prompt
    assert _BETA_RULE in planner_prompt

    assert load_conflict_resolutions(str(skills_dir)) == []

@pytest.mark.asyncio
async def test_workflow_no_conflict_check_without_callback(tmp_path):
    """Without a skill_conflict_callback (e.g. the `fix` pipeline, which doesn't wire
    one in), the conflict-detection LLM call must not fire at all."""
    skills_dir = _make_conflicting_skills_dir(tmp_path)

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",     # Planner - no conflict-check call precedes it
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Build something using both the brokeralpha and brokerbeta skills",
        workspace_path=str(tmp_path),
    )

    assert res["quality_gates_passed"] is True


@pytest.mark.asyncio
async def test_workflow_web_lookup_auto_resolves_skill_gap(tmp_path):
    """When web_lookup_enabled and a search backend are configured, an unverified
    skill gap should be auto-resolved via search+fetch BEFORE ever asking a human for
    a URL - the human-ask path (skill_gap_callback) must not fire at all if live
    lookup fully resolves the gap and the batch is confirmed."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({
        "rules": ["The magic widget constant is 42."],
        "examples": {},
        "conflicts": []
    })
    llm.complete = AsyncMock(side_effect=[
        extraction_response,      # SkillGapAgent.extract_skill_update, for the auto-found reference
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    found_result = [{"term": "widgetlib", "title": "Widgetlib Docs", "url": "https://example.com/widgetlib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="The magic widget constant is 42.")) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with("widgetlib documentation", "http://fake-search:8080", top_k=3)
    mock_fetch.assert_called_once_with("https://example.com/widgetlib")
    assert skill_gap_calls == []  # human-ask path never fired - live lookup resolved it first

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert "The magic widget constant is 42." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_falls_through_to_next_candidate_on_empty_extraction(tmp_path):
    """A single unhelpful top search result (a landing page with nothing concrete to
    extract - confirmed to happen in real testing) must not sink the whole lookup:
    the next candidate should be tried, and the term only counts as unresolved if
    NONE of the fetched candidates yield anything usable."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    cfg.search.top_k = 2
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    empty_extraction = json.dumps({"rules": [], "examples": {}, "conflicts": []})
    real_extraction = json.dumps({"rules": ["The magic widget constant is 42."], "examples": {}, "conflicts": []})
    llm.complete = AsyncMock(side_effect=[
        empty_extraction,         # candidate 1 (landing page) - nothing usable
        real_extraction,          # candidate 2 - the real answer
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    found_result = [
        {"title": "Widgetlib Home", "url": "https://example.com/widgetlib-home", "snippet": "landing page"},
        {"title": "Widgetlib Reference", "url": "https://example.com/widgetlib-ref", "snippet": "reference docs"},
    ]

    async def fetch_side_effect(url):
        return "Marketing copy, no specifics." if url.endswith("-home") else "The magic widget constant is 42."

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(side_effect=fetch_side_effect)) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=lambda reason, names: None,
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    assert mock_fetch.await_count == 2  # both candidates were fetched

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert "The magic widget constant is 42." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_declined_falls_back_to_human_ask(tmp_path):
    """Declining the live-lookup batch confirmation must fall back to the existing
    human-ask (skill_gap_callback) path, not silently drop the gap."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',
        "Review: Approved"
    ])

    found_result = [{"term": "widgetlib", "title": "Widgetlib Docs", "url": "https://example.com/widgetlib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return None

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="text")):
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: False,  # decline the batch
        )

    assert res["quality_gates_passed"] is True
    assert skill_gap_calls == [["widgetlib"]]  # fell back to asking a human

@pytest.mark.asyncio
async def test_workflow_web_lookup_accepted_but_empty_falls_back_to_human_ask(tmp_path):
    """Bug fix: accepting the live-lookup batch doesn't by itself mean the gap is
    resolved - if extraction across all fetched candidates still comes up empty, the
    term must fall through to skill_gap_callback exactly as if lookup had never run,
    not silently leave the skill unverified with no one ever asked. If the human then
    supplies something usable, it must be applied."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    cfg.search.top_k = 1
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    empty_extraction = json.dumps({"rules": [], "examples": {}, "conflicts": []})
    real_extraction = json.dumps({"rules": ["The magic widget constant is 42."], "examples": {}, "conflicts": []})
    llm.complete = AsyncMock(side_effect=[
        empty_extraction,         # live-lookup's one candidate - nothing usable
        real_extraction,          # human-supplied reference, after fallback - usable
        "Step 1: Write code",     # Planner
        "Design: Write app.py",   # Architect
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',  # Developer
        "Review: Approved"        # Reviewer
    ])

    found_result = [{"title": "Widgetlib Landing Page", "url": "https://example.com/widgetlib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return "The magic widget constant is 42."  # pasted text, not a URL - avoids egress_policy entirely

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="The magic widget constant is 42.")):
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: True,  # batch accepted, but content is empty
        )

    assert res["quality_gates_passed"] is True
    assert skill_gap_calls == [["widgetlib"]]  # fell through to human ask despite an accepted batch

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert "The magic widget constant is 42." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_disabled_by_default_never_calls_search(tmp_path):
    """web_lookup_enabled defaults to False - a project that hasn't opted in must see
    zero behavior change, and search_web must never even be called."""
    skills_dir = tmp_path / "skills"
    skill_folder = skills_dir / "widgetlib"
    skill_folder.mkdir(parents=True)
    (skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    assert cfg.autonomy.web_lookup_enabled is False
    assert cfg.search.base_url == ""
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)\\n"}]', "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(side_effect=AssertionError("must not be called"))) as mock_search:
        res = await we.run_generation_workflow(
            goal="Build something with the widgetlib skill",
            workspace_path=str(tmp_path),
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_not_called()

@pytest.mark.asyncio
async def test_workflow_web_lookup_design_derived_bootstraps_new_skill(tmp_path):
    """The goal alone may not name any specific technology, but the Architect's design
    usually will once it makes real decisions - live lookup should catch that too, not
    just what the goal-text-only skill-gap check already covers."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    extraction_response = json.dumps({
        "rules": ["Use gizmolib.connect() to open a connection."],
        "examples": {},
        "conflicts": []
    })
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",                                     # Planner
        "Design: use gizmolib==3.1.0 to connect to the service",  # Architect names a new lib
        extraction_response,                                      # Stage 2B SkillGapAgent.extract_skill_update
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',     # Developer
        "Review: Approved"                                        # Reviewer
    ])

    found_result = [{"term": "gizmolib", "title": "Gizmolib Docs", "url": "https://example.com/gizmolib", "snippet": "..."}]

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="gizmolib docs text")) as mock_fetch:
        res = await we.run_generation_workflow(
            goal="Build an app that talks to an external service",
            workspace_path=str(tmp_path),
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with("gizmolib documentation", "http://fake-search:8080", top_k=3)
    mock_fetch.assert_called_once_with("https://example.com/gizmolib")

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("gizmolib")
    assert "Use gizmolib.connect() to open a connection." in skill.rules

@pytest.mark.asyncio
async def test_workflow_web_lookup_design_derived_falls_back_to_human_ask_on_empty_extraction(tmp_path):
    """Bug fix, design-derived path: if live lookup finds nothing usable for a
    technology only the Architect's design named, Kriya must fall back to asking a
    human (skill_gap_callback) rather than silently generating code against a
    technology it has zero grounding for."""
    from kriya.skills.skill import SkillEngine

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    cfg = AppConfig()
    cfg.paths.skills = str(skills_dir)
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    cfg.search.top_k = 1
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    empty_extraction = json.dumps({"rules": [], "examples": {}, "conflicts": []})
    real_extraction = json.dumps({"rules": ["Use gizmolib.connect() to open a connection."], "examples": {}, "conflicts": []})
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",                                     # Planner
        "Design: use gizmolib==3.1.0 to connect to the service",  # Architect names a new lib
        empty_extraction,                                         # live lookup's candidate - nothing usable
        real_extraction,                                          # human-supplied reference, after fallback
        '[{"filepath": "app.py", "content": "print(1)\\n"}]',     # Developer
        "Review: Approved"                                        # Reviewer
    ])

    found_result = [{"title": "Gizmolib Landing Page", "url": "https://example.com/gizmolib", "snippet": "..."}]
    skill_gap_calls = []
    def skill_gap_cb(reason, names):
        skill_gap_calls.append(names)
        return "Use gizmolib.connect() to open a connection."

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found_result)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="Marketing copy, no specifics.")):
        res = await we.run_generation_workflow(
            goal="Build an app that talks to an external service",
            workspace_path=str(tmp_path),
            skill_gap_callback=skill_gap_cb,
            web_lookup_callback=lambda found: True,
        )

    assert res["quality_gates_passed"] is True
    assert skill_gap_calls == [["gizmolib"]]  # fell back to asking a human for the design-derived gap

    se = SkillEngine(str(skills_dir), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("gizmolib")
    assert "Use gizmolib.connect() to open a connection." in skill.rules


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


@pytest.mark.asyncio
async def test_workflow_successful_run_deletes_its_own_checkpoint(tmp_path):
    """A checkpoint is only useful across a crash - a normal completion should
    leave nothing behind for a future --resume to (mis)pick up."""
    _init_git_repo(tmp_path)
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
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert res.get("run_id")
    assert not os.path.exists(checkpoint_path(str(tmp_path), res["run_id"]))
    assert find_latest_checkpoint(str(tmp_path)) is None


@pytest.mark.asyncio
async def test_workflow_resumes_from_plan_checkpoint_skips_planner_only(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    goal = "Create math library"

    _seed_checkpoint(tmp_path, cfg, goal, "ckpt-plan", "plan", plan="Step 1: Write code (from checkpoint)")

    llm.complete = AsyncMock(side_effect=[
        "Design: Write math.py",  # Architect
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',  # Developer
        "Review: Approved",  # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal=goal, workspace_path=str(tmp_path), resume=True)

    assert res["quality_gates_passed"] is True
    assert res["plan"] == "Step 1: Write code (from checkpoint)"
    assert llm.complete.await_count == 3  # Planner call skipped


@pytest.mark.asyncio
async def test_workflow_resumes_from_design_checkpoint_skips_planner_and_architect(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    goal = "Create math library"

    _seed_checkpoint(
        tmp_path, cfg, goal, "ckpt-design", "design",
        plan="Step 1 (from checkpoint)", design="Design: Write math.py (from checkpoint)",
    )

    llm.complete = AsyncMock(side_effect=[
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',  # Developer
        "Review: Approved",  # Reviewer
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal=goal, workspace_path=str(tmp_path), resume_id="ckpt-design")

    assert res["quality_gates_passed"] is True
    assert res["design"] == "Design: Write math.py (from checkpoint)"
    assert llm.complete.await_count == 2  # Planner + Architect calls both skipped


@pytest.mark.asyncio
async def test_workflow_resumes_from_developer_success_checkpoint_skips_quality_gates(tmp_path):
    """The most valuable resume point: Developer generation + compile/test gates
    already passed before the crash, so only the human-approval/apply/regression
    tail and the Reviewer need to run - no re-generation, no re-compiling."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    goal = "Create math library"

    _seed_checkpoint(
        tmp_path, cfg, goal, "ckpt-dev", "developer_success",
        plan="Step 1 (from checkpoint)",
        design="Design: Write math.py (from checkpoint)",
        final_files={"math.py": "def add(a,b):\n    return a+b"},
        original_files={},
        gate_outcomes=[],
        model_hops=[],
        retry_count=0,
    )

    llm.complete = AsyncMock(side_effect=["Review: Approved"])  # only the Reviewer should run

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal=goal, workspace_path=str(tmp_path), resume=True)

    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    assert (tmp_path / "math.py").read_text() == "def add(a,b):\n    return a+b"
    assert llm.complete.await_count == 1  # Planner, Architect, Developer all skipped


@pytest.mark.asyncio
async def test_workflow_refuses_resume_on_goal_drift(tmp_path):
    """Strict drift detection: any goal-text difference since the checkpoint was
    saved must invalidate it entirely and fall back to a normal fresh run rather
    than a partial/best-effort resume."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    _seed_checkpoint(tmp_path, cfg, "Create math library", "ckpt-stale", "plan", plan="Stale plan")

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create a DIFFERENT math library",  # deliberately not the checkpoint's goal
        workspace_path=str(tmp_path),
        resume=True,
    )

    assert res["quality_gates_passed"] is True
    assert res["plan"] == "Step 1: Write code"  # real Planner call, not the stale checkpoint value
    assert llm.complete.await_count == 4  # nothing was skipped


@pytest.mark.asyncio
async def test_workflow_resume_with_no_checkpoint_falls_back_to_fresh_run(tmp_path):
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path), resume=True)

    assert res["quality_gates_passed"] is True
    assert llm.complete.await_count == 4


def test_checkpoint_save_load_delete_roundtrip(tmp_path):
    save_checkpoint(str(tmp_path), "run-1", {"stage": "plan", "plan": "hello"})
    loaded = load_checkpoint(str(tmp_path), "run-1")
    assert loaded["stage"] == "plan"
    assert loaded["plan"] == "hello"
    assert loaded["run_id"] == "run-1"

    from kriya.workflow.checkpoint import delete_checkpoint
    delete_checkpoint(str(tmp_path), "run-1")
    assert load_checkpoint(str(tmp_path), "run-1") is None


def test_checkpoint_workspace_fingerprint_changes_on_new_commit(tmp_path):
    _init_git_repo(tmp_path)
    fp1 = compute_workspace_fingerprint(str(tmp_path))
    assert fp1 is not None and fp1.endswith(":clean")

    (tmp_path / "app.py").write_text("print(1)\n")
    fp2 = compute_workspace_fingerprint(str(tmp_path))
    assert fp2.endswith(":dirty")
    assert fp2 != fp1

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=tmp_path, check=True)
    fp3 = compute_workspace_fingerprint(str(tmp_path))
    assert fp3.endswith(":clean")
    assert fp3 != fp1 and fp3 != fp2


def test_checkpoint_workspace_fingerprint_none_for_non_git_dir(tmp_path):
    assert compute_workspace_fingerprint(str(tmp_path)) is None


def test_checkpoint_config_fingerprint_stable_and_sensitive_to_changes():
    cfg_a = AppConfig()
    cfg_b = AppConfig()
    assert compute_config_fingerprint(cfg_a.model_dump()) == compute_config_fingerprint(cfg_b.model_dump())

    cfg_b.autonomy.run_verification_enabled = not cfg_b.autonomy.run_verification_enabled
    assert compute_config_fingerprint(cfg_a.model_dump()) != compute_config_fingerprint(cfg_b.model_dump())


def test_checkpoint_find_latest_returns_most_recently_saved(tmp_path):
    save_checkpoint(str(tmp_path), "older", {"stage": "plan"})
    import time as _time
    _time.sleep(0.01)
    save_checkpoint(str(tmp_path), "newer", {"stage": "design"})
    assert find_latest_checkpoint(str(tmp_path)) == "newer"


def test_extract_error_search_terms_finds_maven_coordinate():
    error = (
        "[ERROR] Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java "
        "for parameter arguments: Cannot store value into array"
    )
    assert extract_error_search_terms(error) == ["org.codehaus.mojo:exec-maven-plugin"]

def test_extract_error_search_terms_dedups_multiple_occurrences():
    error = (
        "org.codehaus.mojo:exec-maven-plugin failed. See org.codehaus.mojo:exec-maven-plugin docs."
    )
    assert extract_error_search_terms(error) == ["org.codehaus.mojo:exec-maven-plugin"]

def test_extract_error_search_terms_finds_multiple_distinct_coordinates():
    error = "org.apache.maven.plugins:maven-compiler-plugin failed after org.codehaus.mojo:exec-maven-plugin succeeded"
    assert extract_error_search_terms(error) == [
        "org.apache.maven.plugins:maven-compiler-plugin",
        "org.codehaus.mojo:exec-maven-plugin",
    ]

def test_extract_error_search_terms_ignores_plain_symbols_and_paths():
    # Neither a bare class/package name (no colon) nor a filesystem path (colon-free
    # on this platform, and even Windows-style drive letters don't match groupId
    # shape) should be treated as a safe search term - only real dotted-namespace
    # coordinate syntax should match.
    error = (
        "cannot find symbol: class JmsConnectionFactory\n"
        "location: class com.example.CacheAndMessagingClient\n"
        "at /Users/dev/project/src/main/java/com/example/App.java:19"
    )
    assert extract_error_search_terms(error) == []

@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_no_terms_never_searches():
    with patch("kriya.tools.search.search_web", new=AsyncMock()) as mock_search:
        result = await _augment_error_with_live_lookup("some error", [], "http://fake-search:8080", 3)
    assert result == "some error"
    mock_search.assert_not_called()

@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_appends_found_content():
    found = [{"term": "org.codehaus.mojo:exec-maven-plugin", "url": "https://example.com/exec-plugin", "snippet": "..."}]
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)), \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="Remove the <arguments> block.")):
        result = await _augment_error_with_live_lookup(
            "COMPILATION FAILURE: ...", ["org.codehaus.mojo:exec-maven-plugin"], "http://fake-search:8080", 3
        )
    assert "COMPILATION FAILURE: ..." in result
    assert "Reference material found for 'org.codehaus.mojo:exec-maven-plugin'" in result
    assert "Remove the <arguments> block." in result

@pytest.mark.asyncio
async def test_augment_error_with_live_lookup_nothing_found_returns_unchanged():
    with patch("kriya.tools.search.search_web", new=AsyncMock(return_value=[])):
        result = await _augment_error_with_live_lookup(
            "some error", ["org.codehaus.mojo:exec-maven-plugin"], "http://fake-search:8080", 3
        )
    assert result == "some error"


@pytest.mark.asyncio
async def test_workflow_error_triggered_live_lookup_on_repeated_compile_failure(tmp_path):
    """A compile failure that repeats identically across two consecutive Developer
    retry attempts (the model isn't self-correcting) should trigger live lookup,
    folding found reference material into the THIRD attempt's prompt - not the
    first attempt (no repeat yet), and search must fire exactly once."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.web_lookup_enabled = True
    cfg.search.base_url = "http://fake-search:8080"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    repeated_error = (
        "Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java "
        "for parameter arguments: Cannot store value into array"
    )

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {}"}]',  # attempt 1 - fails
        '[{"filepath": "App.java", "content": "class App {}"}]',  # attempt 2 - fails identically (repeat)
        '[{"filepath": "App.java", "content": "class App {}"}]',  # attempt 3 - succeeds
        "Review: Approved",
    ])

    found = [{"term": "org.codehaus.mojo:exec-maven-plugin", "url": "https://example.com/exec-plugin", "snippet": "..."}]

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.search.search_web", new=AsyncMock(return_value=found)) as mock_search, \
         patch("kriya.tools.web.fetch_url_text", new=AsyncMock(return_value="Remove the <arguments> block - exec:java doesn't need it.")):
        mock_compile.side_effect = [
            {"success": False, "output": repeated_error},
            {"success": False, "output": repeated_error},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    mock_search.assert_called_once_with(
        "org.codehaus.mojo:exec-maven-plugin documentation", "http://fake-search:8080", top_k=3
    )

    third_attempt_prompt = llm.complete.await_args_list[4].args[1]
    assert "Reference material found for 'org.codehaus.mojo:exec-maven-plugin'" in third_attempt_prompt
    assert "Remove the <arguments> block" in third_attempt_prompt

    # And the first (non-repeat) failure must not have triggered anything - the
    # second Developer call's prompt has the raw error but no lookup content yet.
    second_attempt_prompt = llm.complete.await_args_list[3].args[1]
    assert "Reference material found" not in second_attempt_prompt

@pytest.mark.asyncio
async def test_workflow_error_triggered_live_lookup_disabled_by_default_never_searches(tmp_path):
    """Same repeated-failure scenario, but web_lookup_enabled left at its default
    (False) - must never call search_web, even though the failure repeats and
    contains an extractable coordinate."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    # web_lookup_enabled left False (default) - no search.base_url either.
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    repeated_error = "Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java"

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        '[{"filepath": "App.java", "content": "class App {}"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile, \
         patch("kriya.tools.search.search_web", new=AsyncMock()) as mock_search:
        mock_compile.side_effect = [
            {"success": False, "output": repeated_error},
            {"success": False, "output": repeated_error},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    mock_search.assert_not_called()


def test_extract_implicated_files_matches_basename_in_error():
    error = "path/to/CacheAndMessagingClient.java:[22,13] cannot find symbol"
    known = ["src/main/java/com/example/CacheAndMessagingClient.java", "pom.xml"]
    assert extract_implicated_files(error, known) == ["src/main/java/com/example/CacheAndMessagingClient.java"]

def test_extract_implicated_files_matches_multiple():
    error = "App.java:[5,1] error, and also BrokerServer.java:[10,2] error"
    known = ["src/App.java", "src/BrokerServer.java", "pom.xml"]
    assert extract_implicated_files(error, known) == ["src/App.java", "src/BrokerServer.java"]

def test_extract_implicated_files_empty_when_no_known_file_named():
    error = "Process exited with code 1. No further details available."
    known = ["App.java", "pom.xml"]
    assert extract_implicated_files(error, known) == []

def test_extract_implicated_files_matches_full_relative_path_too():
    error = "Traceback: File \"src/main/py/app.py\", line 3, in <module>"
    known = ["src/main/py/app.py"]
    assert extract_implicated_files(error, known) == ["src/main/py/app.py"]


def test_build_targeted_retry_prompt_frames_target_and_reference_files(tmp_path):
    (tmp_path / "App.java").write_text("class App { /* broken */ }")
    (tmp_path / "Helper.java").write_text("class Helper { /* fine */ }")

    task_desc, context = _build_targeted_retry_prompt(
        goal="Build the app",
        plan="Fix App.java",
        error_context="cannot find symbol in App.java",
        target_files=["App.java"],
        all_files_written=["App.java", "Helper.java"],
        worktree_path=str(tmp_path),
        active_code_context="=== base RAG context ===\n",
    )

    assert "TARGETED fix attempt" in task_desc
    assert "App.java" in task_desc
    assert "cannot find symbol in App.java" in task_desc
    assert "=== base RAG context ===" in context
    assert "File to fix: App.java" in context
    assert "class App { /* broken */ }" in context
    assert "already correct, reference only" in context
    assert "Helper.java" in context
    assert "class Helper { /* fine */ }" in context


@pytest.mark.asyncio
async def test_workflow_targeted_retry_fixes_implicated_file_without_escalating(tmp_path):
    """A compile failure that names a known file should trigger a targeted retry
    on the very next attempt (not a full-file-set regeneration), using the
    primary model even with a fallback chain configured - and the targeted
    attempt's prompt should show the model its own previous (broken) content,
    which the full-set path never does."""
    from kriya.config import FallbackModelConfig
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1")]
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    prompts_seen = []

    async def mock_complete(*args, **kwargs):
        prompts_seen.append((args[1] if len(args) > 1 else None, kwargs.get("model_override")))
        n = len(prompts_seen)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py"
        elif n == 3:
            return '[{"filepath": "math.py", "content": "def add(a,b)\\n    return a+b"}]'
        elif n == 4:
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        else:
            return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    # Attempt 2 (index 3 in prompts_seen) must be the targeted retry: primary
    # model (no override, despite a configured fallback chain) and a prompt that
    # shows the model its own broken previous content, not just the error text.
    targeted_prompt, targeted_model_override = prompts_seen[3]
    assert targeted_model_override is None
    assert "TARGETED fix attempt" in targeted_prompt
    assert "def add(a,b)" in targeted_prompt  # the actual previous (broken) content

@pytest.mark.asyncio
async def test_workflow_no_targeted_retry_when_error_names_no_known_file(tmp_path):
    """An error that doesn't mention any known file must never trigger a
    targeted attempt - it should escalate through the full-set fallback chain
    exactly as before this feature existed."""
    from kriya.config import FallbackModelConfig
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1")]
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    model_overrides = []

    async def mock_complete(*args, **kwargs):
        model_overrides.append(kwargs.get("model_override"))
        n = len(model_overrides)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py"
        elif n in (3, 4):
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        else:
            return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": "Process exited with code 1. No file information available."},
            {"success": True, "output": ""},
        ]
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    # Attempt 2 must escalate to the fallback chain (full-set path), never a
    # targeted (primary-model) attempt, since no file was implicated.
    assert model_overrides[3] == "fallback-1"

@pytest.mark.asyncio
async def test_workflow_success_via_targeted_attempt_after_full_set_budget_exhausted(tmp_path):
    """Regression test for a real bug caught while implementing this: quality_passed
    used to be computed as `retry_count < max_retries`, which is wrong once a run
    can succeed via a targeted attempt AFTER the full-set budget is already
    exhausted - this must still report success correctly."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    # No llm_chain configured -> max_retries defaults to 4.
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    same_error = "Failed to build math.py"
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        # 4 full-set failures (exhausts the default max_retries=4 full-set budget,
        # each one naming math.py so a targeted attempt becomes eligible), then a
        # 5th, targeted attempt succeeds.
        mock_compile.side_effect = [
            {"success": False, "output": same_error},
            {"success": False, "output": same_error},
            {"success": False, "output": same_error},
            {"success": False, "output": same_error},
            {"success": True, "output": ""},
        ]
        # Every Developer call (1 full-set + 3 targeted + 1 more targeted) returns
        # the same file - content doesn't matter here since run_compile_check is
        # mocked directly.
        we.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "math.py", "content": "def add(a,b): return a+b"}]
        )
        res = await we.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True



def test_incomplete_generation_error_carries_missing_files():
    err = IncompleteGenerationError(["Helper.java", "config.xml"], "INCOMPLETE GENERATION: ...")
    assert err.missing_files == ["Helper.java", "config.xml"]
    assert isinstance(err, ValueError)
    assert str(err) == "INCOMPLETE GENERATION: ..."


def test_build_missing_files_retry_prompt_frames_missing_and_reference_files(tmp_path):
    (tmp_path / "BrokerServer.java").write_text("class BrokerServer { /* written */ }")

    task_desc, context = _build_missing_files_retry_prompt(
        goal="Build the broker",
        plan="Write BrokerServer.java and BrokerConfig.java",
        design="## Files to Create\n- BrokerServer.java\n- BrokerConfig.java",
        missing_files=["BrokerConfig.java"],
        all_files_written=["BrokerServer.java"],
        worktree_path=str(tmp_path),
        active_code_context="=== base RAG context ===\n",
    )

    assert "MISSING-FILE recovery attempt" in task_desc
    assert "BrokerConfig.java" in task_desc
    assert "=== base RAG context ===" in context
    assert "=== Architect Design ===" in context
    assert "## Files to Create" in context
    assert "Existing file (already written, reference only" in context
    assert "class BrokerServer { /* written */ }" in context


@pytest.mark.asyncio
async def test_workflow_missing_file_recovery_does_not_escalate_or_consume_fullset_budget(tmp_path):
    """A completeness-check failure (Developer omitted a design-required file) must
    trigger a missing-file recovery retry on the very next attempt - primary model
    only, even with a fallback chain configured - not a full-file-set regeneration
    and not model escalation, mirroring the existing implicated-file targeted retry's
    no-escalation budget."""
    from kriya.config import FallbackModelConfig
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model="fallback-1")]
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)

    prompts_seen = []

    async def mock_complete(*args, **kwargs):
        prompts_seen.append((args[1] if len(args) > 1 else None, kwargs.get("model_override")))
        n = len(prompts_seen)
        if n == 1:
            return "Step 1: Write code"
        elif n == 2:
            return "Design: Write math.py and helper.py"
        elif n == 3:
            # Attempt 1 (full-set): only writes math.py, omitting helper.py.
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        elif n == 4:
            # Attempt 2 (missing-file recovery): writes the omitted file.
            return '[{"filepath": "helper.py", "content": "def helper():\\n    pass"}]'
        else:
            return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create math library with a helper module", workspace_path=str(tmp_path)
    )

    assert res["quality_gates_passed"] is True
    assert "helper.py" in res["files"]
    # Attempt 2 (index 3) must be the missing-file recovery retry: primary model
    # (no override, despite a configured fallback chain) and a prompt that names
    # the specific missing file, not a generic full-set regeneration.
    recovery_prompt, recovery_model_override = prompts_seen[3]
    assert recovery_model_override is None
    assert "MISSING-FILE recovery attempt" in recovery_prompt
    assert "helper.py" in recovery_prompt


def test_write_skill_extraction_never_overwrites_existing_example(tmp_path):
    """Regression test for a real bug caught live: a skill-gap/live-lookup extraction
    writing a new 'examples' entry whose basename matches an already-existing example
    file used to silently overwrite it - destroying previously-curated content (a real
    exec-maven-plugin pom.xml example was clobbered by a bare-dependencies-only
    version during live testing). Existing example files must never be overwritten by
    extraction - only genuinely new filenames get written."""
    from kriya.skills.skill import Skill
    from kriya.workflow.workflow import _write_skill_extraction

    _init_git_repo(tmp_path)
    skill_dir = tmp_path / "myskill"
    examples_dir = skill_dir / "examples"
    examples_dir.mkdir(parents=True)
    (examples_dir / "pom.xml").write_text("ORIGINAL CURATED CONTENT")

    skill = Skill(name="myskill", description="test", source_path=str(skill_dir))

    _write_skill_extraction(
        skill,
        {"examples": {"pom.xml": "OVERWRITTEN BY EXTRACTION", "new_file.txt": "brand new content"}},
        source="test",
    )

    assert (examples_dir / "pom.xml").read_text() == "ORIGINAL CURATED CONTENT"
    assert (examples_dir / "new_file.txt").read_text() == "brand new content"


def test_create_git_worktree_carries_over_uncommitted_changes(tmp_path):
    """Regression test for a real bug caught live: create_git_worktree only ever
    reflected git HEAD, so any uncommitted work in the real workspace (the normal
    state of an in-progress project) was invisible inside the sandbox - a goal
    building additively on a previous uncommitted change failed every retry with
    confusing "package does not exist" errors, since the file it was told to
    preserve/extend simply didn't exist in the sandbox at all."""
    from kriya.workflow.workflow import create_git_worktree

    _init_git_repo(tmp_path)

    # Modify a tracked file without committing.
    (tmp_path / "README.md").write_text("modified but uncommitted\n")
    # Add a brand-new untracked file.
    (tmp_path / "pom.xml").write_text("<project>uncommitted new file</project>\n")

    worktree_path = create_git_worktree(str(tmp_path))

    readme = open(os.path.join(worktree_path, "README.md")).read()
    assert readme == "modified but uncommitted\n"
    pom = open(os.path.join(worktree_path, "pom.xml")).read()
    assert pom == "<project>uncommitted new file</project>\n"


def test_create_git_worktree_removes_files_deleted_in_working_tree(tmp_path):
    """A file deleted (uncommitted) in the real workspace must not linger as a stale
    HEAD-only copy in the worktree sandbox."""
    from kriya.workflow.workflow import create_git_worktree

    _init_git_repo(tmp_path)
    (tmp_path / "extra.txt").write_text("will be committed then deleted\n")
    subprocess.run(["git", "add", "extra.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add extra.txt"], cwd=tmp_path, check=True)
    os.remove(tmp_path / "extra.txt")

    worktree_path = create_git_worktree(str(tmp_path))

    assert not os.path.exists(os.path.join(worktree_path, "extra.txt"))
