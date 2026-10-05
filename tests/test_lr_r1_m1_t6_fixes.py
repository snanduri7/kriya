"""LR-R1-M1 T6 defect fixes A-F: one test group per defect, each written to
fail before its fix (design §12 T6; owner authorization 2026-10-05)."""
from _chaos_harness import CALC, TEST_SUB, benign_roles, chaos_config
from _t6_harness import direct_run

from kriya.workflow.retry_progress import NO_PROGRESS_TERMINAL_REASON

WRONG_SUB = CALC + "\n\ndef sub(a, b):\n    return a + b\n"
FILES = {"calc.py": CALC, "test_calc.py": TEST_SUB}


def _always(answer):
    return lambda role, request: answer if role == "developer" else benign_roles(role, request)


# -- A: no-progress terminal evidence ----------------------------------------------------------

def test_a_no_progress_terminal_records_its_classification_and_reason(tmp_path, monkeypatch):
    observed = direct_run(tmp_path, monkeypatch, _always(WRONG_SUB), FILES)
    progress = observed.result["retry_progress"]
    assert progress["no_progress_terminated"] is True
    decisions = observed.of("recovery.decision")
    last = decisions[-1]["payload"]
    # The runtime's own already-computed facts, not a recomputation.
    assert last["progress_classification"] == progress["classification"]
    assert last["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON == progress["terminal_reason"]
    assert last["no_progress_terminated"] is True and last["retry"] is False
    # Every earlier decision carries its own classification (the progression).
    assert all("progress_classification" in d["payload"] for d in decisions)
    assert all(d["payload"]["no_progress_reason"] is None for d in decisions[:-1])
    # explain: Q6 of the last attempt says why it stopped; Q9 names the terminal cause.
    final = observed.attempt(observed.attempts()[-1]["attempt"])
    [why] = final["Q6"]["items"]
    assert why["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert why["progress_classification"] == progress["classification"]
    q9 = observed.explained["Q9"]
    assert q9["last_recovery_decision"]["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON
    assert q9["last_recovery_decision"]["progress_classification"] == progress["classification"]


def test_a_recording_the_terminal_changes_no_retry_behaviour(tmp_path, monkeypatch):
    """The same scenario with the recorder off: identical attempt count, model
    calls and progress outcome."""
    (tmp_path / "on").mkdir()
    with_recorder = direct_run(tmp_path / "on", monkeypatch, _always(WRONG_SUB), FILES)
    cfg = chaos_config()
    cfg.evidence.attempt_recorder.capture = "off"
    (tmp_path / "off").mkdir()
    from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_engine, git_workspace, run_direct

    from kriya.core.state_paths import ENV_STATE_DIR

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "off" / "state"))
    runtime = ChaosRuntime(_always(WRONG_SUB))
    with RuntimeRegistration(runtime):
        without = run_direct(chaos_engine(cfg), "add sub to calc.py", git_workspace(tmp_path / "off", FILES))
    # last_vector_digest covers failure text that names the (different)
    # absolute workspace path; byte identity at one path is the I-2 suite's.
    def comparable(progress):
        return {k: v for k, v in progress.items() if k != "last_vector_digest"}
    assert comparable(without["retry_progress"]) == comparable(with_recorder.result["retry_progress"])
    for key in ("failure_category", "quality_gates_passed", "candidate_gates_passed"):
        assert without.get(key) == with_recorder.result.get(key), key
    # The same model calls in the same order: the same attempts and retries.
    assert runtime.roles == with_recorder.runtime.roles


# -- B: refused proposal / NO_CHANGE / genuinely absent ---------------------------------------

def test_b_a_proposal_refused_before_staging_is_a_refused_candidate_without_invented_bytes(tmp_path, monkeypatch):
    """A10: on the repair attempts the Developer answers a whole file where the
    operation contract requires a patch; the answer for calc.py is refused
    (it does not parse as the required protocol) and no candidate is staged."""
    observed = direct_run(tmp_path, monkeypatch, _always(WRONG_SUB), FILES)
    refused_attempts = [a for a in observed.attempts()
                        if any(d["type"] == "operation_contract" for d in a["answers"]["Q5"].get("diagnoses") or [])]
    assert refused_attempts, [a["answers"]["Q5"] for a in observed.attempts()]
    for attempt in refused_attempts:
        q4 = attempt["answers"]["Q4"]
        assert q4["status"] == "RECORDED", q4
        [change] = q4["items"]
        assert change["decision"] == "REFUSED" and change["candidate_staged"] is False
        # The refusal's own typed reason code, the one its diagnosis carries.
        [diagnosis] = [d for d in attempt["answers"]["Q5"]["diagnoses"] if d["type"] == "operation_contract"]
        assert change["path"] == "calc.py" and change["reason_code"] == diagnosis["reason_code"]
        assert change["diff"] == "NOT_APPLICABLE" and change["after_digest"] == "NOT_APPLICABLE"
        assert "content" not in change                       # no bytes invented
        # The answer did not parse as the patch the contract required.
        assert change["proposal_kind"] == "invalid" and change["parse_reason_code"] == "MODEL_EDIT_PROTOCOL_INVALID"
        # Bound to the parse that produced the proposal.
        parse = next(r for r in observed.records if r["seq"] == change["parse_seq"])
        assert parse["kind"] == "developer.parse" and parse["payload"]["path"] == "calc.py"
        assert parse["attempt_number"] == attempt["attempt"]


def test_b_a_verified_no_change_is_not_missing_evidence(tmp_path, monkeypatch):
    from _t6_harness import shop_enforce

    observed = shop_enforce(tmp_path, monkeypatch)
    statuses = {r.subtask_id: r for r in observed.result.subtask_results}
    assert "VERIFIED_NO_CHANGE" in statuses["s2"].reason_codes
    q4 = observed.attempt(1, unit="s2")["Q4"]
    assert q4 == {"status": "NOT_APPLICABLE", "reason": "model_proposed_no_change"}


def test_b_genuinely_absent_candidate_evidence_stays_not_recorded(tmp_path, monkeypatch):
    """A Developer request whose answer was never parsed and no candidate
    decision: the only state that is reported as missing evidence."""
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        run_id = "run-absent"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                with scope.call_scope("developer"):
                    scope.emit("model.request", {"dispatched": True})
        scope.close_run(_Context(), lambda: None)
    q4 = explain_run(str(tmp_path / "state"), "run-absent")["attempts"][0]["answers"]["Q4"]
    assert q4["status"] == "NOT_RECORDED" and "no candidate" in q4["reason"]


def test_b_an_unverified_no_change_is_still_no_proposal_never_a_refused_candidate(tmp_path, monkeypatch):
    """A judgment criterion cannot verify NO_CHANGE: the attempt fails, but
    the model still proposed no mutation - no REFUSED candidate is invented."""
    import test_enforce_verified_no_change as shape
    from _t6_harness import shop_enforce

    observed = shop_enforce(tmp_path, monkeypatch, plans=[lambda: shape._plan(shape.JUDGMENT_CRITERION)])
    s2 = [a for a in observed.attempts() if a["unit_id"] == "s2"]
    assert s2 and any(a["answers"]["Q6"]["status"] == "RECORDED" for a in s2), "the no-change attempt must fail"
    for attempt in s2:
        assert attempt["answers"]["Q4"] == {"status": "NOT_APPLICABLE", "reason": "model_proposed_no_change"}
    assert not [r for r in observed.of("candidate.change", unit_id="s2")]


def test_b_a_parsed_proposal_without_a_candidate_decision_is_missing_evidence(tmp_path, monkeypatch):
    """Negative control: a parsed file proposal, then no staging or refusal
    record (an interrupted attempt) is NOT_RECORDED, never NO_CHANGE."""
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        run_id = "run-interrupted"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                scope.emit("developer.parse", {"path": "calc.py", "kind": "file"})
        scope.close_run(_Context(), lambda: None)
    q4 = explain_run(str(tmp_path / "state"), "run-interrupted")["attempts"][0]["answers"]["Q4"]
    assert q4["status"] == "NOT_RECORDED"


# -- C: the terminal cause in Q9 ---------------------------------------------------------------

def _refusing_reviewer(monkeypatch):
    from test_prompt_budget_fit_001c import RefusingReviewer

    from kriya.core.llm import LLMClient

    refusing = RefusingReviewer()
    monkeypatch.setattr(LLMClient, "_dispatch_budget", lambda client, **kw: refusing(client, **kw))
    return refusing


def test_c_a_final_review_refusal_is_the_q9_terminal_cause(tmp_path, monkeypatch):
    from _chaos_harness import CALC_WITH_SUB

    refusing = _refusing_reviewer(monkeypatch)
    observed = direct_run(tmp_path, monkeypatch, _always(CALC_WITH_SUB), FILES)
    assert refusing.refused == 1 and observed.result["failure_category"] == "final_review_refused"
    q9 = observed.explained["Q9"]
    assert q9["terminal_cause"] == {"failure_category": "final_review_refused", "quality_gates_passed": False,
                                    "unit_outcome": "RETURNED"}
    assert q9["items"][0]["commit_result"] == "COMMITTED"          # what already happened stays visible


def test_c_an_ordinary_terminal_failure_is_the_q9_terminal_cause(tmp_path, monkeypatch):
    observed = direct_run(tmp_path, monkeypatch, _always(WRONG_SUB), FILES)
    cause = observed.explained["Q9"]["terminal_cause"]
    assert cause["failure_category"] == observed.result["failure_category"] == "no_progress"
    assert cause["quality_gates_passed"] is False


def test_c_a_successful_unit_is_the_q9_terminal_cause(tmp_path, monkeypatch):
    from _chaos_harness import CALC_WITH_SUB

    observed = direct_run(tmp_path, monkeypatch, _always(CALC_WITH_SUB), FILES)
    cause = observed.explained["Q9"]["terminal_cause"]
    assert cause == {"failure_category": None, "quality_gates_passed": True, "unit_outcome": "RETURNED"}


def test_c_a_terminal_cause_is_never_invented(tmp_path, monkeypatch):
    """Negative control: a closed unit that recorded no result fields says
    so; nothing is read from anywhere else."""
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        run_id = "run-no-cause"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            pass
        scope.close_run(_Context(), lambda: None)
    cause = explain_run(str(tmp_path / "state"), "run-no-cause")["Q9"]["terminal_cause"]
    assert cause == {"status": "NOT_RECORDED", "reason": "the last unit recorded no result fields"}


def test_c_an_escaped_exception_is_the_q9_terminal_condition(tmp_path, monkeypatch):
    import pytest
    from _chaos_harness import CALC_WITH_SUB, ChaosRuntime, RuntimeRegistration, chaos_engine, git_workspace, run_direct

    import kriya.agents.agent as agent
    from kriya.core.attempt_evidence import reader
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR

    async def crash(self, *args, **kwargs):
        raise RuntimeError("injected reviewer crash")
    monkeypatch.setattr(agent.ReviewerAgent, "run", crash)
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))
    runtime = ChaosRuntime(_always(CALC_WITH_SUB))
    with RuntimeRegistration(runtime), pytest.raises(RuntimeError, match="injected reviewer crash"):
        run_direct(chaos_engine(chaos_config()), "add sub to calc.py", git_workspace(tmp_path, FILES))
    [run_id] = reader.list_runs(str(tmp_path / "state"))
    explained = explain_run(str(tmp_path / "state"), run_id)
    assert explained["verification"] == reader.VERIFIED and explained["sealed"] is True
    assert explained["Q9"]["terminal_cause"] == {"status": "NOT_RECORDED", "reason": "the last unit recorded no result fields",
                                                 "unit_outcome": "EXCEPTION", "error_type": "RuntimeError"}


