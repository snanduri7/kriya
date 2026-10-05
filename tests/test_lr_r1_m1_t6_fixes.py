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


# -- G3: a Developer call that ended without an answer -----------------------------------------

def test_g3_a_deadline_cut_call_has_no_model_answer(tmp_path, monkeypatch):
    import asyncio

    from _chaos_harness import CALC_WITH_SUB, ChaosRuntime, role_of

    class Slow(ChaosRuntime):
        async def complete(self, client, request):
            if role_of(request) == "developer":
                await asyncio.sleep(30)
            return await super().complete(client, request)
    cfg = chaos_config()
    cfg.autonomy.generation_time_budget_seconds = 3
    cfg.autonomy.generation_seconds_per_file_estimate = 0
    cfg.autonomy.generation_gate_reserve_seconds = 0
    observed = direct_run(tmp_path, monkeypatch, _always(CALC_WITH_SUB), FILES, cfg=cfg, runtime_class=Slow)
    assert observed.attempt(1)["Q4"] == {"status": "NOT_APPLICABLE", "reason": "no_model_answer: InferenceDeadlineError"}


def test_g3_a_call_refused_before_dispatch_has_no_model_answer(tmp_path, monkeypatch):
    from _t6_harness import BIG_CALC, RENAME_GOAL, output_budget_config, rename_responder

    observed = direct_run(tmp_path, monkeypatch, rename_responder, {"calc.py": BIG_CALC},
                          goal=RENAME_GOAL, cfg=output_budget_config(patch_capable=False))
    assert observed.attempt(1)["Q4"] == {"status": "NOT_APPLICABLE",
                                         "reason": "no_model_answer: OutputBudgetUnsatisfiableError"}


def _synthetic_q4(tmp_path, monkeypatch, run_id, calls):
    """``calls``: per Developer call, None (no response record), or the
    model.response payload recorded for it."""
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        pass
    context = _Context()
    context.run_id = run_id
    cfg = strict_config()
    with scope.run_scope(context):
        scope.ensure_store(cfg)
        with scope.unit_scope(cfg, "u1", "direct"):
            with scope.attempt_scope(lambda: 1):
                scope.attempt_opened({"mode": "full_set"})
                for response in calls:
                    with scope.call_scope("developer"), scope.wire_scope():
                        scope.emit("model.request", {"dispatched": True})
                        if response is not None:
                            scope.emit("model.response", response)
        scope.close_run(context, lambda: None)
    return explain_run(str(tmp_path / "state"), run_id)["attempts"][0]["answers"]["Q4"]


def test_g3_a_cancelled_call_has_no_model_answer(tmp_path, monkeypatch):
    q4 = _synthetic_q4(tmp_path, monkeypatch, "run-cancelled", [{"cancelled": True, "error_type": "CancelledError"}])
    assert q4 == {"status": "NOT_APPLICABLE", "reason": "no_model_answer: CANCELLED"}


def test_g3_a_missing_response_record_stays_not_recorded(tmp_path, monkeypatch):
    """Negative control: a dispatched call without its response record is
    missing evidence, never 'no answer existed'."""
    q4 = _synthetic_q4(tmp_path, monkeypatch, "run-missing", [{"cancelled": False, "error_type": "InferenceDeadlineError"},
                                                              None])
    assert q4["status"] == "NOT_RECORDED"


def test_g3_an_answer_that_was_returned_is_never_no_model_answer(tmp_path, monkeypatch):
    """Negative control: one call errored, another returned an answer that
    was not parsed - an answer existed, so this is missing evidence."""
    q4 = _synthetic_q4(tmp_path, monkeypatch, "run-answered",
                       [{"cancelled": False, "error_type": "InferenceDeadlineError"}, {"finish_reason": "stop"}])
    assert q4["status"] == "NOT_RECORDED"


# -- H: Q9's terminal cause follows terminal controller evidence ------------------------------

def test_h_a_controller_terminal_decision_outranks_a_later_successful_unit(tmp_path, monkeypatch):
    """Units close s1 PASS, s2 FAIL (plan-scope conflict), s1 PASS (owner
    recovery); the controller then ends the run needs_review /
    PLAN_SCOPE_REVISION_REQUIRED. That decision is the terminal cause."""
    from _t6_harness import scope_enforce

    observed = scope_enforce(tmp_path, monkeypatch)
    q9 = observed.explained["Q9"]
    assert [(u["unit_id"], u["quality_gates_passed"]) for u in q9["units"]] == [
        ("s1", True), ("s2", False), ("s1", True)]                   # the rerun stays visible in history
    cause = q9["terminal_cause"]
    assert cause["source"] == "controller_terminal_event" and cause["kind"] == "planning.failed"
    assert cause["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"]
    deciding = cause["deciding_subtask"]
    assert deciding["subtask_id"] == "s2" and deciding["status"] == "needs_review"
    assert "app/lib.py" in deciding["error"]                         # the out-of-scope file
    assert q9["plan_scope_conflicts"][0]["required_files"] == ["app/lib.py"]


