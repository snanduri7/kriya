"""TERMINAL-GATE-BROAD-HANDLER-FALSIFIES-FAILURE-CLASS-001: an unexpected exception inside the terminal original-requirement
verification (the verifier or any of its closers) keeps its authoritative class; it is never reported as a requirements
failure.

MEASURED by the independent TEST-A addendum of 401ea52 (pre-existing at ea56d08): ``TerminalGateService._requirement_gap``
wrapped the verifier and every closer in one ``except Exception`` that returned ``REQUIREMENTS_UNRESOLVED: original requirement
verification failed (<Type>: <msg>)``. An AttributeError in a closer therefore ended the run ``status=failed`` with a
requirements gap and no failure type; the true class survived only in the prose. The same error raised outside the handler is
``status=error`` / ``failure_category=AttributeError`` (the enforce controller's BaseException branch records and re-raises).

TRACED: a genuine requirement failure never relies on that handler - the verifier returns its deliberate failures as findings
(an unavailable verdict, a PRD-016 size refusal, invented ids), the spec-compliance agent turns a provider failure into
``status: unknown`` itself, and ``blocking_requirements`` decides the gap - so what reached the handler was only unexpected.

Everything here runs the REAL enforce route (WorkflowController.execute(..., migration_mode="enforce") ->
TerminalGateService.run -> _requirement_gap -> the real closers) through the harness of
test_taf3_enforce_terminal_refusal (no new patch sites). The typed-refusal control is that file's
``test_a_closers_real_refusal_ends_the_enforce_run_as_the_security_refusal``; the unit-level controls are in
test_prd030_terminal_services.py.
"""
from unittest.mock import AsyncMock

import pytest
from _strict_doubles import strict_config
from test_taf3_enforce_terminal_refusal import _enforce, _enforce_row, _engine, _repo

from kriya.workflow import requirement_closers


def _raising_closer(error: BaseException):
    def closer(*_args, **_kwargs):
        raise error
    return closer


def _attribute_error():
    try:
        return None.missing_attribute          # a plain coding defect inside a closer
    except AttributeError as error:
        return error


@pytest.mark.parametrize("error", [_attribute_error(), RuntimeError("closer broke"), OSError("disk vanished")],
                         ids=["AttributeError", "RuntimeError", "OSError"])
@pytest.mark.asyncio
async def test_an_unexpected_exception_in_a_terminal_closer_keeps_its_class_end_to_end(tmp_path, monkeypatch, error):
    """The final authoritative result is the exception's own class: the run raises it, the .enforce trace row is
    status=error / failure_category=<Type>, nothing is committed - never a REQUIREMENTS_UNRESOLVED gap."""
    repo = _repo(tmp_path, tmp_path / "FILTER_RAN")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    monkeypatch.setattr(requirement_closers, "close_requirements_by_mutation_scope", _raising_closer(error))
    with pytest.raises(type(error)) as raised:
        await _enforce(repo, _engine(config, bind_filter=False))
    assert raised.value is error
    row = _enforce_row(config)
    assert (row["status"], row["failure_category"]) == ("error", type(error).__name__), dict(row)
    assert (repo / "app.py").read_text() == "original\n"            # fail closed: nothing committed


@pytest.mark.asyncio
async def test_control_a_genuine_requirement_failure_is_still_a_requirements_gap_end_to_end(tmp_path):
    """Control: the verifier judges 'Update app.py.' missing (a concrete literal, so VIOLATED) on the final candidate - the
    route a genuine failure really takes. The run ends as the fail-closed REQUIREMENTS gap (status=failed, nothing
    committed), a verdict naming the requirement - not a crash report and not an error."""
    repo = _repo(tmp_path, tmp_path / "FILTER_RAN")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)
    engine.spec_compliance.check = AsyncMock(return_value={
        "requirement_verdicts": [{"id": "REQ-1", "verdict": "missing", "evidence": "app.py is not updated"}]})
    result = await _enforce(repo, engine)
    legacy = result.legacy_result
    gap = legacy.get("global_requirement_gap") or ""
    assert legacy["status"] == "failed", legacy
    assert gap.startswith("REQUIREMENTS_UNRESOLVED:") and "REQ-1" in gap and "verification failed" not in gap, gap
    row = _enforce_row(config)
    assert row["status"] != "error", dict(row)
    assert (repo / "app.py").read_text() == "original\n"            # fail closed: nothing committed
