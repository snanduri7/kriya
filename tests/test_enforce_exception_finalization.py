"""D-F1 of the consolidated addendum: once an enforce run ends on an unexpected error, its durable state agrees with that
terminal result.

MEASURED by the independent reviewer on 72d9182: after the terminal gate raised (an ordinary closer error, correctly
``status=error`` / ``failure_category=<Type>``), the approved plan stayed ``lifecycle_state: in_progress`` with ``s1: completed``
and ``control/state.json`` kept ``s1: completed`` plus its written files - the enforce loop's sandbox-state reset and its final
plan save sit AFTER the failing call, so an exception skipped them. The resume guard neutralised the stale state, but the
persisted state still said an active run had completed work. The ordinary failed path (a requirements gap) persists
``lifecycle_state: failed`` and ``s1: pending`` with no written files, because a sandbox-only completion cannot be resumed once the
candidate is discarded.

Invariant: the exception path persists exactly what the ordinary failed path persists - no new status - while the exception, its
class, the ``.enforce`` row (status=error), the fail-closed outcome (nothing committed) and the candidate cleanup are unchanged.

D-F2 (decided with the code contract, not assumed): ``gate.result`` records are written by
``terminal_gate_service._record_terminal_report`` "from the report the commit decision itself reads" (each payload carries
``commit_eligible``). When the gate raises there is no report and no commit decision, so there is nothing to record; the
controller's ``run.exception`` is the one authoritative terminal record and the explainer ranks it first. A synthetic
``gate.result`` would invent a ``commit_eligible`` verdict, so none is written - pinned at the bottom with a control.

Real enforce route (the harness of test_taf3_enforce_terminal_refusal); no new patch sites.
"""
import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _strict_doubles import strict_config
from test_prd030_terminal_services import _request, _run, _validators
from test_taf3_enforce_terminal_refusal import _enforce, _engine, _repo

from kriya.config.config import AppConfig
from kriya.core.state_paths import trace_db_path
from kriya.workflow import requirement_closers, terminal_gate_service

PLAN_ID = "taf3-e2e"          # the plan id the shared harness builds


def _repo_in(root: Path) -> Path:
    root.mkdir(parents=True)
    return _repo(root, root / "FILTER_RAN")


def _enforce_rows(config) -> list:
    """Every .enforce trace row of this test's state directory (two runs share it)."""
    conn = sqlite3.connect(trace_db_path(config))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM runs WHERE run_id LIKE '%.enforce'")]
    finally:
        conn.close()


def _recorded_artifacts(path: Path):
    """What the artifact registry records, without the per-repository identities (workspace path, run id, commit)."""
    if not path.exists():
        return None
    document = json.loads(path.read_text())
    return sorted((record["ecosystem"], record["kind"], tuple(sorted(record["coordinates"].items())))
                  for records in (document.get("artifacts") or {}).values() for record in records)


def _persisted(repo: Path) -> dict:
    """What the run left durable: the approved plan document's lifecycle and stage states, the control state's subtask states
    and written files, and how many git worktrees the repository still lists (the candidate's cleanup)."""
    control = repo / ".kriya" / "control"
    plan = json.loads((control / "plans" / f"{PLAN_ID}.json").read_text())
    state = json.loads((control / "state.json").read_text())
    listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout
    return {
        "plan_lifecycle": plan.get("lifecycle_state"),
        "plan_stages": plan.get("stage_states"),
        "subtask_states": state.get("subtask_states"),
        "written_files": state.get("subtask_written_files"),
        "completion_scope": state.get("subtask_completion_scope"),
        "artifact_registry": _recorded_artifacts(control / "artifacts.json"),
        "git_worktrees": listed.count("worktree "),
    }


def _candidate_bytes(repo: Path) -> list:
    """app.py as every OTHER git worktree of the repository holds it (the run's candidate sandbox)."""
    listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout
    paths = [Path(line.split(" ", 1)[1]) for line in listed.splitlines() if line.startswith("worktree ")]
    return sorted((path / "app.py").read_text() for path in paths if path.resolve() != repo.resolve() and (path / "app.py").exists())


async def _ordinary_failed_run(tmp_path) -> dict:
    """An ordinary (non-exception) terminal failure: the verifier judges 'Update app.py.' missing -> a requirements gap."""
    repo = _repo_in(tmp_path / "gap")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)
    engine.spec_compliance.check = AsyncMock(return_value={
        "requirement_verdicts": [{"id": "REQ-1", "verdict": "missing", "evidence": "app.py is not updated"}]})
    result = await _enforce(repo, engine)
    assert result.legacy_result["status"] == "failed", result.legacy_result
    return _persisted(repo)


