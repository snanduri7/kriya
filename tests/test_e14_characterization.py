"""E14 characterization (full modularization W4, slice E14 Part 1; written BEFORE any move, green at 8e0c90a).

Pins the decisions of every symbol E14 moves out of ``kriya/workflow/attempt.py`` and
``kriya/workflow/workflow_controller.py`` (handover/MODULARIZATION/E14-TRIAGE.md v3, PURE_EXTRACTION rows), each through
the name its callers and the suite's patches use today (``attempt.<name>`` / ``workflow_controller.<name>``), so the
same assertions hold before and after the move:

- the process-boundary test-candidate facts and the Java dependency-graph readers (OI-57);
- the attempt's known-target / omitted-member / authority-expansion recorders and the escalation write scope (OI-52);
- the candidate-gate seams: environment stop, test-verification declaration, the process-boundary obligation, the two
  process-boundary test checks, the workspace type index, the suite judgment, the test-delta integrity check and the
  developer-role wrapper (OI-54, OI-50);
- the staging pre-flight checks and attribution helpers, the D1 patch-only set and the Code Intelligence opener
  (OI-58, OI-50);
- the enforce run's subtask-verification verdict and contract-transition builder (OI-62);
- the call shape: every moved name stays a callable of the same kind and parameters on its module, and every Deps
  builder binds it by that name at call time, so a test patch keeps intercepting the moved code.

Decision-to-killer table: handover/MODULARIZATION/E14-DECISION-TO-KILLER.md."""
import asyncio
import functools
import inspect
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _strict_doubles import strict_config, strict_kernel

from kriya.analyzer.graph import DependencyGraph
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import attempt
from kriya.workflow import workflow_controller as controller
from kriya.workflow.failure import FileLocation, QualityGateFailure
from kriya.workflow.file_integrity import file_raw_digest
from kriya.workflow.file_resolution import build_grounded_java_launch_command
from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationStatus
from kriya.workflow.operations import CodeOperation
from kriya.workflow.plan_schema import ExecutionMethod, Subtask, VerificationMethod, VerificationMethodType
from kriya.workflow.repair_contract import RepairContractStatus
from kriya.workflow.run_events import EventAuthority
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow_types import SubtaskStatus

RUNTIME_INVALID = {"verifier_kind": "application_runtime", "requires_runtime_execution": True,
                   "description": "The app must exit with a non-zero exit code for invalid input."}
RUNTIME_BOTH = {"verifier_kind": "application_runtime", "requires_runtime_execution": True,
                "description": "Process exit code must be non-zero for missing argument and for malformed input."}
APP = ("package demo;\npublic class App {\n    public static void main(String[] args) {\n"
       "        System.exit(1);\n    }\n}\n")
APP_PATH = "src/main/java/demo/App.java"
TEST_PATH = "src/test/java/demo/AppTest.java"


def _state(*written, attempt_number=2):
    state = GenerationState()
    state.attempt_number = attempt_number
    state.all_files_written = set(written)
    return state


def _kernel(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir(exist_ok=True)
    return strict_kernel(strict_config(paths={"memory": str(memory)}))


def _ctx(tmp_path, **fields):
    worktree, workspace = tmp_path / "wt", tmp_path / "ws"
    worktree.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    base = dict(worktree_path=str(worktree), workspace_path=str(workspace), established_files=[],
                required_verification=[], structured_plan=None, current_subtask_id=None, kernel=_kernel(tmp_path))
    base.update(fields)
    return SimpleNamespace(**base)


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# =============================================================================================== process boundary (OI-57)

def test_the_initial_constraint_is_added_only_for_a_test_target_under_a_grounded_terminating_case():
    constraint = attempt._initial_test_process_boundary_constraint
    assert constraint([RUNTIME_INVALID], [TEST_PATH]) == attempt._INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT
    assert constraint([RUNTIME_INVALID], [APP_PATH]) == ""            # no runnable test target
    assert constraint([RUNTIME_INVALID], None) == ""
    assert constraint([{"description": "prints the sum"}], [TEST_PATH]) == ""   # no grounded case
    assert attempt._INITIAL_TEST_PROCESS_BOUNDARY_CONSTRAINT.startswith("\n\n=== Process-boundary test constraint ===\n")


@pytest.mark.parametrize("requirement,expected", [
    (RUNTIME_INVALID, ["invalid"]),
    (RUNTIME_BOTH, ["invalid", "missing"]),
    ({**RUNTIME_INVALID, "requires_runtime_execution": False}, []),            # both runtime facts are required
    ({**RUNTIME_INVALID, "requires_runtime_execution": "yes"}, []),            # `is True`, not truthiness
    ({**RUNTIME_INVALID, "verifier_kind": "unit_test"}, []),
    ({"process_boundary_authority": "relevant_global_invariant",
      "description": "must return a non-zero process exit for invalid input"}, ["invalid"]),
    ({"description": "must return a non-zero process exit for invalid input"}, []),   # neither authority
    ({**RUNTIME_INVALID, "description": "rejects invalid input with a message"}, []),  # no non-zero exit fact
    ({**RUNTIME_INVALID, "description": "process exit code is nonzero when no input is given"}, ["missing"]),
    ({**RUNTIME_INVALID, "description": "exit status is nonzero when no input is given"}, []),   # not a matched form
    ({**RUNTIME_INVALID, "description": "a non-zero exit for non-numeric input"}, ["invalid"]),
    ({**RUNTIME_INVALID, "description": "a non zero application process exit on invalid value"}, ["invalid"]),
])
def test_terminating_cases_come_only_from_an_authoritative_non_zero_exit_fact(requirement, expected):
    assert attempt._required_process_terminating_cases([requirement]) == expected


def test_terminating_cases_are_the_sorted_union_over_requirements():
    missing = {**RUNTIME_INVALID, "description": "non-zero exit for missing input"}
    assert attempt._required_process_terminating_cases([missing, RUNTIME_INVALID]) == ["invalid", "missing"]
    assert attempt._TERMINATING_CASE_MARKERS["invalid"][0] == "invalid input"


class _Verification:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, mode):
        assert mode == "json"
        return dict(self.payload)


def _plan(current="s2"):
    subtasks = {
        "s1": SimpleNamespace(verification=[_Verification({"description": "s1 check"})], relevant_global_invariant_ids=["g1"]),
        "s2": SimpleNamespace(verification=[_Verification({"description": "s2 check"}), _Verification(dict(RUNTIME_INVALID))],
                              relevant_global_invariant_ids=["g2"]),
    }
    return SimpleNamespace(
        subtasks=list(subtasks.values()), subtask_by_id=lambda sid: subtasks.get(sid),
        global_invariants=[SimpleNamespace(id="g1", statement="g1 holds"), SimpleNamespace(id="g2", statement="g2 holds")],
    ), current


def test_runtime_contract_requirements_are_deduplicated_in_first_seen_order(tmp_path):
    plan, current = _plan()
    ctx = _ctx(tmp_path, required_verification=[dict(RUNTIME_INVALID), {"description": "own"}],
               structured_plan=plan, current_subtask_id=current)
    assert attempt._runtime_contract_requirements(ctx) == [
        RUNTIME_INVALID, {"description": "own"}, {"description": "s1 check"}, {"description": "s2 check"},
        {"description": "g2 holds", "process_boundary_authority": "relevant_global_invariant", "invariant_id": "g2"},
    ]


def test_runtime_contract_requirements_without_a_plan_or_current_subtask(tmp_path):
    assert attempt._runtime_contract_requirements(_ctx(tmp_path, required_verification=[{"a": 1}, {"a": 1}])) == [{"a": 1}]
    plan, _ = _plan()
    ctx = _ctx(tmp_path, structured_plan=plan, current_subtask_id=None)
    assert attempt._runtime_contract_requirements(ctx) == [
        {"description": "s1 check"}, {"description": "s2 check"}, RUNTIME_INVALID]


