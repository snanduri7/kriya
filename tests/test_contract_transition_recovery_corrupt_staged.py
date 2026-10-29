"""Wave 2 Step 7 (A5): recovery of a contract-registry transition whose STAGED after-state is damaged is a typed disposition, never a traceback.

MEASURED at 83ccda0 (step7_repro.py in the Step-7 evidence directory): ``complete_contract_transition(committed=True)`` promotes the staged after-state. A staged file that is ASCII garbage,
truncated JSON, empty, a JSON non-object or a wrong-shape registry raises ``ContractRegistryCorruptError`` (a RuntimeError) from the staged reader; the promotion's handler caught only
``(ContractRegistryTransitionError, OSError, ValueError)``, so the typed error escaped recovery as a traceback. A non-UTF-8 staged file (a ValueError) and a valid-but-wrong after-state already
gave NEEDS_REVIEW - those are preserved. Now every unreadable staged state is NEEDS_REVIEW with the evidence left exactly where it is: nothing promoted, nothing discarded, nothing invented, the
run record left open, and ``kriya runs recover`` reports it instead of crashing.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(__file__))
from test_prd029_contract_lifecycle import (  # noqa: E402
    OWNER,
    OWNER_V2,
    _builder,
    _in_run,
    _record,
    _workspace,
    _writes,
)

from kriya.cli import main  # noqa: E402
from kriya.context.workspace_identity import ownership_metadata  # noqa: E402
from kriya.control.contracts import ContractRegistry  # noqa: E402
from kriya.control.persistence import (  # noqa: E402
    TRANSITION_ALREADY_APPLIED,
    TRANSITION_DISCARDED,
    TRANSITION_NEEDS_REVIEW,
    TRANSITION_PROMOTED,
    complete_contract_transition,
    contract_registry_path,
    load_contract_registry,
    pending_contract_registry_path,
    save_contract_registry,
)
from kriya.control.recovery import recover_workspace  # noqa: E402
from kriya.control.run_record import RunLifecycle  # noqa: E402
from kriya.workflow.edit_safety import read_file_revision  # noqa: E402,F401
from kriya.workflow.terminal_commit import commit_terminal_candidate  # noqa: E402
from kriya.workflow.verification_binding import bind_candidate  # noqa: E402

TX = "tx-1"

# Every damaged form of the STAGED registry; the callable receives the valid after-state's text and the workspace and returns the bytes to leave staged.
DAMAGED = {
    "ascii_garbage": lambda good, ws: b"this is not json at all",
    "truncated_json": lambda good, ws: good[: len(good) // 2].encode(),
    "empty_file": lambda good, ws: b"",
    "not_an_object": lambda good, ws: b"[1, 2, 3]",
    "wrong_shape_registry": lambda good, ws: json.dumps({**json.loads(good), "contracts": {"m:cap": [{"id": "m:cap"}]}}).encode(),
    "non_utf8_bytes": lambda good, ws: b"\xff\xfe\x00garbage",                                # already NEEDS_REVIEW before the fix (a ValueError): preserved
    "valid_but_not_the_recorded_after_state": lambda good, ws: json.dumps({**ContractRegistry().to_dict(), "_workspace": ownership_metadata(ws)}).encode(),   # likewise
}


def _world(tmp_path):
    ws = str(tmp_path)
    before = ContractRegistry()
    save_contract_registry(ws, before)
    after = ContractRegistry()
    after.register("M1:cap", "cap", "M1", "shape")
    good = json.dumps({**after.to_dict(), "_workspace": ownership_metadata(ws)})
    intent = {"before_digest": before.digest(), "after_digest": after.digest()}
    return ws, good, intent


def _bytes_or_none(path):
    """The file's bytes, or None when it does not exist (an absent registry is the empty one, and recovery must not create it)."""
    return open(path, "rb").read() if os.path.exists(path) else None


def _stage(ws, data):
    path = pending_contract_registry_path(ws, TX)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


# ------------------------------------------------------------------------------------------------------------------- the transition owner

@pytest.mark.parametrize("form", sorted(DAMAGED))
def test_a_damaged_staged_registry_is_needs_review_and_everything_is_left_exactly_as_it_is(tmp_path, form):
    ws, good, intent = _world(tmp_path)
    staged = _stage(ws, DAMAGED[form](good, ws))
    live_before = open(contract_registry_path(ws), "rb").read()
    staged_before = open(staged, "rb").read()
    outcome = complete_contract_transition(ws, TX, intent, committed=True)         # never raises
    assert outcome == TRANSITION_NEEDS_REVIEW
    assert open(staged, "rb").read() == staged_before                              # not discarded, not repaired: the evidence a person reviews
    assert open(contract_registry_path(ws), "rb").read() == live_before            # nothing promoted over the live registry
    assert load_contract_registry(ws).digest() == intent["before_digest"]


def test_a_valid_staged_after_state_is_still_promoted(tmp_path):
    ws, good, intent = _world(tmp_path)
    staged = _stage(ws, good.encode())
    assert complete_contract_transition(ws, TX, intent, committed=True) == TRANSITION_PROMOTED
    assert load_contract_registry(ws).digest() == intent["after_digest"] and not os.path.exists(staged)


