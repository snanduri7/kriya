"""ENFORCE-IDENTICAL-WRITE-COMPLETION-001 (BACKEND-FINAL-CLOSURE-005 cohort 2, C2-S4_B; second repair cycle).

Measured live (sealed store, candidate.change seq 62/63): an implementation unit's Developer returned both planned
files byte-for-byte unchanged; the unit passed its gates, completed as a successful mutation and was "applied", so
the goal's obligation to change those files was silently discharged and the later unit's failure was grounded to
the "completed" owner. Rule: a write whose bytes equal the captured baseline bytes is not a change. It never enters
the written set, its candidate.change record says so (unchanged=true), and the planned file settles through the
verified no-change contract (deterministic evidence or a typed refusal naming the identical rewrite) - exactly how a
Developer NO CHANGE answer is already decided (ENFORCE-VERIFIED-NO-CHANGE-001 / OD-3). Byte authority: an existing
file's own line-ending convention is applied before the comparison (kriya/workflow/file_integrity.py encode), so a
CRLF rendering of unchanged text for an LF file is unchanged; a text change is a change.

Every run is end to end through the real run_generation_workflow (the partial-no-change harness shape); the model is
scripted at the transport; only the toolchain gates are stubbed. The S4_B shape (T1) fails before the fix: the unit
is applied with both files.
"""
import asyncio
import json
import os
import re
from unittest.mock import AsyncMock, patch

from _protocol_responses import sentinel, wants_structured
from test_backend_final_closure_005_partial_no_change import (
    A_FIXED,
    A_SRC,
    B_SRC,
    GOAL,
    JUDGMENT_CRITERION,
    TESTS_PASS,
    TOOL_CRITERION,
    A,
    B,
    _git,
    _plan,
)

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.attempt_evidence import reader
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

_PATH_IN_PROMPT = re.compile(r'path="([^"]+)"')
B_DOCUMENTED = B_SRC.replace("def populate_pet_types():\n", 'def populate_pet_types():\n    """Cached pet types."""\n')
TESTS_FAIL = {"success": False, "output": "tests/test_shop.py F\nFAILED tests/test_shop.py::test_pet_types - assert\n"
                                          "=================== 1 failed in 0.10s ==================="}


def _run_unit(tmp_path, answers, *, criterion=TOOL_CRITERION, test_results=None):
    """``answers(attempt, path) -> str`` renders one structured block per planned path the prompt names;
    ``test_results`` is the sequence of run_tests results (the last one repeats)."""
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    (workspace / A).write_text(A_SRC)
    (workspace / B).write_text(B_SRC)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    developer = []
    prompts = []
    suite = list(test_results or [TESTS_PASS])

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [A, B]})
        elif "Developer Agent" in first:
            assert wants_structured(system_prompt)
            developer.append(system_prompt)
            prompts.append((system_prompt or "") + "\n" + (user_prompt or ""))
            content = "".join(answers(len(developer), path) for path in dict.fromkeys(_PATH_IN_PROMPT.findall(system_prompt)))
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    def run_tests(*_a, **_k):
        return suite.pop(0) if len(suite) > 1 else suite[0]

    events = []
    real_record = GenerationState.record_event

    def record(state, event):
        events.append(event)
        return real_record(state, event)

    gate_outcomes = []
    real_gate = GenerationState.record_gate_outcome

    def gate(state, outcome):
        gate_outcomes.append(outcome)
        return real_gate(state, outcome)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch.object(GenerationState, "record_gate_outcome", new=gate), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests):
        result = asyncio.run(engine.run_generation_workflow(
            goal=GOAL, workspace_path=str(workspace), predetermined_plan="cache the pet types",
            predetermined_design="", predetermined_architect_files=[A, B], allowed_write_relpaths=[A, B],
            structured_plan=_plan(criterion), current_subtask_id="s1",
            required_verification=[{"type": "tool", "tool_name": "test", "description": "run the tests"}],
            approval_callback=AsyncMock(return_value=True)))
    result["_test_gate_outcomes"] = gate_outcomes
    result["_test_prompts"] = prompts
    return workspace, developer, events, result


def _bytes(workspace):
    return {p: (workspace / p).read_bytes() for p in (A, B)}


def _candidate_changes():
    """The run's candidate.change payloads from the isolated attempt-evidence store, by path."""
    state_dir = os.environ[ENV_STATE_DIR]
    runs = reader.list_runs(state_dir)
    assert len(runs) == 1, runs
    run = reader.open_run(state_dir, runs[0])
    return {(r.get("payload") or {}).get("path"): r["payload"] for r in run.records() if r["kind"] == "candidate.change"}


