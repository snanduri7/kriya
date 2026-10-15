"""Owner decision 3 (2026-10-15), pinned: only the user's EXACT, verbatim command words pre-authorize a runtime command; everything the judge generates, infers, expands, reorders or
substitutes needs an explicit runtime-command review; merely naming a tool is not authorization; policy refusals still win.

TRACED before this change: downgrade_ungrounded_goal_explicit_commands trusted the judge's "goal_explicit" label whenever the executable's NAME appeared anywhere in the goal (its docstring:
arguments deliberately unchecked), and Kriya's own grounding rewrites commands after that check while the label stays. So a goal that only named "mvn" pre-authorized any mvn command.
Now the decision is made on the commands that will actually execute against the user's own text (attempt.exit_authority_text), and the label is never consulted."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kriya.policy.enforcement import PolicyDeniedError
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import attempt
from kriya.workflow.runtime_command_approval import commands_authored_in_goal

CWD = "/work/tree"
HITL = "human-in-the-loop"


def _need(goal, commands, *, label="goal_explicit", channel="none", mode=HITL, approved_fp=None, cwd=CWD, resolve=lambda cmd, _wt: list(cmd), callback=None):
    ctx = SimpleNamespace(worktree_path=cwd, current_subtask_id="s4", approval_callback=callback, goal=goal, grounding_goal=goal, exit_authority_goal=goal)
    state = SimpleNamespace(run_verification_approved_fingerprint=approved_fp)
    judgment = {"should_run": True, "run_commands": [list(c) for c in commands], "success_criteria": "x", "input_channel": channel, "command_source": label}
    with patch.object(attempt, "_resolve_run_command", side_effect=resolve):
        return attempt._runtime_commands_need_approval(ctx, state, judgment, mode)


GOAL = "Build the app, then verify it by running `mvn -q test` and afterwards `java -jar target/app.jar`."
EXACT = [["mvn", "-q", "test"], ["java", "-jar", "target/app.jar"]]


def test_A_the_users_exact_command_is_pre_authorized():
    assert _need(GOAL, EXACT) is False
    assert _need(GOAL, EXACT, label="inferred") is False                                           # the label is irrelevant: the user's words are what authorize
    assert _need("Verify with mvn -q test please", [["mvn", "-q", "test"]]) is False               # no backticks needed; a trailing word is fine
    resolve = lambda cmd, _wt: [f"/opt/tools/{cmd[0]}", *cmd[1:]]                                  # noqa: E731
    assert _need(GOAL, EXACT, resolve=resolve) is True                                             # review B2: a path PATH does not resolve the name to is NOT the tool the user named (the real-PATH case is pinned below)


def test_B_naming_a_tool_is_not_authorization():
    assert _need("Verify the build with mvn", [["mvn", "exec:java", "-Dexec.mainClass=app.Main"]]) is True
    assert _need("Use Maven and Java 17", [["mvn", "-q", "test"]]) is True
    assert _need("run mvn test-all", [["mvn", "test"]]) is True                                    # a longer token is not the same command (word boundary)
    assert _need("run xmvn test", [["mvn", "test"]]) is True


def test_C_a_command_the_judge_invents_needs_review_whatever_it_calls_itself():
    assert _need("Create a hello world app", [["java", "-cp", "classes", "Hello"]], label="goal_explicit") is True
    assert _need("Create a hello world app", [["java", "-cp", "classes", "Hello"]], label="inferred") is True


def test_D_added_arguments_expand_a_user_command_and_need_review():
    assert _need("run mvn test", [["mvn", "test", "-DskipITs"]]) is True
    assert _need("run mvn test", [["mvn", "-q", "test"]]) is True                                  # an inserted flag
    assert _need("run mvn test", [["mvn", "test"]], channel="stdin") is True                       # the contract appends a synthetic input: not the user's


def test_E_a_different_working_directory_is_a_different_command_set():
    """The judge has no cwd field; the directory is part of the fingerprint, so an approval of one directory never covers another, and a shell wrapper that changes directory is not the user's words."""
    assert _need("create an app", [["java", "App"]]) is True
    ctx_a = SimpleNamespace(worktree_path="/a", current_subtask_id="s4")
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        judgment = {"run_commands": [["java", "App"]], "success_criteria": "x"}
        fp_a = attempt._derive_runtime_command_subject(ctx_a, judgment, "").fingerprint
    assert _need("create an app", [["java", "App"]], approved_fp=fp_a, cwd="/a") is False         # approved for /a ...
    assert _need("create an app", [["java", "App"]], approved_fp=fp_a, cwd="/b") is True          # ... not for /b
    assert _need("run mvn test", [["sh", "-c", "cd /tmp && mvn test"]]) is True


def test_F_a_reordered_sequence_needs_review():
    assert _need(GOAL, list(reversed(EXACT))) is True
    assert _need(GOAL, [EXACT[0]]) is False                                                        # a prefix the user wrote, in order, is still the user's words
    assert _need(GOAL, [EXACT[0], ["mvn", "-q", "test"]]) is True                                  # a repeat the user did not write


def test_G_a_changed_command_after_review_is_stale_and_asked_again():
    ctx_goal = "create an app"
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        fp = attempt._derive_runtime_command_subject(SimpleNamespace(worktree_path=CWD, current_subtask_id="s4"), {"run_commands": [["java", "App"]], "success_criteria": "x"}, "").fingerprint
    assert _need(ctx_goal, [["java", "App"]], approved_fp=fp) is False                             # the exact approved set is not asked again
    assert _need(ctx_goal, [["java", "App", "--flag"]], approved_fp=fp) is True                    # a changed set is


