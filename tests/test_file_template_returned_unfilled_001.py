"""FILE-TEMPLATE-RETURNED-UNFILLED-001 (Stage C runs 20261119T125126-20dc890c, unit s4, and 20261119T163313-acea6505, unit s3).

MEASURED in both sealed runs: the plan asked for ``src/main/resources/system.properties`` to "exist and be empty". The Developer answered
with the FILE template exactly as the response contract prints it; the template's placeholder line became the file. The unit's declared
verification was a compile check (which says nothing about the file), the Reviewer wrote "the file is empty", and the compliance check
read the placeholder as a description ("...with the complete file content") and passed it.

First wrong decision: the Developer's answer. First place it can be known with certainty: the response parser, which owns the
template - an answer whose WHOLE payload is the contract's own placeholder line is the template returned unfilled, not content.

The correction is that one refusal in ``response_protocol.parse_structured``, under the existing INVALID_EDIT_PROTOCOL code (the
existing malformed-response retry then asks again, with this text). It is not a placeholder ban: these tests prove an empty file, the
same line among other lines, and other literal text are content as before.

Sealed bytes: tests/fixtures/stage_c_run3 (digest-checked). The run-2 answer for the same file is the same 109 bytes.
"""
import asyncio
import hashlib
import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from _protocol_responses import sentinel, wants_structured

from kriya.agents.response_protocol import (
    FILE_CONTENT_PLACEHOLDER,
    INVALID_EDIT_PROTOCOL,
    parse_structured,
    structured_contract,
)
from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.workflow import WorkflowEngine

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stage_c_run3")
ANSWER = "developer_answer.s3.system_properties.txt"
ANSWER_SHA256 = "67b76ad47d1049dfd7e8704c32b6d980550afa8a6421d4af7fe44c34de237ca0"
PATH = "src/main/resources/system.properties"
OPEN, CLOSE = f'<<<KRIYA:FILE path="{PATH}">>>', "<<<KRIYA:END_FILE>>>"


def _sealed_answer():
    with open(os.path.join(FIXTURES, ANSWER), "rb") as handle:
        data = handle.read()
    assert hashlib.sha256(data).hexdigest() == ANSWER_SHA256, f"{ANSWER} is not the sealed run-3 content"
    return data.decode("utf-8")


def _file(*lines):
    return "\n".join([OPEN, *lines, CLOSE]) + "\n"


def test_the_placeholder_is_the_line_the_contract_itself_prints():
    """One source: what the parser refuses is exactly what the prompt shows, so the two cannot drift apart."""
    contract = structured_contract(PATH, analysis_required=False, allow_edit=False, allow_file=True, allow_no_change=False)
    assert f"{OPEN}\n{FILE_CONTENT_PLACEHOLDER}\n{CLOSE}\n" in contract and FILE_CONTENT_PLACEHOLDER == "<the complete file content>"


def test_the_sealed_run3_answer_is_the_template_and_is_refused_with_nothing_to_write():
    """Before the correction this parsed as a valid FILE whose content was the placeholder line (what the run wrote to disk)."""
    answer = _sealed_answer()
    assert answer == _file(FILE_CONTENT_PLACEHOLDER).rstrip("\n") or answer == _file(FILE_CONTENT_PLACEHOLDER)
    parsed = parse_structured(answer, PATH)
    assert parsed.reason_code == INVALID_EDIT_PROTOCOL and parsed.content is None and parsed.edits == ()
    # the text the existing malformed-response retry hands back: what was wrong, and how an empty file is written
    assert "holds only the protocol's own placeholder line '<the complete file content>'" in parsed.detail
    assert "a file with no content has nothing between them" in parsed.detail


@pytest.mark.parametrize("line", [FILE_CONTENT_PLACEHOLDER, f"  {FILE_CONTENT_PLACEHOLDER}", f"{FILE_CONTENT_PLACEHOLDER}\t \r"])
def test_surrounding_whitespace_does_not_turn_the_template_into_content(line):
    assert parse_structured(_file(line), PATH).reason_code == INVALID_EDIT_PROTOCOL


