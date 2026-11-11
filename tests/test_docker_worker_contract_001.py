"""TEST-DOCKER-WORKER-CONTRACT-001: a test that starts a real container - itself or through a subprocess - runs on the single docker
worker. The escape this pins (MEASURED on the pre-live gate, b64bd4b): tests/test_prd032_chaos_commit.py's crash tests carried no docker
gate, and their crash subprocess started real containers from any xdist worker.

The tripwire itself is tests/_docker_worker_contract.py (see its docstring); these are its own controls, hermetic: the "real docker" here is
a recording script, never the daemon.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import _docker_worker_contract as contract
import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAOS = ROOT / "tests" / "test_prd032_chaos_commit.py"


def _fake_real_docker(tmp_path: Path) -> Path:
    real = tmp_path / "real" / "docker"
    real.parent.mkdir()
    real.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$FAKE_DOCKER_ARGV"\ncat\nexit 7\n', encoding="utf-8")
    real.chmod(0o755)
    return real


@pytest.fixture
def shimmed(tmp_path):
    """An environment whose PATH resolves ``docker`` to the shim in front of a recording fake."""
    real = _fake_real_docker(tmp_path)
    environ = {**os.environ, "PATH": str(real.parent) + os.pathsep + os.environ.get("PATH", ""), "FAKE_DOCKER_ARGV": str(tmp_path / "argv.log")}
    environ.pop(contract.ENV_LOG, None)
    log = contract.install(str(tmp_path / "shim"), environ)
    assert log and environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "shim")
    return environ, log, tmp_path / "argv.log"


def _docker(environ, test, *argv, through_a_child=False, stdin=""):
    env = {**environ, "PYTEST_CURRENT_TEST": test}
    command = ["docker", *argv]
    if through_a_child:        # the measured mechanism: the test starts Python, and THAT process starts the container
        command = [sys.executable, "-c", "import subprocess, sys; sys.exit(subprocess.run(sys.argv[1:]).returncode)", *command]
    return subprocess.run(command, env=env, input=stdin, capture_output=True, text=True, timeout=30)


def test_a_start_made_by_a_subprocess_of_the_test_is_attributed_to_the_test(shimmed):
    environ, log, argv_log = shimmed
    done = _docker(environ, "tests/test_x.py::test_indirect (call)", "run", "--rm", "image", "true", through_a_child=True, stdin="payload")
    assert done.returncode == 7 and done.stdout == "payload"                    # the real docker ran: same streams, same exit status
    assert argv_log.read_text().splitlines() == ["run --rm image true"]         # ... with the unchanged argv
    assert contract.starts_of(log, "tests/test_x.py::test_indirect") == ["run"]
    assert contract.starts_of(log, "tests/test_x.py::test_other") == []


def test_only_container_starts_are_recorded_and_global_options_are_skipped(shimmed):
    environ, log, argv_log = shimmed
    test = "tests/test_x.py::test_verbs (call)"
    for argv in (["image", "inspect", "run"], ["version"], ["ps", "-a"], ["rm", "-f", "start"], ["info"]):
        _docker(environ, test, *argv)
    assert contract.starts_of(log, "tests/test_x.py::test_verbs") == []
    for argv in (["--context", "x", "ps"],):                                  # a value of a global option is not a verb
        _docker(environ, test, *argv)
    for argv in (["run", "x"], ["-D", "create", "x"], ["start", "x"]):
        _docker(environ, test, *argv)
    assert contract.starts_of(log, "tests/test_x.py::test_verbs") == ["run", "create", "start"]
    assert len(argv_log.read_text().splitlines()) == 9                           # every call still reached the real docker


def test_the_decision_fails_only_an_ungrouped_starter(tmp_path):
    assert contract.violation("t::a", [], False) is None and contract.violation("t::a", ["run"], True) is None
    message = contract.violation("t::a", ["run", "run"], False)
    assert message and "t::a started 2 real container(s)" in message and "docker xdist group" in message
    target = tmp_path / "report.jsonl"
    contract.report(f"report:{target}", "t::a", ["run", "run"], False)
    contract.report(f"report:{target}", "t::b", [], False)                       # a test that started nothing is not reported
    contract.report("enforce", "t::c", ["run"], False)                           # enforce mode writes no report
    (line,) = target.read_text().splitlines()
    assert {key: json.loads(line)[key] for key in ("test", "starts", "docker_group")} == {"test": "t::a", "starts": 2, "docker_group": False}


def test_without_the_mode_nothing_is_installed_in_this_session(_docker_worker_contract_shim):
    if os.environ.get(contract.ENV_MODE):
        assert _docker_worker_contract_shim and os.environ[contract.ENV_LOG] == _docker_worker_contract_shim
    else:
        assert _docker_worker_contract_shim is None and contract.ENV_LOG not in os.environ


def _crash_callers():
    tree = ast.parse(CHAOS.read_text(encoding="utf-8"))
    return {node.name: [ast.unparse(d) for d in node.decorator_list] for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_crash" for c in ast.walk(node))}


def test_every_chaos_test_that_runs_the_crash_subprocess_carries_the_canonical_docker_gate():
    """The measured escapers, by construction: whoever calls ``_crash`` (the fresh Python process without the stand-in)."""
    import conftest
    import test_prd032_chaos_commit as chaos

    callers = _crash_callers()
    assert sorted(callers) == ["test_resume_from_an_uncertain_workspace_is_refused", "test_termination_after_intent_before_the_first_byte",
                               "test_termination_after_the_source_write_before_record_settlement",
                               "test_termination_between_staged_replaces_needs_explicit_recovery"]
    assert all("REAL_DOCKER" in decorators for decorators in callers.values()), callers
    assert chaos.REAL_DOCKER.name == "skipif" and "docker" in chaos.REAL_DOCKER.kwargs["reason"].lower()
    class _Item:                                                                  # what the canonical derivation reads of a collected test
        def __init__(self, function):
            self.function = function

        def iter_markers(self):
            return iter(self.function.pytestmark)

    for name in callers:                                                          # the canonical derivation sees the gate on the function itself
        assert conftest._needs_real_docker(_Item(getattr(chaos, name))), name


def test_the_crash_subprocess_inherits_the_path_and_the_current_test():
    """Why the shim sees it: the crash helper passes the worker's environment on (PATH and PYTEST_CURRENT_TEST included)."""
    tree = ast.parse(CHAOS.read_text(encoding="utf-8"))
    crash = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_crash")
    (call,) = [c for c in ast.walk(crash) if isinstance(c, ast.Call) and ast.unparse(c.func) == "subprocess.run"]
    assert ast.unparse(next(k.value for k in call.keywords if k.arg == "env")) == "dict(os.environ, PYTHONPATH=TESTS_DIR)"


def test_no_production_module_knows_the_tripwire():
    for root in ("kriya", "plugins"):
        for path in (ROOT / root).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert contract.ENV_MODE not in text and contract.ENV_LOG not in text and "_docker_worker_contract" not in text, path
