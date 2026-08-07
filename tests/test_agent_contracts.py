"""Tests for kriya/agents/contracts.py - the Architect file-list schema that
replaces extract_expected_files()'s blanket regex-over-prose as the mainline
way Kriya knows which files a design actually requires.

Fixtures here are deliberately shaped like REAL model output patterns
observed live this session, not clean textbook JSON: trailing chatty prose
after the answer, an illustrative snippet earlier in the design before the
real file list, missing/malformed fences, single-quoted "JSON", and
adversarial-looking paths - not just the happy path.
"""
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import ArchitectAgent
from kriya.agents.contracts import ArchitectFileList, parse_architect_file_list

# ---------------------------------------------------------------------------
# parse_architect_file_list - realistic success shapes
# ---------------------------------------------------------------------------

def test_parses_fenced_json_block_at_end_of_realistic_multi_paragraph_design():
    design = (
        "## Interface Design\n\n"
        "The application starts an embedded Qpid broker and an Ignite node "
        "in the same main() method. Person is a plain record with name and "
        "email fields, serialized to JSON for the JMS message body.\n\n"
        "### Files\n\n"
        "- Person.java: the record\n"
        "- IgniteQpidPersonDemo.java: wires broker startup, JMS send/receive, "
        "and the Ignite cache round-trip\n"
        "- pom.xml: Maven dependencies for Ignite 2.18 and Qpid Broker-J 9.2.1\n\n"
        "```json\n"
        '{"files": ["src/main/java/com/example/Person.java", '
        '"src/main/java/com/example/IgniteQpidPersonDemo.java", "pom.xml"]}\n'
        "```\n"
    )
    files, err = parse_architect_file_list(design)
    assert err is None
    assert files == [
        "src/main/java/com/example/Person.java",
        "src/main/java/com/example/IgniteQpidPersonDemo.java",
        "pom.xml",
    ]

def test_parses_fence_with_no_json_language_tag():
    design = 'Design.\n\n```\n{"files": ["app.py"]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files == ["app.py"]
    assert err is None

def test_ignores_trailing_chatty_prose_after_the_json_block():
    # Real, observed model habit: the answer is correct but the model keeps
    # talking afterward ("Let me know if you need anything else!").
    design = (
        'Design.\n\n```json\n{"files": ["cli.py", "tasks/store.py"]}\n```\n\n'
        "Let me know if you'd like me to add any additional test coverage!"
    )
    files, err = parse_architect_file_list(design)
    assert files == ["cli.py", "tasks/store.py"]
    assert err is None

def test_takes_the_last_json_block_not_an_earlier_illustrative_snippet():
    # Real, observed pattern: a design shows a SAMPLE payload (e.g. the JSON
    # a Qpid message will carry) before the real, authoritative file list at
    # the very end - both are valid, unrelated JSON objects.
    design = (
        "The Person is serialized like this before sending:\n\n"
        "```json\n"
        '{"name": "John Doe", "email": "john@example.com"}\n'
        "```\n\n"
        "Files required:\n\n"
        "```json\n"
        '{"files": ["Person.java", "Demo.java", "pom.xml"]}\n'
        "```\n"
    )
    files, err = parse_architect_file_list(design)
    assert files == ["Person.java", "Demo.java", "pom.xml"]
    assert err is None

def test_finds_bare_unfenced_json_object_as_last_resort():
    design = 'Files needed for this goal:\n\n{"files": ["greet.py"]}\n'
    files, err = parse_architect_file_list(design)
    assert files == ["greet.py"]
    assert err is None


# ---------------------------------------------------------------------------
# parse_architect_file_list - realistic failure shapes (never raises)
# ---------------------------------------------------------------------------

def test_no_json_block_at_all_returns_none_cleanly():
    design = (
        "This design uses a standard Maven layout with a Person record, a "
        "main class wiring Ignite and Qpid together, and a pom.xml with the "
        "required dependencies."
    )
    files, err = parse_architect_file_list(design)
    assert files is None
    assert err is not None
    assert "no json" in err.lower()

def test_single_quoted_pseudo_json_fails_to_parse_cleanly():
    # Real quirk observed from a weaker fallback model this session: valid-
    # LOOKING but not actually valid JSON (single quotes instead of double).
    design = "```json\n{'files': ['app.py']}\n```\n"
    files, err = parse_architect_file_list(design)
    assert files is None
    assert "did not parse" in err

