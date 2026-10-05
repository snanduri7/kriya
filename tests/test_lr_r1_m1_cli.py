"""LR-R1-M1.9: ``kriya evidence show|explain|verify|prune`` (design §8; test T6
via the CLI, T12 dry run).

Real runs through the direct pipeline (runtime port scripted), then the CLI
answers the nine questions for each attempt: RECORDED where the run produced
the evidence, NOT_APPLICABLE where the question does not arise, NOT_RECORDED
with a reason - never an empty field.
"""
import json
import os

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
from click.testing import CliRunner

from kriya.cli import main
from kriya.core.attempt_evidence import reader, scope
from kriya.core.state_paths import ENV_STATE_DIR

WRONG = CALC.replace("a + b", "a - b")


def _run(tmp_path, monkeypatch, developer, cfg=None):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: developer(request) if role == "developer"
                           else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(cfg or chaos_config()), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    return state, run_id


def _cli(*args):
    result = CliRunner().invoke(main, ["evidence", *args], catch_exceptions=False)
    return result.exit_code, result.output


def _explain(run_id):
    code, output = _cli("explain", run_id, "--json")
    assert code == 0, output
    return json.loads(output)


def test_a_passing_run_answers_every_question(tmp_path, monkeypatch):
    _state, run_id = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    result = _explain(run_id)
    assert result["verification"] == reader.VERIFIED
    [attempt] = result["attempts"]
    answers = attempt["answers"]
    for label in ("Q1", "Q2", "Q3", "Q4", "Q8"):
        assert answers[label]["status"] == "RECORDED" and answers[label]["items"], label
    assert answers["Q5"]["status"] == "NOT_APPLICABLE"            # every check passed
    assert answers["Q6"]["status"] == "NOT_APPLICABLE"            # nothing was retried
    assert answers["Q7"]["status"] == "NOT_APPLICABLE"            # no later attempt
    assert result["Q9"]["status"] == "RECORDED"
    for answer in list(answers.values()) + [result["Q9"]]:
        assert answer["status"] in ("RECORDED", "NOT_APPLICABLE") or answer.get("reason")


def test_a_retried_run_explains_the_failure_the_decision_and_the_delta(tmp_path, monkeypatch):
    answers_iter = iter([WRONG] + [CALC_WITH_SUB] * 20)
    _state, run_id = _run(tmp_path, monkeypatch, lambda request: next(answers_iter))
    first = _explain(run_id)["attempts"][0]["answers"]
    assert first["Q5"]["status"] == "RECORDED" and first["Q5"]["diagnoses"]
    assert {(item["gate"], item["success"]) for item in first["Q5"]["items"]} >= {("tests", False)}
    assert first["Q6"]["status"] == "RECORDED" and first["Q6"]["items"][-1]["retry"] is True
    assert first["Q7"]["status"] == "RECORDED"
    assert first["Q7"]["items"][0]["information_gain"] in ("PRESENT", "NONE", "UNKNOWN")


def test_an_incompatible_fallback_is_explained_with_its_rejection(tmp_path, monkeypatch):
    import kriya.workflow.model_transition as model_transition
    from kriya.config.config import FallbackModelConfig

    monkeypatch.setattr(model_transition, "fallback_incompatibilities",
                        lambda cfg, profile, **kw: ["TEST_REJECTION"] if profile.model == "fb:1" else [])
    cfg = chaos_config()
    cfg.llm_chain = [FallbackModelConfig(model="fb:1", inference_runtime="chaos", context_window=8192,
                                         extra_body={})]
    _state, run_id = _run(tmp_path, monkeypatch, lambda request: WRONG, cfg=cfg)
    result = _explain(run_id)
    escalations = [item for attempt in result["attempts"] if attempt["answers"]["Q8"]["status"] == "RECORDED"
                   for item in attempt["answers"]["Q8"]["items"] if item.get("phase") == "escalation"]
    assert escalations and escalations[-1].get("selected") is None
    assert escalations[-1]["newly_rejected"][0]["reasons"] == ["TEST_REJECTION"]
    assert result["Q9"]["last_recovery_decision"]["failure_type"] == "fallback_incompatible"


