"""BACKEND-READINESS-V2 slot 3: intermediate deterministic contract feedback (kriya/workflow/contract_feedback.py and the
attempt-pipeline gate). Generic fixtures only: no task, class or repository identity is known to the code under test.

The sealed example class is the REAL artifact the contract compiler produces; the JUnit run is a scripted stand-in that carries
the exact report shape (statuses, per-case type/trace, candidate classes) the terminal judge reads."""
import json
from types import SimpleNamespace

import pytest

from kriya.workflow import contract_feedback as cf
from kriya.workflow import example_oracle_java as ej
from kriya.workflow.attempt import _run_contract_feedback_gate
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.requirements import derive_requirements
from kriya.workflow.state import GenerationState

ALPHA, BETA = "src/main/java/demo/Alpha.java", "src/main/java/demo/Beta.java"
GOAL = ('Fix the builders.\n\nnew Alpha("abab").last("") -> 4\n\nnew Alpha("").last("") -> 0\n\n'
        'new Beta("abab").last("") -> 4\n\nAlpha.parse("x") -> compiles (no exception)\n\nsomething else -> prose\n')
ASSERTION = "org.opentest4j.AssertionFailedError"


def _artifact(tmp_path):
    root = tmp_path / "tree"
    for rel in (ALPHA, BETA):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("package demo;\npublic class " + rel.rsplit("/", 1)[1][:-5] + " {}\n")
    artifact, report = ej.derive_java_example_artifact(GOAL, derive_requirements(GOAL), state_root=str(tmp_path / "state"),
                                                       candidate_root=str(root))
    return root, artifact, report


def _run(artifact, failing=None, errors=None, **overrides):
    """A scripted acceptance run: ``failing``: identity -> 'expected: <X> but was: <Y>'; ``errors``: identity -> trace."""
    failing, errors = failing or {}, errors or {}
    details = {}
    for case in artifact.cases:
        if case.identity in failing:
            details[case.identity] = [{"status": "failed", "type": ASSERTION,
                                       "trace": f"{ASSERTION}: {failing[case.identity]}\n\tat HIDDEN_SECRET.frame(Foo.java:1)"}]
        elif case.identity in errors:
            details[case.identity] = [{"status": "error", "type": "java.lang.IllegalStateException", "trace": errors[case.identity]}]
        else:
            details[case.identity] = [{"status": "passed", "type": "", "trace": ""}]
    report = SimpleNamespace(complete=True, runner="maven", statuses=lambda: {i: [d["status"] for d in v] for i, v in details.items()},
                             passed=lambda identity: details[identity][0]["status"] == "passed")
    fields = dict(runner="maven", refusal=None, integrity_problem=None, report=report, case_details=details,
                  candidate_classes=["demo.Alpha", "demo.Beta"], result={})
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _feedback(tmp_path, script):
    """``script``: list of runs (or exceptions) returned by successive executions."""
    root, artifact, report = _artifact(tmp_path)
    calls = []
    queue = list(script)

    def execute(art, touched):
        calls.append(list(touched))
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item(art) if callable(item) else item

    examples = cf.contract_examples(artifact, report, str(root))
    return cf.ContractFeedback(artifact=artifact, examples=examples, execute=execute), calls, root


def _ctx(feedback, root):
    return SimpleNamespace(contract_feedback=feedback, worktree_path=str(root))


def _state(*files):
    state = GenerationState()
    state.attempt_number = 1
    state.all_files_written = list(files)
    return state


def _case(artifact, n):
    return f"{artifact.java['package']}.{artifact.java['class_name']}.example{n}"


def test_the_sealed_class_holds_only_the_enforced_forms_in_goal_order(tmp_path):
    root, artifact, report = _artifact(tmp_path)
    examples = cf.contract_examples(artifact, report, str(root))
    assert [e.number for e in examples] == [1, 2, 3] and [e.source_path for e in examples] == [ALPHA, ALPHA, BETA]
    assert all(e.form == "java_instance_value" and e.requirement_ids for e in examples)
    assert [e.text for e in examples][0] == 'new Alpha("abab").last("") -> 4'
    shadow = {x["form"] for x in report["shadow_examples"] if x["status"] == "COMPILED"}
    assert "java_static_no_throw" in shadow  # shadow forms are observational: not in the class, not examples here


