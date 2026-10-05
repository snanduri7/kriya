"""P2 PRE-FIX PIN (moved from tests/test_p2_regression_attribution_reproducer.py with the
fix; evidence, run explicitly on the pre-fix revision:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p2/test_p2_prefix_pin.py).
It passed before P2 (handover/evidence/p2/prefix_be07524.txt); its assertions describe the defect."""
import _p2_t4_shape as t4


def test_p2_today_a_candidate_caused_test_compile_regression_is_unattributed_and_never_repaired(
        tmp_path, monkeypatch):
    """Pins today's path (it passes now; its assertions describe the defect)."""
    observed = t4.run(tmp_path, monkeypatch)
    legacy = observed.result.legacy_result
    oracle_calls = observed.extra["oracle"].calls

    # PRE baseline green; the candidate compiles; the full regression's test compile fails.
    tests_runs = [c for c in oracle_calls if c["goals"][0] == "test"]
    assert tests_runs[0]["verdict"] == "green" and tests_runs[-1]["verdict"] == "ambiguous"
    assert legacy["status"] != "success"
    [closed] = [r["payload"] for r in observed.of("unit.closed")]
    assert closed["failure_category"] == "regression_unattributed"
    # Kriya had the compiler's locator (file, line, the conflicting new method) ...
    [regression] = [r for r in observed.of("mirror.gate_outcome") if r["payload"]["type"] == "regression_unattributed"]
    outcome = t4.blob_json(observed, regression, "outcome")
    assert "ArrayFillTest.java:[167,40] reference to fill is ambiguous" in outcome["output"]
    assert "fill(int[],int,int,int) in org.apache.commons.lang3.ArrayFill" in outcome["output"]
    # ... but reported nothing attributable, stopped, and repaired nothing.
    assert outcome["file_locations"] == [] and outcome["likely_files"] == []
    [diagnosis] = [r["payload"] for r in observed.of("diagnosis") if r["payload"]["type"] == "regression_unattributed"]
    assert diagnosis["evidence_class"] == "UNKNOWN" and diagnosis["file_locations"] == []
    decisions = [r["payload"] for r in observed.of("recovery.decision")]
    assert decisions[-1]["action"] == "stop_environment"
    assert decisions[-1]["stop_reason_code"] == "REGRESSION_UNATTRIBUTED"
    assert not any(t4.TEST in (r["payload"].get("authorized_write_scope") or [])
                   for r in observed.of("authority.snapshot"))            # no request could repair the test
    assert [r["payload"]["unit_id"] for r in observed.of("unit.opened")] == ["s1"]   # s2 never ran
    assert t4.workspace_text(observed, t4.MAIN) == t4.BASE_MAIN           # nothing applied
    assert t4.workspace_text(observed, t4.TEST) == t4.BASE_TEST
