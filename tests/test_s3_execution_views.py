"""D2 EXEC-LAYER S3: where a contained gate runs, and who may be given a ``.git`` for the sanitized Git metadata.

MEASURED (real Docker, 2026-10-24): a bind mount onto a MISSING ``<workspace>/.git`` inside a bind-mounted workspace makes the Docker daemon create an
empty ``.git`` directory in the HOST workspace, which survives the run. Left in a tree whose repository identity is not Kriya's to change, that is
false repository state (``find_repository_root`` reads it as a work tree); and the second gate on the same tree then asked Git for the bound set of a
"repository" that is none (``GATE_VIEW_UNAVAILABLE: git ls-files failed: not a git repository`` - the R2 signature).

Two execution-view forms, one invariant - *containment's ``.git`` transport must never change Kriya's semantic classification of a workspace*:

A. normal verification: ``origin -> exact-bound gate view -> OCI workspace``. ``_open_gate_view`` used to return None whenever execution was contained,
   so the OCI workspace WAS the shared worktree (the first wrong decision). The mount target exists only inside the disposable view.
B. a Kriya-created disposable execution copy made without ``.git`` (the Java acceptance export, the authority-bundle scratch, the named-test base
   export) is its own execution view; its CREATOR declares it (``declare_execution_copy``) and its non-Git classification is fixed there. Only these - and a gate view and a tooling directory -
   may be given a ``.git`` mount target; any other tree is a typed refusal, and nothing is created in it.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_oi142_gate_view import CALC2, _repo
from test_oi142_gate_view_oci import _APP, _POM

from kriya.config.config import AutonomyConfig
from kriya.tools import gate_view
from kriya.tools.containment import MountSpec
from kriya.tools.containment_oci import VENV_CONTAINER_PATH
from kriya.tools.gate_view import GATE_VIEW_UNAVAILABLE, GateViewError, is_git_work_tree
from kriya.tools.git_metadata import GIT_METADATA_TARGET_NOT_OWNED, GitMetadataError
from kriya.tools.validate import PolymorphicValidator

REPO_ROOT = Path(__file__).resolve().parents[1]


def _contained(**fields) -> AutonomyConfig:
    return AutonomyConfig(contained_execution_required=True, containment_backend="oci", **fields)


def _copy_without_git(tmp_path, origin, name="copy") -> Path:
    """What a Kriya-created execution copy looks like: the candidate's files and no ``.git`` (every creator ignores it)."""
    copy = tmp_path / name
    shutil.copytree(origin, copy, ignore=shutil.ignore_patterns(".git"))
    return copy


def _head(root) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def _snapshot(root) -> dict:
    """Every path under root (including the whole .git) with its kind: what 'untouched' means."""
    seen = {}
    for directory, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(directory, name)
            seen[os.path.relpath(path, root)] = (os.path.isdir(path), os.lstat(path).st_mtime_ns)
    return seen


# ----------------------------------------------------------------- A0: the fixture repository is quiescent when it is snapshotted (hosted #5)

def _spawned_git_commands(trace_file) -> list:
    """The argv of every child process a git command started, from git's own trace2 event stream (the parent logs ``child_start`` synchronously, so no waiting)."""
    import json
    commands = []
    for line in trace_file.read_text().splitlines():
        event = json.loads(line)
        if event.get("event") == "child_start" and event.get("argv"):
            commands.append(list(event["argv"]))
    return commands


def test_the_fixture_repository_leaves_no_background_git_maintenance_behind(tmp_path, monkeypatch):
    """Hosted run 41785193323 / #5 (MEASURED on Linux git 2.56.0): ``git commit`` spawns ``git maintenance run --auto --detach``; for a moment it holds
    ``.git/objects/maintenance.lock`` and then touches ``.git/objects``, so a snapshot taken right after the fixture commit differs from one taken later with NO Kriya
    write involved. The fixture must not start it (the product-write assertion is untouched)."""
    trace = tmp_path / "trace2.jsonl"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    _repo(tmp_path)
    assert not [argv for argv in _spawned_git_commands(trace) if "maintenance" in argv], "the fixture commit started background maintenance"


def test_negative_control_a_plain_git_commit_does_start_detached_maintenance(tmp_path, monkeypatch):
    """The control that makes the test above non-vacuous: the same fixture commit WITHOUT the fixture's repo-local switches starts the detached maintenance."""
    trace = tmp_path / "trace2.jsonl"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "f").write_text("x")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "c"]):
        subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=x", *args], cwd=repo, check=True, capture_output=True)
    assert [argv for argv in _spawned_git_commands(trace) if "maintenance" in argv], "this git no longer starts background maintenance after a commit: the control no longer discriminates"


