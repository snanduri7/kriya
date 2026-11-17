"""S6 closer parity (owner ruling 2026-11-17): the enforce terminal gate closes the SAME sealed ``file_creation`` and
``runtime_stdout`` requirements the direct executor closes, through the SAME closers, from its own authoritative evidence.

TRACED before the change (kriya/workflow/terminal_gate_service.py at 63c04ba): both closers were handed ``None`` at the enforce
terminal gate, so "Create hello.py. It prints hello world when executed." could be ALL_VERIFIED on direct and OPEN on enforce for an
equivalent final candidate - after S6, SUCCESS on one executor and APPLIED_REQUIREMENTS_OPEN on the other.

- FILE_CREATION: ``requirement_closers.created_paths_from_terminal_batch`` - the base tree of the run's mutation record, the plan's
  start-of-run revision and the declared create of the batch the commit will write. Never a folder's contents.
- RUNTIME_STDOUT: ``requirement_closers.terminal_runtime_stdout_outcomes`` - one run of the sealed statement's own script on the
  isolated final candidate through the candidate validator's runtime seam; the gate outcome it returns is read by the shared
  observation reader and judged by the shared closer.

The last test is the whole path: the real enforce controller (real plan sandbox, terminal gates, commit; the model and the plan parse
scripted) on the hello goal ends SUCCESS with ALL_VERIFIED.
"""
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_project_continuity_two_run import SPEC_MARKER  # noqa: E402
from test_runtime_observation_closers import _setup, _step  # noqa: E402

from kriya.config.config import AppConfig  # noqa: E402
from kriya.config.environment import PROJECT_PYTHON_INTERPRETER as PYTHON  # noqa: E402
from kriya.repository.commit import read_file_revision  # noqa: E402
from kriya.tools.validate import RUNTIME_VERIFICATION_GATE  # noqa: E402
from kriya.workflow import requirement_closers as rc  # noqa: E402
from kriya.workflow import workflow as facade  # noqa: E402
from kriya.workflow.requirements import RequirementOutcome, requirement_outcomes  # noqa: E402
from kriya.workflow.runtime_observation import CANDIDATE_CHANGED, NO_APPLICATION_RUN  # noqa: E402
from kriya.workflow.terminal_commit import CandidateFile, materialize_candidate  # noqa: E402

UNQUOTED = "Create hello.py. It prints hello world when executed.\n"
QUOTED = 'Create hello.py. It prints "hello world" when executed.\n'
HELLO = "print('hello world')\n"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                   cwd=repo, check=True, capture_output=True)


def _repo_and_candidate(tmp_path, *, tracked=None, untracked=None, candidate=None):
    """A real workspace repository (``tracked`` committed, ``untracked`` left in the folder) and an isolated candidate at the same
    base revision holding ``candidate``'s files. The start-of-run revisions are read from the workspace BEFORE the candidate exists,
    as the enforce controller reads them before any unit runs."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _git(workspace, "init", "-q")
    for name, text in {"README.md": "x\n", **(tracked or {})}.items():
        (workspace / name).write_text(text)
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "seed")
    for name, text in (untracked or {}).items():
        (workspace / name).write_text(text)
    root = tmp_path / "cand"
    _git(tmp_path, "clone", "-q", str(workspace), str(root))
    for name, text in (candidate if candidate is not None else {"hello.py": HELLO}).items():
        (root / name).write_text(text)
    return str(workspace), str(root)


def _batch(workspace, root, files):
    """``files``: path -> (declared base existence, delete). The revision is the workspace's own, as the controller records it."""
    return materialize_candidate(root, workspace, [
        CandidateFile(relpath=path, expected_base_revision=read_file_revision(os.path.join(workspace, path)),
                      expected_base_exists=exists, delete=delete)
        for path, (exists, delete) in files.items()])


@pytest.fixture
def base_is_head(monkeypatch):
    """No owning run's committed history: the base is the candidate's HEAD (``mutation_scope_evidence``)."""
    monkeypatch.setattr(facade, "_run_committed_paths", lambda _workspace: (None, None, []))


def _created(goal, workspace, root, writes, paths=("hello.py",)):
    _reqs, contract, _ledger = _setup(goal)
    return facade.created_paths_from_terminal_batch(contract, root, workspace, writes=writes, candidate_paths=list(paths))


# ---------------------------------------------------------------- FILE_CREATION at the enforce terminal gate

