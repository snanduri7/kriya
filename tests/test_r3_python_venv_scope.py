"""R3 (Python sibling, DEPENDENCY-VENV-POISONING-001): the project venv has the lifetime of the verification scope that builds it.

MEASURED at ebd022a: ``<worktree>/.kriya/venv`` was reused across attempts while its pip-install step runs candidate-declared install/build hooks with the
venv writable; a hook in attempt 1 planted a pytest plugin and attempt 2's honest failing test PASSED. Host-side tests pin the lifecycle (removed on entry
and exit of the outermost scope, shared by nested scopes, typed refusal when it cannot be removed); the real-Docker test reproduces the measured sequence."""
import os
import shutil
import subprocess

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import DEPENDENCY_CACHE_SCOPE_REQUIRED, PROJECT_VENV_NOT_DISCARDED, DependencyCacheError
from kriya.tools.dependency_cache import discard_project_venv
from kriya.tools.validate import PolymorphicValidator


def _validator(root):
    return PolymorphicValidator(str(root), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"))


def _venv(root):
    path = root / ".kriya" / "venv" / "lib" / "python3" / "site-packages"
    path.mkdir(parents=True)
    (path / "planted.txt").write_text("left by a hook")
    return root / ".kriya" / "venv"


def test_a_venv_an_earlier_scope_left_is_removed_when_the_outermost_scope_opens(tmp_path):
    validator, venv = _validator(tmp_path), _venv(tmp_path)
    with validator.dependency_cache_scope():
        assert not venv.exists()


def test_the_venv_a_scope_built_is_removed_when_it_ends_also_after_an_error(tmp_path):
    validator = _validator(tmp_path)
    with validator.dependency_cache_scope():
        venv = _venv(tmp_path)
        assert venv.exists()
    assert not venv.exists()
    with pytest.raises(RuntimeError), validator.dependency_cache_scope():
        _venv(tmp_path)
        raise RuntimeError("gate failed")
    assert not (tmp_path / ".kriya" / "venv").exists()


def test_nested_scopes_share_the_one_venv_and_the_inner_exit_keeps_it(tmp_path):
    validator = _validator(tmp_path)
    with validator.dependency_cache_scope():
        venv = _venv(tmp_path)
        with validator.dependency_cache_scope():
            assert venv.exists()
        assert venv.exists()
    assert not venv.exists()


def test_the_memo_of_what_a_scope_installed_does_not_outlive_it(tmp_path):
    validator = _validator(tmp_path)
    with validator.dependency_cache_scope():
        validator._venv_install_cache[("-r", "digest")] = (".kriya/venv/bin/python", None)
        validator._venv_creation_error = "earlier"
    assert validator._venv_install_cache == {} and validator._venv_creation_error is None


def test_a_venv_that_cannot_be_removed_is_a_typed_refusal_never_reused(tmp_path, monkeypatch):
    validator = _validator(tmp_path)
    monkeypatch.setattr("kriya.tools.validate.discard_project_venv", lambda _workspace: False)
    with pytest.raises(DependencyCacheError) as refused, validator.dependency_cache_scope():
        pytest.fail("the scope must not open over a venv that could not be discarded")
    assert refused.value.reason_code == PROJECT_VENV_NOT_DISCARDED
    # at the END of a scope the same failure is reported, not raised (a removal problem never turns a verdict into an error)
    monkeypatch.setattr("kriya.tools.validate.discard_project_venv", lambda _workspace: True)
    with validator.dependency_cache_scope():
        monkeypatch.setattr("kriya.tools.validate.discard_project_venv", lambda _workspace: False)


def test_an_os_error_while_removing_the_venv_is_the_same_typed_refusal_on_entry_and_silent_on_exit(tmp_path, monkeypatch):
    validator = _validator(tmp_path)

    def failing(_workspace):
        raise PermissionError("denied")

    monkeypatch.setattr("kriya.tools.validate.discard_project_venv", failing)
    with pytest.raises(DependencyCacheError) as refused, validator.dependency_cache_scope():
        pytest.fail("the scope must not open")
    assert refused.value.reason_code == PROJECT_VENV_NOT_DISCARDED and "denied" in str(refused.value)
    monkeypatch.setattr("kriya.tools.validate.discard_project_venv", lambda _workspace: True)
    with validator.dependency_cache_scope():
        monkeypatch.setattr("kriya.tools.validate.discard_project_venv", failing)  # the exit removal fails: reported, never raised


def test_the_project_venv_is_never_built_outside_a_verification_scope(tmp_path):
    """MEASURED while landing this change: the managed-service admission grounded its interpreter BEFORE its verification session opened, so the venv it built was
    discarded by the session and the service could not start. A venv asked for outside a scope is the typed refusal, nothing is created or half-used."""
    (tmp_path / "requirements.txt").write_text("six==1.16.0\n")
    validator = _validator(tmp_path)
    for ask in (lambda: validator._ensure_project_venv(["-r", "requirements.txt"]), validator._resolve_python_interpreter):
        with pytest.raises(DependencyCacheError) as refused:
            ask()
        assert refused.value.reason_code == DEPENDENCY_CACHE_SCOPE_REQUIRED
    assert not (tmp_path / ".kriya").exists()


def test_discard_removes_a_read_only_tree_and_a_link_but_never_a_links_target(tmp_path):
    venv = _venv(tmp_path)
    (venv / "lib" / "python3" / "site-packages").chmod(0o500)
    assert discard_project_venv(str(tmp_path)) is True and not venv.exists()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    (tmp_path / ".kriya").mkdir(exist_ok=True)
    os.symlink(target, tmp_path / ".kriya" / "venv")
    assert discard_project_venv(str(tmp_path)) is True
    assert not os.path.lexists(tmp_path / ".kriya" / "venv") and (target / "keep.txt").read_text() == "keep"
    assert discard_project_venv(str(tmp_path)) is True  # nothing there: nothing to do


# --------------------------------------------------------------------------------------- real Docker: the measured sequence
def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001 - an unreachable daemon is the skip condition, whatever the reason
        return False


_PLUGIN = "import pytest\n@pytest.hookimpl(tryfirst=True)\ndef pytest_pyfunc_call(pyfuncitem):\n    return True\n"
_PAYLOAD_SETUP = '''import glob, os
from setuptools import setup
d = os.path.dirname(os.path.abspath(__file__))
sp = None
while d != os.path.dirname(d):
    hit = glob.glob(os.path.join(d, ".kriya", "venv", "lib", "python*", "site-packages"))
    if hit:
        sp = hit[0]
        break
    d = os.path.dirname(d)
if sp:
    open(os.path.join(sp, "kriya_planted_plugin.py"), "w").write(%r)
    di = os.path.join(sp, "kriya_planted-1.0.dist-info")
    os.makedirs(di, exist_ok=True)
    open(os.path.join(di, "METADATA"), "w").write("Metadata-Version: 2.1\\nName: kriya-planted\\nVersion: 1.0\\n")
    open(os.path.join(di, "entry_points.txt"), "w").write("[pytest11]\\nkriya_planted = kriya_planted_plugin\\n")
setup(name="kriyadep", version="1.0", py_modules=["kriyadep"])
''' % _PLUGIN
_BENIGN_SETUP = 'from setuptools import setup\nsetup(name="kriyadep", version="1.0", py_modules=["kriyadep"])\n'
_HONEST_FAILURE = 'def test_honest_candidate_is_broken():\n    assert False, "honest candidate is broken"\n'
_WRITE_ATTEMPT = '''import os
def test_the_venv_is_read_only_to_a_test():
    base = os.path.join(os.getcwd(), ".kriya", "venv", "lib")
    sp = [os.path.join(base, p, "site-packages") for p in os.listdir(base)][0]
    try:
        open(os.path.join(sp, "planted_by_test.txt"), "w").write("x")
    except OSError:
        return
    raise AssertionError("TEST WROTE INTO THE VENV")
'''


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _project(root, setup_source, test_source):
    (root / "vendor" / "dep").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "vendor" / "dep" / "setup.py").write_text(setup_source)
    (root / "vendor" / "dep" / "kriyadep.py").write_text("VALUE = 1\n")
    (root / "requirements.txt").write_text("./vendor/dep\n")
    (root / "tests" / "test_candidate.py").write_text(test_source)
    (root / "app.py").write_text("X = 1\n")
    (root / ".gitignore").write_text(".kriya/\n")
    _git(root, "init", "-q")
    _commit(root, "base")


