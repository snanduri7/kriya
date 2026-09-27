"""PRD-033: the three typed events the metrics need, and the `kriya metrics`
CLI.

Each event is proven end to end: the real pipeline records it, the real
TraceLogger persists it, and the real deriver counts it from the trace row.
The success path and the normal-review path must not record it.
"""
import json
import os
from pathlib import Path

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
    inject_after_static_analysis_gate,
    run_direct,
)
from _fake_inference_runtime import FakeServerError
from click.testing import CliRunner

from kriya.cli import main
from kriya.core.state_paths import trace_db_path
from kriya.core.trace import TraceLogger
from kriya.metrics import adjudication as adj
from kriya.metrics.derive import derive_metrics
from kriya.metrics.evidence import load_trace_runs

GOAL = "add sub to calc.py"


def _generation(cfg):
    return derive_metrics(load_trace_runs(trace_db_path(cfg)))["outcomes"]["generation"]


def _events(result_runs, kind):
    return [details for run in result_runs for details in run.events_of(kind)]


def _runtime(reviewer=None):
    def responder(role, request):
        if role == "developer":
            return CALC_WITH_SUB
        if role == "reviewer" and reviewer is not None:
            return reviewer(request)
        return benign_roles(role, request)
    return ChaosRuntime(responder)


def test_a_commit_stop_is_a_typed_trace_event_and_a_metric(tmp_path, monkeypatch):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    inject_after_static_analysis_gate(monkeypatch, lambda state: Path(workspace, "calc.py").write_text(CALC + "#\n"))
    cfg = chaos_config()
    with RuntimeRegistration(_runtime()):
        result = run_direct(chaos_engine(cfg), GOAL, workspace)
    assert result["failure_category"] == "workspace_commit_failed"
    runs = load_trace_runs(trace_db_path(cfg))
    [event] = _events(runs, "workspace_commit.failed")
    assert event == {"reason_code": "WORKSPACE_REVISION_CONFLICT", "workspace_state": "UNCHANGED"}
    assert _generation(cfg)["workspace_commit_failures"] == {"WORKSPACE_REVISION_CONFLICT": 1}


def test_a_successful_run_records_no_commit_stop_approval_or_unattached_review(tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config()
    with RuntimeRegistration(_runtime()):
        assert run_direct(chaos_engine(cfg), GOAL, workspace)["quality_gates_passed"] is True
    runs = load_trace_runs(trace_db_path(cfg))
    for kind in ("workspace_commit.failed", "approval.decision", "review.pre_approval_unattached"):
        assert _events(runs, kind) == []
    assert _generation(cfg)["human_escalations"]["status"] == "UNAVAILABLE"


def _approval_run(tmp_path, *, decision, reviewer=None, name="ws"):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB}, name=name)
    cfg = chaos_config(mode="human-in-the-loop")
    seen = []

    def callback(diffs, reason):
        seen.append(reason)
        return decision

    with RuntimeRegistration(_runtime(reviewer)):
        result = run_direct(chaos_engine(cfg), GOAL, workspace,
                            **({"approval_callback": callback} if decision is not None else {}))
    return cfg, result, seen


def test_every_approval_decision_is_recorded_with_its_triggers(tmp_path):
    passed = {}
    for decision, name in ((True, "approved"), (False, "rejected"), (None, "unavailable")):
        cfg, result, _ = _approval_run(tmp_path / name, decision=decision)
        passed[name] = result["quality_gates_passed"]
    # One trace store per test: the three runs' decisions, in order.
    events = _events(load_trace_runs(trace_db_path(cfg)), "approval.decision")
    assert sorted(e["outcome"] for e in events) == ["approved", "rejected", "unavailable"]
    assert all(e["triggers"] == ["human_in_the_loop"] for e in events)
    assert passed == {"approved": True, "rejected": False, "unavailable": False}
    assert _generation(cfg)["human_escalations"] == {"approved": 1, "rejected": 1, "unavailable": 1}


def test_an_unattached_pre_approval_review_is_recorded_and_shown_to_the_approver(tmp_path):
    calls = []

    def refuse(request):
        # Only the pre-approval review fails; the post-commit final review
        # answers (a failing final review is registry FINAL-REVIEW-BACKEND-ERROR-001).
        calls.append(request)
        return FakeServerError("review endpoint gone") if len(calls) == 1 else "Review: Approved"

    cfg, result, seen = _approval_run(tmp_path, decision=True, reviewer=refuse)
    [event] = _events(load_trace_runs(trace_db_path(cfg)), "review.pre_approval_unattached")
    assert event["reason_code"]
    assert result["review_included_in_approval"] is False
    assert "NOT ATTACHED" in seen[0] and event["reason_code"] in seen[0]
    assert _generation(cfg)["pre_approval_review_unattached"]["value"] == 1


