"""VERIFIER-FAILURE-OWNER-CORROBORATION-001 (Stage C run 4, 20261119T174636-1a504074; owner ruling 2026-11-19).

MEASURED in the sealed run: unit s4 wrote ``ProtocolParser.decode`` returning a null body for an empty body (the goal says an empty
non-null body is valid). Unit s6's regenerated test caught it in ``testEmptyBody``; a person answered ``expectation_valid``. The failure is
an assertion, so the only frame in the gate output is the test's own, the strict owner predicate selected nobody
(PRODUCTION_OWNER_LOCATOR_VERIFIER_ONLY), and the run stopped with a confirmed defect and no repair path.

The correction keeps the principle that neither a locator nor a plan dependency proves the defective owner. When the locator names only
the accepted verifier, TWO kinds of existing evidence must agree: the verifier's source calls into a production file (the analyzer's
structural parse, ``ownership_findings.artifacts_called_by``) AND the validated plan puts that file in a DIRECT dependency of the
verifier's unit. Exactly one plan owner between the corroborated files = the bounded repair candidate. Everything else selects nobody.

These tests drive the real failure seam (``retry_strategy._generated_verifier_ruling``) on the sealed files and the sealed gate output
(tests/fixtures/stage_c_run4, digest-checked). What happens AFTER an owner is selected - the scope conflict, the owner's reopen with only
its own files, the replay of the unchanged verifier, and a changed verifier never settling a recovery - is existing, unchanged code with
its own proof: tests/test_enforce_owner_recovery_verifier_replay_001.py and tests/test_verifier_semantic_loop_001.py.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

import pytest
from test_enforce_owner_recovery_verifier_replay_001 import (
    COMPILE,
    COMPILE_EVIDENCE,
    RUN_TESTS,
    TESTS_EVIDENCE,
    ZERO_TESTS,
    _statuses,
)
from test_enforce_owner_recovery_verifier_replay_001 import _run as _enforce
from test_verifier_semantic_loop_001 import _report, _unit

from kriya.policy.filesystem import WriteScopeMode
from kriya.repository.snapshot import raw_digest
from kriya.workflow import ownership_findings, retry_strategy, verifier_replay
from kriya.workflow import verifier_semantic as vs
from kriya.workflow.failure import Failure
from kriya.workflow.failure_grounding import grounded_evidence_excerpt
from kriya.workflow.ownership_findings import artifacts_called_by
from kriya.workflow.plan_schema import ChangeKind, EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.state import GenerationState
from kriya.workflow.verifier_replay import CarriedFile, RefusedCandidate

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stage_c_run4")
SEALED = {
    "Protocol.java": "f784d45ab5aac5303019961da1b72a1246beaffcdb1c26206d9ae7d809de6374",
    "ProtocolParser.java": "85da7923ffbc993c212e2d20a50defd43e992e05418d238bdacd62b264967642",
    "ProtocolParserTest.java": "48458b91d46ccd903f436c3cbe17802e869c41fe872b6c0e10985826366fcced",
    "s6_test_failure.output.txt": "69a79732760de7a0356ced0f3607a2074f344cae5198af6593f39e0e739a9348",
}
PROTOCOL, PARSER, TEST = "src/main/java/Protocol.java", "src/main/java/ProtocolParser.java", "src/test/java/ProtocolParserTest.java"
UNRELATED = "src/main/java/Unrelated.java"
FAILED_CASE = "ProtocolParserTest.testEmptyBody"
VERIFIER_ONLY, AMBIGUOUS = "PRODUCTION_OWNER_LOCATOR_VERIFIER_ONLY", "PRODUCTION_OWNER_AMBIGUOUS"


def _sealed(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = handle.read()
    assert hashlib.sha256(data).hexdigest() == SEALED[name], f"{name} is not the sealed run-4 content"
    return data.decode("utf-8")


def _run4_files() -> Dict[str, str]:
    return {PROTOCOL: _sealed("Protocol.java"), PARSER: _sealed("ProtocolParser.java"), TEST: _sealed("ProtocolParserTest.java")}


def _plan(test_depends_on: Sequence[str] = ("s4",), extra: Sequence[Dict[str, Any]] = ()) -> EngineeringPlan:
    """The three units of the sealed run-4 plan this failure is about (ids, files and dependencies as sealed): s3 Protocol.java,
    s4 ProtocolParser.java (depends on s3), s6 ProtocolParserTest.java (depends on s4)."""
    def unit(sid: str, path: str, depends_on: Sequence[str], tool: str = "compile") -> Dict[str, Any]:
        return {"id": sid, "description": f"write {path}", "execution_method": "model", "provides": [sid], "depends_on": list(depends_on),
                "planned_files": [{"path": path, "action": "create"}], "relevant_global_invariant_ids": ["gi1"],
                "verification": [{"type": "tool", "tool_name": tool, "description": tool}]}
    units = [unit("s3", PROTOCOL, []), unit("s4", PARSER, ["s3"]), *extra, unit("s6", TEST, test_depends_on, "test")]
    return EngineeringPlan.model_validate({"plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
                                           "acceptance_criteria": [], "subtasks": units})


def _ruling(tmp_path: Path, *, plan: Optional[EngineeringPlan] = None, files: Optional[Dict[str, str]] = None,
            answer: str = "expectation_valid", output: Optional[str] = None, scope: Any = WriteScopeMode.ALLOWLIST) -> Any:
    """The REAL failure seam on a real tree: unit s6 wrote only its test, the test gate reported ``testEmptyBody`` failed, and the person's
    answer is ``answer``. Returns (ruling, state, unit)."""
    files = _run4_files() if files is None else files
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    unit = _unit({path: (tmp_path / path).read_bytes() for path in files}, answer)
    unit.observe({"success": False, "test_execution": _report("failed", FAILED_CASE)})
    state = GenerationState()
    state.all_files_written = type(state.all_files_written)([TEST])
    ctx = SimpleNamespace(verifier_semantic=unit, structured_plan=_plan() if plan is None else plan, worktree_path=str(tmp_path),
                          write_scope_mode=scope)
    output = _sealed("s6_test_failure.output.txt") if output is None else output
    failure = Failure(type="targeted_test", message=output, raw_output=output)
    return asyncio.run(retry_strategy._generated_verifier_ruling(state, ctx, failure)), state, unit


def _why_nobody(ruling: Any) -> str:
    assert ruling.owner is None and ruling.stop is not None and ruling.stop.reason_code == vs.REASON_NO_PRODUCTION_OWNER
    return ruling.stop.detail.rsplit(": ", 1)[-1]


# ----------------------------------------------------------------------------------------------- the sealed run-4 evidence, as it is

def test_the_sealed_gate_output_names_only_the_verifier_and_the_verifier_calls_into_both_production_files(tmp_path):
    """The exact source evidence. A source reference alone does not single out one file: the test calls methods of BOTH."""
    output = _sealed("s6_test_failure.output.txt")
    assert "ProtocolParserTest.testEmptyBody(ProtocolParserTest.java:40)" in output and "actual array was <null>" in output
    assert "ProtocolParser.java" not in output and "Protocol.java" not in output         # no production frame anywhere
    for path, text in _run4_files().items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    assert artifacts_called_by(str(tmp_path), [TEST], [PROTOCOL, PARSER]) == [PROTOCOL, PARSER]
    assert artifacts_called_by(str(tmp_path), [TEST], [PARSER]) == [PARSER]              # encode / decode
    assert artifacts_called_by(str(tmp_path), [TEST], [PROTOCOL]) == [PROTOCOL]          # the getters
    assert artifacts_called_by(str(tmp_path), [PARSER], [TEST]) == []                    # the parser calls nothing the test declares
    assert artifacts_called_by(str(tmp_path), [TEST], []) == [] and artifacts_called_by(str(tmp_path), [], [PARSER]) == []
    assert artifacts_called_by(str(tmp_path), [TEST], ["src/main/java/Missing.java"]) == []          # unreadable: no evidence


def test_run4_replay_the_confirmed_failure_selects_s4_as_the_bounded_repair_candidate(tmp_path):
    """Before the correction this ruling was a stop: PRODUCTION_OWNER_LOCATOR_VERIFIER_ONLY, the run's terminal cause."""
    ruling, state, unit = _ruling(tmp_path)
    assert ruling.stop is None and ruling.owner is not None and state.environment_failure is None
    assert (ruling.owner.owner_id, ruling.owner.files, ruling.owner.corroborated) == ("s4", (PARSER,), True)
    assert TEST in unit.denied                                           # the accepted verifier stays exactly as it is
    attribution = retry_strategy._semantic_attribution(ruling)
    assert attribution.files == [PARSER] and attribution.tier == "subtask_dependency"   # plan-structure evidence, not a locator
    assert "bounded repair candidate, not a proven root cause" in attribution.reasoning
    assert "direct dependency of the verifier's unit" in attribution.reasoning and "run again unchanged" in attribution.reasoning
    assert "locator names these production artifacts" not in attribution.reasoning       # no locator is claimed
    [event] = [event for event in state.run_events if event.kind == "verifier_semantic.ruling"]
    assert (event.details["action"], event.details["owner_id"], event.details["owner_files"]) == ("repair_production_owner", "s4", [PARSER])


