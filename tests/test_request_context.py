"""CONTEXT (full modularization, slice B1): the typed request/principal/workspace/run context and the workspace
identity owned by ``kriya.context`` (moved from ``kriya.control``; the persisted ``workspace_id`` is unchanged).

The pinned identities below were MEASURED with ``kriya.control.workspace_identity`` at 9a58060, before the move,
on paths that do not exist (so the value does not depend on this machine's filesystem): the moved function must
reproduce them, and an independent recomputation (sha256 of ``canonical_spelling``) must agree on a real folder.
"""
import ast
import dataclasses
import hashlib
import importlib.util
import os

import pytest

from kriya.context import PrincipalContext, RequestContext, RunScope, ScopeRef, SecurityDomain, WorkspaceContext
from kriya.context import workspace_identity as wi
from kriya.control.persistence import ownership_metadata
from kriya.platform.filesystem_semantics import canonical_spelling
from tests.architecture import access_sites as sites
from tests.architecture import module_map as mm

# path -> (workspace_identity, legacy_workspace_identity, canonical_workspace), MEASURED at 9a58060 (pre-move module)
PINNED = {
    "/nonexistent-kriya-b1/Work Space": ("2562bd47399211635112d09b3d7b1e44ab071e2d4bc4faba868dc1eeb7186353",
                                         "2562bd47399211635112d09b3d7b1e44ab071e2d4bc4faba868dc1eeb7186353",
                                         "/nonexistent-kriya-b1/Work Space"),
    "/nonexistent-kriya-b1/ws/../ws2": ("e755717a6ed0010f140979cec8d9e264310b1f609e3f3a366e9f986745775244",
                                        "e755717a6ed0010f140979cec8d9e264310b1f609e3f3a366e9f986745775244",
                                        "/nonexistent-kriya-b1/ws2"),
}
PINNED_OWNERSHIP = {"workspace_id": "c4e37f7b08bc5af8ab27b51aeb7f1831a96f91a0996fc88f85b7afe829d6567c", "version": "2"}


@pytest.mark.parametrize("path", sorted(PINNED))
def test_the_moved_identity_reproduces_the_values_measured_before_the_move(path):
    identity, legacy, canonical = PINNED[path]
    assert wi.workspace_identity(path) == identity
    assert wi.legacy_workspace_identity(path) == legacy
    assert wi.canonical_workspace(path) == canonical
    assert RequestContext.local(path).workspace.workspace_id == identity
    assert wi.ownership_metadata("/nonexistent-kriya-b1/ws") == PINNED_OWNERSHIP


def test_request_context_local_resolves_the_same_workspace_id_the_control_plane_records(tmp_path):
    folder = tmp_path / "Project Folder"
    folder.mkdir()
    ctx = RequestContext.local(str(folder), request_id="r-1")
    expected = hashlib.sha256(canonical_spelling(str(folder)).encode("utf-8")).hexdigest()  # independent recomputation
    assert ctx.workspace.workspace_id == expected == wi.workspace_identity(str(folder))
    assert ownership_metadata(str(folder))["workspace_id"] == expected  # what persistence stamps into every document
    assert ctx.workspace.root == canonical_spelling(str(folder))
    assert ctx.request_id == "r-1" and ctx.run is None and ctx.deadline is None and ctx.cancellation is None
    assert ctx.workspace.state_dir is None and ctx.workspace.config_dir is None  # resolved by CONFIG (D3), never guessed here
    linked = tmp_path / "link"
    linked.symlink_to(folder, target_is_directory=True)
    assert RequestContext.local(str(linked)).workspace.workspace_id == expected  # a symlink alias is the same workspace


