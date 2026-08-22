import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.workflow.self_correction import run_self_correction_loop


def _write(worktree_path, filepath, content):
    full = os.path.join(worktree_path, filepath)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)


@pytest.mark.asyncio
async def test_self_correction_resolves_within_budget(tmp_path):
    worktree_path = str(tmp_path)
    _write(worktree_path, "a.py", "def foo():\n    return 1\n")

    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {
            "content": "",
            "tool_calls": [{
                "id": "1", "name": "apply_patch",
                "arguments": {"filepath": "a.py", "edits": [{"search": "return 1", "replace": "return 2"}]},
            }],
        },
        {"content": "", "tool_calls": [{"id": "2", "name": "recompile", "arguments": {}}]},
    ])

    validator = MagicMock()
    validator.run_compile_check = MagicMock(return_value={"success": True, "output": "compiled OK"})

    result = await run_self_correction_loop(
        llm=llm,
        worktree_path=worktree_path,
        validator=validator,
        files_in_scope=["a.py"],
        compile_error_output="SyntaxError: something",
        active_code_context="",
    )

    assert result.resolved is True
    assert result.turns_used == 2
    assert result.modified_files == {"a.py": "def foo():\n    return 2\n"}
    with open(os.path.join(worktree_path, "a.py"), encoding="utf-8") as fh:
        assert fh.read() == "def foo():\n    return 2\n"
    validator.run_compile_check.assert_called_once_with(["a.py"])


@pytest.mark.asyncio
async def test_self_correction_exhausts_budget(tmp_path):
    worktree_path = str(tmp_path)
    _write(worktree_path, "a.py", "def foo():\n    return 1\n")

    llm = MagicMock()
    # Every turn: apply a no-op-ish patch then recompile, which always fails.
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "recompile", "arguments": {}}]},
        {"content": "", "tool_calls": [{"id": "2", "name": "recompile", "arguments": {}}]},
    ])

    validator = MagicMock()
    validator.run_compile_check = MagicMock(return_value={"success": False, "output": "still broken"})

    result = await run_self_correction_loop(
        llm=llm,
        worktree_path=worktree_path,
        validator=validator,
        files_in_scope=["a.py"],
        compile_error_output="SyntaxError: something",
        active_code_context="",
        max_turns=2,
    )

    assert result.resolved is False
    assert result.turns_used == 2
    assert "still broken" in result.final_compile_output


@pytest.mark.asyncio
async def test_self_correction_llm_exception_preserves_original_compile_error(tmp_path):
    """Regression test for a real bug found live, 2026-08-17
    (ignite_qpid_person, run b-10l): llm.complete_with_tools() had NO
    exception handling, despite this function's own docstring explicitly
    promising the caller can treat any non-resolved outcome "exactly the
    same as if the loop had never run." That promise was false for an
    exception - it propagated all the way up past attempt.py's own
    unguarded call site to workflow.py's outer exception handler, which
    replaced the REAL, already-captured Maven compile error with the raw
    HTTP/SDK exception text (three consecutive HTTP 500s from Ollama while
    parsing the model's own native tool call, in the live incident),
    causing every subsequent retry to implicate the wrong file entirely.
    Must degrade to resolved=False, preserving the original compile error,
    not raise."""
    worktree_path = str(tmp_path)
    _write(worktree_path, "App.java", "class App {}\n")

    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=RuntimeError(
        "Error code: 500 - {'error': {'message': 'XML syntax error on line 15: "
        "element <parameter> closed by </function>'}}"
    ))

    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm,
        worktree_path=worktree_path,
        validator=validator,
        files_in_scope=["App.java"],
        compile_error_output=(
            "App.java:[63,56] incompatible types: java.lang.Object cannot be "
            "converted to com.example.Person"
        ),
        active_code_context="",
    )

    assert result.resolved is False
    assert "Object cannot be converted to com.example.Person" in result.final_compile_output
    assert "500" not in result.final_compile_output
    validator.run_compile_check.assert_not_called()


