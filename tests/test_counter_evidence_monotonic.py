"""COUNTER-EVIDENCE IS MONOTONIC (owner decision after the addendum #4 review).

MEASURED by the review on the real direct path with the default policy ``record``: a deterministic mutation-scope violation already
visible in the run's committed history, plus an AttributeError or an OSError in a LATER git read of the same evidence producer, ended
``quality_gates_passed: False / violated`` -> ``True / unverified``. The first wrong decision was the producer
(requirement_closers.mutation_scope_evidence) collapsing everything it had already established into one ``unavailable`` object, and every
consumer checking ``unavailable`` before looking at the paths.

The rule these tests pin, in both halves:

  known counter-example present          -> VIOLATED, complete or not
  no counter-example, record complete    -> evaluated normally
  no counter-example, record INCOMPLETE  -> never closed (UNVERIFIED with the unavailable reason)

``requirements.scope_evidence_view`` is the one reader of the evidence shape, shared by mutation scope, test immutability, file
immutability and suite preservation. A Kriya coding error (AttributeError) in the producer's collaborators is not degraded: it reaches
the attempt classifier (Q5). Operational failures (OSError, git exiting non-zero) are.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _reg_r1_fixture import validator_for, write_project  # noqa: E402
from test_prd020_mutation_scope import (  # noqa: E402  # pylint: disable=wrong-import-position
    ROLE_TRACKED,
    SCOPE,
    _ledger,
    _scope,
)
from test_requirement_closure_plain_goal_001 import IMMUTABLE, derive_requirements, unverified_ledger  # noqa: E402
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    GOOD,
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
    _run,
    _scripted_engine,
    _workspace,
)

from kriya.workflow import requirement_closers, worktree  # noqa: E402
from kriya.workflow import workflow as workflow_module  # noqa: E402
from kriya.workflow.requirement_closers import CloserDeps  # noqa: E402
from kriya.workflow.requirements import (  # noqa: E402
    RequirementOutcome,
    close_file_immutability_requirements,
    close_mutation_scope_requirements,
    close_suite_preservation_requirements,
    close_test_immutability_requirements,
    requirement_outcomes,
    scope_evidence_view,
)

PARTIAL_WITH_VIOLATION = {"unavailable": "OSError: git ls-tree: I/O error", "known_changed_paths": ["src/B.java"],
                          "committed_history": ["src/B.java"], "run_id": "run-1", "base_revision": "base"}
PARTIAL_WITHOUT_VIOLATION = {"unavailable": "OSError: git ls-tree: I/O error", "known_changed_paths": ["src/A.java"],
                             "committed_history": ["src/A.java"], "run_id": "run-1", "base_revision": "base"}
NOTHING_KNOWN = {"unavailable": "OSError: the committed history cannot be read"}


# ------------------------------------------------------------------------------- the shared reader (one owner of "partial")

def test_the_reader_separates_known_paths_from_completeness():
    complete = scope_evidence_view(_scope(["a.py"], foreign=["b.py"]))
    assert complete.complete and complete.changed == {"a.py", "b.py"} and complete.reason is None
    partial = scope_evidence_view(PARTIAL_WITH_VIOLATION)
    assert not partial.complete and partial.changed == {"src/B.java"} and "I/O error" in partial.reason
    assert not scope_evidence_view(NOTHING_KNOWN).complete and scope_evidence_view(NOTHING_KNOWN).changed == frozenset()
    assert not scope_evidence_view(None).complete and not scope_evidence_view({}).complete


# ------------------------------------------------------------------------------- consumers: mutation scope

def test_mutation_scope_a_known_violation_survives_an_incomplete_record():
    reqs, ledger = _ledger("Modify src/A.java." + SCOPE)
    [attempt] = close_mutation_scope_requirements(ledger, reqs, tracked_paths=ROLE_TRACKED, scope_evidence=PARTIAL_WITH_VIOLATION,
                                                  source="test", revision=1)
    assert attempt["violated"] is True and attempt["out_of_scope_paths"] == ["src/B.java"] and attempt["closed"] is False
    assert attempt["evidence_complete"] is False and "I/O error" in attempt["evidence_unavailable"]    # the diagnostics are kept
    assert requirement_outcomes(ledger, reqs)[reqs.requirements[-1].id] is RequirementOutcome.VIOLATED


@pytest.mark.parametrize("evidence", [PARTIAL_WITHOUT_VIOLATION, NOTHING_KNOWN, None], ids=["partial", "nothing-known", "not-collected"])
@pytest.mark.parametrize("verdict", [RequirementOutcome.UNVERIFIED, RequirementOutcome.SATISFIED])
def test_mutation_scope_an_incomplete_record_without_a_violation_never_closes(evidence, verdict):
    reqs, ledger = _ledger("Modify src/A.java." + SCOPE, outcome=verdict)
    [attempt] = close_mutation_scope_requirements(ledger, reqs, tracked_paths=ROLE_TRACKED, scope_evidence=evidence,
                                                  source="test", revision=1)
    assert attempt["closed"] is False and "violated" not in attempt and "unavailable" in attempt["reason"]
    # a model's `satisfied` is only a claim (FS-1B): it maps to UNVERIFIED, and an incomplete record can not promote it
    assert requirement_outcomes(ledger, reqs)[reqs.requirements[-1].id] is RequirementOutcome.UNVERIFIED


def test_mutation_scope_control_a_complete_record_closes():
    reqs, ledger = _ledger("Modify src/A.java." + SCOPE)
    [attempt] = close_mutation_scope_requirements(ledger, reqs, tracked_paths=ROLE_TRACKED, scope_evidence=_scope(["src/A.java"]),
                                                  source="test", revision=1)
    assert attempt["closed"] is True and attempt["evidence_complete"] is True


# ------------------------------------------------------------------------------- consumers: test and file immutability

def test_test_immutability_a_known_changed_test_survives_an_incomplete_record():
    reqs = derive_requirements("Fix it.\n" + IMMUTABLE)
    evidence = {"unavailable": "OSError: boom", "known_changed_paths": ["tests/test_a.py"]}
    ledger = unverified_ledger(reqs)
    [entry] = close_test_immutability_requirements(
        ledger, reqs, reference_test_files=["tests/test_a.py"], present_test_files=["tests/test_a.py"], scope_evidence=evidence,
        source="t", revision=1)
    assert entry["violated"] is True and entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.VIOLATED


def test_test_immutability_a_vanished_test_needs_no_git_read_to_convict():
    reqs = derive_requirements("Fix it.\n" + IMMUTABLE)
    ledger = unverified_ledger(reqs)
    [entry] = close_test_immutability_requirements(
        ledger, reqs, reference_test_files=["tests/test_a.py"], present_test_files=[], scope_evidence=NOTHING_KNOWN, source="t", revision=1)
    assert entry["violated"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.VIOLATED


@pytest.mark.parametrize("evidence", [{"unavailable": "OSError: boom", "known_changed_paths": ["src/x.py"]}, NOTHING_KNOWN, None],
                         ids=["partial", "nothing-known", "not-collected"])
def test_test_immutability_an_incomplete_record_without_a_violation_never_closes(evidence):
    reqs = derive_requirements("Fix it.\n" + IMMUTABLE)
    ledger = unverified_ledger(reqs)
    [entry] = close_test_immutability_requirements(
        ledger, reqs, reference_test_files=["tests/test_a.py"], present_test_files=["tests/test_a.py"], scope_evidence=evidence,
        source="t", revision=1)
    assert entry["closed"] is False and "violated" not in entry and "unavailable" in entry["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_file_immutability_known_changed_and_vanished_files_survive_an_incomplete_record():
    reqs = derive_requirements("Fix it.\nDo not modify README.md.")
    frozen = {"REQ-2": ["README.md"]}
    for evidence, present in (({"unavailable": "OSError: boom", "known_changed_paths": ["README.md"]}, ["README.md"]),
                              (NOTHING_KNOWN, [])):
        ledger = unverified_ledger(reqs)
        [entry] = close_file_immutability_requirements(
            ledger, reqs, frozen_paths_by_requirement=frozen, present_paths=present, scope_evidence=evidence, source="t", revision=1)
        assert entry["violated"] is True and entry["closed"] is False
        assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.VIOLATED


@pytest.mark.parametrize("evidence", [{"unavailable": "OSError: boom", "known_changed_paths": ["src/x.py"]}, NOTHING_KNOWN, None],
                         ids=["partial", "nothing-known", "not-collected"])
def test_file_immutability_an_incomplete_record_without_a_violation_never_closes(evidence):
    reqs = derive_requirements("Fix it.\nDo not modify README.md.")
    ledger = unverified_ledger(reqs)
    [entry] = close_file_immutability_requirements(
        ledger, reqs, frozen_paths_by_requirement={"REQ-2": ["README.md"]}, present_paths=["README.md"], scope_evidence=evidence,
        source="t", revision=1)
    assert entry["closed"] is False and "violated" not in entry and "unavailable" in entry["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


# A green suite whose statement demands the existing tests stay unchanged (suite preservation shares the same mutation record).
SUITE_UNCHANGED = "Fix it.\nAll existing tests must keep passing unchanged."


def _suite_closure(tmp_path, immutability):
    root = write_project(tmp_path / "ws", "def test_a():\n    assert True\n\ndef test_b():\n    assert 1 == 1\n")
    reqs = derive_requirements(SUITE_UNCHANGED)
    ledger = unverified_ledger(reqs)
    [entry] = close_suite_preservation_requirements(
        ledger, reqs, test_files=["tests/test_suite.py"], run_suite=lambda: validator_for(root).run_tests(), source="t", revision=1,
        test_immutability=immutability)
    return entry, requirement_outcomes(ledger, reqs)["REQ-2"]


def test_suite_preservation_a_known_changed_test_survives_an_incomplete_record(tmp_path):
    incomplete = {"available": False, "reason": "mutation evidence unavailable: OSError: boom",
                  "changed_test_files": ["tests/test_a.py"], "missing_test_files": [], "changed_trust_surface": []}
    entry, outcome = _suite_closure(tmp_path, incomplete)
    assert entry["violated"] is True and entry["closed"] is False and outcome is RequirementOutcome.VIOLATED


def test_suite_preservation_an_incomplete_record_without_a_violation_never_closes(tmp_path):
    incomplete = {"available": False, "reason": "mutation evidence unavailable: OSError: boom",
                  "changed_test_files": [], "missing_test_files": [], "changed_trust_surface": []}
    entry, outcome = _suite_closure(tmp_path, incomplete)
    assert entry["closed"] is False and "violated" not in entry and "needs the run's mutation record" in entry["reason"]
    assert outcome is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("situation", ["zero-tests", "incomplete-report", "attribution-raised", "no-attribution"])
@pytest.mark.parametrize("record", [
    {"available": True, "changed_test_files": ["tests/test_suite.py"], "missing_test_files": [], "changed_trust_surface": []},
    {"available": True, "changed_test_files": [], "missing_test_files": ["tests/test_other.py"], "changed_trust_surface": []},
], ids=["changed", "vanished"])
def test_suite_preservation_a_known_changed_or_vanished_test_convicts_whatever_the_suite_did(tmp_path, situation, record):
    """Addendum #5 (consumer level, pre-existing): the conviction used to be reached only in the final branch, after the report,
    case-count and attribution checks - so a COMPLETE record proving a test changed or vanished was dropped when the suite ran zero
    tests, its report was incomplete, or the attribution raised or was unavailable. The mutation record does not depend on the suite."""
    reqs = derive_requirements(SUITE_UNCHANGED)
    ledger = unverified_ledger(reqs)
    attribute = None
    if situation == "zero-tests":
        run_suite = lambda: validator_for(write_project(tmp_path / "empty", "x = 1\n")).run_tests()          # noqa: E731
    elif situation == "incomplete-report":
        stub = {"success": True, "output": "2 passed", "test_execution": {"gate_id": "g", "runner": "pytest", "workspace": "w",
                                                                           "completeness": "INDETERMINATE", "reason": "STRUCTURED_REPORT_MISSING"}}
        run_suite = lambda: stub                                                                              # noqa: E731
    else:
        run_suite = lambda: validator_for(write_project(tmp_path / "failing", "def test_a():\n    assert False\n")).run_tests()  # noqa: E731
        if situation == "attribution-raised":
            def attribute(_result):
                raise RuntimeError("attribution broke")
    [entry] = close_suite_preservation_requirements(ledger, reqs, test_files=[], run_suite=run_suite, source="t", revision=1,
                                                    test_immutability=record, attribute_suite=attribute)
    assert entry["violated"] is True and entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.VIOLATED


SUITE_NO_CUE = "Fix it.\nAll existing tests must keep passing."          # no "unchanged"/"intact"/...: the tests are not frozen
TOUCHED = {"available": True, "changed_test_files": ["tests/test_suite.py"], "missing_test_files": ["tests/test_other.py"],
           "changed_trust_surface": []}


def _suite_outcome(tmp_path, goal, record, *, claims=None):
    reqs = derive_requirements(goal)
    ledger = unverified_ledger(reqs)
    empty = validator_for(write_project(tmp_path / "empty", "x = 1\n"))            # zero executed tests: the suite itself proves nothing
    [entry] = close_suite_preservation_requirements(
        ledger, reqs, test_files=[], run_suite=lambda: empty.run_tests(), source="t", revision=1, test_immutability=record,
        contract_claims=claims)
    return entry, requirement_outcomes(ledger, reqs)["REQ-2"]


def test_suite_preservation_a_changed_test_does_not_convict_a_statement_that_does_not_ask_for_unchanged_tests(tmp_path):
    """The conviction is a consequence of the STATEMENT: tests are frozen only where it says so. "All existing tests must keep
    passing" lets a candidate edit a test; the touched list alone convicts nothing (here the empty suite leaves it open)."""
    entry, outcome = _suite_outcome(tmp_path, SUITE_NO_CUE, TOUCHED)
    assert "violated" not in entry and entry["closed"] is False
    assert outcome is RequirementOutcome.UNVERIFIED


