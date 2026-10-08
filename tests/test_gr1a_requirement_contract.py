"""GR-R1A EXPLICIT REQUIREMENT CONTRACT (kriya/workflow/requirement_contract.py).

An operator-designated, closed requirement set replaces the requirements derived from the goal's sentences; the
goal itself is unchanged and stays the planning context and the goal identity. Synthetic, non-benchmark fixtures.

Measured before the fix (GR-R0, handover/GR_R0_GRAPHIFY_READINESS.md): an issue-report goal derived 22 GENERAL
requirements - headings, a code fence, a table, version notes and the sentence describing the defect itself - every
one a terminal obligation, so no candidate could ever succeed."""
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _b2a_fixtures import CALC_ACCEPTANCE
from click.testing import CliRunner
from test_b2a_acceptance_oracle import _run
from test_b3_human_acceptance import GENERAL_GOAL, _enforce, _repo
from test_prd020_requirement_lineage import _engine, _gates_pass, _verdict_json

from kriya.config.config import AppConfig
from kriya.control.state import ControlState
from kriya.workflow import acceptance_approval as b3
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow import requirement_contract as rc
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    RequirementOutcome,
    blocking_requirements,
    cited_requirement_ids,
    derive_requirements,
    parse_requirement_verdicts,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
)

PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}
ISSUE_GOAL = (
    "# calc: double() returns the wrong value\n\n"
    "## Summary\n\n"
    "Currently double(5) returns 7. The helper adds two instead of multiplying.\n\n"
    "## Reproducer\n\n"
    "```python\nfrom calc import double\nprint(double(5))\n```\n\n"
    "```\npython repro.py\n```\n\n"
    "| input | v1.2 | expected |\n|---|---|---|\n| 5 | 7 | 10 |\n\n"
    "Measured on v1.2. v1.1 behaves identically.\n\n"
    "## Expected\n\n"
    "double(5) returns 10, and double(x) returns 2 * x for any number x.\n"
)
CONTRACT_EXACT = "double(5) returns 10."
CONTRACT_GENERAL = "double(x) returns 2 * x for any number x."
ACCEPTANCE_TWO = (
    "import pytest\n\nfrom calc import double\n\n\n"
    "@pytest.mark.kriya_requirement(\"REQ-1\")\n"
    "def test_double_of_five():\n"
    "    assert double(5) == 10\n\n\n"
    "@pytest.mark.kriya_requirement(\"REQ-2\")\n"
    "def test_double_doubles_any_number():\n"
    "    for x in (-3, 0, 2.5, 11):\n"
    "        assert double(x) == 2 * x\n"
)
# R9: requirement-set digests derived before GR-R1A (c0a3e91), pinned - auto mode must stay byte-identical.
PINNED_AUTO_DIGESTS = {
    "Add a double(x) function to calc/__init__.py that returns 2 * x for any number x.\n":
        "7d6598611c1ab4cf1bba2e53e6f3cec45bea351207f329430ce08e57934ad1a7",
    "Create greeting.py with a greet(name) function.\n- greet returns the text 'Hello, <name>'\n"
    "- Add a DEFAULT_NAME constant set to 'World'\n":
        "613c855ff10fc57554963635faec2c86986b70e8f7ccb04de8c3c4c6144590af",
}


def _contract_file(tmp_path, entries, name="contract.json", fmt=rc.CONTRACT_FORMAT, extra=None):
    document = {"format": fmt, "requirements": entries, **(extra or {})}
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def _two(tmp_path, name="contract.json", second=CONTRACT_GENERAL):
    return _contract_file(tmp_path, [{"id": "REQ-1", "text": CONTRACT_EXACT, "kind": "requirement"},
                                     {"id": "REQ-2", "text": second, "kind": "requirement"}], name=name)


def _load(tmp_path, path, goal=ISSUE_GOAL, workspace=None):
    workspace = workspace or (tmp_path / "ws")
    workspace.mkdir(exist_ok=True)
    return rc.load_requirement_contract(str(path), goal, state_root=str(tmp_path / "state"), workspace=str(workspace))


