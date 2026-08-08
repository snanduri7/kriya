import json
from unittest.mock import AsyncMock

import pytest

from kriya.agents.agent import (
    ArchitectAgent,
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
async def test_run_verifier_judge_includes_pom_content_when_given():
    """Regression test for a real bug found via two live golden-use-case runs
    (Ignite, then Qpid): the system prompt already tells the judge exactly how
    to read a pom.xml's exec-maven-plugin shape (exec:exec vs exec:java), but
    judge() never actually included the pom.xml's content in its own prompt -
    only the Architect's own already-minimized design (often just a bare file
    list) and file paths, neither of which carry that detail. Confirmed live,
    twice: an exec:exec-shaped pom (needed for --add-opens JVM flags) was
    still judged as exec:java both times. The instruction was correct; the
    data to apply it against was missing."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": True, "run_commands": [["mvn", "exec:exec"]],
        "command_source": "inferred", "success_criteria": "Prints the result",
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    pom_content = "<project><build><plugins><plugin><artifactId>exec-maven-plugin</artifactId></plugin></plugins></build></project>"
    await verifier.judge(goal="Goal", design="## Files to Create\n- pom.xml", files_written=["pom.xml"], build_file_content=pom_content)

    sent_prompt = llm.complete.await_args.args[1]
    assert pom_content in sent_prompt
    assert "Actual pom.xml content" in sent_prompt


@pytest.mark.asyncio
async def test_run_verifier_judge_omits_pom_section_when_not_given():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "should_run": False, "run_commands": None,
        "command_source": "inferred", "success_criteria": "",
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    await verifier.judge(goal="Goal", design="", files_written=["app.py"])

    sent_prompt = llm.complete.await_args.args[1]
    assert "Actual pom.xml content" not in sent_prompt

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


def test_split_fix_analysis_extracts_marker_and_strips_content():
    text = (
        "FIX ANALYSIS: The cache is used as a raw type so .get() returns Object; "
        "adding explicit generics fixes it.\n"
        "FILE CONTENT:\n"
        "public class App {}"
    )
    analysis, content = DeveloperAgent._split_fix_analysis(text)
    assert analysis == "The cache is used as a raw type so .get() returns Object; adding explicit generics fixes it."
    assert content == "public class App {}"

def test_split_fix_analysis_is_case_insensitive():
    text = "fix analysis: short reason\nfile content:\nclass X {}"
    analysis, content = DeveloperAgent._split_fix_analysis(text)
    assert analysis == "short reason"
    assert content == "class X {}"

def test_split_fix_analysis_falls_back_when_marker_missing():
    # A non-compliant response (no marker at all) must degrade to the plain
    # pre-existing behavior - the whole text treated as content, not corrupted
    # or silently dropped.
    text = "public class App {}"
    analysis, content = DeveloperAgent._split_fix_analysis(text)
    assert analysis is None
    assert content == text

@pytest.mark.asyncio
async def test_fill_missing_content_adds_fix_analysis_instruction_only_with_prior_error():
    """Regression test for a real, generalizable bug found live during golden-
    use-case validation: single-shot, non-reasoning completion regenerated
    byte-for-byte identical broken code across 7 straight retry attempts of a
    real failing run, despite the exact compile error being present in every
    prompt - the model was never actually forced to engage with the stated
    error before writing code. The mandatory FIX ANALYSIS step must only be
    requested (and only stripped from saved content) when there's a real
    prior error to analyze - never on a clean first attempt."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(
        return_value="FIX ANALYSIS: raw type bug\nFILE CONTENT:\npublic class App {}"
    )

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["src/main/java/com/example/App.java"],
        prior_error_context="incompatible types: java.lang.Object cannot be converted to java.lang.String",
    )

    file_prompt = llm.complete.call_args_list[0][0][1]
    assert "FIX ANALYSIS" in file_prompt
    assert "RETRY" in file_prompt
    # The analysis preamble must be stripped out of the saved file content.
    assert files[0]["content"] == "public class App {}"
    assert "FIX ANALYSIS" not in files[0]["content"]

@pytest.mark.asyncio
async def test_fill_missing_content_no_fix_analysis_instruction_without_prior_error():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="public class App {}")

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["src/main/java/com/example/App.java"],
    )

    file_prompt = llm.complete.call_args_list[0][0][1]
    assert "FIX ANALYSIS" not in file_prompt
    assert files[0]["content"] == "public class App {}"