def test_suite_preservation_a_compound_statement_is_not_convicted_by_the_suite_closer_alone(tmp_path):
    """A statement that also carries other claims (here: documentation) is judged by their producers; the suite closer convicts only a
    statement whose claims are the regression/immutability pair, so a compound one is never over-convicted from here."""
    from kriya.workflow.requirements import DOCUMENTATION_CLAIM, REGRESSION_PRESERVATION

    entry, outcome = _suite_outcome(tmp_path, SUITE_UNCHANGED, TOUCHED, claims={"REQ-2": (REGRESSION_PRESERVATION, DOCUMENTATION_CLAIM)})
    assert "violated" not in entry and entry["closed"] is False
    assert outcome is RequirementOutcome.UNVERIFIED


def test_suite_preservation_control_the_same_record_convicts_a_statement_that_does_ask_for_unchanged_tests(tmp_path):
    entry, outcome = _suite_outcome(tmp_path, SUITE_UNCHANGED, TOUCHED)
    assert entry["violated"] is True and outcome is RequirementOutcome.VIOLATED


def test_suite_preservation_control_a_complete_clean_record_closes(tmp_path):
    clean = {"available": True, "changed_test_files": [], "missing_test_files": [], "changed_trust_surface": []}
    entry, outcome = _suite_closure(tmp_path, clean)
    assert entry["closed"] is True and outcome is RequirementOutcome.CLOSED_BY_EVIDENCE


