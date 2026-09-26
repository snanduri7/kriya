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
