"""CHARACTERISATION (pins MEASURED behaviour; flip these when the owner decides how Demo 00 gets runtime proof). Greenfield Demo 00 (owner run 20261015T075602-2b5a0ad7): after the empty-approval fix, what does Kriya do at the old s4 stop, and is the application ever EXECUTED?

CHARACTERISATION of measured behaviour (handover/DEMO-V2/GREENFIELD-OWNER-UX-FINDINGS.md): the owner's goal is "Create a small Java 17 Maven application named hello-kriya that prints
"Hello from Kriya" and includes a unit test. Make the application runnable." The Planner's s4 carried a runtime verifier ("Confirm that the application output includes ...");
plan normalization (scope_verification_to_requirements) removed it because Kriya's own reading of the goal, goal_requires_runtime_behavior, is False; s2's run-verification judge
asked to run the main class but the subtask is advisory-only ("a later subtask that DOES own this obligation still runs it"), and no later subtask does. Through the real controller and
workflow; only the model transport and the toolchain gates are stubbed.
"""
import _enforce_unit_harness as harness

from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.plan_schema import EngineeringPlan

GOAL = ('Create a small Java 17 Maven application named hello-kriya that\nprints "Hello from Kriya" and includes a unit test.\nMake the application runnable.')
POM, MAIN, TEST = "pom.xml", "src/main/java/com/example/HelloKriya.java", "src/test/java/com/example/HelloKriyaTest.java"
UNITS = {"s1": (POM, "<project/>\n"), "s2": (MAIN, "package com.example;\npublic class HelloKriya {\n  public static void main(String[] a) { System.out.println(\"Hello from Kriya\"); }\n}\n"),
         "s3": (TEST, "package com.example;\npublic class HelloKriyaTest {}\n")}
JUDGE = {"should_run": True, "execution_mode": "finite_command", "run_commands": [["mvn", "exec:java", "-Dexec.mainClass=com.example.HelloKriya"]], "managed_service": None,
         "command_source": "goal_explicit", "input_channel": "none", "success_criteria": "The command output contains the text 'Hello from Kriya'", "reasoning": "recorded in the owner run"}


def _unit(sid, path, description, verifier, depends=(), provides=None):
    return {"id": sid, "description": description, "execution_method": "model", "planned_files": [{"path": path, "action": "create"}], "depends_on": list(depends),
            "provides": [provides or f"p_{sid}"], "requires": [f"p_{d}" for d in depends], "acceptance_criteria_ids": ["ac1"], "relevant_global_invariant_ids": ["gi1"],
            "verification": [verifier]}


def _plan():
    compile_ = {"type": "tool", "tool_name": "compile", "description": "compile"}
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "milestone", "global_invariants": [{"id": "gi1", "statement": "All Java code must target Java 17 compatibility."}],
        "acceptance_criteria": [{"id": "ac1", "description": "The application prints 'Hello from Kriya' when executed.", "method": "judgment"}],
        "subtasks": [
            _unit("s1", POM, "Generate the Maven project", compile_),
            _unit("s2", MAIN, "Create the main class", compile_, depends=("s1",)),
            _unit("s3", TEST, "Implement a unit test", {"type": "tool", "tool_name": "test", "description": "test"}, depends=("s1", "s2")),
            {"id": "s4", "description": "Verify that the application can be built and run successfully.", "execution_method": "model", "execution_role": "verification",
             "planned_files": [], "depends_on": ["s1", "s2", "s3"], "requires": ["p_s1", "p_s2", "p_s3"], "acceptance_criteria_ids": ["ac1"], "relevant_global_invariant_ids": ["gi1"],
             "verification": [{"type": "tool", "tool_name": "compile", "description": "Build the project with Maven"},
                              {"type": "judgment", "description": "Confirm that the application output includes 'Hello from Kriya'.", "verifier_kind": "application_runtime",
                               "requires_runtime_execution": True}]}]})


