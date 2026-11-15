"""MCP-PROTECTED-AUTHORITY-GRANT-001 (reconciliation of the frozen D2-v3 tree, owner ruling 2026-11-15).

MEASURED on 5527fee through the real mapping owner (no container): an operator-approved ``additional_write_paths`` grant naming
``<workspace>/.kriya/control`` or ``<workspace>/.kriya``, and a read-only grant of the control directory, became bind mounts of
an MCP server's container; nothing related a grant to ``kriya.policy.protected_authority_paths`` (it had no production consumer).

Invariant: no filesystem grant an MCP capability profile turns into a mount may show the contained server Kriya authority state,
writable or read-only. A grant that is a protected path, lies inside one or contains one refuses the whole mapping; so does a
protected set, or a relation, that cannot be established. Nothing is masked. Unrelated grants map exactly as before.

Every test drives ``map_capability_profile_to_containment`` (the one owner that turns a profile into mounts).
"""
import itertools
import os

import pytest

import kriya.mcp.containment_adapter as adapter
import kriya.policy.protected_authority_paths as protected_owner
from kriya.config import environment as env_module
from kriya.mcp.capability import MCPCapabilityProfile, MCPPathScope, MCPResolvedPath, resolve_mcp_capability_profile
from kriya.mcp.containment_adapter import (
    MCP_GRANT_EXPOSES_PROTECTED_AUTHORITY,
    MCP_PROTECTED_AUTHORITY_UNRESOLVABLE,
    MCP_PROTECTED_GRANT_REASON_CODES,
    MCPProtectedAuthorityGrantError,
    map_capability_profile_to_containment,
)
from kriya.platform.filesystem_semantics import PathRelation
from kriya.tools.containment import ContainmentSetupError, MountSpec