@pytest.mark.usefixtures("base_is_head")
def test_a_create_of_the_commit_batch_absent_at_the_base_closes_the_same_creation_requirement_direct_closes(tmp_path):
    workspace, root = _repo_and_candidate(tmp_path)
    created = _created("Create hello.py.\n", workspace, root, _batch(workspace, root, {"hello.py": (False, False)}))
    assert created == ["hello.py"]
    reqs, contract, ledger = _setup("Create hello.py.\n")
    (entry,) = rc.close_requirements_by_file_creation(ledger, reqs, contract, root, created_paths=created, revision="terminal")
    assert entry["closed"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert not os.path.exists(os.path.join(workspace, "hello.py")), "evidence only: nothing was applied to decide it"


@pytest.mark.usefixtures("base_is_head")
@pytest.mark.parametrize("case", ["tracked_at_base", "untracked_before_the_run", "declared_modify", "a_delete", "another_path",
                                  "ignored_by_the_repository"])
def test_negative_controls_nothing_else_is_a_creation_at_the_terminal(tmp_path, case):
    tracked, untracked, files, candidate = {}, {}, {"hello.py": (False, False)}, {"hello.py": HELLO}
    if case == "tracked_at_base":
        tracked = {"hello.py": ""}                              # an EMPTY tracked file: same revision as "no bytes", still not absent
    elif case == "untracked_before_the_run":
        untracked = {"hello.py": "mine\n"}                      # the folder held the user's own file: the start-of-run revision says so
    elif case == "declared_modify":
        files = {"hello.py": (True, False)}                     # the batch does not declare a create: the commit would not enforce absence
    elif case == "a_delete":
        tracked, files, candidate = {"hello.py": "x\n"}, {"hello.py": (True, True)}, {}
    elif case == "another_path":
        files, candidate = {"hello2.py": (False, False)}, {"hello2.py": HELLO}
    elif case == "ignored_by_the_repository":
        tracked = {".gitignore": "hello.py\n"}                  # not a project member: the mutation record never attributes it
    workspace, root = _repo_and_candidate(tmp_path, tracked=tracked, untracked=untracked, candidate=candidate)
    if case == "a_delete":
        os.remove(os.path.join(root, "hello.py"))
    created = _created("Create hello.py.\n", workspace, root, _batch(workspace, root, files), paths=list(files))
    assert created is not None and "hello.py" not in created
    reqs, contract, ledger = _setup("Create hello.py.\n")
    (entry,) = rc.close_requirements_by_file_creation(ledger, reqs, contract, root, created_paths=created, revision="terminal")
    assert entry["closed"] is False and requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_no_authoritative_evidence_is_none_never_an_empty_answer(tmp_path, monkeypatch):
    workspace, root = _repo_and_candidate(tmp_path)
    writes = _batch(workspace, root, {"hello.py": (False, False)})
    monkeypatch.setattr(facade, "_run_committed_paths", lambda _workspace: ("r1", "0" * 40, []))   # the candidate is not at the run base
    assert _created("Create hello.py.\n", workspace, root, writes) is None
    monkeypatch.setattr(facade, "_run_committed_paths", lambda _workspace: (None, None, []))
    assert _created("Create hello.py.\n", workspace, root, None) is None                              # the batch could not be materialized
    assert _created("It prints hello world when executed.\n", workspace, root, writes) is None        # no creation is bound: nothing is read
    assert _created("Create hello.py.\n", workspace, root, writes) == ["hello.py"]                    # the control


# ---------------------------------------------------------------- RUNTIME_STDOUT at the enforce terminal gate

def _autonomy(enabled=True):
    autonomy = AppConfig().autonomy
    autonomy.run_verification_enabled = enabled
    autonomy.run_verification_timeout_seconds = 7
    return autonomy


@pytest.fixture
def scripted_validator(monkeypatch):
    """The candidate validator's runtime seam, scripted: records how it was built, the session it ran in and the argv."""
    class Validator:
        stack = "python"
        built = []
        script = staticmethod(lambda self, commands: {"success": True, "timed_out": False, "output": "hello world\n",
                                                      "steps": [_step(commands[-1])]})

        def __init__(self, root, **kwargs):
            self.root, self.kwargs, self.open_sessions, self.ran, self.tree_binding = root, kwargs, [], [], None
            type(self).built.append(self)

        @contextlib.contextmanager
        def verification_session(self, name):
            self.open_sessions.append(name)
            try:
                yield
            finally:
                self.open_sessions.remove(name)

        def run_app_sequence(self, commands, timeout=90, stdin_payload=None):
            self.ran.append({"commands": commands, "timeout": timeout, "sessions": list(self.open_sessions), "stdin": stdin_payload})
            return type(self).script(self, commands)

    monkeypatch.setattr(rc, "PolymorphicValidator", Validator)
    return Validator


def _terminal_outcomes(goal, workspace, root, *, written=("hello.py",), autonomy=None, tree_binding="bound"):
    _reqs, contract, _ledger = _setup(goal)
    return rc.terminal_runtime_stdout_outcomes(
        autonomy if autonomy is not None else _autonomy(), contract, root, workspace, written_files=written,
        toolchain_declaration_mutable=False, tree_binding=tree_binding)


def _judge(goal, root, outcomes, written=("hello.py",)):
    reqs, contract, ledger = _setup(goal)
    entries = rc.close_requirements_by_runtime_stdout(ledger, reqs, contract, root, gate_outcomes=outcomes, attempt="terminal",
                                                      written_files=written, revision="terminal")
    by_kind = {entry["requirement"]: entry for entry in entries}
    return by_kind, requirement_outcomes(ledger, reqs)


@pytest.mark.parametrize("goal", [UNQUOTED, QUOTED])
def test_the_sealed_statements_script_runs_once_on_the_final_candidate_and_the_shared_closer_closes_it(tmp_path, scripted_validator, goal):
    workspace, root = _repo_and_candidate(tmp_path)
    outcomes = _terminal_outcomes(goal, workspace, root)
    (validator,) = scripted_validator.built
    assert validator.root == root and validator.kwargs["original_workspace_path"] == workspace and validator.tree_binding == "bound"
    assert validator.kwargs["toolchain_declaration_mutable"] is False
    # exactly the sealed script through the project interpreter, inside the runtime-verification session, with the operator's timeout
    assert validator.ran == [{"commands": [[PYTHON, "hello.py"]], "timeout": 7, "sessions": [RUNTIME_VERIFICATION_GATE], "stdin": None}]
    (outcome,) = outcomes
    assert (outcome["attempt"], outcome["type"], outcome["success"], outcome["graded_by"]) == ("terminal", "run_verification", True, "process_exit")
    _entries, judged = _judge(goal, root, outcomes)
    assert judged["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert not os.path.exists(os.path.join(workspace, "hello.py")), "the candidate was verified where it is: nothing was applied"


def test_a_quoted_literal_the_final_candidate_does_not_print_is_contradicted_as_on_direct(tmp_path, scripted_validator):
    workspace, root = _repo_and_candidate(tmp_path)
    scripted_validator.script = staticmethod(lambda self, commands: {
        "success": True, "timed_out": False, "output": "bye\n", "steps": [_step(commands[-1], stdout="bye\n")]})
    _entries, judged = _judge(QUOTED, root, _terminal_outcomes(QUOTED, workspace, root))
    assert judged["REQ-2"] is RequirementOutcome.VIOLATED


@pytest.mark.parametrize("case", ["verification_disabled", "no_autonomy", "in_place_candidate", "no_binding", "two_created_files",
                                  "subject_not_in_the_candidate", "not_a_python_script", "not_a_python_project"])
def test_negative_controls_nothing_runs_without_a_sealed_subject_an_isolated_candidate_and_enabled_verification(tmp_path, scripted_validator, case):
    workspace, root = _repo_and_candidate(tmp_path, candidate={"hello.py": HELLO, "other.py": HELLO, "hello.txt": "x\n"})
    goal, autonomy, written, candidate_root = UNQUOTED, _autonomy(), ("hello.py", "other.py", "hello.txt"), root
    if case == "verification_disabled":
        autonomy = _autonomy(enabled=False)
    elif case == "no_autonomy":
        autonomy = None
    elif case == "in_place_candidate":
        candidate_root = workspace                               # never the real workspace
    elif case == "no_binding":
        goal = "Create hello.py.\n"
    elif case == "two_created_files":
        goal = "Create hello.py. Create other.py. It prints hello world when executed.\n"   # "It" has no single antecedent
    elif case == "subject_not_in_the_candidate":
        written = ("other.py",)
    elif case == "not_a_python_script":
        goal = "Create hello.txt. It prints hello world when executed.\n"
    elif case == "not_a_python_project":
        scripted_validator.stack = "java"
    _reqs, contract, _ledger = _setup(goal)
    outcomes = rc.terminal_runtime_stdout_outcomes(autonomy, contract, candidate_root, workspace, written_files=written,
                                                   toolchain_declaration_mutable=False)
    assert outcomes is None
    assert [validator.ran for validator in scripted_validator.built] in ([], [[]]), "nothing was executed"
    if case != "no_binding":
        _entries, judged = _judge(goal, root, outcomes, written)
        assert RequirementOutcome.CLOSED_BY_EVIDENCE not in {judged[rid] for rid in judged if rid != "REQ-1"}


def test_a_named_subject_is_the_script_that_runs_whatever_else_the_candidate_holds(tmp_path, scripted_validator):
    workspace, root = _repo_and_candidate(tmp_path, candidate={"hello.py": HELLO, "main.py": HELLO})
    goal = 'hello.py prints "hello world" when run.\n'
    outcomes = _terminal_outcomes(goal, workspace, root, written=("hello.py", "main.py"))
    assert scripted_validator.built[0].ran[0]["commands"] == [[PYTHON, "hello.py"]]
    reqs, contract, ledger = _setup(goal)
    rc.close_requirements_by_runtime_stdout(ledger, reqs, contract, root, gate_outcomes=outcomes, attempt="terminal",
                                            written_files=("hello.py", "main.py"), revision="terminal")
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_a_named_subject_wins_over_the_file_the_goal_creates(tmp_path, scripted_validator):
    """The statement's own words name the script; the single created file is only the antecedent of a statement that names none."""
    workspace, root = _repo_and_candidate(tmp_path, candidate={"hello.py": HELLO, "main.py": HELLO})
    _reqs, contract, _ledger = _setup('hello.py prints "hello world" when run. Create main.py.\n')
    assert rc.sealed_runtime_stdout_subject(contract) == "hello.py"
    _terminal_outcomes('hello.py prints "hello world" when run. Create main.py.\n', workspace, root, written=("hello.py", "main.py"))
    assert scripted_validator.built[0].ran[0]["commands"] == [[PYTHON, "hello.py"]]


@pytest.mark.parametrize("result", [
    {"success": False, "timed_out": False, "output": "Traceback", "steps": [_step([PYTHON, "hello.py"], stdout="", exit_code=1)]},
    {"success": False, "timed_out": True, "output": "", "steps": [dict(_step([PYTHON, "hello.py"], stdout="hello world\n"), timed_out=True)]},
    {},
], ids=["nonzero_exit", "timed_out", "no_result"])
def test_a_run_that_did_not_pass_is_no_observation_and_leaves_the_requirement_open(tmp_path, scripted_validator, result):
    workspace, root = _repo_and_candidate(tmp_path)
    scripted_validator.script = staticmethod(lambda self, commands: dict(result))
    outcomes = _terminal_outcomes(UNQUOTED, workspace, root)
    assert outcomes == []
    entries, judged = _judge(UNQUOTED, root, outcomes)
    assert entries["REQ-2"]["reason_code"] == NO_APPLICATION_RUN and judged["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_a_run_that_rewrote_the_candidate_proves_nothing_and_what_it_left_behind_is_removed(tmp_path, scripted_validator):
    workspace, root = _repo_and_candidate(tmp_path)

    def rewriting(self, commands):
        Path(root, "hello.py").write_text("print('hello world')  # rewritten by the run\n")
        Path(root, "state.json").write_text("{}")
        return {"success": True, "timed_out": False, "output": "hello world\n", "steps": [_step(commands[-1])]}
    scripted_validator.script = staticmethod(rewriting)
    outcomes = _terminal_outcomes(UNQUOTED, workspace, root)
    assert not os.path.exists(os.path.join(root, "state.json")), "the run's own leftovers never become part of the candidate"
    entries, judged = _judge(UNQUOTED, root, outcomes)
    assert entries["REQ-2"]["reason_code"] == CANDIDATE_CHANGED and judged["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_the_terminal_gate_hands_both_closers_their_evidence_and_no_longer_none():
    """The wiring (the end-to-end run below executes it): the two terminal thunks call the two evidence functions."""
    import inspect

    from kriya.workflow import terminal_gate_service

    source = inspect.getsource(terminal_gate_service.TerminalGateService)
    assert "created_paths=None" not in source and "gate_outcomes=None" not in source
    assert "created_paths=created_paths_from_terminal_batch(" in source and "gate_outcomes=terminal_runtime_stdout_outcomes(" in source


# ---------------------------------------------------------------- the whole path: the real enforce controller on the hello goal

def _hello_plan():
    from kriya.workflow.plan_schema import EngineeringPlan

    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": "create hello.py", "execution_method": "model",
                      "planned_files": [{"path": "hello.py", "action": "create"}], "provides": ["hello"],
                      "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [],
                      # a goal that states runtime behaviour needs a unit that owns the application run (plan validation)
                      "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"},
                                       {"type": "judgment", "verifier_kind": "application_runtime",
                                        "requires_runtime_execution": True, "description": "run hello.py"}]}]})