GOAL_RUN = ('Create a small Java 17 Maven application named hello-kriya that\nprints "Hello from Kriya" and includes a unit test.\n'
            'Run the application and confirm it prints "Hello from Kriya".')


def _run(tmp_path, goal=GOAL, judge=JUDGE, callback=None):
    return harness.run_enforce(tmp_path, plan=_plan(), units=UNITS, goal=goal, run_verification=True, judge_reply=judge, app_output="Hello from Kriya\n", callback=callback)


def _desktop(calls):
    """What a desktop launch does (kriya/cli.py::_approval_decision): a real change is held for the person (approved here), every empty-file approval is declined at once."""
    def decide(files, reason):
        calls.append((len(files), reason))
        return bool(files)
    return decide


def test_the_goal_reading_that_drives_the_scoping():
    assert goal_requires_runtime_behavior(GOAL) is False        # the owner's own wording matches none of the runtime-context patterns
    assert goal_requires_runtime_behavior(GOAL_RUN) is True     # "Run the application and confirm ..." does


def test_a_goal_not_recognised_as_runtime_behaviour_succeeds_without_ever_executing_the_application(tmp_path):
    """MEASURED defect after the empty-approval fix: s4's runtime verifier is normalised away, s2's judge is advisory-only, nothing owns execution -> SUCCESS with no run."""
    run = _run(tmp_path)

    assert [(unit, files) for unit, files, _reason in run.approvals] == [("s1", [POM]), ("s2", [MAIN]), ("s3", [TEST])]     # the old fourth, empty approval is gone
    assert harness.decisions(run.events) == ["approved", "approved", "approved"]
    assert {r.subtask_id: r.status.value for r in run.result.subtask_results} == {"s1": "completed", "s2": "completed", "s3": "completed", "s4": "completed"}
    assert run.result.legacy_result["status"] == "success"
    assert run.judge_calls == ["s1", "s2", "s3"]                # the runtime judge was never consulted for s4: it owns no runtime obligation any more
    assert run.executed == []                                   # the application was never executed: no "Hello from Kriya" evidence exists


def test_a_goal_recognised_as_runtime_behaviour_reaches_a_command_approval_the_desktop_declines(tmp_path):
    """MEASURED (step 10): with the runtime obligation kept, s4 asks approval_callback([], ...) for a RUNTIME COMMAND - a different subject from a source change. A desktop launch
    declines it (note_approval_required -> other_approval), so nothing is executed and the run fails at s4."""
    calls = []
    run = _run(tmp_path, goal=GOAL_RUN, callback=_desktop(calls))

    assert [c for c in calls if c[0] == 0] and len([c for c in calls if c[0] == 0]) == 1                  # exactly one empty-file approval, at s4
    [(_n, reason)] = [c for c in calls if c[0] == 0]
    assert "wants to actually run the generated app" in reason and "Allow Kriya to execute these command(s)" in reason
    assert "Looking for: The command output contains the text 'Hello from Kriya'" in reason             # the subject: command argv + the success criterion
    assert run.executed == []                                                                           # declined: nothing was run
    assert {r.subtask_id: r.status.value for r in run.result.subtask_results}["s4"] == "failed"
    assert run.result.legacy_result["status"] == "failed"


def test_the_runtime_command_approval_is_a_different_subject_from_the_source_change_gate(tmp_path):
    """Same recording callback that approves the command: the command runs ONLY after that distinct approval, and the source-change gate still saw exactly three changes."""
    run = _run(tmp_path, goal=GOAL_RUN)

    assert [(unit, files) for unit, files, _r in run.approvals if files] == [("s1", [POM]), ("s2", [MAIN]), ("s3", [TEST])]
    [(unit, files, reason)] = [a for a in run.approvals if not a[1]]
    assert unit == "s4" and "Allow Kriya to execute" in reason
    assert harness.decisions(run.events) == ["approved", "approved", "approved"]                       # the command approval is NOT an approval.decision of the change gate
    assert run.executed, "approved -> the command runs"