def test_the_local_principal_and_scopes():
    principal = PrincipalContext.local()
    assert principal == PrincipalContext(id="local", display="local operator", security_domain=SecurityDomain.LOCAL)
    assert SecurityDomain.LOCAL.value == "local" and list(SecurityDomain) == [SecurityDomain.LOCAL]
    ctx = RequestContext.local("/nonexistent-kriya-b1/ws", state_dir="/s", config_dir="/c")
    project = ScopeRef("project", ctx.workspace.workspace_id)
    assert ctx.principal == principal
    assert ctx.config_scope == ctx.skills_scope == ctx.tools_scope == (ScopeRef("system", "kriya"), ScopeRef("user", "local"), project)
    assert ctx.evidence_scope == ctx.state_scope == ScopeRef("user", "local")
    assert (ctx.workspace.state_dir, ctx.workspace.config_dir) == ("/s", "/c")
    assert RequestContext.local(None).request_id != RequestContext.local(None).request_id  # one id per request
    with pytest.raises(ValueError):
        ScopeRef("galaxy", "x")
    with pytest.raises(ValueError):
        ScopeRef("user", "")


def test_no_workspace_never_falls_back_to_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = RequestContext.local(None)
    assert ctx.workspace is None
    assert ctx.config_scope == (ScopeRef("system", "kriya"), ScopeRef("user", "local"))


def test_every_context_value_is_frozen():
    ctx = RequestContext.local("/nonexistent-kriya-b1/ws")
    run = RunScope(run_id="run-1", launch_id=None, workspace=ctx.workspace, principal=ctx.principal,
                   config_fingerprint="f", started_iso="1970-01-01T00:00:00Z")
    for value, field in ((ctx, "request_id"), (ctx.principal, "id"), (ctx.workspace, "root"), (run, "run_id"),
                         (ctx.state_scope, "id"), (WorkspaceContext.for_root("/x"), "state_dir")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field, "changed")


def test_the_old_module_is_gone():
    assert importlib.util.find_spec("kriya.control.workspace_identity") is None


# D3 moved the process-global working-directory lock out of KUP into CONTEXT (`WORKING_DIRECTORY_LOCK`) because the workspace-scoped
# configuration load still enters the folder (a relative KRIYA_TRUST_FILE resolves against the cwd). That file is the ONE declared cwd
# exception; O1 removes it together with the chdir (SLICE-PLAN O1; Q2 probes the lock). Every other context file keeps the full B1 invariant.
CWD_EXCEPTION = "kriya/context/working_directory.py"


def test_context_reads_no_environment_home_or_working_directory_and_imports_only_foundation_and_platform():
    """The A2 cwd table lists CONTEXT as an owner of cwd access; B1 keeps the package free of it anyway (the workspace is an explicit input)
    and of every environment/home read - except the single declared D3 working-directory lock file, which reads no environment or home either."""
    trees = {path: tree for path, tree in mm.trees().items() if path.startswith("kriya/context/")}
    assert sorted(trees) == ["kriya/context/__init__.py", "kriya/context/model.py", CWD_EXCEPTION, "kriya/context/workspace_identity.py"]
    for finder in (sites.environ_sites, sites.home_sites, sites.sqlite_sites, sites.http_sites, sites.subprocess_sites):
        assert {path: lines for path, tree in trees.items() for lines in [finder(tree)] if lines} == {}, finder.__name__
    strict = {path: tree for path, tree in trees.items() if path != CWD_EXCEPTION}
    assert {path: lines for path, tree in strict.items() for lines in [sites.cwd_sites(tree)] if lines} == {}, "cwd_sites"
    table = mm.owner_table()
    targets = {mm.logical(edge.target, table) for edge in mm.scan() if edge.source.startswith("kriya.context")}
    assert targets <= {"FOUNDATION", "PLATFORM", "CONTEXT"}, targets

    def chdirs(tree):
        return [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "chdir"]

    for path, tree in strict.items():  # no os.chdir either
        assert chdirs(tree) == [], path
    assert chdirs(trees[CWD_EXCEPTION]), "the declared exception must be real: delete it (and this allowance) when O1 removes the chdir"
    assert os.path.basename(wi.__file__) == "workspace_identity.py" and "context" in wi.__file__
