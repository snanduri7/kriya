import json
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import DeveloperAgent, PlannerAgent, RunVerifierAgent, SkillGapAgent
from kriya.config import AppConfig
from kriya.core.llm import LLMClient


@pytest.mark.asyncio
async def test_base_agent_complete():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    
    # Mock complete method
    llm.complete = AsyncMock(return_value="Mock response text")
    
    planner = PlannerAgent("planner", llm)
    res = await planner.run("Plan a math library")
    
    assert res == "Mock response text"
    llm.complete.assert_called_once_with(planner.system_prompt, "Plan a math library", stream_callback=None)

@pytest.mark.asyncio
async def test_developer_agent_json_parsing():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    
    # Mock code generation response
    json_response = """
    [
      {
        "filepath": "math_lib.py",
        "content": "def add(a, b): return a + b"
      }
    ]
    """
    llm.complete = AsyncMock(return_value=json_response)
    
    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation("Goal: Add math lib", "Design specs", "Existing code")

    assert len(files) == 1
    assert files[0]["filepath"] == "math_lib.py"
    assert files[0]["content"] == "def add(a, b): return a + b"

def test_normalize_file_entries_list_of_strings():
    entries = DeveloperAgent._normalize_file_entries(["pom.xml", "App.java"])
    assert entries == [
        {"filepath": "pom.xml", "content": None, "edits": None},
        {"filepath": "App.java", "content": None, "edits": None},
    ]

def test_normalize_file_entries_list_of_dicts_with_content():
    entries = DeveloperAgent._normalize_file_entries([{"filepath": "pom.xml", "content": "<project/>"}])
    assert entries == [{"filepath": "pom.xml", "content": "<project/>", "edits": None}]

def test_normalize_file_entries_list_of_dicts_missing_content():
    entries = DeveloperAgent._normalize_file_entries([{"filepath": "pom.xml"}, {"filepath": "App.java", "content": ""}])
    assert entries == [
        {"filepath": "pom.xml", "content": None, "edits": None},
        {"filepath": "App.java", "content": None, "edits": None},
    ]

def test_normalize_file_entries_path_key_alias():
    entries = DeveloperAgent._normalize_file_entries([{"path": "pom.xml", "content": "<project/>"}])
    assert entries == [{"filepath": "pom.xml", "content": "<project/>", "edits": None}]

def test_normalize_file_entries_dict_wrapping_list():
    entries = DeveloperAgent._normalize_file_entries({"files": [{"filepath": "pom.xml", "content": "<project/>"}]})
    assert entries == [{"filepath": "pom.xml", "content": "<project/>", "edits": None}]

def test_normalize_file_entries_unparseable_returns_none():
    assert DeveloperAgent._normalize_file_entries({}) is None
    assert DeveloperAgent._normalize_file_entries([]) is None
    assert DeveloperAgent._normalize_file_entries("not a list or dict") is None
    assert DeveloperAgent._normalize_file_entries([1, 2, 3]) is None

@pytest.mark.asyncio
async def test_fill_missing_content_only_calls_for_missing_entries():
    cfg = AppConfig()
    llm = LLMClient(cfg)

    file_list_response = json.dumps([
        {"filepath": "pom.xml", "content": "<project>existing</project>"},
        {"filepath": "src/main/java/com/example/App.java"}
    ])

    llm.complete = AsyncMock(side_effect=[
        file_list_response,
        "package com.example;\npublic class App {}"
    ])

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation("Task", "Design", "Existing code")

    # Only one extra completion call for the entry missing content - the one that
    # already had content must not trigger a redundant regeneration call.
    assert llm.complete.call_count == 2

    files_by_path = {f["filepath"]: f["content"] for f in files}
    assert files_by_path["pom.xml"] == "<project>existing</project>"
    assert files_by_path["src/main/java/com/example/App.java"] == "package com.example;\npublic class App {}"

    # The per-file call for App.java should mention pom.xml as a sibling file in this batch.
    second_call_args = llm.complete.call_args_list[1]
    file_prompt = second_call_args[0][1]
    assert "Other Files In This Batch" in file_prompt
    assert "pom.xml" in file_prompt

def test_strip_markdown_fences_plain_leading_fence():
    text = "```python\ndef add(a, b):\n    return a + b\n```"
    assert DeveloperAgent._strip_markdown_fences(text) == "def add(a, b):\n    return a + b"

def test_strip_markdown_fences_no_fence_passthrough():
    text = "def add(a, b):\n    return a + b"
    assert DeveloperAgent._strip_markdown_fences(text) == text

def test_strip_markdown_fences_prose_wrapped_fence():
    # Reproduces the deepseek-r1 fallback-model failure: the model returns the
    # correct fenced code but surrounds it with conversational pre/postamble
    # instead of ONLY the fence, despite being told not to.
    text = (
        "Now, to fix the import issue between `tests/` and `src/`, we need to "
        "create an empty `__init__.py` file in both directories.\n\n"
        "Here is the complete content for:\n\n"
        "```python\n"
        "# This is a blank file that makes the directory a Python package\n"
        "```\n\n"
        "This simple file will ensure proper module importing when running pytest tests."
    )
    assert DeveloperAgent._strip_markdown_fences(text) == (
        "# This is a blank file that makes the directory a Python package"
    )

def test_strip_markdown_fences_picks_largest_of_multiple_fences():
    text = (
        "For example:\n```python\nx = 1\n```\n\n"
        "But the real content is:\n"
        "```python\ndef add(a, b):\n    return a + b\n```"
    )
    assert DeveloperAgent._strip_markdown_fences(text) == "def add(a, b):\n    return a + b"

