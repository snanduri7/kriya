"""LR-R1-M1 T6: the ten design paths, end to end through ``explain``
(design §12 T6; owner authorization 2026-10-05).

Each path runs deterministically through the real pipeline with only the
model runtime scripted (tests/_t6_harness.py) and is read back through the
evidence reader. Every Q1-Q9 cell must be answered with recorded evidence
(PASS) or a typed NOT_APPLICABLE reason; a NOT_RECORDED cell fails unless
the runtime genuinely did not record the fact.
"""
import json
import os

import pytest
from _chaos_harness import CALC, CALC_WITH_SUB, TEST_SUB, benign_roles, chaos_config, requested_file
from _protocol_responses import sentinel
from _t6_harness import (
    BIG_CALC,
    RENAME_GOAL,
    direct_run,
    output_budget_config,
    rename_responder,
    shop_enforce,
)

from kriya.workflow.retry_progress import NO_PROGRESS_TERMINAL_REASON

FILES = {"calc.py": CALC, "test_calc.py": TEST_SUB}
QUESTIONS = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8")


def cell(answer):
    status = answer["status"]
    if status == "RECORDED":
        assert answer.get("items") or any(answer.get(k) for k in answer if k not in ("status", "items")), answer
        return "PASS"
    assert isinstance(answer.get("reason"), str) and answer["reason"].strip(), answer
    return f"{status}({answer['reason']})"


def assert_row(answers, *, allowed_not_recorded=()):
    """Every cell PASS or NOT_APPLICABLE(<typed>); returns the row."""
    row = {label: cell(answers[label]) for label in QUESTIONS}
    for label, value in row.items():
        if value.startswith("NOT_RECORDED"):
            assert label in allowed_not_recorded, (label, value)
    return row


# path -> Q1-Q9 cells, filled by each test; written as JSON when
# KRIYA_T6_MATRIX is set (run without xdist to collect every row).
MATRIX = {}


@pytest.fixture(scope="module", autouse=True)
def _write_matrix():
    yield
    target = os.environ.get("KRIYA_T6_MATRIX")
    if target:
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(MATRIX, handle, indent=1, sort_keys=True)


def record(path, answers, explained, **row_options):
    row = assert_row(answers, **row_options)
    row["Q9"] = cell(explained["Q9"])
    MATRIX[path] = row
    return row


def _always(answer):
    return lambda role, request: answer if role == "developer" else benign_roles(role, request)


# 1. iterative per-file Developer ---------------------------------------------------------------

TWO_FILE_DESIGN = "Design: sub in calc.py, a constant in helper.py.\n```json\n" + json.dumps(
    {"files": ["calc.py", "helper.py"]}) + "\n```\n"


def test_t6_iterative_per_file_developer(tmp_path, monkeypatch):
    def responder(role, request):
        if role == "architect":
            return TWO_FILE_DESIGN
        if role == "developer":
            return "X = 2\n" if requested_file(request) == "helper.py" else CALC_WITH_SUB
        return benign_roles(role, request)
    observed = direct_run(tmp_path, monkeypatch, responder, {**FILES, "helper.py": "X = 1\n"})
    answers = observed.attempt(1)
    row = record("iterative per-file", answers, observed.explained)
    developer = [i for i in answers["Q1"]["items"] if i["role"] == "developer"]
    assert len(developer) == 2 and len({i["call_seq"] for i in developer}) == 2     # never collapsed
    assert len(answers["Q3"]["items"]) == 2 and {p["path"] for p in answers["Q3"]["parses"]} == {"calc.py", "helper.py"}
    assert {c["path"] for c in answers["Q4"]["items"]} == {"calc.py", "helper.py"}
    assert row["Q5"].startswith("NOT_APPLICABLE(every recorded check passed")
    assert observed.explained["Q9"]["terminal_cause"]["quality_gates_passed"] is True


# 2. investigation turns -------------------------------------------------------------------------