def test_c_the_terminal_cause_is_the_last_units_not_an_earlier_ones(tmp_path, monkeypatch):
    """Enforce: s1 succeeds, s2 (an unverifiable NO_CHANGE) fails last; the
    terminal cause is s2's own recorded result."""
    import test_enforce_verified_no_change as shape
    from _t6_harness import shop_enforce

    observed = shop_enforce(tmp_path, monkeypatch, plans=[lambda: shape._plan(shape.JUDGMENT_CRITERION)])
    closed = observed.of("unit.closed")
    assert [c["unit_id"] for c in closed][-1] == "s2"
    assert closed[0]["payload"]["quality_gates_passed"] is True       # s1
    cause = observed.explained["Q9"]["terminal_cause"]
    assert cause["quality_gates_passed"] is False
    assert cause["failure_category"] == closed[-1]["payload"]["failure_category"]


# -- D: the enforce TOOL subtask in the evidence hierarchy ------------------------------------

def test_d_an_executed_tool_subtask_is_a_unit_with_its_action_result(tmp_path, monkeypatch):
    from _t6_harness import echo_tool, shop_enforce, tool_plan

    observed = shop_enforce(tmp_path, monkeypatch, plans=[tool_plan], tools={"t6_echo": echo_tool()})
    by_id = {r.subtask_id: r for r in observed.result.subtask_results}
    assert by_id["s0"].status.value == "completed"
    opened = observed.of("unit.opened", unit_id="s0")
    assert len(opened) == 1 and opened[0]["payload"]["unit_kind"] == "tool"
    [execution] = observed.of("tool.execution", unit_id="s0")
    assert execution["attempt_number"] == 1 and execution["payload"]["tool_name"] == "t6_echo"
    assert execution["payload"]["status"] == "completed" and execution["call_seq"] is None
    assert b"echoed" in observed.run.blob(execution["blobs"]["tool_output"])
    [closed] = observed.of("unit.closed", unit_id="s0")
    assert closed["payload"]["tool_status"] == "completed"
    answers = observed.attempt(1, unit="s0")
    for label in ("Q1", "Q2", "Q3", "Q8"):
        assert answers[label]["status"] == "NOT_APPLICABLE" and answers[label]["reason"].startswith("no_model_call")
    assert answers["Q4"] == {"status": "NOT_APPLICABLE", "reason": "tool_action: no Developer candidate"}
    assert answers["Q5"] == {"status": "NOT_APPLICABLE", "reason": "no_verification_gate"}
    assert answers["Q6"] == {"status": "NOT_APPLICABLE", "reason": "tool_subtask: executed once, never retried"}
    # No model call was made inside the tool unit.
    assert not [r for r in observed.records if r["kind"] == "model.request" and r["unit_id"] == "s0"]