@pytest.mark.parametrize("error", [RuntimeError("closer broke"), OSError("disk vanished")], ids=["RuntimeError", "OSError"])
@pytest.mark.asyncio
async def test_an_unexpected_terminal_error_persists_exactly_what_the_ordinary_failed_path_persists(tmp_path, monkeypatch, error):
    ordinary = await _ordinary_failed_run(tmp_path)
    # the reference is itself meaningful (not an empty comparison): failed, nothing completed, nothing written
    assert ordinary["plan_lifecycle"] == "failed" and set(ordinary["plan_stages"].values()) == {"pending"}, ordinary
    assert set(ordinary["subtask_states"].values()) == {"pending"} and ordinary["written_files"] == {}, ordinary

    repo = _repo_in(tmp_path / "boom")
    config = strict_config(autonomy={"spec_compliance_enabled": True})

    def broken(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(requirement_closers, "close_requirements_by_mutation_scope", broken)
    with pytest.raises(type(error)) as raised:
        await _enforce(repo, _engine(config, bind_filter=False))
    assert raised.value is error                                                 # the class is retained
    errored = [row for row in _enforce_rows(config) if row["status"] == "error"]
    assert [row["failure_category"] for row in errored] == [type(error).__name__], errored
    assert (repo / "app.py").read_text() == "original\n"                         # fail closed: nothing committed
    assert _persisted(repo) == ordinary                                          # D-F1: the durable state is the failed path's


@pytest.mark.asyncio
async def test_the_run_after_an_unexpected_terminal_error_is_not_blocked_by_its_state(tmp_path, monkeypatch):
    """Resume safety: the repository is usable straight away (the lease is released and nothing stale is reused)."""
    repo = _repo_in(tmp_path / "boom")
    config = strict_config(autonomy={"spec_compliance_enabled": True})

    def broken(*_args, **_kwargs):
        raise RuntimeError("closer broke")
    with monkeypatch.context() as patched:
        patched.setattr(requirement_closers, "close_requirements_by_mutation_scope", broken)
        with pytest.raises(RuntimeError):
            await _enforce(repo, _engine(config, bind_filter=False))
    result = await _enforce(repo, _engine(config, bind_filter=False))
    assert result.legacy_result["status"] == "success", result.legacy_result
    assert (repo / "app.py").read_text() == "verified candidate\n"


class _Interrupt(BaseException):
    """A BaseException that is neither Exception nor asyncio's CancelledError (a stand-in for KeyboardInterrupt/SystemExit)."""


def _interrupting(engine, at_event: str, error_class=asyncio.CancelledError) -> None:
    async def emit(event_name, _data):
        if event_name == at_event:
            raise error_class()
    engine.kernel.events.emit = emit


def _commit_returns_failure(monkeypatch, workspace_state: str) -> None:
    """The commit service RETURNS a failure (the ordinary path then persists needs_review): UNCHANGED through a real refusal
    (the candidate cannot be materialized), UNCERTAIN through a non-committed outcome of the one transaction function."""
    from types import SimpleNamespace

    from kriya.workflow import commit_service

    if workspace_state == "UNCHANGED":
        def refuse(_request):
            raise commit_service.CandidateMaterializationError("probe: the candidate cannot be materialized")
        monkeypatch.setattr(commit_service, "terminal_writes", refuse)
    else:
        def uncertain(*_args, **_kwargs):
            return SimpleNamespace(committed=False, failure_payload=lambda: {
                "reason_code": "WORKSPACE_COMMIT_FAILED", "workspace_state": "UNCERTAIN", "error": "probe",
                "commit_transaction_id": "probe"})
        monkeypatch.setattr(commit_service, "commit_terminal_candidate", uncertain)


async def _scenario(tmp_path, name, *, monkeypatch=None, commit_failure=None, interrupt_at=None, error_class=asyncio.CancelledError):
    """One enforce run in its own repository; returns (repo, raised BaseException or None, persisted state)."""
    repo = _repo_in(tmp_path / name)
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)
    if interrupt_at:
        _interrupting(engine, interrupt_at, error_class)
    raised = None
    with monkeypatch.context() as patched:
        if commit_failure:
            _commit_returns_failure(patched, commit_failure)
        try:
            await _enforce(repo, engine)
        except BaseException as error:               # noqa: BLE001 - the scenario's outcome
            raised = error
    return repo, raised, _persisted(repo)


