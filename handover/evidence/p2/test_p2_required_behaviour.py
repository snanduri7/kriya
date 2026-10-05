"""P2 required behaviour (evidence; run explicitly:
PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p2/test_p2_required_behaviour.py).
Fails until P2 is fixed; moves into tests/test_p2_regression_attribution_reproducer.py with the fix, replacing
its pin (no xfail in the suite).

The R2 T4 shape: a GREEN baseline, a candidate that compiles, and a full regression whose TEST compilation
fails with a compiler locator in an existing, unchanged test file naming the candidate's new overload.
Required: deterministic attribution of that candidate-caused regression and an authorized repair route -
never REGRESSION_UNATTRIBUTED, never a stop with no repair.
"""
import _p2_t4_shape as t4

from kriya.workflow.attribution import DETERMINISTIC_ATTRIBUTION_TIERS


def test_a_candidate_caused_test_compile_regression_is_attributed_and_repaired(tmp_path, monkeypatch):
    observed = t4.run(tmp_path, monkeypatch)
    decisions = [r["payload"] for r in observed.of("recovery.decision")]

    # 1. Never the unattributed stop.
    assert all(d.get("stop_reason_code") != "REGRESSION_UNATTRIBUTED" for d in decisions)
    assert all(r["payload"]["type"] != "regression_unattributed" for r in observed.of("mirror.gate_outcome"))

    # 2. The regression is grounded deterministically to the compiler's locator.
    regressions = [r["payload"] for r in observed.of("diagnosis") if r["payload"]["type"] == "regression_test"]
    assert regressions, "the candidate-caused full-regression failure must be an ordinary, attributable regression"
    first = regressions[0]
    assert {"filepath": t4.TEST, "line": 167} in [{k: loc[k] for k in ("filepath", "line")}
                                                   for loc in first["file_locations"]]
    assert first["attribution_tier"] in DETERMINISTIC_ATTRIBUTION_TIERS

    # 3. An authorized repair route: a later Developer request may write the broken test file.
    assert any(t4.TEST in (r["payload"].get("authorized_write_scope") or []) for r in observed.of("authority.snapshot"))

    # 4. End to end: the repair is applied and the run succeeds on a green full regression.
    assert observed.result.legacy_result["status"] == "success"
    assert t4.CAST_CALL in t4.workspace_text(observed, t4.TEST)
    assert t4.INT_OVERLOAD.strip() in t4.workspace_text(observed, t4.MAIN)
    assert observed.extra["oracle"].calls[-1]["verdict"] == "green"
