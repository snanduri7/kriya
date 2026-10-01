from kriya.agents.contracts import AcceptanceCriterion, MilestoneMode, MilestoneV2, ProvidedCapability
from kriya.workflow.milestone_validation import (
    AMBIGUOUS_PROVIDER,
    DUPLICATE_ACCEPTANCE_ID,
    DUPLICATE_MILESTONE_ID,
    EMPTY_ACCEPTANCE,
    EXTENSION_DEPENDENCY_NORMALIZED,
    INVALID_EXTENSION,
    MILESTONE_DAG_CYCLE,
    SELF_DEPENDENCY,
    UNJUSTIFIED_ENTRYPOINT,
    UNKNOWN_DEPENDENCY,
    UNKNOWN_PROVIDER,
    MilestonePlanValidator,
)
from kriya.workflow.repository_topology import RepositoryTopology


def mk(
    id, goal="g", depends_on=None, mode=None, extends=None, entrypoint=None,
    provides=None, consumes=None, acceptance=True,
):
    return MilestoneV2(
        id=id,
        goal=goal,
        depends_on=depends_on or [],
        mode=mode,
        extends=extends,
        entrypoint=entrypoint,
        provides=[ProvidedCapability(name=p) for p in (provides or [])],
        consumes=consumes or [],
        acceptance=[AcceptanceCriterion(id=f"{id}-A1", description="ok")] if acceptance else [],
    )


SINGLE_MODULE_TOPOLOGY = RepositoryTopology(
    build_system="maven", build_roots=(".",), modules=(),
    entrypoints=("com.example.Application",), is_multi_module=False,
)
MULTI_MODULE_TOPOLOGY = RepositoryTopology(
    build_system="maven", build_roots=(".", "client", "server"), modules=("client", "server"),
    entrypoints=("com.example.client.Client", "com.example.server.Server"), is_multi_module=True,
)


def test_linear_chain_is_valid():
    r = MilestonePlanValidator().validate([mk("M1"), mk("M2", depends_on=["M1"]), mk("M3", depends_on=["M2"])])
    assert r.valid
    assert not r.errors


def test_diamond_dag_is_valid():
    r = MilestonePlanValidator().validate([
        mk("M1"), mk("M2", depends_on=["M1"]), mk("M3", depends_on=["M1"]), mk("M4", depends_on=["M2", "M3"]),
    ])
    assert r.valid


def test_branching_dag_is_valid():
    r = MilestonePlanValidator().validate([mk("M1"), mk("M2", depends_on=["M1"]), mk("M3", depends_on=["M1"])])
    assert r.valid


def test_duplicate_ids_rejected():
    r = MilestonePlanValidator().validate([mk("M1"), mk("M1")])
    assert not r.valid
    assert any(e.code == DUPLICATE_MILESTONE_ID for e in r.errors)


def test_missing_dependency_rejected():
    r = MilestonePlanValidator().validate([mk("M1"), mk("M2", depends_on=["M99"])])
    assert not r.valid
    assert any(e.code == UNKNOWN_DEPENDENCY and e.milestone_id == "M2" for e in r.errors)


def test_ambiguous_reachable_capability_provider_rejected():
    r = MilestonePlanValidator().validate([
        mk("M1", provides=["Codec"]),
        mk("M2", provides=["Codec"]),
        mk("M3", depends_on=["M1", "M2"], consumes=["Codec"]),
    ])
    assert not r.valid
    assert any(e.code == AMBIGUOUS_PROVIDER and e.milestone_id == "M3" for e in r.errors)


def test_self_dependency_rejected():
    r = MilestonePlanValidator().validate([mk("M1", depends_on=["M1"])])
    assert not r.valid
    assert any(e.code == SELF_DEPENDENCY for e in r.errors)


def test_two_node_cycle_rejected():
    r = MilestonePlanValidator().validate([mk("M1", depends_on=["M2"]), mk("M2", depends_on=["M1"])])
    assert not r.valid
    assert any(e.code == MILESTONE_DAG_CYCLE for e in r.errors)