# ----------------------------------------------------------------- B: a Kriya-created execution copy keeps its classification

def test_an_execution_copy_stays_non_git_after_containment_prepared_its_git_target(tmp_path, monkeypatch):
    origin = _repo(tmp_path)
    copy = _copy_without_git(tmp_path, origin)
    origin_before = _snapshot(origin)
    validator = PolymorphicValidator(str(copy), original_workspace_path=str(origin), autonomy_cfg=_contained())
    validator.declare_execution_copy(str(copy))                                 # what the CREATOR of the copy does (S3 creator provenance)

    first, _backend = validator.build_containment_profile_and_backend()       # the first contained gate
    assert first.git_metadata is not None and first.git_metadata.base_revision == _head(origin)
    assert (copy / ".git").is_dir() and os.listdir(copy / ".git") == [], "the mount target exists, empty, created by Kriya"
    assert is_git_work_tree(str(copy)), "premise: the filesystem now reads the copy as a work tree"

    monkeypatch.setattr(gate_view, "visible_repository_paths", lambda *_a, **_k: pytest.fail("a non-git execution copy was asked for its Git bound set"))
    assert validator._open_gate_view() is None                                  # the second gate: classification did not change
    assert validator._enter_gate_scope("tests") is None and validator.workspace_path == str(copy)
    again, _backend = validator.build_containment_profile_and_backend()        # and a third gate can still build its profile
    assert again.workspace_path == str(copy)
    assert _snapshot(origin) == origin_before, "the original repository was not touched"


def test_negative_control_the_same_copy_without_the_declaration_is_reclassified_and_the_gate_fails_as_r2_did(tmp_path):
    """The pre-fix reproducer: the daemon's placeholder (made here by hand) in a copy nobody pinned makes the next gate ask Git about a non-repository."""
    origin = _repo(tmp_path)
    copy = _copy_without_git(tmp_path, origin)
    validator = PolymorphicValidator(str(copy), original_workspace_path=str(origin), autonomy_cfg=_contained())
    (copy / ".git").mkdir()
    with pytest.raises(GateViewError) as refusal:
        validator._open_gate_view()
    assert refusal.value.reason_code == GATE_VIEW_UNAVAILABLE and "not a git repository" in str(refusal.value)


def test_the_declaration_pins_only_a_tree_that_was_not_a_work_tree_when_the_validator_was_built(tmp_path):
    origin = _repo(tmp_path)
    git_tree = tmp_path / "already-git"
    shutil.copytree(origin, git_tree)
    declared_on_a_work_tree = PolymorphicValidator(str(git_tree), original_workspace_path=str(origin), autonomy_cfg=_contained())
    declared_on_a_work_tree.declare_execution_copy(str(git_tree))
    assert declared_on_a_work_tree._pinned_non_git is False and declared_on_a_work_tree._owned_trees == set()
    assert PolymorphicValidator(str(git_tree))._pinned_non_git is False


# ----------------------------------------------------------------- the ownership boundary

def test_a_tree_kriya_did_not_create_is_never_given_a_git_directory(tmp_path):
    origin = _repo(tmp_path)
    foreign = _copy_without_git(tmp_path, origin, name="staged-candidate")     # no .git, and nobody declared it Kriya's disposable copy
    before = _snapshot(foreign)
    validator = PolymorphicValidator(str(foreign), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with pytest.raises(GitMetadataError, match=GIT_METADATA_TARGET_NOT_OWNED):
        validator.build_containment_profile_and_backend()
    assert _snapshot(foreign) == before and not (foreign / ".git").exists()


def test_a_tree_with_its_own_git_is_not_touched_by_the_target_preparation(tmp_path):
    origin = _repo(tmp_path)
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    before = _snapshot(origin / ".git")
    profile, _backend = validator.build_containment_profile_and_backend()
    assert profile.git_metadata is not None and _snapshot(origin / ".git") == before


def test_the_tooling_directory_is_owned_only_while_the_call_that_made_it_runs(tmp_path):
    origin = _repo(tmp_path)
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    seen = {}

    def spy(_argv, **kwargs):
        mounted = os.path.realpath(kwargs["workspace_path"])
        seen["owned_during"] = mounted in validator._owned_trees
        seen["mounted"] = mounted
        profile, _backend = validator.build_containment_profile_and_backend(workspace_path=kwargs["workspace_path"])
        seen["target_prepared"] = os.path.isdir(os.path.join(mounted, ".git"))
        seen["bound"] = profile.git_metadata is not None
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}

    validator._run_cmd_with_timeout = spy                                       # type: ignore[method-assign]
    validator._acquire_maven_tooling(["compiler:compile"], str(tmp_path / "cache"), 10)
    assert seen["owned_during"] is True and seen["target_prepared"] is True and seen["bound"] is True
    assert seen["mounted"] not in validator._owned_trees and not os.path.lexists(seen["mounted"])