@pytest.mark.asyncio
async def test_fill_missing_content_scopes_fix_analysis_to_implicated_files_only():
    """Regression test for a real bug found live during golden-use-case
    validation: a full-set retry regenerates every file in the batch, and
    without scoping, EVERY file's per-file prompt got the same "explain the
    fix" instruction even though the compile error only ever implicated ONE
    of them - producing confused/wrong analyses for unrelated files (a real
    run blamed a perfectly fine Person.java for a bug that was actually a
    raw-type cache access mistake in a completely different file) and
    diluting the one analysis call that actually mattered. Only the
    implicated file's prompt should carry the fix-analysis instruction and
    error_source_context; an unrelated file in the same batch must be asked
    to regenerate normally, as if it were a clean attempt."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    file_list_response = json.dumps([
        {"filepath": "Broken.java"},
        {"filepath": "Unrelated.java"},
    ])
    llm.complete = AsyncMock(side_effect=[
        file_list_response,
        "FIX ANALYSIS: fixed it\nFILE CONTENT:\nclass Broken {}",
        "class Unrelated {}",
    ])

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation(
        "Task", "Design", "Existing code",
        prior_error_context="incompatible types at Broken.java:[10,5]",
        implicated_files=["Broken.java"],
        error_source_context={"Broken.java": "\n>> 10: bad line\n"},
    )

    broken_prompt = llm.complete.call_args_list[1][0][1]
    unrelated_prompt = llm.complete.call_args_list[2][0][1]

    assert "FIX ANALYSIS" in broken_prompt
    assert "bad line" in broken_prompt
    assert "FIX ANALYSIS" not in unrelated_prompt
    assert "bad line" not in unrelated_prompt

    files_by_path = {f["filepath"]: f["content"] for f in files}
    assert files_by_path["Broken.java"] == "class Broken {}"
    assert files_by_path["Unrelated.java"] == "class Unrelated {}"

def test_split_fix_analysis_edit_extracts_search_replace():
    text = (
        "FIX ANALYSIS: Person needs to implement Serializable for ObjectMessage.\n"
        "SEARCH:\n"
        "public class Person {\n"
        "REPLACE:\n"
        "public class Person implements java.io.Serializable {"
    )
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert analysis == "Person needs to implement Serializable for ObjectMessage."
    assert edits == [{
        "search": "public class Person {",
        "replace": "public class Person implements java.io.Serializable {",
    }]
    assert content is None

def test_split_fix_analysis_edit_falls_back_to_file_content_when_no_markers():
    text = "FIX ANALYSIS: broader change needed.\nFILE CONTENT:\npublic class App {}"
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert analysis == "broader change needed."
    assert edits is None
    assert content == "public class App {}"

def test_split_fix_analysis_edit_falls_back_when_no_markers_at_all():
    text = "public class App {}"
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert analysis is None
    assert edits is None
    assert content == text

def test_split_fix_analysis_edit_truncates_redundant_trailing_file_content():
    """Regression test for a real bug found live, 2026-08-04: a model asked
    to prefer an anchored edit sometimes ALSO appends a redundant, unasked-
    for FILE CONTENT: block after its SEARCH/REPLACE - without truncating
    replace_block there, the entire redundant full-file content (plus the
    literal "FILE CONTENT:" marker text) got swallowed into the applied
    patch. Confirmed live: this exact shape (a correct 3-line import fix,
    plus a redundant trailing FILE CONTENT: block) corrupted a real file
    with a duplicated package/class declaration, producing "class,
    interface, enum, or record expected" - not a model mistake, since the
    SEARCH/REPLACE portion alone was entirely correct."""
    text = (
        "FIX ANALYSIS: IgniteCache is imported from the wrong package.\n"
        "SEARCH:\n"
        "import org.apache.ignite.cache.IgniteCache;\n"
        "REPLACE:\n"
        "import org.apache.ignite.IgniteCache;\n"
        "\n"
        "FILE CONTENT:\n"
        "package com.example;\n"
        "import org.apache.ignite.IgniteCache;\n"
        "public class App { /* redundant full regeneration the model wasn't asked for */ }"
    )
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert edits == [{
        "search": "import org.apache.ignite.cache.IgniteCache;",
        "replace": "import org.apache.ignite.IgniteCache;",
    }]
    assert content is None

def test_split_fix_analysis_edit_strips_copied_error_source_gutter():
    """Regression test for a real bug found live, 2026-08-04: a model shown
    _build_error_source_context()'s own display format (">> N: <line>" for
    the reported error line) copied that gutter directly into its SEARCH
    block instead of the bare source line - confirmed live, the real SEARCH
    text was literally ">> import org.apache.ignite.cache.IgniteCache;"
    (kept the ">>" marker, dropped the line number). That can never match
    the real file's plain "import ...;" line, guaranteeing "Anchor matching
    failed... matched 0 times" regardless of whether the model's intended
    fix was otherwise correct - a real, self-inflicted retry-budget waste,
    not a model reasoning failure."""
    text = (
        "FIX ANALYSIS: wrong package for IgniteCache.\n"
        "SEARCH:\n"
        "```java\n"
        "import org.apache.ignite.Ignite;\n"
        "import org.apache.ignite.Ignition;\n"
        ">> import org.apache.ignite.cache.IgniteCache;\n"
        "import org.slf4j.Logger;\n"
        "```\n"
        "REPLACE:\n"
        "```java\n"
        "import org.apache.ignite.Ignite;\n"
        "import org.apache.ignite.Ignition;\n"
        "import org.apache.ignite.IgniteCache;\n"
        "import org.slf4j.Logger;\n"
        "```"
    )
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert edits == [{
        "search": (
            "import org.apache.ignite.Ignite;\n"
            "import org.apache.ignite.Ignition;\n"
            "import org.apache.ignite.cache.IgniteCache;\n"
            "import org.slf4j.Logger;"
        ),
        "replace": (
            "import org.apache.ignite.Ignite;\n"
            "import org.apache.ignite.Ignition;\n"
            "import org.apache.ignite.IgniteCache;\n"
            "import org.slf4j.Logger;"
        ),
    }]

def test_split_fix_analysis_edit_strips_gutter_with_line_number_preserved():
    # The other real gutter shape (surrounding, non-highlighted context
    # lines): "  N: <line>", also emitted by _build_error_source_context.
    text = (
        "SEARCH:\n"
        "  9: import org.apache.ignite.Ignite;\n"
        ">> 10: import org.apache.ignite.cache.IgniteCache;\n"
        "REPLACE:\n"
        "import org.apache.ignite.Ignite;\n"
        "import org.apache.ignite.IgniteCache;"
    )
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert edits[0]["search"] == (
        "import org.apache.ignite.Ignite;\n"
        "import org.apache.ignite.cache.IgniteCache;"
    )

def test_split_fix_analysis_edit_strips_the_real_three_space_gutter_format(tmp_path):
    """Regression test for a real bug found live, 2026-08-07
    (kriya-protocol-parser-app), diagnosed directly from
    Failure.attempted_edits once that started being persisted:
    _build_error_source_context()'s actual non-highlighted gutter format is
    THREE leading spaces ("   N: "), not two - the format string is
    f"{'>>' if ... else '  '} {i+1}: ...", so the two-space placeholder plus
    the f-string's own literal separator space adds up to three. The gutter
    regex only ever matched an exact two-space prefix, so a real SEARCH
    block that copied this exact format verbatim went unstripped,
    guaranteeing "matched 0 times" regardless of whether the model's
    intended edit was otherwise correct. Generates the gutter via the REAL
    _build_error_source_context() (not a hand-typed guess at its format,
    which is exactly how the original 2-vs-3-space mismatch went unnoticed)
    so this test breaks immediately if the two ever drift apart again."""
    from kriya.workflow.workflow import _build_error_source_context

    (tmp_path / "Calc.java").write_text(
        "\n".join(f"line {i}" for i in range(1, 10))
    )
    error = "at com.example.Calc.divide(Calc.java:5)"
    context = _build_error_source_context(str(tmp_path), error, known_files=["Calc.java"])
    real_gutter_snippet = context["Calc.java"].strip()
    assert real_gutter_snippet.startswith("=== Source context")
    # Pull just the gutter-formatted lines (skip the header line above) to
    # use as a real SEARCH block, exactly as a model copying them verbatim
    # would produce.
    gutter_lines = "\n".join(real_gutter_snippet.splitlines()[1:])
    assert "   4: line 4" in gutter_lines  # confirms the real format IS 3 spaces, not 2

    text = f"SEARCH:\n{gutter_lines}\nREPLACE:\nreplacement"
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert edits is not None
    search_block = edits[0]["search"]
    for line_no in range(2, 9):
        assert f"line {line_no}" in search_block
    assert "   " not in search_block  # no unstripped 3-space gutter survives
    assert ">>" not in search_block   # the highlighted-line marker is also gone

def test_split_fix_analysis_edit_does_not_corrupt_ordinary_indented_code():
    # Must NOT strip legitimate 2-space (or deeper) indentation on real code
    # that has no line-number gutter - only the exact ">>"/"  N:" shapes
    # Kriya itself emits are stripped.
    text = (
        "SEARCH:\n"
        "public class App {\n"
        "  public static void main(String[] args) {\n"
        "REPLACE:\n"
        "public class App implements Serializable {\n"
        "  public static void main(String[] args) {"
    )
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert edits[0]["search"] == (
        "public class App {\n"
        "  public static void main(String[] args) {"
    )

def test_split_fix_analysis_edit_parses_multiple_search_replace_pairs():
    """Regression test for a real bug found live, 2026-08-07
    (ignite_qpid_person): despite the prompt saying "include only the lines
    that actually need to change" (singular), a real response returned THREE
    separate SEARCH/REPLACE pairs for one file, plus a trailing FILE CONTENT:
    block it wasn't asked for either. The old implementation only recognized
    the FIRST search:/replace: pair and took everything up to FILE CONTENT:
    as one giant replace_block - swallowing pairs 2 and 3 (markers and all)
    into pair 1's own replacement text. apply_anchored_edits() already
    applies a LIST of edits in sequence, so the fix is to actually return all
    three as separate edits instead of corrupting the first one with the
    other two's raw text."""
    text = (
        "FIX ANALYSIS: Multiple related fixes needed across this file.\n"
        "SEARCH:\n"
        "Ignite ignite = (Ignite) context.getBean(\"igniteNode\");\n"
        "REPLACE:\n"
        "Ignite ignite = (Ignite) context.getBean(\"igniteNode\");\n"
        "SEARCH:\n"
        "ConnectionFactory factory = (ConnectionFactory) context.getBean(\"qpidConnectionFactory\");\n"
        "REPLACE:\n"
        "ConnectionFactory factory = (ConnectionFactory) context.getBean(\"qpidFactory\");\n"
        "SEARCH:\n"
        "IgniteCache<String, Person> cache = ignite.getOrCreateCache(\"person-cache\");\n"
        "REPLACE:\n"
        "IgniteCache<String, Person> cache = ignite.getOrCreateCache(\"people-cache\");\n"
        "\n"
        "FILE CONTENT:\n"
        "package com.example;\npublic class App { /* redundant, unasked-for full file */ }"
    )
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert content is None
    assert edits == [
        {
            "search": 'Ignite ignite = (Ignite) context.getBean("igniteNode");',
            "replace": 'Ignite ignite = (Ignite) context.getBean("igniteNode");',
        },
        {
            "search": 'ConnectionFactory factory = (ConnectionFactory) context.getBean("qpidConnectionFactory");',
            "replace": 'ConnectionFactory factory = (ConnectionFactory) context.getBean("qpidFactory");',
        },
        {
            "search": 'IgniteCache<String, Person> cache = ignite.getOrCreateCache("person-cache");',
            "replace": 'IgniteCache<String, Person> cache = ignite.getOrCreateCache("people-cache");',
        },
    ]
    # No stray marker text leaked into any replace block - the exact
    # corruption the real live failure produced.
    for edit in edits:
        assert "SEARCH:" not in edit["replace"]
        assert "REPLACE:" not in edit["replace"]