def test_three_node_cycle_rejected():
    r = MilestonePlanValidator().validate([
        mk("M1", depends_on=["M3"]), mk("M2", depends_on=["M1"]), mk("M3", depends_on=["M2"]),
    ])
    assert not r.valid
    assert any(e.code == MILESTONE_DAG_CYCLE for e in r.errors)


def test_extension_target_missing_rejected():
    r = MilestonePlanValidator().validate([mk("M1", mode=MilestoneMode.EXTENSION, extends="M99")])
    assert not r.valid
    assert any(e.code == INVALID_EXTENSION for e in r.errors)


def test_extension_mode_without_extends_rejected():
    r = MilestonePlanValidator().validate([mk("M1", mode=MilestoneMode.EXTENSION)])
    assert not r.valid
    assert any(e.code == INVALID_EXTENSION for e in r.errors)


def test_extension_dependency_auto_normalized_with_warning():
    r = MilestonePlanValidator().validate([mk("M1"), mk("M2", mode=MilestoneMode.EXTENSION, extends="M1")])
    assert r.valid
    assert any(w.code == EXTENSION_DEPENDENCY_NORMALIZED for w in r.warnings)
    m2 = next(m for m in r.milestones if m.id == "M2")
    assert m2.depends_on == ["M1"]


def test_extension_dependency_already_consistent_no_warning():
    r = MilestonePlanValidator().validate([
        mk("M1"), mk("M2", depends_on=["M1"], mode=MilestoneMode.EXTENSION, extends="M1"),
    ])
    assert r.valid
    assert not r.warnings


def test_capability_direct_provider_valid():
    r = MilestonePlanValidator().validate([
        mk("M1", provides=["ProtocolClient"]), mk("M2", depends_on=["M1"], consumes=["ProtocolClient"]),
    ])
    assert r.valid


def test_capability_transitive_provider_valid():
    r = MilestonePlanValidator().validate([
        mk("M1", provides=["A"]), mk("M2", depends_on=["M1"]), mk("M3", depends_on=["M2"], consumes=["A"]),
    ])
    assert r.valid


def test_capability_unreachable_provider_rejected():
    r = MilestonePlanValidator().validate([
        mk("M1", provides=["A"]), mk("M2"), mk("M3", depends_on=["M2"], consumes=["A"]),
    ])
    assert not r.valid
    assert any(e.code == UNKNOWN_PROVIDER and e.milestone_id == "M3" for e in r.errors)


def test_empty_acceptance_rejected():
    r = MilestonePlanValidator().validate([mk("M1", acceptance=False)])
    assert not r.valid
    assert any(e.code == EMPTY_ACCEPTANCE for e in r.errors)


def test_extends_self_caught_as_self_dependency():
    r = MilestonePlanValidator().validate([mk("M1", mode=MilestoneMode.EXTENSION, extends="M1")])
    assert not r.valid
    assert any(e.code == SELF_DEPENDENCY for e in r.errors)


def test_result_is_json_serializable():
    import json
    json.dumps(MilestonePlanValidator().validate([mk("M1")]).to_dict())


def test_validator_is_stateless_across_calls():
    validator = MilestonePlanValidator()
    r1 = validator.validate([mk("M1")])
    r2 = validator.validate([mk("M1"), mk("M2", depends_on=["M1"])])
    assert r1.valid and r2.valid


def test_no_topology_supplied_skips_physical_boundary_check():
    plan = [
        mk("M1", entrypoint="A.java"),
        mk("M2", depends_on=["M1"], entrypoint="B.java"),
    ]
    assert MilestonePlanValidator().validate(plan).valid