def test_a_relevant_violation_is_a_typed_failure_with_the_exact_example_expected_and_observed(tmp_path):
    root, artifact, _ = _artifact(tmp_path)
    feedback, calls, root = _feedback(tmp_path / "f", [lambda art: _run(art, {_case(art, 1): "expected: <4> but was: <3>"})])
    state = _state(ALPHA)
    with pytest.raises(QualityGateFailure) as stop:
        _run_contract_feedback_gate(state, _ctx(feedback, root))
    failure = stop.value.failure
    assert failure.type == "deterministic_contract_violation" and failure.attempt == 1
    assert failure.likely_files == [ALPHA] and failure.file_locations[0].filepath == ALPHA
    for fragment in ('FAILED EXAMPLE: new Alpha("abab").last("") -> 4', "PROVENANCE: goal line 3", "TARGET: demo.Alpha",
                     "EXPECTED: 4", "OBSERVED: 3", "ASSERTION_MISMATCH", "DETERMINISTIC_CONTRACT_VIOLATION"):
        assert fragment in failure.message, fragment
    assert "original obligation" in failure.message.lower() or "original obligations" in failure.message.lower()
    assert len(calls) == 1  # ONE batched run
    outcome = state.gate_outcomes[-1]
    assert outcome["type"] == "deterministic_contract_violation" and outcome["success"] is False
    kinds = [e.kind for e in state.run_events]
    assert kinds == ["contract_feedback.evaluated", "contract_feedback.violation"]
    assert state.run_events[1].details["violations"][0]["expected"] == "4"


def test_only_the_failing_example_is_reported_and_hidden_text_never_enters_the_payload(tmp_path):
    feedback, _calls, root = _feedback(tmp_path, [lambda art: _run(art, {_case(art, 2): "expected: <0> but was: <-1>"})])
    with pytest.raises(QualityGateFailure) as stop:
        _run_contract_feedback_gate(_state(ALPHA), _ctx(feedback, root))
    message = stop.value.failure.message
    assert 'new Alpha("").last("") -> 0' in message and 'new Alpha("abab")' not in message
    assert "HIDDEN_SECRET" not in message  # only the assertion line of the JUnit trace is used, never frames
    diagnostics = stop.value.failure.diagnostics
    assert [v["expected"] for v in diagnostics["violations"]] == ["0"] and [v["observed"] for v in diagnostics["violations"]] == ["-1"]
    assert json.loads(json.dumps(stop.value.failure.to_gate_outcome()))["type"] == "deterministic_contract_violation"
    assert json.loads(json.dumps(diagnostics))["reason_code"] == "DETERMINISTIC_CONTRACT_VIOLATION"


def test_many_failures_are_reported_bounded_and_deterministically(tmp_path):
    root, artifact, report = _artifact(tmp_path)
    examples = list(cf.contract_examples(artifact, report, str(root)))
    many = [cf.ContractViolation(e, "ASSERTION_MISMATCH", "1", "2", ASSERTION) for e in examples * 3]  # 9 violations
    outcome = cf.ContractOutcome("VIOLATED", tuple(examples), tuple(many), compiled_examples=3)
    text = cf.violation_message(outcome, artifact.digest)
    assert text.count("FAILED EXAMPLE") == cf.MAX_REPORTED_VIOLATIONS and "4 more failing example(s)" in text
    assert text == cf.violation_message(outcome, artifact.digest)
    assert len(cf.violation_diagnostics(outcome, artifact.digest)["violations"]) == cf.MAX_REPORTED_VIOLATIONS


def test_repair_that_satisfies_the_examples_continues_and_still_fails_again_if_it_does_not(tmp_path):
    feedback, calls, root = _feedback(tmp_path, [lambda art: _run(art, {_case(art, 1): "expected: <4> but was: <3>"}),
                                                 lambda art: _run(art)])
    state = _state(ALPHA)
    with pytest.raises(QualityGateFailure):
        _run_contract_feedback_gate(state, _ctx(feedback, root))
    state.attempt_number = 2
    assert _run_contract_feedback_gate(state, _ctx(feedback, root)) is None  # repaired: the pipeline continues
    assert len(calls) == 2
    # a repair that still violates fails the same typed way (existing budget semantics then apply)
    feedback2, _calls2, root2 = _feedback(tmp_path / "again", [lambda art: _run(art, {_case(art, 1): "expected: <4> but was: <3>"})])
    for attempt in (1, 2):
        state2 = _state(ALPHA)
        state2.attempt_number = attempt
        with pytest.raises(QualityGateFailure) as stop:
            _run_contract_feedback_gate(state2, _ctx(feedback2, root2))
        assert stop.value.failure.attempt == attempt


