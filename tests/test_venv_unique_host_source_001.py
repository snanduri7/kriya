"""PYTHON-VENV-SOURCE-REUSE-001: the project venv of a view-backed verification scope is bind-mounted from a HOST source the scope owns - an ephemeral unit that is never removed and recreated.

MEASURED (v2 A/B, handover/PRODUCT-72H/SPRINT_LOG.md 12h; frozen rule met): removing and recreating the SAME host pathname for the venv bind source moments before it is bound again made the
create container fail transiently under load (ENOENT on the first write, 13/60), a never-used pathname 0/60. The pathname is what failed, so these tests pin the identity discipline (a scripted
container stand-in cannot reproduce Docker Desktop's file-sharing staleness; the real-Docker proof is the load run of the same validator sequence):

* every creation attempt - the first, the bounded retry, a rebuild after a dropped dependency - is bound from a pathname no earlier attempt of the scope had, and the earlier units stay owned
  (so no pathname handed out in a scope can be handed out again in it);
* what is mounted is exactly the source the scope stored: ``<origin>/.kriya/venv`` is never consulted (a candidate may plant a link, a file or a directory there), a stored source that is
  missing is the environment's typed failure, one that is there but is not the scope's own unit is the authority refusal, never an empty mount;
* the container path and the effective argv are unchanged; a gate that executes in place (no view) is untouched."""
import os
from pathlib import Path

import pytest
from test_oi142_gate_view import _repo

from kriya.tools import validate as validate_module
from kriya.tools.containment import (
    DEPENDENCY_CACHE_REASON_CODES,
    PROJECT_VENV_SOURCE_NOT_SCOPE_OWNED,
    ContainmentSetupError,
    DependencyCacheError,
)
from kriya.tools.dependency_cache import create_ephemeral_cache
from kriya.tools.dependency_execution import PYTHON_ENVIRONMENT_UNAVAILABLE
from kriya.tools.validate import PolymorphicValidator, _verification_gate

VENV_CONTAINER_PATH = "/kriya/workspace/.kriya/venv"
EFFECTIVE_PYTHON = os.path.join(".kriya", "venv", "bin", "python")


class _Containers:
    """A scripted stand-in for the container runs of ``_ensure_project_venv``: for every command it evaluates the validator's real ``_venv_mounts`` (what the OCI backend would be handed) and records
    the host source it would bind - the ledger the tests read. ``fail_create`` is how many creation containers fail before one succeeds."""

    def __init__(self, validator, fail_create=0):
        self.validator = validator
        self.fail_create = fail_create
        self.bound = []      # (kind, host source or None, writable, container path)
        self.argv = []

    def __call__(self, cmd, cwd, timeout=300, **kwargs):
        writable = bool(kwargs.get("venv_writable"))
        mounts = self.validator._venv_mounts(self.validator.workspace_path, writable=writable)  # pylint: disable=protected-access
        kind = "create" if cmd[1:3] == ["-m", "venv"] else "pip" if cmd[1:3] == ["-m", "pip"] else "use"
        self.bound.append((kind, mounts[0].host_path if mounts else None, writable, mounts[0].container_path if mounts else None))
        self.argv.append(list(cmd))
        if kind == "create":
            if self.fail_create:
                self.fail_create -= 1
                return {"returncode": 1, "stdout": "", "stderr": "[Errno 2] No such file or directory: '/kriya/workspace/.kriya/venv/pyvenv.cfg'", "timeout": False}
            source = Path(mounts[0].host_path)
            (source / "bin").mkdir(parents=True, exist_ok=True)
            (source / "bin" / "python").write_text("#!/bin/sh\n")
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}


@pytest.fixture
def ledger(monkeypatch):
    """Every host pathname the validator asked the ephemeral-unit primitive for (it calls through)."""
    allocated = []
    real = validate_module.create_ephemeral_cache

    def spy(state_root, tool):
        path = real(state_root, tool)
        allocated.append(path)
        return path

    monkeypatch.setattr(validate_module, "create_ephemeral_cache", spy)
    return allocated