def test_d_a_failed_tool_subtask_is_the_terminal_condition(tmp_path, monkeypatch):
    from _t6_harness import echo_tool, shop_enforce, tool_plan

    observed = shop_enforce(tmp_path, monkeypatch, plans=[tool_plan], tools={"t6_echo": echo_tool(fail=True)})
    assert [r.subtask_id for r in observed.result.subtask_results] == ["s0"]      # the run stopped there
    [execution] = observed.of("tool.execution", unit_id="s0")
    assert execution["payload"]["status"] == "failed" and "echo failed" in execution["payload"]["error"]
    assert observed.explained["Q9"]["terminal_cause"] == {"tool_status": "failed", "unit_outcome": "RETURNED"}


# -- E: refused-before-dispatch and no-model-call semantics -----------------------------------

def test_e_a_call_refused_before_dispatch_is_not_a_missing_response(tmp_path, monkeypatch):
    from _t6_harness import RENAME_GOAL, output_budget_config, rename_responder

    observed = direct_run(tmp_path, monkeypatch, rename_responder, {"calc.py": __import__("_t6_harness").BIG_CALC},
                          goal=RENAME_GOAL, cfg=output_budget_config(patch_capable=False))
    first = observed.attempt(1)
    [request] = first["Q1"]["items"]
    assert request["dispatched"] is False and request["refusal_type"] == "OutputBudgetUnsatisfiableError"
    assert first["Q3"] == {"status": "NOT_APPLICABLE",
                           "reason": "provider_not_dispatched: OutputBudgetUnsatisfiableError"}