def test_a_pass_is_feedback_only_it_records_no_acceptance_and_no_gate_success(tmp_path):
    feedback, _calls, root = _feedback(tmp_path, [lambda art: _run(art)])
    state = _state(ALPHA)
    assert _run_contract_feedback_gate(state, _ctx(feedback, root)) is None
    assert state.gate_outcomes == []  # later gates stay authoritative; nothing says "contract accepted"
    assert not hasattr(cf, "close_requirements_with_acceptance")  # the ledger is the terminal closer's alone


def test_relevance_is_the_touched_file_declaring_the_target_type(tmp_path):
    feedback, calls, root = _feedback(tmp_path, [lambda art: _run(art, {_case(art, 3): "expected: <4> but was: <3>"})])
    state_unrelated = _state("src/main/java/demo/Other.java", "README.md")
    assert _run_contract_feedback_gate(state_unrelated, _ctx(feedback, root)) is None and calls == []  # E: nothing runs early
    # F: Beta touched -> only example 3 is relevant; example 1 (Alpha) failing would be ignored at this unit
    with pytest.raises(QualityGateFailure) as stop:
        _run_contract_feedback_gate(_state(BETA), _ctx(feedback, root))
    assert [v["case"].rsplit(".", 1)[1] for v in stop.value.failure.diagnostics["violations"]] == ["example3"]
    assert len(calls) == 1


def test_a_failing_example_of_a_file_this_unit_did_not_touch_is_not_judged_early(tmp_path):
    feedback, _calls, root = _feedback(tmp_path, [lambda art: _run(art, {_case(art, 1): "expected: <4> but was: <3>",
                                                                          _case(art, 3): "expected: <4> but was: <3>"})])
    with pytest.raises(QualityGateFailure) as stop:
        _run_contract_feedback_gate(_state(BETA), _ctx(feedback, root))  # Alpha's example 1 also fails but Alpha is untouched
    assert [v["example"] for v in stop.value.failure.diagnostics["violations"]] == ['new Beta("abab").last("") -> 4']


@pytest.mark.parametrize("script,reason", [
    (RuntimeError("container gone"), "runner error"),
    (lambda art: _run(art, refusal=SimpleNamespace(reason_code="ACCEPTANCE_TRUST_SURFACE_UNAVAILABLE")), "ACCEPTANCE_TRUST"),
    (lambda art: _run(art, report=None), "no complete"),
    (lambda art: _run(art, integrity_problem=("ACCEPTANCE_ARTIFACT_CHANGED", "changed")), "no complete"),
    (lambda art: _run(art, case_details=None), "no complete"),
])
def test_runner_unavailability_is_recorded_and_is_never_a_violation(tmp_path, script, reason):
    feedback, _calls, root = _feedback(tmp_path, [script])
    outcome = cf.evaluate(feedback, [ALPHA])
    assert outcome.status == "UNAVAILABLE" and reason in outcome.reason and not outcome.violations
    state = _state(ALPHA)
    assert _run_contract_feedback_gate(state, _ctx(feedback, root)) is None  # terminal validation stays authoritative
    assert state.gate_outcomes == []


def test_a_failure_that_is_not_an_observed_contradiction_is_not_a_violation(tmp_path):
    """A case that failed outside the candidate (an error whose frames never pass through candidate code) is not a contradiction."""
    feedback, _c, _root = _feedback(tmp_path, [lambda art: _run(art, errors={_case(art, 1): "java.lang.IllegalStateException\n\tat harness.Foo.bar(Foo.java:1)"})])
    assert cf.evaluate(feedback, [ALPHA]).status == "PASSED"
    feedback2, _c2, _r2 = _feedback(tmp_path / "x", [lambda art: _run(art, errors={_case(art, 1): "java.lang.IllegalStateException: boom\n\tat demo.Alpha.last(Alpha.java:5)"})])
    outcome = cf.evaluate(feedback2, [ALPHA])
    assert outcome.status == "VIOLATED" and outcome.violations[0].kind == "EXCEPTION_THROWN"


def test_no_bound_class_or_no_touched_relevant_file_costs_nothing(tmp_path):
    assert cf.evaluate(None, [ALPHA]).status == "NOT_RELEVANT"
    state = _state(ALPHA)
    assert _run_contract_feedback_gate(state, SimpleNamespace(contract_feedback=None, worktree_path=str(tmp_path))) is None
    assert state.gate_outcomes == []


