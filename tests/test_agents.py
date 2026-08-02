import json
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import (
    DeveloperAgent,
    PlannerAgent,
    RunVerifierAgent,
    SkillGapAgent,
    call_with_escalation,
)
from kriya.config import AppConfig, FallbackModelConfig, LLMConfig
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
    llm.complete.assert_called_once_with(
        planner.system_prompt, "Plan a math library", stream_callback=None, json_mode=False,
    )

@pytest.mark.asyncio
async def test_call_with_escalation_no_role_config_preserves_default_call_shape():
    """A None candidate (the common case: a role with no dedicated agent_llms config)
    must call complete() with no override kwargs at all, preserving today's exact
    call shape/behavior for any project that never touches per-role config."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="ok")

    result = await call_with_escalation(llm, "sys", "prompt", [None])

    assert result == "ok"
    llm.complete.assert_called_once_with("sys", "prompt", stream_callback=None, json_mode=False)

@pytest.mark.asyncio
async def test_call_with_escalation_passes_full_candidate_config():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="ok")
    candidate = FallbackModelConfig(
        model="devstral-small-2:24b", base_url="http://localhost:11434/v1", api_key="k",
        temperature=0.5, max_tokens=2048, reasoning=True,
    )

    await call_with_escalation(llm, "sys", "prompt", [candidate], json_mode=True)

    llm.complete.assert_called_once_with(
        "sys", "prompt", stream_callback=None, json_mode=True,
        model_override="devstral-small-2:24b", base_url_override="http://localhost:11434/v1",
        api_key_override="k", temperature_override=0.5, max_tokens_override=2048, reasoning_override=True,
    )

@pytest.mark.asyncio
async def test_call_with_escalation_escalates_on_exception():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    fallback = FallbackModelConfig(model="fallback-model")
    llm.complete = AsyncMock(side_effect=[ConnectionError("primary down"), "recovered"])

    result = await call_with_escalation(llm, "sys", "prompt", [None, fallback])

    assert result == "recovered"
    assert llm.complete.await_count == 2

@pytest.mark.asyncio
async def test_call_with_escalation_reraises_after_exhausting_chain():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    fallback = FallbackModelConfig(model="fallback-model")
    llm.complete = AsyncMock(side_effect=[ConnectionError("primary down"), ConnectionError("fallback down too")])

    with pytest.raises(ConnectionError, match="fallback down too"):
        await call_with_escalation(llm, "sys", "prompt", [None, fallback])

@pytest.mark.asyncio
async def test_call_with_escalation_escalates_on_is_failure_predicate():
    """A candidate that doesn't raise but produces an unusable response (e.g.
    unparseable JSON) must still trigger escalation to the next candidate."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    fallback = FallbackModelConfig(model="fallback-model")
    llm.complete = AsyncMock(side_effect=["not json", '{"ok": true}'])

    result = await call_with_escalation(
        llm, "sys", "prompt", [None, fallback], json_mode=True,
        is_failure=lambda r: r != '{"ok": true}',
    )

    assert result == '{"ok": true}'
    assert llm.complete.await_count == 2

@pytest.mark.asyncio
async def test_call_with_escalation_returns_last_response_when_all_fail_predicate():
    """If every candidate is exhausted and none raised, the last (still-inadequate)
    response is returned rather than raising - callers already handle an empty/bad
    parse gracefully, same as if no escalation existed."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="always bad")

    result = await call_with_escalation(llm, "sys", "prompt", [None], is_failure=lambda r: True)

    assert result == "always bad"

@pytest.mark.asyncio
async def test_agent_run_escalates_through_its_own_role_chain():
    """PlannerAgent (via BaseAgent.run) escalates through its configured role_chain on
    a hard call failure, independent of Developer's own retry loop."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[TimeoutError("primary timed out"), "Plan: recovered"])

    planner = PlannerAgent(
        "planner", llm,
        role_llm=LLMConfig(model="devstral-small-2:24b"),
        role_chain=[FallbackModelConfig(model="qwen3:8b")],
    )
    result = await planner.run("Plan something")

    assert result == "Plan: recovered"
    assert llm.complete.await_count == 2
    first_call_kwargs = llm.complete.await_args_list[0].kwargs
    second_call_kwargs = llm.complete.await_args_list[1].kwargs
    assert first_call_kwargs["model_override"] == "devstral-small-2:24b"
    assert second_call_kwargs["model_override"] == "qwen3:8b"