def _validator(tmp_path, requirements="requests\n"):
    root = _repo(tmp_path, extra={"requirements.txt": requirements})
    return root, PolymorphicValidator(str(root), original_workspace_path=str(root))


def _run_gate(validator, body):
    out = {}

    def gate(self):
        out["value"] = body(self)
        return {"success": True}

    _verification_gate("tests")(gate)(validator)
    return out["value"]


def _install(validator, monkeypatch, fail_create=0):
    stand_in = _Containers(validator, fail_create=fail_create)
    monkeypatch.setattr(validator, "_run_cmd_with_timeout", stand_in)
    return stand_in


# --- the identity discipline -------------------------------------------------------------------------------------------------------------------


def test_the_retry_is_bound_from_a_new_source_and_the_first_attempts_unit_stays_owned(tmp_path, monkeypatch, ledger):
    _root, validator = _validator(tmp_path)
    stand_in = _install(validator, monkeypatch, fail_create=1)

    def body(self):
        python, error = self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        return python, error, [os.path.lexists(path) for path in ledger]

    python, error, alive_inside = _run_gate(validator, body)
    assert (python, error) == (EFFECTIVE_PYTHON, None)
    creates = [source for kind, source, _w, _c in stand_in.bound if kind == "create"]
    assert len(creates) == 2 and creates[0] != creates[1], "attempt 2 must not return to attempt 1's source"
    assert len(ledger) == len(set(ledger)) == 2
    assert alive_inside == [True, True], "attempt 1's unit is kept (owned) while the scope lives, so its pathname cannot be handed out again"
    assert not any(os.path.lexists(path) for path in ledger), "...and every unit goes with the scope"


def test_every_command_of_the_scope_binds_the_one_stored_source_of_the_surviving_attempt(tmp_path, monkeypatch, ledger):
    _root, validator = _validator(tmp_path)
    stand_in = _install(validator, monkeypatch, fail_create=1)

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        self._run_cmd_with_timeout([EFFECTIVE_PYTHON, "-c", "pass"], cwd=self.workspace_path)  # the gate's own use of the venv
        return self._venv_source  # pylint: disable=protected-access

    stored = _run_gate(validator, body)
    after_retry = [(kind, source, writable, container) for kind, source, writable, container in stand_in.bound[1:]]
    assert [kind for kind, *_rest in after_retry] == ["create", "pip", "use"]
    assert {source for _kind, source, _w, _c in after_retry} == {stored} and stored == ledger[-1]
    assert [container for *_head, container in after_retry] == [VENV_CONTAINER_PATH] * 3
    assert [(kind, writable) for kind, _s, writable, _c in after_retry] == [("create", True), ("pip", True), ("use", False)]


def test_a_rebuild_after_a_dropped_dependency_is_bound_from_a_new_source_never_recreated_in_place(tmp_path, monkeypatch, ledger):
    _root, validator = _validator(tmp_path, requirements="requests\nflask\n")
    stand_in = _install(validator, monkeypatch)

    def body(self):
        first = self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        first_source = self._venv_source  # pylint: disable=protected-access
        (Path(self.workspace_path) / "requirements.txt").write_text("requests\n")  # the declaration the gate reads (its view's) dropped `flask`
        second = self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        return first, first_source, second, self._venv_source, os.path.lexists(first_source)  # pylint: disable=protected-access

    first, first_source, second, second_source, first_alive = _run_gate(validator, body)
    assert first == second == (EFFECTIVE_PYTHON, None)
    assert validator.venv_recreations == 1
    assert first_source != second_source and len(ledger) == len(set(ledger)) == 2
    assert first_alive, "the dropped-dependency venv is released, not removed and recreated under the same pathname"
    creates = [source for kind, source, _w, _c in stand_in.bound if kind == "create"]
    assert creates == [first_source, second_source]


