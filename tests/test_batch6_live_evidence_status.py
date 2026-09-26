"""The Batch 6 live evidence writer must never record a skipped case as
verified: a SKIP is NOT_LIVE_EXERCISED, a failure is FAILED, and only a case
whose body completed is LIVE_EXERCISED. A completed PRD-027 certification
that does not certify is FAILED (live_status and certification_status apart)."""
import json

import pytest
import test_live_prd025_029_batch6 as live


def _status(tmp_path, name):
    with open(tmp_path / name, encoding="utf-8") as stream:
        return json.load(stream)


def test_a_skipped_live_case_is_recorded_not_live_exercised(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "EVIDENCE_DIR", str(tmp_path))
    with pytest.raises(pytest.skip.Exception):
        with live._verdict("case.json") as evidence:
            evidence["observed"] = 1
            pytest.skip("path never reached")
    record = _status(tmp_path, "case.json")
    assert record["status"] == live.NOT_LIVE_EXERCISED
    assert record["reason"] == "path never reached" and record["observed"] == 1


def test_a_failed_and_a_completed_live_case(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "EVIDENCE_DIR", str(tmp_path))
    with pytest.raises(AssertionError):
        with live._verdict("failed.json") as evidence:
            evidence["status"] = live.LIVE_EXERCISED  # a payload key never overrides the verdict
            raise AssertionError("invariant broken")
    assert _status(tmp_path, "failed.json")["status"] == live.LIVE_FAILED
    with live._verdict("passed.json") as evidence:
        evidence["observed"] = 2
    assert _status(tmp_path, "passed.json")["status"] == live.LIVE_EXERCISED


def _identity(window, status):
    from kriya.core import model_qualification as mq
    from kriya.core.model_runtime import ModelRuntimeFingerprint

    fingerprint = ModelRuntimeFingerprint(alias="m", endpoint="http://localhost:11434/v1",
                                          effective_context_window=window)
    reasons = () if status == mq.QUALIFIED else ("no qualification record",)
    return fingerprint, mq.QualificationAssessment(status, "digest", reasons=reasons)


def test_the_live_preflight_rejects_the_unqualified_8k_fixture_identity():
    """The first Batch 6 live run used num_ctx 8192 and an empty qualification
    home (MISSING, default byte bound): both must be named as a fixture error."""
    from kriya.core import model_qualification as mq

    problems = live.live_identity_problems(*_identity(8192, mq.MISSING), 32768)
    assert len(problems) == 2
    assert "8192, expected 32768" in problems[0] and "MISSING" in problems[1]
    assert live.live_identity_problems(*_identity(32768, mq.QUALIFIED), 32768) == []
    assert live.live_identity_problems(*_identity(32768, mq.STALE), 32768)


def test_the_live_cfg_fixture_does_not_override_the_qualified_identity():
    """The window, sampling options and qualification home come from the
    packaged defaults and the operator's real qualification records."""
    import inspect

    source = inspect.getsource(live.cfg)
    for override in ("llm.extra_body", "llm.context_window", "llm.max_tokens", "llm.temperature",
                     "QUALIFICATION_HOME"):
        assert override not in source, override
    assert live.EXPECTED_CONTEXT_WINDOW == 32768


def _suite_passes(repo):
    """The live run's own test gate on the fixture repository."""
    from kriya.config import AppConfig
    from kriya.tools.validate import PolymorphicValidator

    return PolymorphicValidator(str(repo), autonomy_cfg=AppConfig().autonomy).run_tests()["success"]


def test_the_targeted_prd029_fixture_requires_the_authorized_contract_change(tmp_path):
    """Offline proof of the targeted live fixture's design, through the live
    run's own test gate: the user's tests fail on the unchanged API, the
    reference candidate passes and changes the public signature of total,
    the goal alone authorizes exactly that owner and symbol, and Kriya
    derives the registry delta from the code, with checkout.py as the
    invalidated consumer (the model is never told about the registry)."""
    from kriya.workflow.contract_authority import derive_direct_contract_authorizations
    from kriya.workflow.contract_lifecycle import (
        INVALIDATED_BY_CONTRACT_REVISION,
        derive_contract_transition,
        public_api_contract_id,
    )
    from kriya.workflow.file_resolution import _normalized_public_signatures
    from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
    from kriya.workflow.triage import ChangeKind

    base, solved = tmp_path / "base", tmp_path / "solved"
    live.write_targeted_prd029_repo(base)
    live.write_targeted_prd029_repo(solved, live.TARGETED_PRD029_REFERENCE_SOLUTION)
    assert not _suite_passes(base), "the unchanged API must not satisfy the user's tests"
    assert _suite_passes(solved)
    assert _normalized_public_signatures("pricing.py", live.TARGETED_PRD029_FILES["pricing.py"]) != \
        _normalized_public_signatures("pricing.py", live.TARGETED_PRD029_REFERENCE_SOLUTION["pricing.py"])

    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="apply the change", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="pricing.py", action=FileAction.MODIFY)],
    )])
    authorizations = derive_direct_contract_authorizations(live.TARGETED_PRD029_GOAL, plan)
    assert [a.authorization_id for a in authorizations] == ["grounding_goal::pricing.py::total::modify"]

    kwargs = {"workspace_path": str(base), "registry": None, "original_contents": dict(live.TARGETED_PRD029_FILES),
              "final_contents": dict(live.TARGETED_PRD029_REFERENCE_SOLUTION), "transaction_id": "tx",
              "candidate_hash": "c"}
    transition = derive_contract_transition(authorizations=authorizations, downstream_verified=True, **kwargs)
    assert transition.created == (public_api_contract_id("pricing.py"),)
    assert {"consumer": "checkout.py", "contract": public_api_contract_id("pricing.py"), "symbols": ["total"],
            "reason": INVALIDATED_BY_CONTRACT_REVISION} in [dict(i) for i in transition.invalidated_consumers]
    # Without the goal's authorization nothing is recorded as authorized.
    assert derive_contract_transition(authorizations=(), downstream_verified=True, **kwargs) is None


def _certification(certified, precision):
    return {"certified": certified, "precision": precision, "precision_target": 0.5,
            "classes": {"same_class_member": {"passed": True}}}


def test_a_completed_certification_that_does_not_certify_is_failed_not_green(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "EVIDENCE_DIR", str(tmp_path))
    with pytest.raises(AssertionError, match="certification FAILED"):
        with live._verdict("uncertified.json") as evidence:
            live.accept_certification(evidence, _certification(False, 0.4808))
    record = _status(tmp_path, "uncertified.json")
    assert record["status"] == live.LIVE_FAILED
    assert record["live_status"] == live.LIVE_EXERCISED
    assert record["certification_status"] == live.CERTIFICATION_FAILED
    # Only a literal True certifies: a missing or truthy non-bool value does not.
    for value in (None, "true", 1):
        with pytest.raises(AssertionError):
            live.accept_certification({}, _certification(value, 0.6))
    with live._verdict("certified.json") as evidence:
        live.accept_certification(evidence, _certification(True, 0.58))
    record = _status(tmp_path, "certified.json")
    assert (record["status"], record["live_status"], record["certification_status"]) == (
        live.LIVE_EXERCISED, live.LIVE_EXERCISED, live.CERTIFICATION_CERTIFIED)