def test_control_without_the_source_evidence_the_same_failure_selects_nobody(tmp_path, monkeypatch):
    """The sealed outcome, reproduced by withholding one of the two kinds of evidence: the dependency alone selects no one."""
    monkeypatch.setattr(ownership_findings, "artifacts_called_by", lambda *args, **kwargs: [])
    ruling, state, _unit_state = _ruling(tmp_path)
    assert _why_nobody(ruling) == VERIFIER_ONLY and state.environment_failure.startswith("VERIFIER_SEMANTIC_AUTHORITY_REQUIRED:")


# ------------------------------------------------------------------------------------------------------ what must still select nobody

def test_a_dependency_the_verifier_does_not_call_into_corroborates_nothing(tmp_path):
    files = {**_run4_files(), UNRELATED: "public class Unrelated {\n    public int unrelated() { return 1; }\n}\n"}
    extra = [{"id": "s5", "description": "write Unrelated", "execution_method": "model", "provides": ["s5"], "depends_on": [],
              "planned_files": [{"path": UNRELATED, "action": "create"}], "relevant_global_invariant_ids": ["gi1"],
              "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}]
    ruling, _state, _unit_state = _ruling(tmp_path, plan=_plan(test_depends_on=["s5"], extra=extra), files=files)
    assert _why_nobody(ruling) == VERIFIER_ONLY, "the test calls the parser, but the plan's only direct dependency is a file it never calls"


def test_two_directly_depended_owners_the_verifier_calls_into_are_ambiguous(tmp_path):
    ruling, _state, _unit_state = _ruling(tmp_path, plan=_plan(test_depends_on=["s3", "s4"]))
    assert _why_nobody(ruling) == AMBIGUOUS, "Protocol.java (s3) and ProtocolParser.java (s4) are both called and both direct dependencies"


def test_a_verifier_unit_with_no_dependency_has_no_candidate(tmp_path):
    ruling, _state, _unit_state = _ruling(tmp_path, plan=_plan(test_depends_on=[]))
    assert _why_nobody(ruling) == VERIFIER_ONLY


@pytest.mark.parametrize("answer", ["expectation_invalid", "cannot_decide"])
def test_a_verifier_the_person_did_not_accept_never_selects_a_production_owner(tmp_path, answer):
    ruling, _state, _unit_state = _ruling(tmp_path, answer=answer)
    assert ruling.owner is None
    if answer == "expectation_invalid":
        assert ruling.regenerate == TEST and ruling.stop is None                 # its one regeneration, of exactly the verifier
    else:
        assert ruling.stop is not None and ruling.stop.reason_code != vs.REASON_NO_PRODUCTION_OWNER


def test_without_an_exact_write_scope_nobody_is_repaired(tmp_path):
    """Unchanged: the accepted verifier can only be write-denied under an allow-list scope; anything else repairs nobody."""
    ruling, state, _unit_state = _ruling(tmp_path, scope=WriteScopeMode.UNRESTRICTED)
    assert ruling.owner is None and ruling.stop.reason_code == vs.REASON_NO_PRODUCTION_OWNER
    assert PARSER in ruling.stop.detail and state.environment_failure is not None


# ------------------------------------------------------------------------------------------- the locator rules are what they were

def test_a_locator_that_names_a_production_file_still_decides_by_itself(tmp_path):
    output = ("java.lang.NullPointerException\n\tat ProtocolParser.decode(ProtocolParser.java:47)\n"
              "\tat ProtocolParserTest.testEmptyBody(ProtocolParserTest.java:34)\n")
    ruling, _state, _unit_state = _ruling(tmp_path, plan=_plan(test_depends_on=[]), output=output)
    assert (ruling.owner.owner_id, ruling.owner.files, ruling.owner.corroborated) == ("s4", (PARSER,), False)
    assert retry_strategy._semantic_attribution(ruling).tier == "locator"


def test_no_locator_at_all_is_not_this_case_and_selects_nobody(tmp_path):
    ruling, _state, _unit_state = _ruling(tmp_path, output="assertion failed\n")
    assert _why_nobody(ruling) == "PRODUCTION_OWNER_LOCATOR_ABSENT"


def _predicate(located: List[str], corroborated: List[str], *, owners: Any = None, **over: Any) -> vs.OwnerAuthority:
    owners = owners or {PROTOCOL: ["s3"], PARSER: ["s4"], TEST: ["s6"]}
    return vs.production_owner_authority(
        executed_failure=True, located_paths=located, verifier_paths=[TEST], is_workspace_artifact=lambda path: path != "gone.java",
        owners_of=lambda path: owners.get(path, []), corroborated_paths=corroborated, **over)


def test_the_predicate_uses_corroboration_only_for_a_verifier_only_locator_and_keeps_every_other_rule():
    selected = _predicate([TEST], [PARSER])
    assert (selected.reason_code, selected.owner_id, selected.files, selected.corroborated) == (vs.OWNER_SELECTED, "s4", (PARSER,), True)
    assert _predicate([TEST], []).reason_code == vs.OWNER_LOCATOR_VERIFIER_ONLY
    assert _predicate([TEST], [TEST]).reason_code == vs.OWNER_LOCATOR_VERIFIER_ONLY               # a verifier corroborates nothing
    assert _predicate([], [PARSER]).reason_code == vs.OWNER_LOCATOR_ABSENT                        # no locator: not this case
    assert _predicate([TEST], [PARSER], ambiguous_locators=["Parser.java"]).reason_code == vs.OWNER_LOCATOR_AMBIGUOUS
    assert _predicate([TEST], [PROTOCOL, PARSER]).reason_code == vs.OWNER_AMBIGUOUS               # two owners
    assert _predicate([TEST], ["gone.java"]).reason_code == vs.OWNER_LOCATOR_EXTERNAL             # not a workspace member
    assert _predicate([TEST], ["src/main/java/Orphan.java"]).reason_code == vs.OWNER_UNOWNED      # no plan owner
    assert _predicate([TEST], [PARSER], conflicting_owner_ids=["s3"]).reason_code == vs.OWNER_CONFLICTING
    assert _predicate([TEST], [PARSER], owners={PARSER: ["s4", "s9"], TEST: ["s6"]}).reason_code == vs.OWNER_AMBIGUOUS
    located = _predicate([PARSER, TEST], [PROTOCOL])                                              # a real locator: corroboration unused
    assert (located.owner_id, located.files, located.corroborated) == ("s4", (PARSER,), False)
    not_executed = vs.production_owner_authority(executed_failure=False, located_paths=[TEST], verifier_paths=[TEST],
                                                 is_workspace_artifact=lambda path: True, owners_of=lambda path: ["s4"],
                                                 corroborated_paths=[PARSER])
    assert not_executed.reason_code == vs.OWNER_NO_EXECUTED_FAILURE


# ------------------------------------------------- the whole loop: valid failing test -> eligible owner -> bounded repair -> the SAME test
#
# Owner ruling 2026-11-19 ("restore the normal coding repair loop"). The selection above is made by the REAL failure seam on the sealed
# files; what it selects is then carried, exactly as retry_strategy carries any deterministic attribution, into the REAL enforce loop
# (the scripted-unit harness of tests/test_enforce_owner_recovery_verifier_replay_001.py). Only the two units' own work is scripted:
# s4 writes the parser text it is given; s6 "runs" the test - it fails while the sealed defect line is in the parser, and passes
# without it. Nothing is scripted about who is reopened, with which write scope, or which test bytes are run again.

DEFECT_LINE = "        byte[] body = null;\n"
REPAIRED_LINE = "        byte[] body = new byte[0];\n"


def _enforce_plan() -> EngineeringPlan:
    return EngineeringPlan(plan_id="run4shape", kind=ChangeKind.TASK, subtasks=[
        Subtask(id="s4", description="create the parser", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=PARSER, action=FileAction.CREATE)], provides=["protocol-parser"], verification=[COMPILE]),
        Subtask(id="s6", description="create the JUnit test", execution_method=ExecutionMethod.MODEL, depends_on=["s4"],
                planned_files=[PlannedFile(path=TEST, action=FileAction.CREATE)], requires=["protocol-parser"], verification=[RUN_TESTS]),
    ])