METHODS = ("class AppTest {\n    void first() {\n        int x = 1;\n    }\n\n    @Test\n    public void second() {\n"
           "        if (x) {\n            y();\n        }\n        App.main(args);\n    }\n\n    void third() {\n    }\n}\n")


def test_the_enclosing_test_method_and_its_source_are_the_last_one_before_the_call():
    offset = METHODS.index("App.main")
    assert attempt._enclosing_test_method(METHODS, offset) == "second"
    assert attempt._enclosing_test_method("no method here", 3) is None
    assert attempt._enclosing_test_method_source(METHODS, offset) == (
        "    @Test\n    public void second() {\n        if (x) {\n            y();\n        }\n        App.main(args);\n    }"
        .replace("    @Test\n", ""))
    assert attempt._enclosing_test_method_source("App.main(x);", 2) == "App.main(x);"
    unbalanced = "    void m() {\n        App.main(a);\n"
    assert attempt._enclosing_test_method_source(unbalanced, unbalanced.index("App")) == unbalanced


def test_a_local_argv_variable_resolves_to_its_last_initializer():
    method = ('    void t() {\n        String[] value = {"1"};\n        String[] value = new String[] {"not-a-number"};\n'
              '        App.main(value);\n    }')
    assert attempt._resolve_java_main_argv_expression(method, " value ") == 'new String[] {"not-a-number"}'
    assert attempt._resolve_java_main_argv_expression(method, "other") == "other"
    assert attempt._resolve_java_main_argv_expression(method, 'new String[] {"x"}') == 'new String[] {"x"}'


@pytest.mark.parametrize("requirement,expected", [
    ({**RUNTIME_INVALID, "description": "reads an integer"}, True),
    ({**RUNTIME_INVALID, "description": "reads a number"}, True),
    ({**RUNTIME_INVALID, "description": "reads numeric text"}, True),
    ({**RUNTIME_INVALID, "description": "reads a word"}, False),
    ({"description": "reads an integer"}, False),
    ({"process_boundary_authority": "relevant_global_invariant", "description": "an integer"}, True),
])
def test_numeric_input_is_read_only_from_authoritative_runtime_text(requirement, expected):
    assert attempt._runtime_contract_expects_numeric_input([requirement]) is expected


@pytest.mark.parametrize("cases,argv,numeric,expected", [
    (["missing"], "new String[0]", False, "missing input"),
    (["missing"], "new String[] {}", False, "missing input"),
    (["missing"], "{ }", False, "missing input"),
    (["invalid"], "new String[0]", False, None),
    (["invalid"], '{"not-a-number"}', False, "invalid input"),
    (["invalid"], '{"MALFORMED"}', False, "invalid input"),
    (["invalid"], '{"abc"}', False, None),
    (["invalid"], '{"abc"}', True, "invalid input"),
    (["invalid"], '{"12"}', True, None),
    (["invalid"], '{"1.5"}', True, "invalid input"),
    (["missing"], '{"abc"}', True, None),
])
def test_the_terminating_case_is_classified_from_the_argv_expression_only(cases, argv, numeric, expected):
    assert attempt._terminating_case_from_argv_expression(cases, argv, numeric_input=numeric) == expected


def test_in_process_terminating_invocations_are_found_per_grounded_entrypoint(tmp_path):
    root = tmp_path / "wt"
    _write(root, "b/BTest.java", "class BTest {\n    void t() {\n        demo.App.main(new String[] {\"not-a-number\"});\n    }\n}\n")
    _write(root, "a/ATest.java", "class ATest {\n\n    public void neutral() {\n        String[] v = {\"invalid\"};\n"
                                 "        App.main(v);\n        App.main(new String[] {\"ok\"});\n    }\n}\n")
    found = attempt.find_in_process_terminating_test_invocations(
        [RUNTIME_INVALID], str(root), ["b/BTest.java", "missing/NoTest.java", "a/ATest.java"], ["demo.App"])
    assert found == [
        {"test_file": "a/ATest.java", "test_method": "neutral", "line": 5, "entrypoint_call": "App.main(v)",
         "terminating_case": "invalid input"},
        {"test_file": "b/BTest.java", "test_method": "t", "line": 3,
         "entrypoint_call": 'demo.App.main(new String[] {"not-a-number"})', "terminating_case": "invalid input"},
    ]
    assert attempt.find_in_process_terminating_test_invocations([RUNTIME_INVALID], str(root), ["a/ATest.java"], []) == []
    assert attempt.find_in_process_terminating_test_invocations([{"description": "x"}], str(root), ["a/ATest.java"],
                                                                ["demo.App"]) == []


CHILD_GOOD = ('class ChildTest {\n  void t() throws Exception {\n    Process p = new ProcessBuilder("java", "-cp", '
              '"target/classes", "demo.App").start();\n    p.waitFor();\n    p.getInputStream();\n  }\n}\n')


@pytest.mark.parametrize("known,classes_dir", [
    (["pom.xml"], "target/classes"),
    (["build.gradle"], "build/classes/java/main"),
    (["sub/build.gradle.kts"], "build/classes/java/main"),
    ([], ".kriya/runtime-verification/classes"),
])
def test_ungrounded_child_process_launches_name_every_missing_grounding(tmp_path, known, classes_dir):
    root = tmp_path / "wt"
    _write(root, "t/Good.java", CHILD_GOOD.replace("target/classes", classes_dir))
    _write(root, "t/Bad.java", 'class Bad {\n  void t() throws Exception {\n    new ProcessBuilder("java", '
                               'System.getProperty("java.class.path"), "App").start();\n  }\n}\n')
    _write(root, "t/NoStart.java", "class NoStart { ProcessBuilder b; }\n")
    _write(root, "t/InProcess.java", "class InProcess { void t() { App.main(null); } }\n")
    found = attempt.find_ungrounded_java_child_process_tests(
        [RUNTIME_INVALID], str(root), ["t/NoStart.java", "t/InProcess.java", "t/Good.java", "t/Bad.java"],
        {APP_PATH: "demo.App"}, known)
    expected_reasons = ["reuses the test-runner/Surefire classpath",
                        f"does not use grounded application classpath {classes_dir}",
                        "does not capture the child exit code", "does not capture child stdout/stderr"]
    assert found == [{"test_file": "t/Bad.java", "reasons": expected_reasons, "entrypoint": "demo.App",
                      "classes_dir": classes_dir,
                      "expected_command": build_grounded_java_launch_command("demo.App", ["<args>"], classes_dir)}]


def test_a_child_launch_must_name_the_grounded_main_class(tmp_path):
    root = tmp_path / "wt"
    _write(root, "t/Other.java", CHILD_GOOD.replace('"demo.App"', '"other.Main"'))
    _write(root, "t/Fqcn.java", CHILD_GOOD)
    found = attempt.find_ungrounded_java_child_process_tests(
        [RUNTIME_INVALID], str(root), ["t/Other.java", "t/Fqcn.java"], {APP_PATH: "demo.App"}, ["pom.xml"])
    assert [(item["test_file"], item["reasons"]) for item in found] == [
        ("t/Other.java", ["does not launch grounded main class demo.App"])]