def test_e_an_undispatched_call_beside_a_dispatched_one_is_listed_as_such(tmp_path, monkeypatch):
    from _t6_harness import BIG_CALC, RENAME_GOAL, output_budget_config, rename_responder

    observed = direct_run(tmp_path, monkeypatch, rename_responder, {"calc.py": BIG_CALC},
                          goal=RENAME_GOAL, cfg=output_budget_config(patch_capable=True))
    q3 = observed.attempt(1)["Q3"]
    assert q3["status"] == "RECORDED" and len(q3["items"]) == 1
    [refused] = q3["not_dispatched"]
    assert refused["refusal_type"] == "OutputBudgetUnsatisfiableError"
    assert refused["call_seq"] != q3["items"][0]["call_seq"]


def test_e_a_dispatched_call_without_its_response_is_missing_evidence(tmp_path, monkeypatch):
    """Negative control: the provider was called, its response was not
    recorded - the only case reported as missing."""
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        run_id = "run-lost-response"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                with scope.call_scope("developer"):
                    scope.emit("model.request", {"dispatched": True})
        scope.close_run(_Context(), lambda: None)
    q3 = explain_run(str(tmp_path / "state"), "run-lost-response")["attempts"][0]["answers"]["Q3"]
    assert q3 == {"status": "NOT_RECORDED", "reason": "the provider was called but its response was not recorded"}


