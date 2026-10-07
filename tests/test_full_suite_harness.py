"""scripts/run_full_suite.py: the canonical full-suite entry point refuses a
run whose prerequisites are unmet, builds the repository's invocation, and
detects TEST-WORKTREE-POLLUTION-001 artifacts. Every check is exercised
through injected probes; nothing here touches Docker, Java or the venv."""
import ast
import importlib.util
import os
import pathlib

import pytest
from _worktree_pollution import PACKAGE_ARTIFACT_NAMES

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_full_suite.py"


def _harness():
    spec = importlib.util.spec_from_file_location("run_full_suite", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _harness()


def _venv(root: pathlib.Path) -> pathlib.Path:
    python = pathlib.Path(_harness().venv_python(str(root)))
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    return python


def _all_present(name):
    return f"/usr/bin/{name}"


def test_preflight_passes_when_every_prerequisite_is_met(harness, tmp_path):
    _venv(tmp_path)
    assert harness.preflight(str(tmp_path), which=_all_present, run=lambda cmd: 0, system="Linux", machine="x86_64",
                             nofile_hard_limit=1024) == []


def test_preflight_names_every_missing_tool_and_an_unreachable_daemon(harness, tmp_path):
    _venv(tmp_path)
    problems = harness.preflight(str(tmp_path), which=lambda name: None if name in ("jdtls", "mvn") else _all_present(name),
                                 run=lambda cmd: 1 if cmd[:2] == ["docker", "info"] else 0,
                                 system="Linux", machine="x86_64", nofile_hard_limit=1024)
    assert [p.split(" ")[0] for p in problems] == ["`jdtls`", "`mvn`", "the"]
    assert "docker info" in problems[2]


def test_preflight_refuses_without_the_repository_venv_or_xdist(harness, tmp_path):
    assert harness.preflight(str(tmp_path), which=_all_present, run=lambda cmd: 0, system="Linux", machine="x86_64",
                             nofile_hard_limit=1024)[0].startswith("no repository venv")
    _venv(tmp_path)
    problems = harness.preflight(str(tmp_path), which=_all_present, run=lambda cmd: 1 if "import pytest" in cmd[-1] else 0,
                                 system="Linux", machine="x86_64", nofile_hard_limit=1024)
    assert problems == ["pytest or pytest-xdist is not importable from the repository venv"]


def test_preflight_flags_a_foreign_jvm_only_on_apple_silicon_without_rosetta(harness, tmp_path):
    _venv(tmp_path)
    jvms = ["/Library/Java/JavaVirtualMachines/jdk1.8.0_25.jdk/Contents/Home/bin/java",
            "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin/java"]
    archs = {jvms[0]: "x86_64\n", jvms[1]: "arm64\n"}
    common = dict(which=_all_present, run=lambda cmd: 0, java_binaries=jvms, archs_of=archs.__getitem__,
                  nofile_hard_limit=1024)
    foreign = harness.preflight(str(tmp_path), system="Darwin", machine="arm64", rosetta_present=lambda: False, **common)
    assert len(foreign) == 1 and foreign[0].startswith(jvms[0]) and "Rosetta" in foreign[0]
    assert harness.preflight(str(tmp_path), system="Darwin", machine="arm64", rosetta_present=lambda: True, **common) == []
    assert harness.preflight(str(tmp_path), system="Linux", machine="x86_64", rosetta_present=lambda: False, **common) == []
    assert harness.macos_foreign_jvms([jvms[1]], archs.__getitem__, lambda: False) == []


def test_preflight_requires_the_file_descriptor_limit_to_be_reachable(harness, tmp_path):
    _venv(tmp_path)
    low = harness.preflight(str(tmp_path), which=_all_present, run=lambda cmd: 0, system="Linux", machine="x86_64",
                            nofile_hard_limit=100)
    assert low == ["the hard open-file limit 100 is below the required 256"]
    assert harness.preflight(str(tmp_path), which=_all_present, run=lambda cmd: 0, system="Linux", machine="x86_64",
                             nofile_hard_limit=-1) == []  # RLIM_INFINITY


def test_the_invocation_is_the_repository_worker_contract_and_keeps_extra_pytest_args(harness):
    command = harness.pytest_command("/repo", 8, ["--ff", "-p", "no:cacheprovider"])
    assert command[1:] == ["-m", "pytest", "-q", "-n", "8", "--dist", "loadgroup", "--ff", "-p", "no:cacheprovider"]
    assert command[0] == harness.venv_python("/repo")
    assert not any(arg.startswith("-m ") or arg == "live_model" for arg in command)  # exclusions stay in pyproject


def test_the_child_environment_puts_the_repository_root_first_on_pythonpath(harness):
    assert harness.child_environment("/repo", {"HOME": "/h"})["PYTHONPATH"] == "/repo"
    env = harness.child_environment("/repo", {"PYTHONPATH": "/other"})
    assert env["PYTHONPATH"] == os.pathsep.join(["/repo", "/other"])


def test_package_artifacts_are_detected_even_as_dangling_symlinks(harness, tmp_path):
    assert harness.present_package_artifacts(str(tmp_path)) == []
    (tmp_path / "node_modules").symlink_to(tmp_path / "missing")
    (tmp_path / "package.json").write_text("{}")
    assert harness.present_package_artifacts(str(tmp_path)) == ["package.json", "node_modules"]


def test_main_refuses_with_exit_2_and_runs_nothing_when_a_prerequisite_is_missing(harness, monkeypatch, capsys):
    monkeypatch.setattr(harness, "preflight", lambda root: ["`docker` is not on PATH"])
    monkeypatch.setattr(harness.subprocess, "call", lambda *a, **k: pytest.fail("pytest must not run"))
    assert harness.main([]) == 2
    assert "PREREQUISITE: `docker` is not on PATH" in capsys.readouterr().err


def test_main_fails_with_exit_3_when_the_run_pollutes_the_repository_root(harness, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(harness, "ROOT", str(tmp_path))
    monkeypatch.setattr(harness, "preflight", lambda root: [])
    monkeypatch.setattr(harness, "_apply_nofile_limit", lambda: None)

    def polluting_pytest(command, cwd, env):
        assert cwd == str(tmp_path) and env["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path)
        assert command[3:8] == ["-q", "-n", "8", "--dist", "loadgroup"]
        (tmp_path / "package.json").write_text("{}")
        return 0

    monkeypatch.setattr(harness.subprocess, "call", polluting_pytest)
    assert harness.main([]) == 3
    assert "TEST-WORKTREE-POLLUTION-001: the run created package.json" in capsys.readouterr().err
    (tmp_path / "package.json").unlink()
    monkeypatch.setattr(harness.subprocess, "call", lambda command, cwd, env: 5)
    assert harness.main(["--workers", "2"]) == 5  # pytest's own exit code passes through
    assert "-n 2 --dist loadgroup" in capsys.readouterr().out


def test_preflight_only_prints_the_effective_command_without_running(harness, monkeypatch, capsys):
    monkeypatch.setattr(harness, "preflight", lambda root: [])
    monkeypatch.setattr(harness.subprocess, "call", lambda *a, **k: pytest.fail("pytest must not run"))
    assert harness.main(["--preflight-only", "--", "-x"]) == 0
    out = capsys.readouterr().out
    assert "--dist loadgroup -x" in out and "ulimit -n 256" in out and "PYTHONPATH=" in out


def test_the_full_suite_harness_checks_the_same_artifact_names():
    harness = SCRIPT.read_text(encoding="utf-8")
    names = next(node for node in ast.parse(harness).body
                 if isinstance(node, ast.Assign) and node.targets[0].id == "PACKAGE_ARTIFACT_NAMES")
    assert tuple(ast.literal_eval(names.value)) == PACKAGE_ARTIFACT_NAMES
