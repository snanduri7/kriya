"""MA5.5: DecisionLedger (kriya/control/decisions.py) - append-only JSONL
persistence, tolerant load (a bad line doesn't lose everything before it),
and the field-value truncation guard."""

import json
import os
import tempfile

import pytest

from kriya.control.decisions import DecisionLedger, load_decision_ledger
from kriya.control.persistence import decision_ledger_path
from kriya.policy.errors import PolicyDeniedError


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_record_returns_a_decision_with_flat_fields():
    ledger = DecisionLedger()
    decision = ledger.record("triage", kind="task", risk="high")
    assert decision.type == "triage"
    assert decision.fields == {"kind": "task", "risk": "high"}
    as_dict = decision.to_dict()
    assert as_dict["type"] == "triage"
    assert as_dict["kind"] == "task"
    assert as_dict["risk"] == "high"


def test_all_and_filter_by_type():
    ledger = DecisionLedger()
    ledger.record("triage", kind="task")
    ledger.record("policy", decision="deny")
    ledger.record("triage", kind="refactor")
    assert len(ledger.all()) == 3
    assert len(ledger.filter_by_type("triage")) == 2
    assert len(ledger.filter_by_type("policy")) == 1


def test_long_field_value_is_truncated():
    ledger = DecisionLedger()
    long_value = "x" * 10000
    decision = ledger.record("policy", explanation=long_value)
    assert len(decision.fields["explanation"]) < 10000
    assert decision.fields["explanation"].endswith("...[truncated]")


def test_short_field_value_is_not_touched():
    ledger = DecisionLedger()
    decision = ledger.record("policy", reason_code="X")
    assert decision.fields["reason_code"] == "X"


def test_load_returns_empty_ledger_when_never_saved(workspace):
    assert load_decision_ledger(workspace).all() == ()


def test_record_and_persist_round_trips(workspace):
    ledger = DecisionLedger()
    ledger.record_and_persist(workspace, "triage", kind="task", risk="high")
    ledger.record_and_persist(workspace, "policy", decision="deny", reason_code="X")

    reloaded = load_decision_ledger(workspace)
    types = [d.type for d in reloaded.all()]
    assert types == ["triage", "policy"]
    assert reloaded.all()[0].fields["kind"] == "task"


def test_persisted_file_is_real_jsonl_one_object_per_line(workspace):
    ledger = DecisionLedger()
    ledger.record_and_persist(workspace, "triage", kind="task")
    ledger.record_and_persist(workspace, "policy", decision="deny")

    path = decision_ledger_path(workspace)
    with open(path) as f:
        lines = [line for line in f if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "triage"
    assert json.loads(lines[1])["type"] == "policy"


def test_load_skips_a_malformed_trailing_line_without_losing_earlier_ones(workspace):
    path = decision_ledger_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(json.dumps({"type": "triage", "kind": "task"}) + "\n")
        f.write("not valid json\n")
        f.write(json.dumps({"type": "policy", "decision": "deny"}) + "\n")

    ledger = load_decision_ledger(workspace)
    assert len(ledger.all()) == 2
    assert ledger.all()[0].type == "triage"
    assert ledger.all()[1].type == "policy"


def test_append_goes_through_authorized_file_writer(workspace, monkeypatch):
    import kriya.policy.filesystem as fs_mod

    monkeypatch.setattr(fs_mod, "is_within_scope", lambda scope, target: False)
    with pytest.raises(PolicyDeniedError):
        DecisionLedger().record_and_persist(workspace, "triage", kind="task")
    monkeypatch.undo()