def test_an_empty_file_is_still_an_empty_file():
    """What the plan asked for, written as the contract allows: nothing between the two protocol lines."""
    parsed = parse_structured(_file(), PATH)
    assert parsed.reason_code is None and parsed.content == "" and parsed.edits == ()


@pytest.mark.parametrize("lines", [
    ("# Empty file to satisfy SystemLauncher requirement",),            # the skill's own example of this file
    ("qpid.work_dir=target/qpid",),
    ("<project/>",),                                                     # other text in angle brackets
    ("<the complete file contents>",),                                   # near the placeholder, not it
    ("the complete file content",),
    (FILE_CONTENT_PLACEHOLDER, "key=value"),                             # the same line among others: a file may quote it
    ("# template:", FILE_CONTENT_PLACEHOLDER),
    (FILE_CONTENT_PLACEHOLDER, FILE_CONTENT_PLACEHOLDER),
    ("",),                                                               # one empty line
])
def test_every_other_payload_is_content_exactly_as_written(lines):
    parsed = parse_structured(_file(*lines), PATH)
    assert parsed.reason_code is None and parsed.content == "\n".join(lines) + "\n"


# ------------------------------------------------------ downstream of the parser: the empty file the plan asked for, through a real unit
#
# Owner ruling 2026-11-19: the refusal above tells the Developer that an empty file has nothing between the protocol lines, so that
# answer must actually be accepted by everything after the parser: the operation contract, staging, the creation of the candidate file,
# the unit's declared verification and the apply. The run-3 unit is replayed as it was planned (description, criterion and declared
# verification from the sealed plan), with the real workflow engine; only the model transport and the two validator commands are scripted
# - the harness of tests/test_enforce_verified_no_change.py, whose harness opt-ins are stated on the tests below.
#
# NOT claimed: that the unit's declared verification PROVES emptiness. The sealed plan declared a compile check for "exists and is empty";
# compile evidence says nothing about a file's bytes, in the sealed run and here (open finding, unchanged). The bytes are asserted by
# this test itself.

UNIT_DESCRIPTION = "Create system.properties file to satisfy Qpid Broker-J startup requirements"
UNIT_CRITERION = "System properties file exists and is empty as required by Qpid Broker-J"
UNIT_VERIFICATION = {"type": "tool", "tool_name": "compile", "description": "Verify system.properties file exists and is empty as required"}


def _run3_unit_plan():
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "the broker starts on localhost port 5672"}],
        "acceptance_criteria": [{"id": "ac3", "description": UNIT_CRITERION, "method": "judgment"}],
        "subtasks": [{"id": "s3", "description": UNIT_DESCRIPTION, "execution_method": "model",
                      "planned_files": [{"path": PATH, "action": "create"}], "provides": ["system_properties_file"],
                      "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac3"],
                      "verification": [dict(UNIT_VERIFICATION)]}]})


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                   cwd=repo, check=True, capture_output=True)