def _commit(root, message):
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=r3@x", "-c", "user.name=r3", "commit", "-qm", message, "--allow-empty")


def _gate(root):
    cfg = AutonomyConfig(contained_execution_required=True, containment_backend="oci", sandbox_cpu_seconds=240, sandbox_memory_mb=2048,
                         acquisition_cpu_seconds=300, acquisition_memory_mb=2048)
    return PolymorphicValidator(str(root), autonomy_cfg=cfg).run_tests()


@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_an_install_hook_of_one_attempt_cannot_decide_the_next_attempts_verdict_real_containers(tmp_path):
    """The measured sequence through the production path (``run_tests`` -> venv creation -> registry-scoped ``pip install`` -> contained pytest): attempt 1's
    hook plants a pytest plugin; attempt 2 (same worktree, identical declaration, benign dependency, an honest FAILING test) must fail like the control."""
    root = tmp_path / "worktree"
    root.mkdir()
    _project(root, _PAYLOAD_SETUP, _HONEST_FAILURE)
    _gate(root)  # attempt 1 (the hook ran in ITS scope; whatever it concluded is that scope's own business)
    assert not (root / ".kriya" / "venv").exists(), "the venv outlived the scope that built it"
    (root / "vendor" / "dep" / "setup.py").write_text(_BENIGN_SETUP)
    _commit(root, "attempt 2")
    second = _gate(root)
    assert second["success"] is False and "kriya-planted" not in second["output"] and "honest candidate is broken" in second["output"], second["output"][-1200:]
    assert not (root / ".kriya" / "venv").exists()
    control = tmp_path / "control"
    control.mkdir()
    _project(control, _BENIGN_SETUP, _HONEST_FAILURE)
    assert _gate(control)["success"] is False


@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_a_test_running_in_a_gate_still_cannot_write_into_the_venv_real_containers(tmp_path):
    """The other half of the measurement, kept as a control: the venv mount is read-only for every gate (EROFS), so only the install step is the writer."""
    root = tmp_path / "worktree"
    root.mkdir()
    _project(root, _BENIGN_SETUP, _WRITE_ATTEMPT)
    result = _gate(root)
    assert result["success"] is True, result["output"][-1200:]
    assert not (root / ".kriya" / "venv").exists()