@pytest.mark.parametrize("workspace_state", ["UNCHANGED", "UNCERTAIN"])
@pytest.mark.asyncio
async def test_an_exception_after_a_returned_commit_failure_keeps_the_commit_decisions_lifecycle(tmp_path, monkeypatch, workspace_state):
    """Exceptional unwinding must not overwrite a more authoritative lifecycle decision already made: once the commit service has
    RETURNED a failure the ordinary path persists needs_review; an interrupt at the very next event (workspace_commit_failed) must leave
    exactly that - not `failed`."""
    _repo_ref, raised_ref, reference = await _scenario(tmp_path, "ref", monkeypatch=monkeypatch, commit_failure=workspace_state)
    assert raised_ref is None and reference["plan_lifecycle"] == "needs_review", reference           # the reference is meaningful
    _repo, raised, interrupted = await _scenario(tmp_path, "int", monkeypatch=monkeypatch, commit_failure=workspace_state,
                                            interrupt_at="workspace_commit_failed")
    assert isinstance(raised, asyncio.CancelledError)                                                # the interrupt still propagates
    assert interrupted == reference


@pytest.mark.asyncio
async def test_an_interrupt_after_the_durable_commit_leaves_exactly_what_the_successful_path_leaves(tmp_path, monkeypatch):
    """The workspace holds the verified work, so the durable plan and the completion scope say so: the same two bookkeeping steps
    the ordinary path takes after the commit (completions adopted by the workspace, the plan saved completed) - never failed, never
    left in_progress with the completions still owned by a candidate that no longer exists."""
    repo_ref, raised_ref, reference = await _scenario(tmp_path, "ref", monkeypatch=monkeypatch)
    assert raised_ref is None and reference["plan_lifecycle"] == "completed" and reference["completion_scope"] == "workspace", reference
    repo, raised, interrupted = await _scenario(tmp_path, "int", monkeypatch=monkeypatch, interrupt_at="workspace_commit_completed")
    assert isinstance(raised, asyncio.CancelledError)
    assert (repo / "app.py").read_text() == "verified candidate\n"                                   # the commit is durable
    assert interrupted == reference


@pytest.mark.asyncio
async def test_an_interrupt_after_the_commit_is_durable_but_before_the_service_returns_leaves_the_successful_runs_state(tmp_path, monkeypatch):
    """Addendum #4 observation A (owner: durable repository truth must not contradict the plan/run state). The window: the real
    transaction has settled COMMITTED in the RunRecord and the bytes are in the workspace, and the interrupt arrives before
    `commit_verified_candidate` hands the result back - so the local `workspace_commit_completed` flag is still False. The handler
    must not guess `failed` over a workspace that holds the verified work; the durable commit record is the authority."""
    from kriya.workflow import commit_service

    repo_ref, raised_ref, reference = await _scenario(tmp_path, "ref", monkeypatch=monkeypatch)
    assert raised_ref is None and reference["plan_lifecycle"] == "completed" and reference["completion_scope"] == "workspace", reference
    real = commit_service.commit_terminal_candidate

    def commit_then_interrupt(*args, **kwargs):
        outcome = real(*args, **kwargs)
        assert outcome.committed is True                                       # the transaction really committed
        raise _Interrupt()
    monkeypatch.setattr(commit_service, "commit_terminal_candidate", commit_then_interrupt)
    repo, raised, interrupted = await _scenario(tmp_path, "int", monkeypatch=monkeypatch)
    assert isinstance(raised, _Interrupt)
    assert (repo / "app.py").read_text() == "verified candidate\n"                                   # durable repository truth
    assert interrupted == reference                                                                   # and the plan/control state agree with it


@pytest.mark.asyncio
async def test_an_interrupt_during_the_commit_that_the_journal_proves_rolled_back_is_a_failed_plan(tmp_path, monkeypatch):
    """The interrupt arrives after the durable intent, while the batch is being staged; the transaction rolls itself back (journal
    ROLLED_BACK). The workspace is proven unchanged, so `failed` and a discarded candidate are the TRUE outcome - the journal, not a
    guess, says so."""
    from kriya.repository import commit

    ordinary = await _ordinary_failed_run(tmp_path)

    def interrupted(*_args, **_kwargs):
        raise _Interrupt()
    monkeypatch.setattr(commit, "_stage_content", interrupted)
    repo, raised, state = await _scenario(tmp_path, "int", monkeypatch=monkeypatch)
    assert isinstance(raised, _Interrupt)
    assert (repo / "app.py").read_text() == "original\n"
    assert state == ordinary, state
    assert "verified candidate\n" not in _candidate_bytes(repo)               # proven unchanged: the candidate is reset, not kept