def test_split_fix_analysis_edit_stops_at_trailing_search_with_no_replace():
    # A malformed sequence (a dangling SEARCH with no REPLACE after it)
    # degrades to whatever complete pairs were found, rather than raising or
    # misparsing the dangling block as part of an earlier pair.
    text = (
        "SEARCH:\nfoo();\n"
        "REPLACE:\nbar();\n"
        "SEARCH:\nincomplete, no replace follows"
    )
    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(text)
    assert edits == [{"search": "foo();", "replace": "bar();"}]

def test_build_incompatible_types_scaffold_names_the_reported_types():
    """Regression test for a real bug found live, 2026-08-07
    (ignite_qpid_person): the model's own FIX ANALYSIS correctly said
    "properly handle the generic types" but the actual diff only renamed a
    method call, never touching the reported line - the identical
    "incompatible types: java.lang.Object cannot be converted to
    com.example.Person" error recurred verbatim on the next attempt. Real
    captured error text (two identical lines, as javac repeats itself across
    build phases) - dedup must collapse them to one described pair."""
    error = (
        "[ERROR] /Users/.../PersonApp.java:[111,38] incompatible types: "
        "java.lang.Object cannot be converted to com.example.Person\n"
        "[ERROR] /Users/.../PersonApp.java:[111,38] incompatible types: "
        "java.lang.Object cannot be converted to com.example.Person\n"
    )
    scaffold = DeveloperAgent._build_incompatible_types_scaffold(error)
    assert '"java.lang.Object" cannot be converted to "com.example.Person"' in scaffold
    assert scaffold.count('cannot be converted to "com.example.Person"') == 1  # deduped
    assert "explicit cast" in scaffold
    assert "generic type parameters" in scaffold

