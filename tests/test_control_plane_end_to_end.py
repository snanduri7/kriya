"""MA5.10: end-to-end control-plane validation. Exercises ControlState,
ContractRegistry, ArtifactRegistry, DecisionLedger, ContextPackage/
ContextOrchestrator, and checkpoint hash/resume-validation TOGETHER
against a real temp git workspace with a real pom.xml - not mocked - in a
simulated 2-milestone sequence: M1 registers and freezes a contract and
produces a real Maven artifact; M2 consumes both via ContextOrchestrator.
Then proves the whole thing round-trips through persistence and that
resume validation genuinely distinguishes "nothing changed" from "the
workspace or a registry drifted since the checkpoint was taken."

This is deliberately NOT a run_generation_workflow() integration test -
MA5's own hard constraint is that the existing execution core stays
untouched; this test proves the NEW control-plane layer is internally
coherent on its own terms, which is what MA5.10 asks for."""

import subprocess
import tempfile

import pytest

from kriya.agents.contracts import MilestoneMode, MilestoneV2, ProvidedCapability
from kriya.control.artifacts import ArtifactRegistry
from kriya.control.contracts import ContractRegistry, ContractState
from kriya.control.decisions import DecisionLedger, load_decision_ledger
from kriya.control.persistence import (
    load_artifact_registry,
    load_contract_registry,
    load_control_state,
    save_artifact_registry,
    save_contract_registry,
    save_control_state,
)
from kriya.control.state import ControlState
from kriya.control.telemetry import (
    record_artifact_derivation,
    record_context_package_summary,
    record_contract_access,
    record_registry_hashes,
    record_resume_validation,
)
from kriya.workflow.checkpoint import (
    ResumeStatus,
    compute_control_plane_hashes,
    compute_registry_hash,
    save_checkpoint,
    load_checkpoint,
    validate_resume_against_reality,
)
from kriya.workflow.context_orchestrator import ContextOrchestrator
from kriya.workflow.context_package import artifact_entry_from_record, contract_entry_from_record
from kriya.workflow.process_profile import STANDARD_PROFILE
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