def test_the_violation_type_is_registered_retriable_and_not_a_deterministic_stop():
    from kriya.workflow import retry_strategy
    from kriya.workflow.failure_reporting import _FAILURE_TYPE_TO_CATEGORY, FailureCategory

    assert _FAILURE_TYPE_TO_CATEGORY[cf.FAILURE_TYPE] is FailureCategory.VERIFICATION
    source = open(retry_strategy.__file__, encoding="utf-8").read()
    stop_block = source[source.index('"requirements_unresolved",') - 400:source.index('"verification_tree_mutated",') + 40]
    assert cf.FAILURE_TYPE not in stop_block  # unlike requirements_unresolved: this one reaches the Developer
    assert '"deterministic_contract_violation": 2.5' in source  # ordered between compile and test for no-progress detection


# --- the whole direct workflow, scripted Developer, scripted contract runner -------------------------------------------------

LAST_BUGGY = """/*
 * Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements.
 */
package demo;

public class Alpha {
    private final String text;

    public Alpha(String text) {
        this.text = text;
    }

    public int last(String needle) {
        return text.lastIndexOf(needle, text.length() - 1);
    }
}
"""
LAST_FIXED = LAST_BUGGY.replace("text.length() - 1", "text.length()")
LAST_STILL_WRONG = LAST_BUGGY.replace("text.length() - 1", "text.length() - 2")  # differs from the base, still wrong
E2E_GOAL = ('Make Alpha.last("") match String.lastIndexOf.\n\nnew Alpha("abab").last("") -> 4\n\nnew Alpha("").last("") -> 0\n')


def _scripted_runner(calls):
    """Stands in for the real JVM runner: reads the candidate's bytes like the real one and reports the sealed cases."""
    def runner(artifact, touched, *, candidate_root, workspace_path, autonomy_cfg):
        del workspace_path, autonomy_cfg
        text = open(f"{candidate_root}/{ALPHA}", encoding="utf-8").read()
        calls.append((list(touched), "length() - " in text))
        failing = {}
        if "length() - " in text:
            for case in artifact.cases[:1]:
                failing[case.identity] = "expected: <4> but was: <3>"
        return _run(artifact, failing)
    return runner


def _workflow(tmp_path, answers, *, compile_ok=True, runner=None, tests_ok=True):
    import asyncio
    import subprocess
    from unittest.mock import AsyncMock, patch

    from _edit_protocol_harness import make_config
    from _protocol_responses import sentinel

    from kriya.core import model_runtime
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    root = tmp_path / "ws"
    (root / ALPHA).parent.mkdir(parents=True)
    (root / ALPHA).write_text(LAST_BUGGY)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True)
    model_runtime.clear_model_runtime_cache()
    config = make_config(tmp_path)
    prompts = []
    queue = [sentinel(ALPHA, analysis="FIX ANALYSIS: adjust the start index.", content=content) for content in answers]

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = '{"files": ["%s"]}' % ALPHA
        elif "Developer Agent" in first:
            prompts.append(user_prompt)
            content = queue.pop(0) if len(queue) > 1 else queue[0]
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5, "finish_reason": "stop",
                "provider_metadata": {}}

    calls = []
    engine = WorkflowEngine(Kernel(config=config), LLMClient(config))
    compile_result = {"success": True, "output": "ok"} if compile_ok else {"success": False, "output": "error: broken"}
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch("kriya.workflow.workflow._run_contract_acceptance", new=runner or _scripted_runner(calls)), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: compile_result), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: (
             {"success": True, "output": "1 passed"} if tests_ok else
             {"success": False, "output": "Tests run: 1, Failures: 1\nAlphaTest.last:5 expected: <4> but was: <3>"})):
        result = asyncio.run(engine.run_generation_workflow(
            goal=E2E_GOAL, workspace_path=str(root), predetermined_plan=f"Repair {ALPHA}", predetermined_design="",
            predetermined_architect_files=[ALPHA], approval_callback=AsyncMock(return_value=True)))
    return result, prompts, calls, (root / ALPHA).read_text()


