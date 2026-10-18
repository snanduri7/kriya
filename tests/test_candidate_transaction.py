"""The candidate staging of one attempt (full modularization W4, E10): characterization written and green at 6742f29,
BEFORE the per-file loops of ``run_attempt`` move into ``kriya/workflow/candidate_transaction.py``.

What is pinned, through the real ``run_attempt`` (only the Developer's answer is scripted: ``developer.run_generation``
returns the files) and at boundaries the move does not rebind:

- **Atomic refusal (TODAY's semantics, the A3 gate's shape on synthetic files):** the first invalid response refuses
  the whole attempt and nothing reaches the sandbox, whatever its position: a valid sibling BEFORE the invalid one is
  never staged, one AFTER it is never evaluated. Both refusal points are covered: the per-file operation contract
  (before any write is prepared) and the per-file staging loop (anchored-edit application, the write scope).
- **What a refusal leaves behind** (the partial side effects of the per-file loops that later attempts read): the
  valid sibling's carry-forward content (``last_candidate_contents``, deliberately recorded before the batch outcome is
  known: P9-R1 Case A), the accepted-edit and anchor-failure bookkeeping, the untouched ``files_written`` of a
  contract refusal against the reset one of a staging refusal, the captured originals.
- **Staged bytes identity:** after a staged attempt the ``candidate.change`` records carry, per path, exactly the
  digest of the bytes in the worktree when they are emitted (the commit happened first), and an identical rewrite is
  digested but not written.
- **Evidence order with capture ON:** a refused attempt never emits ``candidate.change``; its refusal's mirrored gate
  outcome precedes the attempt's ``obligations.snapshot`` and ``attempt.closed`` (FAILED); the ``authority.snapshot`` of
  an infeasible edit capability is emitted before its typed stop.

The structure (``CandidateTransaction`` and its per-file proposals) does not exist before the move; the structure pins
are added with it (see the second half of this file).
"""
import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import developer_double

from kriya.config import AppConfig
from kriya.core.attempt_evidence import scope
from kriya.core.kernel import Kernel
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.file_integrity import raw_digest
from kriya.workflow.file_resolution import IncompleteGenerationError
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.state import GenerationState

A = "pkg/a.py"
B = "pkg/b.py"
C = "pkg/c.py"
A_SRC = "def a():\n    return 1\n"
B_SRC = "def b():\n    return 2\n"
A_EDIT = {"search": "    return 1\n", "replace": "    return 10\n"}
B_EDIT = {"search": "    return 2\n", "replace": "    return 20\n"}
FABRICATED = {"search": "    return 999\n", "replace": "    return 0\n"}
INVALID = {"content": None, "protocol_error": "the answer is not in the edit protocol",
           "protocol_reason_code": "INVALID_EDIT_PROTOCOL"}


class _Writer:
    """The open evidence store as the scope sees it: capture on (every emit is recorded by the test's recorder)."""

    capture = "full"

    def sync(self) -> None:
        return None