def test_child_process_checks_need_one_grounded_main_class_and_a_terminating_case(tmp_path):
    root = tmp_path / "wt"
    _write(root, "t/Bad.java", 'class Bad { void t() { new ProcessBuilder("x").start(); } }\n')
    check = attempt.find_ungrounded_java_child_process_tests
    assert check([RUNTIME_INVALID], str(root), ["t/Bad.java"], {}, []) == []
    assert check([RUNTIME_INVALID], str(root), ["t/Bad.java"], {"a": "demo.A", "b": "demo.B"}, []) == []
    assert check([{"description": "x"}], str(root), ["t/Bad.java"], {APP_PATH: "demo.App"}, []) == []
    assert [item["test_file"] for item in check([RUNTIME_INVALID], str(root), ["t/Bad.java"],
                                                 {APP_PATH: "demo.App"}, [])] == ["t/Bad.java"]


def test_the_dependency_graph_readers_query_a_short_lived_graph_and_degrade_on_attempts_logger(tmp_path, caplog):
    ctx = _ctx(tmp_path)
    db_path = attempt._dependency_graph_db_path(ctx)
    assert db_path == os.path.join(ctx.kernel.config.paths.memory, "dependency_graph.db")
    assert attempt._extract_class_names_best_effort(db_path, APP_PATH, APP) == [".java:App"]
    assert attempt._find_java_main_class_best_effort(db_path, APP_PATH, APP) == "demo.App"
    (tmp_path / "plain-file").write_text("not a directory")
    broken = str(tmp_path / "plain-file" / "dependency_graph.db")          # the graph cannot be opened
    with caplog.at_level(logging.DEBUG, logger="kriya.workflow.attempt"):
        assert attempt._extract_class_names_best_effort(broken, APP_PATH, APP) == []
        assert attempt._find_java_main_class_best_effort(broken, APP_PATH, APP) is None
    messages = [(record.name, record.getMessage().split(":")[0]) for record in caplog.records]
    assert ("kriya.workflow.attempt", f"Skipping duplicate-type check for {APP_PATH}") in messages
    assert ("kriya.workflow.attempt", f"Skipping Java entrypoint detection for {APP_PATH}") in messages


def test_the_java_main_class_map_reads_the_worktree_first_then_the_workspace(tmp_path):
    ctx = _ctx(tmp_path)
    no_main = "package demo;\npublic class Lib {}\n"
    _write(ctx.worktree_path, APP_PATH, APP)
    _write(ctx.workspace_path, APP_PATH, no_main)                       # shadowed by the worktree copy
    _write(ctx.worktree_path, "src/main/java/demo/Lib.java", no_main)
    _write(ctx.workspace_path, "src/main/java/demo/Tool.java", APP.replace("App", "Tool"))   # workspace only
    _write(ctx.worktree_path, "src/main/java/demo/Shadow.java", no_main.replace("Lib", "Shadow"))
    _write(ctx.workspace_path, "src/main/java/demo/Shadow.java", APP.replace("App", "Shadow"))
    files = [APP_PATH, "src/main/java/demo/Lib.java", "src/main/java/demo/Tool.java", "src/main/java/demo/Shadow.java",
             "src/main/java/demo/Missing.java"]
    assert attempt._build_java_main_class_map(files, ctx) == {
        APP_PATH: "demo.App", "src/main/java/demo/Tool.java": "demo.Tool"}


# =============================================================================================== repair recorders (OI-52)

def test_omitted_members_record_complete_boundaries_per_path_and_replace_the_record():
    state = _state()
    state.known_target_omitted_members = {"old.py": [(1, 2, "r0")]}
    attempt._record_omitted_members(state, [
        {"path": "a.py", "member_id": "m1", "revision": "r1", "start_line": 3, "end_line": 9},
        {"path": "a.py", "member_id": "m1", "revision": "r1", "start_line": "3", "end_line": "9"},   # duplicate
        {"path": "a.py", "member_id": "m2", "revision": "r1", "start_line": 12, "end_line": 20},
        {"path": "b.py", "member_id": None, "revision": "r1", "start_line": 1, "end_line": 2},
        {"path": "b.py", "member_id": "m3", "revision": "", "start_line": 1, "end_line": 2},
        {"path": "b.py", "member_id": "m3", "revision": "r2", "start_line": None, "end_line": 2},
        {"path": "c.py", "member_id": "m4", "revision": 7, "start_line": 0, "end_line": 1},
    ])
    assert state.known_target_omitted_members == {"a.py": [(3, 9, "r1"), (12, 20, "r1")], "c.py": [(0, 1, "7")]}


def _item(path, tier, member_id=None, label=""):
    return SimpleNamespace(path=path, tier=tier, member_id=member_id, label=label)


def test_a_known_target_package_records_the_highest_ranked_member_or_the_last_item():
    state = _state()
    state.known_target_member_items = {"plain.py": ["stale"]}
    m1, m2 = _item("a.py", "member_exact", "m1"), _item("a.py", "member_exact", "m2")
    skeleton = _item("a.py", "skeleton")
    no_id = _item("plain.py", "member_exact", None, "no id")
    full = _item("plain.py", "full")
    first, last = _item("p.py", "skeleton", label="first"), _item("p.py", "full", label="last")
    attempt._record_known_target_package(state, [skeleton, m1, no_id, m2, full, first, last])
    assert state.known_target_context_items == {"a.py": m1, "plain.py": full, "p.py": last}
    assert state.known_target_member_items == {"a.py": [m1, m2]}


@pytest.mark.parametrize("mode,allowed,expected", [
    (WriteScopeMode.DENY_ALL, ["a.py"], ()),
    (WriteScopeMode.ALLOWLIST, ["c.py", "a.py"], ("a.py", "c.py")),
    (WriteScopeMode.UNRESTRICTED, [], ("a.py", "b.py", "c.py")),
])
def test_the_escalation_write_scope_is_never_widened(mode, allowed, expected):
    ctx = SimpleNamespace(write_scope_mode=mode, allowed_write_relpaths=allowed)
    assert attempt._escalation_authorized_paths(ctx, ["a.py", "b.py", "a.py", "c.py"]) == expected
    assert attempt._escalation_authorized_paths(ctx, None) == ()


class _Expansion:
    outcome, path, member_id, reason_code, source_origin = "granted", "a.py", "A.f", "MEMBER_HINT", "goal"

    def to_dict(self):
        return {"outcome": self.outcome, "path": self.path}


def test_authority_expansions_are_kept_and_recorded_as_advisory_events():
    state = _state(attempt_number=3)
    expansions = [_Expansion(), _Expansion()]
    attempt._record_authority_expansions(state, expansions)
    assert state.authority_expansions == expansions
    assert [(e.kind, e.attempt, e.source, e.authority, e.message, e.details) for e in state.run_events] == [
        ("authority.expansion", 3, "authority_escalation", EventAuthority.ADVISORY,
         "Member authority granted for a.py::A.f (MEMBER_HINT, origin goal).", {"outcome": "granted", "path": "a.py"}),
    ] * 2


# =============================================================================================== gate seams (OI-54, OI-50)

def test_an_environment_reason_code_is_a_typed_infrastructure_stop():
    state = _state()
    assert attempt._stop_on_environment_gate_result(state, {"success": False, "output": "boom"}, "compile") is None
    assert attempt._stop_on_environment_gate_result(state, "not a dict", "compile") is None
    assert state.gate_outcomes == []
    result = {"environment_reason_code": "GRADLE_WRAPPER_UNAVAILABLE", "output": "no wrapper",
              "gradle_distribution": "8.7"}
    with pytest.raises(QualityGateFailure) as raised:
        attempt._stop_on_environment_gate_result(state, result, "test")
    failure = raised.value.failure
    assert (failure.type, failure.source, failure.raw_output, failure.attempt) == (
        "verification_infrastructure_failure", "orchestrator", "no wrapper", 2)
    assert failure.message == ("VERIFICATION_INFRASTRUCTURE_FAILURE: the test gate's verification tool could not start "
                               "(GRADLE_WRAPPER_UNAVAILABLE); nothing about the candidate was observed, so this is not a "
                               "code failure.\nno wrapper")
    assert failure.diagnostics == {"reason_code": "GRADLE_WRAPPER_UNAVAILABLE", "gate": "test", "gradle_distribution": "8.7"}
    assert state.gate_outcomes == [failure.to_gate_outcome()]