def _shop_integration_plan():
    import test_enforce_verified_no_change as shape

    from kriya.workflow.plan_schema import EngineeringPlan

    plan = shape._plan(shape.TOOL_CRITERION).model_dump()
    plan["integration_relationships"] = [{
        "id": "ir1", "kind": "uses", "producer_subtask_ids": ["s1"], "consumer_subtask_ids": ["s2"],
        "relationship_statement": "the controller uses the cached service"}]
    return EngineeringPlan.model_validate(plan)


def test_h_a_failed_terminal_gate_outranks_a_successful_last_unit(tmp_path, monkeypatch):
    """LR-R1-P5's shape: every unit passes (s2 verified NO_CHANGE), then the
    terminal obligations gate fails. The terminal gate is the cause."""
    from _t6_harness import shop_enforce

    observed = shop_enforce(tmp_path, monkeypatch, plans=[_shop_integration_plan])
    assert all(r.status.value == "completed" for r in observed.result.subtask_results)
    q9 = observed.explained["Q9"]
    assert q9["units"][-1]["quality_gates_passed"] is True
    cause = q9["terminal_cause"]
    assert cause["source"] == "terminal_gates"
    assert [g["gate"] for g in cause["failed_gates"]] == ["terminal_obligations"]


def test_h_a_successful_enforce_run_has_no_manufactured_failure(tmp_path, monkeypatch):
    from _t6_harness import shop_enforce

    observed = shop_enforce(tmp_path, monkeypatch)
    late = [r for r in observed.of("mirror.event") if r["payload"].get("source") == "workflow_controller.enforce"]
    assert late, "the enforce terminal wrote its (non-terminal) events"   # e.g. requirement.verdicts
    assert observed.explained["Q9"]["terminal_cause"] == {
        "failure_category": None, "quality_gates_passed": True, "unit_outcome": "RETURNED"}


def _synthetic_q9(tmp_path, monkeypatch, run_id, build, terminal_status):
    from kriya.core.attempt_evidence import scope
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR
    from tests._strict_doubles import strict_config

    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))

    class _Context:
        pass
    context = _Context()
    context.run_id = run_id

    class _Record:
        pass
    run_record = _Record()
    run_record.terminal_status = terminal_status
    cfg = strict_config()
    with scope.run_scope(context):
        scope.ensure_store(cfg)
        build(cfg, scope)
        scope.close_run(context, lambda: run_record)
    return explain_run(str(tmp_path / "state"), run_id)["Q9"]


def _controller_event(scope, kind, reason_codes):
    scope.mirror_event({"kind": kind, "attempt": 0, "source": "workflow_controller.enforce",
                        "authority": "authoritative", "details": {"reason_codes": reason_codes}})


def test_h_the_latest_controller_terminal_event_wins(tmp_path, monkeypatch):
    def build(cfg, scope):
        with scope.unit_scope(cfg, "s1", "structured") as closing:
            closing.update(failure_category=None, quality_gates_passed=True)
        _controller_event(scope, "planning.failed", ["EARLIER"])
        _controller_event(scope, "planning.failed", ["THE_TERMINAL_ONE"])
    cause = _synthetic_q9(tmp_path, monkeypatch, "run-latest", build, "FAILURE")["terminal_cause"]
    assert cause["reason_codes"] == ["THE_TERMINAL_ONE"]


def test_h_a_success_shaped_last_unit_never_explains_a_failed_run(tmp_path, monkeypatch):
    """Guard: the run failed, nothing recorded names why - the last unit's
    success is never presented as the terminal cause."""
    def build(cfg, scope):
        with scope.unit_scope(cfg, "s1", "structured") as closing:
            closing.update(failure_category=None, quality_gates_passed=True)
    cause = _synthetic_q9(tmp_path, monkeypatch, "run-guard", build, "FAILURE")["terminal_cause"]
    assert cause["status"] == "NOT_RECORDED"
    assert cause["last_unit"] == {"failure_category": None, "quality_gates_passed": True, "unit_outcome": "RETURNED"}


def test_h_the_deciding_subtask_is_the_last_one_that_did_not_complete(tmp_path, monkeypatch):
    """A completed subtask decision recorded after the failing one (an owner
    recovery rerun) never becomes the deciding subtask."""
    class _Decision:
        def __init__(self, **value):
            self.value = value

        def to_dict(self):
            return dict(self.value)

    def build(cfg, scope):
        scope.mirror_decision(_Decision(type="subtask_attempt", subtask_id="s2", status="needs_review",
                                        error="grounded required files ['app/lib.py'] are outside scope"))
        scope.mirror_decision(_Decision(type="subtask_attempt", subtask_id="s1", status="completed"))
        _controller_event(scope, "planning.failed", ["PLAN_SCOPE_REVISION_REQUIRED"])
    cause = _synthetic_q9(tmp_path, monkeypatch, "run-deciding", build, "FAILURE")["terminal_cause"]
    assert cause["deciding_subtask"]["subtask_id"] == "s2" and cause["deciding_subtask"]["status"] == "needs_review"