@pytest.mark.asyncio
async def test_an_interrupt_during_the_commit_that_the_journal_cannot_prove_finished_is_the_uncertain_outcome(tmp_path, monkeypatch):
    """The other half of observation A: the transaction started (durable IN_PROGRESS evidence) and the interrupt leaves it
    unsettled. Whether the workspace holds the candidate is unproven - the existing UNCERTAIN outcome: the plan is needs_review and
    the verified candidate is KEPT for explicit recovery (the only copy of bytes the commit may have partly applied), exactly what a
    RETURNED uncertain commit leaves. `failed` plus a deleted candidate would be a guess."""
    import time

    from kriya.repository import commit

    repo_ref, raised_ref, reference = await _scenario(tmp_path, "ref", monkeypatch=monkeypatch, commit_failure="UNCERTAIN")
    assert raised_ref is None and reference["plan_lifecycle"] == "needs_review", reference
    assert "verified candidate\n" in _candidate_bytes(repo_ref)                # the reference KEEPS the candidate's bytes

    def begin_then_interrupt(writes, workspace_path=None, *, transaction_id=None):
        commit._persist_commit_evidence(workspace_path, commit.CommitEvidence(      # pylint: disable=protected-access
            schema_version=commit._COMMIT_EVIDENCE_SCHEMA_VERSION, transaction_id=transaction_id,   # pylint: disable=protected-access
            state=commit.CommitState.IN_PROGRESS, started_at_unix=time.time(), updated_at_unix=time.time(), operations=(), result_revisions={}))
        raise _Interrupt()
    from kriya.workflow import terminal_commit
    monkeypatch.setattr(terminal_commit, "commit_revision_grounded_batch", begin_then_interrupt)
    repo, raised, state = await _scenario(tmp_path, "int", monkeypatch=monkeypatch)
    assert isinstance(raised, _Interrupt)
    assert state == reference, state                                          # needs_review, scope candidate
    assert "verified candidate\n" in _candidate_bytes(repo)                    # the candidate is KEPT for explicit recovery
    assert (repo / "app.py").read_text() == "original\n"                       # this stand-in wrote nothing


@pytest.mark.asyncio
async def test_an_interrupt_during_the_commit_whose_journal_is_unreadable_is_uncertain_never_a_guess(tmp_path, monkeypatch):
    """If the journal entry of this run's transaction cannot be read, what happened is unknown - UNCERTAIN, not `failed`."""
    from kriya.repository import commit
    from kriya.workflow import terminal_commit

    repo_ref, raised_ref, reference = await _scenario(tmp_path, "ref", monkeypatch=monkeypatch, commit_failure="UNCERTAIN")
    assert raised_ref is None and reference["plan_lifecycle"] == "needs_review"

    def garbage_then_interrupt(writes, workspace_path=None, *, transaction_id=None):
        directory = Path(commit.commit_evidence_dir(workspace_path))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{transaction_id}.json").write_text("{ this is not json")
        raise _Interrupt()
    monkeypatch.setattr(terminal_commit, "commit_revision_grounded_batch", garbage_then_interrupt)
    repo, raised, state = await _scenario(tmp_path, "int", monkeypatch=monkeypatch)
    assert isinstance(raised, _Interrupt)
    assert state == reference and "verified candidate\n" in _candidate_bytes(repo)


@pytest.mark.asyncio
async def test_an_earlier_committed_unit_of_the_same_run_is_not_mistaken_for_the_interrupted_terminal_commit(tmp_path, monkeypatch):
    """A milestone sequence commits several units under one run id. The journal diff is taken against the state just before THIS
    terminal commit, so an older COMMITTED transaction of the same run cannot make an interrupted, unproven commit look committed."""
    import time

    from kriya.repository import commit
    from kriya.workflow import commit_service, terminal_commit

    _repo_ref, raised_ref, reference = await _scenario(tmp_path, "ref", monkeypatch=monkeypatch, commit_failure="UNCERTAIN")
    assert raised_ref is None and reference["plan_lifecycle"] == "needs_review"

    def begin_then_interrupt(writes, workspace_path=None, *, transaction_id=None):
        commit._persist_commit_evidence(workspace_path, commit.CommitEvidence(      # pylint: disable=protected-access
            schema_version=commit._COMMIT_EVIDENCE_SCHEMA_VERSION, transaction_id=transaction_id,   # pylint: disable=protected-access
            state=commit.CommitState.IN_PROGRESS, started_at_unix=time.time(), updated_at_unix=time.time(), operations=(),
            result_revisions={}))
        raise _Interrupt()
    monkeypatch.setattr(terminal_commit, "commit_revision_grounded_batch", begin_then_interrupt)
    repo = _repo_in(tmp_path / "int")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)

    async def emit(event_name, data):
        if event_name == "commit_eligible":                 # just before the terminal commit's journal snapshot
            commit._persist_commit_evidence(str(repo), commit.CommitEvidence(      # pylint: disable=protected-access
                schema_version=commit._COMMIT_EVIDENCE_SCHEMA_VERSION,             # pylint: disable=protected-access
                transaction_id=f"{commit_service._transaction_prefix(data['run_id'])}-earlierunit",   # pylint: disable=protected-access
                state=commit.CommitState.COMMITTED, started_at_unix=time.time(), updated_at_unix=time.time(), operations=(),
                result_revisions={}))
    engine.kernel.events.emit = emit
    with pytest.raises(_Interrupt):
        await _enforce(repo, engine)
    state = _persisted(repo)
    assert state["plan_lifecycle"] == "needs_review" and state["completion_scope"] != "workspace", state
    assert "verified candidate\n" in _candidate_bytes(repo)