def test_e_an_attempt_that_made_no_model_call_is_not_applicable(tmp_path, monkeypatch):
    """The generation budget admission stops the attempt before any call."""
    cfg = chaos_config()
    cfg.autonomy.generation_time_budget_seconds = 2
    from _chaos_harness import CALC_WITH_SUB

    observed = direct_run(tmp_path, monkeypatch, _always(CALC_WITH_SUB), FILES, cfg=cfg)
    answers = observed.attempt(1)
    assert [d["type"] for d in answers["Q5"]["diagnoses"]] == ["time_budget_exhausted"]
    for label in ("Q1", "Q2", "Q3"):
        assert answers[label]["status"] == "NOT_APPLICABLE" and answers[label]["reason"].startswith("no_model_call")
    # The model for the call was decided before admission stopped it: a fact.
    assert answers["Q8"]["status"] == "RECORDED" and answers["Q8"]["items"][0]["phase"] == "call"


# -- F: authority is per call, including a within-attempt protocol fallback -------------------

def test_f_a_protocol_fallback_call_has_its_own_authority_snapshot(tmp_path, monkeypatch):
    from _t6_harness import BIG_CALC, RENAME_GOAL, output_budget_config, rename_responder

    observed = direct_run(tmp_path, monkeypatch, rename_responder, {"calc.py": BIG_CALC},
                          goal=RENAME_GOAL, cfg=output_budget_config(patch_capable=True))
    calls = [item for item in observed.attempt(1)["Q1"]["items"] if item["role"] == "developer"]
    assert len(calls) == 2
    refused, patched = calls
    assert refused["dispatched"] is False and refused["refusal_type"] == "OutputBudgetUnsatisfiableError"
    assert patched["dispatched"] is True
    # Each call carries the authority it was actually given.
    assert refused["requested_operations"] == {"calc.py": "repair_with_full_file"}
    assert patched["requested_operations"] == {"calc.py": "repair_with_patch"}
    assert refused["authority_snapshot_seq"] != patched["authority_snapshot_seq"]
    snapshots = {r["seq"]: r for r in observed.of("authority.snapshot")}
    second = snapshots[patched["authority_snapshot_seq"]]["payload"]
    assert second["transition"] == {"reason": "output_budget_protocol_fallback",
                                    "previous_snapshot_seq": refused["authority_snapshot_seq"],
                                    "changed": {"calc.py": "repair_with_patch"}}
    assert snapshots[patched["authority_snapshot_seq"]]["seq"] < next(
        r["seq"] for r in observed.of("model.request") if r["call_seq"] == patched["call_seq"])
    # Q2 shows both snapshots.
    assert len(observed.attempt(1)["Q2"]["items"]) == 2


def test_f_a_call_without_a_transition_keeps_one_snapshot(tmp_path, monkeypatch):
    """Negative control: an attempt whose single Developer call needs no
    fallback has exactly one snapshot, bound to that call."""
    from _chaos_harness import CALC_WITH_SUB

    observed = direct_run(tmp_path, monkeypatch, _always(CALC_WITH_SUB), FILES)
    [call] = [item for item in observed.attempt(1)["Q1"]["items"] if item["role"] == "developer"]
    [snapshot] = observed.of("authority.snapshot")
    assert call["authority_snapshot_seq"] == snapshot["seq"] and "transition" not in snapshot["payload"]


