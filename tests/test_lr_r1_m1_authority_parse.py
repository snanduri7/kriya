"""LR-R1-M1.7: authority.snapshot and developer.parse (design §3.3; T6 part).

Through the real direct pipeline (only the runtime port scripted): every
Developer invocation records the authority it was given before its request,
and every answer records how it was parsed, bound to the call it answered.
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

from kriya.core.attempt_evidence import reader
from kriya.core.state_paths import ENV_STATE_DIR


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


def test_each_developer_request_is_preceded_by_its_authority_and_followed_by_its_parse(tmp_path, monkeypatch):
    _result, _run_store, records = _run(tmp_path, monkeypatch, lambda request: CALC_WITH_SUB)
    developer_requests = [r for r in records if r["kind"] == "model.request" and r["role"] == "developer"]
    assert developer_requests
    for request in developer_requests:
        before = [r for r in records if r["seq"] < request["seq"] and r["attempt_number"] == request["attempt_number"]]
        snapshots = [r for r in before if r["kind"] == "authority.snapshot"]
        assert snapshots, "authority must be recorded before the request"
        target = snapshots[-1]["payload"]["targets"][0]
        assert target["path"] == "calc.py" and target["operations"]
        assert snapshots[-1]["payload"]["write_scope_mode"] is not None
        parses = [r for r in records if r["kind"] == "developer.parse"
                  and r["payload"]["call_seq_parsed"] == request["call_seq"]]
        assert len(parses) == 1 and parses[0]["seq"] > request["seq"]
        assert parses[0]["payload"]["path"] == "calc.py" and parses[0]["payload"]["kind"] == "file"


def test_a_malformed_answer_is_recorded_as_parsed_invalid_with_its_typed_code(tmp_path, monkeypatch):
    answers = iter(["this is prose, not the response protocol"] * 2 + [CALC_WITH_SUB] * 20)
    _result, run, records = _run(tmp_path, monkeypatch, lambda request: next(answers))
    invalid = [r for r in records if r["kind"] == "developer.parse" and r["payload"]["kind"] == "invalid"]
    assert invalid, [r["payload"] for r in records if r["kind"] == "developer.parse"]
    assert invalid[0]["payload"]["reason_code"]
    # The verbatim answer is in the response of the call the parse names.
    call = invalid[0]["payload"]["call_seq_parsed"]
    response = next(r for r in records if r["kind"] == "model.response" and r["call_seq"] == call)
    assert run.blob(response["blobs"]["content"]).decode() == "this is prose, not the response protocol"


def test_model_claimed_analysis_is_content_never_envelope(tmp_path, monkeypatch):
    """The model's FIX ANALYSIS is its claim: recorded as content (a blob in
    full capture), never in the record's envelope."""
    from _protocol_responses import sentinel

    answer = sentinel("calc.py", analysis="FIX ANALYSIS: add sub", content=CALC_WITH_SUB)
    _result, run, records = _run(tmp_path, monkeypatch, lambda request: answer)
    parses = [r for r in records if r["kind"] == "developer.parse" and r["payload"]["kind"] == "file"]
    assert parses
    assert all("analysis" not in r["payload"] for r in parses)
    claimed = [r for r in parses if "analysis_model_claimed" in r["blobs"]]
    assert claimed and b"add sub" in run.blob(claimed[0]["blobs"]["analysis_model_claimed"])