# ------------------------------------------------------------------------------- the producer (real git)

def _deps(run_committed_paths):
    def unused(*_args, **_kwargs):
        raise AssertionError("not used by this closer collaborator")
    import logging
    from unittest.mock import MagicMock
    return CloserDeps(
        logger=MagicMock(spec=logging.Logger), run_committed_paths=run_committed_paths, compute_workspace_content_hash=unused,
        captured_baseline=unused, attribute_suite_result=unused, post_environment_identity=unused,
        load_baseline_environment_identity=unused, load_authority_bundle=unused)


def _repo(tmp_path):
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return str(tmp_path)


def _head(root):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("failing_git_read", ["rev-parse", "ls-tree", "diff", "ls-files"])
def test_the_producer_keeps_the_committed_history_when_a_later_git_read_fails(tmp_path, monkeypatch, failing_git_read):
    root = _repo(tmp_path)
    head = _head(root)
    real = worktree.git_read_lines

    def lines(cwd, *args, **kwargs):
        if args[0] == failing_git_read:
            raise subprocess.CalledProcessError(128, ["git", *args])
        return real(cwd, *args, **kwargs)
    monkeypatch.setattr(worktree, "git_read_lines", lines)
    tracked, evidence = requirement_closers.mutation_scope_evidence(
        _deps(lambda _workspace: ("run-1", head, ["other.txt"])), root, root, candidate_paths=["a.py"])
    assert tracked == [] and "actual_paths" not in evidence                       # incomplete: no claim of "everything the run changed"
    assert evidence["unavailable"].startswith("CalledProcessError: ")             # the reason names the class
    assert evidence["known_changed_paths"] == ["other.txt"] and evidence["committed_history"] == ["other.txt"]
    assert evidence["run_id"] == "run-1" and evidence["base_revision"] == head
    view = scope_evidence_view(evidence)
    assert not view.complete and view.changed == {"other.txt"}