def test_end_to_end_a_violating_candidate_reaches_repair_with_exact_evidence_and_the_repair_succeeds(tmp_path):
    result, prompts, calls, final = _workflow(tmp_path, [LAST_STILL_WRONG, LAST_FIXED])
    assert result.get("quality_gates_passed") is True and "length() - " not in final
    assert len(prompts) == 2 and len(calls) == 2  # the Developer was called twice; the contract ran once per attempt
    repair = prompts[1]
    for fragment in ("DETERMINISTIC_CONTRACT_VIOLATION", 'FAILED EXAMPLE: new Alpha("abab").last("") -> 4', "EXPECTED: 4",
                     "OBSERVED: 3", "PROVENANCE: goal line 3", "Make Alpha.last(\"\") match String.lastIndexOf"):
        assert fragment in repair, fragment  # exact failure + the original goal obligations reach the repair request
    assert "HIDDEN" not in repair
    types = [o["type"] for o in result.get("gate_outcomes", [])] if "gate_outcomes" in result else None
    assert types is None or types.count("deterministic_contract_violation") == 1


def test_end_to_end_an_exhausted_repair_still_ends_in_safe_failure_with_the_real_workspace_untouched(tmp_path):
    result, prompts, calls, final = _workflow(tmp_path, [LAST_STILL_WRONG])  # the Developer never fixes it
    assert result.get("quality_gates_passed") is False and final == LAST_BUGGY  # failed attempts never reach the workspace
    assert len(prompts) == len(calls) >= 2


def test_end_to_end_the_contract_feedback_adds_no_attempts_over_an_ordinary_failing_gate(tmp_path):
    """The existing budgets are 4 full-set + 3 targeted attempts. A candidate that keeps violating the contract consumes exactly
    that and no more - the same number as a candidate that keeps failing an ordinary test gate."""
    _r1, contract_prompts, _c, _f = _workflow(tmp_path / "contract", [LAST_STILL_WRONG])
    _r2, test_prompts, _c2, _f2 = _workflow(tmp_path / "tests", [LAST_FIXED], tests_ok=False)
    assert len(contract_prompts) == 7  # 4 + 3: the whole existing budget, not one attempt more
    assert len(contract_prompts) <= max(len(test_prompts), 7)


def test_end_to_end_a_candidate_that_passes_the_examples_is_not_blocked_and_the_runner_ran_once(tmp_path):
    result, prompts, calls, final = _workflow(tmp_path, [LAST_FIXED])
    assert result.get("quality_gates_passed") is True and len(prompts) == 1 and len(calls) == 1 and "length() - " not in final


def test_end_to_end_an_unavailable_runner_never_becomes_a_candidate_failure(tmp_path):
    def broken(artifact, touched, **_kwargs):
        raise RuntimeError("container unavailable")

    result, prompts, _calls, _final = _workflow(tmp_path, [LAST_FIXED], runner=broken)
    assert len(prompts) == 1 and result.get("quality_gates_passed") is True  # recorded, ignored; terminal validation decides


def test_the_gate_sits_after_compile_and_before_the_model_verifier_gates():
    import inspect

    from kriya.workflow import attempt

    source = inspect.getsource(attempt.run_attempt)
    assert source.index("compile_res = validator.run_compile_check") < source.index("_run_contract_feedback_gate(state, ctx)") \
        < source.index("runnable_test_files = find_runnable_test_files")


def test_unsupported_ambiguous_and_shadow_lines_alone_bind_no_feedback(tmp_path):
    from types import SimpleNamespace as NS

    from kriya.workflow.workflow import build_contract_feedback

    root = tmp_path / "tree"
    (root / ALPHA).parent.mkdir(parents=True)
    (root / ALPHA).write_text("package demo;\npublic class Alpha {}\n")
    goal = ('Fix.\n\nAlpha.parse("x") -> compiles (no exception)\n\nnew Alpha("a").last("") -> fails\n\n'
            'new Alpha("a").last("") -> 4L\n\nAlpha.go(x) -> 1\n\nprose -> more prose\n')
    artifact, report = ej.derive_java_example_artifact(goal, derive_requirements(goal), state_root=str(tmp_path / "s"),
                                                       candidate_root=str(root))
    assert artifact is None  # nothing enforceable compiled: no sealed class at all
    engine = NS(derived_examples=artifact, derived_examples_report=report)
    assert build_contract_feedback(engine, str(root), str(root), object()) is None
    assert {x["status"] for x in report["shadow_examples"]} >= {"AMBIGUOUS", "COMPILED", "UNSUPPORTED"}


def test_a_unit_that_creates_the_target_type_is_covered_and_an_unrelated_path_is_not(tmp_path):
    root, artifact, report = _artifact(tmp_path)
    (root / BETA).unlink()  # the target file does not exist when the unit starts (it will be created)
    examples = cf.contract_examples(artifact, report, str(root))
    assert examples[2].source_path is None  # unknown before generation ...
    created = cf.relevant_examples(examples, [BETA])
    assert [e.number for e in created] == [3] and created[0].source_path == BETA  # ... resolved from what the unit touched
    assert cf.relevant_examples(examples, ["./" + BETA])[0].source_path == BETA  # path spelling is normalised
    for other in ("src/test/java/demo/Beta.java", "src/main/java/other/Beta.java", "src/main/java/demo/BetaX.java", "demo/Beta.java"):
        assert cf.relevant_examples(examples, [other]) == (), other