def test_t6_investigation_turns(tmp_path, monkeypatch):
    from _chaos_harness import plausible_message_tokens  # noqa: F401 - harness token helper is importable

    from kriya.config.config import ModelCapabilities
    from kriya.core.inference_runtime import ChatResponse, RawToolCall

    cfg = chaos_config()
    cfg.autonomy.developer_investigation_enabled = True
    cfg.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    turns = [ChatResponse(content="", finish_reason="tool_calls", prompt_tokens=50, completion_tokens=2,
                          tool_calls=[RawToolCall("c1", "find_symbol", json.dumps({"symbol": "add"}))])]

    def responder(role, request):
        if role == "investigation":
            return turns.pop(0) if turns else "Ready to implement."
        return CALC_WITH_SUB if role == "developer" else benign_roles(role, request)
    observed = direct_run(tmp_path, monkeypatch, responder, FILES, cfg=cfg)
    answers = observed.attempt(1)
    record("investigation turns", answers, observed.explained)
    requests = [r for r in observed.of("model.request") if r["attempt_number"] == 1]
    investigation = [r for r in requests if r["payload"]["tools"] > 0]
    assert investigation and "investigation" in observed.runtime.roles
    # Each investigation turn is its own call (and wire), inside attempt 1.
    identities = [(r["call_seq"], r["wire_seq"]) for r in requests]
    assert len(identities) == len(set(identities)) and len(requests) >= 2
    assert {i["call_seq"] for i in answers["Q1"]["items"]} == {r["call_seq"] for r in requests}


# 3. output-budget lower-protocol retry ----------------------------------------------------------

def test_t6_output_budget_retry(tmp_path, monkeypatch):
    observed = direct_run(tmp_path, monkeypatch, rename_responder, {"calc.py": BIG_CALC}, goal=RENAME_GOAL,
                          cfg=output_budget_config(patch_capable=True))
    answers = observed.attempt(1)
    record("output-budget retry", answers, observed.explained)
    refused, patched = [i for i in answers["Q1"]["items"] if i["role"] == "developer"]
    assert (refused["dispatched"], refused["refusal_type"]) == (False, "OutputBudgetUnsatisfiableError")
    assert refused["requested_operations"] == {"calc.py": "repair_with_full_file"}
    assert patched["dispatched"] is True and patched["requested_operations"] == {"calc.py": "repair_with_patch"}
    assert refused["authority_snapshot_seq"] != patched["authority_snapshot_seq"]
    assert [t["transition"]["reason"] for t in answers["Q2"]["items"] if t.get("transition")] == [
        "output_budget_protocol_fallback"]
    assert [i["refusal_type"] for i in answers["Q3"]["not_dispatched"]] == ["OutputBudgetUnsatisfiableError"]
    assert observed.explained["Q9"]["terminal_cause"]["quality_gates_passed"] is True


# 4. REPEATED_VECTOR ----------------------------------------------------------------------------