def _identical(_attempt, path):
    return sentinel(path, analysis="returned as is.", content=A_SRC if path == A else B_SRC)


def test_t1_the_measured_shape_every_planned_file_returned_identical_is_a_verified_no_change_never_an_applied_mutation(tmp_path):
    """C2-S4_B s1: both files byte-identical; with deterministic coverage the unit completes as VERIFIED_NO_CHANGE
    (nothing applied), its evidence says so, and the goal's obligation is not discharged by the writes."""
    workspace, developer, events, result = _run_unit(tmp_path, _identical)

    assert developer, "the Developer was asked"
    assert result["quality_gates_passed"] is True, (result.get("failure_category"), result.get("environment_failure"))
    assert result["files"] == [], result["files"]
    assert result["completion_kind"] == "VERIFIED_NO_CHANGE"
    assert _bytes(workspace) == {A: A_SRC.encode(), B: B_SRC.encode()}
    [proposed] = [e for e in events if e.kind == "unit.no_change_proposed"]
    assert proposed.details["paths"] == sorted([A, B]) and proposed.details.get("identical_rewrites") == sorted([A, B])
    [verified] = [e for e in events if e.kind == "unit.verified_no_change"]
    assert verified.details["paths"] == sorted([A, B]) and verified.details["partial"] is False
    changes = _candidate_changes()
    assert {A, B} <= set(changes), changes.keys()
    for path in (A, B):
        assert changes[path]["unchanged"] is True and changes[path]["before_digest"] == changes[path]["after_digest"]
        assert changes[path]["lines_added"] == 0 and changes[path]["lines_removed"] == 0


def test_t2_an_identical_rewrite_without_deterministic_coverage_is_refused_and_names_the_rewrite(tmp_path):
    """A judgment criterion covers nothing: the unit does not complete, nothing is applied, and the refusal tells the
    Developer the files were returned byte-identical - not that they were 'never written'."""
    workspace, _developer, events, result = _run_unit(tmp_path, _identical, criterion=JUDGMENT_CRITERION)

    assert result["quality_gates_passed"] is False
    assert _bytes(workspace) == {A: A_SRC.encode(), B: B_SRC.encode()}
    refusals = [e for e in events if e.kind == "unit.verified_no_change_refused"]
    assert refusals and refusals[0].details["paths"] == sorted([A, B])
    assert result["failure_category"] == "quality_gates_exhausted"
    last = result["last_failure"]
    message = last.get("message") if isinstance(last, dict) else getattr(last, "message", str(last))
    assert "VERIFIED_NO_CHANGE_REFUSED" in message and "byte-identical to the baseline" in message, message[:300]
    assert "never written" not in message
    assert not any(e.kind == "unit.verified_no_change" for e in events)


def test_t3_a_changed_file_beside_an_identical_rewrite_is_a_partial_no_change(tmp_path):
    def answers(_attempt, path):
        return sentinel(path, analysis="x", content=A_FIXED if path == A else B_SRC)

    workspace, _developer, events, result = _run_unit(tmp_path, answers)

    assert result["quality_gates_passed"] is True, (result.get("failure_category"), result.get("environment_failure"))
    assert result["files"] == [A]
    assert result["completion_kind"] is None
    assert _bytes(workspace) == {A: A_FIXED.encode(), B: B_SRC.encode()}
    [verified] = [e for e in events if e.kind == "unit.verified_no_change"]
    assert verified.details["paths"] == [B] and verified.details["partial"] is True and verified.details["written_paths"] == [A]


def test_t4_line_endings_follow_the_files_own_convention_and_a_text_change_is_a_change(tmp_path):
    """Byte authority through the existing encoder: a CRLF rendering of unchanged text for an LF file is written in
    the file's convention - identical bytes, no change; a one-line text change is a mutation."""
    def crlf_answers(_attempt, path):
        return sentinel(path, analysis="x", content=A_FIXED if path == A else B_SRC.replace("\n", "\r\n"))

    workspace, _developer, _events, result = _run_unit(tmp_path, crlf_answers)
    assert result["quality_gates_passed"] is True and result["files"] == [A]
    assert _bytes(workspace)[B] == B_SRC.encode()

    def documented_answers(_attempt, path):
        return sentinel(path, analysis="x", content=A_FIXED if path == A else B_DOCUMENTED)

    workspace, _developer, events, result = _run_unit(tmp_path / "second", documented_answers)
    assert result["quality_gates_passed"] is True and sorted(result["files"]) == sorted([A, B])
    assert _bytes(workspace)[B] == B_DOCUMENTED.encode()
    assert not any(e.kind in ("unit.no_change_proposed", "unit.verified_no_change") for e in events)