def test_no_pathname_is_ever_bound_twice_across_scopes_and_cleanup_residue_never_returns_one(tmp_path, monkeypatch, ledger):
    """The v2 sequence at the validator level: gate after gate, each outermost scope builds its venv. Even when a unit cannot be removed (a stale Docker Desktop mount), the next scope gets a pathname that did not exist."""
    _root, validator = _validator(tmp_path)
    stand_in = _install(validator, monkeypatch)
    removals_blocked = {"on": True}
    real_remove = validate_module.remove_ephemeral_cache
    monkeypatch.setattr(validate_module, "remove_ephemeral_cache", lambda path: None if removals_blocked["on"] and "venv" in path else real_remove(path))

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        return self._venv_source  # pylint: disable=protected-access

    sources = [_run_gate(validator, body) for _ in range(6)]
    assert len(set(sources)) == 6 and len(ledger) == len(set(ledger)) == 6
    assert all(os.path.lexists(path) for path in sources), "a unit whose removal is blocked stays (it is swept later), and is not handed out again"
    bound = [source for kind, source, _w, _c in stand_in.bound if kind == "create"]
    assert bound == sources


def test_a_pathname_never_existed_when_it_was_allocated(tmp_path, monkeypatch):
    """The allocation is an atomic create of a name nobody holds: a pathname a bound container could still remember is never recreated."""
    _root, validator = _validator(tmp_path)
    _install(validator, monkeypatch, fail_create=1)
    existed = []
    real = validate_module.create_ephemeral_cache

    def spy(state_root, tool):
        before = set(os.listdir(os.path.join(state_root, "gate-caches"))) if os.path.isdir(os.path.join(state_root, "gate-caches")) else set()
        path = real(state_root, tool)
        existed.append(os.path.basename(os.path.dirname(path)) in before)
        return path

    monkeypatch.setattr(validate_module, "create_ephemeral_cache", spy)
    _run_gate(validator, lambda self: self._ensure_project_venv(["-r", "requirements.txt"]))  # pylint: disable=protected-access
    assert existed == [False, False]


# --- one source of truth for what is mounted; the origin entry is nobody's authority ------------------------------------------------------------


def test_no_alias_is_created_at_the_origin_and_the_view_gets_the_mount_target_only(tmp_path, monkeypatch, ledger):
    root, validator = _validator(tmp_path)
    _install(validator, monkeypatch)

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        return os.path.lexists(root / ".kriya" / "venv"), (Path(self.workspace_path) / ".kriya" / "venv").is_dir(), self.workspace_path != str(root)

    origin_has_entry, view_has_target, in_view = _run_gate(validator, body)
    assert in_view and origin_has_entry is False and view_has_target is True
    assert ledger and not str(ledger[0]).startswith(str(root))


@pytest.mark.parametrize("kind", ["symlink_to_outside", "file", "directory"])
def test_a_candidate_planted_origin_entry_mid_scope_cannot_influence_the_mount(tmp_path, monkeypatch, ledger, kind):
    root, validator = _validator(tmp_path)
    stand_in = _install(validator, monkeypatch)
    outside = tmp_path / "outside-the-workspace"
    outside.mkdir()
    (outside / "sentinel").write_text("keep")

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        planted = root / ".kriya" / "venv"
        planted.parent.mkdir(parents=True, exist_ok=True)
        if kind == "symlink_to_outside":
            planted.symlink_to(outside)
        elif kind == "file":
            planted.write_text("planted")
        else:
            (planted / "bin").mkdir(parents=True)
            (planted / "bin" / "python").write_text("#!/bin/sh\necho planted\n")
        stand_in.bound.clear()
        self._run_cmd_with_timeout([EFFECTIVE_PYTHON, "-c", "pass"], cwd=self.workspace_path)  # pylint: disable=protected-access
        again = self._ensure_project_venv(["-r", "requirements.txt"])  # a memo hit: nothing is rebuilt, nothing is read from the planted entry
        return self._venv_source, again, os.path.realpath(planted)  # pylint: disable=protected-access

    stored, again, planted_real = _run_gate(validator, body)
    assert again == (EFFECTIVE_PYTHON, None)
    assert stand_in.bound == [("use", stored, False, VENV_CONTAINER_PATH)], "the mount is the scope's stored source, whatever sits at the origin entry"
    assert stored == ledger[0] and stored != planted_real
    assert (outside / "sentinel").read_text() == "keep", "the planted link's target is never traversed or removed, not even by the scope's own exit discard"
    assert not os.path.lexists(root / ".kriya" / "venv"), "the exit discard removes the planted entry itself, without following it"