def test_the_reported_observation_is_the_entry_that_was_a_contradiction(tmp_path):
    from kriya.workflow.acceptance_jvm import contradicted_case_details

    _root, artifact, _report = _artifact(tmp_path)
    run = _run(artifact, {_case(artifact, 1): "expected: <4> but was: <3>"})
    case = _case(artifact, 1)
    run.case_details[case] = [{"status": "skipped", "type": "", "trace": ""}, *run.case_details[case]]
    run.report.statuses = lambda: {i: [d["status"] for d in v] for i, v in run.case_details.items()}
    found = contradicted_case_details(artifact, run)
    assert found[case]["status"] == "failed" and "expected: <4>" in found[case]["trace"]


def test_the_gate_never_writes_to_the_unit_tree_or_the_requirement_ledger(tmp_path):
    import hashlib

    feedback, _calls, root = _feedback(tmp_path, [lambda art: _run(art, {_case(art, 1): "expected: <4> but was: <3>"}), lambda art: _run(art)])

    def digest():
        return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(root.rglob("*.java")))).hexdigest()

    before = digest()
    with pytest.raises(QualityGateFailure):
        _run_contract_feedback_gate(_state(ALPHA), _ctx(feedback, root))
    assert _run_contract_feedback_gate(_state(ALPHA), _ctx(feedback, root)) is None
    assert digest() == before


def test_end_to_end_the_terminal_examples_closer_still_evaluates_after_an_intermediate_pass(tmp_path):
    from unittest.mock import patch

    import kriya.workflow.workflow as workflow_module

    seen = []
    real = workflow_module.close_requirements_with_derived_examples

    def spy(*args, **kwargs):
        seen.append(kwargs.get("modified"))
        return real(*args, **kwargs)

    with patch.object(workflow_module, "close_requirements_with_derived_examples", spy):
        result, _prompts, calls, _final = _workflow(tmp_path, [LAST_FIXED])
    assert result.get("quality_gates_passed") is True and len(calls) == 1  # the intermediate gate ran and passed ...
    assert seen, "the terminal/pre-apply examples closer must still run on the final candidate"  # ... and did not replace the closer


# --- real Maven/JUnit: the production runner path of the gate (skipped without mvn) -------------------------------------------

@pytest.mark.skipif(__import__("shutil").which("mvn") is None, reason="real Maven is not installed")
def test_real_runner_pass_and_violation_through_build_contract_feedback(tmp_path):
    from _b2c_fixtures import BASE_CALC, TARGET, maven_workspace

    from kriya.config.config import AutonomyConfig
    from kriya.workflow.workflow import build_contract_feedback

    workspace = maven_workspace(tmp_path / "ws")
    goal = "Make Calc.twice double its argument.\n\nCalc.twice(2) -> 4\n\nCalc.twice(5) -> 10\n"
    artifact, report = ej.derive_java_example_artifact(goal, derive_requirements(goal), state_root=str(tmp_path / "state"),
                                                       candidate_root=str(workspace))
    engine = SimpleNamespace(derived_examples=artifact, derived_examples_report=report)
    import os

    os.environ["KRIYA_STATE_DIR"] = str(tmp_path / "state")
    feedback = build_contract_feedback(engine, str(workspace), str(workspace), AutonomyConfig())
    assert feedback is not None and [e.number for e in feedback.examples] == [1, 2]
    target = workspace / TARGET
    assert cf.evaluate(feedback, [TARGET]).status == "PASSED"  # the base satisfies the examples
    target.write_text(BASE_CALC.replace("2 * value", "3 * value"))
    outcome = cf.evaluate(feedback, [TARGET])
    assert outcome.status == "VIOLATED" and len(outcome.violations) == 2
    observed = {(v.expected, v.observed) for v in outcome.violations}
    assert observed == {("4", "6"), ("10", "15")}  # exact expected/observed parsed from the real JUnit report
    assert feedback.runs == 2
    assert cf.evaluate(feedback, ["src/main/java/demo/Other.java"]).status == "NOT_RELEVANT" and feedback.runs == 2
