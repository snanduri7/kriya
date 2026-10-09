"""ENFORCE-VERIFIED-NO-CHANGE-001: a planned unit may finish without a
mutation only when Kriya deterministically verifies it is already satisfied.

Live (Planner R1 replay, spring-framework-petclinic pet types cache, run
20261002T185607-1c2bf01d): s1 cached findPetTypes(); s2 was planned against
PetController, whose code already called the now-cached service method. The
Developer correctly answered NO CHANGE for s2; enforce counted it as
INCOMPLETE GENERATION ("never written"), the missing-file retry's NO CHANGE
became an attribution dispute, and the unit stopped.

A NO CHANGE answer is never success by itself: it asks Kriya to verify the
current candidate. The unit completes as VERIFIED_NO_CHANGE only when every
acceptance criterion of the unit is covered by deterministic gate evidence
of the final attempt (the milestone rule, kriya/workflow/milestone_
completion.py), after the normal gates and the terminal regression ran.

End to end through the real run_generation_workflow for a structured
subtask (s2 of a two-unit plan whose s1 change is already in the
workspace); the model is scripted at the transport, only the toolchain
gates are stubbed.
"""
import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

from _protocol_responses import sentinel, wants_structured

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

SERVICE = "shop/service.py"
CONTROLLER = "shop/controller.py"
SERVICE_SRC = "import functools\n\n\n@functools.lru_cache(maxsize=None)\ndef find_pet_types():\n    return ('cat', 'dog')\n"
CONTROLLER_SRC = "from shop.service import find_pet_types\n\n\ndef populate_pet_types():\n    return find_pet_types()\n"
GOAL = "Cache the pet types the same way the vets are cached."
# The real gate's pytest summary shape (measured in the live matrix logs).
TESTS_PASS = {"success": True, "output": "tests/test_shop.py .....\n=================== 5 passed in 0.10s ==================="}