@pytest.mark.asyncio
async def test_self_correction_apply_patch_grounded_via_read_file_not_rejected_by_stale_context(tmp_path):
    """Regression test for a real bug found live, 2026-08-17
    (ignite_qpid_person, run b-10m). apply_patch's own edit-application
    already re-reads the real, current, full file content (identical to
    what read_file returns), but it ALSO independently re-validated the
    search block against active_code_context - a snapshot fixed at the
    START of this conversation, never updated as read_file gets called
    mid-loop. A model that called read_file, was shown the real current
    content, and copied its search block exactly from what it just read
    could still be rejected with "elided in the skeletonized context and
    not shown to the model" - false, it WAS shown, just through
    read_file rather than the original prompt-time snapshot. Confirmed
    live: the self-correction model read the file, correctly diagnosed
    the cast issue, and proposed an exact, grounded fix that was rejected
    before ever reaching recompile."""
    worktree_path = str(tmp_path)
    full_content = (
        "public class App {\n"
        "    public static void main(String[] args) {\n"
        "        var cache = ignite.getOrCreateCache(\"person-cache\");\n"
        "        Person cachedPerson = cache.get(1);\n"
        "    }\n"
        "}\n"
    )
    _write(worktree_path, "App.java", full_content)

    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "read_file", "arguments": {"filepath": "App.java"}}]},
        {"content": "", "tool_calls": [{"id": "2", "name": "apply_patch", "arguments": {
            "filepath": "App.java",
            "edits": [{
                "search": "Person cachedPerson = cache.get(1);",
                "replace": "Person cachedPerson = (Person) cache.get(1);",
            }],
        }}]},
        {"content": "", "tool_calls": [{"id": "3", "name": "recompile", "arguments": {}}]},
    ])
    validator = MagicMock()
    validator.run_compile_check = MagicMock(return_value={"success": True, "output": "compiled OK"})

    # Deliberately a SKELETONIZED/stale snapshot that does NOT contain the
    # real search text - exactly the live incident's shape.
    result = await run_self_correction_loop(
        llm=llm,
        worktree_path=worktree_path,
        validator=validator,
        files_in_scope=["App.java"],
        compile_error_output="incompatible types: Object cannot be converted to Person",
        active_code_context="public class App { /* ... skeletonized ... */ }",
    )

    assert result.resolved is True
    assert result.modified_files["App.java"] == full_content.replace(
        "Person cachedPerson = cache.get(1);", "Person cachedPerson = (Person) cache.get(1);"
    )


@pytest.mark.asyncio
async def test_self_correction_apply_patch_without_read_file_still_grounded_against_shown_context(tmp_path):
    # Companion negative case - a model that never calls read_file (or
    # never had this file modified earlier in the same conversation) and
    # proposes a search block absent from BOTH the real file and the shown
    # context must still be rejected - the safety net this check exists
    # for is untouched for a genuinely ungrounded guess.
    worktree_path = str(tmp_path)
    _write(worktree_path, "App.java", "public class App {\n    int x = 1;\n}\n")

    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "apply_patch", "arguments": {
            "filepath": "App.java",
            "edits": [{"search": "int hallucinatedField = 42;", "replace": "int hallucinatedField = 43;"}],
        }}]},
        {"content": "giving up", "tool_calls": []},
    ])
    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm,
        worktree_path=worktree_path,
        validator=validator,
        files_in_scope=["App.java"],
        compile_error_output="some error",
        active_code_context="public class App { /* skeletonized, no real content shown */ }",
    )

    assert result.resolved is False


@pytest.mark.asyncio
async def test_self_correction_apply_patch_error_fed_back_to_model(tmp_path):
    worktree_path = str(tmp_path)
    _write(worktree_path, "a.py", "def foo():\n    return 1\n")

    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {
            "content": "",
            "tool_calls": [{
                "id": "1", "name": "apply_patch",
                "arguments": {"filepath": "a.py", "edits": [{"search": "this text is not in the file", "replace": "x"}]},
            }],
        },
        {"content": "", "tool_calls": []},  # model gives up after seeing the error
    ])

    validator = MagicMock()
    validator.run_compile_check = MagicMock(return_value={"success": False, "output": "still broken"})

    result = await run_self_correction_loop(
        llm=llm,
        worktree_path=worktree_path,
        validator=validator,
        files_in_scope=["a.py"],
        compile_error_output="SyntaxError: something",
        active_code_context="",
        max_turns=4,
    )

    assert result.resolved is False
    assert result.modified_files == {}
    assert any("matched 0 times" in entry["result"] for entry in result.transcript)
    validator.run_compile_check.assert_not_called()


