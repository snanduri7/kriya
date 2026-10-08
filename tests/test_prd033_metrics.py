"""PRD-033: metrics are derived from persisted evidence, reproducibly, with
false success only from adjudication and no proprietary content.

Trace rows are written with the real TraceLogger (the production writer),
then read back through the real read-only loader.
"""
import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kriya.core.trace import TraceLogger
from kriya.metrics import adjudication as adj
from kriya.metrics.derive import derive_metrics
from kriya.metrics.evidence import load_trace_runs, project_trace_row
from kriya.metrics.report import build_report, render_markdown
from kriya.metrics.thresholds import ThresholdConfigError, evaluate_thresholds, load_thresholds

CANARY = "PROPRIETARY-CANARY-7d2e"
DEV_A = {"role": "developer", "model": "m:1", "runtime_digest": "rt-a", "runtime_exact": True,
         "inference_settings_digest": "set-1", "calls": 2, "protocol_failures": 1, "schema_failures": 0,
         "prompt_tokens": 100, "completion_tokens": 40, "attempts": 2, "attempts_passed": 1,
         "first_pass_success": False, "latency_seconds": 1.5}


def _gate(attempt, gate_type, success):
    return {"attempt": attempt, "type": gate_type, "success": success, "output": CANARY,
            "failed_content": {"x.py": CANARY}, "attempted_edits": [CANARY]}


def _event(kind, **details):
    return {"kind": kind, "message": f"{CANARY} message", "details": details}


def _log(db, run_id, *, status="success", category=None, gates=(), events=(), route="TASK", retry=(0, 0),
         failure_types=(), wall=10.0, verification=4.0, llm_calls=3, group=None, enforce=None):
    TraceLogger(str(db)).log_run(
        run_id=run_id, goal=f"goal {CANARY}", duration_sec=wall, attempts=1, status=status,
        files_modified=[f"{CANARY}.py"], retrieved_chunks=[{"text": CANARY}], prompt_rendered=CANARY,
        gate_outcomes=list(gates), failure_category=category, milestone_group_id=group, enforce_run_id=enforce,
        run_events=list(events),
        evidence_records=[{"payload": CANARY}],
        generation_metrics={
            "total_wall_seconds": wall, "llm": {"calls": llm_calls},
            "validators": {"wall_seconds": verification, "invocations": 2 if verification else 0},
            "retry": {"full_set_attempts": retry[0], "targeted_attempts": retry[1]},
            **({"engineering_route": {"kind": route}} if route else {}),
        },
        failure_report=[{"failure_type": t, "category": "x", "attribution_tier": None} for t in failure_types],
    )


@pytest.fixture
def db(tmp_path):
    return tmp_path / "state" / "traces.db"


def _runs(db):
    return load_trace_runs(str(db))


def test_no_proprietary_content_reaches_a_projection_or_a_report(db, tmp_path):
    _log(db, "r1", gates=[_gate(1, "compile", False), _gate(2, "compile", True)],
         events=[_event("model.role_metrics", rows=[DEV_A]), _event("static_analysis.result", outcome="PASS",
                                                                     evidence_path=f"/x/{CANARY}", banner=CANARY),
                 _event("approval.decision", outcome="approved", triggers=["diff_size"], note=CANARY)])
    runs = _runs(db)
    assert CANARY not in repr(runs)
    report = build_report(runs, generated={"at": "t"})
    text = json.dumps(report) + render_markdown(report)
    assert CANARY not in text


def test_outcomes_first_pass_retries_and_wall_time(db):
    _log(db, "ok", gates=[_gate(1, "compile", True)], retry=(0, 0))
    _log(db, "bad", status="failure", category="no_progress", gates=[_gate(1, "compile", False)], retry=(2, 1))
    _log(db, "ctx", status="failure", category="quality_gates_exhausted",
         failure_types=("context_budget_unsatisfiable",), route=None)
    block = derive_metrics(_runs(db))["outcomes"]["generation"]
    assert block["runs"] == 3
    assert block["final_verified_success"] == {"status": "MEASURED", "value": 0.3333, "numerator": 1, "denominator": 3}
    assert block["first_pass_compile"]["numerator"] == 1 and block["first_pass_compile"]["denominator"] == 2
    assert block["no_progress_termination"]["numerator"] == 1
    assert block["retries"]["full_set"]["value"] == 2 and block["retries"]["targeted"]["value"] == 1
    assert block["context_insufficiency"]["value"] == 1
    assert block["failure_categories"] == {"no_progress": 1, "quality_gates_exhausted": 1}
    assert block["wall_time_seconds"]["count"] == 3 and block["verification_share"]["value"] == 0.4
    assert block["llm_calls"]["value"] == 9