RUN_VERIFIER = ('{"should_run": true, "run_commands": [["python3", "hello.py"]], "success_criteria": "prints hello world", '
                '"input_channel": "none", "reasoning": "the goal states what it prints", "passed": true, "verdict": "PASS", "likely_files": []}')


def _hello_responder(content):
    def respond(role, request):
        from _chaos_harness import benign_roles
        from _protocol_responses import sentinel

        if role == "file_list":
            return '{"files": ["hello.py"]}'
        if role == "developer":
            return sentinel("hello.py", analysis="create it.", content=content)
        system = next((m.get("content") or "" for m in request.messages if m.get("role") == "system"), "")
        if SPEC_MARKER in (system.splitlines() or [""])[0]:
            # the requirement verifier's model judgment: evidence only (MODEL_CLAIMED); it closes nothing by itself
            return json.dumps({"compliant": True, "reasoning": "hello.py is there", "missing_requirements": [], "likely_files": [],
                               "requirement_verdicts": [{"id": rid, "verdict": "satisfied", "evidence": "hello.py"} for rid in ("REQ-1", "REQ-2")]})
        if role in ("run_verifier", "other"):     # the unit's runtime judge, and its grader (a role the harness does not name)
            return RUN_VERIFIER
        return benign_roles(role, request)
    return respond