def _plan(criterion):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [criterion] if criterion else [],
        "subtasks": [
            {"id": "s1", "description": "cache find_pet_types", "execution_method": "model",
             "planned_files": [{"path": SERVICE, "action": "modify"}], "provides": ["cached"],
             "relevant_global_invariant_ids": ["gi1"],
             "acceptance_criteria_ids": [criterion["id"]] if criterion else [],
             "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]},
            {"id": "s2", "description": "use the cached pet types in the controller", "execution_method": "model",
             "planned_files": [{"path": CONTROLLER, "action": "modify"}], "requires": ["cached"],
             "depends_on": ["s1"], "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [criterion["id"]] if criterion else [],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ]})


TOOL_CRITERION = {"id": "ac1", "description": "pet types are cached", "method": "tool", "tool_name": "test"}
JUDGMENT_CRITERION = {"id": "ac1", "description": "pet types are cached", "method": "judgment"}


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def _run(tmp_path, *, criterion=TOOL_CRITERION, test_results=None, developer_answer=None):
    """s2 of the plan; the Developer answers NO CHANGE for the controller."""
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
    (workspace / SERVICE).write_text(SERVICE_SRC)  # s1's change, already applied
    (workspace / CONTROLLER).write_text(CONTROLLER_SRC)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base + s1")
    developer = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % CONTROLLER
        elif "Developer Agent" in first:
            developer.append(user_prompt)
            answer = developer_answer or (lambda sp: sentinel(
                CONTROLLER, analysis="populate_pet_types already calls the cached service.", no_change=True)
                if wants_structured(sp) else "NO CHANGE NEEDED: already calls the cached service.")
            content = answer(system_prompt)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    results = list(test_results or [TESTS_PASS] * 40)
    test_calls = []

    def tests(*args, **kwargs):
        del args, kwargs
        test_calls.append(1)
        return results[min(len(test_calls), len(results)) - 1]

    events = []
    real_record = GenerationState.record_event

    def record(state, event):
        events.append(event)
        return real_record(state, event)

    plan = _plan(criterion)
    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=tests):
        result = asyncio.run(engine.run_generation_workflow(
            goal=GOAL, workspace_path=str(workspace), predetermined_plan="use the cached pet types",
            predetermined_design="", predetermined_architect_files=[CONTROLLER],
            allowed_write_relpaths=[CONTROLLER], structured_plan=plan, current_subtask_id="s2",
            required_verification=[{"type": "tool", "tool_name": "test", "description": "run the tests"}],
            approval_callback=AsyncMock(return_value=True)))
    return workspace, developer, events, result


def _bytes(workspace):
    return {p: (workspace / p).read_bytes() for p in (SERVICE, CONTROLLER)}


def test_a_deterministically_verified_no_change_unit_completes(tmp_path):
    workspace, developer, events, result = _run(tmp_path)
    assert developer, "the Developer was asked"
    assert result["quality_gates_passed"] is True, result.get("failure_category")
    assert result["files"] == []
    assert result["completion_kind"] == "VERIFIED_NO_CHANGE"
    assert _bytes(workspace) == {SERVICE: SERVICE_SRC.encode(), CONTROLLER: CONTROLLER_SRC.encode()}
    [verified] = [e.details for e in events if e.kind == "unit.verified_no_change"]
    assert verified["acceptance_coverage"][0]["criterion_id"] == "ac1"
    # The normal gates ran: the terminal regression is part of the evidence.
    assert any(e.kind == "terminal_regression.passed" for e in events)


def _refused(events):
    return [e.details for e in events if e.kind == "unit.verified_no_change_refused"]


def _not_completed(workspace, result):
    assert result["quality_gates_passed"] is False
    assert result["completion_kind"] is None
    assert _bytes(workspace) == {SERVICE: SERVICE_SRC.encode(), CONTROLLER: CONTROLLER_SRC.encode()}


def test_a_judgment_criterion_is_never_deterministic_evidence(tmp_path):
    """The live s2 shape: its only criterion was method=judgment."""
    workspace, _, events, result = _run(tmp_path, criterion=JUDGMENT_CRITERION)
    _not_completed(workspace, result)
    refusal = _refused(events)[0]["refusal"]
    assert refusal["code"] == "ACCEPTANCE_COVERAGE_INCOMPLETE" and refusal["criteria"] == {"ac1": "UNCOVERED"}


def test_a_unit_without_acceptance_criteria_cannot_prove_no_change(tmp_path):
    workspace, _, events, result = _run(tmp_path, criterion=None)
    _not_completed(workspace, result)
    assert _refused(events)[0]["refusal"]["code"] == "ACCEPTANCE_COVERAGE_UNAVAILABLE"


def test_tests_that_ran_nothing_are_not_evidence(tmp_path):
    vacuous = {"success": True, "output": "================== no tests ran in 0.01s =================="}
    workspace, _, events, result = _run(tmp_path, test_results=[vacuous] * 40)
    _not_completed(workspace, result)
    assert _refused(events)[0]["refusal"]["criteria"] == {"ac1": "NO_TESTS_EXECUTED"}


def test_an_unparseable_test_count_is_unknown_and_refuses(tmp_path):
    """UNKNOWN evidence: the runner's output gives no executed-test count."""
    unknown = {"success": True, "output": "custom runner: OK"}
    workspace, _, events, result = _run(tmp_path, test_results=[unknown] * 40)
    _not_completed(workspace, result)
    assert _refused(events)[0]["refusal"]["criteria"] == {"ac1": "NO_TESTS_EXECUTED"}


def test_a_failing_named_test_refuses_no_change(tmp_path):
    """The dependency's work is not actually satisfying the unit: tests fail
    on the current candidate, so the unit cannot complete as no-change."""
    failing = {"success": False, "output": "FAILED tests/test_shop.py::test_cached - AssertionError\n"
                                           "=================== 1 failed, 4 passed in 0.10s ==================="}
    workspace, _, events, result = _run(tmp_path, test_results=[failing] * 40)
    _not_completed(workspace, result)
    assert not any(e.kind == "unit.verified_no_change" for e in events)


def test_no_change_never_bypasses_the_terminal_regression(tmp_path):
    """The candidate gate's tests pass, the terminal full regression fails:
    no VERIFIED_NO_CHANGE - the decision is taken only after it passed."""
    failing = {"success": False, "output": "FAILED tests/test_other.py::test_x - AssertionError\n"
                                           "=================== 1 failed, 9 passed in 0.10s ==================="}
    workspace, _, events, result = _run(tmp_path, test_results=[TESTS_PASS, failing] * 20)
    _not_completed(workspace, result)
    assert not any(e.kind == "unit.verified_no_change" for e in events)


def test_the_verified_decision_follows_the_terminal_regression_and_binds_the_candidate(tmp_path):
    _, _, events, _ = _run(tmp_path)
    kinds = [e.kind for e in events]
    assert kinds.index("unit.no_change_proposed") < kinds.index("unit.verified_no_change")
    [verified] = [e.details for e in events if e.kind == "unit.verified_no_change"]
    assert verified["paths"] == [CONTROLLER] and verified["workspace_content_hash"]
    assert verified["acceptance_coverage"][0]["tests_executed"] == 5


def test_a_change_after_a_no_change_is_an_ordinary_mutation(tmp_path):
    """The proposal is per attempt: once the Developer writes, nothing is a
    no-change, and the normal path applies the edit."""
    answers = []

    def answer(system_prompt):
        answers.append(1)
        changed = CONTROLLER_SRC.replace("def populate_pet_types():\n",
                                         'def populate_pet_types():\n    """Cached pet types."""\n')
        assert wants_structured(system_prompt)
        return sentinel(CONTROLLER, analysis="document the cached call.", content=changed)

    workspace, _, events, result = _run(tmp_path, developer_answer=answer)
    assert result["quality_gates_passed"] is True and result["completion_kind"] is None
    assert not any(e.kind == "unit.no_change_proposed" for e in events)
    assert b"Cached pet types" in (workspace / CONTROLLER).read_bytes()


# -- the proposal and the predicate, edge by edge -----------------------------

from types import SimpleNamespace  # noqa: E402 - grouped with the unit tests below

from kriya.workflow.attempt import _verified_no_change_proposal  # noqa: E402
from kriya.workflow.verified_no_change import unit_coverage_items, verify_no_change_unit  # noqa: E402


def _ctx(tmp_path, *, structured=True):
    (tmp_path / "shop").mkdir(exist_ok=True)
    (tmp_path / CONTROLLER).write_text(CONTROLLER_SRC)
    return SimpleNamespace(structured_plan=_plan(TOOL_CRITERION) if structured else None,
                           current_subtask_id="s2" if structured else None, worktree_path=str(tmp_path),
                           reopened_owner=False)


NO_CHANGE = {"filepath": CONTROLLER, "content": None, "no_change": True}


def test_the_proposal_needs_an_enforce_unit_an_existing_file_and_no_earlier_write(tmp_path):
    state = SimpleNamespace(all_files_written=set(), identical_rewrites=set())
    ctx = _ctx(tmp_path)
    from kriya.workflow.operations import all_results_are_no_change
    assert all_results_are_no_change([NO_CHANGE]), "fixture must be the real NO CHANGE result shape"
    assert _verified_no_change_proposal(state, ctx, [NO_CHANGE], ["controller.py"]) == [CONTROLLER]
    # A direct (unstructured) run: unchanged behaviour.
    assert _verified_no_change_proposal(state, _ctx(tmp_path, structured=False), [NO_CHANGE], ["controller.py"]) == []
    # A planned file that does not exist is a missing artifact, never a no-change.
    missing = {**NO_CHANGE, "filepath": "shop/new_view.py"}
    assert _verified_no_change_proposal(state, ctx, [missing], ["new_view.py"]) == []
    # The unit already wrote in an earlier attempt.
    assert _verified_no_change_proposal(SimpleNamespace(all_files_written={CONTROLLER}, identical_rewrites=set()), ctx, [NO_CHANGE],
                                        ["controller.py"]) == []
    # Another expected file is still missing: incomplete, not a no-change.
    assert _verified_no_change_proposal(state, ctx, [NO_CHANGE], ["controller.py", "service.py"]) == []
    # Nothing missing: no proposal.
    assert _verified_no_change_proposal(state, ctx, [NO_CHANGE], []) == []


def _outcome(gate, success, output, attempt=2):
    return {"type": gate, "success": success, "output": output, "attempt": attempt}


def _verify(plan, outcomes, attempt=2):
    from kriya.workflow.workflow import deterministic_gate_evidence
    result = {"deterministic_gate_evidence": deterministic_gate_evidence(outcomes, attempt),
              "acceptance_coverage": unit_coverage_items(plan, "s2", outcomes, attempt)}
    return verify_no_change_unit(plan, "s2", result)


PYTEST_5 = "=================== 5 passed in 0.10s ==================="


def test_the_predicate_accepts_only_final_attempt_tool_evidence():
    plan = _plan(TOOL_CRITERION)
    binding, refusal = _verify(plan, [_outcome("compile", True, "ok"), _outcome("regression_test", True, PYTEST_5)])
    assert refusal is None and binding["criteria"] == {"ac1": "PASS_WITH_TESTS"}
    # Evidence from an earlier attempt judged another candidate.
    _, refusal = _verify(plan, [_outcome("regression_test", True, PYTEST_5, attempt=1),
                                _outcome("compile", True, "ok")])
    assert refusal["criteria"] == {"ac1": "UNCOVERED"}
    # A failed gate.
    _, refusal = _verify(plan, [_outcome("regression_test", False, "=== 1 failed, 4 passed in 0.1s ===")])
    assert refusal["criteria"] == {"ac1": "FAILED"}


def test_compile_never_stands_in_for_a_test_criterion():
    plan = _plan(TOOL_CRITERION)
    _, refusal = _verify(plan, [_outcome("compile", True, "ok")])
    assert refusal["criteria"] == {"ac1": "UNCOVERED"}


def test_a_compile_criterion_is_covered_only_by_a_compile_gate_that_passed():
    plan = _plan({"id": "ac1", "description": "it builds", "method": "tool", "tool_name": "compile"})
    binding, refusal = _verify(plan, [_outcome("compile", True, "ok")])
    assert refusal is None and binding["criteria"] == {"ac1": "PASSED"}
    _, refusal = _verify(plan, [_outcome("compile", False, "error")])
    assert refusal["criteria"] == {"ac1": "FAILED"}


def test_every_criterion_of_the_unit_counts_even_one_a_later_unit_also_claims():
    """The 'due now' projection defers a criterion a downstream unit also
    claims; for a no-change unit that would make the check vacuous."""
    plan = _plan(JUDGMENT_CRITERION)
    _, refusal = _verify(plan, [_outcome("regression_test", True, PYTEST_5)])
    assert refusal["code"] == "ACCEPTANCE_COVERAGE_INCOMPLETE"




UNCACHED_SERVICE = "def find_pet_types():\n    return ('cat', 'dog')\n"


def _enforce(tmp_path, criterion):
    """The live shape through the real WorkflowController enforce loop and
    the real workflow: s1 changes the service, s2 (planned against the
    controller) is answered NO CHANGE."""
    from kriya.workflow.workflow_controller import WorkflowController

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
    (workspace / SERVICE).write_text(UNCACHED_SERVICE)
    (workspace / CONTROLLER).write_text(CONTROLLER_SRC)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            path = SERVICE if SERVICE in (user_prompt or "") and CONTROLLER not in (user_prompt or "") else CONTROLLER
            content = '{"files": ["%s"]}' % path
        elif "Developer Agent" in first:
            if f'path="{SERVICE}"' in system_prompt or "cache find_pet_types" in (user_prompt or ""):
                content = sentinel(SERVICE, analysis="cache it.", content=SERVICE_SRC)
            else:
                content = sentinel(CONTROLLER, analysis="already calls the cached service.", no_change=True)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    events = []
    real_record = GenerationState.record_event

    def record(state, event):
        events.append(event)
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value="structured plan")
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: TESTS_PASS), \
         patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               return_value=_plan(criterion)):
        result = asyncio.run(WorkflowController(engine).execute(GOAL, str(workspace), migration_mode="enforce"))
    return workspace, events, result


