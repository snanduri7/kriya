"""R3 (Wave-2 final closure): the dependency directory a gate mounts is EPHEMERAL, and still never lives in the tree the project's own build walks.

Live matrix (commons-lang, Apache RAT, run 86fa47d4): every contained compile failed in apache-rat-plugin's validate-phase check because 567 of Kriya's own cached
artifacts sat under the worktree's ``.kriya/m2_cache``; the cache was moved under the Kriya state root (that part stands: ``<state>/gate-caches``). Before R3 it was ONE
persistent directory per workspace, mounted read-write into every gate: a candidate test could rewrite a cached jar and a later gate then trusted it (MEASURED at
f3b7d39, ``tests/test_r3_dependency_cache_poisoning.py``). Now each outermost verification scope gets a fresh, empty directory that is removed with the scope.
These are the host-side (no Docker) pins of that contract; the containment proof is the Docker-tier test of the same area."""
import os
import shutil

import pytest

from kriya.config.config import AutonomyConfig
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.tools import dependency_cache
from kriya.tools.containment import (
    DEPENDENCY_CACHE_SCOPE_REQUIRED,
    PERSISTENT_DEPENDENCY_CACHE_WRITABLE,
    ContainmentProfile,
    DependencyCacheError,
    TrustClass,
)
from kriya.tools.containment_oci import _require_ephemeral_writable_caches
from kriya.tools.validate import PolymorphicValidator, _verification_gate


def _within(path, root):
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)


def _validator(tree, workspace=None):
    cfg = AutonomyConfig(contained_execution_required=True, containment_backend="oci")
    return PolymorphicValidator(str(tree), original_workspace_path=str(workspace) if workspace else None, autonomy_cfg=cfg)


@pytest.fixture(name="state")
def _state(tmp_path, monkeypatch):
    path = tmp_path / "state"
    monkeypatch.setenv(ENV_STATE_DIR, str(path))
    return path


def _caches_left(state):
    base = dependency_cache.caches_directory(str(state))
    return sorted(os.listdir(base)) if os.path.isdir(base) else []


def test_the_dependency_layer_is_outside_every_source_tree_and_fresh_and_empty(tmp_path, state):
    workspace = tmp_path / "repo"
    worktree = workspace / ".kriya" / "worktree"
    candidate = workspace / ".kriya" / "worktrees" / "candidate-1"
    for path in (worktree, candidate):
        path.mkdir(parents=True)
    for tree in (worktree, candidate):
        validator = _validator(tree, workspace)
        with validator.dependency_cache_scope():
            cache = validator._maven_cache_dir()  # pylint: disable=protected-access
            assert os.path.isdir(cache) and os.listdir(cache) == [] and os.path.basename(cache) == "maven"
            assert _within(cache, dependency_cache.caches_directory(str(state)))
            for source in (workspace, worktree, candidate):
                assert not _within(cache, source)  # never inside what a project build tool walks (Apache RAT)