def test_historical_incident_competing_entrypoints_rejected():
    """The real 2026-08-15 failure shape this rule exists to catch: a
    3-part goal against a single-module repo, planner invents a distinct
    entrypoint per milestone instead of extending one."""
    plan = [
        mk("M1", entrypoint="Protocol.java"),
        mk("M2", depends_on=["M1"], entrypoint="Cache.java"),
        mk("M3", depends_on=["M2"], entrypoint="Api.java"),
    ]
    r = MilestonePlanValidator().validate(
        plan, repository_topology=SINGLE_MODULE_TOPOLOGY,
        goal_text="Build one Maven app that reads protocol messages, caches them, exposes the result.",
    )
    assert not r.valid
    unjustified = [e for e in r.errors if e.code == UNJUSTIFIED_ENTRYPOINT]
    assert len(unjustified) == 2
    assert {e.milestone_id for e in unjustified} == {"M2", "M3"}


def test_single_extended_entrypoint_is_valid():
    plan = [
        mk("M1", entrypoint="Application.java"),
        mk("M2", depends_on=["M1"], mode=MilestoneMode.EXTENSION, extends="M1", entrypoint="Application.java"),
        mk("M3", depends_on=["M2"], mode=MilestoneMode.EXTENSION, extends="M2", entrypoint="Application.java"),
    ]
    r = MilestonePlanValidator().validate(
        plan, repository_topology=SINGLE_MODULE_TOPOLOGY,
        goal_text="Build one Maven app that reads protocol messages, caches them, exposes the result.",
    )
    assert r.valid


def test_legitimate_existing_multi_module_repo_allows_composition():
    plan = [
        mk("M1", provides=["ProtocolClient"], entrypoint="client/Client.java"),
        mk("M2", depends_on=["M1"], mode=MilestoneMode.COMPOSITION, consumes=["ProtocolClient"], entrypoint="server/Server.java"),
    ]
    r = MilestonePlanValidator().validate(
        plan, repository_topology=MULTI_MODULE_TOPOLOGY,
        goal_text="Add protocol capability to client and consume it from server.",
    )
    assert r.valid


def test_explicit_goal_justifies_new_build_boundary():
    plan = [
        mk("M1", entrypoint="Library.java"),
        mk("M2", depends_on=["M1"], entrypoint="Cli.java"),
    ]
    r = MilestonePlanValidator().validate(
        plan, repository_topology=SINGLE_MODULE_TOPOLOGY,
        goal_text="Create a reusable Java library and a separate CLI executable that consumes the library.",
    )
    assert r.valid


def test_duplicate_acceptance_id_across_milestones_rejected():
    m1 = MilestoneV2(id="M1", goal="g", acceptance=[AcceptanceCriterion(id="A1", description="ok")])
    m2 = MilestoneV2(id="M2", goal="g", depends_on=["M1"], acceptance=[AcceptanceCriterion(id="A1", description="ok")])
    r = MilestonePlanValidator().validate([m1, m2])
    assert not r.valid
    assert any(e.code == DUPLICATE_ACCEPTANCE_ID for e in r.errors)


def test_duplicate_acceptance_id_within_one_milestone_rejected():
    m1 = MilestoneV2(id="M1", goal="g", acceptance=[
        AcceptanceCriterion(id="A1", description="first"),
        AcceptanceCriterion(id="A1", description="second"),
    ])
    r = MilestonePlanValidator().validate([m1])
    assert not r.valid
    assert any(e.code == DUPLICATE_ACCEPTANCE_ID for e in r.errors)


def test_extension_entrypoint_need_not_exist_on_disk():
    """Section 34's own distinction: an extension milestone's entrypoint is
    frequently one the PREDECESSOR milestone is about to create, not
    something already on disk - the validator must never require the literal
    file to exist. This module never touches the filesystem for entrypoint
    checks at all (only compares string values across milestones), so a
    plausible-but-nonexistent path is accepted exactly like a real one."""
    plan = [
        mk("M1", entrypoint="src/main/java/com/example/NotYetWritten.java"),
        mk("M2", depends_on=["M1"], mode=MilestoneMode.EXTENSION, extends="M1",
           entrypoint="src/main/java/com/example/NotYetWritten.java"),
    ]
    r = MilestonePlanValidator().validate(plan, repository_topology=SINGLE_MODULE_TOPOLOGY, goal_text="goal")
    assert r.valid, r.to_dict()
