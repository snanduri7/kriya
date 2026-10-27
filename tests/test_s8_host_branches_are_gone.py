"""S8: the legacy PROJECT-CONTROLLED host-execution branches are gone, and the sites that used to choose between host and container now only contain.

The S7 guards pin that nothing but the retained record readers reads the decision's ``contained`` flag (tests/test_s7_s8_list.py). This file is the negative proof at the sites
themselves: each deleted host branch is absent from the code (a symbol, a module, a return shape), and each edited site hands its process a containment profile and backend
and nothing else. A branch that comes back - even one the decision would never select - fails here, with the site named."""
import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _function(relpath: str, qualname: str) -> ast.AST:
    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names, current = [], node
            while current in parents or isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(current.name)
                if current not in parents:
                    break
                current = parents[current]
            if ".".join(reversed(names)) == qualname:
                return node
    raise AssertionError(f"{relpath}::{qualname} not found")


def _calls(node: ast.AST) -> set:
    return {call.func.id if isinstance(call.func, ast.Name) else call.func.attr
            for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, (ast.Name, ast.Attribute))}


def _names(node: ast.AST) -> set:
    """Every identifier and attribute the CODE uses (never a docstring or a comment)."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


# ------------------------------------------------------------------ symbols and modules that carried a host branch are gone

def test_the_host_venv_seal_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("kriya.tools.venv_integrity")
    assert not (ROOT / "kriya" / "tools" / "venv_integrity.py").exists()


@pytest.mark.parametrize("owner,name", [
    ("kriya.static_analysis.adapters.semgrep.SemgrepAdapter", "_contained"),
    ("kriya.static_analysis.adapters.semgrep.SemgrepAdapter", "_host_env"),
    ("kriya.static_analysis.adapters.semgrep.SemgrepAdapter", "_host_scratch"),
    ("kriya.static_analysis.adapters.semgrep.SemgrepAdapter", "_resolve_executable"),
    ("kriya.static_analysis.adapters.semgrep", "LIMITATION_HOST_NETWORK"),
])
def test_the_host_provider_pieces_of_the_static_analysis_adapter_are_gone(owner, name):
    module_name, _, attribute = owner.rpartition(".")
    try:
        target = getattr(importlib.import_module(module_name), attribute)
    except (ModuleNotFoundError, AttributeError):
        target = importlib.import_module(owner)
    assert not hasattr(target, name), f"{owner}.{name} is back"


def test_the_static_analysis_context_has_no_host_mode_flag():
    from kriya.static_analysis.port import ExecutionContext

    assert [field for field in ExecutionContext.__dataclass_fields__] == ["containment_backend", "workspace_root"]


# ------------------------------------------------------------------ the sites that used to choose now only contain

def test_the_shell_tool_always_builds_a_containment_profile_and_never_runs_the_command_bare():
    node = _function("plugins/core_tools/__init__.py", "ShellTool._run")
    assigned_none = [target.id for stmt in ast.walk(node) if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) and stmt.value.value is None
                     for target in stmt.targets if isinstance(target, ast.Name) and target.id in ("profile", "backend")]
    assert assigned_none == [], "ShellTool._run can pass no containment profile/backend again"
    assert {"ContainmentProfile", "require_isolating"} <= _calls(node)


def test_the_validator_never_returns_a_host_profile_and_the_maven_and_gradle_runners_have_no_host_pass_through():
    profile_node = _function("kriya/tools/validate.py", "PolymorphicValidator.build_containment_profile_and_backend")
    assert not [stmt for stmt in ast.walk(profile_node) if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Tuple)
                and all(isinstance(e, ast.Constant) and e.value is None for e in stmt.value.elts)], "the (None, None) host return is back"
    for qualname in ("PolymorphicValidator._run_maven_cmd", "PolymorphicValidator._run_gradle_cmd"):
        node = _function("kriya/tools/validate.py", qualname)
        first = [stmt for stmt in node.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))][0]
        assert not isinstance(first, ast.If), f"{qualname} starts with a branch again (the host pass-through)"


def test_the_validator_has_no_host_interpreter_or_host_venv_branch():
    assert "venv_integrity" not in (ROOT / "kriya/tools/validate.py").read_text(encoding="utf-8")
    for qualname in ("PolymorphicValidator._resolve_python_interpreter", "PolymorphicValidator._ensure_project_venv_impl", "PolymorphicValidator._ensure_project_venv",
                     "PolymorphicValidator._resolve_toolchain_identity", "PolymorphicValidator.resolve_maven_classpath"):
        node = _function("kriya/tools/validate.py", qualname)
        assert "external_execution" not in _calls(node), f"{qualname} asks the owner for a host/contained choice again"
        assert "contained" not in _names(node), f"{qualname} reads a contained flag again"
        assert "executable" not in _names(node) and "venv_python_host" not in _names(node) or qualname.endswith("_ensure_project_venv_impl"), qualname
    assert "sys" not in _names(_function("kriya/tools/validate.py", "PolymorphicValidator._resolve_python_interpreter"))


def test_the_static_analysis_adapter_runs_only_through_a_containment_profile_and_backend():
    for qualname in ("SemgrepAdapter.scan", "SemgrepAdapter._run_probe"):
        node = _function("kriya/static_analysis/adapters/semgrep.py", qualname)
        run_calls = [call for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "run"]
        assert run_calls, qualname
        for call in run_calls:
            assert {"containment_profile", "containment_backend"} <= {kw.arg for kw in call.keywords}, f"{qualname}: a run without containment"
            assert "env" not in {kw.arg for kw in call.keywords}, f"{qualname}: a host environment is passed again"


def test_jdtls_has_no_host_spawn_left_only_the_typed_refusal():
    node = _function("kriya/tools/lsp.py", "JdtlsClient.start")
    assert _calls(node) == {"refuse_unsupported", "decide_external_execution"}
    source = (ROOT / "kriya/tools/lsp.py").read_text(encoding="utf-8")
    assert "spawn_subprocess_exec_fail_closed" not in source and "create_subprocess" not in source


def test_the_mcp_manager_and_the_finite_command_preparation_hand_over_containment_unconditionally():
    node = _function("kriya/mcp/mcp.py", "MCPManager.start_all")
    constructor = next(call for call in ast.walk(node) if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "MCPClient")
    required = next(kw.value for kw in constructor.keywords if kw.arg == "containment_required")
    assert isinstance(required, ast.Constant) and required.value is True
    prepare = _function("kriya/workflow/runtime_verification.py", "_prepare_finite_command_runtime_artifacts")
    assert "contained_maven_build" in _names(prepare)
    assert not {"build_subprocess_env_and_preexec", "external_execution"} & (_names(prepare) | _calls(prepare))
    runtime = ast.parse((ROOT / "kriya/workflow/runtime_verification.py").read_text(encoding="utf-8"))
    managed = [kw for call in ast.walk(runtime) if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "run_managed_service_verification"
               for kw in call.keywords if kw.arg == "contained_build"]
    assert managed and all(isinstance(kw.value, ast.Attribute) and kw.value.attr == "contained_maven_build" for kw in managed)


def test_the_authority_bundle_has_no_not_contained_refusal_left_to_fall_past():
    node = _function("kriya/workflow/authority_bundle.py", "run_authority_bundle")
    assert "decide_external_execution" not in _calls(node)


# ----------------------------------------------------------------------------------------- S8-ORPHAN-HOST-CODE-001: the orphan host code, per family

def _defined_names(relpath: str) -> set:
    return {n.name for n in ast.walk(ast.parse((ROOT / relpath).read_text(encoding="utf-8"))) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _production_sources():
    return [path for base in ("kriya", "plugins") for path in (ROOT / base).rglob("*.py")]


def test_the_host_semgrep_engine_identity_helper_and_its_only_consumer_are_gone():
    """Family A. `host_executable_digest` had no caller anywhere; `_file_sha256` of the adapter existed only for it. No source references either name again."""
    defined = _defined_names("kriya/static_analysis/adapters/semgrep.py")
    assert not {"host_executable_digest", "_file_sha256"} & defined
    offenders = [str(path.relative_to(ROOT)) for path in _production_sources() if "host_executable_digest" in path.read_text(encoding="utf-8")]
    assert offenders == [], f"host_executable_digest is referenced again: {offenders}"


def test_the_validator_has_no_host_env_and_preexec_policy_and_its_one_spawn_is_contained():
    """Family B. `build_subprocess_env_and_preexec` / `host_resource_plan` fed only the host tail of `_run_cmd_with_timeout`, reachable only through a (None, None) profile the builder never returns."""
    defined = _defined_names("kriya/tools/validate.py")
    assert not {"build_subprocess_env_and_preexec", "host_resource_plan"} & defined
    node = _function("kriya/tools/validate.py", "PolymorphicValidator._run_cmd_with_timeout")
    runs = [call for call in ast.walk(node) if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "run"]
    assert len(runs) == 1, "exactly one spawn in the validator's command runner"
    assert {"containment_profile", "containment_backend"} <= {kw.arg for kw in runs[0].keywords}
    assert not {"env", "preexec_fn"} & {kw.arg for kw in runs[0].keywords}, "a host environment is passed again"
    assert "ContainmentSetupError" in _names(node), "a missing profile/backend is the typed refusal"


def test_a_validator_without_a_profile_or_backend_refuses_and_spawns_nothing(tmp_path):
    """Family B negative control, behavioural: the (None, None) the builder used to be able to return is a typed refusal now, and no process is started."""
    from unittest.mock import patch

    from kriya.config.config import AutonomyConfig
    from kriya.tools.containment import ContainmentSetupError
    from kriya.tools.validate import PolymorphicValidator
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig())
    for pair in ((None, None), (object(), None), (None, object())):
        with patch.object(PolymorphicValidator, "build_containment_profile_and_backend", return_value=pair), \
                patch("kriya.tools.validate.ProcessController.run") as run, pytest.raises(ContainmentSetupError, match="no host mode"):
            validator._run_cmd_with_timeout(["echo", "x"], cwd=str(tmp_path))  # pylint: disable=protected-access
        assert run.call_count == 0, f"{pair}: a process was started"


def test_the_mcp_launcher_has_no_host_spawn_branch_left():
    """Family C. `MCPClient._start_sequence` is the live launcher; only its `containment_required=False` raw host spawn was the orphan. It no longer reads the flag, always asks the owner for an
    isolating backend, and always spawns through the prepared prefix; the constructor refuses a client that does not require containment."""
    node = _function("kriya/mcp/mcp.py", "MCPClient._start_sequence")
    assert "_containment_required" not in _names(node), "the launcher branches on the containment flag again"
    assert "require_isolating_backend" in _calls(node)
    spawn = next(call for call in ast.walk(node) if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "spawn_mcp_process")
    prefix = next(kw.value for kw in spawn.keywords if kw.arg == "command_prefix")
    assert isinstance(prefix, ast.Name) and prefix.id == "command_prefix"
    assigned = [stmt for stmt in ast.walk(node) if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "command_prefix" for t in stmt.targets)]
    assert all(not (isinstance(stmt.value, ast.Constant) and stmt.value.value is None) for stmt in assigned), "command_prefix can be None (a host spawn) again"
    constructor = _function("kriya/mcp/mcp.py", "MCPClient.__init__")
    assert "ContainmentSetupError" in _calls(constructor) | _names(constructor)


JDTLS_CONTROL = "test_jdtls_has_no_process_machinery_and_cannot_be_started"


def test_jdtls_has_no_process_machinery_and_cannot_be_started():
    """Family D. jdtls has no contained mode, so `start()` is always the typed refusal and no process can exist; the stdio framing, reader loop, requests, private candidate mirror and tree-kill
    shutdown that used to follow it were unreachable and are gone. This is also why jdtls can never write the candidate (the premise of the retired mirror tests): nothing of it runs."""
    from kriya.config.config import AutonomyConfig
    from kriya.tools.lsp import JdtlsClient
    methods = {n.name for n in ast.walk(ast.parse((ROOT / "kriya/tools/lsp.py").read_text(encoding="utf-8"))
                                        .body[next(i for i, n in enumerate(ast.parse((ROOT / "kriya/tools/lsp.py").read_text(encoding="utf-8")).body)
                                                   if isinstance(n, ast.ClassDef) and n.name == "JdtlsClient")]) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert methods == {"__init__", "start"}, f"JdtlsClient grew machinery again: {sorted(methods)}"
    source = (ROOT / "kriya/tools/lsp.py").read_text(encoding="utf-8")
    for banned in ("subprocess", "terminate_process_tree", "killpg", "create_subprocess", "tempfile", "os.walk", "shutil.copy", "_mirror"):
        assert banned not in source, f"lsp.py mentions {banned!r} again"
    client = JdtlsClient("/project", "/opt/jdtls", AutonomyConfig())
    assert not hasattr(client, "process"), "a process attribute exists on a client that can never start"
    # the refusal itself is pinned by tests/test_s7_secure_default_contract.py::test_jdtls_is_never_started_on_the_host (a test that consults the decision is census-classified there)


# ----------------------------------------------------------------------------------------- S8-HELD-SERVICE-RUNTIME-HOST-HARNESS-001: the managed-service layer has no host mode

def test_service_runtime_has_no_host_launch_or_host_build_left():
    """The host-capable remainder (Optional containment arguments that launched the service bare, the host `controller.run` build with env/preexec_fn, and the project's ./mvnw wrapper) is gone; the
    ~45 lifecycle/artifact tests that borrowed it as a harness run through the test-owned containment stand-in and the production `contained_build` seam instead."""
    import inspect

    from kriya.tools import service_runtime
    for name in ("_prepare_required_artifact", "run_managed_service_verification"):
        assert not {"env", "preexec_fn"} & set(inspect.signature(getattr(service_runtime, name)).parameters), f"{name} accepts a host environment again"
    assert list(inspect.signature(service_runtime._maven_package_command).parameters) == ["cwd"], "the project-wrapper switch is back"  # pylint: disable=protected-access
    node = _function("kriya/tools/service_runtime.py", "_maven_package_command")
    assert "mvnw" not in {c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)}, "the project's wrapper is considered again"
    for qualname in ("_prepare_required_artifact", "run_managed_service_verification"):
        assert "ContainmentSetupError" in _names(_function("kriya/tools/service_runtime.py", qualname)), f"{qualname} no longer refuses a missing containment context"
    prepare = _function("kriya/tools/service_runtime.py", "_prepare_required_artifact")
    for call in ast.walk(prepare):
        if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "run":
            assert {"containment_profile", "containment_backend"} <= {kw.arg for kw in call.keywords}
            assert not {"env", "preexec_fn"} & {kw.arg for kw in call.keywords}


def test_a_managed_service_run_or_build_without_containment_is_refused_and_starts_nothing(tmp_path):
    """Behavioural: project-controlled host execution cannot reach service_runtime. No profile/backend -> typed refusal, and the controller is never asked to run or start anything."""
    import sys

    from kriya.tools.containment import ContainmentSetupError
    from kriya.tools.process import ProcessController
    from kriya.tools.service_runtime import (
        ManagedServiceVerificationSpec,
        ProbeSpec,
        ReadinessSpec,
        _prepare_required_artifact,
        run_managed_service_verification,
    )

    class _Watching(ProcessController):
        def __init__(self):
            super().__init__()
            self.started = []

        def run(self, command, **kwargs):
            self.started.append(("run", list(command)))
            raise AssertionError("a process was run")

        def start_managed(self, command, **kwargs):
            self.started.append(("start_managed", list(command)))
            raise AssertionError("a process was started")

    spec = ManagedServiceVerificationSpec(service_command=[sys.executable, "-c", "pass"], cwd=str(tmp_path),
                                          readiness=ReadinessSpec(kind="tcp", port=1), probe=ProbeSpec(port=1))
    for kwargs in ({}, {"containment_profile": object()}, {"containment_backend": object()}):
        controller = _Watching()
        with pytest.raises(ContainmentSetupError, match="no host mode"):
            run_managed_service_verification(spec, controller=controller, **kwargs)
        assert controller.started == [], f"{kwargs}: a process was started"
    # the build step: an artifact is required, a pom exists, and no contained build or containment context is given
    (tmp_path / "pom.xml").write_text("<project/>")
    controller = _Watching()
    with pytest.raises(ContainmentSetupError, match="no host mode"):
        _prepare_required_artifact(["java", "-jar", "target/app.jar"], str(tmp_path), controller=controller)
    assert controller.started == []
    with pytest.raises(TypeError):
        _prepare_required_artifact(["java", "-jar", "target/app.jar"], str(tmp_path), controller=controller, env={"A": "b"})  # pylint: disable=unexpected-keyword-arg


# ----------------------------------------------------------------------------------------- SPAWN-MCP-PROCESS-HOST-MODE-001: spawn_mcp_process has no host mode

def test_spawn_mcp_process_requires_a_containment_prefix_and_has_no_host_rlimit_branch():
    import inspect

    from kriya.mcp import lifecycle
    parameters = inspect.signature(lifecycle.spawn_mcp_process).parameters
    prefix = parameters["command_prefix"]
    assert prefix.default is inspect.Parameter.empty and prefix.kind is inspect.Parameter.KEYWORD_ONLY, "the launch prefix is optional again"
    assert not {"cpu_seconds", "memory_mb"} & set(parameters), "the host rlimit parameters are back"
    node = _function("kriya/mcp/lifecycle.py", "spawn_mcp_process")
    assert "posix_resource_limits_preexec_fn" not in _names(node) | _calls(node) and "preexec_fn" not in {kw.arg for c in ast.walk(node) if isinstance(c, ast.Call) for kw in c.keywords}
    imported = {alias.name for n in ast.walk(ast.parse((ROOT / "kriya/mcp/lifecycle.py").read_text(encoding="utf-8"))) if isinstance(n, ast.ImportFrom) for alias in n.names}
    assert "posix_resource_limits_preexec_fn" not in imported, "the host rlimit helper is imported again"
    assert "ContainmentSetupError" in _names(node), "an empty prefix is no longer refused"

    def calls_it(path):
        return any(isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", None)) == "spawn_mcp_process"
                   for c in ast.walk(ast.parse(path.read_text(encoding="utf-8"))))
    callers = [str(p.relative_to(ROOT)) for p in _production_sources() if calls_it(p)]
    assert callers == ["kriya/mcp/mcp.py"], f"another production caller of spawn_mcp_process appeared: {callers}"


def test_spawn_mcp_process_refuses_a_missing_or_empty_prefix_and_spawns_nothing_but_composes_the_prefix_exactly():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from kriya.mcp import lifecycle
    from kriya.tools.containment import ContainmentSetupError
    with patch.object(lifecycle, "spawn_subprocess_exec_fail_closed", new=AsyncMock()) as spawn:
        for prefix in ([], None):
            with pytest.raises(ContainmentSetupError, match="no host mode"):
                asyncio.run(lifecycle.spawn_mcp_process("srv", ["--x"], None, max_stdout_line_bytes=10, command_prefix=prefix))
        with pytest.raises(TypeError):
            asyncio.run(lifecycle.spawn_mcp_process("srv", ["--x"], None, max_stdout_line_bytes=10))  # pylint: disable=missing-kwoa
        assert spawn.call_count == 0, "a process was spawned without a containment prefix"
        asyncio.run(lifecycle.spawn_mcp_process("srv", ["--x"], None, max_stdout_line_bytes=10, command_prefix=["docker", "run", "-i", "img"]))
        assert spawn.call_args.args == ("docker", "run", "-i", "img", "srv", "--x"), "the prefix is composed ahead of the server command, verbatim"