def _derive_one_artifact(monkeypatch) -> None:
    """The candidate yields one derived artifact record (the harness repository has no pyproject to derive from)."""
    from kriya.control.artifacts import ArtifactRecord, ArtifactRegistry

    def derive(_workspace_path, milestone_id, resolved_at_commit=None):
        return (ArtifactRecord(milestone_id=milestone_id, ecosystem="python", kind="package", coordinates={"name": "demo"},
                               resolved_at_commit=resolved_at_commit),)
    monkeypatch.setattr(ArtifactRegistry, "derive_from_workspace", staticmethod(derive))


@pytest.mark.asyncio
async def test_an_interrupt_after_the_durable_commit_still_records_the_artifacts_derived_from_the_verified_candidate(tmp_path, monkeypatch):
    """Observation C (owner: mandatory evidence after a committed mutation must not silently vanish). The artifact registry holds the
    facts derived from the candidate that is now the workspace; the ordinary path records them after the commit, and so does the
    handler - one shared step, so the interrupted run leaves the registry the successful run leaves."""
    _derive_one_artifact(monkeypatch)
    _repo_ref, raised_ref, reference = await _scenario(tmp_path, "ref", monkeypatch=monkeypatch)
    assert raised_ref is None and reference["artifact_registry"], reference                    # the reference really records one
    _repo, raised, interrupted = await _scenario(tmp_path, "int", monkeypatch=monkeypatch, interrupt_at="workspace_commit_completed")
    assert isinstance(raised, asyncio.CancelledError)
    assert interrupted["artifact_registry"] == reference["artifact_registry"]
    assert interrupted == reference


class _SecondInterrupt(BaseException):
    """A second operator interrupt, arriving while the first is being finalized."""


@pytest.mark.parametrize("target", ["control_state", "approved_plan"])
@pytest.mark.asyncio
async def test_a_second_interrupt_while_finalizing_leaves_no_reusable_completion_and_chains_the_first(tmp_path, monkeypatch, target):
    """Observation B. The second interrupt is the exception that reaches the caller (the first stays chained as its context) -
    that is the operator's latest and escalating intent, and both are interrupts. What matters is the durable state it leaves: the
    two finalization writes (control state, then the plan) are individually atomic, and an interruption between them leaves
    completions marked `candidate` scope - which resume never reuses (the candidate is gone) - or already discarded. Never a
    `workspace`-scoped completion that was not committed."""
    from kriya.control import persistence

    real = persistence._save_json_document            # pylint: disable=protected-access
    unwinding = []
    resolve = persistence.control_state_path if target == "control_state" else (lambda ws: persistence.approved_plan_path(ws, PLAN_ID))

    def save(*args, **kwargs):
        if unwinding and args[1] == resolve(args[0]):
            raise _SecondInterrupt()
        return real(*args, **kwargs)
    first = _Interrupt()
    repo = _repo_in(tmp_path / "int")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)

    async def emit(event_name, _data):
        if event_name == "terminal_gates_started":
            unwinding.append(True)
            raise first
    engine.kernel.events.emit = emit
    monkeypatch.setattr(persistence, "_save_json_document", save)
    with pytest.raises(_SecondInterrupt) as raised:
        await _enforce(repo, engine)
    assert raised.value.__context__ is first                                    # the first interrupt is not lost
    state = _persisted(repo)
    assert state["completion_scope"] != "workspace"                             # nothing claims the workspace holds the work
    if "completed" in (state["subtask_states"] or {}).values():
        assert state["completion_scope"] == "candidate"                         # completions still owned by a dead candidate
    assert state["plan_lifecycle"] in ("in_progress", "failed")