def test_two_scopes_of_one_workspace_never_share_a_dependency_layer(tmp_path, state):
    """The pre-R3 contract was the opposite (one cache per workspace, reused by every run). A write in one scope is invisible to the next."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    first, second = _validator(workspace, workspace), _validator(workspace, workspace)
    with first.dependency_cache_scope():
        poisoned = first._dependency_cache_dir("maven")  # pylint: disable=protected-access
        with open(os.path.join(poisoned, "poison.jar"), "w", encoding="utf-8") as handle:
            handle.write("written by candidate-controlled code")
    with second.dependency_cache_scope():
        fresh = second._dependency_cache_dir("maven")  # pylint: disable=protected-access
        assert fresh != poisoned and os.listdir(fresh) == []
    with first.dependency_cache_scope():  # even the SAME validator, in a later scope
        assert os.listdir(first._dependency_cache_dir("maven")) == []  # pylint: disable=protected-access


def test_the_layer_is_removed_with_its_scope_also_after_an_error(tmp_path, state):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    validator = _validator(workspace)
    with validator.dependency_cache_scope():
        cache = validator._dependency_cache_dir("gradle")  # pylint: disable=protected-access
        os.chmod(cache, 0o500)  # a build that made its own directory read-only: still Kriya's to remove
        assert _caches_left(state)
    assert not os.path.exists(cache) and _caches_left(state) == []
    with pytest.raises(RuntimeError, match="boom"):
        with validator.dependency_cache_scope():
            cache = validator._dependency_cache_dir("maven")  # pylint: disable=protected-access
            raise RuntimeError("boom")
    assert not os.path.exists(cache) and _caches_left(state) == []
    assert validator._gate_caches is None  # pylint: disable=protected-access  # no layer can be asked for once the scope is gone


def test_a_dependency_directory_asked_for_outside_a_scope_is_a_typed_refusal(tmp_path, state):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    validator = _validator(workspace)
    for ask in (lambda: validator._maven_cache_dir(), lambda: validator._dependency_cache_dir("gradle")):  # pylint: disable=protected-access
        with pytest.raises(DependencyCacheError) as raised:
            ask()
        assert raised.value.reason_code == DEPENDENCY_CACHE_SCOPE_REQUIRED
    assert _caches_left(state) == []  # nothing was created on the way to the refusal


def test_a_gate_opens_the_scope_and_nested_gates_share_one_layer_that_a_later_gate_does_not_see(tmp_path, state):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    validator = _validator(workspace)
    seen = []

    def inner(self):
        seen.append(("inner", self._dependency_cache_dir("maven")))
        return {"success": True}

    def outer(self):
        layer = self._dependency_cache_dir("maven")
        seen.append(("outer", layer))
        _verification_gate("compile")(inner)(self)
        assert os.path.isdir(layer)  # the inner gate's end does not remove the outer gate's layer
        return {"success": True}

    _verification_gate("tests")(outer)(validator)
    _verification_gate("tests")(outer)(validator)
    (_, first_outer), (_, first_inner), (_, second_outer), _ = seen
    assert first_outer == first_inner and second_outer != first_outer
    assert not os.path.exists(first_outer) and not os.path.exists(second_outer) and _caches_left(state) == []


def test_a_verification_session_is_a_scope_too(tmp_path, state):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    validator = _validator(workspace)
    with validator.verification_session("tests"):
        cache = validator._dependency_cache_dir("maven")  # pylint: disable=protected-access
        assert os.path.isdir(cache)
    assert not os.path.exists(cache) and _caches_left(state) == []


def test_the_pre_r3_persistent_directories_are_never_read_moved_or_removed(tmp_path, state):
    """What an earlier Kriya left (``<state>/dependency-cache/...`` and an in-tree ``.kriya/m2_cache``) was written by candidate-controlled code and is
    unprovenanced: it is not migrated into a layer and not mounted anywhere. It is also not deleted (nothing of an earlier run's evidence is)."""
    workspace = tmp_path / "ws"
    legacy_tree = workspace / ".kriya" / "m2_cache" / "org" / "x"
    legacy_state = state / "dependency-cache" / "maven" / "0123456789abcdef" / "org" / "y"
    for directory in (legacy_tree, legacy_state):
        directory.mkdir(parents=True)
        (directory / "old.jar").write_text("pre-R3 bytes")
    validator = _validator(workspace)
    with validator.dependency_cache_scope():
        assert os.listdir(validator._maven_cache_dir()) == []  # pylint: disable=protected-access
    assert (legacy_tree / "old.jar").read_text() == "pre-R3 bytes" and (legacy_state / "old.jar").read_text() == "pre-R3 bytes"


def _profile(path, writable):
    return ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(path.parent),
                              dependency_cache_paths=[str(path)], dependency_cache_writable=writable)


