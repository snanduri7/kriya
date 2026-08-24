"""MA5.2/5.3: ContractRegistry (kriya/control/contracts.py) - the linear
lifecycle, its invariants (only APPROVED->FROZEN, only FROZEN/IMPLEMENTED
is a stable surface, no silent in-place mutation), and ContractChange's
consumer-invalidation flow."""

import pytest

from kriya.control.contracts import (
    ContractChangeConflictError,
    ContractNotFoundError,
    ContractRegistry,
    ContractState,
    ContractStateError,
    compute_shape_hash,
    contract_records_from_provided_capabilities,
    mark_capabilities_implemented,
)
from kriya.agents.contracts import MilestoneV2, ProvidedCapability


def _registry_with_one_contract(consumers=("M2", "M3")):
    reg = ContractRegistry()
    reg.register("M1:ProtocolClient", "ProtocolClient", "M1", shape={"encode": "bytes->str"}, consumers=consumers)
    return reg


# --- registration ---

def test_register_starts_in_proposed_state():
    reg = _registry_with_one_contract()
    record = reg.get("M1:ProtocolClient")
    assert record.state == ContractState.PROPOSED
    assert record.revision == "v1"


def test_register_computes_a_real_content_hash():
    reg = _registry_with_one_contract()
    record = reg.get("M1:ProtocolClient")
    assert record.content_hash == compute_shape_hash({"encode": "bytes->str"})


def test_register_rejects_a_duplicate_id():
    reg = _registry_with_one_contract()
    with pytest.raises(ValueError):
        reg.register("M1:ProtocolClient", "ProtocolClient", "M1", shape={})


def test_get_unknown_contract_raises():
    reg = ContractRegistry()
    with pytest.raises(ContractNotFoundError):
        reg.get("nonexistent")


def test_try_get_unknown_contract_returns_none():
    reg = ContractRegistry()
    assert reg.try_get("nonexistent") is None


# --- lifecycle invariants ---

def test_full_lifecycle_happy_path():
    reg = _registry_with_one_contract()
    reg.approve("M1:ProtocolClient")
    assert reg.get("M1:ProtocolClient").state == ContractState.APPROVED
    reg.freeze("M1:ProtocolClient")
    assert reg.get("M1:ProtocolClient").state == ContractState.FROZEN
    reg.mark_implemented("M1:ProtocolClient")
    assert reg.get("M1:ProtocolClient").state == ContractState.IMPLEMENTED


def test_cannot_freeze_a_proposed_contract():
    """Only APPROVED contracts may become FROZEN."""
    reg = _registry_with_one_contract()
    with pytest.raises(ContractStateError):
        reg.freeze("M1:ProtocolClient")


def test_cannot_approve_a_frozen_contract_again():
    reg = _registry_with_one_contract()
    reg.approve("M1:ProtocolClient")
    reg.freeze("M1:ProtocolClient")
    with pytest.raises(ContractStateError):
        reg.approve("M1:ProtocolClient")


def test_cannot_mark_implemented_before_frozen():
    reg = _registry_with_one_contract()
    reg.approve("M1:ProtocolClient")
    with pytest.raises(ContractStateError):
        reg.mark_implemented("M1:ProtocolClient")


def test_require_stable_rejects_proposed_and_approved():
    reg = _registry_with_one_contract()
    with pytest.raises(ContractStateError):
        reg.require_stable("M1:ProtocolClient")
    reg.approve("M1:ProtocolClient")
    with pytest.raises(ContractStateError):
        reg.require_stable("M1:ProtocolClient")


def test_require_stable_accepts_frozen_and_implemented():
    reg = _registry_with_one_contract()
    reg.approve("M1:ProtocolClient")
    reg.freeze("M1:ProtocolClient")
    assert reg.require_stable("M1:ProtocolClient").state == ContractState.FROZEN
    reg.mark_implemented("M1:ProtocolClient")
    assert reg.require_stable("M1:ProtocolClient").state == ContractState.IMPLEMENTED


# --- queries ---

def test_list_for_provider_returns_only_that_providers_current_records():
    reg = ContractRegistry()
    reg.register("M1:A", "A", "M1", shape={})
    reg.register("M1:B", "B", "M1", shape={})
    reg.register("M2:C", "C", "M2", shape={})
    provider_m1 = {r.id for r in reg.list_for_provider("M1")}
    assert provider_m1 == {"M1:A", "M1:B"}