def _run_the_unit(tmp_path, answers):
    """Unit s3 through ``WorkflowEngine.run_generation_workflow``; the Developer gives ``answers`` in order (the last one repeats)."""
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.knowledge.offline_mode = True               # no registry lookup from a test
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # what unit s3 found in the sealed run: unit s1's pom.xml, already applied (a Maven project, so the unit's compile gate applies)
    with open(os.path.join(FIXTURES, "pom.s1.xml"), "rb") as handle:
        pom = handle.read()
    assert hashlib.sha256(pom).hexdigest() == "e0ca976fa98393b0ba94a8f157da64bf07f995e38b586b6e49949009a8a9f178"
    (workspace / "pom.xml").write_bytes(pom)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base + s1")
    developer, structured, other = [], [], []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % PATH
        elif "Developer Agent" in first:
            developer.append(user_prompt)
            structured.append(wants_structured(system_prompt))
            content = answers[min(len(developer), len(answers)) - 1]
        else:
            other.append(first[:80])
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda *a, **k: {"success": True, "output": "no tests ran"}):
        result = asyncio.run(engine.run_generation_workflow(
            goal="Create the Qpid application's system.properties resource.", workspace_path=str(workspace),
            predetermined_plan=UNIT_DESCRIPTION, predetermined_design="", predetermined_architect_files=[PATH],
            allowed_write_relpaths=[PATH], structured_plan=_run3_unit_plan(), current_subtask_id="s3",
            required_verification=[dict(UNIT_VERIFICATION)], approval_callback=AsyncMock(return_value=True),
            # A unit of a plan runs after the run has resolved its knowledge gaps (the sealed run: the operator's strict policy with
            # the Qpid skill, "Knowledge resolution: cleared"). MEASURED (owner run 2026-11-19, c4-prep/check.v3): without this the
            # unit stops with status knowledge_gap for org.apache.qpid:qpid-broker-core (read from s1's pom.xml) before any Developer
            # call. The confirmation is the run-level fact, stated; the guard itself is not this test's subject.
            knowledge_risk_confirmed=True))
    # What the run itself says, for every assertion message below: a run that stops before or after the Developer names why.
    said = {key: str(result.get(key))[:600] for key in ("status", "failure_category", "error", "quality_gates_passed", "files",
                                                        "environment_failure", "no_progress_reason", "gap_report", "last_failure")
            if isinstance(result, dict) and result.get(key) is not None}
    why = f"developer calls={len(developer)} structured={structured} other model calls={other} result={said}"
    assert all(structured), why                                 # the structured protocol is the path under proof
    return workspace, developer, result, why


# The twin harness file gets these from tests/conftest.py's PRE_S6_S7_SCRIPTED_RUN_FILES (a scripted run asserted to end SUCCESS) and
# its containment-census file rule (S, M); a new file states them: the two fixtures here, the two exact ids in tests/containment_census.py.
_scripted_success = pytest.mark.usefixtures("requirements_closed", "complete_change_approved")


@_scripted_success
def test_an_empty_file_answer_creates_the_empty_file_and_passes_the_units_own_verification(tmp_path):
    workspace, developer, result, why = _run_the_unit(tmp_path, [sentinel(PATH, content="")])
    assert sentinel(PATH, content="") == _file()                       # the answer IS the two protocol lines with nothing between
    assert len(developer) == 1, f"accepted on the first answer, no retry - {why}"
    assert result["quality_gates_passed"] is True, why
    assert result["files"] == [PATH], why
    assert (workspace / PATH).is_file() and (workspace / PATH).read_bytes() == b"", why     # created, applied, and really empty
    [verified] = result["verification_results"]
    assert (verified["type"], verified["tool_name"], verified["passed"]) == ("tool", "compile", True), why


@_scripted_success
def test_the_unfilled_template_is_refused_the_developer_is_told_and_the_empty_answer_then_passes(tmp_path):
    """The sealed sequence with its correction: the run-3 answer first, then what the refusal asks for. Before the correction the first
    answer was accepted and the placeholder line was the file."""
    workspace, developer, result, why = _run_the_unit(tmp_path, [_sealed_answer(), sentinel(PATH, content="")])
    assert len(developer) == 2, f"the first answer was refused and the Developer was asked again - {why}"
    assert "holds only the protocol's own placeholder line" in developer[1]                # the next request names the refusal
    assert "a file with no content has nothing between them" in developer[1]
    assert result["quality_gates_passed"] is True, why
    assert (workspace / PATH).read_bytes() == b"", why
    assert FILE_CONTENT_PLACEHOLDER.encode() not in (workspace / PATH).read_bytes()