def test_build_incompatible_types_scaffold_empty_when_no_match():
    assert DeveloperAgent._build_incompatible_types_scaffold(None) == ""
    assert DeveloperAgent._build_incompatible_types_scaffold("cannot find symbol: class Foo") == ""

@pytest.mark.asyncio
async def test_fill_missing_content_prompt_includes_incompatible_types_scaffold():
    """Integration check: when prior_error_context carries the javac
    'incompatible types' shape, the actual prompt sent to the model (not just
    the standalone builder) includes the scaffold - confirms it's really
    wired into _fill_missing_content, not just unit-tested in isolation."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=(
        "FIX ANALYSIS: fixed\nSEARCH:\nfoo();\nREPLACE:\nbar();"
    ))
    dev = DeveloperAgent("developer", llm)
    await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["PersonApp.java"],
        prior_error_context=(
            "PersonApp.java:[111,38] incompatible types: java.lang.Object "
            "cannot be converted to com.example.Person"
        ),
        error_source_context={"PersonApp.java": "\n>> 111: Person p = cache.get(1);\n"},
    )
    file_prompt = llm.complete.call_args_list[0][0][1]
    assert '"java.lang.Object" cannot be converted to "com.example.Person"' in file_prompt
    assert "explicit cast" in file_prompt

def test_build_buffer_capacity_scaffold_names_overflow_direction():
    """Regression test for a real bug found live, 2026-08-08
    (ignite_qpid_protocol): the model's own FIX ANALYSIS correctly diagnosed
    "the dataLength field ... is being written as a 4-byte int but only the
    first 3 bytes are meaningful" but the produced diff never actually changed
    the reported line (line 23) - the identical BufferOverflowException
    recurred. Real captured stack trace from that exact run."""
    error = (
        'Exception in thread "main" java.nio.BufferOverflowException\n'
        "\tat java.base/java.nio.HeapByteBuffer.put(HeapByteBuffer.java:231)\n"
        "\tat java.base/java.nio.ByteBuffer.put(ByteBuffer.java:1210)\n"
        "\tat com.example.ProtocolParser.encode(ProtocolParser.java:23)\n"
        "\tat com.example.ProtocolApp.main(ProtocolApp.java:40)\n"
    )
    scaffold = DeveloperAgent._build_buffer_capacity_scaffold(error)
    assert "BufferOverflowException" in scaffold
    assert "writing (put)" in scaffold
    assert "non-standard width" in scaffold
    assert "allocated total size" in scaffold

def test_build_buffer_capacity_scaffold_names_underflow_direction():
    """The sibling exception (BufferUnderflowException, the read-path version of
    the same root cause) was also found live, independently, 2026-08-07
    (kriya-protocol-parser-app's hand-rolled protocol decode())."""
    error = (
        'Exception in thread "main" java.nio.BufferUnderflowException\n'
        "\tat java.base/java.nio.Buffer.nextGetIndex(Buffer.java:640)\n"
        "\tat com.example.ProtocolParser.decode(ProtocolParser.java:45)\n"
    )
    scaffold = DeveloperAgent._build_buffer_capacity_scaffold(error)
    assert "BufferUnderflowException" in scaffold
    assert "reading (get)" in scaffold

def test_build_buffer_capacity_scaffold_empty_when_no_match():
    assert DeveloperAgent._build_buffer_capacity_scaffold(None) == ""
    assert DeveloperAgent._build_buffer_capacity_scaffold("cannot find symbol: class Foo") == ""

@pytest.mark.asyncio
async def test_fill_missing_content_prompt_includes_buffer_capacity_scaffold():
    """Integration check: when prior_error_context carries a
    java.nio.BufferOverflowException, the actual prompt sent to the model
    includes the scaffold - confirms it's wired into _fill_missing_content,
    not just unit-tested in isolation."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=(
        "FIX ANALYSIS: fixed\nSEARCH:\nfoo();\nREPLACE:\nbar();"
    ))
    dev = DeveloperAgent("developer", llm)
    await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["ProtocolParser.java"],
        prior_error_context=(
            'Exception in thread "main" java.nio.BufferOverflowException\n'
            "\tat com.example.ProtocolParser.encode(ProtocolParser.java:23)\n"
        ),
        error_source_context={"ProtocolParser.java": "\n>> 23: buffer.putInt(dataLength);\n"},
    )
    file_prompt = llm.complete.call_args_list[0][0][1]
    assert "BufferOverflowException" in file_prompt
    assert "bit-shifting" in file_prompt

def test_sanitize_generated_content_none_passthrough():
    assert DeveloperAgent.sanitize_generated_content(None) is None

def test_sanitize_generated_content_strips_gutter_and_fence():
    text = (
        "```java\n"
        ">> 3: import org.apache.ignite.cache.IgniteCache;\n"
        "  4: public class App {\n"
        "```"
    )
    assert DeveloperAgent.sanitize_generated_content(text) == (
        "import org.apache.ignite.cache.IgniteCache;\npublic class App {"
    )

def test_sanitize_generated_content_truncates_redundant_trailing_marker():
    text = "public class App {}\n\nFILE CONTENT:\npublic class App { /* duplicated */ }"
    assert DeveloperAgent.sanitize_generated_content(text) == "public class App {}"

def test_sanitize_generated_content_plain_text_passthrough():
    # No gutter, no fence, no marker - must not be altered at all.
    text = "public class App {\n    public static void main(String[] args) {}\n}"
    assert DeveloperAgent.sanitize_generated_content(text) == text

@pytest.mark.asyncio
async def test_fill_missing_content_full_content_retry_strips_copied_gutter():
    """Regression test: unlike the anchored-edit SEARCH/REPLACE path (already
    covered in test_split_fix_analysis_edit_strips_copied_error_source_gutter),
    a full FILE CONTENT: retry response is shown the exact same gutter-
    formatted error_source_context but, before sanitize_generated_content was
    wired into _fill_missing_content's non-anchored branch, only ever had
    markdown fences stripped - a model that echoed the gutter back into a
    full-file response (not just a SEARCH block) would have written it
    straight to disk uncorrected."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=(
        "FIX ANALYSIS: wrong import package.\n"
        "FILE CONTENT:\n"
        ">> 1: import org.apache.ignite.cache.IgniteCache;\n"
        "  2: public class App {}\n"
    ))
    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["App.java"],
        prior_error_context="cannot find symbol",
        # No error_source_context entry for this file - keeps prefer_anchored_edit
        # False so this exercises the non-anchored FILE CONTENT: branch, not the
        # SEARCH/REPLACE one already covered by _split_fix_analysis_edit's tests.
        error_source_context=None,
    )
    assert files[0]["content"] == (
        "import org.apache.ignite.cache.IgniteCache;\npublic class App {}"
    )