def _conflict_from_the_real_ruling(tmp_path: Path) -> Dict[str, Any]:
    """The scope conflict of unit s6, built the way retry_strategy builds it from an attribution - here the attribution of the REAL
    ruling on the sealed run-4 failure (so the selection that feeds the loop is the one the product makes, not one written by hand)."""
    ruling, _state, _unit_state = _ruling(tmp_path)
    attribution = retry_strategy._semantic_attribution(ruling)
    output = _sealed("s6_test_failure.output.txt")
    return {"reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "classification": "PLAN_SCOPE_DEFECT", "failure_type": "targeted_test",
            "required_files": list(attribution.files), "allowed_files": [TEST], "attribution_tier": attribution.tier,
            "reason": attribution.reasoning, "raw_evidence": grounded_evidence_excerpt(output, attribution.files)}


class Run4Shape:
    """The unit boundary of the enforce loop for the run-4 shape."""

    def __init__(self, parser_texts: List[str], conflict: Dict[str, Any]) -> None:
        self.parser_texts, self.conflict = list(parser_texts), conflict
        self.calls: List[Dict[str, Any]] = []
        self.generated: List[str] = []          # every test text a Developer generation produced
        self.replayed: List[Any] = []

    async def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        workspace = Path(kwargs["workspace_path"])
        if kwargs["current_subtask_id"] == "s4":
            path = workspace / PARSER
            path.parent.mkdir(parents=True, exist_ok=True)
            text = self.parser_texts.pop(0)
            changed = not path.exists() or path.read_text(encoding="utf-8") != text
            path.write_text(text, encoding="utf-8")
            return {"status": "success", "quality_gates_passed": True, "files": [PARSER] if changed else [],
                    "verification_results": [COMPILE_EVIDENCE],
                    "gate_outcomes": [{"type": "compile", "success": True, "attempt": 1}, dict(ZERO_TESTS)]}
        handle = kwargs.get("carried_candidate")
        if handle is not None:
            self.replayed.append(handle)
            test_text = handle.candidate.files[0].content
        else:
            test_text = _sealed("ProtocolParserTest.java")
            self.generated.append(test_text)
        if DEFECT_LINE not in (workspace / PARSER).read_text(encoding="utf-8"):
            (workspace / TEST).parent.mkdir(parents=True, exist_ok=True)
            (workspace / TEST).write_text(test_text, encoding="utf-8")
            return {"status": "success", "quality_gates_passed": True, "files": [TEST], "verification_results": [TESTS_EVIDENCE]}
        output = _sealed("s6_test_failure.output.txt")
        result: Dict[str, Any] = {"status": "failed", "quality_gates_passed": False, "files": [TEST],
                                  "last_failure": {"type": "targeted_test", "message": output, "raw_output": output},
                                  "plan_scope_conflict": dict(self.conflict)}
        if kwargs.get("capture_refused_candidate"):
            result["refused_candidate"] = RefusedCandidate(
                files=(CarriedFile(TEST, test_text, raw_digest(test_text.encode("utf-8"))),), binding_digest="binding",
                failure_type="targeted_test", failure_digest=verifier_replay.failure_identity("targeted_test", output))
        return result

    def units(self) -> List[str]:
        return [call["current_subtask_id"] for call in self.calls]

    def roles(self) -> List[str]:
        return [call["execution_scope"].split("role=")[1] for call in self.calls]