def test_the_backend_refuses_a_writable_cache_that_is_not_an_ephemeral_one(tmp_path, state):
    """The structural guard (consulted by ``OCIContainmentBackend.prepare`` before any docker resource): a persistent directory, a directory that merely
    sits under ``gate-caches`` without Kriya's marker, a directory carrying the marker INSIDE itself (the one place a container could write), and a link
    to a real ephemeral layer are all refused; the layer this module made is accepted."""
    persistent = tmp_path / "state" / "dependency-cache" / "maven" / "key"
    persistent.mkdir(parents=True)
    unmarked = state / "gate-caches" / "cache-1-abc" / "maven"
    unmarked.mkdir(parents=True)
    self_marked = state / "gate-caches" / "cache-2-def" / "maven"
    self_marked.mkdir(parents=True)
    (self_marked / dependency_cache.EPHEMERAL_MARKER).write_text("{}")
    real = dependency_cache.create_ephemeral_cache(str(state), "maven")
    link = tmp_path / "link"
    link.symlink_to(real)
    moved = tmp_path / "elsewhere" / "cache-4-ghi"  # a genuine layer moved out of ``gate-caches``: still marked, no longer where Kriya keeps layers
    moved.parent.mkdir()
    shutil.copytree(os.path.dirname(real), moved)
    for refused in (persistent, unmarked, self_marked, link, moved / "maven"):
        with pytest.raises(DependencyCacheError) as raised:
            _require_ephemeral_writable_caches(_profile(refused, writable=True))
        assert raised.value.reason_code == PERSISTENT_DEPENDENCY_CACHE_WRITABLE, refused
    _require_ephemeral_writable_caches(_profile(__import__("pathlib").Path(real), writable=True))  # accepted
    _require_ephemeral_writable_caches(_profile(persistent, writable=False))  # a READ-ONLY mount is not what R3 forbids (no caller has one)
    dependency_cache.remove_ephemeral_cache(real)


def test_a_crashed_process_s_layer_is_swept_on_the_next_creation_and_a_live_one_is_not(tmp_path, state):
    base = dependency_cache.caches_directory(str(state))
    dead = os.path.join(base, "cache-999999999-dead")
    live = os.path.join(base, f"cache-{os.getpid()}-live")
    for unit in (dead, live):
        os.makedirs(os.path.join(unit, "maven"))
        with open(os.path.join(unit, dependency_cache.EPHEMERAL_MARKER), "w", encoding="utf-8") as handle:
            handle.write("{}")
    dependency_cache.remove_ephemeral_cache(dependency_cache.create_ephemeral_cache(str(state), "maven"))
    assert not os.path.exists(dead) and os.path.isdir(live)
    unmarked = tmp_path / "unmarked" / "maven"
    unmarked.mkdir(parents=True)
    dependency_cache.remove_ephemeral_cache(str(unmarked))
    assert unmarked.is_dir()  # a directory that is not a layer is never removed


def test_every_path_of_a_writable_profile_is_checked_not_just_the_first(tmp_path, state):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    real = dependency_cache.create_ephemeral_cache(str(state), "maven")
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path),
                                 dependency_cache_paths=[real, str(persistent)], dependency_cache_writable=True)
    with pytest.raises(DependencyCacheError) as raised:
        _require_ephemeral_writable_caches(profile)
    assert raised.value.reason_code == PERSISTENT_DEPENDENCY_CACHE_WRITABLE
    dependency_cache.remove_ephemeral_cache(real)


@pytest.mark.parametrize("which", ["acquisition", "execution"])
def test_the_real_backend_refuses_the_production_profiles_over_a_persistent_cache_before_any_docker_resource(tmp_path, state, which):
    """The two production profile builders (the registry-scoped acquisition and the network-denied execution) over a persistent directory: refused by
    ``OCIContainmentBackend.prepare`` itself, before the docker CLI is even looked for - so nothing was created."""
    from kriya.tools.containment_oci import OCIContainmentBackend
    from kriya.tools.dependency_execution import _acquisition_profile, _execution_profile

    persistent = tmp_path / "state" / "dependency-cache" / "maven" / "key"
    persistent.mkdir(parents=True)
    if which == "acquisition":
        profile = _acquisition_profile(str(tmp_path), str(persistent), [], registry_hosts=["repo.maven.apache.org"])
    else:
        profile = _execution_profile(str(tmp_path), str(persistent), [], None, None)
    backend = OCIContainmentBackend()
    backend._require_docker = lambda: (_ for _ in ()).throw(AssertionError("docker was consulted before the R3 guard"))  # pylint: disable=protected-access
    with pytest.raises(DependencyCacheError) as raised:
        backend.prepare(profile, ["mvn", "-B", "test"])
    assert raised.value.reason_code == PERSISTENT_DEPENDENCY_CACHE_WRITABLE


