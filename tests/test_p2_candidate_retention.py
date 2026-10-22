"""P-2 (durability): a second interrupt while the error handler decides the outcome of an UNPROVEN terminal commit must not
destroy the verified candidate.

MEASURED at f6194c4 (reconciliation, P-2): the first interrupt arrives after the durable IN_PROGRESS journal entry; a second
BaseException raised by the handler's journal lookup escaped the handler before ``workspace_commit_failure`` was assigned, so the
``finally`` that removes the candidate saw no UNCERTAIN marker and removed the worktree - the verified bytes were gone while the
journal still said the commit was unproven (PRD-004: the candidate "is the only copy of possibly-partly-applied verified bytes").

Invariant: after a started terminal commit the candidate is released only when the durable records PROVE the workspace holds the
bytes (COMMITTED) or that nothing durable happened / the commit was rolled back. An interrupted lookup proves nothing, so it keeps
the candidate. The second interrupt is still the exception that reaches the caller, with the first chained as its context."""
import time

import pytest
from _strict_doubles import strict_config
from test_enforce_exception_finalization import (
    _candidate_bytes,
    _enforce,
    _engine,
    _Interrupt,
    _scenario,
    _SecondInterrupt,
)

from kriya.control import persistence
from kriya.repository import commit
from kriya.workflow import commit_service, terminal_commit

VERIFIED = "verified candidate\n"


def _begin_then_interrupt(first, on_first=None):
    """The terminal commit has durably started (IN_PROGRESS journal entry) and is interrupted: the unproven state."""
    def begin(writes, workspace_path=None, *, transaction_id=None):
        commit._persist_commit_evidence(workspace_path, commit.CommitEvidence(      # pylint: disable=protected-access
            schema_version=commit._COMMIT_EVIDENCE_SCHEMA_VERSION, transaction_id=transaction_id,   # pylint: disable=protected-access
            state=commit.CommitState.IN_PROGRESS, started_at_unix=time.time(), updated_at_unix=time.time(), operations=(),
            result_revisions={}))
        if on_first is not None:
            on_first()
        raise first
    return begin


def _second_interrupt_at(monkeypatch, target, unwinding):
    """Arm a second interrupt at one finalization step, live only once the first interrupt is being handled."""
    second = _SecondInterrupt()
    if target == "outcome_lookup":
        real = commit_service.list_commit_evidence

        def lookup(workspace_path):
            if unwinding:
                raise second
            return real(workspace_path)
        monkeypatch.setattr(commit_service, "list_commit_evidence", lookup)
    else:
        real_save = persistence._save_json_document            # pylint: disable=protected-access
        resolve = persistence.control_state_path if target == "control_state" else None

        def save(*args, **kwargs):
            wanted = resolve(args[0]) if resolve else persistence.approved_plan_path(args[0], "taf3-e2e")
            if unwinding and args[1] == wanted:
                raise second
            return real_save(*args, **kwargs)
        monkeypatch.setattr(persistence, "_save_json_document", save)
    return second


@pytest.mark.parametrize("target", ["outcome_lookup", "control_state", "approved_plan"])
@pytest.mark.asyncio
async def test_a_second_interrupt_while_finalizing_an_unproven_commit_keeps_the_verified_candidate(tmp_path, monkeypatch, target):
    first, unwinding = _Interrupt(), []
    monkeypatch.setattr(terminal_commit, "commit_revision_grounded_batch",
                        _begin_then_interrupt(first, on_first=lambda: unwinding.append(True)))
    second = _second_interrupt_at(monkeypatch, target, unwinding)
    repo, raised, _state = await _scenario(tmp_path, "int", monkeypatch=monkeypatch)
    assert raised is second                                                  # the latest interrupt reaches the caller ...
    assert raised.__context__ is first                                       # ... with the first not lost
    assert VERIFIED in _candidate_bytes(repo)                                # P-2: the only copy of the verified bytes survives
    assert (repo / "app.py").read_text() == "original\n"                     # this stand-in committed nothing


@pytest.mark.asyncio
async def test_after_a_second_interrupt_the_run_is_refused_not_resumed_and_the_candidate_survives_the_refusal(tmp_path, monkeypatch):
    """Resume safety: the retained candidate does not make the next run proceed - it refuses with the typed UNCERTAIN code, calls
    no Developer, and leaves the retained bytes where recovery expects them."""
    first, unwinding = _Interrupt(), []
    with monkeypatch.context() as patched:
        patched.setattr(terminal_commit, "commit_revision_grounded_batch",
                        _begin_then_interrupt(first, on_first=lambda: unwinding.append(True)))
        _second_interrupt_at(patched, "outcome_lookup", unwinding)
        repo, raised, _state = await _scenario(tmp_path, "int", monkeypatch=patched)
    assert isinstance(raised, _SecondInterrupt) and VERIFIED in _candidate_bytes(repo)

    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)
    real_generate, developer_runs = engine.run_generation_workflow, []

    async def counting(*args, **kwargs):
        developer_runs.append(True)
        return await real_generate(*args, **kwargs)
    engine.run_generation_workflow = counting
    result = await _enforce(repo, engine)
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False, legacy
    assert "UNCERTAIN_COMMIT_STATE" in legacy["reason_codes"], legacy
    assert not developer_runs
    assert VERIFIED in _candidate_bytes(repo) and (repo / "app.py").read_text() == "original\n"


@pytest.mark.asyncio
async def test_a_single_interrupt_before_any_durable_commit_state_still_releases_the_candidate(tmp_path, monkeypatch):
    """The other half of the invariant, so 'always retain' cannot pass: the lookup WORKS and proves nothing durable happened (no
    journal entry at all) - the candidate is released (reset, as `remove_git_worktree` does) and the plan is `failed`, exactly the ordinary failed path."""
    def raise_before_any_journal(writes, workspace_path=None, *, transaction_id=None):
        raise _Interrupt()
    monkeypatch.setattr(terminal_commit, "commit_revision_grounded_batch", raise_before_any_journal)
    repo, raised, state = await _scenario(tmp_path, "int", monkeypatch=monkeypatch)
    assert isinstance(raised, _Interrupt)
    assert state["plan_lifecycle"] == "failed", state
    assert VERIFIED not in _candidate_bytes(repo)                            # released: reset to the repository's commit


@pytest.mark.asyncio
async def test_an_interrupt_after_the_commit_is_settled_committed_releases_the_candidate_because_the_workspace_holds_the_bytes(
        tmp_path, monkeypatch):
    """The COMMITTED half of the invariant: the journal and the commit's own settled record prove the workspace holds the verified
    bytes, so the candidate is no longer the only copy and is released (an unproven marker must not outlive that proof)."""
    real = commit_service.commit_terminal_candidate

    def commit_then_interrupt(*args, **kwargs):
        assert real(*args, **kwargs).committed is True
        raise _Interrupt()
    monkeypatch.setattr(commit_service, "commit_terminal_candidate", commit_then_interrupt)
    repo, raised, state = await _scenario(tmp_path, "int", monkeypatch=monkeypatch)
    assert isinstance(raised, _Interrupt)
    assert (repo / "app.py").read_text() == VERIFIED                         # durable in the workspace
    assert state["plan_lifecycle"] == "completed" and state["completion_scope"] == "workspace", state
    assert VERIFIED not in _candidate_bytes(repo)                            # released: reset to the repository's commit