def test_a_normal_pre_approval_review_is_attached_and_records_nothing(tmp_path):
    cfg, result, seen = _approval_run(tmp_path, decision=True, reviewer=lambda request: "Review: Approved, looks correct")
    assert result["review_included_in_approval"] is True
    assert "=== Automated Code Review ===" in seen[0] and "NOT ATTACHED" not in seen[0]
    assert _events(load_trace_runs(trace_db_path(cfg)), "review.pre_approval_unattached") == []


# --- CLI ------------------------------------------------------------------------

def _cli(args, cwd, env):
    runner = CliRunner()
    previous = os.getcwd()
    os.chdir(cwd)
    try:
        return runner.invoke(main, args, env=env, catch_exceptions=False)
    finally:
        os.chdir(previous)


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv(adj.ENV_HOME_OVERRIDE, str(tmp_path / "trusted" / "adjudications"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    trace_db = Path(os.environ["KRIYA_STATE_DIR"]) / "traces.db"
    TraceLogger(str(trace_db)).log_run(run_id="run-1", goal="g", duration_sec=1.0, attempts=1, status="success",
                                       files_modified=[], generation_metrics={"total_wall_seconds": 1.0})
    return workspace


def test_the_metrics_report_cli_is_deterministic_and_has_no_default_thresholds(tmp_path, monkeypatch):
    workspace = _state(tmp_path, monkeypatch)
    first = _cli(["metrics", "report", "--json"], workspace, None)
    second = _cli(["metrics", "report", "--json", "--out", str(tmp_path / "out")], workspace, None)
    assert first.exit_code == 0 and second.exit_code == 0, first.output
    a, b = json.loads(first.output), json.loads(second.output.split("\n", 1)[1] if second.output.startswith("Wrote") else second.output)
    assert a["content_digest"] == b["content_digest"]
    assert a["content"]["thresholds"] == {"status": "NOT_CONFIGURED"}
    assert (tmp_path / "out" / "metrics-report.json").is_file() and (tmp_path / "out" / "metrics-report.md").is_file()
    assert "legacy_trace_db_present" in a["generated"]


def test_a_failing_threshold_exits_1_and_a_threshold_file_inside_the_workspace_is_refused(tmp_path, monkeypatch):
    workspace = _state(tmp_path, monkeypatch)
    entry = {"metric": "outcomes.generation.final_verified_success", "comparator": "min", "value": 2.0}
    outside = tmp_path / "operator-thresholds.json"
    outside.write_text(json.dumps({"schema_version": 1, "thresholds": [entry]}))
    failed = _cli(["metrics", "report", "--thresholds", str(outside)], workspace, None)
    assert failed.exit_code == 1 and "FAIL" in failed.output
    inside = workspace / "thresholds.json"
    inside.write_text(outside.read_text())
    refused = _cli(["metrics", "report", "--thresholds", str(inside)], workspace, None)
    assert refused.exit_code == 2 and "refused" in refused.output


def test_adjudication_cli_records_refuses_and_lists(tmp_path, monkeypatch):
    workspace = _state(tmp_path, monkeypatch)
    unknown = _cli(["metrics", "adjudicate", "nope", "--verdict", "false_success", "--evidence", "bug 1",
                    "--adjudicator", "qa", "-y"], workspace, None)
    assert unknown.exit_code == 2 and "no traced run" in unknown.output
    declined = _cli(["metrics", "adjudicate", "run-1", "--verdict", "false_success", "--evidence", "bug 1",
                     "--adjudicator", "qa"], workspace, None)
    assert declined.exit_code == 1 and adj.load_adjudications().status == "absent"
    recorded = _cli(["metrics", "adjudicate", "run-1", "--verdict", "false_success", "--evidence", "bug 1",
                     "--adjudicator", "qa", "-y"], workspace, None)
    assert recorded.exit_code == 0, recorded.output
    listing = json.loads(_cli(["metrics", "adjudications", "--json"], workspace, None).output)
    assert [a["verdict"] for a in listing["adjudications"]] == ["false_success"] and listing["status"] == "valid"
    report = json.loads(_cli(["metrics", "report", "--json"], workspace, None).output)
    assert report["content"]["adjudication"]["false_success_rate"]["value"] == 1.0


def test_an_adjudication_store_inside_the_workspace_is_refused(tmp_path, monkeypatch):
    workspace = _state(tmp_path, monkeypatch)
    monkeypatch.setenv(adj.ENV_HOME_OVERRIDE, str(workspace / ".kriya" / "adjudications"))
    refused = _cli(["metrics", "adjudicate", "run-1", "--verdict", "confirmed_success", "--evidence", "ok",
                    "--adjudicator", "qa", "-y"], workspace, None)
    assert refused.exit_code == 2 and not (workspace / ".kriya" / "adjudications").exists()