def test_a_missing_staged_file_is_still_needs_review(tmp_path):
    ws, _good, intent = _world(tmp_path)
    assert complete_contract_transition(ws, TX, intent, committed=True) == TRANSITION_NEEDS_REVIEW


def test_a_live_registry_that_is_corrupt_is_still_needs_review(tmp_path):
    ws, good, intent = _world(tmp_path)
    _stage(ws, good.encode())
    with open(contract_registry_path(ws), "wb") as handle:
        handle.write(b"live registry garbage")
    assert complete_contract_transition(ws, TX, intent, committed=True) == TRANSITION_NEEDS_REVIEW
    with open(contract_registry_path(ws), "rb") as handle:
        assert handle.read() == b"live registry garbage"


def test_a_live_registry_that_became_corrupt_mid_promotion_is_needs_review_too(tmp_path):
    """The promotion re-reads the live registry; a corruption error from THAT read (not the first) used to escape the same way."""
    from kriya.control import persistence

    ws, good, intent = _world(tmp_path)
    _stage(ws, good.encode())
    real, calls = persistence.load_contract_registry, []

    def second_read_corrupt(workspace):
        calls.append(1)
        if len(calls) >= 2:
            raise persistence.ContractRegistryCorruptError(contract_registry_path(workspace), "unreadable: injected")
        return real(workspace)
    with patch.object(persistence, "load_contract_registry", side_effect=second_read_corrupt):
        assert complete_contract_transition(ws, TX, intent, committed=True) == TRANSITION_NEEDS_REVIEW


def test_the_uncommitted_branch_still_discards_a_damaged_staged_file_when_the_live_registry_is_the_before_state(tmp_path):
    """Unchanged: an uncommitted cycle's staged transition is discarded without being read."""
    ws, good, intent = _world(tmp_path)
    staged = _stage(ws, b"garbage")
    assert complete_contract_transition(ws, TX, intent, committed=False) == TRANSITION_DISCARDED and not os.path.exists(staged)


def test_the_already_applied_branch_is_unchanged(tmp_path):
    ws, _good, _intent = _world(tmp_path)
    after = ContractRegistry()
    after.register("M1:cap", "cap", "M1", "shape")
    save_contract_registry(ws, after)
    already = {"before_digest": "x", "after_digest": load_contract_registry(ws).digest()}      # the live registry IS the recorded after-state
    staged = _stage(ws, b"garbage")
    assert complete_contract_transition(ws, TX, already, committed=True) == TRANSITION_ALREADY_APPLIED and not os.path.exists(staged)


# ------------------------------------------------------------------------------------------------------------ the real recovery run, end to end

def _uncertain_run_with_a_staged_registry(tmp_path):
    """A real commit whose registry promotion failed after the source landed (UNCERTAIN, the after-state staged) - the harness of PRD-029's own crash-recovery test."""
    workspace = _workspace(tmp_path)
    with patch("kriya.control.persistence.promote_pending_contract_registry", side_effect=OSError("io")):
        outcome = _in_run(workspace, lambda: commit_terminal_candidate(
            _writes(workspace), workspace_path=workspace, verified_candidate=bind_candidate(_writes(workspace), workspace),
            static_analysis=__import__("_fake_static_analysis").DISABLED_STATIC_ANALYSIS, transaction_id="tx1", contract_transition=_builder(workspace),
        ))
    assert not outcome.committed and outcome.workspace_state == "UNCERTAIN"
    staged = pending_contract_registry_path(workspace, "tx1")
    assert os.path.isfile(staged)
    return workspace, staged


@pytest.mark.parametrize("form", ["ascii_garbage", "truncated_json", "empty_file", "not_an_object", "wrong_shape_registry"])
def test_kriya_runs_recover_reports_a_damaged_staged_registry_and_leaves_the_run_open(tmp_path, form):
    workspace, staged = _uncertain_run_with_a_staged_registry(tmp_path)
    good = open(staged, encoding="utf-8").read()
    with open(staged, "wb") as handle:
        handle.write(DAMAGED[form](good, workspace))
    damaged = open(staged, "rb").read()
    live_before = _bytes_or_none(contract_registry_path(workspace))
    report = recover_workspace(workspace)                                           # no traceback
    assert report.contract_transitions[0]["outcome"] == TRANSITION_NEEDS_REVIEW
    assert any("needs review" in error and "unreadable" in error for error in report.errors)
    assert _record(workspace).lifecycle_state is RunLifecycle.UNCERTAIN              # left open for review: no fabricated success, no silent absence
    assert open(staged, "rb").read() == damaged and _bytes_or_none(contract_registry_path(workspace)) == live_before
    assert open(os.path.join(workspace, OWNER), encoding="utf-8").read() == OWNER_V2   # the source commit stands; only the registry is unsettled


def test_the_recover_command_prints_the_review_finding_instead_of_a_traceback(tmp_path):
    workspace, staged = _uncertain_run_with_a_staged_registry(tmp_path)
    with open(staged, "wb") as handle:
        handle.write(b"this is not json at all")
    result = CliRunner().invoke(main, ["runs", "recover", "--workspace", workspace])
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output and "ContractRegistryCorruptError" not in result.output
    assert "needs review" in result.output.lower()