# ----------------------------------------------------------------- A: the normal path - the exact-bound gate view under containment

def test_a_contained_gate_runs_in_the_view_and_only_the_view_gets_the_git_target(tmp_path):
    origin = _repo(tmp_path)
    origin_before = _snapshot(origin)
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    seen = {}
    with validator.verification_session("tests"):
        view = validator.workspace_path
        assert view != str(origin) and os.path.basename(view).startswith("view-")
        profile, _backend = validator.build_containment_profile_and_backend()
        seen.update(view=view, workspace=profile.workspace_path, target=os.path.isdir(os.path.join(view, ".git")),
                    target_entries=os.listdir(os.path.join(view, ".git")), base=profile.git_metadata.base_revision)
    assert seen["workspace"] == seen["view"] and seen["target"] and seen["target_entries"] == []
    assert seen["base"] == _head(origin), "the sanitized export is the ORIGIN's identity, not an empty view's"
    assert not os.path.lexists(seen["view"]), "the view - and the mount target in it - is gone"
    assert validator._owned_trees == set(), "a closed view is no longer an owned tree"
    assert validator.workspace_path == str(origin) and _snapshot(origin) == origin_before


def test_a_view_is_bound_to_the_origins_identity_even_for_a_validator_built_without_an_original_workspace(tmp_path):
    origin = _repo(tmp_path)
    validator = PolymorphicValidator(str(origin), autonomy_cfg=_contained())
    with validator.verification_session("tests"):
        profile, _backend = validator.build_containment_profile_and_backend()
    assert profile.git_metadata is not None and profile.git_metadata.base_revision == _head(origin)


def test_the_origin_venv_is_mounted_into_the_view_read_only_and_writable_only_when_asked(tmp_path):
    origin = _repo(tmp_path)
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with validator.verification_session("tests"):
        (origin / ".kriya" / "venv").mkdir(parents=True)  # R3: the venv is built INSIDE the scope that uses it (one an earlier scope left is discarded on entry)
        read_only, _b = validator.build_containment_profile_and_backend()
        writable, _b = validator.build_containment_profile_and_backend(venv_writable=True)
        view = validator.workspace_path
        assert os.path.isdir(os.path.join(view, ".kriya", "venv")), "the mount target in the view is made by Kriya"
    host = os.path.realpath(origin / ".kriya" / "venv")
    assert read_only.additional_mounts == (MountSpec(host_path=host, container_path=VENV_CONTAINER_PATH, writable=False),)
    assert writable.additional_mounts == (MountSpec(host_path=host, container_path=VENV_CONTAINER_PATH, writable=True),)


def test_no_venv_mount_without_a_venv_or_outside_a_view(tmp_path):
    origin = _repo(tmp_path)
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with validator.verification_session("tests"):
        assert validator.build_containment_profile_and_backend()[0].additional_mounts == ()     # no venv directory yet
    (origin / ".kriya" / "venv").mkdir(parents=True)
    assert validator.build_containment_profile_and_backend()[0].additional_mounts == ()         # no view open: the venv is inside the mount itself


# ----------------------------------------------------------------- the permission cannot spread silently

def test_no_downstream_factory_can_declare_an_execution_copy_because_the_constructor_no_longer_takes_one(tmp_path):
    """The declaration belongs to the CREATOR of the copy (tests/architecture/test_execution_copy_creators.py pins the creators); a factory has no way to make it."""
    with pytest.raises(TypeError):
        PolymorphicValidator(str(tmp_path), execution_copy=True)    # pylint: disable=unexpected-keyword-arg


# ----------------------------------------------------------------- the same, through the REAL OCI runtime

