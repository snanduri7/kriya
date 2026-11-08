"""REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001 (owner sign-off 2026-11-08): the two closers the sealed contract binds for the two anchored whole-statement
grammars - "It prints <literal> when executed." (RUNTIME_STDOUT, a BEHAVIOR claim) and "Create <path>." (FILE_CREATION).

TRACED before this change: the contract compiler classified both statements as BEHAVIOR / GENERAL / AUTHORITY_REQUIRED (MEASURED on the live ``hello.py`` contract) and the runtime gate carried no requirement linkage, so a
passed run closed nothing. The linkage is the contract's binding (the expectation is sealed from the user's words); the runtime gate stays generic and the closer reads its evidence only for the requirement it was bound to.
The owner's controls A-H are the structure of this file; the mutations at the end are the decision logic's negative controls.
"""
import os

import pytest

from kriya.config.environment import PROJECT_PYTHON_INTERPRETER as PYTHON
from kriya.workflow import contract_closers as cc
from kriya.workflow import requirement_closers as rc
from kriya.workflow.contract_compilation import (
    CLOSER_FILE_CREATION,
    CLOSER_RUNTIME_STDOUT,
    STATUS_AUTHORITY_REQUIRED,
    STATUS_CLOSABLE,
    compile_verification_contract,
)
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirement_scopes import file_creation_statement, runtime_stdout_statement
from kriya.workflow.requirements import (
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)
from kriya.workflow.runtime_observation import (
    CANDIDATE_CHANGED,
    CANDIDATE_NOT_STAMPED,
    COMMAND_NOT_A_CANDIDATE_FILE,
    NO_APPLICATION_RUN,
    STDOUT_LOST,
    STEP_NOT_PASSED,
    application_run_observation,
    candidate_files_digest,
    normalise_stdout,
    observed_run_output,
)

QUOTED = 'Create hello.py. It prints "hello world" when executed. Include a pytest test for it.\n'
UNQUOTED = "Create hello.py. It prints hello world when executed.\n"


def _candidate(tmp_path, files=None):
    root = tmp_path / "cand"
    root.mkdir(exist_ok=True)
    for name, text in (files or {"hello.py": "print('hello world')\n"}).items():
        (root / name).write_text(text)
    return str(root)


def _step(command, stdout="hello world\n", exit_code=0, **extra):
    return {"command": list(command), "exit_code": exit_code, "stdout": stdout, "stderr": "", "timed_out": False, **extra}


def _outcome(root, written, steps=None, *, attempt=1, type="run_verification", success=True, stamp=True, **extra):
    outcome = {"attempt": attempt, "type": type, "success": success, "graded_by": "deterministic",
               "steps": steps if steps is not None else [_step([PYTHON, "hello.py"])], **extra}
    if stamp:
        outcome["candidate_digest"] = candidate_files_digest(root, written)
    return outcome


def _setup(goal):
    reqs = derive_requirements(goal)
    contract = compile_verification_contract(reqs, origins=statement_origins(goal))
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    return reqs, contract, ledger


def _stdout_close(goal, root, outcomes, *, written=("hello.py",), attempt=1):
    reqs, contract, ledger = _setup(goal)
    entries = cc.close_runtime_stdout_requirements(
        ledger, reqs, contract, gate_outcomes=outcomes, attempt=attempt, candidate_root=root, written_files=written,
        source="t", revision=1)
    return entries, requirement_outcomes(ledger, reqs), ledger, reqs


# ---------------------------------------------------------------------------------------------------------------- the grammars (anchored; uncertain = not bound)
@pytest.mark.parametrize("text,expected,quoted", [
    ("It prints hello world when executed.", "hello world", False),
    ('It prints "hello world" when executed.', "hello world", True),
    ("The program outputs `42` when run", "42", True),
    ("The script displays 'Hi' when run.", "Hi", True),
    ("prints hello world when run", "hello world", False),
])
def test_the_approved_forms_bind_with_their_exact_literal(text, expected, quoted):
    parsed = runtime_stdout_statement(text)
    assert parsed is not None and parsed["expected"] == expected and parsed["quoted"] is quoted