@pytest.mark.asyncio
async def test_self_correction_read_file_and_list_files_scoped(tmp_path):
    worktree_path = str(tmp_path)
    _write(worktree_path, "a.py", "in scope")
    _write(worktree_path, "secret.env", "OUT_OF_SCOPE_SECRET")

    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "read_file", "arguments": {"filepath": "secret.env"}}]},
        {"content": "", "tool_calls": [{"id": "2", "name": "list_files", "arguments": {}}]},
        {"content": "", "tool_calls": []},
    ])

    validator = MagicMock()
    validator.run_compile_check = MagicMock(return_value={"success": False, "output": "still broken"})

    result = await run_self_correction_loop(
        llm=llm,
        worktree_path=worktree_path,
        validator=validator,
        files_in_scope=["a.py"],
        compile_error_output="SyntaxError: something",
        active_code_context="",
        max_turns=4,
    )

    read_result = result.transcript[0]["result"]
    assert "OUT_OF_SCOPE_SECRET" not in read_result
    assert "not a file in this attempt's sandbox" in read_result

    list_result = result.transcript[1]["result"]
    assert "secret.env" not in list_result


# --- The 4 "ground truth" tools added 2026-08-22 - close the gap where a fix
# needs grounding in something that isn't any one file's content: an
# external dependency's real API shape, or a real build-layout question. ---

@pytest.mark.asyncio
async def test_self_correction_list_dependencies_returns_real_declared_coordinates(tmp_path):
    worktree_path = str(tmp_path)
    _write(worktree_path, "pom.xml", (
        "<project><dependencies>"
        "<dependency><groupId>org.apache.ignite</groupId><artifactId>ignite-core</artifactId>"
        "<version>2.18.0</version></dependency>"
        "</dependencies></project>"
    ))
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "list_dependencies", "arguments": {}}]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=["App.java"],
        compile_error_output="err", active_code_context="",
    )

    assert "org.apache.ignite:ignite-core" in result.transcript[0]["result"]


@pytest.mark.asyncio
async def test_self_correction_inspect_class_resolves_a_workspace_class_by_simple_name(tmp_path):
    worktree_path = str(tmp_path)
    _write(worktree_path, "Protocol.java", "public class Protocol {\n  public Protocol() {}\n}\n")
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{
            "id": "1", "name": "inspect_class", "arguments": {"fully_qualified_name": "com.example.Protocol"},
        }]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=["Protocol.java"],
        compile_error_output="err", active_code_context="",
    )

    assert "public Protocol()" in result.transcript[0]["result"]


@pytest.mark.asyncio
async def test_self_correction_inspect_class_resolves_an_external_dependency_via_javap(tmp_path):
    """The actual gap this widening exists for: an external library's real
    API is invisible to every workspace-file-based check in this codebase -
    inspect_class is the one thing that can ground a fix against it."""
    worktree_path = str(tmp_path)
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{
            "id": "1", "name": "inspect_class", "arguments": {"fully_qualified_name": "org.apache.ignite.Ignition"},
        }]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()
    validator.inspect_external_class = MagicMock(
        return_value="public class Ignition {\n  public static Ignite start(String);\n}"
    )

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=["App.java"],
        compile_error_output="err", active_code_context="",
    )

    assert "public static Ignite start" in result.transcript[0]["result"]
    validator.inspect_external_class.assert_called_once_with("org.apache.ignite.Ignition")


@pytest.mark.asyncio
async def test_self_correction_inspect_class_reports_honest_not_found(tmp_path):
    worktree_path = str(tmp_path)
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{
            "id": "1", "name": "inspect_class", "arguments": {"fully_qualified_name": "com.nonexistent.Thing"},
        }]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()
    validator.inspect_external_class = MagicMock(return_value=None)

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=[],
        compile_error_output="err", active_code_context="",
    )

    assert "could not be resolved" in result.transcript[0]["result"].lower()