@pytest.mark.asyncio
async def test_developer_agent_nested_json_parsing():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    
    # Mock code generation response wrapped in a dictionary object (common in json_mode)
    json_response = """
    {
      "files": [
        {
          "filepath": "math_lib.py",
          "content": "def add(a, b): return a + b"
        }
      ]
    }
    """
    llm.complete = AsyncMock(return_value=json_response)

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation("Goal: Add math lib", "Design specs", "Existing code")

    assert len(files) == 1
    assert files[0]["filepath"] == "math_lib.py"
    assert files[0]["content"] == "def add(a, b): return a + b"

@pytest.mark.asyncio
async def test_run_verifier_judge_goal_explicit_command():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": True,
        "run_command": ["python", "app.py"],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains 'Hello, world!'"
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Run with python app.py and print Hello, world!", design="", files_written=["app.py"])

    assert judgment["should_run"] is True
    assert judgment["run_command"] == ["python", "app.py"]
    assert judgment["command_source"] == "goal_explicit"
    assert "Hello, world!" in judgment["success_criteria"]

@pytest.mark.asyncio
async def test_run_verifier_judge_no_runnable_entrypoint():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": False,
        "run_command": None,
        "command_source": "inferred",
        "success_criteria": ""
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Add a utility library", design="", files_written=["utils.py"])

    assert judgment["should_run"] is False
    assert judgment["run_command"] is None

@pytest.mark.asyncio
async def test_run_verifier_judge_missing_run_command_forces_should_run_false():
    # A model that says should_run=true but omits a usable run_command must not be
    # trusted - there's nothing to actually execute.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": True,
        "run_command": None,
        "command_source": "inferred",
        "success_criteria": "Something"
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Goal", design="", files_written=[])

    assert judgment["should_run"] is False

@pytest.mark.asyncio
async def test_run_verifier_judge_unparseable_response_defaults_to_no_run():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="I cannot comply with this request.")

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Goal", design="", files_written=[])

    assert judgment["should_run"] is False
    assert judgment["run_command"] is None

@pytest.mark.asyncio
async def test_run_verifier_grade_passed():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "passed": True,
        "reasoning": "The output contains the expected [SUCCESS] line."
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    grade = await verifier.grade(
        goal="Print [SUCCESS]", success_criteria="Output contains [SUCCESS]",
        output="[SUCCESS] done", returncode=0
    )

    assert grade["passed"] is True
    assert "SUCCESS" in grade["reasoning"]

@pytest.mark.asyncio
async def test_run_verifier_grade_unparseable_response_defaults_to_failure():
    # A grader response that can't be parsed must fail closed, not silently pass.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="not json at all")

    verifier = RunVerifierAgent("run_verifier", llm)
    grade = await verifier.grade(goal="Goal", success_criteria="Criteria", output="output", returncode=0)

    assert grade["passed"] is False

@pytest.mark.asyncio
async def test_skill_gap_agent_extracts_rules_and_examples():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "rules": ["Use modelVersion 8.0, not the broker-core artifact version."],
        "examples": {"qpid-initial-config.json": '{"modelVersion": "8.0"}'},
        "conflicts": []
    }))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.extract_skill_update(
        reference_text="The default initial-config.json ships with modelVersion 8.0...",
        gap_description="What modelVersion value should the initial config JSON use?",
        existing_rules=[]
    )

    assert result["rules"] == ["Use modelVersion 8.0, not the broker-core artifact version."]
    assert "qpid-initial-config.json" in result["examples"]
    assert result["conflicts"] == []

@pytest.mark.asyncio
async def test_skill_gap_agent_flags_conflicts_instead_of_silently_adding():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "rules": [],
        "examples": {},
        "conflicts": [{
            "candidate_rule": "Use qpid-broker-core 10.0.0.",
            "conflicts_with": "Use qpid-broker-core 9.2.1.",
            "reason": "Different pinned version."
        }]
    }))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.extract_skill_update(
        reference_text="qpid-broker-core 10.0.0 is now the latest release...",
        gap_description="What version of qpid-broker-core should be used?",
        existing_rules=["Use qpid-broker-core 9.2.1."]
    )

    assert result["rules"] == []
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["candidate_rule"] == "Use qpid-broker-core 10.0.0."

@pytest.mark.asyncio
async def test_skill_gap_agent_rule_also_flagged_as_conflict_is_not_double_added():
    # Reproduces a real observed failure: a non-reasoning model correctly flagged a
    # candidate as conflicting AND separately included the exact same text in "rules"
    # in the same response, despite the prompt saying not to. Must not trust prompt
    # adherence alone - enforce mutual exclusivity in code.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "rules": ["The magic widget constant is 42."],
        "examples": {},
        "conflicts": [{
            "candidate_rule": "The magic widget constant is 42.",
            "conflicts_with": "The magic widget constant is 999.",
            "reason": "Different value for the same constant."
        }]
    }))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.extract_skill_update(
        reference_text="The correct magic widget constant is 42, not 999.",
        gap_description="What is the magic widget constant?",
        existing_rules=["The magic widget constant is 999."]
    )

    assert result["rules"] == []
    assert len(result["conflicts"]) == 1

@pytest.mark.asyncio
async def test_skill_gap_agent_irrelevant_reference_returns_empty():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({"rules": [], "examples": {}, "conflicts": []}))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.extract_skill_update(
        reference_text="This page is about an unrelated topic entirely.",
        gap_description="What modelVersion value should be used?",
        existing_rules=[]
    )

    assert result["rules"] == []
    assert result["examples"] == {}

@pytest.mark.asyncio
async def test_skill_gap_agent_unparseable_response_returns_empty_not_error():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="I cannot comply with this request.")

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.extract_skill_update(
        reference_text="Some reference text.", gap_description="Missing info.", existing_rules=[]
    )

    assert result == {"rules": [], "examples": {}, "conflicts": []}