def test_a_preexisting_origin_symlink_is_discarded_on_entry_without_traversal(tmp_path, monkeypatch, ledger):
    root, validator = _validator(tmp_path)
    _install(validator, monkeypatch)
    outside = tmp_path / "outside-the-workspace"
    (outside / "bin").mkdir(parents=True)
    (outside / "bin" / "python").write_text("#!/bin/sh\necho outside\n")
    (root / ".kriya").mkdir(exist_ok=True)
    (root / ".kriya" / "venv").symlink_to(outside)

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        return os.path.lexists(root / ".kriya" / "venv"), self._venv_source  # pylint: disable=protected-access

    entry_left, stored = _run_gate(validator, body)
    assert entry_left is False, "entry discard removed the link itself"
    assert (outside / "bin" / "python").read_text() == "#!/bin/sh\necho outside\n", "its target was neither traversed nor emptied"
    assert stored == ledger[0] and stored != str(outside), "the venv was built from the scope's own unit, never reusing what the link pointed at"


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_a_preexisting_origin_directory_or_file_is_discarded_on_entry(tmp_path, monkeypatch, ledger, kind):
    root, validator = _validator(tmp_path)
    _install(validator, monkeypatch)
    entry = root / ".kriya" / "venv"
    entry.parent.mkdir(exist_ok=True)
    if kind == "directory":
        (entry / "bin").mkdir(parents=True)
        (entry / "bin" / "python").write_text("#!/bin/sh\necho stale\n")
        (entry / validate_module._VENV_INSTALLED_MARKER).write_text('{"specifiers": ["requests"]}')  # pylint: disable=protected-access
    else:
        entry.write_text("stale")

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        return os.path.lexists(entry), self.venv_recreations

    assert _run_gate(validator, body) == (False, 0), "the old venv was discarded, not trusted (no 'marker says installed' reuse) and not rebuilt as a recreation of it"
    assert len(ledger) == 1


# --- typed failures: environment vs authority ---------------------------------------------------------------------------------------------------


def test_a_stored_source_that_is_gone_is_the_typed_environment_failure_and_never_an_empty_mount(tmp_path, monkeypatch):
    _root, validator = _validator(tmp_path)
    stand_in = _install(validator, monkeypatch)

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        source = self._venv_source  # pylint: disable=protected-access
        import shutil

        shutil.rmtree(source)
        stand_in.bound.clear()
        with pytest.raises(ContainmentSetupError) as failed:
            self._run_cmd_with_timeout([EFFECTIVE_PYTHON, "-c", "pass"], cwd=self.workspace_path)  # pylint: disable=protected-access
        assert not isinstance(failed.value, DependencyCacheError), "an environment failure is not an authority refusal"
        assert str(failed.value).startswith(f"{PYTHON_ENVIRONMENT_UNAVAILABLE}:")
        with pytest.raises(ContainmentSetupError):
            self._ensure_project_venv(["--pre"])  # pylint: disable=protected-access  # a rebuild is not silently made after approval either
        return list(stand_in.bound)

    assert _run_gate(validator, body) == []