def test_the_producer_keeps_the_committed_history_when_the_candidate_is_not_at_the_run_base(tmp_path):
    root = _repo(tmp_path)
    _tracked, evidence = requirement_closers.mutation_scope_evidence(
        _deps(lambda _workspace: ("run-1", "0" * 40, ["other.txt"])), root, root, candidate_paths=[])
    assert "not the run base" in evidence["unavailable"] and evidence["known_changed_paths"] == ["other.txt"]


def test_the_producer_with_an_unreadable_history_knows_nothing_and_says_so(tmp_path):
    root = _repo(tmp_path)

    def unreadable(_workspace):
        raise OSError("disk vanished")
    _tracked, evidence = requirement_closers.mutation_scope_evidence(_deps(unreadable), root, root, candidate_paths=[])
    assert evidence == {"unavailable": "OSError: disk vanished"}


@pytest.mark.parametrize("failing", ["history", "git"])
def test_a_kriya_coding_error_in_the_producers_collaborators_is_not_degraded(tmp_path, monkeypatch, failing):
    """C of the owner's rule: an AttributeError reaching the trusted helper propagates to the attempt classifier (Q5)."""
    root = _repo(tmp_path)
    head = _head(root)
    error = AttributeError("'NoneType' object has no attribute 'splitlines'")

    def history(_workspace):
        if failing == "history":
            raise error
        return "run-1", head, ["other.txt"]
    if failing == "git":
        real = worktree.git_read_lines

        def lines(cwd, *args, **kwargs):
            if args[0] == "ls-tree":
                raise error
            return real(cwd, *args, **kwargs)
        monkeypatch.setattr(worktree, "git_read_lines", lines)
    with pytest.raises(AttributeError) as raised:
        requirement_closers.mutation_scope_evidence(_deps(history), root, root, candidate_paths=[])
    assert raised.value is error