def test_verification_share_needs_timed_validators(db):
    _log(db, "untimed", verification=0.0)
    assert derive_metrics(_runs(db))["outcomes"]["generation"]["verification_share"]["status"] == "UNAVAILABLE"
    _log(db, "timed", verification=5.0, wall=20.0)
    share = derive_metrics(_runs(db))["outcomes"]["generation"]["verification_share"]
    assert share["value"] == 0.25 and share["runs"] == 1 and "not timed" in share["coverage"]


def test_first_pass_compile_is_the_first_compile_outcome(db):
    _log(db, "fixed-in-attempt", gates=[_gate(1, "compile", False), _gate(1, "compile", True)])
    _log(db, "broke-later", gates=[_gate(1, "compile", True), _gate(1, "compile", False)])
    block = derive_metrics(_runs(db))["outcomes"]["generation"]["first_pass_compile"]
    assert (block["numerator"], block["denominator"]) == (1, 2)


def test_no_evidence_is_unavailable_never_zero(db):
    empty = derive_metrics([])
    generation = empty["outcomes"]["generation"]
    assert generation["final_verified_success"]["status"] == "UNAVAILABLE"
    assert generation["first_pass_compile"]["status"] == "UNAVAILABLE"
    assert generation["wall_time_seconds"]["status"] == "UNAVAILABLE"
    assert generation["human_escalations"]["status"] == "UNAVAILABLE"
    assert empty["static_analysis"]["status"] == "UNAVAILABLE"
    assert empty["outcomes"]["enforce"]["status"] == "UNAVAILABLE"
    assert empty["adjudication"] == {"status": "NOT_PROVIDED"}
    assert empty["chaos"] == {"status": "NOT_PROVIDED"} and empty["run_records"] == {"status": "NOT_PROVIDED"}


def test_populations_are_never_mixed(db):
    _log(db, "unit-1")
    _log(db, "run-x.enforce", status="failed", category="plan_invalid", route=None)
    _log(db, "grp.milestone-plan", status="accepted", route=None)
    _log(db, "t9.exception", status="error", category="RuntimeError", route=None)
    content = derive_metrics(_runs(db))
    assert content["outcomes"]["generation"]["runs"] == 2  # the unit and the raised run
    assert content["outcomes"]["enforce"]["runs"] == 1
    assert content["outcomes"]["milestone_plans"] == {"rows": 1, "statuses": {"accepted": 1}}


def test_model_metrics_are_keyed_by_runtime_settings_role_and_task_class(db):
    other_settings = {**DEV_A, "inference_settings_digest": "set-2", "calls": 1, "protocol_failures": 0}
    planner = {**DEV_A, "role": "planner", "calls": 1, "protocol_failures": 0, "schema_failures": 1,
               "first_pass_success": None}
    _log(db, "a", events=[_event("model.role_metrics", rows=[DEV_A, other_settings, planner])], route="TASK")
    _log(db, "b", events=[_event("model.role_metrics", rows=[DEV_A])], route="ENHANCEMENT")
    rows = derive_metrics(_runs(db))["model_protocol"]
    keys = [(r["runtime_digest"], r["inference_settings_digest"], r["role"], r["task_class"]) for r in rows]
    assert keys == sorted(keys) and len(keys) == 4
    dev_task = next(r for r in rows if r["role"] == "developer" and r["inference_settings_digest"] == "set-1"
                    and r["task_class"] == "task")
    assert dev_task["calls"] == 2 and dev_task["protocol_failure_rate"]["value"] == 0.5
    assert dev_task["first_pass_success"] == {"status": "MEASURED", "value": 0.0, "numerator": 0, "denominator": 1}
    planner_row = next(r for r in rows if r["role"] == "planner")
    assert planner_row["structured_output_failure_rate"]["value"] == 1.0