READ, WRITE = "additional_read_paths", "additional_write_paths"
HOME_VARIABLES = (env_module.AUTHORITY_HOME, env_module.MCP_APPROVAL_HOME, env_module.STATIC_ANALYSIS_HOME,
                  env_module.QUALIFICATION_HOME, env_module.CERTIFICATION_HOME, env_module.ADJUDICATION_HOME)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A workspace, every authority home and a trust file, all outside one another, plus two unrelated directories."""
    class World:
        workspace = tmp_path / "ws"
        homes = {variable: tmp_path / "homes" / variable.lower() for variable in HOME_VARIABLES}
        trust_file = tmp_path / "trust" / "approvals.json"
        safe_a, safe_b = tmp_path / "data" / "a", tmp_path / "data" / "b"

    for directory in (World.workspace / ".kriya" / "control" / "runs", World.workspace / ".kriya" / "checkpoints",
                      World.workspace / "src", World.trust_file.parent, World.safe_a, World.safe_b,
                      *(home / "records" for home in World.homes.values())):
        directory.mkdir(parents=True)
    World.trust_file.write_text("{}")
    for variable, home in World.homes.items():
        monkeypatch.setenv(variable, str(home))
    monkeypatch.setenv(env_module.TRUST_FILE, str(World.trust_file))
    return World


def _map(world, config):
    profile = resolve_mcp_capability_profile(config, str(world.workspace))
    return map_capability_profile_to_containment(profile, workspace_root=str(world.workspace), resolved_env={}, profile_digest="d")


def _refused(world, config, code=MCP_GRANT_EXPOSES_PROTECTED_AUTHORITY):
    with pytest.raises(MCPProtectedAuthorityGrantError) as caught:
        _map(world, config)
    assert caught.value.reason_code == code
    assert isinstance(caught.value, ContainmentSetupError)   # the existing typed containment-setup hierarchy
    return caught.value


def _protected_relations(world):
    """(name, path) of every relation the invariant refuses: exact, inside and containing, for each kind of protected path."""
    ws, authority = world.workspace, world.homes[env_module.AUTHORITY_HOME]
    return {
        "workspace_control_exact": ws / ".kriya" / "control",
        "workspace_control_inside": ws / ".kriya" / "control" / "runs",
        "workspace_checkpoints_exact": ws / ".kriya" / "checkpoints",
        "workspace_anchor_contains": ws / ".kriya",
        "workspace_root_contains": ws,
        "authority_home_exact": authority,
        "authority_home_inside": authority / "records",
        "authority_home_parent_contains": authority.parent,
        "mcp_approval_home_exact": world.homes[env_module.MCP_APPROVAL_HOME],
        "qualification_home_inside": world.homes[env_module.QUALIFICATION_HOME] / "records",
        "trust_file_exact": world.trust_file,
        "trust_file_parent_contains": world.trust_file.parent,
    }


RELATIONS = ("workspace_control_exact", "workspace_control_inside", "workspace_checkpoints_exact", "workspace_anchor_contains",
             "workspace_root_contains", "authority_home_exact", "authority_home_inside", "authority_home_parent_contains",
             "mcp_approval_home_exact", "qualification_home_inside", "trust_file_exact", "trust_file_parent_contains")


@pytest.mark.parametrize("field", [WRITE, READ])
@pytest.mark.parametrize("relation", RELATIONS)
def test_a_grant_that_is_inside_or_contains_protected_authority_state_is_refused_writable_and_read_only(world, relation, field):
    """ORIGINAL SYMPTOM (the first, second and fourth probe cases mapped to a bind mount before the fix)."""
    grant = _protected_relations(world)[relation]

    error = _refused(world, {field: [str(grant)]})

    assert f"capabilities.{field}[0]" in str(error)
    assert str(grant) not in str(error) and str(world.workspace) not in str(error)   # the label names it, never a path


def test_the_relation_table_covers_every_listed_relation(world):
    assert set(_protected_relations(world)) == set(RELATIONS)


def test_unrelated_grants_map_exactly_as_before(world):
    mapped = _map(world, {READ: [str(world.safe_a)], WRITE: [str(world.safe_b)], "workspace_read": True})

    assert mapped.additional_mounts == (
        MountSpec(host_path=os.path.realpath(world.safe_a), container_path="/kriya/mcp/extra/ro/0", writable=False),
        MountSpec(host_path=os.path.realpath(world.safe_b), container_path="/kriya/mcp/extra/rw/0", writable=True),
    )
    assert (mapped.mount_workspace, mapped.workspace_write) == (True, False)
    assert (mapped.run_as_uid, mapped.run_as_gid) == (os.getuid(), os.getgid())


def test_a_grant_inside_the_workspace_but_outside_authority_state_still_maps(world):
    mapped = _map(world, {WRITE: [str(world.workspace / "src")]})
    assert [mount.host_path for mount in mapped.additional_mounts] == [os.path.realpath(world.workspace / "src")]


@pytest.mark.parametrize("dangerous_field", [WRITE, READ])
def test_one_dangerous_grant_among_safe_ones_refuses_the_whole_mapping_in_every_order(world, dangerous_field):
    dangerous = str(world.homes[env_module.CERTIFICATION_HOME])
    paths = [str(world.safe_a), str(world.safe_b), dangerous]
    for order in itertools.permutations(paths):
        for split in range(len(order) + 1):   # every way of dealing the same paths to the two fields, the dangerous one in its own
            reads = [path for path in order[:split] if path != dangerous]
            writes = [path for path in order[split:] if path != dangerous]
            config = {READ: reads, WRITE: writes}
            config[dangerous_field] = [path for path in order if path in config[dangerous_field] or path == dangerous]
            _refused(world, config)   # raised: no ContainmentProfile, so no partial mount set, exists


def test_a_protected_set_that_cannot_be_resolved_refuses_every_filesystem_grant(world, monkeypatch):
    """The SEC-009 authority home inside the workspace: the owner raises, the adapter refuses instead of mounting unprotected."""
    monkeypatch.setenv(env_module.AUTHORITY_HOME, str(world.workspace / "authority"))

    for config in ({WRITE: [str(world.safe_a)]}, {READ: [str(world.safe_a)]}, {"workspace_read": True}, {"workspace_write": True}):
        _refused(world, config, MCP_PROTECTED_AUTHORITY_UNRESOLVABLE)


def test_a_relation_that_cannot_be_established_refuses(world, monkeypatch):
    monkeypatch.setattr(adapter, "path_relation", lambda root, target: PathRelation.UNKNOWN)
    _refused(world, {READ: [str(world.safe_a)]}, MCP_PROTECTED_AUTHORITY_UNRESOLVABLE)


def test_a_profile_with_no_filesystem_grant_needs_no_protected_set(world, monkeypatch):
    """Nothing is mounted, so nothing can be exposed: an unresolvable set does not stop a server that was granted no files."""
    monkeypatch.setenv(env_module.AUTHORITY_HOME, str(world.workspace / "authority"))
    mapped = _map(world, {})
    assert mapped.additional_mounts == () and mapped.mount_workspace is False


def _profile_with(field, path) -> MCPCapabilityProfile:
    """A profile whose grant keeps the GIVEN spelling (resolve_mcp_capability_profile would realpath it away)."""
    resolved = (MCPResolvedPath(scope=MCPPathScope.HOST_PATH, real_path=str(path), writable=field == WRITE),)
    return MCPCapabilityProfile(workspace_read=False, workspace_write=False, temp_read_write=False, dependency_cache_read=False,
                                **{field: resolved})


@pytest.mark.parametrize("field", [WRITE, READ])
def test_a_symlink_spelling_of_a_protected_path_cannot_bypass_the_refusal(world, field):
    alias = world.safe_a / "innocent"
    alias.symlink_to(world.homes[env_module.AUTHORITY_HOME], target_is_directory=True)
    parent_alias = world.safe_b / "also-innocent"
    parent_alias.symlink_to(world.workspace / ".kriya", target_is_directory=True)

    for spelling in (alias, alias / "records", parent_alias, parent_alias / "control"):
        with pytest.raises(MCPProtectedAuthorityGrantError) as caught:
            map_capability_profile_to_containment(
                _profile_with(field, spelling), workspace_root=str(world.workspace), resolved_env={}, profile_digest="d")
        assert caught.value.reason_code == MCP_GRANT_EXPOSES_PROTECTED_AUTHORITY


def test_a_case_variant_spelling_follows_the_filesystems_own_identity(world):
    """On a case-insensitive volume ``.KRIYA/CONTROL`` IS the control directory; on a case-sensitive one it is another path."""
    variant = world.workspace / ".KRIYA" / "CONTROL"
    if not variant.exists():
        pytest.skip("case-sensitive filesystem: the variant spelling names a different (absent) path, so there is nothing to alias")
    with pytest.raises(MCPProtectedAuthorityGrantError):
        map_capability_profile_to_containment(
            _profile_with(WRITE, variant), workspace_root=str(world.workspace), resolved_env={}, profile_digest="d")


@pytest.mark.parametrize("workspace_grant", ["workspace_read", "workspace_write"])
def test_a_workspace_grant_is_refused_when_the_workspace_holds_authority_state_the_backend_does_not_mask(world, monkeypatch,
                                                                                                    workspace_grant):
    """The backend hides .kriya/control and .kriya/checkpoints inside a workspace mount (D2 rebuild S1); a home or a trust file
    inside the workspace has no such mask, so the workspace grant itself is the exposure."""
    monkeypatch.setenv(env_module.QUALIFICATION_HOME, str(world.workspace / "quals"))
    _refused(world, {workspace_grant: True})


@pytest.mark.parametrize("workspace_grant", ["workspace_read", "workspace_write"])
def test_an_ordinary_workspace_grant_is_unchanged(world, workspace_grant):
    mapped = _map(world, {workspace_grant: True})
    assert mapped.mount_workspace is True and mapped.workspace_write is (workspace_grant == "workspace_write")
    assert mapped.additional_mounts == ()


def test_a_path_added_to_the_canonical_protected_set_is_protected_with_no_mcp_change(world, monkeypatch):
    """The adapter keeps no list of its own: extending the owner's set is enough."""
    vault = world.safe_a
    assert _map(world, {READ: [str(vault)]}).additional_mounts   # an ordinary directory today

    added = protected_owner.AuthorityHome("new_vault_home", "KRIYA_TEST_NEW_VAULT_HOME", lambda environment: str(vault),
                                          protected_owner.DECISION_AUTHORITY, "test: a home added to the canonical set")
    monkeypatch.setattr(protected_owner, "AUTHORITY_HOMES", (*protected_owner.AUTHORITY_HOMES, added))

    error = _refused(world, {READ: [str(vault)]})
    assert "new_vault_home" in str(error)


def test_the_reason_codes_are_a_closed_table():
    assert MCP_PROTECTED_GRANT_REASON_CODES == {MCP_GRANT_EXPOSES_PROTECTED_AUTHORITY, MCP_PROTECTED_AUTHORITY_UNRESOLVABLE}