@pytest.mark.asyncio
async def test_fill_missing_content_prefers_anchored_edit_when_source_context_known():
    """A precise source location (error_source_context has a real snippet for
    this file) should make the prompt prefer a small SEARCH:/REPLACE: patch
    over full-file regeneration, and a compliant response should come back as
    edits, not content. Motivated by a real, distinct failure mode: a full
    regeneration correctly self-diagnosed a one-line fix (Person needing
    `implements Serializable`) in its own FIX ANALYSIS text, then still
    emitted the class without it - the stated intention got lost somewhere
    across rewriting the whole file. A small anchored edit has no unrelated
    content for that to happen inside."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(
        return_value=(
            "FIX ANALYSIS: Person needs Serializable.\n"
            "SEARCH:\npublic class Person {\nREPLACE:\npublic class Person implements java.io.Serializable {"
        )
    )

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["Person.java"],
        prior_error_context="incompatible types at Person.java:[5,1]",
        error_source_context={"Person.java": "\n>> 5: public class Person {\n"},
    )

    file_prompt = llm.complete.call_args_list[0][0][1]
    assert "SEARCH:" in file_prompt
    assert "REPLACE:" in file_prompt
    assert files[0]["content"] is None
    assert files[0]["edits"] == [{
        "search": "public class Person {",
        "replace": "public class Person implements java.io.Serializable {",
    }]

@pytest.mark.asyncio
async def test_fill_missing_content_no_anchored_edit_preference_without_source_context():
    """Without a known source location (error_source_context has no entry for
    this file), the prompt must stay on the plain FILE CONTENT: instruction -
    an anchored edit isn't well-grounded without knowing where to anchor it."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="FIX ANALYSIS: fixed\nFILE CONTENT:\nclass App {}")

    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["App.java"],
        prior_error_context="some error with no location",
    )

    file_prompt = llm.complete.call_args_list[0][0][1]
    assert "SEARCH:" not in file_prompt
    assert "FILE CONTENT:" in file_prompt
    assert files[0]["content"] == "class App {}"