@pytest.mark.parametrize("verification,expected", [
    ([{"type": "tool", "tool_name": "test"}], True),
    ([{"type": "tool", "tool_name": "tests"}], True),
    ([{"type": "tool", "tool_name": "regression"}], True),
    ([{"type": "tool", "tool_name": "compile"}], False),
    ([{"type": "judgment", "tool_name": "test"}], False),
    ([], False),
    (None, False),
])
def test_a_test_verifier_is_a_declared_tool_named_test_tests_or_regression(verification, expected):
    assert attempt._declares_test_verification(verification) is expected


def _obligation_ctx(tmp_path, subtask="s1", ledger=True):
    return _ctx(tmp_path, obligation_ledger=ObligationLedger() if ledger else None, current_subtask_id=subtask)


def test_process_boundary_obligations_track_recurrence_per_subtask(tmp_path):
    assert attempt._process_boundary_obligation_id("s1") == "attempt.subtask.s1.process_boundary_compatibility"
    ctx, state = _obligation_ctx(tmp_path), _state(attempt_number=2)
    record = attempt._record_process_boundary_obligation
    synced = []
    with patch("kriya.workflow.attempt._sync_active_repair_contract",
               side_effect=lambda c, s, r: synced.append((c, s, r.id, r.status))):
        assert record(ctx, state, violated=False, evidence={"x": 1}) is False          # never violated: no record
        assert ctx.obligation_ledger.current("attempt.subtask.s1.process_boundary_compatibility") is None
        assert record(ctx, state, violated=True, evidence={"raw_output": "exit"}) is False
        state.attempt_number = 3
        assert record(ctx, state, violated=True, evidence={"raw_output": "exit"}) is True
    oid = "attempt.subtask.s1.process_boundary_compatibility"
    current = ctx.obligation_ledger.current(oid)
    assert (current.kind, current.status, current.revision, current.owner_subtask_id, current.terminal_required,
            current.source) == (ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY, ObligationStatus.VIOLATED, 3, "s1", False,
                                "attempt.run_attempt")
    assert current.evidence == {"raw_output": "exit", "recurrence": True, "current_attempt": 3, "prior_violation_attempt": 2}
    assert synced == [(ctx, state, oid, ObligationStatus.VIOLATED)] * 2


def test_a_satisfied_check_clears_only_a_violated_obligation_and_closes_its_contract(tmp_path):
    ctx, state = _obligation_ctx(tmp_path), _state(attempt_number=4)
    oid = "attempt.subtask.s1.process_boundary_compatibility"
    with patch("kriya.workflow.attempt._sync_active_repair_contract") as sync:
        attempt._record_process_boundary_obligation(ctx, state, violated=True, evidence={})
        assert ctx.obligation_ledger.current(oid).evidence == {"recurrence": False, "current_attempt": 4}
        state.repair_contract = SimpleNamespace(id="rc-1", status=RepairContractStatus.ACTIVE, source_obligation_ids=[oid])
        state.attempt_number = 5
        assert attempt._record_process_boundary_obligation(ctx, state, violated=False, evidence={"ok": 1}) is False
        assert sync.call_count == 1
    current = ctx.obligation_ledger.current(oid)
    assert (current.status, current.evidence, current.revision) == (ObligationStatus.SATISFIED, {"ok": 1}, 5)
    assert state.repair_contract.status == RepairContractStatus.SATISFIED
    assert [(e.kind, e.attempt, e.authority, e.details) for e in state.run_events] == [
        ("repair_contract_satisfied", 5, EventAuthority.ADVISORY, {"repair_contract_id": "rc-1", "obligation_id": oid})]
    # a violated -> satisfied -> violated sequence is a fresh, non-recurrent violation
    with patch("kriya.workflow.attempt._sync_active_repair_contract"):
        assert attempt._record_process_boundary_obligation(ctx, state, violated=True, evidence={}) is False


def test_an_unrelated_repair_contract_is_not_closed_and_no_subtask_means_no_record(tmp_path):
    ctx, state = _obligation_ctx(tmp_path), _state()
    with patch("kriya.workflow.attempt._sync_active_repair_contract"):
        attempt._record_process_boundary_obligation(ctx, state, violated=True, evidence={})
    state.repair_contract = SimpleNamespace(id="rc-2", status=RepairContractStatus.ACTIVE, source_obligation_ids=["other"])
    attempt._record_process_boundary_obligation(ctx, state, violated=False, evidence={})
    assert state.repair_contract.status == RepairContractStatus.ACTIVE and state.run_events == []
    for no_owner in (_obligation_ctx(tmp_path, subtask=None), _obligation_ctx(tmp_path, ledger=False)):
        with patch("kriya.workflow.attempt._sync_active_repair_contract") as sync:
            assert attempt._record_process_boundary_obligation(no_owner, state, violated=True, evidence={}) is False
            assert sync.call_count == 0


def _boundary_ctx(tmp_path, **fields):
    ctx = _ctx(tmp_path, required_verification=[RUNTIME_INVALID], established_files=[APP_PATH], **fields)
    _write(ctx.worktree_path, APP_PATH, APP)
    return ctx


def test_an_in_process_terminating_test_is_a_typed_test_only_failure(tmp_path):
    ctx = _boundary_ctx(tmp_path)
    _write(ctx.worktree_path, TEST_PATH, "class AppTest {\n    @Test\n    void rejects() {\n"
                                         "        App.main(new String[] {\"not-a-number\"});\n    }\n}\n")
    state = _state(TEST_PATH)
    with pytest.raises(QualityGateFailure) as raised:
        attempt._raise_unsafe_process_boundary_test_candidate(state, ctx, [TEST_PATH])
    failure = raised.value.failure
    finding = {"test_file": TEST_PATH, "test_method": "rejects", "line": 4,
               "entrypoint_call": 'App.main(new String[] {"not-a-number"})', "terminating_case": "invalid input"}
    assert failure.type == "process_terminating_behavior_tested_in_process"
    assert failure.diagnostics == {"reason_code": "PROCESS_TERMINATING_BEHAVIOR_TESTED_IN_PROCESS",
                                   "repair_owner": "test", "findings": [finding]}
    assert failure.file_locations == [FileLocation(filepath=TEST_PATH, line=4)]
    assert failure.likely_files == [TEST_PATH] and failure.attempt == 2 and failure.raw_output == failure.message
    assert set(failure.failed_content) == {TEST_PATH}
    assert (f"- {TEST_PATH}:4 method=rejects invokes App.main(new String[] {{\"not-a-number\"}}) for invalid input"
            in failure.message.splitlines())
    assert failure.message.startswith("PROCESS_TERMINATING_BEHAVIOR_TESTED_IN_PROCESS: generated test code invokes")
    assert state.gate_outcomes == [failure.to_gate_outcome()]


def test_a_safe_test_candidate_passes_the_process_boundary_checks(tmp_path):
    ctx = _boundary_ctx(tmp_path)
    _write(ctx.worktree_path, TEST_PATH, "class AppTest { void ok() { App.main(new String[] {\"7\"}); } }\n")
    state = _state(TEST_PATH)
    assert attempt._raise_unsafe_process_boundary_test_candidate(state, ctx, [TEST_PATH]) is None
    assert attempt._raise_ungrounded_child_process_test_candidate(state, ctx, [TEST_PATH]) is None
    assert state.gate_outcomes == []


