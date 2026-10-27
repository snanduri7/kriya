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
    node = _function("kriya/tools/lsp.py", "JdtlsClient._start")
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