class _Run:
    """One real ``run_attempt`` over a workspace and a separate worktree (the sandbox) holding the same files."""

    def __init__(self, tmp_path: Path, files: List[Dict[str, Any]], *, architect: Optional[List[str]] = None,
                 **ctx_overrides: Any) -> None:
        self.workspace, self.worktree = tmp_path / "ws", tmp_path / "wt"
        for root in (self.workspace, self.worktree):
            for path, text in ((A, A_SRC), (B, B_SRC)):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text, encoding="utf-8")
        self.files = files
        self.state = GenerationState()
        self.emitted: List[Tuple[str, Dict[str, Any], Dict[str, Optional[str]]]] = []
        self.failure: Any = None
        self.error: Optional[BaseException] = None
        from kriya.workflow.migration import resolve_migration_resolution

        architect = architect if architect is not None else [A, B]
        developer = developer_double()
        developer.run_generation = AsyncMock(return_value=files)
        defaults: Dict[str, Any] = dict(
            goal="Return the larger constants", plan="Step 1: change the returns", design="", workspace_path=str(
                self.workspace), worktree_path=str(self.worktree), architect_files=architect, resume_state=None,
            run_id="e10-candidate-transaction", skills_prompt="", learned_rag_context="", matched_files=[],
            related_files=[], ecosystem_invariant_block="", resource_lifecycle_block="",
            verification_contract_block="", recovery_contract_block="", required_files_prompt_block="",
            required_dependencies_prompt_block="", expected_files_upfront=list(architect),
            architect_basename_to_path={os.path.basename(p): p for p in architect}, chain=[], targeted_max_retries=3,
            stream_callback=None, approval_callback=None, active_skills=[], active_skill_rules_snapshot={},
            developer=developer, run_verifier=AsyncMock(), spec_compliance=AsyncMock(), skill_engine=MagicMock(),
            kernel=Kernel(config=AppConfig()), max_retries=4, web_lookup_query_callback=None,
            approve_web_lookup=AsyncMock(return_value=False), obligation_ledger=ObligationLedger(),
            migration_resolution=resolve_migration_resolution("Return the larger constants", str(self.workspace)),
        )
        defaults.update(ctx_overrides)
        self.ctx = attempt.AttemptContext(**defaults)

    def disk(self) -> Dict[str, Optional[str]]:
        """The worktree's bytes of every path this harness knows, as digests (None = absent)."""
        out = {}
        for path in (A, B, C):
            full = self.worktree / path
            out[path] = raw_digest(full.read_bytes()) if full.is_file() else None
        return out

    def run(self) -> "_Run":
        def emit(kind, payload, *, content=None, provenance=None):  # noqa: ARG001 - the recorder ignores provenance
            self.emitted.append((kind, dict(payload), self.disk()))

        with patch.object(scope, "_active_writer", return_value=_Writer()), patch.object(scope, "emit", emit):
            try:
                asyncio.run(attempt.run_attempt(self.state, self.ctx))
            except QualityGateFailure as error:
                self.failure, self.error = error.failure, error
            except (PolicyDeniedError, IncompleteGenerationError) as error:
                self.error = error
        return self

    def kinds(self) -> List[str]:
        return [kind for kind, _payload, _disk in self.emitted]

    def records(self, kind: str) -> List[Tuple[Dict[str, Any], Dict[str, Optional[str]]]]:
        return [(payload, disk) for k, payload, disk in self.emitted if k == kind]


BASELINE = {A: raw_digest(A_SRC.encode()), B: raw_digest(B_SRC.encode()), C: None}


def _edit(path: str, *edits: Dict[str, str], **extra: Any) -> Dict[str, Any]:
    return {"filepath": path, "content": None, "edits": [dict(edit) for edit in edits], **extra}


def _tail(run: _Run) -> List[Tuple[str, Any]]:
    """The last three records: the refusal's mirrored gate outcome, the obligations snapshot, the attempt close."""
    out = []
    for kind, payload, _disk in run.emitted[-3:]:
        detail = (payload.get("type"), payload.get("success")) if kind == "mirror.gate_outcome" else (
            payload.get("outcome") if kind == "attempt.closed" else None)
        out.append((kind, detail))
    return out


# ------------------------------------------------------------------ operation-contract refusal (before any write)

def test_a_valid_sibling_before_an_invalid_response_is_refused_with_it(tmp_path):
    """The per-file operation contract: file 1 (a NEW file, full content) passes, file 2 is not in the protocol. The
    whole attempt is refused with file 2's typed reason; nothing is staged; file 1's content is already recorded as
    carry-forward content (P9-R1 Case A: recorded before the batch outcome is known) and its accepted whole-file edit
    cleared its edit-protocol failure; the staging bookkeeping (originals, files_written) is untouched."""
    run = _Run(tmp_path, [{"filepath": C, "content": "C = 3\n"}, {"filepath": B, **INVALID}], architect=[C, B])
    run.state.files_written = ["sentinel.py"]  # reset only by the staging loop, which a contract refusal never reaches
    run.state.edit_failure_capability = {C: ("anchored_edit", "d1", None), B: ("anchored_edit", "d2", None)}
    run.run()
    assert run.failure is not None and run.failure.type == "operation_contract"
    assert run.failure.likely_files == [B]
    assert run.failure.diagnostics == {"reason_code": "INVALID_EDIT_PROTOCOL"}
    assert run.failure.message.startswith(f"OPERATION CONTRACT FAILURE in {B}: malformed repair response: ")
    assert run.disk() == BASELINE
    assert run.state.files_written == ["sentinel.py"]
    assert run.state.candidate_digests == {}
    assert run.state.last_candidate_contents == {C: "C = 3\n"}
    assert set(run.state.edit_failure_capability) == {B}   # C's accepted whole-file edit cleared its failure
    assert run.state.all_original_raw == {}                # originals are captured only after the contract loop
    assert run.state.gate_outcomes[-1]["type"] == "operation_contract"
    assert "candidate.change" not in run.kinds()
    assert _tail(run) == [("mirror.gate_outcome", ("operation_contract", False)), ("obligations.snapshot", None),
                          ("attempt.closed", "FAILED")]
    assert all(disk == BASELINE for _kind, _payload, disk in run.emitted)