@pytest.mark.asyncio
async def test_a_base_exception_that_is_not_an_exception_before_the_commit_is_finalized_like_the_failed_path(tmp_path, monkeypatch):
    """The width of the handler: narrowing `except BaseException` to `except Exception` would leave the plan in_progress here
    (an interrupt is not an Exception). Uses a BaseException subclass that is not asyncio's CancelledError."""
    ordinary = await _ordinary_failed_run(tmp_path)
    repo, raised, interrupted = await _scenario(tmp_path, "int", monkeypatch=monkeypatch, interrupt_at="terminal_gates_started",
                                           error_class=_Interrupt)
    assert isinstance(raised, _Interrupt)
    assert (repo / "app.py").read_text() == "original\n"                                             # fail closed: nothing committed
    assert interrupted == ordinary


@pytest.mark.asyncio
async def test_a_failing_control_state_save_during_finalization_keeps_plan_and_control_in_agreement_and_the_original_error(
        tmp_path, monkeypatch):
    """The discard of the sandbox completions is all-or-nothing: if the control state cannot be saved, the plan must not claim the
    completions were discarded. Either way the original error is re-raised, never replaced."""
    from kriya.control import persistence

    real = persistence._save_json_document            # pylint: disable=protected-access
    error = RuntimeError("closer broke")
    unwinding = []

    def failing_save_while_unwinding(*args, **kwargs):
        if unwinding and args[1] == persistence.control_state_path(args[0]):     # only the control state, only while unwinding
            raise OSError("control state write failed")
        return real(*args, **kwargs)

    def broken(*_args, **_kwargs):
        unwinding.append(True)
        raise error
    repo = _repo_in(tmp_path / "boom")
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    with monkeypatch.context() as patched:
        patched.setattr(requirement_closers, "close_requirements_by_mutation_scope", broken)
        patched.setattr(persistence, "_save_json_document", failing_save_while_unwinding)
        with pytest.raises(RuntimeError) as raised:
            await _enforce(repo, _engine(config, bind_filter=False))
    assert raised.value is error                                                       # the original error, not the OSError
    after = _persisted(repo)
    assert after["plan_lifecycle"] == "failed"
    assert set(after["plan_stages"].values()) == set(after["subtask_states"].values()), after      # plan and control agree


# ------------------------------------------------------------------------------------------------ D-F2 (intentional contract)

def _record_gate_results(monkeypatch) -> list:
    emitted: list = []
    scope = terminal_gate_service.attempt_evidence_scope
    monkeypatch.setattr(scope, "capture_mode", lambda: "on")
    monkeypatch.setattr(scope, "emit", lambda kind, payload, content=None: emitted.append((kind, payload)))
    return emitted


def test_d_f2_control_a_completed_gate_run_records_one_gate_result_per_gate_from_its_report(tmp_path, monkeypatch):
    emitted = _record_gate_results(monkeypatch)
    report, _events = _run(_request(tmp_path), _validators())
    gate_results = [payload for kind, payload in emitted if kind == "gate.result"]
    assert len(gate_results) == 8 and report.commit_eligible
    assert all("commit_eligible" in payload and payload["stage"] == "terminal" for payload in gate_results)


def test_d_f2_a_terminal_gate_that_raises_records_no_gate_result_because_no_commit_decision_exists(tmp_path, monkeypatch):
    """The exception leaves no report for the commit decision to read, so no gate.result is written - not even for the gates
    that had passed (a record would have to invent commit_eligible). The controller's run.exception is the terminal record."""
    emitted = _record_gate_results(monkeypatch)
    autonomy = AppConfig().autonomy
    autonomy.spec_compliance_enabled = True

    def broken(*_args, **_kwargs):
        raise RuntimeError("closer broke")
    monkeypatch.setattr(requirement_closers, "close_requirements_by_mutation_scope", broken)
    with pytest.raises(RuntimeError, match="closer broke"):
        _run(_request(tmp_path, autonomy=autonomy), _validators())
    assert [kind for kind, _payload in emitted if kind == "gate.result"] == []
