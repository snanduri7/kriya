"""D2 EXEC-LAYER S2: ONE canonical decision "does this external workload run in a real container?" (kriya/tools/containment.py).

Before S2 each consumer read ``contained_execution_required`` / ``containment_backend`` and decided for itself (the validator at nine places, ShellTool,
the pip capability, static analysis, the acceptance-bundle runner, the egress report, the toolchain fingerprint, the identity digests), and some consumers
(JDTLS) never asked. A copy of the decision per site is how a site gets missed, and it is why activating mandatory containment would have meant a
consumer hunt. Now:

- every production READ of the fields (an attribute, ``getattr``, a constant subscript) outside the owner is in the closed table below, each with a
  reason, and the only reads that remain are the owner's, a backend OBJECT held by the static-analysis context, and two places that RECORD the configured
  backend name without deciding anything (S5 moved MCP's own flag, and its three decision consumers, into the owner);
- every workload launch site of the S1 census is either routed through the owner (its boundary function is checked to call it) or explicitly PENDING the
  slice that owns it (S4/S5), so S2 cannot claim a site it did not route;
- the one production caller of JDTLS passes the configuration the decision is made from.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from typing import Dict, List, Set, Tuple

import pytest

from . import execution_site_inventory as inv
from .execution_sites import REPO, ROOTS

FIELDS = {"contained_execution_required", "containment_backend", "mcp_contained_execution_required"}

OWNER, BACKEND_OBJECT, RECORD = "OWNER", "BACKEND_OBJECT", "RECORD"
# (file, function) -> (kind, why). A new read anywhere else fails until it is classified (or, better, routed through the owner).
READS: Dict[Tuple[str, str], Tuple[str, str]] = {
    # S7: ``_containment_is_mandatory`` is unconditional and reads NO configuration field, so it has no entry here: this table failing on a read inside it is the tripwire.
    ("kriya/tools/containment.py", "decide_external_execution"): (OWNER, "the configured backend name enters the decision here"),
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter._containment_ready"): (
        BACKEND_OBJECT, "``self._context.containment_backend`` is the backend OBJECT the owner's decision resolved, not the configuration field"),
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter._run_probe"): (BACKEND_OBJECT, "the same context attribute (a backend object)"),
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter.scan"): (BACKEND_OBJECT, "the same context attribute (a backend object)"),
    ("kriya/workflow/egress_authority.py", "describe_run_egress"): (RECORD, "displays the configured backend name beside each decision; every decision itself is the owner's (S5)"),
    ("kriya/workflow/subtask_executor.py", "plannable_tool_names"): (RECORD, "passes the configured backend name to the planned-tool governance resolution, which records it and decides nothing"),
}


def _reads() -> Dict[Tuple[str, str], Set[str]]:
    found: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not any(name in source for name in FIELDS):
                continue
            tree = ast.parse(source)
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.Attribute) and node.attr in FIELDS:
                    name = node.attr
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2
                      and isinstance(node.args[1], ast.Constant) and node.args[1].value in FIELDS):
                    name = node.args[1].value
                elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in FIELDS:
                    name = node.slice.value
                if name is None:
                    continue
                names: List[str] = []
                current = node
                while current in parents:
                    current = parents[current]
                    if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.append(current.name)
                found[(path.relative_to(REPO).as_posix(), ".".join(reversed(names)) or "<module>")].add(name)
    return dict(found)


def test_no_production_module_reads_the_containment_fields_outside_the_classified_table():
    scanned = set(_reads())
    classified = set(READS)
    assert scanned == classified, (
        f"unclassified reads (route them through kriya.tools.containment.decide_external_execution): {sorted(scanned - classified)}; "
        f"stale entries: {sorted(classified - scanned)}")


def test_only_the_owner_reads_the_non_mcp_fields():
    """``contained_execution_required``, ``mcp_contained_execution_required`` and the configured backend NAME are read by the owner and by no one else
    (except two places that only record the name); the static-analysis context's ``containment_backend`` is a backend object."""
    for (path, function), names in _reads().items():
        kind = READS[(path, function)][0]
        if kind == OWNER:
            continue
        assert "contained_execution_required" not in names, f"{path}::{function} reads contained_execution_required outside the owner"
        assert "mcp_contained_execution_required" not in names, f"{path}::{function} reads the MCP mandate outside the owner"
        if kind == RECORD:
            assert names <= {"containment_backend"}, (path, function, names)


def test_the_activation_point_is_one_function_that_every_decision_goes_through():
    from kriya.tools import containment

    tree = ast.parse((REPO / "kriya/tools/containment.py").read_text(encoding="utf-8"))
    callers = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
               and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_containment_is_mandatory"
                       for call in ast.walk(node))]
    assert callers == ["decide_external_execution"], callers
    assert containment._containment_is_mandatory.__name__ == "_containment_is_mandatory"  # pylint: disable=protected-access


# ------------------------------------------------------------------ every workload launch site: routed or explicitly pending