def test_an_ungrounded_child_launch_is_a_typed_verification_infrastructure_failure(tmp_path):
    ctx = _boundary_ctx(tmp_path)
    _write(ctx.worktree_path, TEST_PATH, 'class AppTest { void t() { new ProcessBuilder("java", "Other").start(); } }\n')
    state = _state(TEST_PATH)
    with pytest.raises(QualityGateFailure) as raised:
        attempt._raise_ungrounded_child_process_test_candidate(state, ctx, [TEST_PATH])
    failure = raised.value.failure
    assert failure.type == "test_verification_infrastructure_failure"
    assert failure.diagnostics == {"reason_code": "UNGROUNDED_CHILD_PROCESS_LAUNCH", "grounded_entrypoint": "demo.App",
                                   "grounded_classpath": ".kriya/runtime-verification/classes"}
    assert failure.likely_files == [TEST_PATH] and failure.attempt == 2 and failure.raw_output == failure.message
    assert (f"- {TEST_PATH}: does not launch grounded main class demo.App; does not use grounded application classpath "
            ".kriya/runtime-verification/classes; does not capture the child exit code; does not capture child "
            "stdout/stderr") in failure.message.splitlines()
    assert "Use main class demo.App with classpath .kriya/runtime-verification/classes," in failure.message
    assert state.gate_outcomes == [failure.to_gate_outcome()]


def test_the_workspace_type_index_merges_the_persisted_baseline_with_every_known_file(tmp_path):
    ctx = _ctx(tmp_path, established_files=["src/main/java/demo/Est.java", "src/main/java/demo/Gone.java"])
    graph = DependencyGraph(attempt._dependency_graph_db_path(ctx))
    try:
        graph.index_file("src/main/java/demo/Old.java", "package demo;\npublic class Old {}\n", 0.0)
        graph.index_file("src/main/java/demo/App.java", APP, 0.0)
    finally:
        graph.close()
    _write(ctx.worktree_path, APP_PATH, APP)
    _write(ctx.workspace_path, "src/main/java/demo/Est.java", "package demo;\npublic class Est {}\nclass Old {}\n")
    state = _state(APP_PATH, "src/main/java/demo/Absent.java")
    assert attempt._build_workspace_type_index(state, ctx) == {
        ".java:Old": ["src/main/java/demo/Old.java", "src/main/java/demo/Est.java"],
        ".java:App": [APP_PATH], ".java:Est": ["src/main/java/demo/Est.java"]}


def test_the_workspace_type_index_degrades_to_empty_on_attempts_logger(tmp_path, caplog):
    ctx = _ctx(tmp_path)
    (tmp_path / "plain-file").write_text("not a directory")
    ctx.kernel.config.paths.memory = str(tmp_path / "plain-file")          # the graph cannot be opened
    with caplog.at_level(logging.DEBUG, logger="kriya.workflow.attempt"):
        assert attempt._build_workspace_type_index(_state(), ctx) == {}
    assert [(r.name, r.getMessage().split(":")[0]) for r in caplog.records] == [
        ("kriya.workflow.attempt", "_build_workspace_type_index")]


class _Attribution:
    blocking = True

    def __init__(self):
        self.calls = []

    def evidence_text(self, baseline, output, details):
        self.calls.append((baseline, output, details))
        return "attributed"

    def gate_evidence(self):
        return {"blocking": self.blocking}


def test_the_suite_judgment_is_the_raw_rule_without_a_baseline_and_the_attribution_with_one(tmp_path):
    state, ctx = _state(), _ctx(tmp_path)
    with patch("kriya.workflow.attempt._attribute_candidate_suite", return_value=None) as attribute:
        assert attempt._judge_candidate_suite(state, ctx, {"success": False, "output": "1 failed"}, "scope-x") == (
            True, "1 failed", {})
        assert attempt._judge_candidate_suite(state, ctx, {"success": True}, "scope-x") == (False, "", {})
    assert attribute.call_args_list[0].args == (state, ctx, {"success": False, "output": "1 failed"}, "scope-x")
    attribution = _Attribution()
    state.validation_baseline_full_regression = "baseline"
    result = {"success": True, "output": "out", "test_execution": {"failures": [{"id": "t1"}]}}
    with patch("kriya.workflow.attempt._attribute_candidate_suite", return_value=attribution):
        assert attempt._judge_candidate_suite(state, ctx, result, "scope-y") == (
            True, "attributed", {"regression_attribution": {"blocking": True}})
    from kriya.tools import test_execution
    assert attribution.calls == [("baseline", "out", test_execution.failure_details(result))]


def test_the_developer_wrapper_attributes_model_calls_to_the_developer_role():
    from kriya.core.role_metrics import current_model_role

    async def probe():
        return current_model_role()

    assert asyncio.run(attempt._as_developer(probe())) == "developer"


PY_BASE = "def test_existing():\n    assert True\n"


class _Validator:
    def __init__(self, result, stack="java"):
        self.stack, self.result, self.calls = stack, result, []

    def run_tests(self, target_test=None):
        self.calls.append(target_test)
        return self.result


def _delta_case(tmp_path, covering, stack="java"):
    """A changed test source whose accepted invocation carried no structured report: one covering run decides."""
    ctx = _ctx(tmp_path)
    _write(ctx.workspace_path, "tests/test_calc.py", PY_BASE)
    _write(ctx.worktree_path, "tests/test_calc.py", PY_BASE + "\n\ndef test_new():\n    assert True\n")
    return _state("tests/test_calc.py"), ctx, _Validator(covering, stack)


def _report_result(identities, success=True):
    from kriya.tools import test_execution
    from kriya.tools.test_execution import COMPLETE, TestCaseResult, TestExecutionReport

    report = TestExecutionReport(gate_id="g1", runner="pytest", workspace="/w", completeness=COMPLETE, reason=None,
                                 cases=[TestCaseResult(i, i.rsplit(".", 1)[0], i.rsplit(".", 1)[1], "passed")
                                        for i in identities])
    assert test_execution.report_from_result({"test_execution": report.to_dict()}) is not None
    return {"success": success, "output": "suite output", "test_execution": report.to_dict()}


def test_a_covering_run_whose_tool_cannot_start_is_an_environment_stop(tmp_path):
    state, ctx, validator = _delta_case(tmp_path, {"success": False, "output": "no wrapper",
                                                   "environment_reason_code": "GRADLE_WRAPPER_UNAVAILABLE"})
    with pytest.raises(QualityGateFailure) as raised:
        attempt._raise_unexecuted_test_delta(state, ctx, validator, {"success": True, "output": ""}, None)
    assert validator.calls == [None]                                  # the java covering run is the whole suite
    failure = raised.value.failure
    assert failure.type == "verification_infrastructure_failure"
    assert failure.diagnostics == {"reason_code": "GRADLE_WRAPPER_UNAVAILABLE", "gate": "test", "gradle_distribution": None}


def test_a_full_suite_covering_run_is_judged_in_its_own_scope_and_its_attribution_is_kept(tmp_path):
    from kriya.workflow.suite_attribution import SCOPE_TEST_DELTA_COVERING_RUN

    passing = _report_result(["tests.test_calc.test_existing", "tests.test_calc.test_new"])
    state, ctx, validator = _delta_case(tmp_path, passing)
    with patch("kriya.workflow.attempt._judge_candidate_suite", return_value=(False, "", {})) as judge:
        assert attempt._raise_unexecuted_test_delta(state, ctx, validator, {"success": True, "output": ""}, None) is None
    assert judge.call_args.args == (state, ctx, passing, SCOPE_TEST_DELTA_COVERING_RUN)
    assert [o["type"] for o in state.gate_outcomes] == ["test_delta"] and state.gate_outcomes[0]["success"] is True
    (tmp_path / "blocking").mkdir()
    state, ctx, validator = _delta_case(tmp_path / "blocking", passing)
    with patch("kriya.workflow.attempt._judge_candidate_suite",
               return_value=(True, "attributed evidence", {"regression_attribution": {"blocking": True}})), \
            pytest.raises(QualityGateFailure) as raised:
        attempt._raise_unexecuted_test_delta(state, ctx, validator, {"success": True, "output": ""}, None)
    assert raised.value.failure.diagnostics["regression_attribution"] == {"blocking": True}
    assert state.gate_outcomes[-1]["regression_attribution"] == {"blocking": True}
    assert "attributed evidence" in raised.value.failure.message


