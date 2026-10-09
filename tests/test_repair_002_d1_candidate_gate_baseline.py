"""CANDIDATE-GATE-BASELINE-POLICY-001 (+ D1b) - BACKEND-FINAL-CLOSURE-005 cohort 2, C2-S2_A / C2-S6_A; second repair
cycle.

Measured live: one pre-existing failing test was PRE_EXISTING_FAILURE / not blocking for unit s1's terminal
full-regression check and blocked all five attempts of unit s2's candidate gate in the same run (S2_A, a correct
applied fix discarded: FALSE NEGATIVE); the same failure blocked a verification unit's declared test gate and, at the
contract baseline, left REGRESSION_PRESERVATION unsatisfied so NO_MUTATION_REQUIRED was unreachable (S6_A, D1b).
Producer: the candidate gates judged the raw suite result; only the terminal check consumed the PRD-024 attribution.

Fix: one attribution owner (kriya/workflow/suite_attribution.py) decides every full-suite verdict against the frozen
PRE baseline with the existing delta/envelope semantics - the terminal check, the candidate gate's full-suite runs
and the verification coordinator. A NEW/CHANGED failure blocks exactly as before; stable pre-existing failures do
not; no captured baseline keeps the conservative raw rule. At the contract baseline a complete per-case report of the
untouched base records its failures as pre-existing (a zero mutation introduces none); an aggregate or indeterminate
suite result stays FAIL/INDETERMINATE (VC3-R9 preserved). Repository-independent fixtures; the scripted suite outputs
are the PRD-024 test shapes.
"""
import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from _protocol_responses import sentinel

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.policy.filesystem import WriteScopeMode
from kriya.tools import test_execution
from kriya.workflow import contract_baseline as cb
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

SRC = "src/pricing.py"
SRC_BASE = "def price(q, u):\n    return q * u\n"
SRC_FIXED = "def price(q, u):\n    return q * u  # unchanged behaviour\n"
FAILING = ("FAILED tests/test_legacy.py::test_old - AssertionError: 1 != 2\n"
           "========== 1 failed, 3 passed in 0.10s ==========\n")
NEW_FAILING = ("FAILED tests/test_legacy.py::test_old - AssertionError: 1 != 2\n"
               "FAILED tests/test_pricing.py::test_price - AssertionError: 5 != 6\n"
               "========== 2 failed, 2 passed in 0.10s ==========\n")
GOAL = "Tidy the pricing comment."
_PATH_IN_PROMPT = re.compile(r'path="([^"]+)"')


def _git(ws, *args):
    import subprocess
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=ws, check=True, capture_output=True)


def _plan(role="implementation"):
    unit = {"id": "s1", "description": "tidy the comment", "execution_method": "model",
            "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
            "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]}
    if role == "implementation":
        unit["planned_files"] = [{"path": SRC, "action": "modify"}]
    else:
        unit.update({"execution_role": "verification", "planned_files": []})
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [{"id": "ac1", "description": "the suite is not worse", "method": "tool", "tool_name": "test"}],
        "subtasks": [unit]})


def _run_unit(tmp_path, *, pre, post, policy="required", verification_only=False):
    """One enforce unit through run_generation_workflow with a declared test verification: the PRE baseline suite
    returns ``pre``; every later full-suite run returns ``post`` (targeted runs pass). ``policy`` is the brownfield
    full-regression baseline policy."""
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.autonomy.brownfield_full_regression_baseline_policy = policy
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / SRC).write_text(SRC_BASE)
    (workspace / "tests" / "test_pricing.py").write_text("from src.pricing import price\n\ndef test_price():\n    assert price(2, 3) == 6\n")
    (workspace / "tests" / "test_legacy.py").write_text("def test_old():\n    assert 1 == 2\n")
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    suite_runs = []

    def run_tests(self, target_test=None, *args, **kwargs):
        del self, args, kwargs
        if target_test:
            return {"success": True, "output": "1 passed"}
        output = pre if not suite_runs else post
        suite_runs.append(output)
        return {"success": "failed" not in output, "output": output}

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [SRC]})
        elif "Developer Agent" in first:
            content = "".join(sentinel(path, analysis="tidy.", content=SRC_FIXED)
                              for path in dict.fromkeys(_PATH_IN_PROMPT.findall(system_prompt)))
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

    gate_outcomes = []
    real_gate = GenerationState.record_gate_outcome

    def gate(state, outcome):
        gate_outcomes.append(outcome)
        return real_gate(state, outcome)

    files = [] if verification_only else [SRC]
    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch.object(GenerationState, "record_gate_outcome", new=gate), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests):
        result = asyncio.run(engine.run_generation_workflow(
            goal=GOAL, workspace_path=str(workspace), predetermined_plan="tidy the comment",
            predetermined_design="", predetermined_architect_files=files, allowed_write_relpaths=files,
            write_scope_mode=WriteScopeMode.DENY_ALL if verification_only else None,
            structured_plan=_plan("verification" if verification_only else "implementation"), current_subtask_id="s1",
            required_verification=[{"type": "tool", "tool_name": "test", "description": "run the tests"}],
            approval_callback=AsyncMock(return_value=True)))
    return SimpleNamespace(result=result, events=events, gate_outcomes=gate_outcomes, suite_runs=suite_runs,
                           workspace=workspace)