SITE = Tuple[str, str]
# site -> the function that asks the owner at that site's boundary, and the name it calls
ROUTED: Dict[SITE, Tuple[SITE, str]] = {
    ("kriya/tools/validate.py", "PolymorphicValidator._run_cmd_with_timeout"): (
        ("kriya/tools/validate.py", "PolymorphicValidator.build_containment_profile_and_backend"), "external_execution"),
    ("plugins/core_tools/__init__.py", "ShellTool._run"): (("plugins/core_tools/__init__.py", "ShellTool._run"), "decide_external_execution"),
    ("kriya/tools/lsp.py", "JdtlsClient._start"): (("kriya/tools/lsp.py", "JdtlsClient._start"), "decide_external_execution"),
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter._run_probe"): (
        ("kriya/static_analysis/service.py", "execution_context"), "decide_external_execution"),
    ("kriya/static_analysis/adapters/semgrep.py", "SemgrepAdapter.scan"): (
        ("kriya/static_analysis/service.py", "execution_context"), "decide_external_execution"),
    # S4: these two are launched by callers that ask the validator's owner decision; the callers themselves are pinned in tests/test_s4_runtime_toolchain.py
    ("kriya/tools/service_runtime.py", "_prepare_required_artifact"): (
        ("kriya/workflow/runtime_verification.py", "_prepare_finite_command_runtime_artifacts"), "external_execution"),
    ("kriya/tools/service_runtime.py", "run_managed_service_verification"): (
        ("kriya/workflow/runtime_verification.py", "_execute_managed_service_verification"), "build_containment_profile_and_backend"),
    # S5: the MCP launcher is reached only through MCPClient._start_sequence, which applies the owner's isolating-backend fact to the backend it holds
    # (and MCPManager.start_all, which hands it that backend, asks decide_external_execution: pinned below)
    ("kriya/mcp/lifecycle.py", "spawn_mcp_process"): (("kriya/mcp/mcp.py", "MCPClient._start_sequence"), "require_isolating_backend"),
}
# site -> the slice that routes it. Visible on purpose; EMPTY once S5 routed MCP (every workload launch site is now routed or unreferenced).
PENDING: Dict[SITE, str] = {}
UNREFERENCED = {("kriya/tools/dependency_execution.py", name) for name in (
    "maven_acquire_dependencies", "maven_execute_offline", "python_acquire_dependencies", "python_execute_offline")}


def _function(path: str, qualname: str):
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names, current = [], node
            while True:
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(current.name)
                if current not in parents:
                    break
                current = parents[current]
            if ".".join(reversed(names)) == qualname:
                return node
    return None


def test_every_workload_launch_site_is_routed_pending_or_unreferenced_and_nothing_is_double_counted():
    workload = {key for key, site in inv.INVENTORY.items() if site.cls == inv.WORKLOAD}
    accounted = set(ROUTED) | set(PENDING) | UNREFERENCED
    assert workload == accounted, f"unaccounted workload sites: {sorted(workload - accounted)}; stale: {sorted(accounted - workload)}"
    assert not (set(ROUTED) & set(PENDING)) and not ((set(ROUTED) | set(PENDING)) & UNREFERENCED)


@pytest.mark.parametrize("site", sorted(ROUTED))
def test_a_routed_launch_site_is_behind_a_function_that_asks_the_owner(site):
    (path, qualname), callee = ROUTED[site]
    node = _function(path, qualname)
    assert node is not None, f"{path}::{qualname} not found"
    called = {call.func.id if isinstance(call.func, ast.Name) else call.func.attr
              for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, (ast.Name, ast.Attribute))}
    assert callee in called, f"{path}::{qualname} does not call {callee}"


def test_the_validator_asks_the_owner_through_one_method_that_calls_it():
    node = _function("kriya/tools/validate.py", "PolymorphicValidator.external_execution")
    assert node is not None and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "decide_external_execution"
                                    for call in ast.walk(node))


def test_the_one_production_caller_of_jdtls_passes_the_autonomy_configuration():
    """JdtlsClient takes the configuration the decision is made from; a caller that forgets it would get "no mandate" and a host spawn."""
    callers = []
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "JdtlsClient":
                    callers.append((path.relative_to(REPO).as_posix(), len(node.args) + len(node.keywords)))
    assert callers == [("kriya/workflow/lsp_integration.py", 3)], callers
    retry = (REPO / "kriya/workflow/retry_strategy.py").read_text(encoding="utf-8")
    assert "_get_or_start_jdtls_client(state.jdtls_client, ctx.worktree_path, ctx.kernel.config.autonomy)" in retry


def test_the_mcp_manager_gets_its_decision_and_its_backend_from_the_owner_and_resolves_neither_itself():
    start_all = _function("kriya/mcp/mcp.py", "MCPManager.start_all")
    assert start_all is not None
    called = {call.func.id if isinstance(call.func, ast.Name) else call.func.attr
              for call in ast.walk(start_all) if isinstance(call, ast.Call) and isinstance(call.func, (ast.Name, ast.Attribute))}
    assert {"decide_external_execution", "mcp_subject", "backend"} <= called, called
    assert "resolve_containment_backend" not in called
    module = ast.parse((REPO / "kriya/mcp/mcp.py").read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(module) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "resolve_containment_backend" not in imported, "MCP resolves no backend by name: the owner's decision does"
