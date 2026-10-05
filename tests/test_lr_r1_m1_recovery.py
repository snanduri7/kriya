"""LR-R1-M1.8b: diagnosis, recovery.decision, fallback.decision, retry.delta
and candidate.change REFUSED (design §3.3, §3.4, §5.6; tests T6, T11).

Through the real direct pipeline (only the runtime port scripted), a failed
attempt is recorded as its diagnosis and the recovery decision that followed
it - the policy's own action and reason - and the next attempt's first
Developer request carries the input delta against the previous one. The
pure delta function and the REFUSED rule are pinned at unit level.
"""
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

from kriya.core.attempt_evidence import reader, scope
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.failure import Failure


def _run(tmp_path, monkeypatch, developer):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    runtime = ChaosRuntime(lambda role, request: developer(request) if role == "developer"
                           else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    run = reader.open_run(str(state), run_id)
    return result, run, list(run.records())


def _first_fails_then_passes():
    answers = iter([CALC.replace("a + b", "a - b")] + [CALC_WITH_SUB] * 20)
    return lambda request: next(answers)


def test_a_failed_attempt_records_its_diagnosis_then_the_recovery_decision(tmp_path, monkeypatch):
    _result, run, records = _run(tmp_path, monkeypatch, _first_fails_then_passes())
    diagnoses = [r for r in records if r["kind"] == "diagnosis"]
    decisions = [r for r in records if r["kind"] == "recovery.decision"]
    assert diagnoses and len(decisions) == len(diagnoses)
    diagnosis, decision = diagnoses[0], decisions[0]
    assert diagnosis["seq"] < decision["seq"]
    assert diagnosis["payload"]["attempt"] == 1 and diagnosis["payload"]["type"]
    assert diagnosis["payload"]["evidence_class"] in ("MEASURED", "DERIVED_DETERMINISTIC", "MODEL_CLAIMED", "UNKNOWN")
    assert run.blob(diagnosis["blobs"]["message"])
    payload = decision["payload"]
    assert payload["failure_type"] == diagnosis["payload"]["type"]
    assert payload["retry"] is True and payload["stop_loop"] is False
    retry = payload["retry_decision"]
    assert retry["should_continue"] is True and retry["action"] == payload["action"] and retry["reason"]
    # The next attempt opened after the decision.
    opened = [r for r in records if r["kind"] == "attempt.opened" and r["attempt_number"] == 2]
    assert opened and opened[0]["seq"] > decision["seq"]


def test_each_retry_records_its_delta_at_its_first_developer_request(tmp_path, monkeypatch):
    _result, _run_store, records = _run(tmp_path, monkeypatch, _first_fails_then_passes())
    attempts = sorted({r["attempt_number"] for r in records if r["kind"] == "attempt.opened"})
    assert len(attempts) >= 2
    deltas = {r["attempt_number"]: r for r in records if r["kind"] == "retry.delta"}
    assert sorted(deltas) == attempts[1:]           # exactly one per retry, none for attempt 1
    for attempt, delta in deltas.items():
        assert delta["payload"]["previous_attempt"] == attempt - 1
        requests = [r for r in records if r["kind"] == "model.request" and r["role"] == "developer"
                    and r["attempt_number"] == attempt]
        if requests:
            assert delta["seq"] == requests[0]["seq"] + 1
            assert delta["payload"]["information_gain"] in ("PRESENT", "NONE")
            assert set(scope.RETRY_DELTA_DIMENSIONS) <= set(delta["payload"]["dimensions"])
        else:
            assert delta["payload"]["information_gain"] == "UNKNOWN"
    # Attempt 2 was triggered by attempt 1's failure; attempt 1 by none.
    assert "failure_signature" in deltas[2]["payload"]["changed"]


def test_every_developer_generation_records_its_model_decision_before_its_requests(tmp_path, monkeypatch):
    _result, _run_store, records = _run(tmp_path, monkeypatch, _first_fails_then_passes())
    decisions = [r for r in records if r["kind"] == "fallback.decision" and r["payload"]["phase"] == "call"]
    assert decisions
    for decision in decisions:
        assert decision["payload"]["fallback"] is False and decision["payload"]["requested_rejection"] == []
        assert decision["payload"]["selected"] == decision["payload"]["requested"]
        assert decision["payload"]["profile_digest"] == decision["payload"]["requested_profile_digest"]
    for request in (r for r in records if r["kind"] == "model.request" and r["role"] == "developer"):
        before = [d for d in decisions if d["seq"] < request["seq"]
                  and d["attempt_number"] == request["attempt_number"]]
        assert before, f"Developer request {request['seq']} has no model decision"
        assert before[-1]["payload"]["selected"] == request["payload"]["model"]


def test_an_exhausted_run_records_the_stop_decision(tmp_path, monkeypatch):
    result, _run_store, records = _run(tmp_path, monkeypatch, lambda request: CALC.replace("a + b", "a - b"))
    decisions = [r["payload"] for r in records if r["kind"] == "recovery.decision"]
    assert decisions and all(d["retry"] for d in decisions[:-1])
    last = decisions[-1]
    assert last["retry"] is False
    assert last["retry_decision"] is None or last["retry_decision"]["should_continue"] is False
    assert result.get("status") != "success"


# -- unit level ------------------------------------------------------------------------------

def _summary(attempt, *, mode="full_set", trigger=None, evidence=None, request=True, **request_fields):
    first = None
    if request:
        first = {"model": "m", "model_profile": "p", "temperature": 0.2, "targets": ["a.py"], "authority": "A",
                 "sections": {"skills": "s1", "graph_context": "g1"}, "request_digest": "r"}
        first.update(request_fields)
    return {"attempt": attempt, "mode": mode, "trigger_failure": trigger, "retry_evidence": evidence,
            "first": first, "last": first}


def test_retry_delta_identical_inputs_are_no_information_gain():
    delta = scope.retry_delta(_summary(1, trigger="f1"), _summary(2, trigger="f1"))
    assert delta["information_gain"] == "NONE" and delta["changed"] == []


def test_retry_delta_names_every_changed_dimension_and_only_those():
    delta = scope.retry_delta(
        _summary(1, trigger="f1", evidence="e1"),
        _summary(2, trigger="f2", evidence="e1", model="fallback", sections={"skills": "s1", "graph_context": "g2"}),
    )
    assert delta["information_gain"] == "PRESENT"
    assert delta["changed"] == ["failure_signature", "model", "sections.graph_context"]
    assert delta["dimensions"]["model"] == {"previous": "m", "current": "fallback"}


def test_retry_delta_a_request_digest_change_alone_is_not_information():
    """The whole request text always differs (it names the attempt); the
    delta is over the declared dimensions only."""
    delta = scope.retry_delta(_summary(1), _summary(2, request_digest="other"))
    assert delta["information_gain"] == "NONE"
    assert delta["request_digests"] == {"previous": "r", "current": "other"}


def test_retry_delta_is_unknown_without_a_request_on_either_side():
    assert scope.retry_delta(_summary(1, request=False), _summary(2))["information_gain"] == "UNKNOWN"
    assert scope.retry_delta(_summary(1), _summary(2, request=False))["information_gain"] == "UNKNOWN"


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


def _refusal_records(tmp_path, monkeypatch, *, staged):
    from tests._strict_doubles import strict_config

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    failure = Failure(type="structural_corruption", message="unbalanced", attempt=1,
                      failed_content={"a.py": "def broken(:\n"}, diagnostics={"reason_code": "STRUCTURAL"})
    with scope.run_scope(_Context("run-refused")):
        scope.ensure_store(strict_config())
        with scope.unit_scope(strict_config(), "u", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                if staged:
                    scope.emit("candidate.change", {"decision": "STAGED", "path": "a.py"})
            scope.record_diagnosis(failure, "full_set")   # recorded after the attempt, as in production
        scope._RUN.get().writer.seal("test")
    run = reader.open_run(str(state), "run-refused")
    return run, [r for r in run.records() if r["kind"] == "candidate.change"]


def test_content_proposed_but_never_staged_is_recorded_refused(tmp_path, monkeypatch):
    run, changes = _refusal_records(tmp_path, monkeypatch, staged=False)
    [refused] = changes
    assert refused["payload"]["decision"] == "REFUSED" and refused["payload"]["reason_code"] == "STRUCTURAL"
    assert refused["payload"]["path"] == "a.py" and refused["payload"]["attempt"] == 1
    assert run.blob(refused["blobs"]["proposed"]) == b"def broken(:\n"


def test_a_staged_candidate_is_never_also_recorded_refused(tmp_path, monkeypatch):
    _run_store, changes = _refusal_records(tmp_path, monkeypatch, staged=True)
    assert [c["payload"]["decision"] for c in changes] == ["STAGED"]


def test_recovery_decision_field_is_additive_and_not_compared():
    from kriya.workflow.recovery_coordinator import RecoveryDecision
    from kriya.workflow.retry_policy import RetryAction, RetryDecision

    with_policy = RecoveryDecision(stop_loop=False, action=RetryAction.TARGETED, budgets_exhausted=False,
                                   retry_decision=RetryDecision(RetryAction.TARGETED, "why"))
    assert with_policy == RecoveryDecision(stop_loop=False, action=RetryAction.TARGETED, budgets_exhausted=False)


def test_an_escalation_records_the_fallback_decision_and_the_call_on_the_fallback(tmp_path, monkeypatch):
    """With a configured fallback, every escalation records what was asked
    for and what was selected, and the Developer call that follows records
    the fallback it went to."""
    from kriya.config.config import FallbackModelConfig

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config()
    cfg.llm_chain = [FallbackModelConfig(model="chaos-fallback:1", inference_runtime="chaos",
                                         context_window=8192, extra_body={})]
    wrong = CALC.replace("a + b", "a - b")
    runtime = ChaosRuntime(lambda role, request: wrong if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    records = list(reader.open_run(str(state), run_id).records())
    escalations = [r for r in records if r["kind"] == "fallback.decision" and r["payload"]["phase"] == "escalation"]
    assert escalations
    assert all(e["payload"]["requested"] == "chaos-fallback:1" == e["payload"]["selected"] for e in escalations)
    on_fallback = [r for r in records if r["kind"] == "fallback.decision" and r["payload"]["phase"] == "call"
                   and r["payload"]["fallback"] is True]
    assert on_fallback and all(r["payload"]["selected"] == "chaos-fallback:1" for r in on_fallback)
    requests = [r for r in records if r["kind"] == "model.request" and r["role"] == "developer"
                and r["payload"]["model"] == "chaos-fallback:1"]
    assert requests


def test_an_incompatible_fallback_records_the_rejection_and_no_selection(tmp_path, monkeypatch):
    """The LR-R1-P1 shape: the fallback is proven unable to serve; the
    escalation records the rejection with its reasons and profile digest,
    selects nothing, and the run's recovery decision stops."""
    import kriya.workflow.model_transition as model_transition
    from kriya.config.config import FallbackModelConfig

    def incompatible(cfg, profile, **kwargs):
        return ["TEST_REJECTION: fallback cannot serve"] if profile.model == "chaos-fallback:1" else []
    monkeypatch.setattr(model_transition, "fallback_incompatibilities", incompatible)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config()
    cfg.llm_chain = [FallbackModelConfig(model="chaos-fallback:1", inference_runtime="chaos",
                                         context_window=8192, extra_body={})]
    wrong = CALC.replace("a + b", "a - b")
    runtime = ChaosRuntime(lambda role, request: wrong if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    records = list(reader.open_run(str(state), run_id).records())
    [escalation] = [r for r in records if r["kind"] == "fallback.decision" and r["payload"]["phase"] == "escalation"]
    assert escalation["payload"]["selected"] is None
    [rejected] = escalation["payload"]["newly_rejected"]
    assert rejected["model"] == "chaos-fallback:1" and rejected["reasons"] == ["TEST_REJECTION: fallback cannot serve"]
    assert rejected["profile_digest"]
    diagnosis = [r for r in records if r["kind"] == "diagnosis"][-1]
    assert diagnosis["payload"]["type"] == "fallback_incompatible"
    assert diagnosis["payload"]["reason_code"] == "FALLBACK_MODEL_INCOMPATIBLE"
    decision = [r for r in records if r["kind"] == "recovery.decision"][-1]["payload"]
    assert decision["retry"] is False and decision["failure_type"] == "fallback_incompatible"
    assert not [r for r in records if r["kind"] == "model.request" and r["payload"]["model"] == "chaos-fallback:1"]


def test_evidence_class_follows_who_localized_the_failure():
    assert scope._evidence_class(Failure(type="test", message="", attribution_tier="triage")) == "MODEL_CLAIMED"
    assert scope._evidence_class(Failure(type="test", message="", attribution_tier="self_diagnosis")) == \
        "MODEL_CLAIMED"
    assert scope._evidence_class(Failure(type="test", message="", attribution_tier="locator")) == \
        "DERIVED_DETERMINISTIC"
    assert scope._evidence_class(Failure(type="compile", message="", source="quality_gate")) == "MEASURED"
    assert scope._evidence_class(Failure(type="x", message="", source="orchestrator")) == "UNKNOWN"


def test_a_call_substituted_to_the_next_fallback_records_both_models(tmp_path, monkeypatch):
    """A fallback that passes escalation but cannot serve the call itself is
    replaced by the next one; the call's decision names the requested and
    the selected model with their own profile digests."""
    import kriya.workflow.model_transition as model_transition
    from kriya.config.config import FallbackModelConfig

    def incompatible(cfg, profile, **kwargs):
        if profile.model == "fb-one:1" and "patch_required_files" in kwargs:
            return ["TEST_CALL_REJECTION"]
        return []
    monkeypatch.setattr(model_transition, "fallback_incompatibilities", incompatible)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config()
    cfg.llm_chain = [FallbackModelConfig(model=name, inference_runtime="chaos", context_window=8192, extra_body={})
                     for name in ("fb-one:1", "fb-two:1")]
    wrong = CALC.replace("a + b", "a - b")
    runtime = ChaosRuntime(lambda role, request: wrong if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    records = list(reader.open_run(str(state), run_id).records())
    substituted = [r["payload"] for r in records if r["kind"] == "fallback.decision"
                   and r["payload"]["phase"] == "call" and r["payload"]["requested"] == "fb-one:1"]
    assert substituted
    for payload in substituted:
        assert payload["selected"] == "fb-two:1" and payload["requested_rejection"] == ["TEST_CALL_REJECTION"]
        assert payload["profile_digest"] != payload["requested_profile_digest"]
    assert not [r for r in records if r["kind"] == "model.request" and r["payload"]["model"] == "fb-one:1"]


def test_a_call_no_fallback_can_serve_records_the_refused_call(tmp_path, monkeypatch):
    import kriya.workflow.model_transition as model_transition
    from kriya.config.config import FallbackModelConfig

    def incompatible(cfg, profile, **kwargs):
        return ["TEST_CALL_REJECTION"] if profile.model == "fb-one:1" and "patch_required_files" in kwargs else []
    monkeypatch.setattr(model_transition, "fallback_incompatibilities", incompatible)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    cfg = chaos_config()
    cfg.llm_chain = [FallbackModelConfig(model="fb-one:1", inference_runtime="chaos", context_window=8192,
                                         extra_body={})]
    wrong = CALC.replace("a + b", "a - b")
    runtime = ChaosRuntime(lambda role, request: wrong if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        run_direct(chaos_engine(cfg), "add sub to calc.py", workspace)
    [run_id] = reader.list_runs(str(state))
    records = list(reader.open_run(str(state), run_id).records())
    [refused] = [r["payload"] for r in records if r["kind"] == "fallback.decision"
                 and r["payload"]["phase"] == "call" and r["payload"]["requested"] == "fb-one:1"]
    assert refused["selected"] is None and refused["profile_digest"] is None
    assert refused["requested_rejection"] == ["TEST_CALL_REJECTION"]
    assert [r for r in records if r["kind"] == "diagnosis"][-1]["payload"]["type"] == "fallback_incompatible"