def _ledger(requirements, verdict=RequirementOutcome.SATISFIED, candidate="cand"):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, requirements)
    record_requirement_verdicts(ledger, requirements, {r.id: (verdict, "model") for r in requirements.requirements},
                                revision=1, evidence_fingerprint=candidate, source="test")
    return ledger


# ---------------------------------------------------------------- R1, R2: exactly the supplied obligations

def test_r1_clean_prose_gives_exactly_the_supplied_obligations(tmp_path):
    goal = "Make double(x) in calc/__init__.py multiply by two.\n"
    contract = _load(tmp_path, _two(tmp_path), goal=goal)
    requirements = rc.requirement_set_for(goal, contract)
    assert [(r.id, r.text, r.kind, r.source) for r in requirements.requirements] == [
        ("REQ-1", CONTRACT_EXACT, "requirement", rc.CONTRACT_SOURCE),
        ("REQ-2", CONTRACT_GENERAL, "requirement", rc.CONTRACT_SOURCE)]
    assert requirements.contract_digest == contract.digest
    assert requirements.digest != derive_requirements(goal).digest


def test_r2_an_issue_report_yields_only_the_contract_and_no_narrative_obligation(tmp_path):
    derived = derive_requirements(ISSUE_GOAL)
    assert len(derived.requirements) > 2  # what auto mode does with the same goal (unchanged)
    contract = _load(tmp_path, _two(tmp_path))
    requirements = rc.requirement_set_for(ISSUE_GOAL, contract)
    assert requirements.ids == ["REQ-1", "REQ-2"]
    narrative = ("## Summary", "Currently double(5) returns 7", "```", "| input", "Measured on v1.2", "# calc")
    assert not any(marker in r.text for r in requirements.requirements for marker in narrative)
    ledger = _ledger(requirements)
    assert sorted(requirement_outcomes(ledger, requirements)) == ["REQ-1", "REQ-2"]
    assert len(blocking_requirements(ledger, requirements, **PRODUCTION)) == 2  # only these two can block


@pytest.mark.asyncio
async def test_r2_the_direct_workflow_judges_and_reports_only_the_contract(tmp_path):
    contract = _load(tmp_path, _two(tmp_path))
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt))
    engine.requirement_contract = contract
    workspace = tmp_path / "ws"
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=ISSUE_GOAL, workspace_path=str(workspace))
    assert sorted(res["requirements"]["outcomes"]) == ["REQ-1", "REQ-2"]
    assert all("REQ-1: " + CONTRACT_EXACT in p and "REQ-3" not in p for p in calls["spec"])
    assert ISSUE_GOAL.strip().splitlines()[0] in calls["planner"][0]  # the raw goal stays the planning context


# ---------------------------------------------------------------- R3: the bug narrative is no obligation