# =============================================================================================== staging helpers (OI-58, OI-50)

def test_a_baseline_restoration_writes_the_exact_original_bytes_over_the_current_revision(tmp_path):
    target = _write(tmp_path, "a.txt", "current\n")
    state = _state()
    state.all_original_raw["a.txt"] = b"one\r\ntwo\r\n"
    state.all_original_contents["a.txt"] = "one\ntwo\n"
    write = attempt._baseline_restoration_write(state, str(target), "a.txt")
    assert (write.target_path, write.base_path, write.content_bytes, write.expected_base_revision) == (
        str(target), str(target), b"one\r\ntwo\r\n", file_raw_digest(str(target)))
    state.all_original_raw["a.txt"] = None
    assert attempt._baseline_restoration_write(state, str(target), "a.txt").content_bytes == b"one\ntwo\n"


def test_a_file_integrity_stop_is_a_deterministic_typed_failure():
    state = _state(attempt_number=3)
    failure = attempt._file_integrity_stop(state, "a.txt", ValueError("mixed endings")).failure
    assert (failure.type, failure.message, failure.raw_output, failure.source, failure.authority, failure.attempt) == (
        "file_integrity_unsupported", "mixed endings", "mixed endings", "orchestrator", "deterministic", 3)
    assert failure.file_locations == [FileLocation(filepath="a.txt")] and failure.likely_files == ["a.txt"]
    assert failure.diagnostics == {"reason_code": None}
    coded = ValueError("symlink")
    coded.reason_code = "SYMLINK"
    assert attempt._file_integrity_stop(state, "b.txt", coded).failure.diagnostics == {"reason_code": "SYMLINK"}
    assert len(state.gate_outcomes) == 2 and state.gate_outcomes[0] == failure.to_gate_outcome()


def test_a_target_exists_in_the_worktree_or_the_workspace(tmp_path):
    ctx = _ctx(tmp_path)
    _write(ctx.worktree_path, "w.py", "")
    _write(ctx.workspace_path, "s.py", "")
    assert [attempt._target_exists(ctx, p) for p in ("w.py", "s.py", "n.py")] == [True, True, False]


def _locator_state(mode="targeted", tier="locator", authority="authoritative"):
    state = _state()
    state.last_attempt_mode = mode
    state.last_attribution = SimpleNamespace(tier=tier, files=["a.py", "b.py", "z.py"])
    state.last_failure = SimpleNamespace(authority=authority)
    return state


@pytest.mark.parametrize("state,self_diagnosed,expected", [
    (_locator_state(), [], ["a.py", "b.py"]),
    (_locator_state("fallback_targeted"), [], ["a.py", "b.py"]),
    (_locator_state(), ["c.py"], []),
    (_locator_state("full_set"), [], []),
    (_locator_state(tier="exact"), [], []),
    (_locator_state(authority="advisory"), [], []),
])
def test_an_authoritative_locator_survives_only_without_an_alternate_diagnosis(state, self_diagnosed, expected):
    assert attempt._preserved_authoritative_locator_files(state, ["b.py", "a.py", "c.py"], self_diagnosed) == expected


def test_no_locator_means_nothing_is_preserved():
    state = _locator_state()
    state.last_attribution = None
    assert attempt._preserved_authoritative_locator_files(state, ["a.py"], []) == []
    state = _locator_state()
    state.last_failure = None
    assert attempt._preserved_authoritative_locator_files(state, ["a.py"], []) == []


@pytest.mark.parametrize("preserved,mode,available,attempted,expected", [
    (["a.py"], "targeted", True, False, True),
    ([], "targeted", True, False, False),
    (["a.py"], "fallback_targeted", True, False, False),
    (["a.py"], "targeted", False, False, False),
    (["a.py"], "targeted", True, True, False),
])
def test_a_rejected_authoritative_target_requests_one_fallback_targeted_retry(tmp_path, preserved, mode, available,
                                                                             attempted, expected):
    state, ctx = _state(), _ctx(tmp_path)
    state.last_attempt_mode = mode
    state.budgets.fallback_targeted_attempted = attempted
    with patch("kriya.workflow.attempt._fallback_routing", return_value=SimpleNamespace(available=available)) as routing:
        attempt._request_fallback_for_rejected_authoritative_target(state, ctx, preserved)
    assert state.budgets.fallback_targeted_requested is expected
    if preserved and mode == "targeted":
        assert routing.call_args.args == (state, ctx)


def test_explanatory_prose_is_judged_only_on_what_the_candidate_added():
    state = _state()
    prose = "def f():\n    return 1\nThe fix is to count.\n"
    assert attempt._reject_explanatory_prose(state, "pkg/a.py", prose, prose) is None
    assert attempt._reject_explanatory_prose(state, "pkg/a.py", "def f():\n    return 1\n", None) is None
    with pytest.raises(QualityGateFailure) as raised:
        attempt._reject_explanatory_prose(state, "pkg/a.py", prose, "def f():\n    return 1\n")
    failure = raised.value.failure
    assert failure.type == "prose_contamination" and failure.raw_output
    assert failure.message.startswith("SOURCE PROSE CONTAMINATION in pkg/a.py: obvious explanatory text")
    assert (failure.likely_files, failure.failed_content, failure.attempt) == (["pkg/a.py"], {"pkg/a.py": prose}, 2)
    assert failure.file_locations == [FileLocation(filepath="pkg/a.py")]
    assert state.gate_outcomes == [failure.to_gate_outcome()]


JAVA = "src/main/java/demo/Parser.java"
JAVA_CLEAN = ("package demo;\n\npublic class Parser {\n    public static String compile(String path) {\n        try {\n"
              "            return read(path);\n        } catch (Exception e) {\n            throw new IllegalStateException(e);\n"
              "        }\n    }\n\n    static String read(String p) {\n        return p;\n    }\n}\n")
JAVA_MASKED = JAVA_CLEAN.replace("catch (Exception e) {\n            throw new IllegalStateException(e);",
                                 "catch (Throwable e) {\n            return null;")


def test_exception_masking_is_judged_against_the_pristine_run_start_content():
    state = _state()
    assert attempt._reject_exception_masking(state, JAVA, JAVA_CLEAN, None) is None
    assert attempt._reject_exception_masking(state, JAVA, JAVA_MASKED, JAVA_MASKED) is None   # no pristine: baseline
    state.all_original_contents[JAVA] = JAVA_CLEAN            # an earlier attempt introduced the clause
    with pytest.raises(QualityGateFailure) as raised:
        attempt._reject_exception_masking(state, JAVA, JAVA_MASKED, JAVA_MASKED)
    failure = raised.value.failure
    assert failure.type == "unsafe_exception_masking"
    assert failure.message.startswith(f"UNSAFE EXCEPTION MASKING in {JAVA}: UNSAFE_EXCEPTION_MASKING: line 7 ")
    assert failure.message.endswith("do not widen a catch to Error/Throwable.")
    assert (failure.likely_files, failure.failed_content, failure.attempt) == ([JAVA], {JAVA: JAVA_MASKED}, 2)
    assert failure.raw_output == failure.message.split(f"in {JAVA}: ", 1)[1]
    assert state.gate_outcomes == [failure.to_gate_outcome()]