def test_the_live_shape_through_the_enforce_controller(tmp_path):
    workspace, events, result = _enforce(tmp_path, TOOL_CRITERION)
    by_id = {r.subtask_id: r for r in result.subtask_results}
    assert by_id["s1"].status.value == "completed" and by_id["s2"].status.value == "completed", \
        {k: (v.status, v.error) for k, v in by_id.items()}
    assert "VERIFIED_NO_CHANGE" in by_id["s2"].reason_codes and "VERIFIED_NO_CHANGE" not in by_id["s1"].reason_codes
    # s1's change committed; s2 changed nothing.
    assert (workspace / SERVICE).read_text() == SERVICE_SRC
    assert (workspace / CONTROLLER).read_text() == CONTROLLER_SRC
    assert result.legacy_result["status"] == "success"
    assert any(e.kind == "unit.verified_no_change" for e in events)


def test_the_live_shape_with_a_judgment_criterion_fails_closed_through_the_controller(tmp_path):
    workspace, events, result = _enforce(tmp_path, JUDGMENT_CRITERION)
    by_id = {r.subtask_id: r for r in result.subtask_results}
    assert by_id["s2"].status.value != "completed"
    assert result.legacy_result["status"] != "success"
    assert (workspace / CONTROLLER).read_text() == CONTROLLER_SRC
    assert any(e.kind == "unit.verified_no_change_refused" for e in events)