def test_an_invalid_first_response_refuses_before_its_valid_sibling_is_evaluated(tmp_path):
    """Order matters only for what is evaluated: file 1 invalid -> file 2 (valid) is never looked at."""
    run = _Run(tmp_path, [{"filepath": B, **INVALID}, {"filepath": C, "content": "C = 3\n"}], architect=[B, C])
    run.run()
    assert run.failure is not None and (run.failure.type, run.failure.likely_files) == ("operation_contract", [B])
    assert run.state.last_candidate_contents == {}
    assert run.disk() == BASELINE and run.state.candidate_digests == {}


# --------------------------------------------------------------------- staging refusal (the per-file write loop)

def test_a_valid_anchored_edit_before_a_failing_one_is_never_staged(tmp_path):
    """The staging loop: file 1's edit applies (in memory), file 2's anchor is not in its file. The whole attempt is
    refused (``anchored_edit``, file 2); nothing reaches the worktree. Left behind: file 1's anchor-failure count reset
    to 0 and its diagnosis veto cleared, file 2's count incremented, ``files_written`` reset, both originals captured."""
    run = _Run(tmp_path, [_edit(A, A_EDIT), _edit(B, FABRICATED)])
    run.state.files_written = ["sentinel.py"]
    run.state.budgets.diagnosis_mismatch_veto_counts = {A: 1, B: 1}
    run.run()
    assert run.failure is not None and run.failure.type == "anchored_edit"
    assert run.failure.likely_files == [B]
    assert run.failure.message.startswith(f"ANCHORED EDIT FAILURE in {B}: ")
    assert run.failure.attempted_edits == [FABRICATED]
    assert run.failure.failed_content == {B: B_SRC}
    assert run.disk() == BASELINE
    assert run.state.files_written == []
    assert run.state.candidate_digests == {}
    assert run.state.budgets.anchor_failure_counts == {A: 0, B: 1}
    assert run.state.budgets.diagnosis_mismatch_veto_counts == {B: 1}
    assert set(run.state.all_original_raw) == {A, B}
    assert run.state.all_original_raw[A] == A_SRC.encode()
    assert "candidate.change" not in run.kinds()
    assert _tail(run) == [("mirror.gate_outcome", ("anchored_edit", False)), ("obligations.snapshot", None),
                          ("attempt.closed", "FAILED")]
    assert all(disk == BASELINE for _kind, _payload, disk in run.emitted)


def test_a_failing_first_edit_refuses_before_its_valid_sibling_is_applied(tmp_path):
    run = _Run(tmp_path, [_edit(B, FABRICATED), _edit(A, A_EDIT)])
    run.run()
    assert run.failure is not None and (run.failure.type, run.failure.likely_files) == ("anchored_edit", [B])
    assert run.state.budgets.anchor_failure_counts == {B: 1}   # A was never reached
    assert run.disk() == BASELINE and run.state.candidate_digests == {}


def test_a_target_outside_the_write_scope_refuses_the_whole_batch(tmp_path):
    """MA9's atomic-rejection invariant in the staging loop: an unauthorized target raises the writer's own denial
    before anything is written, although an authorized sibling came first."""
    run = _Run(tmp_path, [_edit(A, A_EDIT), _edit(B, B_EDIT)], write_scope_mode=WriteScopeMode.ALLOWLIST,
               allowed_write_relpaths=[A])
    run.run()
    assert isinstance(run.error, PolicyDeniedError)
    assert run.error.result.reason_code == "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"
    assert run.error.request.target == os.path.join(str(run.worktree), B)
    assert run.state.rejected_generation_targets == [B]
    assert run.disk() == BASELINE and run.state.candidate_digests == {}


