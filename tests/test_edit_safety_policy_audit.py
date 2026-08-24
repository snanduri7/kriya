"""MA4.5's own regression requirement, mirroring MA4.3 (core/llm.py) and
MA4.4 (kriya/tools/validate.py): ExecutionPolicy's audit-only integration
into kriya/workflow/edit_safety.py's atomic_write_file must never affect
whether the real write happens, under any condition including a
misconfigured or outright broken policy engine.
"""
from unittest.mock import MagicMock

import kriya.workflow.edit_safety as edit_safety
from kriya.policy.model import PolicyDecision, PolicyResult


def test_write_still_happens_even_though_policy_denies_it_by_default(tmp_path):
    """No workspace_path is passed at this call site, so a write here falls
    through to the default-deny backstop for WRITE_FILE (see
    kriya/policy/execution.py's own MA4.5 docstring) - the real write must
    still succeed regardless."""
    target = tmp_path / "out.txt"
    edit_safety.atomic_write_file(str(target), "hello")
    assert target.read_text() == "hello"


def test_a_broken_policy_engine_never_blocks_the_real_write(tmp_path, monkeypatch):
    monkeypatch.setattr(edit_safety._execution_policy, "evaluate", MagicMock(side_effect=RuntimeError("broke")))
    target = tmp_path / "out.txt"
    edit_safety.atomic_write_file(str(target), "hello")
    assert target.read_text() == "hello"


def test_a_forced_deny_never_blocks_the_real_write(tmp_path, monkeypatch):
    monkeypatch.setattr(edit_safety._execution_policy, "evaluate", MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.DENY, reason_code="TEST_FORCED_DENY", explanation="simulated",
    )))
    target = tmp_path / "out.txt"
    edit_safety.atomic_write_file(str(target), "hello")
    assert target.read_text() == "hello"


def test_audit_call_observes_the_real_target_path(tmp_path, monkeypatch):
    captured = {}
    real_evaluate = edit_safety._execution_policy.evaluate

    def spy(request):
        captured["target"] = request.target
        return real_evaluate(request)

    monkeypatch.setattr(edit_safety._execution_policy, "evaluate", spy)
    target = tmp_path / "out.txt"
    edit_safety.atomic_write_file(str(target), "hello")
    assert captured["target"] == str(target)


def test_sensitive_looking_target_still_writes_since_ma45_is_audit_only(tmp_path):
    """A path matching the sensitive-path pattern (e.g. containing
    'credentials') would DENY at the policy level, but MA4.5 is audit-only -
    the write must still happen."""
    target = tmp_path / "credentials.txt"
    edit_safety.atomic_write_file(str(target), "hello")
    assert target.read_text() == "hello"