@pytest.mark.parametrize("how", ["link", "file", "foreign_unit"])
def test_a_stored_source_that_is_not_the_scopes_own_unit_is_the_typed_authority_refusal(tmp_path, monkeypatch, how):
    _root, validator = _validator(tmp_path)
    _install(validator, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def body(self):
        self._ensure_project_venv(["-r", "requirements.txt"])  # pylint: disable=protected-access
        source = self._venv_source  # pylint: disable=protected-access
        import shutil

        if how == "foreign_unit":
            self._venv_source = create_ephemeral_cache(self._state_root(), "venv")  # pylint: disable=protected-access  # a real ephemeral unit, but another scope's
        else:
            shutil.rmtree(source)
            if how == "link":
                os.symlink(elsewhere, source)
            else:
                Path(source).write_text("not a directory")
        with pytest.raises(DependencyCacheError) as refused:
            self._venv_mounts(self.workspace_path, writable=False)  # pylint: disable=protected-access
        return refused.value.reason_code

    assert _run_gate(validator, body) == PROJECT_VENV_SOURCE_NOT_SCOPE_OWNED


def test_the_reason_codes_stay_closed_and_the_two_failures_are_distinct():
    assert PROJECT_VENV_SOURCE_NOT_SCOPE_OWNED in DEPENDENCY_CACHE_REASON_CODES
    assert PYTHON_ENVIRONMENT_UNAVAILABLE not in DEPENDENCY_CACHE_REASON_CODES  # the environment's code is not an authority code
    assert len(DEPENDENCY_CACHE_REASON_CODES) == 4


# --- what does not change ------------------------------------------------------------------------------------------------------------------------


def test_the_create_argv_the_container_path_and_the_effective_argv_are_unchanged(tmp_path, monkeypatch):
    _root, validator = _validator(tmp_path)
    stand_in = _install(validator, monkeypatch)
    python, error = _run_gate(validator, lambda self: self._ensure_project_venv(["-r", "requirements.txt"]))  # pylint: disable=protected-access
    assert (python, error) == (EFFECTIVE_PYTHON, None) and python == validate_module.PROJECT_PYTHON_INTERPRETER
    assert stand_in.argv[0] == ["python3", "-m", "venv", os.path.join(".kriya", "venv")]
    assert stand_in.argv[1][:3] == [EFFECTIVE_PYTHON, "-m", "pip"]
    assert {container for _k, _s, _w, container in stand_in.bound} == {VENV_CONTAINER_PATH}


def test_a_gate_that_executes_in_place_is_untouched_its_venv_stays_inside_the_workspace(tmp_path, monkeypatch, ledger):
    root = tmp_path / "plain"  # not a Git work tree: no view, no bind source
    root.mkdir()
    (root / "requirements.txt").write_text("requests\n")
    validator = PolymorphicValidator(str(root), original_workspace_path=str(root))
    created = []

    def fake_run(cmd, cwd, timeout=300, **_kwargs):
        assert validator._venv_mounts(validator.workspace_path, writable=True) == ()  # pylint: disable=protected-access
        if cmd[1:3] == ["-m", "venv"]:
            (root / ".kriya" / "venv" / "bin").mkdir(parents=True)
            (root / ".kriya" / "venv" / "bin" / "python").write_text("#!/bin/sh\n")
            created.append(cmd[3])
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}

    monkeypatch.setattr(validator, "_run_cmd_with_timeout", fake_run)

    def body(self):
        assert self.workspace_path == str(root)
        return self._ensure_project_venv(["-r", "requirements.txt"]), self._venv_source  # pylint: disable=protected-access

    result, source = _run_gate(validator, body)
    assert result == (EFFECTIVE_PYTHON, None) and source is None and ledger == []
    assert created == [os.path.join(".kriya", "venv")]


def test_a_failed_creation_leaves_the_scope_without_a_venv_and_the_typed_environment_outcome(tmp_path, monkeypatch, ledger):
    _root, validator = _validator(tmp_path)
    stand_in = _install(validator, monkeypatch, fail_create=2)

    def body(self):
        interpreter, error = self._resolve_python_interpreter()  # pylint: disable=protected-access
        return interpreter, error, self.python_environment_error, self._venv_source, [os.path.lexists(path) for path in ledger]  # pylint: disable=protected-access

    interpreter, error, env_error, source, alive = _run_gate(validator, body)
    assert error is None and env_error is not None and env_error[0] == PYTHON_ENVIRONMENT_UNAVAILABLE and interpreter == "python3"
    assert "attempt 1" in env_error[1] and "attempt 2" in env_error[1]
    assert source is None and len(ledger) == len(set(ledger)) == 2 and alive == [True, True]
    creates = [s for kind, s, _w, _c in stand_in.bound if kind == "create"]
    assert creates[0] != creates[1]