def test_preserved_attribution_diagnostics_name_the_locator_files():
    assert attempt._preserved_attribution_diagnostics([]) is None
    files = ("a.py", "b.py")
    assert attempt._preserved_attribution_diagnostics(files) == {
        "preserved_prior_attribution": {"tier": "locator", "files": ["a.py", "b.py"]}}


def test_patch_required_files_are_existing_patch_targets_without_authoritative_full_source(tmp_path):
    ctx = _ctx(tmp_path)
    for rel in ("shown.py", "unshown.py", "full.py"):
        _write(ctx.worktree_path, rel, "x = 1\n")
    state = _state()
    with patch("kriya.workflow.attempt._has_authoritative_full_source", side_effect=lambda p, c, s: p == "shown.py"):
        required = attempt._patch_required_files(state, ctx, {"operation_by_file": {
            "shown.py": CodeOperation.REPAIR_WITH_PATCH, "unshown.py": CodeOperation.REPAIR_WITH_PATCH,
            "new.py": CodeOperation.REPAIR_WITH_PATCH, "full.py": CodeOperation.REPAIR_WITH_FULL_FILE}})
    assert required == ["unshown.py"]
    assert attempt._patch_required_files(state, ctx, {}) == []


def test_code_intelligence_opens_the_repository_index_or_degrades_on_attempts_logger(tmp_path, caplog):
    ctx = _ctx(tmp_path)
    with patch("kriya.workflow.graph_retrieval.open_code_intelligence", return_value="index") as opener:
        assert attempt._code_intelligence_for(ctx) == "index"
    assert opener.call_args.args == (ctx.workspace_path, os.path.join(ctx.kernel.config.paths.memory, "dependency_graph.db"))
    with patch("kriya.workflow.graph_retrieval.open_code_intelligence", side_effect=RuntimeError("corrupt")), \
            caplog.at_level(logging.WARNING, logger="kriya.workflow.attempt"):
        assert attempt._code_intelligence_for(ctx) is None
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        ("kriya.workflow.attempt", "Code Intelligence index unavailable for known-target context: corrupt")]
    ctx.kernel.config = None
    with patch("kriya.workflow.graph_retrieval.open_code_intelligence") as opener:
        assert attempt._code_intelligence_for(ctx) is None
    assert opener.call_count == 0


# =============================================================================================== enforce (OI-62)

def _tool(description="tests pass", tool_name="test"):
    return VerificationMethod(type=VerificationMethodType.TOOL, description=description, tool_name=tool_name)


def _judgment(description="reads well"):
    return VerificationMethod(type=VerificationMethodType.JUDGMENT, description=description)


def _subtask(*verification):
    return Subtask(id="s1", description="do s1", execution_method=ExecutionMethod.MODEL, verification=list(verification))


def _evidence(method, passed=True, **over):
    item = {"type": method.type.value, "description": method.description, "passed": passed}
    if method.tool_name:
        item["tool_name"] = method.tool_name
    item.update(over)
    return item


@pytest.mark.parametrize("verification,evidence,expected", [
    ((), None, (SubtaskStatus.COMPLETED, None, ())),
    ((_tool(),), None, (SubtaskStatus.NEEDS_REVIEW, "declared verification requirements have no complete authoritative "
                        "evidence", ("VERIFICATION_EVIDENCE_MISSING",))),
    ((_tool(), _judgment()), [_evidence(_tool())], (SubtaskStatus.NEEDS_REVIEW, "declared verification requirements have "
                                                     "no complete authoritative evidence", ("VERIFICATION_EVIDENCE_MISSING",))),
    ((_tool(), _judgment()), [_evidence(_tool()), _evidence(_judgment())], (SubtaskStatus.COMPLETED, None, ())),
    ((_tool(),), [_evidence(_tool(), passed=False)], (SubtaskStatus.FAILED, "verification failed: ['tests pass']",
                                                       ("VERIFICATION_FAILED",))),
    ((_tool(),), [_evidence(_tool(), passed=None)], (SubtaskStatus.NEEDS_REVIEW, "verification unresolved: ['tests pass']",
                                                      ("VERIFICATION_UNRESOLVED",))),
    ((_tool(),), [_evidence(_tool(), passed="true")], (SubtaskStatus.NEEDS_REVIEW, "verification unresolved: "
                                                        "['tests pass']", ("VERIFICATION_UNRESOLVED",))),
    ((_tool(),), [_evidence(_tool(), type="judgment")], (SubtaskStatus.NEEDS_REVIEW, "verification unresolved: "
                                                          "['tests pass']", ("VERIFICATION_UNRESOLVED",))),
    ((_tool(),), [_evidence(_tool(), tool_name="lint")], (SubtaskStatus.NEEDS_REVIEW, "verification unresolved: "
                                                           "['tests pass']", ("VERIFICATION_UNRESOLVED",))),
    ((_tool(),), [_evidence(_tool(), description="other")], (SubtaskStatus.NEEDS_REVIEW, "verification unresolved: "
                                                              "['tests pass']", ("VERIFICATION_UNRESOLVED",))),
    ((_tool(),), ["not a dict"], (SubtaskStatus.NEEDS_REVIEW, "verification unresolved: ['tests pass']",
                                  ("VERIFICATION_UNRESOLVED",))),
    ((_tool("t1"), _tool("t2")), [_evidence(_tool("t1"), passed=False), _evidence(_tool("t2"), passed=None)],
     (SubtaskStatus.FAILED, "verification failed: ['t1']", ("VERIFICATION_FAILED",))),
])
def test_the_subtask_verification_verdict_needs_complete_matching_evidence(verification, evidence, expected):
    call_result = {} if evidence is None else {"verification_results": evidence}
    assert controller._evaluate_subtask_verification(_subtask(*verification), call_result) == expected


def test_the_enforce_contract_transition_binds_the_live_workspace_and_the_final_suite(tmp_path):
    from kriya.workflow.contract_lifecycle import derive_contract_transition

    workspace = tmp_path / "ws"
    _write(workspace, "a.py", "old a\n")
    writes = [SimpleNamespace(target_path=str(workspace / "a.py"), content="new a\n", delete=False),
              SimpleNamespace(target_path="b.py", content="new b\n", delete=False),
              SimpleNamespace(target_path="gone.py", content="", delete=True)]
    results = [{"deterministic_gate_evidence": [{"type": "regression_test", "status": "PASS_WITH_TESTS"}]},
               {"deterministic_gate_evidence": [{"type": "regression_test", "status": "PASS"},
                                                {"type": "compile", "status": "PASS_WITH_TESTS"}]}]
    with patch("kriya.workflow.contract_authority.derive_direct_contract_authorizations",
               return_value=iter(["auth-1"])) as authorizations:
        built = controller._enforce_contract_transition(writes, workspace_path=str(workspace), goal="g", plan="p",
                                                        transaction_id="tx-1", subtask_call_results=results)
    assert authorizations.call_args.args == ("g", "p")
    assert isinstance(built, functools.partial) and built.func is derive_contract_transition and built.args == ()
    assert built.keywords == {"workspace_path": str(workspace), "registry": None, "original_contents": {"a.py": "old a\n"},
                              "final_contents": {"a.py": "new a\n", "b.py": "new b\n"}, "authorizations": ("auth-1",),
                              "transaction_id": "tx-1", "downstream_verified": False}
    with patch("kriya.workflow.contract_authority.derive_direct_contract_authorizations", return_value=[]):
        verified = controller._enforce_contract_transition([], workspace_path=str(workspace), goal="g", plan="p",
                                                           transaction_id="tx-2", subtask_call_results=results[:1])
        empty = controller._enforce_contract_transition([], workspace_path=str(workspace), goal="g", plan="p",
                                                        transaction_id="tx-3", subtask_call_results=[])
    assert verified.keywords["downstream_verified"] is True and empty.keywords["downstream_verified"] is False


