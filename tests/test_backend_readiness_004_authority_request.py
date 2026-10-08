"""BACKEND-READINESS-004 (owner decision D2): typed authority requests - kriya/workflow/authority_request.py.

A VERIFICATION_AUTHORITY_REQUIRED refusal carries one request per residual (requirement, claim): what must be proven,
the evidence already bound and why it is insufficient, the acceptable authorities cheapest-first, how to supply each,
and UNSEALED skeletons (empty why/reason). Sealed under the state root, on the result and as a run event on both
execution paths; printed by the CLI. A request proposes authority and authorizes nothing - no closer reads it.
"""
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from kriya.config.config import AppConfig
from kriya.workflow import authority_request as ar
from kriya.workflow import requirement_disposition as rd
from kriya.workflow.contract_compilation import (
    AUTHORITY_ACCEPTANCE_APPROVAL,
    AUTHORITY_EXTERNAL_COMMAND,
    AUTHORITY_OPERATOR_DISPOSITION,
    compile_verification_contract,
)
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_GENERAL,
    REGRESSION_PRESERVATION,
    VerificationAuthorityRequired,
    derive_requirements,
    statement_origins,
)
from tests._strict_doubles import strict_kernel
from tests.test_fs1_false_success_reproducer import PRODUCTION, _run
from tests.test_fs1c1_requirement_claims import LEGACY, WRONG_BEHAVIOUR_GOAL
from tests.test_prd020_requirement_lineage import _engine, _events, _git_base, _verdicts_json
from tests.test_verification_contract_003_authority import _workspace

GOAL = ("Entries must leave the cache at the expiry instant for every ttl; tests/test_a.py must still pass.\n\n"
        "The public API must stay unchanged.\n\n"
        "lower('ABC') -> 'abc'\n")


def _contract(goal=GOAL, language=None):  # an unknown language: no API predicate, the API claim stays residual
    reqs = derive_requirements(goal)
    return reqs, compile_verification_contract(reqs, origins=statement_origins(goal), test_files=["tests/test_a.py"],
                                               project_language=language)


def test_01_one_request_per_residual_claim_with_bound_evidence_and_cheapest_first_authorities():
    reqs, contract = _contract()
    refusal = contract.refusal()
    assert isinstance(refusal, VerificationAuthorityRequired)
    requests = refusal.authority_requests
    expected = [(e.requirement_id, r.claim) for e in contract.entries for r in e.residual]
    assert [(r.requirement_id, r.claim) for r in requests] == expected and ("REQ-1", BEHAVIOR) in expected
    assert ("REQ-2", API_PRESERVATION) in expected
    general = requests[0]
    assert general.strength == BEHAVIOR_GENERAL and "every input" in general.must_prove
    # the named-test closer is bound to the statement's other claim and says what it proves
    assert [(e["claim"], e["closer"]) for e in general.existing_evidence] == [(REGRESSION_PRESERVATION, "named_tests")]
    assert "proves other claims of this statement only" in general.why_insufficient and "B2-COV" in general.why_insufficient
    assert general.cheapest_safe_closer.startswith(AUTHORITY_EXTERNAL_COMMAND)
    assert general.acceptable_authorities[-1].startswith(AUTHORITY_OPERATOR_DISPOSITION)
    assert set(general.how_to_supply) == {AUTHORITY_EXTERNAL_COMMAND, AUTHORITY_ACCEPTANCE_APPROVAL, AUTHORITY_OPERATOR_DISPOSITION}
    assert "--verification-authority" in general.how_to_supply[AUTHORITY_EXTERNAL_COMMAND]
    api = next(r for r in requests if r.claim == API_PRESERVATION)
    assert api.existing_evidence == () and "no deterministic evidence is bound" in api.why_insufficient
    assert "public signature" in api.must_prove and api.cheapest_safe_closer.startswith(AUTHORITY_EXTERNAL_COMMAND)
    # the EXACT example statement (REQ-3) has its own closer on a Python project and no request at all
    reqs_py, python = _contract(language="python")
    assert ("REQ-2", API_PRESERVATION) not in [(r.requirement_id, r.claim) for r in python.refusal().authority_requests]
    # the EXACT example statement: its cheapest closer is the goal's own compiled example (bound by the run, not here)
    example = next(r for r in python.refusal().authority_requests if r.text.startswith("lower("))
    assert example.strength == BEHAVIOR_EXACT and example.cheapest_safe_closer.startswith("goal_examples")
    # an admitted contract has no requests (the goal's example bound as its own authority)
    from kriya.workflow.contract_compilation import ExternalAuthority
    exact = "lower('ABC') -> 'abc'\n"
    examples = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_EXACT}}})
    admitted = compile_verification_contract(derive_requirements(exact), origins=statement_origins(exact), test_files=[],
                                             external_authorities=[examples], project_language="python")
    assert admitted.refusal() is None and ar.authority_requests(admitted) == []