@pytest.mark.asyncio
async def test_self_correction_inspect_class_lists_multiple_workspace_matches_instead_of_guessing(tmp_path):
    worktree_path = str(tmp_path)
    _write(worktree_path, "a/Protocol.java", "class Protocol {}")
    _write(worktree_path, "b/Protocol.java", "class Protocol {}")
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{
            "id": "1", "name": "inspect_class", "arguments": {"fully_qualified_name": "x.Protocol"},
        }]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator,
        files_in_scope=["a/Protocol.java", "b/Protocol.java"],
        compile_error_output="err", active_code_context="",
    )

    text = result.transcript[0]["result"]
    assert "a/Protocol.java" in text and "b/Protocol.java" in text
    assert "multiple files" in text


@pytest.mark.asyncio
async def test_self_correction_resolve_missing_dependency_looks_up_a_real_coordinate(tmp_path):
    worktree_path = str(tmp_path)
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{
            "id": "1", "name": "resolve_missing_dependency",
            "arguments": {"symbol": "org.apache.qpid.jms.JmsConnectionFactory"},
        }]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()

    with patch(
        "kriya.workflow.self_correction.resolve_maven_class",
        return_value={"groupId": "org.apache.qpid", "artifactId": "qpid-jms-client", "version": "1.9.0"},
    ) as mock_resolve:
        result = await run_self_correction_loop(
            llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=[],
            compile_error_output="err", active_code_context="",
        )

    assert "qpid-jms-client" in result.transcript[0]["result"]
    mock_resolve.assert_called_with("org.apache.qpid.jms.JmsConnectionFactory", "fc")


@pytest.mark.asyncio
async def test_self_correction_resolve_missing_dependency_rejects_a_bare_simple_name(tmp_path):
    """resolver.py's own docstring documents WHY a bare simple name is
    actively harmful, not just unhelpful (two real live false-positive
    incidents) - this tool must not let the model bypass that safeguard."""
    worktree_path = str(tmp_path)
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{
            "id": "1", "name": "resolve_missing_dependency", "arguments": {"symbol": "IgniteCache"},
        }]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=[],
        compile_error_output="err", active_code_context="",
    )

    assert "ERROR" in result.transcript[0]["result"]


@pytest.mark.asyncio
async def test_self_correction_list_compiled_output_reports_empty_build_honestly(tmp_path):
    worktree_path = str(tmp_path)
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "list_compiled_output", "arguments": {}}]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=[],
        compile_error_output="err", active_code_context="",
    )

    assert "nothing was actually compiled" in result.transcript[0]["result"]


@pytest.mark.asyncio
async def test_self_correction_list_compiled_output_lists_real_class_files(tmp_path):
    worktree_path = str(tmp_path)
    os.makedirs(os.path.join(worktree_path, "target", "classes"))
    with open(os.path.join(worktree_path, "target", "classes", "App.class"), "wb") as fh:
        fh.write(b"")
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "list_compiled_output", "arguments": {}}]},
        {"content": "done", "tool_calls": []},
    ])
    validator = MagicMock()

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=[],
        compile_error_output="err", active_code_context="",
    )

    assert "App.class" in result.transcript[0]["result"]


@pytest.mark.asyncio
async def test_self_correction_run_verification_failure_type_uses_recompile_not_a_full_rerun(tmp_path):
    """failure_type="run_verification" (2026-08-22) fixes the INFRASTRUCTURE-
    shaped cause of a runtime failure (wrong classpath, empty build output),
    validated via the same recompile tool - it deliberately does NOT attempt
    to re-run the generated application itself; actual behavior correctness
    stays owned by attempt.py's own run-verification cycle on the next
    attempt."""
    worktree_path = str(tmp_path)
    _write(worktree_path, "pom.xml", "<project></project>")
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{"id": "1", "name": "recompile", "arguments": {}}]},
    ])
    validator = MagicMock()
    validator.run_compile_check = MagicMock(return_value={"success": True, "output": "compiled OK"})

    result = await run_self_correction_loop(
        llm=llm, worktree_path=worktree_path, validator=validator, files_in_scope=["App.java"],
        compile_error_output="Could not find or load main class App", active_code_context="",
        failure_type="run_verification",
    )

    assert result.resolved is True
    validator.run_compile_check.assert_called_once_with(["App.java"])