class _AuthorityValidator:
    """A validator double for ``run_authority_bundle`` that records the dependency scope around its phases (no docker daemon needed)."""

    def __init__(self, fail_with=None):
        self.events, self.open, self.fail_with = [], False, fail_with
        self._owned_trees, self._pinned_non_git = set(), False

    @__import__("contextlib").contextmanager
    def dependency_cache_scope(self):
        self.open = True
        self.events.append("enter")
        try:
            yield
        finally:
            self.open = False
            self.events.append("exit")

    def _dependency_cache_dir(self, tool):
        assert self.open, "the cache was asked for outside the scope"
        return f"/layer/{tool}"

    def _run_cmd_with_timeout(self, argv, **kwargs):
        assert self.open, "a phase ran outside the scope"
        self.events.append((argv[0], kwargs.get("dependency_cache_path")))
        if self.fail_with is not None:
            raise self.fail_with
        return {"returncode": 0, "timeout": False, "stdout": "", "stderr": "", "target_started": True}


def _authority_inputs(tmp_path):
    from kriya.workflow.authority_bundle import AuthorityBundle

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "pom.xml").write_text("<project/>")
    bundle = AuthorityBundle(
        digest="d" * 64, stored_dir=str(tmp_path / "stored"), source_path="bundle.json", authority_id="a" * 16, goal_sha256="g" * 64,
        requirement_set_sha256="r" * 64, base_revision="b" * 40, assets={}, prepare=("echo", "prepare"), verify=("echo", "verify"),
        timeout_seconds=30, language="java", build_tool="maven", visibility="visible", covers={})
    return bundle, str(candidate)


def test_an_authority_bundle_run_holds_one_dependency_scope_around_its_prepare_and_verify_phases(tmp_path, state):
    """The run asks its validator for the cache only inside the scope it entered, uses ONE layer for both phases and leaves the scope when it returns
    (a layer asked for outside a scope is the typed refusal, see above)."""
    from kriya.workflow.authority_bundle import AUTHORITY_PASSED, run_authority_bundle

    bundle, candidate = _authority_inputs(tmp_path)
    validator = _AuthorityValidator()
    run = run_authority_bundle(bundle, candidate, state_root=str(state), validator_factory=lambda _root: validator)
    assert run.reason_code == AUTHORITY_PASSED, (run.reason_code, run.reason)
    assert validator.events == ["enter", ("echo", "/layer/maven"), ("echo", "/layer/maven"), "exit"]


def test_an_authority_bundle_run_leaves_its_dependency_scope_when_a_phase_raises_not_when_the_error_is_released(tmp_path, state):
    """The exception's traceback keeps the run's frame (and anything it holds) alive: a scope left to garbage collection would still be open while the
    caller handles the error, and the layer would outlive the failed run."""
    from kriya.workflow.authority_bundle import run_authority_bundle

    bundle, candidate = _authority_inputs(tmp_path)
    validator = _AuthorityValidator(fail_with=RuntimeError("the contained prepare blew up"))
    with pytest.raises(RuntimeError, match="blew up") as raised:
        run_authority_bundle(bundle, candidate, state_root=str(state), validator_factory=lambda _root: validator)
    assert raised.traceback and validator.events[-1] == "exit" and validator.open is False  # asserted while the traceback (and its frames) is still held