def test_empty_files_list_is_rejected():
    design = '```json\n{"files": []}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert "empty" in err.lower()

def test_missing_files_key_is_rejected():
    design = '```json\n{"notes": "nothing to build"}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert err is not None

def test_empty_design_returns_none_without_raising():
    files, err = parse_architect_file_list("")
    assert files is None
    assert "empty" in err.lower()

def test_none_design_returns_none_without_raising():
    files, err = parse_architect_file_list(None)
    assert files is None
    assert err is not None


# ---------------------------------------------------------------------------
# parse_architect_file_list - adversarial/unsafe path shapes
# ---------------------------------------------------------------------------

def test_rejects_absolute_unix_path():
    design = '```json\n{"files": ["/etc/passwd"]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert "workspace-relative" in err

def test_rejects_absolute_windows_path():
    design = '```json\n{"files": ["C:\\\\Windows\\\\system.ini"]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert "workspace-relative" in err

def test_rejects_path_traversal_forward_slash():
    design = '```json\n{"files": ["../../etc/passwd"]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert "path-traversal" in err

def test_rejects_path_traversal_backslash():
    design = '```json\n{"files": ["..\\\\..\\\\secrets.txt"]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert "path-traversal" in err

def test_rejects_blank_path_entry():
    design = '```json\n{"files": ["app.py", "   "]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert "blank" in err

def test_one_bad_path_rejects_the_whole_list_not_a_partial_result():
    # A partially-valid list is worse than no structured list at all - the
    # caller's fallback path is a known-safe floor; a silently-truncated
    # "mostly right" list is not.
    design = '```json\n{"files": ["Person.java", "../outside.java", "pom.xml"]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files is None
    assert err is not None

def test_valid_relative_paths_with_dots_in_filename_are_not_confused_with_traversal():
    # "..".split("/") membership check must not false-positive on a filename
    # that merely CONTAINS two dots, e.g. a versioned resource file.
    design = '```json\n{"files": ["config/app.v2.1.yaml", "README.md"]}\n```\n'
    files, err = parse_architect_file_list(design)
    assert files == ["config/app.v2.1.yaml", "README.md"]
    assert err is None


# ---------------------------------------------------------------------------
# ArchitectFileList schema directly
# ---------------------------------------------------------------------------

def test_architect_file_list_accepts_plain_valid_shape():
    parsed = ArchitectFileList.model_validate({"files": ["a.py", "b/c.py"]})
    assert parsed.files == ["a.py", "b/c.py"]

def test_architect_file_list_rejects_non_list_files_field():
    with pytest.raises(Exception):
        ArchitectFileList.model_validate({"files": "app.py"})


# ---------------------------------------------------------------------------
# ArchitectAgent.run_with_file_list - single completion, no speculative retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_with_file_list_returns_structured_files_on_valid_response():
    architect = ArchitectAgent("architect", llm_client=None)
    architect.run = AsyncMock(
        return_value='Design.\n\n```json\n{"files": ["cli.py", "tasks/store.py"]}\n```\n'
    )
    design, files = await architect.run_with_file_list("some prompt")
    assert files == ["cli.py", "tasks/store.py"]
    assert "cli.py" in design
    architect.run.assert_awaited_once()

@pytest.mark.asyncio
async def test_run_with_file_list_returns_none_files_on_prose_only_response_no_extra_call():
    # Realistic: an old-style response with no JSON block at all (a design
    # written before this contract existed, or a model that just ignores the
    # instruction). Must NOT trigger any additional completion call - the
    # caller (workflow.py) owns the fallback, not this method.
    architect = ArchitectAgent("architect", llm_client=None)
    architect.run = AsyncMock(return_value="Design: create pom.xml and Main.java.")
    design, files = await architect.run_with_file_list("some prompt")
    assert files is None
    assert design == "Design: create pom.xml and Main.java."
    architect.run.assert_awaited_once()

@pytest.mark.asyncio
async def test_run_with_file_list_returns_none_files_on_empty_list_response():
    architect = ArchitectAgent("architect", llm_client=None)
    architect.run = AsyncMock(return_value='```json\n{"files": []}\n```\n')
    design, files = await architect.run_with_file_list("some prompt")
    assert files is None
    architect.run.assert_awaited_once()