@pytest.mark.asyncio
async def test_fill_missing_content_applies_retry_temperature_only_to_implicated_file():
    """retry_temperature (LLMConfig.retry_temperature) must only override the
    completion temperature for a file the fix-analysis instruction actually
    applies to - never a clean first attempt, never an unrelated file in the
    same full-set batch. Real, cited motivation: code-gen success rate was
    found to drop as temperature rises even within Kriya's own low default
    range, so a retry benefits from going lower, not higher - but only the
    file actually being fixed should pay that (opt-in) behavior change."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    file_list_response = json.dumps([
        {"filepath": "Broken.java"},
        {"filepath": "Unrelated.java"},
    ])
    llm.complete = AsyncMock(side_effect=[
        file_list_response,
        "FIX ANALYSIS: fixed it\nFILE CONTENT:\nclass Broken {}",
        "class Unrelated {}",
    ])

    dev = DeveloperAgent("developer", llm)
    await dev.run_generation(
        "Task", "Design", "Existing code",
        prior_error_context="incompatible types at Broken.java:[10,5]",
        implicated_files=["Broken.java"],
        retry_temperature=0.05,
    )

    broken_kwargs = llm.complete.call_args_list[1].kwargs
    unrelated_kwargs = llm.complete.call_args_list[2].kwargs
    assert broken_kwargs["temperature_override"] == 0.05
    assert unrelated_kwargs["temperature_override"] is None

@pytest.mark.asyncio
async def test_fill_missing_content_no_retry_temperature_on_clean_first_attempt():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value="class App {}")

    dev = DeveloperAgent("developer", llm)
    await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["App.java"],
        retry_temperature=0.05,
    )

    assert llm.complete.call_args_list[0].kwargs["temperature_override"] is None

@pytest.mark.asyncio
async def test_fill_missing_content_implicated_files_none_applies_to_all():
    """implicated_files=None (the default, and what a targeted retry passes,
    where every file in the batch already IS implicated by construction) must
    preserve the pre-existing behavior: apply the fix-analysis instruction to
    every file needing content, not just a subset."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "FIX ANALYSIS: fixed a\nFILE CONTENT:\nclass A {}",
        "FIX ANALYSIS: fixed b\nFILE CONTENT:\nclass B {}",
    ])

    dev = DeveloperAgent("developer", llm)
    await dev.run_generation(
        "Task", "Design", "Existing code",
        known_target_files=["A.java", "B.java"],
        prior_error_context="some error",
    )

    for call in llm.complete.call_args_list:
        assert "FIX ANALYSIS" in call[0][1]

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
    assert grade["likely_files"] == []