def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_real_oci_two_gates_on_an_execution_copy_and_a_gate_in_a_view_never_put_a_git_into_the_origin(tmp_path, monkeypatch):
    """R2 end to end: two contained gates on a Kriya-created copy (the second used to fail on the first's placeholder), then a gate on the origin.
    The origin's ``.git`` is byte-for-byte what it was; the container sees the sanitized repository at the origin's commit."""
    origin = _repo(tmp_path, calc=CALC2, extra={"pom.xml": _POM, "src/main/java/demo/App.java": _APP})   # a Maven project: its toolchain image carries git
    origin_before = _snapshot(origin)
    copy = _copy_without_git(tmp_path, origin)
    with monkeypatch.context() as scoped:  # only the copy: the origin's own view legitimately asks Git for its bound set
        scoped.setattr(gate_view, "visible_repository_paths", lambda *_a, **_k: pytest.fail("the execution copy was asked for its Git bound set"))
        on_copy = PolymorphicValidator(str(copy), original_workspace_path=str(origin), autonomy_cfg=_contained())
        on_copy.declare_execution_copy(str(copy))
        answers = []
        for _gate in range(2):
            result = on_copy._run_cmd_with_timeout(["git", "rev-parse", "HEAD"], cwd=str(copy))
            answers.append((result["returncode"], result["stdout"].strip()))
        assert answers == [(0, _head(origin))] * 2, answers
        assert on_copy._open_gate_view() is None and (copy / ".git").is_dir()
        assert _snapshot(origin) == origin_before, "the original repository was not touched"

    in_view = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    with in_view.verification_session("tests"):
        result = in_view._run_cmd_with_timeout(["git", "rev-parse", "HEAD"], cwd=in_view.workspace_path)
        view = in_view.workspace_path
    assert (result["returncode"], result["stdout"].strip()) == (0, _head(origin)), result
    assert not os.path.lexists(view) and _snapshot(origin) == origin_before


# ----------------------------------------------------------------- a disposable copy is removed on every path (success, failure, timeout, exception)

@pytest.mark.parametrize("script", ["success", "failure", "timeout", "exception"])
def test_the_authority_bundle_copy_is_removed_on_every_path(tmp_path, script):
    from test_verification_contract_003_authority import _fail, _loaded, _ok, _Validator

    from kriya.workflow import authority_bundle as ab

    def timed_out(_cwd):
        return {"returncode": -9, "stdout": "", "stderr": "", "timeout": True}

    def raises(_cwd):
        raise RuntimeError("the container runtime died")

    steps = {"success": [_ok, _ok], "failure": [_ok, _fail], "timeout": [_ok, timed_out], "exception": [_ok, raises]}[script]
    ws, _reqs, bundle = _loaded(tmp_path)
    state = str(tmp_path / "state")
    try:
        ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda root: _Validator(root, list(steps)))
    except RuntimeError:
        assert script == "exception"
    scratch_root = os.path.join(state, ab.AUTHORITY_RUNS_DIR)
    assert not os.path.isdir(scratch_root) or os.listdir(scratch_root) == [], "no disposable copy outlives its run"


# ----------------------------------------------------------------- the same, for the Java acceptance copy and the named-test base export

class _CopyProbe(PolymorphicValidator):
    """The REAL validator a production factory returns for a Kriya-created execution copy (contained). It is built EXACTLY as the production factories build it -
    it declares nothing about the tree: the CREATOR of the copy (run_java_acceptance, run_authority_bundle, judge_named_tests) must. What it runs is scripted, but
    every gate first builds the REAL containment profile, which is what makes Kriya create the ``.git`` mount target in the copy. It records what existed then."""

    def __init__(self, root, *, origin, script, seen):
        super().__init__(root, original_workspace_path=str(origin), autonomy_cfg=_contained())
        self._script, self._seen = script, seen

    def _contained_gate(self, name):
        profile, _backend = self.build_containment_profile_and_backend()
        self._seen.append({"gate": name, "root": self.workspace_path, "git_target": os.path.isdir(os.path.join(self.workspace_path, ".git")),
                           "bound": profile.git_metadata is not None, "viewed": self._open_gate_view() is not None})
        return self._script(name)

    def run_compile_check(self, *_args, **_kwargs):
        return self._contained_gate("compile")

    def run_tests(self, *_args, **_kwargs):
        return self._contained_gate("tests")

    def _test_runner(self):
        return "pytest"