def _enforce_hello(tmp_path, monkeypatch, printed):
    """The real enforce controller on the hello goal. The ONE scripted execution seam is the candidate validator's application run
    (``PolymorphicValidator.run_app_sequence``): the unit's own run and the terminal gate's run both go through it; every run is
    recorded with the folder it ran in."""
    from _chaos_harness import chaos_config
    from _t6_harness import enforce_run

    from kriya.control.persistence import list_run_records
    from kriya.tools.validate import PolymorphicValidator

    runs = []

    def run_app_sequence(self, commands, timeout=90, stdin_payload=None):
        runs.append((os.path.realpath(self.workspace_path), [list(command) for command in commands]))
        return {"success": True, "timed_out": False, "returncode": 0, "output": printed,
                "steps": [_step(commands[-1], stdout=printed)]}

    monkeypatch.setattr(PolymorphicValidator, "run_app_sequence", run_app_sequence)
    cfg = chaos_config(spec_compliance_enabled=True)      # the requirement gate (verifier and closers) runs only when it is enabled
    cfg.autonomy.run_verification_enabled = True
    observed = enforce_run(tmp_path, monkeypatch, _hello_responder(HELLO), {"README.md": "x\n"}, UNQUOTED, [_hello_plan], cfg=cfg)
    (record,) = list_run_records(str(observed.workspace))
    return observed, observed.result.legacy_result, record, runs