def test_f_a_transition_names_only_operations_that_changed(tmp_path, monkeypatch):
    from kriya.core.attempt_evidence import reader, scope
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        run_id = "run-transition"
    cfg = strict_config()
    with scope.run_scope(_Context()):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                scope.emit("authority.snapshot", {"targets": [
                    {"path": "a.py", "requested_operation": "repair_with_full_file"},
                    {"path": "b.py", "requested_operation": "repair_with_patch"}]})
                scope.record_authority_transition("x", {"a.py": "repair_with_full_file",
                                                        "b.py": "repair_with_patch"})   # nothing changed
                scope.record_authority_transition("x", {"a.py": "repair_with_patch", "b.py": "repair_with_patch"})
        scope.close_run(_Context(), lambda: None)
    snapshots = [r for r in reader.open_run(str(tmp_path / "state"), "run-transition").records()
                 if r["kind"] == "authority.snapshot"]
    assert len(snapshots) == 2                          # the no-change call recorded nothing
    second = snapshots[1]["payload"]
    assert second["transition"]["changed"] == {"a.py": "repair_with_patch"}
    assert [t["requested_operation"] for t in second["targets"]] == ["repair_with_patch", "repair_with_patch"]


# -- G1: the recorded plan-scope conflict in Q6 and Q9 ----------------------------------------

def test_g1_the_recorded_plan_scope_conflict_is_explained(tmp_path, monkeypatch):
    from _t6_harness import conflict_attempt, scope_enforce

    observed = scope_enforce(tmp_path, monkeypatch)
    expected = {"reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "required_files": ["app/lib.py"]}
    attempt = conflict_attempt(observed)
    recorded = next(r for r in observed.of("recovery.decision", unit_id="s2", attempt_number=attempt["attempt"]))
    assert recorded["payload"]["plan_scope_conflict"] == expected               # what the recorder holds
    [decision] = attempt["answers"]["Q6"]["items"]
    assert decision["plan_scope_conflict"] == expected and decision["retry"] is False
    conflicts = observed.explained["Q9"]["plan_scope_conflicts"]
    assert conflicts == [{"seq": recorded["seq"], "unit_id": "s2", "attempt": attempt["attempt"], **expected}]


def test_g1_runs_without_a_conflict_list_none(tmp_path, monkeypatch):
    """Negative control: nothing is listed when no decision recorded one."""
    observed = direct_run(tmp_path, monkeypatch, _always(WRONG_SUB), FILES)
    assert observed.explained["Q9"]["plan_scope_conflicts"] == []
    assert all("plan_scope_conflict" not in item for a in observed.attempts()
               for item in a["answers"]["Q6"].get("items") or [])


# -- G2: the recorded authorized write scope in Q2 --------------------------------------------

def test_g2_the_recorded_authorized_write_scope_is_explained(tmp_path, monkeypatch):
    from _t6_harness import conflict_attempt, scope_enforce

    observed = scope_enforce(tmp_path, monkeypatch)
    attempt = conflict_attempt(observed)
    recorded = [r for r in observed.of("authority.snapshot", unit_id="s2", attempt_number=attempt["attempt"])]
    shown = attempt["answers"]["Q2"]["items"]
    assert len(shown) == len(recorded) >= 1
    for item, snapshot in zip(shown, recorded, strict=True):
        # Exactly the snapshot's own value - never the plan or the targets.
        assert item["authorized_write_scope"] == snapshot["payload"]["authorized_write_scope"] == ["app/config.py"]
        assert item["write_scope_mode"] == "allowlist"
        assert {t["path"]: t["in_write_scope"] for t in item["targets"]} == {"app/config.py": True}


def test_g2_an_unrestricted_scope_is_shown_as_recorded(tmp_path, monkeypatch):
    """Negative control: a direct run records an empty authorized scope (no
    allowlist); it is shown as that, never filled from the targets."""
    from _chaos_harness import CALC_WITH_SUB

    observed = direct_run(tmp_path, monkeypatch, _always(CALC_WITH_SUB), FILES)
    [item] = observed.attempt(1)["Q2"]["items"]
    [snapshot] = observed.of("authority.snapshot")
    assert item["authorized_write_scope"] == snapshot["payload"]["authorized_write_scope"]
    assert item["authorized_write_scope"] != [t["path"] for t in item["targets"]]