def test_t5_a_retry_that_restores_a_file_to_its_baseline_bytes_withdraws_it_from_the_mutation(tmp_path):
    """Attempt 1 changes both files and the test gate fails; attempt 2 keeps A's fix and returns B exactly as the
    baseline: B is restored on disk and leaves the written set - the unit settles B as a verified no-change."""
    def answers(attempt, path):
        if path == A:
            return sentinel(path, analysis="x", content=A_FIXED)
        return sentinel(path, analysis="x", content=B_DOCUMENTED if attempt == 1 else B_SRC)

    workspace, developer, events, result = _run_unit(tmp_path, answers, test_results=[TESTS_FAIL, TESTS_PASS])

    assert len(developer) >= 2, len(developer)
    assert result["quality_gates_passed"] is True, (result.get("failure_category"), result.get("environment_failure"))
    assert result["files"] == [A]
    assert _bytes(workspace) == {A: A_FIXED.encode(), B: B_SRC.encode()}
    [verified] = [e for e in events if e.kind == "unit.verified_no_change"]
    assert verified.details["paths"] == [B] and verified.details["written_paths"] == [A]


def test_t6_a_direct_goal_whose_every_file_came_back_identical_is_a_typed_stop_never_passed(tmp_path):
    """Direct path (no structured plan, no milestone driver): both expected files byte-identical. Nothing downstream
    judges "already satisfied", so the run never reports PASSED on the Developer's identical bytes alone (review F1 of
    the second repair cycle): a typed NO_CHANGE_UNVERIFIED stop through the repair path, nothing applied, tree
    unchanged. A MILESTONE's zero-change result is decided by the milestone driver (a deterministic no-change proof
    completes it, a refusal fails it typed - MILESTONE-ZERO-COMMIT-COMPLETION-001, tests/test_prd008_s4c_*); the
    integration pass is decided by the plan-level original-requirement verification (PRD-020)."""
    engine, workspace = _legacy_engine(tmp_path, [{"filepath": A, "content": A_SRC}, {"filepath": B, "content": B_SRC}])
    gate_outcomes = []
    real_gate = GenerationState.record_gate_outcome

    def gate(state, outcome):
        gate_outcomes.append(outcome)
        return real_gate(state, outcome)

    with patch.object(GenerationState, "record_gate_outcome", new=gate), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: TESTS_PASS):
        result = asyncio.run(engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace),
                                                            approval_callback=AsyncMock(return_value=True)))

    assert result["quality_gates_passed"] is False
    assert result["files"] == []
    assert _bytes(workspace) == {A: A_SRC.encode(), B: B_SRC.encode()}
    stops = [o for o in gate_outcomes if (o.get("failure_type") or o.get("type")) == "unverified_no_change"]
    assert stops and "NO_CHANGE_UNVERIFIED" in str(stops[0].get("output", "")) and "byte-identical" in str(stops[0].get("output", ""))
    assert not any((o.get("failure_type") or o.get("type")) == "incomplete_generation" for o in gate_outcomes)


def test_t6b_a_restored_unplanned_path_is_never_a_no_change_proposal():
    """Review F3: the identical set may hold a restoration of a file the unit never planned (a protected caller
    restored to its baseline bytes); only the unit's planned paths join the no-change proposal."""
    from types import SimpleNamespace

    from kriya.workflow.attempt import _verified_no_change_proposal

    plan = _plan(TOOL_CRITERION)
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        for rel in (A, B, "lib/helper.py"):
            os.makedirs(os.path.join(root, os.path.dirname(rel)), exist_ok=True)
            open(os.path.join(root, rel), "w").write("x\n")
        ctx = SimpleNamespace(structured_plan=plan, current_subtask_id="s1", worktree_path=root, reopened_owner=False)
        state = SimpleNamespace(all_files_written={A}, identical_rewrites={B, "lib/helper.py"})
        assert _verified_no_change_proposal(state, ctx, [], ["controller.py"]) == [B]
        state = SimpleNamespace(all_files_written={A}, identical_rewrites={"lib/helper.py"})
        assert _verified_no_change_proposal(state, ctx, [], ["controller.py"]) == []


