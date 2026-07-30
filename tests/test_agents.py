import json
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import DeveloperAgent, PlannerAgent
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