def _scripted(outcome):
    """The scripted gates: ``outcome`` is what the run-the-tests gate does (the compile gate passes)."""
    def script(name):
        if name == "compile":
            return {"success": True}
        if outcome == "success":
            return {"success": True}
        if outcome == "failure":
            return {"success": False, "stdout": "FAILED"}
        if outcome == "timeout":
            return {"success": False, "returncode": -9, "timeout": True}
        if outcome == "exception":
            raise RuntimeError("the container runtime died")
        raise asyncio.CancelledError()      # "cancellation": a BaseException, not an Exception
    return script


_OUTCOMES = ["success", "failure", "timeout", "exception", "cancellation"]
_RAISES = {"exception": RuntimeError, "cancellation": asyncio.CancelledError}


@pytest.mark.parametrize("outcome", _OUTCOMES)
def test_the_java_acceptance_copy_and_its_git_target_are_removed_on_every_path(tmp_path, outcome):
    from _b2c_fixtures import ACCEPTANCE, EXACT_GOAL, TARGET, base_revision, candidate, maven_workspace

    from kriya.workflow import acceptance_jvm as jvm
    from kriya.workflow import acceptance_oracle as ao
    from kriya.workflow.requirements import derive_requirements

    workspace = maven_workspace(tmp_path / "ws")
    cand = candidate(workspace, tmp_path / "cand", "correct")
    (tmp_path / "KriyaAcceptanceTest.java").write_text(ACCEPTANCE)
    artifact = ao.load_acceptance(str(tmp_path / "KriyaAcceptanceTest.java"), derive_requirements(EXACT_GOAL), str(tmp_path / "artifacts"))
    cand_before = _snapshot(cand)
    seen = []
    kwargs = dict(candidate_paths=[TARGET], base_revision=base_revision(workspace),
                  validator_factory=lambda root: _CopyProbe(root, origin=workspace, script=_scripted(outcome), seen=seen))
    if outcome in _RAISES:
        with pytest.raises(_RAISES[outcome]):
            jvm.run_java_acceptance(artifact, str(cand), **kwargs)
    else:
        jvm.run_java_acceptance(artifact, str(cand), **kwargs)
    ran = [event for event in seen if event["gate"] == "tests"]
    assert [event["gate"] for event in seen][:1] == ["compile"] and len(ran) == 1, seen          # the scripted outcome really was reached
    assert all(event["git_target"] and event["bound"] and not event["viewed"] for event in seen), seen   # Kriya's .git target existed in the copy
    copy = seen[0]["root"]
    assert copy != str(cand) and not os.path.lexists(copy), "the copy, with its .git target, did not outlive its run"
    assert not os.path.lexists(os.path.dirname(copy)), "nor did its staging directory"
    assert _snapshot(cand) == cand_before and not os.path.lexists(cand / ".kriya"), "the candidate itself never received anything"


@pytest.mark.parametrize("outcome", _OUTCOMES)
def test_the_named_test_base_export_and_its_git_target_are_removed_on_every_path(tmp_path, outcome):
    from _named_test_oracle_harness import NAMED, git, make_base

    from kriya.workflow.named_test_oracle import ORACLE_BASELINE_INDETERMINATE, judge_named_tests

    repo = make_base(tmp_path / "repo")
    repo_before = _snapshot(repo)
    seen = []

    class _Candidate:
        @staticmethod
        def _test_runner():
            return "pytest"

        @staticmethod
        def run_tests(**_kwargs):
            pytest.fail("the candidate run must not happen when the base run did not produce an inventory")

    def judge():
        return judge_named_tests([NAMED], candidate_root=str(repo), base_revision=git(repo, "rev-parse", "HEAD").strip(), modified=[],
                                 candidate_validator=lambda: _Candidate(),
                                 base_validator=lambda root: _CopyProbe(root, origin=repo, script=_scripted(outcome), seen=seen))

    if outcome == "cancellation":
        with pytest.raises(asyncio.CancelledError):
            judge()
    else:
        verdict = judge()                       # success carries no inventory, so it too ends at "indeterminate": the point is the cleanup
        assert verdict.reason_code == ORACLE_BASELINE_INDETERMINATE, verdict
    assert [event["gate"] for event in seen] == ["tests"], seen
    assert seen[0]["git_target"] and seen[0]["bound"] and not seen[0]["viewed"], "Kriya's .git target existed in the export"
    assert not os.path.lexists(seen[0]["root"]), "the export, with its .git target, did not outlive its run"
    assert _snapshot(repo) == repo_before, "the repository itself was not touched"