@pytest.mark.asyncio
async def test_run_verifier_grade_returns_likely_files_on_failure():
    """A runtime failure's captured output structurally never names a .java
    file the way a compile error does - the grader naming the responsible
    file directly is what lets the retry loop scope a fix instead of
    retrying blind against every file."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "passed": False,
        "reasoning": "The Person was never found in the cache.",
        "likely_files": ["src/main/java/com/example/CombinedApplication.java"],
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    grade = await verifier.grade(
        goal="Cache a Person", success_criteria="Person is cached and logged",
        output="No Person found in cache", returncode=0,
        files_written=["src/main/java/com/example/CombinedApplication.java", "src/main/java/com/example/Person.java"],
    )

    assert grade["passed"] is False
    assert grade["likely_files"] == ["src/main/java/com/example/CombinedApplication.java"]

@pytest.mark.asyncio
async def test_run_verifier_grade_filters_out_hallucinated_likely_files():
    # Trust boundary: never let the grader point the retry loop at a file
    # that was never actually generated for this goal.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "passed": False,
        "reasoning": "Something failed.",
        "likely_files": ["src/main/java/com/example/CombinedApplication.java", "NotARealFile.java"],
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    grade = await verifier.grade(
        goal="Goal", success_criteria="Criteria", output="output", returncode=0,
        files_written=["src/main/java/com/example/CombinedApplication.java"],
    )

    assert grade["likely_files"] == ["src/main/java/com/example/CombinedApplication.java"]

@pytest.mark.asyncio
async def test_run_verifier_grade_timed_out_adds_prompt_note():
    """timed_out=True must tell the grader not to treat the forced-kill exit
    code/output as evidence of failure on its own - the caller (workflow.py)
    still treats a timeout as disqualifying regardless of grade()'s verdict,
    but grade() itself must judge purely on whether the goal's described
    output is genuinely present in what was captured before the kill."""
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "passed": True,
        "reasoning": "The [SUCCESS] line is present despite the forced kill.",
    }))

    verifier = RunVerifierAgent("run_verifier", llm)
    grade = await verifier.grade(
        goal="Print [SUCCESS]", success_criteria="Output contains [SUCCESS]",
        output="[SUCCESS] done\n", returncode=-1, timed_out=True,
    )

    assert grade["passed"] is True
    prompt = llm.complete.call_args_list[0][0][1]
    assert "forcibly killed" in prompt
    assert "Do NOT treat the exit code or the kill itself as evidence of failure" in prompt

@pytest.mark.asyncio
async def test_run_verifier_grade_no_timeout_note_by_default():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({"passed": True, "reasoning": "ok"}))

    verifier = RunVerifierAgent("run_verifier", llm)
    await verifier.grade(goal="Goal", success_criteria="Criteria", output="output", returncode=0)

    prompt = llm.complete.call_args_list[0][0][1]
    assert "forcibly killed" not in prompt

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
            "rule_a_index": 1,
            "rule_b_index": 1,
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
async def test_check_skill_conflicts_discards_out_of_range_index():
    # Defensive check mirroring extract_skill_update's mutual-exclusivity fix: a
    # "conflict" whose index doesn't resolve to a real position in either skill's
    # actual rule list must never be trusted, since it would silently exclude
    # real rule content. Index-based referencing (not verbatim text) is itself
    # the fix for a real efficiency bug found live: asking the model to
    # reproduce rule text character-for-character caused a near-100% discard
    # rate (up to 28 discarded "conflicts" from a single call) even when the
    # model's underlying judgment may have been reasonable - an index is either
    # a valid position or it isn't, no "almost right" case to fail on.
    cfg = AppConfig()
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(return_value=json.dumps({
        "conflicts": [{
            "rule_a_index": 5,  # out of range - skill_a only has 1 rule
            "rule_b_index": 1,
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


def test_architect_prompt_requires_listing_files_that_need_modification_too():
    """Regression test for a real bug found via a live golden-use-case run
    (M2, Qpid-extends-Ignite): the Architect's prompt only ever said
    "## Files to Create", and DeveloperAgent's own prompt treats that list
    as exhaustive ("implement ALL files... do not omit any"). When extending
    an existing project, a file that needs a real change but already exists
    (e.g. adding a new dependency to the existing pom.xml) was never listed,
    so the Developer never touched it - confirmed live: a new class
    referencing Qpid/JMS classes compiled against a pom.xml that still only
    had Ignite dependencies, since pom.xml was never in the Architect's
    "Files to Create" list. The prompt must explicitly cover modifying
    existing files, not just creating new ones.

    The original fix used a markdown heading ("## Files to Create or
    Modify") later replaced by a validated JSON contract (see
    kriya/agents/contracts.py, ArchitectAgent.run_with_file_list) - this
    test now checks for the JSON shape instead of the old heading text,
    same regression intent."""
    prompt = ArchitectAgent("architect", None).system_prompt
    assert '"files"' in prompt
    assert "already-existing" in prompt.lower() or "existing files" in prompt.lower()

def test_architect_agent_requires_explicit_build_manifest():
    """Regression test for a real bug found live (2026-08-07,
    kriya-protocol-parser-app): a goal saying 'In a Maven project...' never
    explicitly asked for pom.xml, the Architect's design never listed it
    either, and no attempt across an entire generation run ever created
    one - every retry failed on the same missing-dependency compile errors
    since nothing in the retry loop could recover a file that was never
    requested in the first place (see _detect_missing_build_manifest's
    structural fix for the other half of this)."""
    prompt = ArchitectAgent("architect", None).system_prompt
    assert "pom.xml" in prompt
    assert "build.gradle" in prompt
    assert "not implicit" in prompt.lower()
