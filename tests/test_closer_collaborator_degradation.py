"""INTENTIONAL CONTRACT (consolidated addendum #3, step D): a closer's guarded git/I-O collaborator that fails for ANY reason - a
coding error included - degrades to "unavailable", and "unavailable" is never evidence for a requirement: its consumers fail closed
(the requirement stays unverified, the reason names the exception class). The terminal result then follows the operator's
``requirement_unknown_policy`` (``record`` by default, ``block`` in the strict profiles). The defect class this pins out is the
opposite one: an error that makes the requirement look SATISFIED.

The typed S19 refusal (RepositoryContentFilterRefused) is not degraded: it has its own pins (test_fs1c0_named_test_oracle,
test_s19e_honest_refusal)."""
import logging
import subprocess
from unittest.mock import MagicMock

import pytest

from kriya.workflow import requirement_closers
from kriya.workflow.requirement_closers import CloserDeps
from kriya.workflow.requirements import test_immutability_evidence as immutability_evidence


def _deps(run_committed_paths) -> CloserDeps:
    def unused(*_args, **_kwargs):
        raise AssertionError("not used by this closer collaborator")
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


@pytest.mark.parametrize("error", [AttributeError("'NoneType' object has no attribute 'x'"), OSError("disk vanished")],
                         ids=["AttributeError", "OSError"])
def test_a_failing_run_record_read_makes_the_mutation_evidence_unavailable_and_never_available(tmp_path, error):
    def failing(_workspace):
        raise error
    root = _repo(tmp_path)
    tracked, evidence = requirement_closers.mutation_scope_evidence(_deps(failing), root, root, candidate_paths=["a.py"])
    assert tracked == [] and list(evidence) == ["unavailable"]
    assert evidence["unavailable"].startswith(f"{type(error).__name__}: ")             # the reason names the class
    # the consumer fails closed: unavailable evidence is never a verdict about the tests the run left alone
    judged = immutability_evidence(["tests/test_a.py"], ["tests/test_a.py"], evidence)
    assert judged["available"] is False and type(error).__name__ in judged["reason"]


def test_a_failing_run_record_read_leaves_the_named_test_oracle_without_a_base(tmp_path):
    def failing(_workspace):
        raise AttributeError("'NoneType' object has no attribute 'x'")
    not_a_repository = str(tmp_path)
    assert requirement_closers._oracle_base_revision(      # pylint: disable=protected-access
        _deps(failing), not_a_repository, not_a_repository) is None                 # the oracle then refuses (UNVERIFIED)


def test_control_a_readable_run_record_yields_available_evidence(tmp_path):
    root = _repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    tracked, evidence = requirement_closers.mutation_scope_evidence(
        _deps(lambda _workspace: ("run-1", head, [])), root, root, candidate_paths=["a.py"])
    assert tracked == ["a.py"] and "unavailable" not in evidence and evidence["base_revision"] == head