def test_outcomes_are_keyed_by_the_developer_identity_and_task_class(db):
    _log(db, "a", events=[_event("model.role_metrics", rows=[DEV_A])])
    _log(db, "b", status="failure", category="no_progress", route="ENHANCEMENT",
         events=[_event("model.role_metrics", rows=[{**DEV_A, "runtime_digest": "rt-b"}])])
    _log(db, "c", status="failure", category="planning_failed", route=None)
    content = derive_metrics(_runs(db))
    assert sorted(content["by_developer_identity"]) == ["none", "rt-a|set-1", "rt-b|set-1"]
    assert sorted(content["by_task_class"]) == ["enhancement", "task", "unclassified"]
    assert content["by_developer_identity"]["rt-a|set-1"]["final_verified_success"]["value"] == 1.0


def test_static_analysis_escalation_authority_and_commit_evidence(db):
    _log(db, "a", events=[
        _event("static_analysis.result", outcome="BLOCKED", coverage="FULL", reason_codes=["NEW_FINDING_BLOCKED"]),
        _event("static_analysis.result", outcome="ACCEPTED_RISK", accepted_risk=True, coverage="PARTIAL",
               waiver_ids=["SAW-1"], reason_codes=["WAIVER_APPLIED"]),
        _event("approval.decision", outcome="approved", triggers=["sensitive_path"]),
        _event("review.pre_approval_unattached", reason_code="CONTEXT_BUDGET_UNSATISFIABLE"),
        _event("operation_authority.rejected"), _event("authority.expansion", outcome="GRANTED"),
        _event("model.fallback_selection"),
        _event("model.transition", initial=True, fallback=False), _event("model.transition", initial=False, fallback=True),
    ])
    _log(db, "b", status="failure", category="workspace_commit_failed",
         events=[_event("workspace_commit.failed", reason_code="STATIC_ANALYSIS_EVIDENCE_STALE",
                        workspace_state="UNCHANGED")])
    content = derive_metrics(_runs(db))
    static = content["static_analysis"]
    assert static["runs_evaluated"] == 1 and static["outcomes"] == {"ACCEPTED_RISK": 1}  # the last result per run
    assert static["incomplete_coverage"] == 1 and static["waiver_uses"] == 1
    assert static["stale_or_missing_authorization_at_commit"] == 1
    block = content["outcomes"]["generation"]
    assert block["human_escalations"] == {"approved": 1}
    assert block["pre_approval_review_unattached"]["value"] == 1
    assert block["authority_rejections"]["value"] == 1 and block["authority_expansions"] == {"GRANTED": 1}
    # The initial request profile is not a fallback; the skip is counted apart.
    assert block["fallback_transitions"]["value"] == 1 and block["fallback_selection_skips"]["value"] == 1
    assert block["workspace_commit_failures"] == {"STATIC_ANALYSIS_EVIDENCE_STALE": 1}


@pytest.fixture
def store_home(tmp_path, monkeypatch):
    monkeypatch.setenv(adj.ENV_HOME_OVERRIDE, str(tmp_path / "adjudications"))
    return tmp_path / "adjudications"


def test_false_success_comes_only_from_adjudication(db, store_home):
    for run_id in ("s1", "s2", "s3"):
        _log(db, run_id)
    _log(db, "f1", status="failure", category="no_progress")
    store = adj.load_adjudications()
    assert derive_metrics(_runs(db), adjudications=store)["adjudication"]["false_success_rate"]["status"] == "UNAVAILABLE"
    adj.record_adjudication(run_id="s1", verdict=adj.VERDICT_FALSE_SUCCESS, adjudicator="qa", evidence="bug #12",
                            run_status="success")
    adj.record_adjudication(run_id="s2", verdict=adj.VERDICT_CONFIRMED_SUCCESS, adjudicator="qa", evidence="checked",
                            run_status="success")
    adj.record_adjudication(run_id="outside-window", verdict=adj.VERDICT_FALSE_SUCCESS, adjudicator="qa",
                            evidence="a run this report does not cover", run_status="success")
    block = derive_metrics(_runs(db), adjudications=adj.load_adjudications())["adjudication"]
    assert block["false_success_rate"] == {"status": "MEASURED", "value": 0.5, "numerator": 1, "denominator": 2}
    assert block["verdicts"]["false_success"] == 1
    assert block["adjudication_coverage"]["value"] == round(2 / 3, 4)
    assert block["sources"] == {"human": 2}


