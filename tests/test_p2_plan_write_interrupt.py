"""P-2 final disposition: INTENTIONAL CRASH-CONSISTENCY CONTRACT for an interrupt AT the plan-state write.

The destructive defect (the verified candidate removed by a second interrupt) is fixed and pinned in tests/test_p2_candidate_retention.py. What remains is one
crash window: the second interrupt lands on the approved-plan document's own write, so the document keeps its previous lifecycle (`in_progress`) instead of the
pessimistic `needs_review` written just before the outcome lookup.

Why that window is safe (each statement is pinned below):
  A. the write is atomic - a temp file plus ``os.replace`` (kriya/repository/atomic_write.py) - so an interrupt leaves the previous, complete document, never a partial one;
  B. the verified candidate is retained (the UNCERTAIN marker was set before the plan write and the ``finally`` honours it);
  C. the durable evidence is the commit journal (IN_PROGRESS entry) and the run record, neither of which the plan write touches;
  D. ``resume=True`` and E. a fresh run are both refused by the workspace commit assessment (``assess_workspace_commit_state``, the owner of "never start or resume
     model work while the workspace may hold a partial commit") BEFORE any Developer work: that gate reads the journal and the run record, not the plan's lifecycle;
  F. nothing is reused or reported falsely: no Developer call, no `completed`/`failed` plan, the stale document is left byte-for-byte as it was, the workspace is unchanged;
  G. the stale `in_progress` is interpreted by that same assessment (reason code UNCERTAIN_COMMIT_STATE) and settled only by explicit recovery (``kriya runs recover``).
"""
import json
import os

import pytest
from test_enforce_exception_finalization import _candidate_bytes, _enforce, _engine, _scenario, _SecondInterrupt
from test_p2_candidate_retention import VERIFIED, _begin_then_interrupt, _Interrupt, _second_interrupt_at

from kriya.control import persistence
from kriya.control.commit_state import REASON_UNCERTAIN_COMMIT, assess_workspace_commit_state
from kriya.repository import atomic_write
from kriya.workflow import terminal_commit

from _strict_doubles import strict_config  # isort: skip


def _plan_document(repo):
    path = repo / ".kriya" / "control" / "plans" / "taf3-e2e.json"
    return path, path.read_bytes()


async def _interrupted_at_the_plan_write(tmp_path, monkeypatch):
    first, unwinding = _Interrupt(), []
    with monkeypatch.context() as patched:
        patched.setattr(terminal_commit, "commit_revision_grounded_batch", _begin_then_interrupt(first, on_first=lambda: unwinding.append(True)))
        second = _second_interrupt_at(patched, "approved_plan", unwinding)
        repo, raised, state = await _scenario(tmp_path, "int", monkeypatch=patched)
    assert raised is second and raised.__context__ is first
    return repo, state


@pytest.mark.asyncio
async def test_an_interrupt_at_the_plan_write_leaves_a_complete_previous_document_the_candidate_and_the_journal(tmp_path, monkeypatch):
    repo, state = await _interrupted_at_the_plan_write(tmp_path, monkeypatch)
    path, raw = _plan_document(repo)
    document = json.loads(raw)                                                   # A: a complete, parseable document - never a partial write
    assert document["lifecycle_state"] == "in_progress" == state["plan_lifecycle"]
    assert VERIFIED in _candidate_bytes(repo)                                    # B: the only copy of the verified bytes survives
    assert (repo / "app.py").read_text() == "original\n"                         # nothing was committed to the workspace
    assessment = assess_workspace_commit_state(str(repo))                        # C/G: the journal's IN_PROGRESS entry is what the next run reads
    assert not assessment.safe and REASON_UNCERTAIN_COMMIT in assessment.reason_codes and assessment.uncertain_commit_ids
    assert state["completion_scope"] != "workspace"                              # F: no completion claims the workspace holds the work
    assert path.exists()


@pytest.mark.asyncio
async def test_the_write_itself_is_atomic_an_interrupt_inside_the_replace_keeps_the_previous_document(tmp_path, monkeypatch):
    """A for real: the interrupt is raised by ``os.replace`` of the plan document's own atomic write (after the temp file was written), not by a stub in front of the writer."""
    first, unwinding = _Interrupt(), []
    second = _SecondInterrupt()
    real_replace = atomic_write.os.replace

    def replace(source, destination, *args, **kwargs):
        if unwinding and os.path.basename(str(destination)) == "taf3-e2e.json":
            raise second
        return real_replace(source, destination, *args, **kwargs)
    with monkeypatch.context() as patched:
        patched.setattr(terminal_commit, "commit_revision_grounded_batch", _begin_then_interrupt(first, on_first=lambda: unwinding.append(True)))
        patched.setattr(atomic_write.os, "replace", replace)
        repo, raised, state = await _scenario(tmp_path, "int", monkeypatch=patched)
    assert raised is second
    _path, raw = _plan_document(repo)
    assert json.loads(raw)["lifecycle_state"] == "in_progress" and state["plan_lifecycle"] == "in_progress"      # the previous document, whole
    assert VERIFIED in _candidate_bytes(repo)
    # The only leftover of the interrupted replace is the writer's own pid-named temp file: inert (no `.json` suffix, so nothing that lists plan documents reads it).
    assert [name for name in os.listdir(_path.parent) if name.endswith(".json")] == ["taf3-e2e.json"]


@pytest.mark.parametrize("entry", ["fresh", "resume"])
@pytest.mark.asyncio
async def test_after_an_interrupt_at_the_plan_write_a_fresh_run_and_resume_are_refused_before_any_model_work(tmp_path, monkeypatch, entry):
    repo, _state = await _interrupted_at_the_plan_write(tmp_path, monkeypatch)
    path, before = _plan_document(repo)

    config = strict_config(autonomy={"spec_compliance_enabled": True})
    engine = _engine(config, bind_filter=False)
    real_generate, developer_runs = engine.run_generation_workflow, []

    async def counting(*args, **kwargs):
        developer_runs.append(True)
        return await real_generate(*args, **kwargs)
    engine.run_generation_workflow = counting
    result = await _enforce(repo, engine, **({"resume": True} if entry == "resume" else {}))

    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False, legacy       # F: no false success / failure
    assert REASON_UNCERTAIN_COMMIT in legacy["reason_codes"], legacy                                      # G: the commit assessment names it
    assert not developer_runs                                                                             # F: no stale work reused, no new work started
    assert path.read_bytes() == before                                                                    # the stale document is not rewritten by the refusal
    assert VERIFIED in _candidate_bytes(repo) and (repo / "app.py").read_text() == "original\n"           # B: evidence and workspace untouched
    assert persistence.approved_plan_path(str(repo), "taf3-e2e") == str(path)