def test_r3_a_correct_candidate_is_not_contradicted_by_the_described_bug(tmp_path):
    goal = "Currently double(5) returns 7.\nExpected: double(5) returns 10.\n"
    assert any("returns 7" in r.text for r in derive_requirements(goal).requirements)  # auto mode, unchanged
    contract = _load(tmp_path, _contract_file(tmp_path, [{"id": "REQ-1", "text": CONTRACT_EXACT,
                                                          "kind": "requirement"}]), goal=goal)
    requirements = rc.requirement_set_for(goal, contract)
    root, _ = _repo(tmp_path, correct=True)
    acceptance_path = tmp_path / "acceptance.py"
    acceptance_path.write_text(CALC_ACCEPTANCE)
    acceptance = ao.load_acceptance(str(acceptance_path), requirements, str(tmp_path / "state"))
    ledger = _ledger(requirements)
    ao.close_requirements_with_acceptance(ledger, requirements, acceptance, test_files=[],
                                          execute=lambda a: _run(a, root, ()), source="test", revision=1)
    assert requirement_outcomes(ledger, requirements) == {"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert not blocking_requirements(ledger, requirements, **PRODUCTION)


# ---------------------------------------------------------------- R4, R10: no candidate or model authority

def test_r4_a_contract_inside_the_workspace_is_refused(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inside = _two(workspace, name="requirements.json")
    with pytest.raises(rc.RequirementContractError) as refused:
        _load(tmp_path, inside, workspace=workspace)
    assert refused.value.reason_code == rc.REQUIREMENT_CONTRACT_INVALID and "outside the workspace" in str(refused.value)


def test_r4_the_bound_set_is_fixed_and_stored_whatever_happens_to_the_source_file(tmp_path):
    path = _two(tmp_path)
    contract = _load(tmp_path, path)
    path.write_text(json.dumps({"format": rc.CONTRACT_FORMAT, "requirements": [
        {"id": "REQ-1", "text": "anything", "kind": "requirement"}]}))
    assert rc.requirement_set_for(ISSUE_GOAL, contract).ids == ["REQ-1", "REQ-2"]
    assert contract.stored_path.startswith(str(tmp_path / "state"))
    assert hashlib.sha256(open(contract.stored_path, "rb").read()).hexdigest() == contract.digest


def test_r10_a_model_proposed_obligation_has_no_authority(tmp_path):
    requirements = rc.requirement_set_for(ISSUE_GOAL, _load(tmp_path, _two(tmp_path)))
    verdicts, findings = parse_requirement_verdicts(
        [{"id": "REQ-1", "verdict": "satisfied"}, {"id": "REQ-2", "verdict": "satisfied"},
         {"id": "REQ-3", "verdict": "missing", "evidence": "a new obligation the model invented"}], requirements)
    assert sorted(verdicts) == ["REQ-1", "REQ-2"] and any("REQ-3" in f for f in findings)
    assert cited_requirement_ids("Step 1 (REQ-1, REQ-3, REQ-9)", requirements) == ["REQ-1"]
    ledger = _ledger(requirements)
    assert sorted(requirement_outcomes(ledger, requirements)) == ["REQ-1", "REQ-2"]


# ---------------------------------------------------------------- R5, R6: binding

def test_r5_a_changed_contract_changes_the_resume_identity(tmp_path):
    from kriya.workflow.resume_fingerprints import ARTIFACT_DEPENDENCIES, generation_resume_fingerprints

    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")

    def fingerprints(**extra):
        return generation_resume_fingerprints(cfg, str(tmp_path), goal=ISSUE_GOAL, **extra)

    first = _load(tmp_path, _two(tmp_path, name="a.json"))
    second = _load(tmp_path, _two(tmp_path, name="b.json", second="double(x) returns x + x for any number x."))
    without = fingerprints()
    bound, changed = (fingerprints(requirement_contract_digest=c.digest) for c in (first, second))
    assert without == fingerprints(requirement_contract_digest=None)  # auto mode: byte-identical
    assert {name for name in without if without[name] != bound[name]} == {"goal"}  # never silently upgraded
    assert bound["goal"] != changed["goal"]
    assert "goal" in ARTIFACT_DEPENDENCIES["candidate"]  # a changed contract regenerates the candidate


def test_r5_the_enforce_control_state_binds_the_contract_and_keeps_old_hashes():
    base = ControlState(schema_version=1, run_id="r")
    assert base.with_updates(requirement_contract_digest=None).content_hash() == base.content_hash()
    bound = base.with_updates(requirement_contract_digest="a" * 64)
    assert bound.content_hash() != base.content_hash()
    assert ControlState.from_dict(bound.to_dict()).requirement_contract_digest == "a" * 64


@pytest.mark.asyncio
async def test_r5_enforce_resume_never_reuses_subtasks_recorded_under_another_contract(tmp_path, monkeypatch):
    from kriya.control.persistence import save_control_state
    from kriya.control.state import CURRENT_SCHEMA_VERSION

    wc, we, workspace, plan, valid = _enforce(tmp_path, monkeypatch, correct=True, approved=False)
    # VERIFICATION-CONTRACT-003: an EXACT contract statement is admitted with the acceptance file alone (a GENERAL
    # one would need the approval); the resume-identity rule under test is unchanged
    we.requirement_contract = _load(tmp_path, _contract_file(tmp_path, [{"id": "REQ-1", "text": CONTRACT_EXACT,
                                                                         "kind": "requirement"}]),
                                    goal=GENERAL_GOAL, workspace=workspace)
    save_control_state(str(workspace), ControlState(
        schema_version=CURRENT_SCHEMA_VERSION, run_id="earlier", subtask_states={"s1": "completed"},
        subtask_completion_scope="workspace", requirement_contract_digest="0" * 64))
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=valid(valid=True))):
        result = await wc.WorkflowController(we).execute(GENERAL_GOAL, str(workspace), migration_mode="enforce",
                                                         resume=True)
    decision = result.legacy_result["resume_decision"]
    assert decision["reason"] == "REQUIREMENT_CONTRACT_CHANGED" and decision["reused_subtasks"] == []
    assert sorted(result.legacy_result["requirements"]["outcomes"]) == ["REQ-1"]