def test_02_skeletons_are_unsealed_and_a_request_authorizes_nothing():
    reqs, contract = _contract()
    requests = contract.refusal().authority_requests
    general = requests[0]
    api = next(r for r in requests if r.claim == API_PRESERVATION)
    assert general.coverage_skeleton == {"requirement_id": "REQ-1", "requirement_text_sha256": general.coverage_skeleton["requirement_text_sha256"],
                                         "claim": BEHAVIOR, "accepted_strength": BEHAVIOR_GENERAL, "accept_as_sufficient": True, "why": ""}
    assert api.coverage_skeleton["accepted_strength"] is None and api.disposition_skeleton["disposition"] == "" \
        and api.disposition_skeleton["reason"] == ""
    assert general.to_dict()["authorizes"].startswith("nothing")
    # the skeleton's text digest is the one every sealed artifact must carry
    assert general.coverage_skeleton["requirement_text_sha256"] == rd.text_sha256(reqs.get("REQ-1").text)
    # pure: the same contract gives the same requests; the refusal result carries them
    assert [r.to_dict() for r in ar.authority_requests(contract)] == [r.to_dict() for r in contract.refusal().authority_requests]
    assert contract.refusal().to_dict()["authority_requests"][0]["claim"] == BEHAVIOR


def test_03_requests_are_sealed_content_addressed_by_the_contract_digest(tmp_path):
    reqs, contract = _contract()
    requests = contract.refusal().authority_requests
    stored = ar.seal_authority_requests(contract, requests, str(tmp_path / "state"))
    assert stored == os.path.join(str(tmp_path / "state"), ar.AUTHORITY_REQUEST_STORE_DIR, f"{contract.digest}.json")
    document = json.loads(open(stored, encoding="utf-8").read())
    assert document["format"] == ar.AUTHORITY_REQUEST_FORMAT and document["contract_digest"] == contract.digest
    assert [r["claim"] for r in document["requests"]] == [r.claim for r in requests]
    assert ar.seal_authority_requests(contract, requests, str(tmp_path / "state")) == stored  # idempotent
    # another state root: the store never lives in the workspace
    ws, base = _workspace(tmp_path)
    assert not os.path.exists(os.path.join(str(ws), ar.AUTHORITY_REQUEST_STORE_DIR))