def test_adjudication_refusals_and_supersession(store_home):
    with pytest.raises(adj.AdjudicationRefused, match="no traced run"):
        adj.record_adjudication(run_id="x", verdict=adj.VERDICT_FALSE_SUCCESS, adjudicator="a", evidence="e",
                                run_status=None)
    with pytest.raises(adj.AdjudicationRefused, match="only to a run that ended SUCCESS"):
        adj.record_adjudication(run_id="x", verdict=adj.VERDICT_REGRESSION_ESCAPE, adjudicator="a", evidence="e",
                                run_status="failure")
    with pytest.raises(adj.AdjudicationRefused, match="unknown verdict"):
        adj.record_adjudication(run_id="x", verdict="looks_fine_to_the_reviewer", adjudicator="a", evidence="e",
                                run_status="success")
    with pytest.raises(adj.AdjudicationRefused, match="evidence is required"):
        adj.record_adjudication(run_id="x", verdict=adj.VERDICT_FALSE_SUCCESS, adjudicator="a", evidence=" ",
                                run_status="success")
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    adj.record_adjudication(run_id="x", verdict=adj.VERDICT_CONFIRMED_SUCCESS, adjudicator="a", evidence="e",
                            run_status="success", now=early)
    adj.record_adjudication(run_id="x", verdict=adj.VERDICT_FALSE_SUCCESS, adjudicator="a", evidence="later bug",
                            run_status="success")
    assert adj.load_adjudications().by_run()["x"].verdict == adj.VERDICT_FALSE_SUCCESS


def test_a_tampered_adjudication_store_counts_nothing(db, store_home):
    _log(db, "s1")
    adj.record_adjudication(run_id="s1", verdict=adj.VERDICT_FALSE_SUCCESS, adjudicator="qa", evidence="bug",
                            run_status="success")
    path = Path(adj.store_path())
    payload = json.loads(path.read_text())
    payload["adjudications"][0]["verdict"] = adj.VERDICT_CONFIRMED_SUCCESS
    path.write_text(json.dumps(payload))
    store = adj.load_adjudications()
    assert store.status == "invalid" and "digest" in store.error
    block = derive_metrics(_runs(db), adjudications=store)["adjudication"]
    assert block["status"] == "UNAVAILABLE" and "invalid" in block["reason"]
    with pytest.raises(adj.AdjudicationStoreError):
        adj.record_adjudication(run_id="s1", verdict=adj.VERDICT_FALSE_SUCCESS, adjudicator="qa", evidence="x",
                                run_status="success")


def test_only_the_operator_cli_writes_adjudications():
    repo = Path(__file__).resolve().parent.parent
    callers = sorted(
        str(path.relative_to(repo)) for path in (repo / "kriya").rglob("*.py")
        if any(isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None))
               == "record_adjudication" for node in ast.walk(ast.parse(path.read_text())))
    )
    assert callers == ["kriya/cli.py"]


def test_workflow_never_imports_the_metrics_package():
    repo = Path(__file__).resolve().parent.parent
    offenders = [str(p.relative_to(repo)) for p in (repo / "kriya" / "workflow").rglob("*.py")
                 if "kriya.metrics" in p.read_text()]
    assert offenders == []


def _thresholds(tmp_path, entries):
    path = tmp_path / "operator" / "thresholds.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "thresholds": entries}))
    return str(path)


