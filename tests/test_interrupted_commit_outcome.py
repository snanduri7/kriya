"""``commit_service.interrupted_commit_outcome``: what the durable records say about a terminal commit that raised before the service
returned (addendum #5/#6).

The invariant: an EARLIER transaction of the same run - a milestone sequence commits several units under one run id, and each settles
COMMITTED in the batch journal AND in the owning run's cycles - must never vouch for the interrupted one. Only a transaction that
started AFTER the snapshot taken just before the terminal commit counts, and a journal COMMITTED counts only where the commit's own
settled cycle agrees (the digest re-read and the registry promotion come between the two)."""
import time

import pytest

from kriya.control import run_coordinator
from kriya.repository import commit
from kriya.repository.commit import CommitState
from kriya.workflow import commit_service

RUN = "run-abc"


def _journal(workspace, transaction, state):
    commit._persist_commit_evidence(str(workspace), commit.CommitEvidence(            # pylint: disable=protected-access
        schema_version=commit._COMMIT_EVIDENCE_SCHEMA_VERSION, transaction_id=transaction,   # pylint: disable=protected-access
        state=state, started_at_unix=time.time(), updated_at_unix=time.time(), operations=(), result_revisions={}))


@pytest.mark.parametrize("current_state, current_result, expected", [
    (CommitState.IN_PROGRESS, None, "UNCERTAIN"),                 # started, never proven
    (CommitState.COMMITTED, None, "UNCERTAIN"),                   # bytes moved, this cycle not settled
    (CommitState.COMMITTED, "UNCERTAIN", "UNCERTAIN"),            # settled as uncertain
    (CommitState.COMMITTED, "COMMITTED", "COMMITTED"),            # control: settled by the commit's own record
    (CommitState.ROLLED_BACK, "ROLLED_BACK", None),               # control: proven nothing durable
], ids=["in-progress", "journal-committed-unsettled", "settled-uncertain", "settled-committed", "rolled-back"])
def test_an_earlier_settled_unit_of_the_same_run_never_vouches_for_the_interrupted_commit(tmp_path, monkeypatch, current_state,
                                                                                         current_result, expected):
    prefix = commit_service._transaction_prefix(RUN)            # pylint: disable=protected-access
    earlier, current = f"{prefix}-unit1", f"{prefix}-unit2"
    _journal(tmp_path, earlier, CommitState.COMMITTED)
    before = commit_service.run_commit_states(str(tmp_path), RUN)
    assert before == {earlier: CommitState.COMMITTED}                 # the snapshot really contains the earlier, settled unit
    _journal(tmp_path, current, current_state)
    cycles = [{"transaction_id": earlier, "result": "COMMITTED"}, {"transaction_id": current}]
    if current_result is not None:
        cycles[1]["result"] = current_result
    monkeypatch.setattr(run_coordinator, "owning_run_commits", lambda _workspace: (RUN, cycles))
    assert commit_service.interrupted_commit_outcome(str(tmp_path), RUN, before) == expected


def test_a_commit_that_left_no_journal_entry_is_nothing_durable_even_with_earlier_units(tmp_path, monkeypatch):
    prefix = commit_service._transaction_prefix(RUN)            # pylint: disable=protected-access
    earlier = f"{prefix}-unit1"
    _journal(tmp_path, earlier, CommitState.COMMITTED)
    before = commit_service.run_commit_states(str(tmp_path), RUN)
    monkeypatch.setattr(run_coordinator, "owning_run_commits", lambda _workspace: (RUN, [{"transaction_id": earlier, "result": "COMMITTED"}]))
    assert commit_service.interrupted_commit_outcome(str(tmp_path), RUN, before) is None
