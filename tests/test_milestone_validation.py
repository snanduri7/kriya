from kriya.agents.contracts import AcceptanceCriterion, MilestoneMode, MilestoneV2, ProvidedCapability
from kriya.workflow.milestone_validation import (
    DUPLICATE_MILESTONE_ID,
    EMPTY_ACCEPTANCE,
    EXTENSION_DEPENDENCY_NORMALIZED,
    INVALID_EXTENSION,
    MILESTONE_DAG_CYCLE,
    SELF_DEPENDENCY,
    UNKNOWN_DEPENDENCY,
    UNKNOWN_PROVIDER,
    MilestonePlanValidator,
)


def mk(id, goal="g", depends_on=None, mode=None, extends=None, provides=None, consumes=None, acceptance=True):
    return MilestoneV2(
        id=id,
        goal=goal,
        depends_on=depends_on or [],
        mode=mode,
        extends=extends,
        provides=[ProvidedCapability(name=p) for p in (provides or [])],
        consumes=consumes or [],
        acceptance=[AcceptanceCriterion(id=f"{id}-A1", description="ok")] if acceptance else [],
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