def test_enforce_hello_goal_ends_success_all_verified_like_direct(tmp_path, monkeypatch):
    observed, result, record, runs = _enforce_hello(tmp_path, monkeypatch, "hello world\n")
    assert result["requirement_truth"]["status"] == "ALL_VERIFIED", result.get("requirement_truth")
    assert result["status"] == "success" and result["quality_gates_passed"] is True
    assert (record.lifecycle_state.value, record.commit_result) == ("SUCCESS", "COMMITTED")
    assert (observed.workspace / "hello.py").read_text() == HELLO
    # the terminal gate's run: the sealed script, through the project interpreter, in a folder that is not the real workspace
    assert runs[-1][1] == [[PYTHON, "hello.py"]] and runs[-1][0] != os.path.realpath(observed.workspace)
    assert all(folder != os.path.realpath(observed.workspace) for folder, _commands in runs)


def test_enforce_hello_goal_whose_final_candidate_prints_something_else_stays_open_and_is_not_success(tmp_path, monkeypatch):
    """The negative control of the whole path: same run, the application prints another line. Unquoted literal: open, never success."""
    _observed, result, record, _runs = _enforce_hello(tmp_path, monkeypatch, "goodbye\n")
    assert result["requirement_truth"]["status"] == "OPEN_REQUIREMENTS"
    assert result["status"] != "success" and result["quality_gates_passed"] is False
    assert record.lifecycle_state.value != "SUCCESS"
