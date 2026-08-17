import os
from unittest.mock import AsyncMock, MagicMock

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
    assert "a.py" in list_result
