"""CONTEXT-REFUSAL-EVIDENCE-001 (Stage B run 4, F1): a pre-provider refusal is
reproducible from its own record.

TRACED (run 20261118T111000-acc9061c): "the request needs about 31601 prompt
tokens plus at least 1024 output tokens, but the served context window is
32768" reads as a request that fits (32625). The dispatch check also keeps a
256-token safety margin (token_budget.DISPATCH_SAFETY_MARGIN_TOKENS): 32881 >
32768. The refusal was correct - the request carried no optional section, so
no permitted reduction existed (prompt.sections: one mandatory segment) - but
neither the message nor the sealed record showed the margin, the reductions
attempted or the irreducible estimate. The budget itself is unchanged.
"""
import pytest
from _strict_doubles import strict_config

from kriya.core import model_admission as admission
from kriya.core import token_budget as tb
from kriya.core.attempt_evidence import reader, scope
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.context_budget import OptionalSection, RequestCapacity, fit_developer_request


def _run4_refusal():
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
        tb.plan_dispatch(
            messages=[{"role": "system", "content": "s" * 22}, {"role": "user", "content": "u" * 78899}],
            requested_max_tokens=16384, context_window=32768, window_source="served_observed")
    return refused.value


def _records(tmp_path, monkeypatch, run_id, body):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(ENV_STATE_DIR, str(state))

    class _Context:
        pass
    _Context.run_id = run_id
    with scope.run_scope(_Context()):
        scope.ensure_store(strict_config())
        body()
        scope._RUN.get().writer.seal("test")
    return list(reader.open_run(str(state), run_id).records())


def test_the_refusal_states_the_safety_margin_and_the_total_it_compared():
    refusal = _run4_refusal()
    decision = refusal.decision
    assert (decision.prompt_tokens, decision.min_output_tokens, decision.safety_margin, decision.context_window) == (
        31601, 1024, 256, 32768)
    assert decision.prompt_tokens + decision.min_output_tokens <= decision.context_window   # why the old text misled
    message = str(refusal)
    assert "31601 prompt tokens" in message and "at least 1024 output tokens" in message
    assert "a 256-token dispatch safety margin (32881 tokens in all)" in message
    assert "the served context window is 32768 (served_observed)" in message
    assert message.endswith("Nothing was sent to the model.")


def test_a_request_one_token_inside_the_margin_is_still_admitted():
    """The control: the evidence change moved no boundary (32768 - 256 - 1024 = 31488 prompt tokens fit)."""
    fits = tb.plan_dispatch(
        messages=[{"role": "system", "content": "s" * 22}, {"role": "user", "content": "u" * (78899 - 283)}],
        requested_max_tokens=16384, context_window=32768, window_source="served_observed")
    assert fits.satisfiable and fits.prompt_tokens == 31488 and fits.max_tokens == 1024
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        tb.plan_dispatch(
            messages=[{"role": "system", "content": "s" * 22}, {"role": "user", "content": "u" * (78899 - 280)}],
            requested_max_tokens=16384, context_window=32768, window_source="served_observed")


def test_an_undispatched_call_records_the_refusals_own_decision(tmp_path, monkeypatch):
    budget_refusal = _run4_refusal()
    admission_refusal = admission.ModelAdmissionError("MODEL_BINDING_NOT_QUALIFIED: refused", {
        "reason_code": admission.MODEL_BINDING_NOT_QUALIFIED, "model": "fallback:1", "status": "MISSING",
        "inference_settings_digest": "sha256:c4f0",
        "inference_settings": {"version": 3, "temperature": 0.2, "reasoning": False,
                               "extra_body": {"think": False}, "server_config": {}},
        "inference_runtime": "ollama_native", "requested_context_window": 65536})

    def body():
        for error in (budget_refusal, admission_refusal, ValueError("a refusal with no structured decision")):
            with scope.call_scope("developer"):
                scope.record_undispatched_call(error, [{"role": "user", "content": "u"}])

    requests = [r["payload"] for r in _records(tmp_path, monkeypatch, "run-refusals", body) if r["kind"] == "model.request"]
    budget, admitted, plain = requests
    assert budget["dispatched"] is False and budget["reason_code"] == tb.CONTEXT_BUDGET_UNSATISFIABLE
    recorded = budget["refusal_details"]["budget"]
    # resolved cap | input estimate | output reserve | other reserve | what was compared
    assert (recorded["context_window"], recorded["prompt_tokens"], recorded["min_output_tokens"],
            recorded["safety_margin"], recorded["required_tokens"]) == (32768, 31601, 1024, 256, 32881)
    assert recorded["considered_tiers"] == [{"tokens": 32768, "source": "preferred", "room": 911, "fits": False}]
    assert admitted["refusal_details"]["inference_settings"]["temperature"] == 0.2
    assert admitted["refusal_details"]["requested_context_window"] == 65536
    assert admitted["refusal_details"] == admission_refusal.details
    assert "refusal_details" not in plain and plain["refusal_type"] == "ValueError"


def test_an_oversize_request_records_its_irreducible_estimate_and_that_it_is_still_over_capacity(tmp_path, monkeypatch):
    """Run 4's shape: mandatory text alone exceeds the capacity. Every optional section is left out (the only
    permitted reduction) and the record says what remains and that it is NOT within the capacity - "fitted" alone
    read as if it were."""
    capacity = RequestCapacity(tokens=600)
    optional = "graph line\n" * 50
    section = OptionalSection(kind="graph_context", text=optional, rebuild=lambda budget: "")
    prompt = "MANDATORY " * 400 + optional + "FOOTER\n"

    def body():
        fit_developer_request(capacity, "SYSTEM", prompt, [section])                       # over capacity
        fit_developer_request(capacity, "SYSTEM", "small task\n", [])                      # fits unchanged

    records = [r["payload"] for r in _records(tmp_path, monkeypatch, "run-oversize", body)
               if r["kind"] == "prompt.sections" and r["payload"]["fitter"] == "fit_developer_request"]
    oversize, unchanged = records
    mandatory = capacity.count("SYSTEM") + capacity.count("MANDATORY " * 400 + "FOOTER\n")
    assert oversize["fitted"] is True and oversize["mandatory_tokens"] == mandatory > capacity.tokens
    assert oversize["fitted_tokens"] == mandatory and oversize["within_capacity"] is False
    assert [(s["name"], s["outcome"]) for s in oversize["segments"] if not s["mandatory"]] == [("graph_context", "dropped")]
    # a request that fits unchanged keeps the record it always had
    assert unchanged["fitted"] is False
    assert not {"mandatory_tokens", "fitted_tokens", "within_capacity"} & set(unchanged)


def test_a_reduced_request_that_now_fits_says_so(tmp_path, monkeypatch):
    capacity = RequestCapacity(tokens=600)
    big = "graph line\n" * 400
    section = OptionalSection(kind="graph_context", text=big, rebuild=lambda budget: "graph line\n" * max(0, budget // 4))

    def body():
        fit_developer_request(capacity, "SYSTEM", f"TASK HEADER\n{big}TASK FOOTER\n", [section])

    [payload] = [r["payload"] for r in _records(tmp_path, monkeypatch, "run-reduced", body)
                 if r["kind"] == "prompt.sections" and r["payload"]["fitter"] == "fit_developer_request"]
    assert payload["within_capacity"] is True and payload["mandatory_tokens"] < payload["fitted_tokens"] <= capacity.tokens
