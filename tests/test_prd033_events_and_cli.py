"""PRD-033: the three typed events the metrics need, and the `kriya metrics`
CLI.

Each event is proven end to end: the real pipeline records it, the real
TraceLogger persists it, and the real deriver counts it from the trace row.
The success path and the normal-review path must not record it.
"""
import json
import os
from pathlib import Path

import pytest
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


def _decision_of_the_new_run(runs, known_run_ids):
    """The ``approval.decision`` event of the ONE run that is not in ``known_run_ids`` - chosen by run identity, never by list position (``load_trace_runs`` orders by a one-second
    timestamp and then a random run id, so two runs inside one second come back in an arbitrary order; hosted run 41785193323 failed this test on four different cases)."""
    fresh = [run for run in runs if run.run_id not in known_run_ids]
    assert len(fresh) == 1, [run.run_id for run in fresh]
    [event] = fresh[0].events_of("approval.decision")
    return fresh[0].run_id, event


def test_the_approval_decision_event_names_why_no_decision_could_be_obtained(tmp_path):
    """N1: a person's no (False / DECLINED), a launch that could not ask (UNAVAILABLE) and an approval are told apart by `outcome`; an UNAVAILABLE outcome also carries its closed
    machine-readable `reason_code`. An approval, a decline and a missing callback keep their recorded shape (no `reason_code` key: `outcome` already names them)."""
    from kriya.policy.approval import APPROVAL_REVIEW_EXPIRED, ApprovalOutcome
    cases = {"approved": (True, "approved", None), "declined": (False, "rejected", None), "typed-declined": (ApprovalOutcome.decline(), "rejected", None),
             "expired": (ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED), "unavailable", "APPROVAL_REVIEW_EXPIRED"),
             "garbage": ("yes", "unavailable", "APPROVAL_RESULT_UNRECOGNIZED")}
    known = set()
    for name, (decision, outcome, code) in cases.items():
        cfg, _result, _seen = _approval_run(tmp_path / name, decision=decision)
        run_id, event = _decision_of_the_new_run(load_trace_runs(trace_db_path(cfg)), known)  # one trace store per test: the run this case just made is the one run not seen before
        known.add(run_id)
        assert (event["outcome"], event.get("reason_code")) == (outcome, code), name
    cfg, _result, _seen = _approval_run(tmp_path / "no-callback", decision=None)
    _run_id, event = _decision_of_the_new_run(load_trace_runs(trace_db_path(cfg)), known)
    assert event["outcome"] == "unavailable" and event.get("reason_code") is None


@pytest.mark.parametrize("ids", [("aaaa", "zzzz"), ("zzzz", "aaaa")], ids=["intended-run-sorts-first", "intended-run-sorts-last"])
def test_the_run_is_selected_by_identity_when_timestamps_are_identical_and_ids_arbitrary(ids):
    """Control for the selection (hosted run 41785193323): two runs with the SAME timestamp come back in run-id order, which says nothing about which is newer. The old position-based
    pick (`events[-1]`) returns the wrong run for one of the two id orders; the identity-based pick returns the intended run for both."""
    from types import SimpleNamespace

    def run(run_id, outcome):
        return SimpleNamespace(run_id=run_id, timestamp="2026-11-04 14:00:00", events_of=lambda kind: [{"outcome": outcome}] if kind == "approval.decision" else [])

    earlier, intended = run(ids[0], "approved"), run(ids[1], "rejected")   # `intended` is the case under test, made after `earlier`
    listed = sorted([earlier, intended], key=lambda r: (r.timestamp, r.run_id))   # exactly load_trace_runs' ordering
    _id, event = _decision_of_the_new_run(listed, {earlier.run_id})
    assert event["outcome"] == "rejected"
    position_based = _events(listed, "approval.decision")[-1]["outcome"]
    assert position_based == ("rejected" if listed[-1] is intended else "approved")   # the old pick is only right when the ids happen to sort that way
    assert (position_based == "rejected") == (ids == ("aaaa", "zzzz"))


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
