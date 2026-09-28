"""PLAT-002 (ARCH-PLATFORM-001): every "outside the workspace" guard (the
SEC-009 trust file, the TOOL-002 MCP approval store, the PRD-014
qualification store) recognizes every spelling of the workspace - case and
Unicode-normalization variants on an insensitive filesystem, symlink
aliases - and fails closed when identity cannot be established.
"""
import os

import _path_identity_probes as probes
import pytest
from _path_identity_probes import NFC_NAME, NFD_NAME

from kriya.config import authority_approval
from kriya.core import model_qualification
from kriya.mcp import invocation_approval
from kriya.platform import filesystem_semantics as fs


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "Workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def case_insensitive(tmp_path):
    return probes.case_insensitive(tmp_path)


@pytest.fixture
def normalization_insensitive(tmp_path):
    return probes.normalization_insensitive(tmp_path)


# --- PLAT-002: "outside the workspace" guards --------------------------------

GUARDS = [
    pytest.param(lambda ws, p: authority_approval.validate_trust_path_outside_workspace(p, ws),
                 authority_approval.TrustPathInsideWorkspaceError, id="authority-trust-file"),
    pytest.param(lambda ws, p: invocation_approval.validate_store_path_outside_workspace(p, ws),
                 invocation_approval.MCPTrustPathInsideWorkspaceError, id="mcp-approval-store"),
    pytest.param(lambda ws, p: model_qualification._refuse_inside_workspace(os.path.dirname(p), ws),
                 model_qualification.QualificationPathInsideWorkspaceError, id="qualification-store"),
]


def _refused(guard, error, workspace, path):
    try:
        guard(str(workspace), str(path))
    except error:
        return True
    return False


def _variant(workspace, spelling):
    return workspace.parent / spelling / "store" / "x.json"


@pytest.mark.parametrize("guard,error", GUARDS)
def test_plat002_exact_and_nested_inside_paths_are_refused(workspace, guard, error):
    assert _refused(guard, error, workspace, workspace / "x.json")
    assert _refused(guard, error, workspace, workspace / "deep" / "new" / "x.json")


@pytest.mark.parametrize("guard,error", GUARDS)
@pytest.mark.parametrize("spelling", ["WORKSPACE", "workspace", "wOrKsPaCe"])
def test_plat002_ascii_case_variant_of_the_workspace(workspace, case_insensitive, guard, error, spelling):
    assert _refused(guard, error, workspace, _variant(workspace, spelling)) is case_insensitive


@pytest.mark.parametrize("guard,error", GUARDS)
@pytest.mark.parametrize("spelling", [NFD_NAME, NFD_NAME.upper()])
def test_plat002_normalization_and_combined_variant(tmp_path, case_insensitive, normalization_insensitive,
                                                   guard, error, spelling):
    workspace = tmp_path / NFC_NAME
    workspace.mkdir()
    needs_case = spelling.casefold() != spelling
    expected = normalization_insensitive and (case_insensitive or not needs_case)
    assert _refused(guard, error, workspace, _variant(workspace, spelling)) is expected


@pytest.mark.parametrize("guard,error", GUARDS)
def test_plat002_symlink_alias_of_the_workspace_is_refused(workspace, guard, error):
    os.symlink(workspace, workspace.parent / "alias")
    assert _refused(guard, error, workspace, workspace.parent / "alias" / "x.json")


@pytest.mark.parametrize("guard,error", GUARDS)
def test_plat002_sibling_prefix_is_outside(workspace, guard, error):
    sibling = workspace.parent / (workspace.name + "-evil")
    sibling.mkdir()
    assert not _refused(guard, error, workspace, sibling / "x.json")
    assert not _refused(guard, error, workspace, workspace.parent / "elsewhere" / "x.json")


@pytest.mark.parametrize("guard,error", GUARDS)
def test_plat002_unknown_identity_fails_closed(workspace, monkeypatch, guard, error):
    outside = workspace.parent / "outside"
    outside.mkdir()
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path) == os.path.realpath(outside):
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(fs.os, "stat", stat)
    assert _refused(guard, error, workspace, outside / "x.json")