@pytest.mark.asyncio
async def test_04_the_direct_path_refusal_carries_seals_and_records_the_requests(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _git_base(workspace, {LEGACY: "def test_legacy():\n    assert True\n"})
    res = await engine.run_generation_workflow(goal=WRONG_BEHAVIOUR_GOAL, workspace_path=str(workspace))
    assert res["failure_category"] == "verification_authority_required" and calls["planner"] == []
    [request] = res["authority_requests"]
    assert request["requirement_id"] == "REQ-1" and request["claim"] == BEHAVIOR and request["coverage_skeleton"]["why"] == ""
    assert res["requirements_admission"]["authority_requests"] == res["authority_requests"]
    assert os.path.isfile(res["authority_requests_path"]) and not res["authority_requests_path"].startswith(str(workspace))
    [event] = _events(cfg, "verification_contract.authority_requested")
    assert event["stored_path"] == res["authority_requests_path"] and event["requests"] == res["authority_requests"]


def test_05_the_enforce_path_refusal_carries_and_seals_the_requests(tmp_path, monkeypatch):
    observed = _run(tmp_path, monkeypatch, PRODUCTION)
    legacy = observed.result.legacy_result
    assert legacy["failure_category"] == "verification_authority_required"
    assert legacy["authority_requests"] and all(r["coverage_skeleton"]["why"] == "" for r in legacy["authority_requests"])
    assert {r["requirement_id"] for r in legacy["authority_requests"]} == {row["id"] for row in legacy["requirements_admission"]["residual"]}
    assert legacy["authority_requests_path"] and os.path.isfile(legacy["authority_requests_path"])
    assert not legacy["authority_requests_path"].startswith(str(observed.workspace))


def test_06_the_cli_prints_the_requests_and_binds_a_disposition_only_from_outside_the_workspace(tmp_path, monkeypatch):
    ws, base = _workspace(tmp_path)
    monkeypatch.chdir(ws)
    goal = "CSVFormat.EXCEL produces spurious empty records for blank lines.\nEvery existing test must keep passing unchanged.\n"
    reqs = derive_requirements(goal)
    document = rd.disposition_template(reqs, goal, [("REQ-1", None)], base_revision=base, operator_identity="owner",
                                       issued_at="2026-10-08T10:00:00Z")
    document["dispositions"][0].update(disposition=rd.REJECTED_FALSE_PREMISE, reason="documented behaviour")
    outside = tmp_path / "disposition.json"
    outside.write_text(json.dumps(document))
    inside = ws / "disposition.json"
    inside.write_text(json.dumps(document))
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    refusal = {"status": "failure", "run_id": "r", "quality_gates_passed": False, "files": [],
               "failure_category": "verification_authority_required", "environment_failure": "VERIFICATION_AUTHORITY_REQUIRED: x",
               "error": "x", "reason_codes": ["VERIFICATION_AUTHORITY_REQUIRED"], "requirements_admission": {"residual": []},
               "authority_requests": [{"requirement_id": "REQ-1", "claim": BEHAVIOR, "strength": BEHAVIOR_EXACT,
                                       "must_prove": "the exact outcome", "why_insufficient": "no evidence",
                                       "cheapest_safe_closer": "acceptance_file", "acceptable_authorities": ["acceptance_file"]}],
               "authority_requests_path": str(tmp_path / "state" / "authority-requests" / "abc.json")}

    def invoke(args, result):
        dispatch = AsyncMock(return_value=result)
        with patch("kriya.cli.load_config", return_value=cfg), patch("kriya.cli.LLMClient", autospec=True) as llm, \
             patch("kriya.cli.Kernel", return_value=strict_kernel(cfg)), patch("kriya.cli.WorkflowEngine") as engine, \
             patch("kriya.cli._learned_reference_context", new=AsyncMock(return_value="")), \
             patch("kriya.cli._dispatch_generation", new=dispatch):
            outcome = CliRunner().invoke(__import__("kriya.cli", fromlist=["main"]).main, ["generate", goal, "-y", *args])
        return outcome, engine.return_value, dispatch, llm

    outcome, engine, dispatch, _ = invoke(["--requirement-disposition", str(outside)],
                                          {"status": "success", "run_id": "r", "quality_gates_passed": True})
    assert outcome.exit_code == 0, outcome.output
    assert isinstance(engine.requirement_dispositions, rd.RequirementDispositions) and dispatch.await_count == 1
    assert "Requirement disposition bound" in outcome.output and "REQ-1=REJECTED_FALSE_PREMISE" in outcome.output
    outcome, _, dispatch, llm = invoke(["--requirement-disposition", str(inside)], {"status": "success"})
    assert outcome.exit_code == 1 and "DISPOSITION_INVALID" in outcome.output and dispatch.await_count == 0 and llm.call_count == 0
    outcome, _, _, _ = invoke([], refusal)
    assert outcome.exit_code != 0 and "[AUTHORITY REQUESTS] 1 uncovered claim(s)" in outcome.output
    assert "REQ-1 / BEHAVIOR EXACT: prove the exact outcome" in outcome.output and "cheapest safe closer: acceptance_file" in outcome.output