@pytest.mark.asyncio
async def test_run_verifier_judge_escalates_on_unparseable_json():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    good_response = json.dumps({
        "should_run": True, "run_commands": [["python", "app.py"]],
        "command_source": "goal_explicit", "success_criteria": "Prints done",
    })
    llm.complete = AsyncMock(side_effect=["not json at all", good_response])

    verifier = RunVerifierAgent(
        "run_verifier", llm, role_chain=[FallbackModelConfig(model="fallback-verifier")],
    )
    judgment = await verifier.judge(goal="Goal", design="", files_written=["app.py"])

    assert judgment["should_run"] is True
    assert llm.complete.await_count == 2

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

@pytest.mark.asyncio
async def test_run_generation_with_known_target_files_skips_file_list_call():
    """Regression test for a real bug caught live: a targeted retry already knows
    exactly which files need fixing (extract_implicated_files, deterministic, no
    LLM call) - but run_generation always re-asked the model "what files do you
    think need fixing" via a fresh call regardless, with no enforcement that the
    answer matched what the caller already knew. Confirmed live as the actual
    cause of a targeted retry: the model's own file-list response silently
    dropped the one file that actually needed fixing, which then never got
    revisited, burning the entire retry budget without progress. known_target_files
    must skip that call entirely and generate directly for exactly the given set."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "package com.example;\npublic class App {}",
        "<project>fixed</project>",
    ])

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["src/main/java/com/example/App.java", "pom.xml"],
    )

    # Exactly one call per target file - no extra "what files need fixing" call.
    assert llm.complete.call_count == 2
    files_by_path = {f["filepath"]: f["content"] for f in files}
    assert files_by_path["src/main/java/com/example/App.java"] == "package com.example;\npublic class App {}"
    assert files_by_path["pom.xml"] == "<project>fixed</project>"


@pytest.mark.asyncio
async def test_run_generation_without_known_target_files_still_asks_for_file_list():
    """known_target_files must be strictly opt-in - a normal (non-targeted)
    generation call, which doesn't know the file set in advance, must be
    unaffected and keep asking the model for one as before."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps(
        [{"filepath": "math_lib.py", "content": "def add(a, b): return a + b"}]
    ))

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation("Goal: Add math lib", "Design specs", "Existing code")

    assert llm.complete.call_count == 1
    assert files[0]["filepath"] == "math_lib.py"


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

def test_extract_json_value_direct_parse():
    assert DeveloperAgent._extract_json_value('["a.txt", "b.txt"]') == ["a.txt", "b.txt"]

def test_extract_json_value_recovers_prose_prefixed_array():
    # Reproduces a real observed deepseek-r1 failure: response_format=json_object
    # doesn't stop a reasoning model from explaining itself in prose before finally
    # emitting the JSON - there's no markdown fence here at all, so
    # _strip_markdown_fences alone can't recover it; only bracket-span recovery can.
    text = (
        "To fix the compile error, we need to modify the CacheAndMessagingClient.java "
        "file to include the correct imports for JmsConnectionFactory.\n\n"
        '["pom.xml", "src/main/java/CacheAndMessagingClient.java"]'
    )
    assert DeveloperAgent._extract_json_value(text) == [
        "pom.xml", "src/main/java/CacheAndMessagingClient.java"
    ]

def test_extract_json_value_recovers_prose_prefixed_object():
    text = 'Here is the result you asked for:\n\n{"filepath": "App.java", "content": "class App {}"}'
    assert DeveloperAgent._extract_json_value(text) == {"filepath": "App.java", "content": "class App {}"}

def test_extract_json_value_prefers_array_over_object_when_array_starts_first():
    text = '["a.txt"] is the list, derived from {"note": "context"}'
    assert DeveloperAgent._extract_json_value(text) == ["a.txt"]

def test_extract_json_value_raises_on_pure_prose_with_no_json():
    with pytest.raises(json.JSONDecodeError):
        DeveloperAgent._extract_json_value("I think we should add an import statement here.")

@pytest.mark.asyncio
async def test_run_generation_recovers_prose_prefixed_file_list_without_fallback():
    """The real-world case that used to force the expensive single-stage fallback:
    a reasoning model's file-list response has prose before the JSON array. This
    must now be recovered directly - the single-stage fallback (a much bigger,
    slower call) should never be triggered."""
    cfg = AppConfig()
    llm = LLMClient(cfg)

    prose_prefixed_list = (
        "We need to fix the missing import.\n\n"
        '["src/main/java/App.java"]'
    )
    llm.complete = AsyncMock(side_effect=[
        prose_prefixed_list,
        "package com.example;\npublic class App {}",  # per-file content for App.java
    ])

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation("Task", "Design", "Existing code")

    # Exactly two calls: the file-list call, then one per-file call for App.java -
    # if the prose prefix had forced the single-stage fallback instead, there would
    # be a third, much bigger call (a different system prompt/call shape).
    assert llm.complete.call_count == 2
    assert files == [{"filepath": "src/main/java/App.java", "content": "package com.example;\npublic class App {}"}]

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
        "run_commands": [["python", "app.py"]],
        "command_source": "goal_explicit",
        "success_criteria": "Output contains 'Hello, world!'"
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Run with python app.py and print Hello, world!", design="", files_written=["app.py"])

    assert judgment["should_run"] is True
    assert judgment["run_commands"] == [["python", "app.py"]]
    assert judgment["command_source"] == "goal_explicit"
    assert "Hello, world!" in judgment["success_criteria"]

