"""CONTAINED-PYTHON-TEST-GATE-001 (blind reliability cohort 2026-10-07, T1/T4/T5):
under contained execution the Python test gate was assembled from host
assumptions - (1) a dependency-free project got the container image's bare
`python3`, which has no pytest, so zero tests ran on the baseline and on every
candidate; (2) the pytest bootstrap embedded HOST absolute sys.path roots that
do not exist at the container mount, so test modules outside the package walk
could not import the project. Both left correct candidates rejected
(REGRESSION_UNATTRIBUTED / incomplete session). Fix: a dependency-free
contained project gets the project venv (pytest only, same acquisition
authority), and the roots are resolved inside the child relative to its cwd."""
import ast
import os
import re
import sys

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator

CONTAINER_ONLY_INTERPRETER = "/kriya-test-nonexistent/usr/local/bin/python3"


def _validator(tmp_path, *, contained):
    (tmp_path / "module.py").write_text("X = 1\n")  # a Python workspace (stack detection needs a .py file)
    return PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(
        contained_execution_required=contained, containment_backend="oci" if contained else "none"))


def _fake_commands(validator, calls, *, pip_returncode=0):
    """Records every command; a `-m venv` call plants the venv's interpreter link
    (the host-side presence check is lexists under containment)."""
    def run(cmd, cwd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            bin_dir = os.path.join(validator.workspace_path, ".kriya", "venv", "bin")
            os.makedirs(bin_dir, exist_ok=True)
            os.symlink(CONTAINER_ONLY_INTERPRETER, os.path.join(bin_dir, "python"))
        if cmd[1:3] == ["-m", "pip"]:
            return {"returncode": pip_returncode, "stdout": "", "stderr": "pip said no" if pip_returncode else "",
                    "timeout": False}
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}
    validator._run_cmd_with_timeout = run


@pytest.mark.parametrize("manifest", [
    None,                                   # nothing at all (T1: cachetools has no dependency key)
    ("pyproject.toml", "[project]\nname = 'x'\nversion = '0'\ndependencies = []\n"),   # T4: empty list
    ("setup.py", "from setuptools import setup\nsetup()\n"),                            # setup.py only
    ("requirements.txt", "# comments only\n\n"),                                         # no real requirement
])
def test_a_dependency_free_contained_project_gets_a_venv_with_pytest(tmp_path, manifest):
    if manifest:
        (tmp_path / manifest[0]).write_text(manifest[1])
    validator = _validator(tmp_path, contained=True)
    calls = []
    _fake_commands(validator, calls)
    assert validator._resolve_python_interpreter() == (".kriya/venv/bin/python", None)
    assert [cmd[:3] for cmd in calls] == [["python3", "-m", "venv"], [".kriya/venv/bin/python", "-m", "pip"]]
    assert calls[1][3:] == ["install", "-q", "pytest"]
    # The resolver is shared with run_app_sequence()/run_app(): a second resolution
    # (runtime verification) reuses the venv, creating nothing twice.
    assert validator._resolve_python_interpreter() == (".kriya/venv/bin/python", None)
    assert len(calls) == 2


def test_host_mode_dependency_free_project_still_uses_kriya_interpreter_without_any_command(tmp_path):
    validator = _validator(tmp_path, contained=False)
    calls = []
    _fake_commands(validator, calls)
    assert validator._resolve_python_interpreter() == (sys.executable, None)
    assert calls == []


def test_a_failed_pytest_install_fails_the_gate_with_pips_output_never_the_bare_interpreter(tmp_path):
    """Newly reachable path: the pytest install itself fails -> a hard gate
    failure carrying pip's output (fail closed), not a silent run on `python3`."""
    validator = _validator(tmp_path, contained=True)
    calls = []
    _fake_commands(validator, calls, pip_returncode=1)
    interpreter, install_error = validator._resolve_python_interpreter()
    assert interpreter == "python3" and "pip said no" in install_error
    result = validator.run_tests(None)
    assert result["success"] is False and "pip said no" in result["output"]
    assert result["test_execution"]["reason"] == "TEST_PROCESS_NOT_RUN"
    assert [cmd[1:3] for cmd in calls] == [["-m", "venv"], ["-m", "pip"]]  # pytest itself never ran


def test_a_failed_venv_creation_keeps_the_fail_closed_bare_interpreter(tmp_path):
    validator = _validator(tmp_path, contained=True)
    calls = []

    def run(cmd, cwd, **kwargs):
        calls.append(cmd)
        return {"returncode": 1, "stdout": "", "stderr": "no venv module", "timeout": False}
    validator._run_cmd_with_timeout = run
    assert validator._resolve_python_interpreter() == ("python3", None)
    assert [cmd[1:3] for cmd in calls] == [["-m", "venv"]]


def _bootstrap_roots(cmd):
    match = re.search(r"sys\.path\.extend\(\[os\.path\.abspath\(r\) for r in (\[.*?\])\]\)", cmd[2])
    assert match, cmd[2]
    return ast.literal_eval(match.group(1))


def _pytest_argv(validator, calls):
    def run(cmd, cwd, **kwargs):
        calls.append(cmd)
        return {"returncode": 5, "stdout": "no tests ran", "stderr": "", "timeout": False}
    validator._run_cmd_with_timeout = run


def test_contained_pytest_bootstrap_carries_no_host_path_and_resolves_roots_in_the_child(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main" / "python").mkdir(parents=True)
    validator = _validator(tmp_path, contained=True)
    calls = []
    _pytest_argv(validator, calls)
    validator._resolve_python_interpreter = lambda: ("py-under-test", None)
    validator.run_tests(["tests"])
    bootstrap = calls[0][2]
    assert str(tmp_path) not in bootstrap, bootstrap
    assert _bootstrap_roots(calls[0]) == [".", "src", "src/main/python", "src/main"]


def test_host_mode_roots_are_realpath_equal_to_the_former_absolute_roots(tmp_path):
    (tmp_path / "src").mkdir()
    validator = _validator(tmp_path, contained=False)
    calls = []
    _pytest_argv(validator, calls)
    validator._resolve_python_interpreter = lambda: ("py-under-test", None)
    validator.run_tests(None)
    roots = _bootstrap_roots(calls[0])
    former = [str(tmp_path), os.path.join(str(tmp_path), "src")]
    resolved = [os.path.realpath(os.path.join(str(tmp_path), r)) for r in roots]
    assert resolved == [os.path.realpath(p) for p in former]


def test_host_mode_src_layout_imports_through_the_in_child_resolution(tmp_path):
    """A real pytest child on the host: a src-layout package with no manifest
    and a test under tests/ importing it - the resolved root makes the import
    work exactly as the absolute root did."""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("def answer():\n    return 42\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pkg.py").write_text("from pkg import answer\n\ndef test_answer():\n    assert answer() == 42\n")
    validator = _validator(tmp_path, contained=False)
    result = validator.run_tests(["tests/test_pkg.py"])
    assert result["success"] is True, result["output"]
    assert result["test_execution"]["status_counts"] == {"passed": 1}
