"""TEST-WORKTREE-POLLUTION-001 regression: the full suite used to leave
package.json, package-lock.json and node_modules/ (left-pad) in the
repository root. Producer (TRACED, 2026-10-07): ShellTool._run spawns
`/bin/sh -c <command>` with cwd = the process cwd, and the profile tests in
tests/test_sec005_shell_acquisition_network.py / test_tool001_* /
test_prd012_egress.py captured the profile through DummyContainmentBackend,
which contains nothing - so `npm install left-pad` ran the real npm in the
pytest cwd (reproduced with one test alone, evidence in
~/kriya-m1-live/backend-readiness-001/pollution/discriminate). Fix: those
tests use ProfileCapturingBackend, which records the profile and executes
nothing; tests/conftest.py fails any test that creates the artifacts."""
import ast
import os
import pathlib

import pytest
from _plugin_test_support import load_core_tools_module
from _strict_doubles import NO_EXEC_COMMAND_PREFIX, ProfileCapturingBackend
from _worktree_pollution import PACKAGE_ARTIFACT_NAMES, new_package_artifacts, present_package_artifacts

from kriya.config import AppConfig
from kriya.tools.containment import NetworkAuthority

TESTS = pathlib.Path(__file__).resolve().parent
_core_tools = load_core_tools_module()


@pytest.mark.asyncio
async def test_profile_capturing_backend_records_the_profile_and_executes_nothing(tmp_path, monkeypatch):
    """The command would create a file; with the no-exec backend it never runs,
    while ShellTool still builds and hands over the real profile."""
    marker = tmp_path / "ran"
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.acquisition_registry_hosts = ["repo.maven.apache.org"]
    backend = ProfileCapturingBackend()
    monkeypatch.setattr(_core_tools, "resolve_containment_backend", lambda name: backend)
    monkeypatch.chdir(tmp_path)
    result = await _core_tools.ShellTool(autonomy_cfg=cfg.autonomy).execute(
        command=f"touch {marker} && npm install left-pad")
    assert not marker.exists()
    assert result["exit_code"] == 0 and result["stdout"] == ""
    assert present_package_artifacts(str(tmp_path)) == []
    assert [p.network for p in backend.prepared_profiles] == [NetworkAuthority.DENIED]  # npm: unmapped


def test_no_exec_prefix_is_a_real_executable_that_ignores_its_arguments():
    assert os.access(NO_EXEC_COMMAND_PREFIX[0], os.X_OK)
    assert os.spawnv(os.P_WAIT, NO_EXEC_COMMAND_PREFIX[0], NO_EXEC_COMMAND_PREFIX + ["/bin/sh", "-c", "exit 7"]) == 0


def test_new_package_artifacts_reports_only_what_appeared(tmp_path):
    root = str(tmp_path)
    assert present_package_artifacts(root) == []
    (tmp_path / "package.json").write_text("{}")
    before = present_package_artifacts(root)
    assert before == ["package.json"]
    assert new_package_artifacts(root, before) == []
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "package-lock.json").write_text("{}")
    assert new_package_artifacts(root, before) == ["package-lock.json", "node_modules"]
    assert new_package_artifacts(root, ()) == list(PACKAGE_ARTIFACT_NAMES)


def _shelltool_profile_test_files():
    """Every test module that captures a ShellTool containment profile."""
    return sorted(path for path in TESTS.glob("test_*.py")
                  if "resolve_containment_backend" in path.read_text(encoding="utf-8"))


def test_shelltool_profile_tests_never_capture_through_an_executing_backend():
    """Mutation control: restoring DummyContainmentBackend in any ShellTool
    profile test fails here (and, on a host with npm, the conftest tripwire)."""
    files = _shelltool_profile_test_files()
    assert {p.name for p in files} >= {"test_sec005_shell_acquisition_network.py",
                                       "test_tool001_autonomous_tool_execution.py", "test_prd012_egress.py"}
    offenders = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "DummyContainmentBackend"
                    and _assigned_to_resolve_backend(tree, node)):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], offenders


def _assigned_to_resolve_backend(tree, call):
    """The call's result is the backend `resolve_containment_backend` is patched
    to return (the only way a ShellTool command reaches it)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.value is call and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            return any(isinstance(n, ast.Lambda) and isinstance(n.body, ast.Name) and n.body.id == name
                       for n in ast.walk(tree))
    return False