_POM_V1 = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>protocol-lib</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>
</project>
"""


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, capture_output=True)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        _git(["init"], d)
        _git(["config", "user.email", "t@t.com"], d)
        _git(["config", "user.name", "t"], d)
        yield d


def _route(kind=ChangeKind.MILESTONE, weight=ExecutionWeight.STANDARD):
    return EngineeringRoute(
        kind=kind, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=weight,
    )


def test_full_two_milestone_control_plane_round_trip(workspace):
    ledger = DecisionLedger()

    # --- Milestone M1: produce a real artifact, register+freeze a contract ---
    with open(f"{workspace}/pom.xml", "w") as f:
        f.write(_POM_V1)
    _git(["add", "pom.xml"], workspace)
    _git(["commit", "-m", "M1"], workspace)

    artifacts = ArtifactRegistry()
    for record in artifacts.derive_from_workspace(workspace, milestone_id="M1"):
        artifacts.record(record)
        record_artifact_derivation(ledger, record)
    artifact_record = artifacts.resolve_for_milestone("M1")[0]
    assert artifact_record.coordinates["artifactId"] == "protocol-lib"

    contracts = ContractRegistry()
    contract_id = "M1:ProtocolClient"
    contracts.register(contract_id, "ProtocolClient", "M1", shape={"encode": "bytes->str"}, consumers=("M2",))
    contracts.approve(contract_id)
    contracts.freeze(contract_id)
    frozen = contracts.require_stable(contract_id)
    record_contract_access(ledger, frozen, access="write")

    control_state = ControlState.new(run_id="run-1", milestone_group_id="grp-1").with_updates(
        current_milestone_id="M1", milestone_states={"M1": "done"},
    )

    # --- Persist everything ---
    save_control_state(workspace, control_state)
    save_contract_registry(workspace, contracts)
    save_artifact_registry(workspace, artifacts)

    checkpoint_hashes = compute_control_plane_hashes(
        workspace, control_state=control_state, contract_registry=contracts, artifact_registry=artifacts,
    )
    record_registry_hashes(
        ledger, control_state_hash=checkpoint_hashes["control_state_hash"],
        contract_hash=checkpoint_hashes["contract_hash"], artifact_registry_hash=checkpoint_hashes["artifact_registry_hash"],
    )
    save_checkpoint(workspace, "run-1", {"stage": "milestone_m1_done", **checkpoint_hashes})

    # --- Milestone M2: consumes M1's contract + artifact via ContextOrchestrator ---
    milestone_2 = MilestoneV2(
        id="M2", goal="Build a client using ProtocolClient", mode=MilestoneMode.COMPOSITION,
        depends_on=["M1"], provides=[ProvidedCapability(name="ClientApp")], consumes=["ProtocolClient"],
    )
    orchestrator = ContextOrchestrator()
    import asyncio
    package = asyncio.run(orchestrator.build(
        request="Build a client using ProtocolClient",
        route=_route(), profile=STANDARD_PROFILE, workspace_path=workspace,
        milestone=milestone_2, control_state=control_state,
        contract_entries=[contract_entry_from_record(frozen)],
        artifact_entries=[artifact_entry_from_record(artifact_record)],
    ))
    assert package.spec_slice["consumes"] == ["ProtocolClient"]
    assert package.contract_entries[0]["id"] == contract_id
    assert package.contract_entries[0]["state"] == "frozen"
    assert package.artifact_entries[0]["coordinates"]["artifactId"] == "protocol-lib"
    record_context_package_summary(ledger, package)

    # --- Everything round-trips through persistence ---
    reloaded_state = load_control_state(workspace)
    reloaded_contracts = load_contract_registry(workspace)
    reloaded_artifacts = load_artifact_registry(workspace)
    assert reloaded_state.content_hash() == control_state.content_hash()
    assert reloaded_contracts.get(contract_id).state == ContractState.FROZEN
    assert reloaded_artifacts.resolve_for_milestone("M1")[0].coordinates["artifactId"] == "protocol-lib"

    # --- Resume validation: nothing changed -> OK ---
    checkpoint_data = load_checkpoint(workspace, "run-1")
    result_ok = validate_resume_against_reality(
        checkpoint_data, workspace, control_state=reloaded_state,
        contract_registry=reloaded_contracts, artifact_registry=reloaded_artifacts,
    )
    assert result_ok.status == ResumeStatus.OK
    record_resume_validation(ledger, result_ok)

    # --- Now drift the workspace: a new commit bumps the artifact's version ---
    with open(f"{workspace}/pom.xml", "w") as f:
        f.write(_POM_V1.replace("1.0.0", "2.0.0"))
    _git(["add", "pom.xml"], workspace)
    _git(["commit", "-m", "bump version"], workspace)

    result_drifted = validate_resume_against_reality(
        checkpoint_data, workspace, control_state=reloaded_state,
        contract_registry=reloaded_contracts, artifact_registry=reloaded_artifacts,
    )
    assert result_drifted.status == ResumeStatus.NEEDS_REVIEW
    assert any("base_commit" in m or "tree_hash" in m for m in result_drifted.mismatches)
    record_resume_validation(ledger, result_drifted)

    # ArtifactRegistry.validate() independently confirms the SAME real drift
    # by re-deriving from the now-changed workspace.
    artifact_validation = reloaded_artifacts.validate(workspace, "M1")
    assert artifact_validation[0].drifted is True
    assert artifact_validation[0].current.coordinates["version"] == "2.0.0"

    # --- The decision ledger accumulated a real, ordered audit trail ---
    persisted_types = [d.type for d in ledger.all()]
    assert persisted_types == [
        "artifact_derivation", "contract_access", "registry_hashes",
        "context_package_built", "resume_validation", "resume_validation",
    ]
    for decision in ledger.all():
        ledger.append_to_file(workspace, decision)
    reloaded_ledger = load_decision_ledger(workspace)
    assert [d.type for d in reloaded_ledger.all()] == persisted_types