# =============================================================================================== call shape

_PARAMS = {
    "_initial_test_process_boundary_constraint": ("required_verification", "target_files"),
    "_runtime_contract_requirements": ("ctx",),
    "_required_process_terminating_cases": ("required_verification",),
    "_enclosing_test_method": ("source", "offset"),
    "_enclosing_test_method_source": ("source", "offset"),
    "_resolve_java_main_argv_expression": ("method_source", "call_expression"),
    "_runtime_contract_expects_numeric_input": ("required_verification",),
    "_terminating_case_from_argv_expression": ("cases", "argv_expression", "*numeric_input"),
    "find_in_process_terminating_test_invocations": ("required_verification", "worktree_path", "test_files",
                                                     "application_entrypoints"),
    "find_ungrounded_java_child_process_tests": ("required_verification", "worktree_path", "test_files",
                                                 "java_main_classes", "known_files"),
    "_dependency_graph_db_path": ("ctx",),
    "_extract_class_names_best_effort": ("db_path", "filepath", "content"),
    "_find_java_main_class_best_effort": ("db_path", "filepath", "content"),
    "_build_java_main_class_map": ("java_files", "ctx"),
    "_record_omitted_members": ("state", "omitted"),
    "_record_known_target_package": ("state", "items"),
    "_escalation_authorized_paths": ("ctx", "target_files"),
    "_record_authority_expansions": ("state", "expansions"),
    "_stop_on_environment_gate_result": ("state", "result", "gate"),
    "_declares_test_verification": ("required_verification",),
    "_process_boundary_obligation_id": ("subtask_id",),
    "_record_process_boundary_obligation": ("ctx", "state", "*violated", "*evidence"),
    "_raise_unsafe_process_boundary_test_candidate": ("state", "ctx", "test_files"),
    "_raise_ungrounded_child_process_test_candidate": ("state", "ctx", "test_files"),
    "_build_workspace_type_index": ("state", "ctx"),
    "_judge_candidate_suite": ("state", "ctx", "result", "scope"),
    "_raise_unexecuted_test_delta": ("state", "ctx", "validator", "accepted_result", "selected_test"),
    "_as_developer": ("awaitable",),
    "_baseline_restoration_write": ("state", "target_path", "relpath"),
    "_file_integrity_stop": ("state", "filepath", "error"),
    "_target_exists": ("ctx", "filepath"),
    "_preserved_authoritative_locator_files": ("state", "target_files", "self_diagnosed"),
    "_request_fallback_for_rejected_authoritative_target": ("state", "ctx", "preserved_files"),
    "_reject_explanatory_prose": ("state", "filepath", "content", "baseline"),
    "_reject_exception_masking": ("state", "filepath", "content", "baseline"),
    "_preserved_attribution_diagnostics": ("preserved_files",),
    "_patch_required_files": ("state", "ctx", "kwargs"),
    "_code_intelligence_for": ("ctx",),
}
_CONTROLLER_PARAMS = {
    "_evaluate_subtask_verification": ("subtask", "call_result"),
    "_enforce_contract_transition": ("terminal_writes", "*workspace_path", "*goal", "*plan", "*transaction_id",
                                     "*subtask_call_results"),
}


def _shape(function):
    out = []
    for parameter in inspect.signature(function).parameters.values():
        assert parameter.default is inspect.Parameter.empty, parameter
        out.append(("*" if parameter.kind is inspect.Parameter.KEYWORD_ONLY else "") + parameter.name)
    return tuple(out)


@pytest.mark.parametrize("module,name,params", [(attempt, n, p) for n, p in _PARAMS.items()]
                         + [(controller, n, p) for n, p in _CONTROLLER_PARAMS.items()])
def test_every_moved_name_keeps_its_call_shape_on_its_module(module, name, params):
    function = getattr(module, name)
    assert inspect.isfunction(function) and function.__module__ == module.__name__
    assert _shape(function) == params
    assert inspect.iscoroutinefunction(function) is (name == "_as_developer")


# builder -> the moved names it binds by name (a test patch of the module attribute must reach the field)
_DEPS_BINDINGS = {
    "_gate_deps": ("_build_java_main_class_map", "_build_workspace_type_index", "_declares_test_verification",
                   "_judge_candidate_suite", "_raise_unexecuted_test_delta",
                   "_raise_ungrounded_child_process_test_candidate", "_raise_unsafe_process_boundary_test_candidate",
                   "_record_process_boundary_obligation", "_stop_on_environment_gate_result", "_as_developer"),
    "_runtime_deps": ("_build_java_main_class_map", "_stop_on_environment_gate_result", "_as_developer"),
    "_repair_deps": ("_escalation_authorized_paths", "_record_authority_expansions", "_record_known_target_package",
                     "_record_omitted_members"),
    "_generation_deps": ("_dependency_graph_db_path", "_escalation_authorized_paths", "_record_authority_expansions",
                         "_code_intelligence_for", "_patch_required_files"),
    "_fallback_deps": ("_patch_required_files",),
    "_spec_deps": ("_baseline_restoration_write",),
    "_candidate_deps": ("_code_intelligence_for", "_reject_exception_masking", "_build_workspace_type_index",
                        "_dependency_graph_db_path", "_extract_class_names_best_effort", "_file_integrity_stop",
                        "_preserved_attribution_diagnostics", "_preserved_authoritative_locator_files",
                        "_reject_explanatory_prose", "_request_fallback_for_rejected_authoritative_target",
                        "_target_exists"),
}


def _builder_keywords(module, builder):
    """The keyword arguments of the one ``*Deps(...)`` call a builder returns, as {field: source of the value}."""
    import ast

    tree = ast.parse(inspect.getsource(getattr(module, builder)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and node.keywords]
    assert len(calls) == 1, builder
    return {keyword.arg: ast.unparse(keyword.value) for keyword in calls[0].keywords}


@pytest.mark.parametrize("builder,name", [(b, n) for b, names in _DEPS_BINDINGS.items() for n in names])
def test_every_deps_builder_binds_the_moved_name_at_call_time(builder, name):
    # a bare-name load inside the builder's body resolves the module global when the builder runs (a test patch of
    # the module attribute reaches the field), and the field is today's object
    assert _builder_keywords(attempt, builder)[name] == name
    assert getattr(getattr(attempt, builder)(), name) is getattr(attempt, name)


def test_a_patch_of_a_patched_moved_name_reaches_its_deps_fields():
    sentinel = object()
    with patch("kriya.workflow.attempt._judge_candidate_suite", sentinel):
        assert attempt._gate_deps()._judge_candidate_suite is sentinel
    with patch("kriya.workflow.attempt._reject_exception_masking", sentinel):
        assert attempt._candidate_deps()._reject_exception_masking is sentinel
    with patch("kriya.workflow.attempt._code_intelligence_for", sentinel):
        assert attempt._candidate_deps()._code_intelligence_for is sentinel
        assert attempt._generation_deps()._code_intelligence_for is sentinel


def test_the_enforce_deps_bind_the_controllers_moved_names_at_call_time():
    from _strict_doubles import strict_engine

    built = controller.WorkflowController(strict_engine())
    for name in ("_evaluate_subtask_verification", "_enforce_contract_transition"):
        assert _builder_keywords(controller, "_enforce_deps")[name] == name
        assert getattr(controller._enforce_deps(built), name) is getattr(controller, name)
    sentinel = object()
    with patch("kriya.workflow.workflow_controller._evaluate_subtask_verification", sentinel):
        assert controller._enforce_deps(built)._evaluate_subtask_verification is sentinel