def test_an_attempt_without_a_model_call_says_so(tmp_path, monkeypatch):
    """The correct answer to Q1-Q3 for an attempt that called no model (an
    enforce TOOL subtask, a verification-only unit) is NOT_RECORDED with
    the no_model_call reason - never a neighbouring attempt's call."""
    from tests._strict_doubles import strict_config

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))

    class _Context:
        run_id = "run-no-call"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "verification_only"})
                scope.emit("gate.result", {"stage": "validator", "gate": "tests", "success": True})
        scope.close_run(_Context(), lambda: None)
    answers = _explain("run-no-call")["attempts"][0]["answers"]
    for label in ("Q1", "Q2", "Q3", "Q8"):
        assert answers[label]["status"] == "NOT_RECORDED" and answers[label]["reason"].startswith("no_model_call")
    assert answers["Q5"]["status"] == "NOT_APPLICABLE"


def test_human_output_never_prints_an_empty_answer(tmp_path, monkeypatch):
    _state, run_id = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    code, output = _cli("explain", run_id)
    assert code == 0
    lines = [line for line in output.splitlines() if line.strip().startswith("Q")]
    assert len(lines) == 9
    assert all(("RECORDED (" in line) or ("NOT APPLICABLE (" in line) or ("NOT RECORDED (" in line)
               for line in lines), output


def test_show_lists_runs_and_prints_exact_content(tmp_path, monkeypatch):
    state, run_id = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    code, output = _cli("show", "--json")
    assert code == 0 and json.loads(output)["runs"] == [run_id]
    run = reader.open_run(str(state), run_id)
    request = next(r for r in run.records() if r["kind"] == "model.request")
    ref = request["blobs"]["messages"]
    result = CliRunner().invoke(main, ["evidence", "show", run_id, "--content", ref], catch_exceptions=False)
    assert result.exit_code == 0 and result.stdout_bytes == run.blob(ref)


def test_verify_exit_codes_follow_the_store_state(tmp_path, monkeypatch):
    state, run_id = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    assert _cli("verify", run_id)[0] == 0
    records = os.path.join(reader.open_run(str(state), run_id).directory, "records.jsonl")
    with open(records, "rb") as handle:
        data = handle.read()
    with open(records, "wb") as handle:
        handle.write(data.replace(b'"run.opened"', b'"run.OPENED"', 1))
    code, output = _cli("verify", run_id)
    assert code == 2 and "CHAIN_BROKEN" in output
    code, output = _cli("explain", "no-such-run")
    assert code == 1 and "ATTEMPT EVIDENCE UNAVAILABLE" in output


def test_prune_dry_run_reports_and_writes_nothing(tmp_path, monkeypatch):
    state, run_id = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    before = sorted(os.walk(str(state)))
    code, output = _cli("prune", "--dry-run", "--json", "--workspace", str(tmp_path))
    assert code == 0
    report = json.loads(output)
    assert report["dry_run"] is True and report["pruned"] == [] and report["kept"] == [run_id]
    assert sorted(os.walk(str(state))) == before


def _synthetic(tmp_path, monkeypatch, run_id, build):
    from tests._strict_doubles import strict_config

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))

    class _Context:
        pass
    context = _Context()
    context.run_id = run_id
    cfg = strict_config()
    with scope.run_scope(context):
        scope.ensure_store(cfg)
        build(cfg)
        scope.close_run(context, lambda: None)
    return _explain(run_id)


def test_a_failure_without_a_recovery_decision_is_not_recorded_not_inapplicable(tmp_path, monkeypatch):
    def build(cfg):
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                scope.emit("diagnosis", {"type": "test", "attempt": 1})
    answers = _synthetic(tmp_path, monkeypatch, "run-no-decision", build)["attempts"][0]["answers"]
    assert answers["Q6"]["status"] == "NOT_RECORDED"


def test_the_next_attempt_is_never_taken_from_another_invocation(tmp_path, monkeypatch):
    """Q7 compares within one unit invocation: the last attempt of
    invocation 1 has no later attempt, even if invocation 2 follows."""
    def build(cfg):
        for _invocation in range(2):
            with scope.unit_scope(cfg, "u1", "direct"):
                with scope.attempt_scope(lambda: 1):
                    scope.attempt_opened({"mode": "full_set"})
                    scope.emit("retry.delta", {"information_gain": "PRESENT", "changed": ["model"]})
    attempts = _synthetic(tmp_path, monkeypatch, "run-two-invocations", build)["attempts"]
    assert [(a["invocation_seq"], a["attempt"]) for a in attempts] == [(1, 1), (2, 1)]
    assert attempts[0]["answers"]["Q7"]["status"] == "NOT_APPLICABLE"