def test_t6_repeated_vector(tmp_path, monkeypatch):
    """A wrong candidate, then NO_CHANGE repairs: the workspace and the
    failure repeat, so the real classifier sees an already-seen vector."""
    wrong = CALC + "\n\ndef sub(a, b):\n    return a + b\n"
    calls = []

    def responder(role, request):
        if role != "developer":
            return benign_roles(role, request)
        calls.append(1)
        if len(calls) == 1:
            return wrong
        return sentinel("calc.py", analysis="FIX ANALYSIS: sub is already correct.", no_change=True)
    observed = direct_run(tmp_path, monkeypatch, responder, FILES)
    assert observed.result["retry_progress"]["classification"] == "REPEATED_VECTOR"
    attempts = observed.attempts()
    progression = []
    first_candidate = None
    for attempt in attempts:
        answers = attempt["answers"]
        assert_row(answers)
        [decision] = answers["Q6"]["items"]
        progression.append(decision["progress_classification"])
        if attempt["attempt"] > 1:
            # The model answered NO_CHANGE; the gates re-verified the candidate
            # still in the sandbox (attempt 1's), which is what Q4 records.
            assert [p["kind"] for p in answers["Q3"]["parses"]] == ["no_change"]
            [staged] = answers["Q4"]["items"]
            assert staged["decision"] == "STAGED" and staged["after_digest"] == first_candidate
        else:
            [staged] = answers["Q4"]["items"]
            first_candidate = staged["after_digest"]
    assert progression == ["PROGRESS", "REPEATED_ACTION", "REPEATED_VECTOR", "REPEATED_VECTOR"]
    record("REPEATED_VECTOR", attempts[-1]["answers"], observed.explained)
    final = attempts[-1]["answers"]["Q6"]["items"][0]
    assert final["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON and final["retry"] is False
    q9 = observed.explained["Q9"]
    assert q9["terminal_cause"]["failure_category"] == "no_progress"
    assert q9["last_recovery_decision"]["progress_classification"] == "REPEATED_VECTOR"
    assert q9["last_recovery_decision"]["no_progress_reason"] == NO_PROGRESS_TERMINAL_REASON


# 5. plan-scope conflict -------------------------------------------------------------------------

def test_t6_plan_scope_conflict(tmp_path, monkeypatch):
    from _t6_harness import conflict_attempt, scope_enforce

    observed = scope_enforce(tmp_path, monkeypatch)
    by_id = {r.subtask_id: r for r in observed.result.subtask_results}
    assert "PLAN_SCOPE_REVISION_REQUIRED" in by_id["s2"].reason_codes
    answers = conflict_attempt(observed)["answers"]
    record("plan-scope conflict", answers, observed.explained)
    [diagnosis] = [d for d in answers["Q5"]["diagnoses"] if d["type"] == "misdirected_edit"]
    assert "app/lib.py" in diagnosis["likely_files"]                                # the grounded target
    assert answers["Q2"]["items"][-1]["authorized_write_scope"] == ["app/config.py"]  # the authorized scope
    [decision] = answers["Q6"]["items"]
    assert decision["retry"] is False
    assert decision["plan_scope_conflict"]["reason_code"] == "PLAN_SCOPE_REVISION_REQUIRED"   # the conflict
    assert decision["plan_scope_conflict"]["required_files"] == ["app/lib.py"]
    cause = observed.explained["Q9"]["terminal_cause"]                     # the controller's decision (H)
    assert cause["source"] == "controller_terminal_event" and cause["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"]
    assert cause["deciding_subtask"]["subtask_id"] == "s2"
    assert observed.explained["Q9"]["plan_scope_conflicts"][0]["required_files"] == ["app/lib.py"]


# 6. Planner repair ------------------------------------------------------------------------------

def test_t6_planner_repair(tmp_path, monkeypatch):
    import test_enforce_verified_no_change as shape

    def invalid():
        from kriya.workflow.plan_schema import EngineeringPlan

        plan = shape._plan(shape.TOOL_CRITERION).model_dump()
        plan["subtasks"][1]["depends_on"] = ["s9"]
        return EngineeringPlan.model_validate(plan)
    observed = shop_enforce(tmp_path, monkeypatch, plans=[invalid, lambda: shape._plan(shape.TOOL_CRITERION)])
    planning = [r for r in observed.of("model.request") if r["phase"] == "planning"]
    # Two Planner calls, each its own call, outside any unit or attempt.
    assert len(planning) == 2 and len({r["call_seq"] for r in planning}) == 2
    assert all(r["unit_id"] is None and r["attempt_number"] is None for r in planning)
    assert observed.explained["calls_outside_attempts"]["planning"] == 2
    for attempt in observed.attempts():
        assert_row(attempt["answers"])
    record("Planner repair", observed.attempt(1, unit="s1"), observed.explained)
    assert observed.explained["Q9"]["terminal_cause"]["quality_gates_passed"] is True


# 7. enforce TOOL subtask ------------------------------------------------------------------------

def test_t6_enforce_tool_subtask(tmp_path, monkeypatch):
    from _t6_harness import echo_tool, tool_plan

    observed = shop_enforce(tmp_path, monkeypatch, plans=[tool_plan], tools={"t6_echo": echo_tool()})
    row = record("TOOL subtask", observed.attempt(1, unit="s0"), observed.explained)
    row.pop("Q9")
    assert row == {"Q1": "NOT_APPLICABLE(no_model_call: a tool subtask makes no model call)",
                   "Q2": "NOT_APPLICABLE(no_model_call: a tool subtask makes no model call)",
                   "Q3": "NOT_APPLICABLE(no_model_call: a tool subtask makes no model call)",
                   "Q4": "NOT_APPLICABLE(tool_action: no Developer candidate)",
                   "Q5": "NOT_APPLICABLE(no_verification_gate)",
                   "Q6": "NOT_APPLICABLE(tool_subtask: executed once, never retried)",
                   "Q7": "NOT_APPLICABLE(no later attempt in this unit invocation)",
                   "Q8": "NOT_APPLICABLE(no_model_call: a tool subtask makes no model call)"}
    entry = next(a for a in observed.attempts() if a["unit_id"] == "s0")
    assert [e["status"] for e in entry["tool_execution"]] == ["completed"]


# 8. final-review refusal ------------------------------------------------------------------------

def test_t6_final_review_refusal(tmp_path, monkeypatch):
    from test_prompt_budget_fit_001c import RefusingReviewer

    from kriya.core.llm import LLMClient

    refusing = RefusingReviewer()
    monkeypatch.setattr(LLMClient, "_dispatch_budget", lambda client, **kw: refusing(client, **kw))
    observed = direct_run(tmp_path, monkeypatch, _always(CALC_WITH_SUB), FILES)
    record("final-review refusal", observed.attempt(1), observed.explained)
    review = [r for r in observed.of("model.request") if r["phase"] == "review"]
    assert [r["payload"]["dispatched"] for r in review] == [False]                # refused before dispatch
    assert all(r["attempt_number"] is None for r in review)                      # never a Developer attempt
    assert observed.explained["Q9"]["terminal_cause"]["failure_category"] == "final_review_refused"


# 9. exception escape ----------------------------------------------------------------------------

def test_t6_exception_escape(tmp_path, monkeypatch):
    from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_engine, git_workspace, run_direct

    import kriya.agents.agent as agent
    from kriya.core.attempt_evidence import reader
    from kriya.core.attempt_evidence.explain import explain_run
    from kriya.core.state_paths import ENV_STATE_DIR

    async def crash(self, *args, **kwargs):
        raise RuntimeError("injected reviewer crash")
    monkeypatch.setattr(agent.ReviewerAgent, "run", crash)
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))
    with RuntimeRegistration(ChaosRuntime(_always(CALC_WITH_SUB))), pytest.raises(RuntimeError):
        run_direct(chaos_engine(chaos_config()), "add sub to calc.py", git_workspace(tmp_path, FILES))
    [run_id] = reader.list_runs(str(tmp_path / "state"))
    explained = explain_run(str(tmp_path / "state"), run_id)
    assert explained["verification"] == reader.VERIFIED and explained["sealed"] is True
    record("exception escape", explained["attempts"][0]["answers"], explained)
    cause = explained["Q9"]["terminal_cause"]
    assert (cause["unit_outcome"], cause["error_type"]) == ("EXCEPTION", "RuntimeError")


# 10. deadline stop ------------------------------------------------------------------------------

def test_t6_deadline_stop(tmp_path, monkeypatch):
    import asyncio

    from _chaos_harness import ChaosRuntime, role_of

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
    answers = observed.attempt(1)
    record("deadline stop", answers, observed.explained)
    [request] = [i for i in answers["Q1"]["items"] if i["role"] == "developer"]
    assert answers["Q4"] == {"status": "NOT_APPLICABLE", "reason": "no_model_answer: InferenceDeadlineError"}
    [response] = answers["Q3"]["items"]
    assert request["dispatched"] is True and response["error_type"] == "InferenceDeadlineError"
    assert response["call_seq"] == request["call_seq"]                          # bound to the same call
    assert [d["type"] for d in answers["Q5"]["diagnoses"]] == ["time_budget_exhausted"]
    [decision] = answers["Q6"]["items"]
    assert decision["retry"] is False
    assert observed.explained["Q9"]["terminal_cause"]["quality_gates_passed"] is False
