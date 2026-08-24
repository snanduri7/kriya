"""MA5.2: ContractRegistry file persistence (kriya/control/persistence.py's
save_contract_registry/load_contract_registry)."""

import os
import tempfile

import pytest

from kriya.control.contracts import ContractRegistry, ContractState
from kriya.control.persistence import (
    contract_registry_path,
    load_contract_registry,
    save_contract_registry,
)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_load_returns_empty_registry_when_never_saved(workspace):
    registry = load_contract_registry(workspace)
    assert registry.all_records() == ()


def test_save_and_load_round_trips(workspace):
    reg = ContractRegistry()
    reg.register("M1:X", "X", "M1", shape={"a": 1}, consumers=("M2",))
    reg.approve("M1:X")
    save_contract_registry(workspace, reg)

    loaded = load_contract_registry(workspace)
    record = loaded.get("M1:X")
    assert record.state == ContractState.APPROVED
    assert record.consumers == ("M2",)


def test_load_fails_closed_on_corrupt_file(workspace):
    path = contract_registry_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("not json")
    registry = load_contract_registry(workspace)
    assert registry.all_records() == ()