def _deltas(run, kind):
    return [e.details for e in run.events if e.kind == kind]


def test_t1_the_measured_shape_a_pre_existing_failure_never_blocks_the_candidate_gate(tmp_path):
    """C2-S2_A unit s2: the PRE baseline carries one failing test; the candidate's full-suite gate sees the identical
    failure - PRE_EXISTING, not blocking; the unit passes and its test gate outcome says why."""
    run = _run_unit(tmp_path, pre=FAILING, post=FAILING)

    assert run.result["quality_gates_passed"] is True, (run.result.get("failure_category"), run.result.get("environment_failure"))
    assert run.result["files"] == [SRC]
    candidate = _deltas(run, "validation_baseline.candidate_gate_delta")
    assert candidate and all(d["blocking"] is False and d["level1_classification"] == "PRE_EXISTING_FAILURE" for d in candidate)
    terminal = _deltas(run, "validation_baseline.full_regression_delta")
    assert terminal and all(d["blocking"] is False for d in terminal)
    test_gates = [o for o in run.gate_outcomes if o.get("type") == "test"]
    assert test_gates and test_gates[-1]["success"] is True
    assert test_gates[-1]["regression_attribution"]["blocking"] is False
    assert test_gates[-1]["regression_attribution"]["pre_existing_only"] is True
    assert test_gates[-1]["regression_attribution"]["suite_success"] is False  # the raw verdict stays readable


def test_t2_a_new_failure_still_blocks_the_candidate_gate_with_per_test_evidence(tmp_path):
    """Negative control: the candidate adds a failure; the gate blocks and the Developer-facing evidence names the
    attributable test, never the pre-existing one as the cause."""
    run = _run_unit(tmp_path, pre=FAILING, post=NEW_FAILING)

    assert run.result["quality_gates_passed"] is False
    candidate = _deltas(run, "validation_baseline.candidate_gate_delta")
    assert candidate and all(d["blocking"] is True for d in candidate)
    failed = [o for o in run.gate_outcomes if o.get("type") == "test" and o.get("success") is False]
    assert failed and "test_price" in failed[0].get("message", "") + failed[0].get("output", "")
    attributed = [o["regression_attribution"] for o in failed if "regression_attribution" in o]
    assert attributed, failed[:1]
    # With FAILED-line-only pytest text (no structured per-case report) the new test is NOT_COMPARABLE, which blocks
    # conservatively (PRD-024); the raw output then names it. A structured report would confirm it per test.
    assert all(a["blocking"] is True and a["suite_success"] is False and a["pre_existing_only"] is False
               and a["confirmed_regressions"] in ([], ["tests/test_pricing.py::test_price"]) for a in attributed), attributed


def test_t3_without_a_captured_baseline_the_raw_rule_is_unchanged(tmp_path):
    """No PRE baseline (policy off): a failing suite still blocks - the conservative behaviour before this fix."""
    run = _run_unit(tmp_path, pre=FAILING, post=FAILING, policy="off")

    assert run.result["quality_gates_passed"] is False
    assert not _deltas(run, "validation_baseline.candidate_gate_delta")
    assert all("regression_attribution" not in o for o in run.gate_outcomes if o.get("type") == "test")


def test_t4_a_verification_only_unit_is_judged_by_the_same_attribution(tmp_path):
    """C2-S6_A unit s2 (declared test verification, DENY_ALL): the coordinator's suite run is attributed against the
    same baseline - the pre-existing failure does not fail the unit."""
    run = _run_unit(tmp_path, pre=FAILING, post=FAILING, verification_only=True)

    assert run.result["quality_gates_passed"] is True, (run.result.get("failure_category"), run.result.get("environment_failure"))
    candidate = _deltas(run, "validation_baseline.candidate_gate_delta")
    assert candidate and all(d["blocking"] is False for d in candidate)
    test_gates = [o for o in run.gate_outcomes if o.get("type") == "test"]
    assert test_gates and test_gates[-1]["success"] is True and test_gates[-1]["regression_attribution"]["pre_existing_only"] is True


