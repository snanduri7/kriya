"""A full-file rewrite of an existing file keeps the file's final newline.

Seen live in demo-03 (2026-09-25): the model returned the fixed file in a
```java fence, fence extraction trimmed the newline before the closing fence,
and the accepted diff dropped the file's final newline next to the one-line
fix. These tests drive the real run_attempt() write loop (sanitization, write,
commit to the sandbox), not the helper alone.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import AttemptContext, _final_line_ending, _keep_final_newline, run_attempt
from kriya.workflow.migration import resolve_migration_resolution
from kriya.workflow.state import GenerationState

ORIGINAL = "public class Target {\n    public void doWork() {\n        int x = 1;\n    }\n}\n"
FIXED_BODY = "public class Target {\n    public void doWork() {\n        int x = 2;\n    }\n}"


def _ctx(workspace, developer):
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": False, "run_commands": [], "command_source": "inferred", "success_criteria": "",
    })
    run_verifier.grade = AsyncMock(return_value={"passed": False, "reasoning": "", "likely_files": []})
    spec_compliance = AsyncMock()
    spec_compliance.check = AsyncMock(return_value={
        "compliant": True, "reasoning": "", "missing_requirements": [], "likely_files": [],
    })
    goal = "Change x to 2 in Target.doWork()"
    return AttemptContext(
        goal=goal, plan="Step 1: change x", design="Design: edit Target.java",
        workspace_path=workspace, worktree_path=workspace,
        architect_files=["Target.java"], resume_state=None, run_id="newline-run",
        skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="", verification_contract_block="",
        recovery_contract_block="", required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=["Target.java"], architect_basename_to_path={"Target.java": "Target.java"},
        chain=[], targeted_max_retries=3, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={}, developer=developer,
        run_verifier=run_verifier, spec_compliance=spec_compliance, skill_engine=MagicMock(),
        kernel=Kernel(config=AppConfig()), max_retries=4, web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
        migration_resolution=resolve_migration_resolution(goal, workspace),
    )


def _rewrite(tmp_path, original, model_content, target="Target.java"):
    path = tmp_path / target
    if original is not None:
        path.write_bytes(original.encode("utf-8"))
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[{"filepath": target, "content": model_content}])
    state = GenerationState()
    state.attempt_number = 0
    state.all_files_written = set()
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests",
        return_value={"success": True, "output": ""},
    ):
        asyncio.run(run_attempt(state, _ctx(str(tmp_path), developer)))
    return path.read_bytes().decode("utf-8")


def test_fenced_full_file_rewrite_keeps_the_existing_final_newline(tmp_path):
    written = _rewrite(tmp_path, ORIGINAL, "```java\n" + FIXED_BODY + "\n```")
    assert written == FIXED_BODY + "\n"


def test_unfenced_full_file_rewrite_without_final_newline_gets_it_back(tmp_path):
    assert _rewrite(tmp_path, ORIGINAL, FIXED_BODY) == FIXED_BODY + "\n"


def test_crlf_file_keeps_its_crlf_final_line_ending(tmp_path):
    original = ORIGINAL.replace("\n", "\r\n")
    body = FIXED_BODY.replace("\n", "\r\n")
    assert _rewrite(tmp_path, original, body).endswith("}\r\n")


def test_file_without_a_final_newline_is_not_given_one(tmp_path):
    assert _rewrite(tmp_path, ORIGINAL.rstrip("\n"), FIXED_BODY) == FIXED_BODY


@pytest.mark.parametrize(("prior_ending", "content", "expected"), [
    ("\n", "b", "b\n"),
    ("\n", "b\n", "b\n"),
    ("\r\n", "b", "b\r\n"),
    ("", "b", "b"),
    ("\n", "", ""),          # empty content is never padded
])
def test_keep_final_newline(prior_ending, content, expected):
    assert _keep_final_newline(prior_ending, content) == expected


@pytest.mark.parametrize(("data", "expected"), [
    (b"x\r\n", "\r\n"), (b"x\n", "\n"), (b"x", ""), (b"", ""), (b"\n", "\n"),
])
def test_final_line_ending(tmp_path, data, expected):
    path = tmp_path / "f"
    path.write_bytes(data)
    assert _final_line_ending(str(path)) == expected


def test_new_file_is_written_as_given(tmp_path):
    assert _rewrite(tmp_path, None, "```java\n" + FIXED_BODY + "\n```") == FIXED_BODY