def _legacy_engine(tmp_path, developer_files):
    """The direct (legacy) path: no structured plan; the Developer's result list may omit an expected file."""
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    (workspace / A).write_text(A_SRC)
    (workspace / B).write_text(B_SRC)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.developer.run_generation = AsyncMock(return_value=developer_files)

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        del user_prompt, args, kwargs
        system = system_prompt or ""
        if "Kriya Planner Agent" in system:
            return "Step 1: review the shop files"
        if "Kriya Architect Agent" in system:
            return "Design: shop/service.py and shop/controller.py stay consistent"
        if "File List Planner" in system:
            return json.dumps({"files": [A, B]})
        return "Review: Approved"

    engine.llm.complete = complete
    return engine, workspace


def test_t7_an_identical_rewrite_beside_a_genuinely_missing_file_names_only_the_missing_file(tmp_path):
    """Direct path: the Developer returns A byte-identical and omits B on every attempt. The completeness failure
    names B as never written and A as delivered unchanged - never A as 'never written'. (In the structured
    protocol a planned file cannot be silently omitted - that is a protocol error - so the shape is the legacy one.)"""
    engine, workspace = _legacy_engine(tmp_path, [{"filepath": A, "content": A_SRC}])
    gate_outcomes = []
    real_gate = GenerationState.record_gate_outcome

    def gate(state, outcome):
        gate_outcomes.append(outcome)
        return real_gate(state, outcome)

    with patch.object(GenerationState, "record_gate_outcome", new=gate), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: TESTS_PASS):
        result = asyncio.run(engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace),
                                                            approval_callback=AsyncMock(return_value=True)))

    assert result["quality_gates_passed"] is False
    incomplete = [o for o in gate_outcomes if (o.get("failure_type") or o.get("type")) == "incomplete_generation"]
    assert incomplete, [(o.get("type"), o.get("failure_type")) for o in gate_outcomes][:10]
    message = str(incomplete[0].get("output", ""))
    missing_clause = message.split("never written:")[1].split(". ")[0]
    assert "controller.py" in missing_clause and "service.py" not in missing_clause, message
    assert "shop/service.py: returned byte-identical to the baseline - delivered, not a change" in message, message
    assert _bytes(workspace) == {A: A_SRC.encode(), B: B_SRC.encode()}


def test_t8_a_gate_declared_no_progress_stop_is_applied_by_the_retry_strategy_with_its_guards():
    """Second review F5: the retry strategy owns the no-progress terminal. A failure declaring a typed stop under
    NO_PROGRESS_STOP_KEY terminates the loop with that reason and a terminal event; an environment stop, a
    plan-scope conflict or an already reached terminal take precedence; a failure without the key changes nothing.
    (The ordering after the workspace-progress classification is covered by the reopened-owner reproducers:
    an inverted order retries and breaks their two-Developer-call assertions.)"""
    from kriya.workflow.failure import Failure
    from kriya.workflow.retry_strategy import NO_PROGRESS_STOP_KEY, _apply_declared_no_progress_stop

    def failure(**diag):
        return Failure(type="verified_no_change_refused", message="m", raw_output="m", diagnostics=diag or None)

    state = GenerationState()
    _apply_declared_no_progress_stop(state, failure())
    assert state.no_progress_terminated is False and state.no_progress_reason is None

    state = GenerationState()
    _apply_declared_no_progress_stop(state, failure(**{NO_PROGRESS_STOP_KEY: "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"}))
    assert state.no_progress_terminated is True and state.no_progress_reason == "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"
    [terminal] = [e for e in state.run_events if e.kind == "retry.no_progress_terminal"]
    assert terminal.details["reason_code"] == "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"
    assert terminal.details["declared_by"] == "verified_no_change_refused"

    for guard in ({"environment_failure": "containment unavailable"}, {"plan_scope_conflict": {"reason": "x"}},
                  {"no_progress_terminated": True, "no_progress_reason": "NO_PROGRESS_TERMINAL"}):
        state = GenerationState()
        for key, value in guard.items():
            setattr(state, key, value)
        _apply_declared_no_progress_stop(state, failure(**{NO_PROGRESS_STOP_KEY: "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"}))
        assert state.no_progress_reason == guard.get("no_progress_reason"), guard
        assert not [e for e in state.run_events if e.kind == "retry.no_progress_terminal"], guard
