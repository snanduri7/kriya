"""WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001: the Python project environment is created by whichever gate needs it FIRST (a runtime-only plan has no earlier test gate), and that
creation is a container run over a fresh bind-mount target - an infrastructure step that can fail transiently. MEASURED: two runtime-only runs failed (`ensurepip` exit 1; an empty venv),
the same goal then passed unchanged, and the identical contained run succeeded 12 of 12 on a quiet host. So creation gets ONE bounded retry from nothing and still fails closed with both
attempts' errors. Pinned here through the one owner (``PolymorphicValidator._ensure_project_venv_impl``), with a scripted command runner:

 A. an environment that is created first time: one creation, no retry; a runtime-only caller needs no earlier gate;
 B. a transient first failure (non-zero exit, an exception, or an exit 0 that left no interpreter): a second attempt from nothing succeeds, and the first attempt's debris is gone;
 C. both attempts failing: fail closed (no interpreter, a typed creation error naming BOTH attempts), and exactly two creations - bounded;
 D. a venv that already exists is used as before: no creation, no retry (the with-test path is unchanged).
"""
import os

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import VENV_CREATION_ATTEMPTS, PolymorphicValidator, _record_installed_specifiers


def _validator(root):
    return PolymorphicValidator(str(root), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"))


class Runner:
    """Scripts the container: ``outcomes`` is the list of creation outcomes, consumed in order. Each is 'ok', 'fail' (exit 1, partial debris), 'silent' (exit 0, nothing created) or 'raise'."""

    def __init__(self, validator, root, outcomes):
        self.root, self.outcomes, self.creations, self.pip_calls, self.debris_seen = root, list(outcomes), 0, 0, []
        validator._run_cmd_with_timeout = self  # pylint: disable=protected-access

    def __call__(self, cmd, **kwargs):
        venv = self.root / ".kriya" / "venv"
        if cmd[1:3] == ["-m", "venv"]:
            self.creations += 1
            self.debris_seen.append((venv / "debris").exists())
            outcome = self.outcomes.pop(0)
            if outcome == "ok":
                (venv / "bin").mkdir(parents=True, exist_ok=True)
                os.symlink("/usr/local/bin/python3", venv / "bin" / "python")
                return {"returncode": 0, "stdout": "", "stderr": ""}
            if outcome == "fail":
                venv.mkdir(parents=True, exist_ok=True)
                (venv / "debris").write_text("half-made")
                return {"returncode": 1, "stdout": "", "stderr": "Error: Command '[ensurepip]' returned non-zero exit status 1."}
            if outcome == "silent":
                return {"returncode": 0, "stdout": "", "stderr": ""}
            raise OSError("docker run could not start")
        self.pip_calls += 1
        return {"returncode": 0, "stdout": "", "stderr": ""}


def test_a_runtime_only_caller_gets_its_environment_without_any_earlier_gate_and_without_a_retry(tmp_path):
    validator = _validator(tmp_path)
    runner = Runner(validator, tmp_path, ["ok"])
    interpreter, error = validator._ensure_project_venv_impl([])  # pylint: disable=protected-access
    assert error is None and interpreter.endswith("bin/python")
    assert (runner.creations, runner.pip_calls) == (1, 1) and validator._venv_creation_error is None  # pylint: disable=protected-access


def test_a_transient_failure_is_retried_from_nothing_and_the_first_attempts_debris_is_gone(tmp_path):
    for first in ("fail", "silent", "raise"):
        root = tmp_path / first
        root.mkdir()
        validator = _validator(root)
        runner = Runner(validator, root, [first, "ok"])
        interpreter, error = validator._ensure_project_venv_impl([])  # pylint: disable=protected-access
        assert error is None and interpreter.endswith("bin/python"), first
        assert runner.creations == 2 and runner.debris_seen == [False, False], f"{first}: the retry started from nothing"
        assert validator._venv_creation_error is None  # pylint: disable=protected-access


def test_two_failed_attempts_fail_closed_with_both_errors_and_stop_there(tmp_path):
    validator = _validator(tmp_path)
    runner = Runner(validator, tmp_path, ["fail", "silent", "ok"])
    assert validator._ensure_project_venv_impl([]) == (None, None)  # pylint: disable=protected-access
    error = validator._venv_creation_error  # pylint: disable=protected-access
    assert runner.creations == VENV_CREATION_ATTEMPTS == 2 and runner.pip_calls == 0, "bounded: a third attempt is never made, and nothing is installed into nothing"
    assert error.startswith("attempt 1: ") and " | attempt 2: " in error and "ensurepip" in error and "exited 0" in error
    assert runner.outcomes == ["ok"], "the scripted third success was never consumed"


def test_an_unlaunchable_container_twice_is_reported_as_not_invoked_and_fails_closed(tmp_path):
    validator = _validator(tmp_path)
    Runner(validator, tmp_path, ["raise", "raise"])
    assert validator._ensure_project_venv_impl([]) == (None, None)  # pylint: disable=protected-access
    assert validator._venv_creation_error.count("could not be invoked") == 2  # pylint: disable=protected-access


def test_an_existing_environment_is_used_as_before_with_no_creation_and_no_retry(tmp_path):
    venv = tmp_path / ".kriya" / "venv" / "bin"
    venv.mkdir(parents=True)
    os.symlink("/usr/local/bin/python3", venv / "python")
    _record_installed_specifiers(str(tmp_path / ".kriya" / "venv"), [])  # the marker of a venv Kriya built: an unmarked venv is deliberately rebuilt (VENV-ADDITIVE-REUSE-001)
    validator = _validator(tmp_path)
    runner = Runner(validator, tmp_path, [])
    interpreter, error = validator._ensure_project_venv_impl([])  # pylint: disable=protected-access
    assert error is None and interpreter.endswith("bin/python") and runner.creations == 0