def test_consumers_of_returns_the_registered_consumer_list():
    reg = _registry_with_one_contract(consumers=("M2", "M3"))
    assert reg.consumers_of("M1:ProtocolClient") == ("M2", "M3")


# --- ContractChange / invalidation (MA5.3) ---

def test_propose_change_identifies_current_consumers_as_affected():
    reg = _registry_with_one_contract(consumers=("M2", "M3"))
    change = reg.propose_change("M1:ProtocolClient", proposed_shape={"encode": "bytes->bytes"}, reason="API rework")
    assert change.affected_consumers == ("M2", "M3")
    assert change.old_revision == "v1"


def test_propose_change_does_not_mutate_the_registry():
    reg = _registry_with_one_contract()
    reg.propose_change("M1:ProtocolClient", proposed_shape={"new": "shape"}, reason="x")
    assert reg.get("M1:ProtocolClient").revision == "v1"
    assert reg.get("M1:ProtocolClient").shape == {"encode": "bytes->str"}


def test_apply_change_produces_a_new_revision_reset_to_proposed():
    """A changed FROZEN contract is not silently edited in place - the new
    shape must earn FROZEN again."""
    reg = _registry_with_one_contract()
    reg.approve("M1:ProtocolClient")
    reg.freeze("M1:ProtocolClient")
    change = reg.propose_change("M1:ProtocolClient", proposed_shape={"encode": "bytes->bytes"}, reason="rework")
    new_record = reg.apply_change(change)
    assert new_record.revision == "v2"
    assert new_record.state == ContractState.PROPOSED
    assert new_record.shape == {"encode": "bytes->bytes"}


def test_apply_change_preserves_prior_revision_in_history():
    reg = _registry_with_one_contract()
    original_shape = reg.get("M1:ProtocolClient").shape
    change = reg.propose_change("M1:ProtocolClient", proposed_shape={"new": "shape"}, reason="x")
    reg.apply_change(change)
    history = reg.history_for("M1:ProtocolClient")
    assert len(history) == 2
    assert history[0].shape == original_shape
    assert history[0].revision == "v1"
    assert history[1].revision == "v2"


def test_apply_change_carries_consumers_forward_unchanged():
    reg = _registry_with_one_contract(consumers=("M2", "M3"))
    change = reg.propose_change("M1:ProtocolClient", proposed_shape={"x": 1}, reason="x")
    new_record = reg.apply_change(change)
    assert new_record.consumers == ("M2", "M3")


def test_apply_change_rejects_a_stale_proposal():
    """If the contract already moved past old_revision, applying a change
    proposed against an earlier one must fail, never silently stack."""
    reg = _registry_with_one_contract()
    stale_change = reg.propose_change("M1:ProtocolClient", proposed_shape={"a": 1}, reason="first")
    reg.apply_change(reg.propose_change("M1:ProtocolClient", proposed_shape={"b": 2}, reason="second"))
    with pytest.raises(ContractChangeConflictError):
        reg.apply_change(stale_change)


def test_content_hash_changes_with_shape():
    assert compute_shape_hash({"a": 1}) != compute_shape_hash({"a": 2})
    assert compute_shape_hash({"a": 1}) == compute_shape_hash({"a": 1})


def test_content_hash_handles_string_shapes_too():
    """Shape may be a textual contract (e.g. an OpenAPI file's raw text,
    section 14), not just structured JSON."""
    assert compute_shape_hash("openapi: 3.0.0\npaths: {}") == compute_shape_hash("openapi: 3.0.0\npaths: {}")
    assert compute_shape_hash("shape A") != compute_shape_hash("shape B")


# --- persistence round trip (dict shape, not file I/O - see test_control_contract_persistence.py) ---