def test_a_no_change_verified_then_not_committed_is_not_reported_as_completed(tmp_path):
    """Verified, then the terminal commit refuses: the result never carries
    VERIFIED_NO_CHANGE (completion follows the whole attempt, not one gate)."""
    from kriya.workflow.terminal_commit import TerminalCommitOutcome

    refused = TerminalCommitOutcome(committed=False, workspace_state="UNCHANGED", commit_result="NOT_COMMITTED", transaction_id="t",
                                    reason_code="WORKSPACE_REVISION_CONFLICT", error=RuntimeError("revision moved"))
    with patch("kriya.workflow.workflow.commit_terminal_candidate", return_value=refused):
        workspace, _, events, result = _run(tmp_path)
    assert any(e.kind == "unit.verified_no_change" for e in events)
    _not_completed(workspace, result)


def test_a_later_attempt_never_inherits_an_earlier_no_change_completion(tmp_path):
    """Attempt 1 is verified no-change but not committed; attempt 2 writes a
    real change: the result is an ordinary change, never VERIFIED_NO_CHANGE."""
    from kriya.workflow.attempt import run_attempt

    state = GenerationState()
    state.completion_kind = "VERIFIED_NO_CHANGE"
    state.no_change_proposal = [CONTROLLER]
    ctx = SimpleNamespace(write_scope_mode=None, required_verification=[], architect_files=["../outside"],
                          worktree_path=str(tmp_path), max_retries=4, targeted_max_retries=3, chain=[])
    try:
        asyncio.run(run_attempt(state, ctx))
    except Exception:  # noqa: BLE001 - the attempt stops at the control-path guard; only the reset matters
        pass
    assert state.completion_kind is None and state.no_change_proposal == []