@pytest.mark.parametrize("text", [
    'It prints "Hello, <name>!" when executed.',            # a placeholder
    'It prints "{name}" when run',                          # a format field
    'It prints "line1\\nline2" when run.',                  # an escape
    'It prints "a" and "b" when executed.',                 # two literals
    "It prints hello world and exits when executed.",       # a conjunction
    "It prints hello world then stops when executed.",
    "It prints a greeting when executed.",                  # a description, not a literal
    "It prints the sum when executed.",
    "It prints Hello, World! when executed.",               # ambiguous punctuation
    "It prints hello world when it is executed.",           # outside the approved suffix
    "It prints hello world when executed with no arguments.",
    "It prints hello world.",                               # no suffix
    "It does not print hello world when executed.",
    'Create hello.py that prints "x" when executed.',       # a compound is not one of the anchored forms
    "../x.py prints hi when run",
    "It prints hello world or goodbye when run",
    "It prints 2 + 2 when run",                             # a formula
    'It prints "" when executed.',                          # empty
])
def test_anything_uncertain_is_not_bound(text):
    assert runtime_stdout_statement(text) is None


@pytest.mark.parametrize("text,path", [
    ("Create hello.py.", "hello.py"), ("Create `src/hello.py`", "src/hello.py"), ("create hello.py", "hello.py"),
    ("Please create a.b/c.py.", "a.b/c.py"),
])
def test_an_exact_creation_statement_binds_its_literal_path(text, path):
    assert file_creation_statement(text) == path


@pytest.mark.parametrize("text", [
    "Create greet.py with a function greet(name).", "Create hello.py and test_hello.py.", "Create ../x.py.",
    "Create /etc/x.py.", "Create hello.", "Create a hello world program.", "Create hello.py. It prints x.",
    "Create `a b.py`.", "Create ./hello.py.",
])
def test_a_compound_or_unsafe_creation_statement_is_not_bound(text):
    assert file_creation_statement(text) is None


def test_the_contract_binds_both_statements_and_no_longer_asks_for_authority():
    reqs = derive_requirements(QUOTED)
    contract = compile_verification_contract(reqs, origins=statement_origins(QUOTED))
    by = {e.requirement_id: e for e in contract.entries}
    assert [c for c, _ in [(b.closer, b) for b in by["REQ-1"].bindings]] == [CLOSER_FILE_CREATION]
    assert [b.closer for b in by["REQ-2"].bindings] == [CLOSER_RUNTIME_STDOUT]
    assert by["REQ-2"].bindings[0].detail["expected"] == "hello world" and by["REQ-2"].bindings[0].detail["quoted"] is True
    assert all(e.status == STATUS_CLOSABLE for e in by.values()) and not any(e.residual for e in by.values())


def test_an_unbound_phrasing_stays_authority_required_as_before():
    for goal in ('Create hello.py. It prints "Hello, <name>!" when executed.\n', "Create greet.py with a function greet(name).\n"):
        reqs = derive_requirements(goal)
        contract = compile_verification_contract(reqs, origins=statement_origins(goal))
        assert any(e.status == STATUS_AUTHORITY_REQUIRED for e in contract.entries), goal


