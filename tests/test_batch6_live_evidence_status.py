"""The Batch 6 live evidence writer must never record a skipped case as
verified: a SKIP is NOT_LIVE_EXERCISED, a failure is FAILED, and only a case
whose body completed is LIVE_EXERCISED."""
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