def test_t4b_a_verification_only_unit_still_fails_on_a_new_failure(tmp_path):
    run = _run_unit(tmp_path, pre=FAILING, post=NEW_FAILING, verification_only=True)

    assert run.result["quality_gates_passed"] is False
    assert all(d["blocking"] is True for d in _deltas(run, "validation_baseline.candidate_gate_delta"))


# ------------------------------------------------------------------ D1b: the contract baseline (NO_MUTATION_REQUIRED)

def _case(identity, status):
    return test_execution.TestCaseResult(identity=identity, classname=identity.rsplit(".", 1)[0],
                                         name=identity.rsplit(".", 1)[-1], status=status)


def _report(cases, complete=True):
    report = test_execution.TestExecutionReport(gate_id="g", runner="pytest", workspace="/ws",
                                                completeness=test_execution.COMPLETE if complete else test_execution.INDETERMINATE,
                                                cases=list(cases))
    return report


def test_t5_the_untouched_base_with_identified_failing_cases_is_pre_existing_not_fail():
    verdict = cb.suite_verdict_from_report({"success": False}, _report([_case("tests.test_a.test_ok", test_execution.PASSED),
                                                                         _case("tests.test_b.test_old", test_execution.FAILED)]))
    assert verdict.verdict == cb.BASELINE_PRE_EXISTING and verdict.pre_existing_failures == ("tests.test_b.test_old",)
    green = cb.suite_verdict_from_report({"success": True}, _report([_case("tests.test_a.test_ok", test_execution.PASSED)]))
    assert green.verdict == cb.BASELINE_PASS and green.pre_existing_failures == ()
    # evidence that cannot name the failing cases is never read as pre-existing
    assert cb.suite_verdict_from_report({"success": False}, _report([], complete=True)).verdict == cb.BASELINE_INDETERMINATE
    assert cb.suite_verdict_from_report({"success": False}, _report([_case("t.a", test_execution.FAILED)], complete=False)).verdict == cb.BASELINE_INDETERMINATE
    assert cb.suite_verdict_from_report({"success": False}, None).verdict == cb.BASELINE_INDETERMINATE
    # a failed run whose complete report shows only passing cases: the failure is outside the cases (collection,
    # a crashed session) - FAIL, never pre-existing
    assert cb.suite_verdict_from_report({"success": False}, _report([_case("t.a", test_execution.PASSED)])).verdict == cb.BASELINE_FAIL


def test_t6_a_regression_claim_is_satisfied_at_baseline_by_recorded_pre_existing_failures_only():
    from kriya.workflow.contract_compilation import ExternalAuthority, compile_verification_contract
    from kriya.workflow.requirements import (
        BEHAVIOR,
        BEHAVIOR_EXACT,
        REGRESSION_PRESERVATION,
        derive_requirements,
        statement_origins,
    )

    text = "Examples:\n  calc.lower('ABC') -> 'abc'\n\nEvery existing test must keep passing.\n"
    reqs = derive_requirements(text)
    examples = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_EXACT}}})
    contract = compile_verification_contract(reqs, origins=statement_origins(text), test_files=["tests/test_a.py"],
                                             external_authorities=[examples], project_language="python")
    passing = SimpleNamespace(passed=True, violated=False)

    def run(suite):
        return cb.run_baseline_authorities(contract, reqs, base_revision="abc", judge_examples=lambda: {"REQ-1": passing},
                                           examples_digest="e" * 64, judge_suite=lambda: suite)

    pre_existing = run(cb.BaselineSuiteVerdict(cb.BASELINE_PRE_EXISTING, ("tests.test_legacy.test_old",)))
    assert pre_existing.no_mutation_required is True
    claim = pre_existing.claims["REQ-2"][REGRESSION_PRESERVATION]
    assert claim["state"] == cb.BASELINE_PRE_EXISTING and claim["pre_existing_failures"] == ["tests.test_legacy.test_old"]
    [suite_entry] = [e for e in pre_existing.authorities_run if e["kind"] == "baseline_suite"]
    assert suite_entry["verdict"] == cb.BASELINE_PRE_EXISTING and suite_entry["pre_existing_failures"] == ["tests.test_legacy.test_old"]
    # VC3-R9 preserved: an aggregate FAIL or an indeterminate suite never satisfies the claim
    assert run(cb.BASELINE_FAIL).no_mutation_required is False
    assert run(cb.BaselineSuiteVerdict(cb.BASELINE_FAIL, ())).no_mutation_required is False
    assert run(cb.BASELINE_INDETERMINATE).no_mutation_required is False
    assert run(cb.BASELINE_PASS).no_mutation_required is True