def test_control_a_readable_record_is_complete(tmp_path):
    root = _repo(tmp_path)
    head = _head(root)
    tracked, evidence = requirement_closers.mutation_scope_evidence(
        _deps(lambda _workspace: ("run-1", head, ["other.txt"])), root, root, candidate_paths=["a.py"])
    assert tracked == ["a.py"] and "unavailable" not in evidence and evidence["actual_paths"] == ["other.txt"]
    assert scope_evidence_view(evidence).complete


# ------------------------------------------------------------------------------- the pure git/file helpers (trusted Kriya code)

def _failing_git(monkeypatch, error):
    def lines(_cwd, *_args, **_kwargs):
        raise error
    monkeypatch.setattr(worktree, "git_read_lines", lines)


@pytest.mark.parametrize("error", [OSError("git missing"), subprocess.CalledProcessError(128, ["git"]), subprocess.TimeoutExpired("git", 60),
                                   UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")],
                         ids=["OSError", "CalledProcessError", "TimeoutExpired", "UnicodeDecodeError"])
def test_the_helpers_degrade_an_operational_git_failure(tmp_path, monkeypatch, error):
    """B/C: no prior conclusion exists in these helpers; an operational failure is the existing unavailable semantic."""
    _failing_git(monkeypatch, error)
    assert requirement_closers._tracked_workspace_paths(str(tmp_path)) == []       # pylint: disable=protected-access
    assert requirement_closers._workspace_head(str(tmp_path)) is None              # pylint: disable=protected-access
    assert requirement_closers._oracle_base_revision(                              # pylint: disable=protected-access
        _deps(lambda _workspace: (None, None, [])), str(tmp_path), str(tmp_path)) is None


@pytest.mark.parametrize("error", [AttributeError("'NoneType' object has no attribute 'splitlines'"), NameError("name 'x' is not defined")],
                         ids=["AttributeError", "NameError"])
def test_the_helpers_do_not_swallow_a_kriya_coding_error(tmp_path, monkeypatch, error):
    """E: a genuine internal error in a trusted helper is not turned into 'unavailable' - it propagates."""
    _failing_git(monkeypatch, error)
    for call in (lambda: requirement_closers._tracked_workspace_paths(str(tmp_path)),       # pylint: disable=protected-access
                 lambda: requirement_closers._workspace_head(str(tmp_path)),                 # pylint: disable=protected-access
                 lambda: requirement_closers._oracle_base_revision(                          # pylint: disable=protected-access
                     _deps(lambda _workspace: (None, None, [])), str(tmp_path), str(tmp_path))):
        with pytest.raises(type(error)) as raised:
            call()
        assert raised.value is error


def test_an_empty_git_answer_is_handled_explicitly_not_through_an_index_error(tmp_path, monkeypatch):
    """IndexError is never a rule: empty output is a state the helpers represent directly."""
    monkeypatch.setattr(worktree, "git_read_lines", lambda *_args, **_kwargs: [])
    assert requirement_closers._workspace_head(str(tmp_path)) is None              # pylint: disable=protected-access
    assert requirement_closers._oracle_base_revision(                              # pylint: disable=protected-access
        _deps(lambda _workspace: (None, None, [])), str(tmp_path), str(tmp_path)) is None
    _tracked, evidence = requirement_closers.mutation_scope_evidence(
        _deps(lambda _workspace: ("run-1", "b" * 40, ["other.txt"])), str(tmp_path), str(tmp_path), candidate_paths=[])
    assert "no HEAD revision" in evidence["unavailable"] and evidence["known_changed_paths"] == ["other.txt"]


@pytest.mark.parametrize("error", [AttributeError("'NoneType' object has no attribute 'x'"), NameError("x")], ids=["AttributeError", "NameError"])
def test_the_base_tree_sites_do_not_swallow_a_kriya_coding_error(tmp_path, monkeypatch, error):
    from kriya.workflow import named_test_oracle

    class Boom:
        def __init__(self, *_args, **_kwargs):
            raise error
    monkeypatch.setattr(named_test_oracle, "BaseTree", Boom)

    class Entry:
        required_claims = ("TEST_IMMUTABILITY",)
    contract = type("Contract", (), {"entries": [Entry()], "named_preserved_test_files": ()})()
    monkeypatch.setattr(requirement_closers, "_workspace_head", lambda _workspace: "a" * 40)
    monkeypatch.setattr(requirement_closers, "named_preserved_test_files", lambda _contract: [])
    monkeypatch.setattr("kriya.workflow.requirements.TEST_IMMUTABILITY_CLAIM", "TEST_IMMUTABILITY")
    with pytest.raises(type(error)):
        requirement_closers.immutable_test_files(_deps(lambda _workspace: (None, None, [])), contract, str(tmp_path))
    monkeypatch.setattr(requirement_closers, "_oracle_base_revision", lambda *_args: "a" * 40)
    with pytest.raises(type(error)):
        requirement_closers._reference_test_files(                                  # pylint: disable=protected-access
            _deps(lambda _workspace: (None, None, [])), str(tmp_path), str(tmp_path / "other"))


# ------------------------------------------------------------------------------- end to end: the measured scenario

GOAL = ("Add a function sub(a, b) returning a - b to `calc.py`.\n\n>>> from calc import sub\n>>> sub(5, 3)\n2\n\n"
        "Do not modify any other file in the repository.\n")


def _direct_run(tmp_path, monkeypatch, *, violation: bool, git_error: BaseException = None):
    """The review's scenario on the real direct path (policy ``record``, the default): the verifier model says `satisfied` for
    everything; a deterministic violation is planted in the run's committed history (a file outside the authorized calc.py); a
    later git read of the evidence producer fails with ``git_error``."""
    eng, _cfg, developer_calls = _scripted_engine(lambda n: GOOD, configure=lambda cfg: setattr(cfg.autonomy, "spec_compliance_enabled", True))
    llm = next(v for v in vars(eng).values() if hasattr(v, "complete") and callable(v.complete))
    scripted = llm.complete

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        text = f"{system_prompt or ''}\n{user_prompt or ''}"
        if '"compliant"' in text:
            ids = sorted(set(re.findall(r"\bREQ-\d+\b", text)))
            return json.dumps({"compliant": True, "reasoning": "looks right", "missing_requirements": [], "likely_files": [],
                               "requirement_verdicts": [{"id": rid, "verdict": "satisfied", "evidence": "model says so"} for rid in ids]})
        return await scripted(system_prompt, user_prompt, *args, **kwargs)
    llm.complete = complete
    injected = []
    if violation:
        real_committed = workflow_module._run_committed_paths  # pylint: disable=protected-access

        def committed(workspace_path):
            run_id, base, paths = real_committed(workspace_path)
            return run_id, base, [*paths, "other.txt"]
        monkeypatch.setattr(workflow_module, "_run_committed_paths", committed)
    if git_error is not None:
        real_lines = worktree.git_read_lines

        def lines(cwd, *args, **kwargs):
            if args[:1] == ("ls-tree",) and "--name-only" in args:
                injected.append(args)
                raise git_error
            return real_lines(cwd, *args, **kwargs)
        monkeypatch.setattr(worktree, "git_read_lines", lines)
    result = _run(eng, _workspace(tmp_path), goal=GOAL)
    return result, injected, developer_calls


def _scope_requirement(result):
    """(requirement id, its outcome, its recorded evidence) for the goal's "Do not modify any other file" statement."""
    requirements = result["requirements"]
    [rid] = [r["id"] for r in requirements["requirements"] if r["text"].startswith("Do not modify any other file")]
    return rid, requirements["outcomes"][rid], (requirements.get("evidence") or {}).get(rid) or {}


def test_e2e_control_a_clean_run_passes(tmp_path, monkeypatch):
    result, _injected, _calls = _direct_run(tmp_path, monkeypatch, violation=False)
    assert result["quality_gates_passed"] is True
    assert _scope_requirement(result)[1] == RequirementOutcome.CLOSED_BY_EVIDENCE.value


def test_e2e_a_deterministic_violation_alone_fails_the_gate(tmp_path, monkeypatch):
    result, _injected, _calls = _direct_run(tmp_path, monkeypatch, violation=True)
    assert result["quality_gates_passed"] is not True
    assert _scope_requirement(result)[1] == RequirementOutcome.VIOLATED.value


@pytest.mark.parametrize("error", [OSError("git ls-tree: I/O error"), subprocess.CalledProcessError(128, ["git", "ls-tree"])],
                         ids=["OSError", "CalledProcessError"])
def test_e2e_a_violation_plus_an_operational_git_failure_is_still_a_failed_gate(tmp_path, monkeypatch, error):
    """B: the measured flip (False/violated -> True/unverified) is gone; the evidence is incomplete AND the violation survives."""
    result, injected, _calls = _direct_run(tmp_path, monkeypatch, violation=True, git_error=error)
    assert injected, "the injected git read did not run"
    assert result["quality_gates_passed"] is not True
    _rid, outcome, evidence = _scope_requirement(result)
    assert outcome == RequirementOutcome.VIOLATED.value
    assert evidence["evidence_complete"] is False and type(error).__name__ in evidence["evidence_unavailable"]
    assert evidence["out_of_scope_paths"] == ["other.txt"]


def test_e2e_a_violation_plus_a_kriya_coding_error_is_an_internal_stop_not_a_pass(tmp_path, monkeypatch):
    """A: the AttributeError reaches the single attempt classifier (internal_framework_error, no Developer retry)."""
    result, injected, developer_calls = _direct_run(tmp_path, monkeypatch, violation=True,
                                                    git_error=AttributeError("'NoneType' object has no attribute 'splitlines'"))
    assert injected
    assert result["quality_gates_passed"] is not True
    assert (result["last_failure"]["type"],) == ("internal_framework_error",)
    assert len(developer_calls) == 1


def test_e2e_no_violation_plus_an_operational_git_failure_is_unverified_never_satisfied(tmp_path, monkeypatch):
    """C: no counter-evidence was ever established; the record is incomplete, so the requirement is not closed by evidence. The
    result then follows requirement_unknown_policy (record by default), exactly as before the change."""
    result, injected, _calls = _direct_run(tmp_path, monkeypatch, violation=False, git_error=OSError("git ls-tree: I/O error"))
    assert injected
    _rid, outcome, _evidence = _scope_requirement(result)
    assert outcome == RequirementOutcome.UNVERIFIED.value