@pytest.mark.asyncio
async def test_run_verifier_judge_multi_step_sequence():
    """A goal whose correctness can only be observed across multiple invocations
    (add-then-list) must be returned as an ordered list of multiple commands, not
    collapsed into one. Regression test for a real bug caught live: judge() only
    ever inferred ONE no-argument command for exactly this kind of goal, which
    could only ever show a help/usage message - never demonstrating the described
    behavior - and got misread as a code bug across an entire retry budget when
    the generated code was actually correct the whole time."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": True,
        "run_commands": [["python", "cli.py", "add", "Task 1"], ["python", "cli.py", "list"]],
        "command_source": "inferred",
        "success_criteria": "Lists 'Task 1' after adding it"
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Add a task then list tasks", design="", files_written=["cli.py"])

    assert judgment["should_run"] is True
    assert judgment["run_commands"] == [["python", "cli.py", "add", "Task 1"], ["python", "cli.py", "list"]]

@pytest.mark.asyncio
async def test_run_verifier_judge_tolerates_old_single_command_shape():
    # Backward compatibility: a model returning the old flat ["executable", "arg"]
    # shape (instead of a list of commands) must be wrapped into a single-element
    # run_commands list rather than rejected outright.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": True,
        "run_commands": ["python", "app.py"],
        "command_source": "goal_explicit",
        "success_criteria": "Prints done"
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Goal", design="", files_written=["app.py"])

    assert judgment["should_run"] is True
    assert judgment["run_commands"] == [["python", "app.py"]]

@pytest.mark.asyncio
async def test_run_verifier_judge_no_runnable_entrypoint():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": False,
        "run_commands": None,
        "command_source": "inferred",
        "success_criteria": ""
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    judgment = await verifier.judge(goal="Add a utility library", design="", files_written=["utils.py"])

    assert judgment["should_run"] is False
    assert judgment["run_commands"] is None

@pytest.mark.asyncio
async def test_run_verifier_judge_missing_run_commands_forces_should_run_false():
    # A model that says should_run=true but omits usable run_commands must not be
    # trusted - there's nothing to actually execute.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": True,
        "run_commands": None,
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
    assert judgment["run_commands"] is None

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


@pytest.mark.asyncio
async def test_check_skill_conflicts_returns_valid_conflict():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "conflicts": [{
            "rule_a": "Broker must bind AMQP to port 5672.",
            "rule_b": "Configure the broker to listen on port 5673 for AMQP clients.",
            "explanation": "Both skills configure the same embedded broker's AMQP port to different values."
        }]
    }))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.check_skill_conflicts(
        "qpid", ["Broker must bind AMQP to port 5672."],
        "activemq-artemis", ["Configure the broker to listen on port 5673 for AMQP clients."]
    )

    assert len(result) == 1
    assert result[0]["rule_a"] == "Broker must bind AMQP to port 5672."
    assert result[0]["rule_b"] == "Configure the broker to listen on port 5673 for AMQP clients."

@pytest.mark.asyncio
async def test_check_skill_conflicts_discards_hallucinated_rule_text():
    # Defensive check mirroring extract_skill_update's mutual-exclusivity fix: a
    # "conflict" whose rule text doesn't exactly match either skill's actual rules
    # must never be trusted, since it would silently exclude real rule content.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "conflicts": [{
            "rule_a": "Paraphrased version of the real rule.",
            "rule_b": "Use port 5673.",
            "explanation": "Ports differ."
        }]
    }))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.check_skill_conflicts(
        "qpid", ["Broker must bind AMQP to port 5672."],
        "activemq-artemis", ["Use port 5673."]
    )

    assert result == []

@pytest.mark.asyncio
async def test_check_skill_conflicts_no_conflict_returns_empty():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({"conflicts": []}))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.check_skill_conflicts(
        "qpid", ["Use SLF4J for logging."],
        "activemq-artemis", ["Use artemis-server, not artemis-core-server."]
    )

    assert result == []

@pytest.mark.asyncio
async def test_check_skill_conflicts_skips_llm_call_when_either_skill_has_no_rules():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=AssertionError("should not be called"))

    agent = SkillGapAgent("skill_gap", llm)
    result = await agent.check_skill_conflicts("qpid", [], "activemq-artemis", ["Some rule."])

    assert result == []
    llm.complete.assert_not_called()