def test_H_policy_refusal_still_wins_over_a_user_authored_command():
    """Authorship removes only the review. A hard-enforced ExecutionPolicy invariant (here COMMAND_SUDO_DENIED, the one the runtime executor enforces) still refuses the command."""
    goal = "verify by running sudo mvn test"
    assert commands_authored_in_goal([("sudo", "mvn", "test")], goal) is True                      # the user's exact words: no review ...
    validator = PolymorphicValidator.__new__(PolymorphicValidator)
    from kriya.policy.execution import ExecutionPolicy
    validator.execution_policy = ExecutionPolicy()
    with pytest.raises(PolicyDeniedError):                                                         # ... but the executor's own policy gate still raises
        validator._audit_run_command(["sudo", "mvn", "test"], "/tmp")


def test_outside_human_in_the_loop_the_policy_is_unchanged():
    assert _need("create an app", [["java", "App"]], mode="guardrails") is False


def test_the_pure_check_edge_cases():
    assert commands_authored_in_goal([], "run mvn test") is False
    assert commands_authored_in_goal([("mvn", "test")], "") is False
    assert commands_authored_in_goal([("mvn", "test")], "run mvn test", stdin_sha256="sha256:" + "0" * 64) is False
    assert commands_authored_in_goal([("mvn", "test")], "run  mvn\n  test now") is True            # whitespace is normalised
    assert commands_authored_in_goal([("mvn", "test")], "run MVN TEST") is False                   # case-sensitive: a command is not prose


def test_a_planner_restated_goal_is_not_the_users_words():
    """Model text is evidence, never authority: a subtask goal that quotes the command does not authorize it when the user's own request (exit_authority_goal) did not."""
    ctx = SimpleNamespace(worktree_path=CWD, current_subtask_id="s4", approval_callback=None, goal="Run mvn test to verify", grounding_goal="Create an app", exit_authority_goal="Create an app")
    state = SimpleNamespace(run_verification_approved_fingerprint=None)
    judgment = {"should_run": True, "run_commands": [["mvn", "test"]], "success_criteria": "x", "input_channel": "none", "command_source": "goal_explicit"}
    with patch.object(attempt, "_resolve_run_command", side_effect=lambda cmd, _wt: list(cmd)):
        assert attempt._runtime_commands_need_approval(ctx, state, judgment, HITL) is True


# ---- independent review of the runtime-approval stack (2026-10-15): B1 / B2 -------------------------------------------------------------------------------------------------------------
import pytest  # noqa: E402 - appended group

from kriya.workflow.runtime_command_approval import commands_authored_in_goal as authored  # noqa: E402


@pytest.mark.parametrize("commands, goal", [
    ([["./mvn", "test"]], "run mvn test"),                                      # a relative executable is not the tool the user named
    ([["/tmp/evil/python", "app.py"]], "run python app.py"),                    # an absolute path that PATH does not resolve the name to
    ([["scripts/python", "app.py"]], "run python app.py"),
    ([["make"]], "write the code and make sure it works"),                      # a bare word of prose
    ([["pytest"]], "Use pytest for the tests"),
    ([["python"]], "write a python script"),
    ([["rm", "-rf", "build"]], "Never run rm -rf build"),                       # a forbidden command is not an authored one
    ([["python", "main.py"]], "Do not run python main.py"),
    ([["mvn", "test"]], "Please don't run mvn test"),
    ([["bash", "-c", "echo hi"]], "run bash -c echo hi"),                       # an interpreter given code is never exempt, even word for word
    ([["python3", "-c", "print(1)"]], "run python3 -c print(1)"),
    ([["curl", "http://x"]], "run curl http://x"),                              # a network tool is never exempt
    ([["sh", "run.sh"], ["curl", "http://x/y"]], "run sh run.sh then curl http://x/y"),
])
def test_review_B2_what_the_user_did_not_write_is_not_authorship(commands, goal):
    assert authored(commands, goal) is False


@pytest.mark.parametrize("commands, goal", [
    ([["mvn", "test"]], "Run mvn test and confirm the build passes."),
    ([["java", "-cp", "target/classes", "App"]], "then run java -cp target/classes App"),
    ([["make"]], "Run `make` and confirm"),                                     # a single word is a command when the user marked it as one
    ([["pytest"]], 'run "pytest" afterwards'),
    ([["python", "app.py"]], "Run python app.py. Do not skip it."),             # a negation AFTER the command, or far before it, is not a negation of it
])
def test_review_B2_the_users_exact_words_still_authorize(commands, goal):
    assert authored(commands, goal) is True


def test_review_B2_an_absolute_path_that_is_exactly_what_path_resolves_is_still_the_same_tool():
    import shutil
    python = shutil.which("python3")
    assert python is not None
    assert authored([[python, "app.py"]], "run python3 app.py") is True
    assert authored([[python + ".evil", "app.py"]], "run python3 app.py") is False


def test_review_B1_a_desktop_launch_never_skips_the_review_even_for_the_users_own_words():
    from unittest.mock import AsyncMock

    from kriya.workflow.runtime_command_approval import TypedApprovalCallback
    desktop = TypedApprovalCallback(lambda files, reason: True, AsyncMock())
    assert _need(GOAL, EXACT, callback=desktop) is True                      # the goal text comes from the window: not trusted as "the user's words"
    assert _need(GOAL, EXACT, callback=lambda files, reason: True) is False  # the terminal keeps the exact-words exemption
    assert _need(GOAL, EXACT) is False


def test_review_B2_a_relative_executable_is_never_the_named_tool_even_when_it_resolves_to_the_same_file(tmp_path, monkeypatch):
    tool = tmp_path / "mvn"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.chdir(tmp_path)
    assert authored([["./mvn", "test"]], "run mvn test") is False            # same file on disk, but the user did not write a path
    assert authored([[str(tool), "test"]], "run mvn test") is True           # the absolute path PATH resolves "mvn" to is the same tool