# ---------------------------------------------------------------------------------------------------------------- A / C: the observation closes the requirement it states
def test_control_a_quoted_literal_with_the_complete_matching_stdout_closes_the_requirement(tmp_path):
    root = _candidate(tmp_path)
    entries, outcomes, ledger, reqs = _stdout_close(QUOTED, root, [_outcome(root, ["hello.py"])])
    assert entries == [{"requirement": "REQ-2", "kind": "runtime_stdout_observation", "closed": True,
                        "expected": "hello world", "quoted": True, "reason_code": cc.STDOUT_MATCHED}]
    assert outcomes["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    evidence = requirement_evidence(ledger, reqs)["REQ-2"]["claims"]["BEHAVIOR"]
    assert evidence["observed"] == "hello world" and evidence["command"] == [PYTHON, "hello.py"] and evidence["exit_code"] == 0


def test_control_c_unquoted_exact_match_closes(tmp_path):
    root = _candidate(tmp_path)
    _, outcomes, _, _ = _stdout_close(UNQUOTED, root, [_outcome(root, ["hello.py"])])
    assert outcomes["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_control_c_unquoted_case_difference_is_open_never_violated(tmp_path):
    root = _candidate(tmp_path)
    outcome = _outcome(root, ["hello.py"], [_step([PYTHON, "hello.py"], "Hello World\n")])
    entries, outcomes, _, _ = _stdout_close(UNQUOTED, root, [outcome])
    assert entries[0]["reason_code"] == cc.STDOUT_DIFFERS_UNQUOTED and entries[0]["closed"] is False
    assert outcomes["REQ-2"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------------------------------------------------------- B: a quoted mismatch is deterministic counter-evidence
def test_control_b_exit_zero_but_wrong_value_is_violated_for_a_quoted_literal(tmp_path):
    root = _candidate(tmp_path, {"hello.py": "print('goodbye')\n"})
    outcome = _outcome(root, ["hello.py"], [_step([PYTHON, "hello.py"], "goodbye\n")])
    entries, outcomes, ledger, reqs = _stdout_close(QUOTED, root, [outcome])
    assert entries[0]["reason_code"] == cc.STDOUT_DIFFERS_QUOTED
    assert outcomes["REQ-2"] is RequirementOutcome.VIOLATED
    assert requirement_evidence(ledger, reqs)["REQ-2"]["observed"] == "goodbye"


@pytest.mark.parametrize("stdout", ["hello world!\n", "hello world\n\n", " hello world\n", "say hello world\n", "hello\n", ""])
def test_whole_stdout_is_compared_no_substring_no_trimming(tmp_path, stdout):
    root = _candidate(tmp_path)
    outcome = _outcome(root, ["hello.py"], [_step([PYTHON, "hello.py"], stdout)])
    _, outcomes, _, _ = _stdout_close(QUOTED, root, [outcome])
    assert outcomes["REQ-2"] is RequirementOutcome.VIOLATED


def test_exactly_one_conventional_trailing_newline_is_normalised():
    assert normalise_stdout("x\n") == "x" and normalise_stdout("x\r\n") == "x" and normalise_stdout("x\n\n") == "x\n"
    assert normalise_stdout("x") == "x"


# ---------------------------------------------------------------------------------------------------------------- E / F: evidence that is not the application run of THIS candidate closes nothing
def test_control_e_a_passed_gate_with_no_binding_to_the_requirement_closes_nothing(tmp_path):
    root = _candidate(tmp_path)
    goal = "Create hello.py. It prints a greeting when executed.\n"            # not an anchored form: no binding
    reqs, contract, ledger = _setup(goal)
    assert not contract.binding_closers(CLOSER_RUNTIME_STDOUT)
    entries = cc.close_runtime_stdout_requirements(
        ledger, reqs, contract, gate_outcomes=[_outcome(root, ["hello.py"])], attempt=1, candidate_root=root,
        written_files=["hello.py"], source="t", revision=1)
    assert entries == [] and requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("case,expect", [
    ("test_typed_gate", NO_APPLICATION_RUN),          # a pytest run is not the application run (control G's cousin)
    ("managed_probe", NO_APPLICATION_RUN),
    ("other_attempt", NO_APPLICATION_RUN),
    ("failed_gate", NO_APPLICATION_RUN),
    ("unstamped", CANDIDATE_NOT_STAMPED),
    ("changed_after", CANDIDATE_CHANGED),
    ("lost_stdout", STDOUT_LOST),
    ("setup_failed", STEP_NOT_PASSED),
    ("timed_out", STEP_NOT_PASSED),
    ("not_started", STEP_NOT_PASSED),
    ("no_stdout", "STDOUT_UNAVAILABLE"),
    ("no_steps", "STDOUT_UNAVAILABLE"),
    ("echo", COMMAND_NOT_A_CANDIDATE_FILE),            # a model-chosen command that prints the literal is not the candidate's behaviour
    ("python_c", COMMAND_NOT_A_CANDIDATE_FILE),
    ("with_args", COMMAND_NOT_A_CANDIDATE_FILE),
    ("host_python", COMMAND_NOT_A_CANDIDATE_FILE),
    ("other_file", COMMAND_NOT_A_CANDIDATE_FILE),      # a file the candidate did not write
    ("escaping", COMMAND_NOT_A_CANDIDATE_FILE),
])
def test_control_f_incomplete_or_foreign_evidence_cannot_close_the_requirement(tmp_path, case, expect):
    root = _candidate(tmp_path, {"hello.py": "print('hello world')\n", "other.py": "print('hello world')\n"})
    written = ["hello.py"]
    good = [_step([PYTHON, "hello.py"])]
    outcome = _outcome(root, written)
    if case == "test_typed_gate":
        outcome["type"] = "test"
    elif case == "managed_probe":
        outcome["graded_by"] = "managed_service_probe"
    elif case == "other_attempt":
        outcome["attempt"] = 2
    elif case == "failed_gate":
        outcome["success"] = False
    elif case == "unstamped":
        outcome.pop("candidate_digest")
    elif case == "changed_after":
        (tmp_path / "cand" / "hello.py").write_text("print('goodbye')\n")
    elif case == "lost_stdout":
        outcome["steps"] = [_step([PYTHON, "hello.py"], stdout_lost_chars=10)]
    elif case == "setup_failed":
        outcome["steps"] = [_step(["pip", "install"], exit_code=1), *good]
    elif case == "timed_out":
        outcome["steps"] = [{**good[0], "timed_out": True}]
    elif case == "not_started":
        outcome["steps"] = [{**good[0], "target_started": False}]
    elif case == "no_stdout":
        outcome["steps"] = [{"command": [PYTHON, "hello.py"], "exit_code": 0, "timed_out": False}]
    elif case == "no_steps":
        outcome["steps"] = []
    elif case == "echo":
        outcome["steps"] = [_step(["echo", "hello world"])]
    elif case == "python_c":
        outcome["steps"] = [_step([PYTHON, "-c", "print('hello world')"])]
    elif case == "with_args":
        outcome["steps"] = [_step([PYTHON, "hello.py", "--flag"])]
    elif case == "host_python":
        outcome["steps"] = [_step(["/usr/bin/python3", "hello.py"])]
    elif case == "other_file":
        outcome["steps"] = [_step([PYTHON, "other.py"])]
    elif case == "escaping":
        outcome["steps"] = [_step([PYTHON, "../hello.py"])]
    if case == "changed_after":
        pass
    obs = application_run_observation([outcome], 1, candidate_root=root, written_files=written)
    assert obs.available is False and obs.reason_code == expect, (case, obs)
    entries, outcomes, _, _ = _stdout_close(QUOTED, root, [outcome], written=written)
    assert entries[0]["closed"] is False and entries[0]["reason_code"] == expect
    assert outcomes["REQ-2"] is RequirementOutcome.UNVERIFIED         # unusable evidence is open, never violated


def test_a_setup_step_before_the_application_run_is_allowed_when_it_passed(tmp_path):
    root = _candidate(tmp_path)
    steps = [_step(["pip", "install", "-r", "requirements.txt"], stdout="ok\n"), _step([PYTHON, "hello.py"])]
    _, outcomes, _, _ = _stdout_close(QUOTED, root, [_outcome(root, ["hello.py"], steps)])
    assert outcomes["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_the_last_passed_run_of_the_attempt_is_the_evidence(tmp_path):
    root = _candidate(tmp_path)
    older = _outcome(root, ["hello.py"], [_step([PYTHON, "hello.py"], "stale\n")])
    newer = _outcome(root, ["hello.py"])
    _, outcomes, _, _ = _stdout_close(QUOTED, root, [older, newer])
    assert outcomes["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_a_subject_path_the_statement_names_must_be_the_file_that_ran(tmp_path):
    root = _candidate(tmp_path, {"hello.py": "x\n", "main.py": "x\n"})
    goal = 'hello.py prints "hello world" when run.\n'
    outcome = _outcome(root, ["hello.py", "main.py"], [_step([PYTHON, "main.py"])])
    entries, outcomes, _, _ = _stdout_close(goal, root, [outcome], written=["hello.py", "main.py"])
    assert entries[0]["reason_code"] == "SUBJECT_NOT_EXECUTED" and outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED
    ok = _outcome(root, ["hello.py", "main.py"], [_step([PYTHON, "hello.py"])])
    _, outcomes, _, _ = _stdout_close(goal, root, [ok], written=["hello.py", "main.py"])
    assert outcomes["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_a_later_unusable_evaluation_withdraws_the_earlier_judgment_of_the_same_candidate(tmp_path):
    root = _candidate(tmp_path)
    reqs, contract, ledger = _setup(QUOTED)
    kwargs = dict(attempt=1, candidate_root=root, written_files=["hello.py"], source="t", revision=1)
    cc.close_runtime_stdout_requirements(ledger, reqs, contract, gate_outcomes=[_outcome(root, ["hello.py"])], **kwargs)
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    cc.close_runtime_stdout_requirements(ledger, reqs, contract, gate_outcomes=None, **kwargs)        # e.g. a resume without the run's evidence
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_a_record_for_other_words_closes_nothing_at_read_time(tmp_path):
    root = _candidate(tmp_path)
    reqs, contract, ledger = _setup(QUOTED)
    cc.close_runtime_stdout_requirements(ledger, reqs, contract, gate_outcomes=[_outcome(root, ["hello.py"])], attempt=1,
                                         candidate_root=root, written_files=["hello.py"], source="t", revision=1)
    from kriya.workflow.obligations import ObligationRecord
    from kriya.workflow.requirements import requirement_claim_id
    record = ledger.current(requirement_claim_id("REQ-2", "BEHAVIOR"))
    evidence = dict(record.evidence)
    evidence["requirement_text_sha256"] = "0" * 64                 # the record was judged for other words
    ledger.record(ObligationRecord(id=record.id, kind=record.kind, status=record.status, authority=record.authority,
                                   description=record.description, source=record.source, revision=record.revision,
                                   evidence=evidence, terminal_required=False))
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------------------------------------------------------- G: tests are not self-proving
def test_control_g_a_passing_model_written_test_closes_neither_the_stdout_statement_nor_a_general_one(tmp_path):
    root = _candidate(tmp_path, {"hello.py": "print('goodbye')\n", "test_hello.py": "def test_x():\n    assert True\n"})
    test_gate = {"attempt": 1, "type": "test", "success": True, "graded_by": "deterministic",
                 "steps": [_step([PYTHON, "-m", "pytest"], "1 passed\n")],
                 "candidate_digest": candidate_files_digest(root, ["hello.py", "test_hello.py"])}
    _, outcomes, _, _ = _stdout_close(QUOTED, root, [test_gate], written=["hello.py", "test_hello.py"])
    assert outcomes["REQ-2"] is RequirementOutcome.UNVERIFIED
    # and a GENERAL behaviour statement has no runtime binding at all: no closer in this module can see it
    goal = 'Make greet(name) return "Hello, <name>!" for any name.\n'
    reqs = derive_requirements(goal)
    contract = compile_verification_contract(reqs, origins=statement_origins(goal))
    assert not contract.binding_closers(CLOSER_RUNTIME_STDOUT) and not contract.binding_closers(CLOSER_FILE_CREATION)


# ---------------------------------------------------------------------------------------------------------------- the file-creation closer (D and H live in the existing test-addition suite / here)
def _creation(goal, root, created, written=("hello.py",)):
    reqs, contract, ledger = _setup(goal)
    entries = rc.close_requirements_by_file_creation(ledger, reqs, contract, root, created_paths=created, revision=1)
    return entries, requirement_outcomes(ledger, reqs)


def test_a_path_the_raw_capture_proves_created_and_present_closes_the_creation_requirement(tmp_path):
    root = _candidate(tmp_path)
    created = rc.created_paths_from_capture(["hello.py"], {"hello.py": None})
    entries, outcomes = _creation("Create hello.py.\n", root, created)
    assert entries[0]["closed"] is True and entries[0]["reason_code"] == cc.FILE_CREATED
    assert outcomes["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE


@pytest.mark.parametrize("case", ["modified_existing", "no_capture", "hello2", "absent_now", "symlink", "enforce_none", "directory"])
def test_control_h_nothing_else_closes_a_creation_requirement(tmp_path, case):
    root = _candidate(tmp_path, {"hello2.py": "x\n"} if case == "hello2" else {"hello.py": "x\n"})
    written = ["hello.py"]
    raw = {"hello.py": None}
    created = None
    if case == "modified_existing":
        raw = {"hello.py": b"old\n"}                         # it existed before: MODIFY, not CREATE
    elif case == "no_capture":
        raw = {}                                             # not provable: never inferred from presence
    elif case == "hello2":
        written, raw = ["hello2.py"], {"hello2.py": None}    # created a similarly named file
    elif case == "absent_now":
        os.remove(os.path.join(root, "hello.py"))
    elif case == "symlink":
        os.remove(os.path.join(root, "hello.py"))
        (tmp_path / "elsewhere.py").write_text("x\n")
        os.symlink(str(tmp_path / "elsewhere.py"), os.path.join(root, "hello.py"))
    elif case == "directory":
        os.remove(os.path.join(root, "hello.py"))
        os.mkdir(os.path.join(root, "hello.py"))
    if case != "enforce_none":
        created = rc.created_paths_from_capture(written, raw)
    entries, outcomes = _creation("Create hello.py.\n", root, created)
    assert entries[0]["closed"] is False
    assert outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED          # open, never verified (and never guessed violated)


def test_a_compound_creation_statement_keeps_its_old_claims_and_is_not_closed_by_the_creation_closer(tmp_path):
    root = _candidate(tmp_path, {"greet.py": "x\n"})
    goal = "Create greet.py with a function greet(name) that returns a greeting.\n"
    reqs, contract, ledger = _setup(goal)
    assert not contract.binding_closers(CLOSER_FILE_CREATION)
    assert rc.close_requirements_by_file_creation(ledger, reqs, contract, root, created_paths=["greet.py"], revision=1) == []
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------------------------------------------------------- the whole goal: the live hello.py contract
def test_the_live_hello_goal_reaches_all_verified_when_every_closer_has_its_evidence(tmp_path):
    root = _candidate(tmp_path, {"hello.py": "print('hello world')\n", "test_hello.py": "def test_x():\n    assert True\n"})
    written = ["hello.py", "test_hello.py"]
    goal = 'Create hello.py. It prints "hello world" when executed. Include a pytest test for it.\n'
    reqs, contract, ledger = _setup(goal)
    rc.close_requirements_by_file_creation(ledger, reqs, contract, root, created_paths=written, revision=1)
    cc.close_runtime_stdout_requirements(ledger, reqs, contract, gate_outcomes=[_outcome(root, written)], attempt=1,
                                         candidate_root=root, written_files=written, source="t", revision=1)
    cc.close_test_addition_requirements(
        ledger, reqs, contract, reference_test_files=[], candidate_test_files=["test_hello.py"], base_test_identities=None,
        candidate_test_identities=None, source="t", revision=1, read_candidate=lambda p: (tmp_path / "cand" / p).read_bytes(),
        read_reference=lambda p: None)
    assert set(requirement_outcomes(ledger, reqs).values()) == {RequirementOutcome.CLOSED_BY_EVIDENCE}


# ---------------------------------------------------------------------------------------------------------------- the sequence and the enforce terminal gate
def test_the_closer_sequence_contains_both_closers_in_the_contract_group():
    assert rc.CLOSER_SEQUENCE.index("file_creation") < rc.CLOSER_SEQUENCE.index("documentation")
    assert {"file_creation", "runtime_stdout"} <= set(rc.CONTRACT_CLOSERS) and rc.CONTRACT_CLOSERS[-1] == "documentation"


def test_the_enforce_terminal_gate_has_no_evidence_and_closes_neither(tmp_path):
    root = _candidate(tmp_path)
    reqs, contract, ledger = _setup(QUOTED)
    rc.close_requirements_by_file_creation(ledger, reqs, contract, root, created_paths=None, revision="terminal")
    rc.close_requirements_by_runtime_stdout(ledger, reqs, contract, root, gate_outcomes=None, attempt="terminal",
                                            written_files=(), revision="terminal")
    outcomes = requirement_outcomes(ledger, reqs)
    assert outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED and outcomes["REQ-2"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------------------------------------------------------- the result's Observed output (display, never closure)
def test_observed_output_is_capped_redacted_and_flags_lost_output(tmp_path):
    big = "x" * 3000
    out = observed_run_output([{"attempt": 1, "type": "run_verification", "success": True,
                                "steps": [_step([PYTHON, "hello.py"], big, stdout_lost_chars=5)]}], 1)
    assert out["command"] == "python3 hello.py" and out["exit_status"] == 0
    assert len(out["stdout"]) == 2000 and out["truncated"] is True and out["stdout_lost"] is True
    secret = observed_run_output([{"attempt": 1, "type": "run_verification", "success": True,
                                   "steps": [_step([PYTHON, "hello.py"], "token=ghp_" + "a" * 36 + "\n")]}], 1)
    assert "ghp_" + "a" * 36 not in secret["stdout"]
    assert observed_run_output([], 1) is None
    assert observed_run_output([{"attempt": 1, "type": "test", "success": True, "steps": [_step(["pytest"])]}], 1) is None
    assert observed_run_output([{"attempt": 1, "type": "run_verification", "success": True, "graded_by": "managed_service_probe",
                                 "steps": [_step(["x"])]}], 1) is None