def test_a_refusal_at_the_guarded_commit_stages_nothing(tmp_path):
    """The batch commit is the last check: a protected target passes every per-file check (the write-scope pre-check
    is the ALLOWLIST's) and is refused by the writer before its first write; no digest is recorded, nothing is written,
    and ``files_written`` stays reset."""
    run = _Run(tmp_path, [_edit(A, A_EDIT), _edit(B, B_EDIT)], protected_relpath=B)
    run.state.files_written = ["sentinel.py"]
    run.run()
    assert isinstance(run.error, PolicyDeniedError)
    assert run.error.request.target == os.path.join(str(run.worktree), B)
    assert run.disk() == BASELINE
    assert run.state.candidate_digests == {} and run.state.files_written == []
    assert run.state.budgets.anchor_failure_counts == {A: 0, B: 0}   # both edits were applied in memory first
    assert "candidate.change" not in run.kinds()


# ------------------------------------------------------------------------------ staged: the bytes identity

def test_the_staged_candidate_digests_are_the_worktree_bytes(tmp_path):
    """A staged attempt: an anchored edit, a byte-identical whole-file rewrite and a new file. The commit happens before
    the tree binding emits ``candidate.change``: every record's ``after_digest`` is the digest of the bytes in the
    worktree at the moment it is emitted, which is ``candidate_digests``; the identical rewrite is digested (unchanged)
    but not written. The completeness check (a planned file never written) stops the attempt right after the binding,
    before any executing gate."""
    files = [_edit(A, A_EDIT), {"filepath": B, "content": B_SRC}, {"filepath": C, "content": "C = 3\n"}]
    run = _Run(tmp_path, files, architect=[A, B, C, "pkg/missing.py"])
    run.run()
    assert isinstance(run.error, IncompleteGenerationError)
    on_disk = run.disk()
    assert on_disk[A] == raw_digest(b"def a():\n    return 10\n") and on_disk[B] == BASELINE[B]
    assert on_disk[C] == raw_digest(b"C = 3\n")
    assert run.state.candidate_digests == on_disk
    assert run.state.files_written == [A, C]
    assert run.state.identical_rewrites == {B}
    changes = run.records("candidate.change")
    assert [payload["path"] for payload, _disk in changes] == [A, B, C]
    for payload, disk in changes:
        assert payload["after_digest"] == disk[payload["path"]] == on_disk[payload["path"]]
        assert payload["decision"] == "STAGED"
    assert [payload["unchanged"] for payload, _disk in changes] == [False, True, False]
    assert [payload["created"] for payload, _disk in changes] == [False, False, True]
    first_change = run.kinds().index("candidate.change")
    assert all(disk == BASELINE for _kind, _payload, disk in run.emitted[:first_change])


# ------------------------------------------------------------------- the authority snapshot before a typed stop

def test_the_authority_snapshot_is_emitted_before_an_infeasible_capability_stops(tmp_path):
    """``_decide_edit_capabilities``: the ``authority.snapshot`` (what the invocation was authorized to change) is
    recorded BEFORE the CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE stop, so a refused invocation still has its authority on
    record; the capability event precedes it."""
    from test_prd016_adaptive_budget import _attempt_ctx, _cfg

    from kriya.workflow import context_budget as budget
    from kriya.workflow.edit_capability import CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE

    source = "def calc(a, b):\n    return a + b\n"
    (tmp_path / "calc.py").write_text(source)
    cfg = _cfg()
    cfg.llm.inference_runtime = None
    ctx = _attempt_ctx(tmp_path, cfg, developer=None)
    shown = f"=== File: calc.py ===\n{source}"
    kwargs = {"known_target_files": ["calc.py"], "existing_code_context": "HEAD\n",
              "optional_sections": (budget.OptionalSection("planned_source", shown, lambda _b: ""),)}
    state = GenerationState()
    state.attempt_number = 1
    emitted: List[Tuple[str, Dict[str, Any]]] = []
    with patch.object(scope, "_active_writer", return_value=_Writer()), \
            patch.object(scope, "emit", lambda kind, payload, **_kw: emitted.append((kind, dict(payload)))), \
            pytest.raises(QualityGateFailure) as stopped:
        attempt._decide_edit_capabilities(state, ctx, kwargs)
    assert stopped.value.failure.diagnostics["reason_code"] == CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE
    kinds = [kind for kind, _payload in emitted]
    assert kinds == ["mirror.event", "authority.snapshot"]
    assert emitted[0][1]["kind"] == "context.edit_capability"
    snapshot = emitted[1][1]
    assert [target["path"] for target in snapshot["targets"]] == ["calc.py"]
    assert snapshot["targets"][0]["whole_file_authority"] is False
    assert snapshot["known_target_files"] == ["calc.py"]
