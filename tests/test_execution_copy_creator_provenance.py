"""D2 S3 defect fix (found during S7, pre-existing at 6d099ca): execution-copy ownership is declared by the component that CREATES the copy.

The defect: ``kriya/workflow/workflow.py`` built validators for the contract-acceptance copy (``<state>/acceptance-runs/<id>/workspace``) and the
authority-bundle / baseline-authority exports with factories that did not carry ownership, so under containment on a git checkout the copy was refused
``GIT_METADATA_TARGET_NOT_OWNED`` and the check was UNAVAILABLE (the requirement_closers factories had been patched one by one). The rule now:

- ``PolymorphicValidator.declare_execution_copy(created_path)`` is the only way a git-less tree becomes ownable; the constructor no longer takes it;
- the three creators (run_java_acceptance, run_authority_bundle, judge_named_tests) call it on the validator their factory returned, for the copy THEY made;
- a git-less tree nobody declared stays refused; the declaration cannot name another tree or the run's original workspace;
- the two replay copies (deterministic_failure_diagnostic, regression_attribution) are git-less but bind no Git metadata (their original IS the copy), so they
  need - and make - no declaration.
Cleanup of the copies on every outcome is pinned by tests/test_s3_execution_views.py (its probe is now built exactly like a production factory builds it).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_oi142_gate_view import _repo

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import declare_execution_copy
from kriya.tools.git_metadata import GIT_METADATA_TARGET_NOT_OWNED, GitMetadataError
from kriya.tools.validate import PolymorphicValidator


def _contained() -> AutonomyConfig:
    return AutonomyConfig(contained_execution_required=True, containment_backend="oci")


def _git_less_copy(tmp_path, origin, name="copy") -> Path:
    copy = tmp_path / name
    shutil.copytree(origin, copy, ignore=shutil.ignore_patterns(".git"))
    return copy


def _contained_gate(self, seen, name="gate"):
    profile, _backend = self.build_containment_profile_and_backend()
    seen.append({"gate": name, "root": self.workspace_path, "bound": profile.git_metadata is not None,
                 "git_target": os.path.isdir(os.path.join(self.workspace_path, ".git"))})


# ------------------------------------------------------------------ the declaration itself

def test_the_creator_declaration_makes_a_git_less_copy_ownable_and_only_that_copy(tmp_path):
    origin = _repo(tmp_path)
    copy = _git_less_copy(tmp_path, origin)
    validator = PolymorphicValidator(str(copy), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with pytest.raises(GitMetadataError, match=GIT_METADATA_TARGET_NOT_OWNED):          # NEGATIVE CONTROL: before the declaration it is refused
        validator.build_containment_profile_and_backend()
    validator.declare_execution_copy(str(copy))
    profile, _backend = validator.build_containment_profile_and_backend()
    assert profile.git_metadata is not None and (copy / ".git").is_dir()


def test_a_git_less_tree_nobody_declared_stays_refused_and_nothing_is_created_in_it(tmp_path):
    origin = _repo(tmp_path)
    foreign = _git_less_copy(tmp_path, origin, name="a-staged-candidate-kriya-did-not-create")
    validator = PolymorphicValidator(str(foreign), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with pytest.raises(GitMetadataError, match=GIT_METADATA_TARGET_NOT_OWNED):
        validator.build_containment_profile_and_backend()
    assert not (foreign / ".git").exists()


def test_a_path_under_a_kriya_state_directory_is_not_ownable_by_location(tmp_path, monkeypatch):
    """Ownership is never inferred from where a tree lives: a git-less tree under the state root that no creator declared is refused like any other."""
    state = tmp_path / "state"
    monkeypatch.setenv("KRIYA_STATE_DIR", str(state))
    origin = _repo(tmp_path)
    inside_state = _git_less_copy(state / "acceptance-runs" / "abc", origin, name="workspace")
    validator = PolymorphicValidator(str(inside_state), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with pytest.raises(GitMetadataError, match=GIT_METADATA_TARGET_NOT_OWNED):
        validator.build_containment_profile_and_backend()


def test_a_declaration_cannot_name_another_tree_or_the_original_workspace(tmp_path):
    origin = _repo(tmp_path)
    copy = _git_less_copy(tmp_path, origin)
    other = _git_less_copy(tmp_path, origin, name="other")
    on_copy = PolymorphicValidator(str(copy), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with pytest.raises(ValueError, match="not this validator's tree"):
        on_copy.declare_execution_copy(str(other))
    on_origin = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with pytest.raises(ValueError, match="never an execution copy"):
        on_origin.declare_execution_copy(str(origin))
    assert on_copy._owned_trees == set() and on_origin._owned_trees == set()               # pylint: disable=protected-access


def test_a_real_git_work_tree_is_unchanged_by_a_declaration_and_the_constructor_takes_no_ownership(tmp_path):
    origin = _repo(tmp_path)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(worktree)], cwd=origin, check=True)
    validator = PolymorphicValidator(str(worktree), original_workspace_path=str(origin), autonomy_cfg=_contained())
    validator.declare_execution_copy(str(worktree))
    assert validator._pinned_non_git is False and validator._owned_trees == set()           # pylint: disable=protected-access
    profile, _backend = validator.build_containment_profile_and_backend()                   # a real worktree has its own .git: nothing prepared, nothing refused
    assert profile.git_metadata is not None
    with pytest.raises(TypeError):
        PolymorphicValidator(str(worktree), execution_copy=True)                            # pylint: disable=unexpected-keyword-arg


def test_a_stand_in_that_executes_nothing_has_nothing_to_declare(tmp_path):
    declare_execution_copy(object(), str(tmp_path))                                         # no error: an execution-less double is not a validator


# ------------------------------------------------------------------ each creator, through a factory built the way production builds it

def test_the_production_contract_acceptance_copy_works_under_explicit_containment(tmp_path, monkeypatch):
    """workflow._run_contract_acceptance is the exact production path that returned UNAVAILABLE: its factory builds a plain validator over the
    acceptance-runs copy. The creator (run_java_acceptance) must make that copy ownable."""
    from _b2c_fixtures import ACCEPTANCE, EXACT_GOAL, TARGET, candidate, maven_workspace

    from kriya.workflow import acceptance_oracle as ao
    from kriya.workflow import workflow as wf
    from kriya.workflow.requirements import derive_requirements

    workspace = maven_workspace(tmp_path / "ws")
    cand = candidate(workspace, tmp_path / "cand", "correct")
    (tmp_path / "KriyaAcceptanceTest.java").write_text(ACCEPTANCE)
    artifact = ao.load_acceptance(str(tmp_path / "KriyaAcceptanceTest.java"), derive_requirements(EXACT_GOAL), str(tmp_path / "artifacts"))
    seen = []

    def compile_gate(self, *_args, **_kwargs):
        _contained_gate(self, seen, "compile")
        return {"success": False, "output": "probe: nothing is compiled"}

    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", compile_gate)
    wf._run_contract_acceptance(artifact, [TARGET], candidate_root=str(cand), workspace_path=str(workspace), autonomy_cfg=_contained())   # pylint: disable=protected-access
    assert len(seen) == 1 and seen[0]["bound"] and seen[0]["git_target"], seen
    assert "acceptance-runs" in seen[0]["root"] and not os.path.lexists(seen[0]["root"]), "the copy, with its .git target, did not outlive its run"


def test_the_authority_bundle_export_works_under_explicit_containment(tmp_path):
    from test_verification_contract_003_authority import _loaded

    from kriya.workflow import authority_bundle as ab

    ws, _reqs, bundle = _loaded(tmp_path)
    seen = []

    class AuthorityProbe(PolymorphicValidator):
        def _run_cmd_with_timeout(self, cmd, cwd, timeout=300, stdin_payload=None, **kwargs):      # pylint: disable=unused-argument
            _contained_gate(self, seen, "authority")
            return {"returncode": 0, "stdout": "ok", "stderr": "", "timeout": False}

    run = ab.run_authority_bundle(bundle, str(ws), state_root=str(tmp_path / "state"),
                                  validator_factory=lambda root: AuthorityProbe(root, original_workspace_path=str(ws), autonomy_cfg=_contained()))
    assert seen and all(event["bound"] and event["git_target"] for event in seen), (seen, run.reason_code, run.reason)
    assert not os.path.lexists(seen[0]["root"]), "the export did not outlive its run"


# ------------------------------------------------------------------ the two replay copies: git-less, but no Git metadata is ever bound to them

def test_the_deterministic_replay_copy_binds_no_git_metadata_and_needs_no_declaration(tmp_path, monkeypatch):
    from kriya.workflow.deterministic_failure_diagnostic import replay_deterministic_verification_against_baseline

    origin = _repo(tmp_path)
    seen = []

    def compile_gate(self, *_args, **_kwargs):
        assert not is_git_tree(self.workspace_path), "premise: the replay copy is git-less"
        _contained_gate(self, seen, "replay")
        return {"success": True, "output": "ok"}

    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", compile_gate)
    result = replay_deterministic_verification_against_baseline(fail_type="compile", authoritative_workspace_path=str(origin), known_files=["calc.py"],
                                                                autonomy_cfg=_contained())
    assert result["success"] is True and seen and seen[0]["bound"] is False and seen[0]["git_target"] is False, seen


def test_the_regression_replay_copy_binds_no_git_metadata_and_needs_no_declaration(tmp_path, monkeypatch):
    from kriya.workflow.regression_attribution import _replay_test_ids_against_workspace

    origin = _repo(tmp_path)
    seen = []

    def run_tests(self, *_args, **_kwargs):
        assert not is_git_tree(self.workspace_path), "premise: the replay copy is git-less"
        _contained_gate(self, seen, "replay")
        return {"success": True, "output": "ok"}

    monkeypatch.setattr(PolymorphicValidator, "run_tests", run_tests)
    result = _replay_test_ids_against_workspace(test_ids=["tests/test_calc.py::test_double"], workspace_path=str(origin), autonomy_cfg=_contained())   # pylint: disable=protected-access
    assert result["success"] is True and seen and seen[0]["bound"] is False and seen[0]["git_target"] is False, seen


# ------------------------------------------------------------------ the original symptom, through the real runner (real OCI, real Maven)

def _docker_ready(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        if subprocess.run(["docker", "info"], capture_output=True, timeout=15, check=False).returncode != 0:
            return False
        listed = subprocess.run(["docker", "image", "ls", "-q", image], capture_output=True, text=True, timeout=30, check=False)
        return listed.returncode == 0 and bool(listed.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _docker_ready("maven:3.9-eclipse-temurin-17"), reason="real OCI tier: docker daemon or the canonical image maven:3.9-eclipse-temurin-17 is not available")
def test_real_oci_the_contract_feedback_runner_passes_and_violates_under_explicit_containment(tmp_path):
    """The reproduced symptom (UNAVAILABLE with GIT_METADATA_TARGET_NOT_OWNED at 6d099ca), re-measured: the sealed goal-example class now runs in the Kriya-created
    acceptance copy inside the real container, PASSED on the base and VIOLATED (exact expected/observed) on a wrong candidate."""
    from types import SimpleNamespace

    from _b2c_fixtures import BASE_CALC, TARGET, maven_workspace

    from kriya.workflow import contract_feedback as cf
    from kriya.workflow import example_oracle_java as ej
    from kriya.workflow.requirements import derive_requirements
    from kriya.workflow.workflow import build_contract_feedback

    workspace = maven_workspace(tmp_path / "ws")
    goal = "Make Calc.twice double its argument.\n\nCalc.twice(2) -> 4\n\nCalc.twice(5) -> 10\n"
    artifact, report = ej.derive_java_example_artifact(goal, derive_requirements(goal), state_root=str(tmp_path / "state"), candidate_root=str(workspace))
    engine = SimpleNamespace(derived_examples=artifact, derived_examples_report=report)
    os.environ["KRIYA_STATE_DIR"] = str(tmp_path / "state")
    feedback = build_contract_feedback(engine, str(workspace), str(workspace), _contained())
    assert feedback is not None and [e.number for e in feedback.examples] == [1, 2]
    assert cf.evaluate(feedback, [TARGET]).status == "PASSED"
    (workspace / TARGET).write_text(BASE_CALC.replace("2 * value", "3 * value"))
    outcome = cf.evaluate(feedback, [TARGET])
    assert outcome.status == "VIOLATED" and {(v.expected, v.observed) for v in outcome.violations} == {("4", "6"), ("10", "15")}


def is_git_tree(path) -> bool:
    return os.path.lexists(os.path.join(path, ".git"))