def test_the_evidence_the_reopened_owner_is_shown_contains_the_confirmed_assertion(tmp_path):
    conflict = _conflict_from_the_real_ruling(tmp_path)
    assert (conflict["required_files"], conflict["allowed_files"], conflict["attribution_tier"]) == ([PARSER], [TEST], "subtask_dependency")
    assert "ProtocolParserTest.testEmptyBody" in conflict["raw_evidence"] and "actual array was <null>" in conflict["raw_evidence"]
    assert "bounded repair candidate, not a proven root cause" in conflict["reason"]


# the harness opt-ins of a scripted enforce run asserted to end SUCCESS (tests/conftest.py PRE_S6_S7_SCRIPTED_RUN_FILES lists the twin file)
_scripted_run = pytest.mark.usefixtures("requirements_closed", "complete_change_approved")


@pytest.fixture
def run4_conflict(tmp_path):
    """The real ruling's scope conflict, made BEFORE the async test's event loop exists: the seam helper drives its coroutine with
    asyncio.run, which cannot be called from inside a running loop (owner run 2026-11-19, c5-prep/check.v2: both loop tests failed on
    exactly that, in the test's own setup, before any product code of the loop ran). It is run in a thread of its own, which has no
    event loop at all, so it neither depends on nor disturbs the loop the async test is given."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(_conflict_from_the_real_ruling, tmp_path / "seam").result()


@_scripted_run
@pytest.mark.asyncio
async def test_run4_shape_s4_alone_repairs_production_and_the_unchanged_accepted_test_is_run_again(tmp_path, run4_conflict):
    defective = _sealed("ProtocolParser.java")
    assert defective.count(DEFECT_LINE) == 1
    repaired = defective.replace(DEFECT_LINE, REPAIRED_LINE)
    accepted_test = _sealed("ProtocolParserTest.java")
    shape = Run4Shape([defective, repaired], run4_conflict)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await _enforce(workspace, shape, _enforce_plan())

    assert shape.units() == ["s4", "s6", "s4", "s6"]
    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay"]
    # s4, and only s4, is reopened - with exactly its own file; s6 never gets the parser in its write scope
    assert list(shape.calls[2]["allowed_write_relpaths"]) == [PARSER]
    assert all(list(call["allowed_write_relpaths"]) == [TEST] for call in shape.calls if call["current_subtask_id"] == "s6")
    # the SAME accepted test is run again: carried byte for byte, never generated a second time
    assert shape.generated == [accepted_test] and len(shape.replayed) == 1
    handle = shape.replayed[0]
    assert [(carried.path, carried.content) for carried in handle.candidate.files] == [(TEST, accepted_test)]
    assert handle.expected_digests() == {TEST: raw_digest(accepted_test.encode("utf-8"))} and handle.unit_id == "s6"
    # and the run ends on that test passing against the repaired parser
    assert result.legacy_result["status"] == "success", result.legacy_result.get("error")
    assert _statuses(result) == {"s4": "completed", "s6": "completed"}
    assert [event["owner_recovery_passed"] for event in result.legacy_result["plan_recovery_events"]] == [True]
    assert (workspace / TEST).read_text(encoding="utf-8") == accepted_test
    assert (workspace / PARSER).read_text(encoding="utf-8") == repaired


@_scripted_run
@pytest.mark.asyncio
async def test_run4_shape_a_repair_that_leaves_the_defect_never_ends_green_and_the_test_is_never_rewritten(tmp_path, run4_conflict):
    """Eligibility is not proof and a repair attempt is not a result: s4 changes the file each time without removing the defect, the
    unchanged test fails each replay, and nothing - not a different test, not the owner's own gates - turns the run green."""
    defective = _sealed("ProtocolParser.java")
    attempts = [defective, defective + "// attempt 1\n", defective + "// attempt 2\n", defective + "// attempt 3\n"]
    shape = Run4Shape(attempts, run4_conflict)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await _enforce(workspace, shape, _enforce_plan())

    assert result.legacy_result["status"] != "success"
    assert len(shape.generated) == 1 and len(shape.replayed) >= 1                       # one test, replayed; never a second one
    assert all(handle.candidate.files[0].content == _sealed("ProtocolParserTest.java") for handle in shape.replayed)
    assert all(list(call["allowed_write_relpaths"]) == [PARSER] for call in shape.calls if call["current_subtask_id"] == "s4")
    assert not (workspace / TEST).exists()                                              # a failing verifier never reaches the workspace
