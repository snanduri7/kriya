from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.workflow.self_correction import run_repair_loop


@pytest.mark.asyncio
async def test_generic_repair_loop_retests_only_the_targeted_test(tmp_path):
    source = tmp_path / "pricing.py"
    source.write_text("def price(): return 0\n", encoding="utf-8")
    llm = MagicMock()
    llm.complete_with_tools = AsyncMock(side_effect=[
        {"content": "", "tool_calls": [{
            "id": "1", "name": "read_file", "arguments": {"filepath": "pricing.py"},
        }]},
        {"content": "", "tool_calls": [{
            "id": "2", "name": "apply_patch", "arguments": {
                "filepath": "pricing.py", "edits": [{
                    "search": "return 0", "replace": "return 1",
                }],
            },
        }, {
            "id": "3", "name": "retest", "arguments": {},
        }]},
    ])
    validator = MagicMock()
    validator.run_tests.return_value = {"success": True, "output": "1 passed"}

    result = await run_repair_loop(
        llm=llm, worktree_path=str(tmp_path), validator=validator,
        files_in_scope=["pricing.py"], compile_error_output="assert 0 == 1",
        active_code_context="", target_test="tests/test_pricing.py",
    )

    assert result.resolved
    validator.run_tests.assert_called_once_with(target_test="tests/test_pricing.py")
    assert source.read_text(encoding="utf-8") == "def price(): return 1\n"
