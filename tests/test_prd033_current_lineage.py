"""PRD-033 (Wave 7): the metrics derive correctly from what the CURRENT engine persists.

The metrics package reads persisted trace rows; Waves 3-6 changed result categories, terminal truth, run clocks and the
commit/recovery boundaries. This runs the real engine twice (one verified success, one run whose compile gate rejects
every candidate) with only the model transport scripted, then derives the report from the rows the production TraceLogger
wrote, and checks the typed fields the metrics depend on are present and mean what they say."""
import json
from unittest.mock import patch

from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    run_direct,
)

from kriya.core.state_paths import trace_db_path
from kriya.metrics.evidence import load_trace_runs
from kriya.metrics.report import build_report, render_markdown

GOAL = "add a function sub(a, b) returning a - b to calc.py PROPRIETARY-GOAL-CANARY-31c"


def _runtime():
    return ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))


def test_the_real_engines_persisted_evidence_derives_typed_metrics(tmp_path):
    runtime = _runtime()
    ok = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB}, name="ok")
    bad = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB}, name="bad")
    with RuntimeRegistration(runtime):
        engine = chaos_engine(chaos_config())
        success = run_direct(engine, GOAL, ok)
        with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
                   return_value={"success": False, "output": "BUILD ERROR: rejected."}):
            failure = run_direct(engine, GOAL, bad)
    assert success["quality_gates_passed"] is True and failure["quality_gates_passed"] is not True

    runs = load_trace_runs(str(trace_db_path(chaos_config())))
    report = build_report(runs, generated={"at": "t"})
    content = report["content"]
    block = content["outcomes"]["generation"]
    assert block["runs"] == 2
    assert block["final_verified_success"] == {"status": "MEASURED", "value": 0.5, "numerator": 1, "denominator": 2}
    # the typed failure category the run reported is the one the metrics tally (not a guess, not the success)
    assert block["failure_categories"] == {failure["failure_category"]: 1} and failure["failure_category"]
    # first-pass compile: the success passed its first compile gate, the failure did not
    assert block["first_pass_compile"] == {"status": "MEASURED", "value": 0.5, "numerator": 1, "denominator": 2}
    assert block["llm_calls"]["value"] >= 2 and block["wall_time_seconds"]["status"] == "MEASURED"
    assert block["developer_attempts_per_run"]["status"] == "MEASURED" and block["developer_attempts_per_run"]["value"] >= 1
    # evidence that does not exist is UNAVAILABLE, never zero
    assert block["human_escalations"]["status"] == "UNAVAILABLE"
    assert content["adjudication"]["status"] == "NOT_PROVIDED"
    statement = content["evidence_statement"]
    assert statement["runs"] == {"generation": 2, "enforce": 0} and statement["runtime_identities"]
    assert statement["incidents"]["status"] == "UNAVAILABLE" and "no false-success" in statement["supported"][0]
    # keyed by the exact runtime identity + role + task class; content-free
    assert {row["role"] for row in content["model_protocol"]} >= {"developer"}
    assert all(row["runtime_digest"] and row["inference_settings_digest"] for row in content["model_protocol"])
    assert "PROPRIETARY-GOAL-CANARY" not in json.dumps(report) + render_markdown(report)
    assert build_report(runs, generated={"at": "later"})["content_digest"] == report["content_digest"]   # reproducible