def test_thresholds_have_no_default_and_evaluate_every_status(db, tmp_path):
    _log(db, "ok")
    _log(db, "bad", status="failure", category="no_progress")
    content = derive_metrics(_runs(db))
    assert evaluate_thresholds(content, None) == {"status": "NOT_CONFIGURED"}
    path = _thresholds(tmp_path, [
        {"metric": "outcomes.generation.final_verified_success", "comparator": "min", "value": 0.4, "min_samples": 2},
        {"metric": "outcomes.generation.no_progress_termination", "comparator": "max", "value": 0.1, "min_samples": 2},
        {"metric": "outcomes.generation.first_pass_compile", "comparator": "min", "value": 0.9},
        {"metric": "outcomes.generation.final_verified_success", "comparator": "min", "value": 0.4, "min_samples": 50},
    ])
    thresholds, digest = load_thresholds(path, workspace_root=str(tmp_path / "ws"))
    result = evaluate_thresholds(content, thresholds, digest)
    assert [row["status"] for row in result["results"]] == ["PASS", "FAIL", "UNAVAILABLE", "INSUFFICIENT_EVIDENCE"]
    assert result["status"] == "FAIL" and result["thresholds_digest"] == digest
    only_gap = evaluate_thresholds(content, thresholds[2:3], digest)
    assert only_gap["status"] == "INCONCLUSIVE"


def test_threshold_files_are_validated_and_never_inside_the_workspace(tmp_path, db):
    with pytest.raises(ThresholdConfigError, match="unknown metric"):
        evaluate_thresholds(derive_metrics([]), load_thresholds(_thresholds(tmp_path, [
            {"metric": "outcomes.generation.nope", "comparator": "max", "value": 1}]))[0])
    with pytest.raises(ThresholdConfigError, match="invalid"):
        load_thresholds(_thresholds(tmp_path, [{"metric": "m", "comparator": "about", "value": 1}]))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inside = workspace / "thresholds.yaml"
    inside.write_text(json.dumps({"schema_version": 1, "thresholds": [
        {"metric": "outcomes.generation.runs", "comparator": "min", "value": 0}]}))
    with pytest.raises(Exception, match="(?i)workspace"):
        load_thresholds(str(inside), workspace_root=str(workspace))


def test_the_same_evidence_always_gives_the_same_report(db, tmp_path):
    _log(db, "a", gates=[_gate(1, "compile", True)], events=[_event("model.role_metrics", rows=[DEV_A])])
    _log(db, "b", status="failure", category="no_progress")
    first = build_report(_runs(db), generated={"at": "one"})
    second = build_report(load_trace_runs(str(db)), generated={"at": "two"})
    assert first["content_digest"] == second["content_digest"] and first["content"] == second["content"]
    assert first["generated"] != second["generated"]


def test_chaos_and_run_record_inputs(tmp_path):
    from kriya.metrics.evidence import RunRecordFacts, WorkspaceFacts

    chaos = {"content_digest": "d", "scenarios": [
        {"scenario_id": "A01", "family": "model", "verdict": "PASSED"},
        {"scenario_id": "C01", "family": "runtime", "verdict": "FAILED"},
        {"scenario_id": "L01", "family": "live", "verdict": "NOT_RUN"}]}
    facts = WorkspaceFacts(records=(
        RunRecordFacts("SUCCESS", "COMMITTED", None, (), False),
        RunRecordFacts("FAILURE", "NO_COMMIT", "FAILURE", ("design",), True),
        RunRecordFacts("RECOVERED", "COMMITTED", "NEEDS_REVIEW", (), True)), unreadable=1, workspaces=1)
    content = derive_metrics([], chaos=chaos, run_records=facts)
    assert content["chaos"]["invariant_failures"] == 1 and content["chaos"]["invariant_failures_by_family"] == {"runtime": 1}
    records = content["run_records"]
    assert records["recovered"] == 1 and records["resume_invalidation"]["value"] == 0.5
    assert records["invalidated_stages"] == {"design": 1} and records["unreadable_run_records"] == 1


def test_a_row_with_unparseable_json_projects_safely():
    run = project_trace_row({"run_id": "r", "status": "success", "generation_metrics": "{not json",
                             "run_events": "[", "gate_outcomes": None, "failure_report": "{}"})
    assert run.kind == "generation" and run.gates == () and run.role_rows == () and run.llm_calls == 0