def test_r6_a_changed_goal_invalidates_the_binding(tmp_path):
    contract = _load(tmp_path, _two(tmp_path))
    with pytest.raises(rc.RequirementContractError) as refused:
        rc.requirement_set_for(ISSUE_GOAL + "\nAlso keep the CLI unchanged.\n", contract)
    assert refused.value.reason_code == rc.REQUIREMENT_CONTRACT_GOAL_MISMATCH
    other = _load(tmp_path, _two(tmp_path), goal="Make double multiply.\n")
    assert other.requirement_set.digest != contract.requirement_set.digest  # same file, another goal


# ---------------------------------------------------------------- R7, R8: B2/B3 bind the explicit set

def test_r7_acceptance_naming_a_requirement_outside_the_contract_is_refused(tmp_path):
    requirements = rc.requirement_set_for(ISSUE_GOAL, _load(tmp_path, _two(tmp_path)))
    path = tmp_path / "acceptance.py"
    path.write_text(ACCEPTANCE_TWO.replace('"REQ-2"', '"REQ-3"'))
    with pytest.raises(ao.AcceptanceError) as refused:
        ao.load_acceptance(str(path), requirements, str(tmp_path / "state"))
    assert refused.value.reason_code == ao.ACCEPTANCE_UNKNOWN_REQUIREMENT


def _explicit_b3(tmp_path):
    root, base = _repo(tmp_path, correct=True)
    contract = rc.load_requirement_contract(str(_two(tmp_path)), ISSUE_GOAL, state_root=str(tmp_path / "state"),
                                            workspace=str(root))
    requirements = contract.requirement_set
    path = tmp_path / "acceptance.py"
    path.write_text(ACCEPTANCE_TWO)
    acceptance = ao.load_acceptance(str(path), requirements, str(tmp_path / "state"))
    return root, base, requirements, acceptance