def test_to_dict_from_dict_round_trips_full_history_and_state():
    reg = _registry_with_one_contract(consumers=("M2",))
    reg.approve("M1:ProtocolClient")
    reg.freeze("M1:ProtocolClient")
    change = reg.propose_change("M1:ProtocolClient", proposed_shape={"v": 2}, reason="x")
    reg.apply_change(change)

    reloaded = ContractRegistry.from_dict(reg.to_dict())
    assert reloaded.get("M1:ProtocolClient").revision == "v2"
    assert reloaded.get("M1:ProtocolClient").state == ContractState.PROPOSED
    assert len(reloaded.history_for("M1:ProtocolClient")) == 2
    assert reloaded.history_for("M1:ProtocolClient")[0].state == ContractState.FROZEN


# --- contract_records_from_provided_capabilities / mark_capabilities_implemented (MA5.2/5.7 bridge) ---
# The "one-way bridge" this module's own docstring promised since MA5.2 but
# was never built - confirmed dead code (zero callers anywhere) until
# 2026-08-24, when it was wired into kriya/workflow/milestones.py::run_milestones().

def _milestone(id="M1", provides=None):
    return MilestoneV2(
        id=id, goal="build the thing",
        provides=[ProvidedCapability(**p) for p in (provides or [])],
    )


def test_bridge_registers_one_proposed_record_per_provided_capability():
    reg = ContractRegistry()
    m = _milestone(provides=[
        {"name": "ProtocolCodec", "description": "encode/decode Protocol objects"},
        {"name": "Logger"},
    ])
    records = contract_records_from_provided_capabilities(reg, m)

    assert len(records) == 2
    codec = reg.get("M1:ProtocolCodec")
    assert codec.state == ContractState.PROPOSED
    assert codec.provider_milestone_id == "M1"
    assert codec.shape == "encode/decode Protocol objects"

    logger_record = reg.get("M1:Logger")
    assert logger_record.shape == "Logger", "falls back to the capability name when no description is given"


def test_bridge_scopes_contract_id_by_milestone_not_just_capability_name():
    """Two different milestones may declare the same capability name without
    colliding - reachability between provider/consumer is
    milestone_validation.py's job, this registry only needs a unique id."""
    reg = ContractRegistry()
    contract_records_from_provided_capabilities(reg, _milestone(id="M1", provides=[{"name": "Cache"}]))
    contract_records_from_provided_capabilities(reg, _milestone(id="M2", provides=[{"name": "Cache"}]))

    assert reg.get("M1:Cache").provider_milestone_id == "M1"
    assert reg.get("M2:Cache").provider_milestone_id == "M2"


def test_bridge_is_idempotent_on_a_resumed_run():
    """A resumed multi-milestone run re-processing a milestone whose
    capabilities were already registered on an earlier attempt must not
    crash (register() raises on a duplicate id)."""
    reg = ContractRegistry()
    m = _milestone(provides=[{"name": "Cache"}])
    first = contract_records_from_provided_capabilities(reg, m)
    second = contract_records_from_provided_capabilities(reg, m)

    assert first[0].id == second[0].id
    assert len(reg.history_for("M1:Cache")) == 1, "must not create a second revision, just return the existing one"


def test_mark_capabilities_implemented_advances_the_full_lifecycle():
    reg = ContractRegistry()
    m = _milestone(provides=[{"name": "Cache"}])
    contract_records_from_provided_capabilities(reg, m)

    mark_capabilities_implemented(reg, m)

    assert reg.get("M1:Cache").state == ContractState.IMPLEMENTED


def test_mark_capabilities_implemented_noops_on_an_unregistered_capability():
    """A capability that was never registered (e.g. the plan changed shape
    mid-run) must not raise - this is bookkeeping, never allowed to break a
    real milestone completion."""
    reg = ContractRegistry()
    m = _milestone(provides=[{"name": "NeverRegistered"}])
    mark_capabilities_implemented(reg, m)  # must not raise
    assert reg.try_get("M1:NeverRegistered") is None


def test_mark_capabilities_implemented_is_idempotent():
    """A resumed run re-processing an already-IMPLEMENTED milestone must not
    crash trying to re-transition it (approve()/freeze() raise on the
    wrong prior state)."""
    reg = ContractRegistry()
    m = _milestone(provides=[{"name": "Cache"}])
    contract_records_from_provided_capabilities(reg, m)
    mark_capabilities_implemented(reg, m)

    mark_capabilities_implemented(reg, m)  # must not raise

    assert reg.get("M1:Cache").state == ContractState.IMPLEMENTED