def _approval_path(tmp_path, document, name="approval.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def _approve(document):
    for entry in document["approvals"]:
        entry["accept_suite_as_sufficient"] = True
    return document


def test_r8_an_approval_for_the_explicit_set_binds_it_and_human_accepts(tmp_path):
    root, base, requirements, acceptance = _explicit_b3(tmp_path)
    document = _approve(b3.approval_template(requirements, acceptance, ["REQ-2"], base))
    assert document["format"] == b3.APPROVAL_FORMAT_V2
    assert document["approvals"][0]["requirement_set_sha256"] == requirements.digest
    approval = b3.load_approval(str(_approval_path(tmp_path, document)), requirements, acceptance,
                                state_root=str(tmp_path / "state"), workspace=str(root), base_revision=base)
    ledger = _ledger(requirements, verdict=RequirementOutcome.VIOLATED)  # whatever the model says (GR-R0)
    ao.close_requirements_with_acceptance(ledger, requirements, acceptance, test_files=[],
                                          execute=lambda a: _run(a, root, ()), source="test", revision=1,
                                          approval=approval, base_revision=base)
    assert requirement_outcomes(ledger, requirements) == {"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE,
                                                          "REQ-2": RequirementOutcome.HUMAN_ACCEPTED}


def test_r8_an_approval_made_against_the_derived_set_never_applies_to_the_contract(tmp_path):
    root, base, requirements, acceptance = _explicit_b3(tmp_path)
    legacy = _approve(b3.approval_template(requirements, acceptance, ["REQ-2"], base))
    legacy["format"] = b3.APPROVAL_FORMAT
    legacy["approvals"][0].pop("requirement_set_sha256")
    with pytest.raises(ao.AcceptanceError) as refused:
        b3.load_approval(str(_approval_path(tmp_path, legacy)), requirements, acceptance,
                         state_root=str(tmp_path / "state"), workspace=str(root), base_revision=base)
    assert refused.value.reason_code == b3.ACCEPTANCE_APPROVAL_INVALID and "/2" in str(refused.value)


def test_r8_an_approval_for_another_contract_is_refused_and_closes_nothing(tmp_path):
    root, base, requirements, acceptance = _explicit_b3(tmp_path)
    document = _approve(b3.approval_template(requirements, acceptance, ["REQ-2"], base))
    document["approvals"][0]["requirement_set_sha256"] = "0" * 64  # another explicit set, same words
    with pytest.raises(ao.AcceptanceError) as refused:
        b3.load_approval(str(_approval_path(tmp_path, document)), requirements, acceptance,
                         state_root=str(tmp_path / "state"), workspace=str(root), base_revision=base)
    assert "bound to another requirement set" in str(refused.value)
    entry = b3.ApprovalEntry("REQ-2", b3.text_sha256(CONTRACT_GENERAL), requirements.goal_digest, acceptance.digest,
                             tuple(sorted(acceptance.identities_for("REQ-2"))), b3.runner_contract_digest(acceptance),
                             base, None)
    forged = b3.AcceptanceApproval(digest="f" * 64, stored_path="", source_name="x", entries={"REQ-2": entry})
    assert "another requirement set" in b3.approval_problem(forged, requirements.get("REQ-2"), requirements,
                                                            acceptance, base)


# ---------------------------------------------------------------- R9: auto mode unchanged

@pytest.mark.parametrize("goal, digest", sorted(PINNED_AUTO_DIGESTS.items()))
def test_r9_without_a_contract_the_derived_set_is_byte_identical(goal, digest):
    assert derive_requirements(goal).digest == digest
    assert rc.requirement_set_for(goal, None) == derive_requirements(goal)
    assert "contract_digest" not in derive_requirements(goal).to_dict()


def test_r9_a_format_1_approval_still_binds_a_derived_set(tmp_path):
    root, base = _repo(tmp_path, correct=True)
    requirements = derive_requirements(GENERAL_GOAL)
    path = tmp_path / "acceptance.py"
    path.write_text(CALC_ACCEPTANCE)
    acceptance = ao.load_acceptance(str(path), requirements, str(tmp_path / "state"))
    document = _approve(b3.approval_template(requirements, acceptance, ["REQ-1"], base))
    assert document["format"] == b3.APPROVAL_FORMAT and "requirement_set_sha256" not in document["approvals"][0]
    b3.load_approval(str(_approval_path(tmp_path, document)), requirements, acceptance,
                     state_root=str(tmp_path / "state"), workspace=str(root), base_revision=base)


# ---------------------------------------------------------------- refusals before any model call

@pytest.mark.parametrize("entries, fmt, extra, message", [
    ([], rc.CONTRACT_FORMAT, None, "empty"),
    ([{"id": "REQ-1", "text": "a", "kind": "requirement"}, {"id": "REQ-1", "text": "b", "kind": "requirement"}],
     rc.CONTRACT_FORMAT, None, "duplicate requirement id REQ-1"),
    ([{"id": "REQ-1", "text": "a", "kind": "behavior"}], rc.CONTRACT_FORMAT, None, "unsupported kind"),
    ([{"id": "REQ-1", "text": "a", "kind": "requirement", "priority": 1}], rc.CONTRACT_FORMAT, None, "exactly"),
    ([{"id": "R1", "text": "a", "kind": "requirement"}], rc.CONTRACT_FORMAT, None, "REQ-<n>"),
    ([{"id": "REQ-*", "text": "a", "kind": "requirement"}], rc.CONTRACT_FORMAT, None, "REQ-<n>"),
    ([{"id": "REQ-1", "text": "  ", "kind": "requirement"}], rc.CONTRACT_FORMAT, None, "non-empty"),
    ([{"id": "REQ-1", "text": "a", "kind": "requirement"}], "kriya.requirements/2", None, "expected"),
    ([{"id": "REQ-1", "text": "a", "kind": "requirement"}], rc.CONTRACT_FORMAT, {"merge": True}, "expected"),
])
def test_an_invalid_contract_is_refused(tmp_path, entries, fmt, extra, message):
    with pytest.raises(rc.RequirementContractError) as refused:
        _load(tmp_path, _contract_file(tmp_path, entries, fmt=fmt, extra=extra))
    assert refused.value.reason_code == rc.REQUIREMENT_CONTRACT_INVALID and message in str(refused.value)


def test_the_cli_binds_the_contract_before_any_model_call_and_refuses_an_invalid_one(tmp_path, monkeypatch):
    from _strict_doubles import strict_kernel

    root, base = _repo(tmp_path, correct=True)
    monkeypatch.chdir(root)
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    good, bad = _two(tmp_path), _contract_file(tmp_path, [], name="empty.json")
    acceptance_path = tmp_path / "acceptance.py"
    acceptance_path.write_text(ACCEPTANCE_TWO.replace('"REQ-2"', '"REQ-7"'))

    def invoke(args):
        dispatch = AsyncMock(return_value={"status": "success", "run_id": "r", "quality_gates_passed": True})
        with patch("kriya.cli.load_config", return_value=cfg), patch("kriya.cli.LLMClient", autospec=True) as llm, \
             patch("kriya.cli.Kernel", return_value=strict_kernel(cfg)), patch("kriya.cli.WorkflowEngine") as engine, \
             patch("kriya.cli._learned_reference_context", new=AsyncMock(return_value="")), \
             patch("kriya.cli._dispatch_generation", new=dispatch):
            result = CliRunner().invoke(__import__("kriya.cli", fromlist=["main"]).main,
                                        ["generate", ISSUE_GOAL, "-y", *args])
        return result, engine.return_value, dispatch, llm

    result, engine, dispatch, _ = invoke(["--requirements", str(good)])
    assert result.exit_code == 0, result.output
    assert isinstance(engine.requirement_contract, rc.RequirementContract) and dispatch.await_count == 1
    assert engine.requirement_contract.requirement_set.ids == ["REQ-1", "REQ-2"]
    result, _, dispatch, llm = invoke(["--requirements", str(bad)])
    assert result.exit_code == 1 and "REQUIREMENT_CONTRACT_INVALID" in result.output
    assert dispatch.await_count == 0 and llm.call_count == 0
    result, _, dispatch, llm = invoke(["--requirements", str(good), "--acceptance", str(acceptance_path)])
    assert result.exit_code == 1 and "ACCEPTANCE_UNKNOWN_REQUIREMENT" in result.output and llm.call_count == 0


def test_r8_the_set_digest_binds_the_exact_contract_bytes_not_only_its_entries(tmp_path):
    """Two contract files with the same entries but different bytes are two
    operator decisions: the set digest - and so an approval's
    requirement_set_sha256 - binds the exact contract approved."""
    root, base, requirements, acceptance = _explicit_b3(tmp_path)
    entries = [{"id": "REQ-1", "text": CONTRACT_EXACT, "kind": "requirement"},
               {"id": "REQ-2", "text": CONTRACT_GENERAL, "kind": "requirement"}]
    reformatted = tmp_path / "reformatted.json"
    reformatted.write_text(json.dumps({"format": rc.CONTRACT_FORMAT, "requirements": entries}, indent=4))
    other = rc.load_requirement_contract(str(reformatted), ISSUE_GOAL, state_root=str(tmp_path / "state"),
                                         workspace=str(root)).requirement_set
    assert other.requirements == requirements.requirements
    assert other.digest != requirements.digest
    document = _approve(b3.approval_template(requirements, acceptance, ["REQ-2"], base))
    other_acceptance = ao.load_acceptance(str(tmp_path / "acceptance.py"), other, str(tmp_path / "state"))
    with pytest.raises(ao.AcceptanceError) as refused:
        b3.load_approval(str(_approval_path(tmp_path, document)), other, other_acceptance,
                         state_root=str(tmp_path / "state"), workspace=str(root), base_revision=base)
    assert "bound to another requirement set" in str(refused.value)
