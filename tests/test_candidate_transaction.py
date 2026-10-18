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
from kriya.workflow.edit_safety import content_revision
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
                 extra: Optional[Dict[str, bytes]] = None, workspace_only: Tuple[str, ...] = (),
                 **ctx_overrides: Any) -> None:
        self.workspace, self.worktree = tmp_path / "ws", tmp_path / "wt"
        for root in (self.workspace, self.worktree):
            for path, data in [(A, A_SRC.encode()), (B, B_SRC.encode()), *(extra or {}).items()]:
                if root == self.worktree and path in workspace_only:
                    continue
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(data)
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


# ---------------------------------------------------------------------------- unit pins of the staging helpers
# Added after the pre-move mutant pass over the to-be-moved code (6742f29): each pins a decision a surviving mutant
# changed (named in the docstring) through the name attempt.py keeps.

def _full_item(path: str, content: str, **overrides: Any) -> Any:
    from kriya.workflow.context_package import make_context_item

    fields: Dict[str, Any] = dict(tier="full", is_exact=True, revision=content_revision(content))
    fields.update(overrides)
    return make_context_item(path=path, content=content, reason="known_target_full_source",
                             source_type="named_in_request", trust_level="repository", **fields)


def _restoring(state: GenerationState) -> None:
    from kriya.workflow.state import APIContractRecovery

    state.api_contract_recovery = APIContractRecovery.detected(
        [{"owner": A, "removed_signature": "a()"}], [], {A: "API_OWNER"})
    state.api_contract_recovery.begin_restoration()


def test_an_original_is_recorded_once_and_keeps_an_earlier_display_text(tmp_path):
    """``_capture_original`` (mutants c01/c02): the raw bytes once per run, the display text only when none is known."""
    run = _Run(tmp_path, [])
    run.state.all_original_contents[A] = "earlier display text"
    attempt._capture_original(run.state, run.ctx, A)
    assert run.state.all_original_raw[A] == A_SRC.encode()
    assert run.state.all_original_contents[A] == "earlier display text"
    (run.workspace / A).write_text("changed later\n", encoding="utf-8")
    attempt._capture_original(run.state, run.ctx, A)
    assert run.state.all_original_raw[A] == A_SRC.encode()
    attempt._capture_original(run.state, run.ctx, "pkg/absent.py")
    assert run.state.all_original_raw["pkg/absent.py"] is None and run.state.all_original_contents["pkg/absent.py"] == ""


def test_whole_file_authority_needs_full_exact_current_whole_file_source(tmp_path):
    """``_has_authoritative_full_source`` (mutants c07/c08/c09): a full, exact, whole-file item of the current revision
    authorizes; a skeleton, a member slice, a stale revision or an inexact item does not; an item without a revision
    predates revisions and counts as current."""
    run = _Run(tmp_path, [])
    items = run.state.known_target_context_items
    for overrides, authoritative in (({}, True), ({"tier": "skeleton"}, False), ({"member_id": "a"}, False),
                                     ({"revision": "stale"}, False), ({"is_exact": False}, False),
                                     ({"revision": ""}, True)):
        items[A] = _full_item(A, A_SRC, **overrides)
        assert attempt._has_authoritative_full_source(A, run.ctx, run.state) is authoritative, overrides


def test_the_deterministic_restoration_phase_keeps_whole_file_authority(tmp_path):
    """RESTORE_PUBLIC_CONTRACT (mutants c04/c12): without authoritative source a whole-file request is downgraded
    mandatorily and a whole-file response refused - except in the deterministic restoration phase, at request and at
    response time alike."""
    from kriya.workflow.operations import CodeOperation

    run = _Run(tmp_path, [])
    full = CodeOperation.REPAIR_WITH_FULL_FILE
    assert attempt._completeness_gated_operation(A, full, file_exists=True, ctx=run.ctx, state=run.state) == (
        CodeOperation.REPAIR_WITH_PATCH, True)
    assert attempt._validate_actual_mutation_authority(A, full, file_exists=True, ctx=run.ctx, state=run.state)
    _restoring(run.state)
    assert attempt._completeness_gated_operation(A, full, file_exists=True, ctx=run.ctx, state=run.state) == (full, False)
    assert attempt._validate_actual_mutation_authority(A, full, file_exists=True, ctx=run.ctx, state=run.state) is None


def test_a_capability_grants_whole_file_only_for_its_own_attempt_and_revision(tmp_path):
    """``_validate_actual_mutation_authority`` with a capability (mutants c11/c41): the capability decided for THIS
    attempt and THIS revision decides; one of another revision (the file changed since) or without whole-file authority
    refuses; one of an earlier attempt is not current (the authoritative-source check decides: none here)."""
    from types import SimpleNamespace

    from kriya.workflow.operations import CodeOperation

    run = _Run(tmp_path, [])
    state, full = run.state, CodeOperation.REPAIR_WITH_FULL_FILE
    state.attempt_number = state.edit_capabilities_attempt = 2
    current = content_revision(A_SRC)
    for capability, authorized in ((SimpleNamespace(full_file=True, revision=current), True),
                                   (SimpleNamespace(full_file=True, revision="another"), False),
                                   (SimpleNamespace(full_file=False, revision=current), False)):
        state.edit_capabilities = {A: capability}
        verdict = attempt._validate_actual_mutation_authority(A, full, file_exists=True, ctx=run.ctx, state=state)
        assert (verdict is None) is authorized, capability
    state.edit_capabilities = {A: SimpleNamespace(full_file=True, revision=current)}
    state.edit_capabilities_attempt = 1
    assert attempt._current_edit_capability(state, A) is None
    assert attempt._validate_actual_mutation_authority(A, full, file_exists=True, ctx=run.ctx, state=state)


def test_one_anchor_failure_escalates_the_next_request_to_whole_file_where_authorized(tmp_path):
    """``_operation_map`` (mutant c13): after ONE anchored-edit failure an existing file is requested whole - and the
    D1 gate still downgrades it where no authoritative source is on record."""
    from kriya.workflow.operations import CodeOperation

    run = _Run(tmp_path, [])
    run.state.known_target_context_items[A] = _full_item(A, A_SRC)
    patch_op, full = CodeOperation.REPAIR_WITH_PATCH, CodeOperation.REPAIR_WITH_FULL_FILE
    assert attempt._operation_map(run.ctx, [A, B], patch_op, run.state) == {A: patch_op, B: patch_op}
    run.state.budgets.anchor_failure_counts = {A: 1, B: 1}
    assert attempt._operation_map(run.ctx, [A, B], patch_op, run.state) == {A: full, B: patch_op}


def test_edit_loci_are_this_files_failure_lines_and_every_rejected_anchor(tmp_path):
    """``_edit_capability_loci`` / ``_remember_anchor_loci`` (mutants c15/c19): only the failure locations of THIS
    file are loci; a rejected SEARCH block's real lines join the loci already kept (never replace them)."""
    from kriya.workflow.edit_capability import locate_search_text
    from kriya.workflow.failure import Failure, FileLocation

    run = _Run(tmp_path, [])
    run.state.last_failure = Failure(type="compile", message="x", raw_output="x", attempt=1, file_locations=[
        FileLocation(filepath=A, line=2), FileLocation(filepath=B, line=1)])
    assert attempt._edit_capability_loci(run.state, run.ctx, A, A_SRC.splitlines()) == [2]
    run.state.edit_anchor_loci[A] = [7]
    attempt._remember_anchor_loci(run.state, A, [A_EDIT], A_SRC)
    located = locate_search_text(A_SRC.splitlines(), A_EDIT["search"])
    assert located and run.state.edit_anchor_loci[A] == sorted({7, *located})


def test_the_requested_operation_falls_back_to_the_invocations_default():
    """``_requested_operation`` (mutant c40)."""
    from kriya.workflow.operations import CodeOperation

    kwargs = {"operation_by_file": {A: CodeOperation.REPAIR_WITH_FULL_FILE},
              "default_operation": CodeOperation.REPAIR_WITH_PATCH}
    assert attempt._requested_operation(kwargs, A) == CodeOperation.REPAIR_WITH_FULL_FILE.value
    assert attempt._requested_operation(kwargs, B) == CodeOperation.REPAIR_WITH_PATCH.value


BOX_PATH = "src/main/java/p/Box.java"
BOX_SRC = ("package p;\n\npublic class Box {\n    private int size;\n\n    public int size() {\n        return size;\n"
           "    }\n}\n")
BOX_GOAL = "Add `public int clamp(int v)` to Box."


def test_the_structural_insertion_locus_reads_the_grounded_members_and_the_users_goal():
    """``_structural_insertion_locus`` (mutants c38/c39): a grounded member anchors the insertion right after it; the
    locus is decided from the user's grounding goal (the unit goal here names no new member)."""
    from types import SimpleNamespace

    state = GenerationState()
    ctx = SimpleNamespace(goal="Fix the box", grounding_goal=BOX_GOAL)
    assert attempt._structural_insertion_locus(state, ctx, BOX_PATH, BOX_SRC).tier == "owner_closing_delimiter"
    state.known_target_context_items[BOX_PATH] = _full_item(BOX_PATH, BOX_SRC, member_id="Box.size")
    assert attempt._structural_insertion_locus(state, ctx, BOX_PATH, BOX_SRC).tier == "after_grounded_member"
    assert attempt._structural_insertion_locus(state, SimpleNamespace(goal="Fix the box", grounding_goal=None),
                                               BOX_PATH, BOX_SRC) is None


def _capability_ctx(tmp_path):
    from test_prd016_adaptive_budget import _attempt_ctx, _cfg

    source = "def calc(a, b):\n    return a + b\n"
    (tmp_path / "calc.py").write_text(source)
    cfg = _cfg()
    cfg.llm.inference_runtime = None
    return source, _attempt_ctx(tmp_path, cfg, developer=None)


def test_a_shown_item_of_another_revision_never_makes_source_shown(tmp_path):
    """``_decide_edit_capabilities`` (mutant c24): an exact item recorded for a since-changed revision is not shown
    source: with nothing else shown the invocation is infeasible; the same item of the current revision is shown."""
    from kriya.workflow.edit_capability import CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE

    source, ctx = _capability_ctx(tmp_path)
    for revision, feasible in (("stale-revision", False), (content_revision(source), True)):
        state = GenerationState()
        state.attempt_number = 1
        state.known_target_context_items["calc.py"] = _full_item("calc.py", source, tier="member_exact",
                                                                 member_id="calc", revision=revision)
        kwargs = {"known_target_files": ["calc.py"], "existing_code_context": "HEAD\n"}
        if feasible:
            assert attempt._decide_edit_capabilities(state, ctx, kwargs)["calc.py"].feasible
        else:
            with pytest.raises(QualityGateFailure) as stopped:
                attempt._decide_edit_capabilities(state, ctx, kwargs)
            assert stopped.value.failure.diagnostics["reason_code"] == CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE


def test_the_edit_capabilities_of_an_earlier_attempt_are_dropped(tmp_path):
    """``_decide_edit_capabilities`` (mutant c30): a new attempt's invocation starts from no capabilities."""
    source, ctx = _capability_ctx(tmp_path)
    state = GenerationState()
    state.attempt_number = 2
    state.edit_capabilities, state.edit_capabilities_attempt = {"old.py": object()}, 1
    kwargs = {"known_target_files": ["calc.py"], "existing_code_context": f"HEAD\n=== File: calc.py ===\n{source}"}
    attempt._decide_edit_capabilities(state, ctx, kwargs)
    assert set(state.edit_capabilities) == {"calc.py"} and state.edit_capabilities_attempt == 2


# --------------------------------------------------------------------- staging-loop pins (pre-move mutant pass)

def test_an_anchored_edit_on_a_file_the_engine_cannot_represent_is_a_deterministic_stop(tmp_path):
    """The staging loop's file-integrity stop (mutant r14): a non-UTF-8 file is never edited; the typed
    ``file_integrity_unsupported`` stop, not an anchored-edit retry."""
    latin = "pkg/latin.py"
    run = _Run(tmp_path, [_edit(latin, {"search": "x = 1\n", "replace": "x = 2\n"})], architect=[latin],
               extra={latin: "s = 'caf\xe9'\nx = 1\n".encode("latin-1")}).run()
    assert run.failure is not None and run.failure.type == "file_integrity_unsupported"
    assert (run.failure.source, run.failure.authority, run.failure.likely_files) == ("orchestrator", "deterministic", [latin])
    assert run.state.budgets.anchor_failure_counts == {}


def test_an_accepted_anchored_edit_clears_its_files_edit_protocol_failure(tmp_path):
    """Mutant r21: the remembered edit-protocol failure of a file stands until an edit to it is accepted."""
    run = _Run(tmp_path, [_edit(A, A_EDIT)], architect=[A, "pkg/missing.py"])
    run.state.edit_failure_capability = {A: ("anchored_edit", "d1", None), B: ("anchored_edit", "d2", None)}
    run.run()
    assert isinstance(run.error, IncompleteGenerationError)
    assert set(run.state.edit_failure_capability) == {B}


@pytest.mark.parametrize("shape", ["edit", "content"])
def test_explanatory_prose_added_by_the_candidate_is_refused(tmp_path, shape):
    """The prose pre-flight check on both staging branches (mutants r28/r42)."""
    prose = A_SRC.replace("    return 1\n", "    return 10\nThe fix is to return ten.\n")
    if shape == "edit":
        files = [_edit(A, {"search": "    return 1\n", "replace": "    return 10\nThe fix is to return ten.\n"})]
        path = A
    else:
        path, files = C, [{"filepath": C, "content": prose}]
    run = _Run(tmp_path, files, architect=[path]).run()
    assert run.failure is not None and (run.failure.type, run.failure.likely_files) == ("prose_contamination", [path])
    assert run.disk() == BASELINE


JAVA = "src/P.java"
JAVA_SRC = ("class P {\n    void f() {\n        try {\n            g();\n        } catch (Exception e) {\n"
            "            throw new RuntimeException(e);\n        }\n    }\n    void g() {}\n}\n")


@pytest.mark.parametrize("shape", ["edit", "content"])
def test_newly_masked_exceptions_are_refused(tmp_path, shape):
    """The exception-masking pre-flight check on both staging branches (mutants r29/r43), through the name tests
    patch on attempt."""
    masked = JAVA_SRC.replace("Exception e", "Throwable e")
    if shape == "edit":
        files = [_edit(JAVA, {"search": "        } catch (Exception e) {\n", "replace": "        } catch (Throwable e) {\n"})]
        path = JAVA
        extra = {JAVA: JAVA_SRC.encode()}
    else:
        path, files, extra = "src/Q.java", [{"filepath": "src/Q.java", "content": masked.replace("class P", "class Q")}], {}
    run = _Run(tmp_path, files, architect=[path], extra=extra).run()
    assert run.failure is not None and (run.failure.type, run.failure.likely_files) == ("unsafe_exception_masking",
                                                                                         [path])


def test_an_anchored_edit_keeps_the_files_line_endings_and_mode(tmp_path):
    """The prepared write carries the edit engine's bytes and the file mode (mutants r31/r32): a CRLF file stays
    CRLF and an executable stays executable."""
    script = "pkg/run.py"
    run = _Run(tmp_path, [_edit(script, {"search": "x = 1\n", "replace": "x = 2\n"})], architect=[script, "pkg/missing.py"],
               extra={script: b"#!/usr/bin/env python3\r\nx = 1\r\n"})
    for root in (run.workspace, run.worktree):
        os.chmod(root / script, 0o755)
    run.run()
    assert isinstance(run.error, IncompleteGenerationError)
    assert (run.worktree / script).read_bytes() == b"#!/usr/bin/env python3\r\nx = 2\r\n"
    assert os.stat(run.worktree / script).st_mode & 0o777 == 0o755
    assert run.state.candidate_digests[script] == raw_digest(b"#!/usr/bin/env python3\r\nx = 2\r\n")


def test_a_whole_file_replacement_based_on_the_workspace_file_is_refused_by_the_guarded_commit(tmp_path):
    """Mutant r34 (TODAY's behaviour, pinned): a whole-file replacement of a file the sandbox lacks but the workspace
    has is based on the WORKSPACE file - outside the writer's root - so the guarded batch commit refuses the batch
    (BatchCommitError) and nothing is written."""
    from kriya.repository.commit import BatchCommitError

    nonl = "pkg/nonl.py"
    run = _Run(tmp_path, [{"filepath": nonl, "content": "X = 2\n"}], architect=[nonl, "pkg/missing.py"],
               extra={nonl: b"X = 1"}, workspace_only=(nonl,))
    run.state.known_target_context_items[nonl] = _full_item(nonl, "X = 1")
    with pytest.raises(BatchCommitError, match="escapes workspace"):
        asyncio.run(attempt.run_attempt(run.state, run.ctx))
    assert not (run.worktree / nonl).exists() and run.state.candidate_digests == {}


def test_a_whole_file_replacement_of_an_unrepresentable_file_is_a_deterministic_stop(tmp_path):
    """Mutant r35: an existing file the engine cannot represent exactly (mixed line endings) is refused before any
    re-encoding: the typed ``file_integrity_unsupported`` stop."""
    mixed = "pkg/mixed.py"
    run = _Run(tmp_path, [{"filepath": mixed, "content": "a = 2\nb = 2\n"}], architect=[],
               extra={mixed: b"a = 1\r\nb = 1\n"})
    run.state.known_target_context_items[mixed] = _full_item(mixed, "a = 1\nb = 1\n", revision="")
    run.run()
    assert run.failure is not None and run.failure.type == "file_integrity_unsupported", run.failure
    assert (run.worktree / mixed).read_bytes() == b"a = 1\r\nb = 1\n"


def test_a_structurally_corrupt_whole_file_is_refused(tmp_path):
    """Mutant r38: the structural pre-flight check of a whole-file (new) candidate."""
    run = _Run(tmp_path, [{"filepath": "src/R.java", "content": "class R {\n    void f() {\n}\n"}],
               architect=["src/R.java"]).run()
    assert run.failure is not None and (run.failure.type, run.failure.likely_files) == ("structural_corruption",
                                                                                         ["src/R.java"])


def test_two_new_files_of_one_batch_declaring_the_same_type_collide(tmp_path):
    """Mutant r40: each accepted new file's types join the batch's type index, so a second new file in the SAME
    response redeclaring one is refused as a duplicate (the index is not only the workspace's)."""
    run = _Run(tmp_path, [{"filepath": "src/x/Dup.java", "content": "class Dup {\n}\n"},
                          {"filepath": "src/y/Dup.java", "content": "class Dup {\n}\n"}],
               architect=["src/x/Dup.java", "src/y/Dup.java"], extra={JAVA: JAVA_SRC.encode()},
               established_files=[JAVA]).run()
    assert run.failure is not None and run.failure.type == "duplicate_type_across_files"
    assert run.failure.likely_files == ["src/y/Dup.java", "src/x/Dup.java"]


def test_a_real_change_clears_an_earlier_identical_rewrite_mark(tmp_path):
    """Mutant r49: a path an earlier attempt rewrote byte-identically is no longer an identical rewrite once this
    attempt really changes it."""
    run = _Run(tmp_path, [_edit(A, A_EDIT)], architect=[A, "pkg/missing.py"])
    run.state.identical_rewrites = {A, B}
    run.run()
    assert isinstance(run.error, IncompleteGenerationError)
    assert run.state.identical_rewrites == {B} and run.state.files_written == [A]


# ------------------------------------------------------------------------ spec-compliance pins (pre-move mutant pass)

def _migration_ledger(*statuses: Any, source: str = "io-gson", target: str = "jackson-databind") -> ObligationLedger:
    from kriya.workflow.obligations import ObligationAuthority, ObligationKind, ObligationRecord

    ledger = ObligationLedger()
    for index, status in enumerate(statuses):
        ledger.record(ObligationRecord(
            id=f"migration.m{index}", kind=ObligationKind.MIGRATION_COMPLETION, status=status,
            authority=ObligationAuthority.DETERMINISTIC, description=f"migration step {index}", source="test",
            revision=1, evidence={"source_identity": source, "target_identity": target}))
    return ledger


def test_the_authoritative_migration_context_needs_every_obligation_satisfied():
    """Mutants s01/s03: the authoritative context and the arbitration precondition exist only when at least one
    migration obligation exists and every one is SATISFIED."""
    from kriya.workflow.obligations import ObligationStatus

    ok, bad = ObligationStatus.SATISFIED, ObligationStatus.VIOLATED
    assert attempt._spec_compliance_authoritative_context(_migration_ledger(ok, bad)) is None
    assert attempt._spec_compliance_authoritative_context(None) is None
    assert attempt._spec_compliance_authoritative_context(_migration_ledger(ok, ok)) == (
        "AUTHORITATIVELY ESTABLISHED (deterministic evidence - do not report these as missing/incomplete):\n"
        "- migration step 0\n- migration step 1\n\nEvaluate only requirements not already covered by the above.")
    assert attempt._migration_obligations_all_satisfied(ObligationLedger()) is False
    assert attempt._migration_obligations_all_satisfied(_migration_ledger(ok, bad)) is False
    assert attempt._migration_obligations_all_satisfied(_migration_ledger(ok)) is True


def test_a_contradicted_claim_names_a_migration_identity_term_of_three_or_more_letters():
    """Mutant s05: the identity's short tokens ("io" of "io-gson") correlate with nothing."""
    from kriya.workflow.obligations import ObligationStatus

    ledger = _migration_ledger(ObligationStatus.SATISFIED)
    kept, contradicted = attempt._spec_requirements_contradicting_authority(
        ["the code still imports gson", "the io layer is incomplete"], ledger)
    assert (kept, contradicted) == (["the io layer is incomplete"], ["the code still imports gson"])


def test_requirement_identifier_tokens_drop_short_words_and_stopwords():
    """Mutant s09."""
    assert attempt._extract_requirement_identifier_tokens("Add an id field to the CustomerDto record") == [
        "Add", "field", "CustomerDto", "record"]


def _stage_plan(completed=()):
    from types import SimpleNamespace

    from kriya.workflow.plan_schema import RequirementOwnershipRelation

    subtasks = [SimpleNamespace(id="s1", description="scaffold the project", acceptance_criteria_ids=["AC-1"],
                                relevant_global_invariant_ids=[]),
                SimpleNamespace(id="s0", description="done earlier", acceptance_criteria_ids=["AC-0"],
                                relevant_global_invariant_ids=[]),
                SimpleNamespace(id="s2", description="the endpoint", acceptance_criteria_ids=["AC-2"],
                                relevant_global_invariant_ids=["INV-1"])]
    plan = SimpleNamespace(
        subtasks=subtasks, acceptance_criteria=[SimpleNamespace(id="AC-1", description="the build passes")],
        global_invariants=[SimpleNamespace(id="INV-1", statement="no new dependency")],
        subtask_by_id=lambda sid: next((st for st in subtasks if st.id == sid), None),
        classify_requirement_ownership=lambda _current, _claimants: RequirementOwnershipRelation.CURRENT)
    return SimpleNamespace(structured_plan=plan, current_subtask_id="s1", completed_subtask_ids=list(completed),
                           goal="the whole goal")


def test_the_stage_scoped_goal_lists_due_requirements_and_only_pending_later_work():
    """Mutants s17/s18: the scoped goal's exact text (with the pending rule), and only requirements of NOT YET
    completed other subtasks are future."""
    scoped, future = attempt._stage_scoped_spec_compliance_goal(_stage_plan(completed=("s0",)))
    assert scoped == ("Current approved subtask: s1\nCurrent implementation obligation: scaffold the project\n"
                      "Acceptance requirements due at this stage:\n- the build passes\nOnly decide whether this current "
                      "stage satisfies the requirements listed above. Requirements assigned to unfinished later "
                      "subtasks are pending, not violated.")
    assert future == ["AC-2", "INV-1"]
    assert attempt._stage_scoped_spec_compliance_goal(_stage_plan())[1] == ["AC-0", "AC-2", "INV-1"]


def _requirements(outcomes: Dict[str, Any], evidence_id: str = "cand-1"):
    from test_prd020_requirement_lineage import _ledger_with

    goal = "Migrate the JSON layer from gson to jackson-databind.\n- Use jackson snake_case naming everywhere\n"
    return _ledger_with(outcomes, goal=goal, evidence_id=evidence_id)


def test_the_recorded_verdicts_carry_the_verifier_and_its_findings():
    """Mutants s21/s22: the lineage event of the original verdicts names the verifier identity per requirement and
    keeps the verifier's findings."""
    from types import SimpleNamespace

    reqs, ledger = _requirements({})
    state = GenerationState()
    state.attempt_number = 1
    result = {"compliant": False, "reasoning": "x", "missing_requirements": ["REQ-2: snake_case naming missing"],
              "verifier": {"model": "judge-model", "runtime_digest": "d1"},
              "requirement_verdicts": [{"id": "REQ-9", "verdict": "missing"}]}
    attempt._record_original_requirement_verdicts(state, SimpleNamespace(requirement_set=reqs, obligation_ledger=ledger),
                                                  result, "fp-1")
    event = state.run_events[-1]
    assert event.kind == "requirement.verdicts" and event.details["evidence_id"] == "fp-1"
    assert {rid: v["verifier"] for rid, v in event.details["verdicts"].items()} == {
        rid: {"model": "judge-model", "runtime_digest": "d1"} for rid in reqs.ids}
    from kriya.workflow.requirements import verifier_result_verdicts

    findings = verifier_result_verdicts(result, reqs)[1]
    assert findings and event.details["findings"] == findings


def test_a_suppressed_claim_downgrades_only_its_own_requirement_to_unverified():
    """Mutants s23/s24: a suppressed 'REQ-n is missing' claim records exactly REQ-n UNVERIFIED with the
    suppression's typed reason (never as the model's VIOLATED claim, never another requirement)."""
    from types import SimpleNamespace

    from kriya.workflow.requirements import (
        CLAIM_CONTRADICTS_STRONGER_AUTHORITY,
        RequirementOutcome,
        requirement_outcomes,
        requirement_verdict_details,
    )

    reqs, ledger = _requirements({})
    state = GenerationState()
    state.attempt_number = 2
    attempt._downgrade_suppressed_requirement_claims(state, SimpleNamespace(requirement_set=reqs, obligation_ledger=ledger),
                                                     ["REQ-2: snake_case naming missing"], "cand-1")
    outcomes = requirement_outcomes(ledger, reqs)
    assert (outcomes["REQ-1"], outcomes["REQ-2"]) == (RequirementOutcome.UNKNOWN, RequirementOutcome.UNVERIFIED)
    details = requirement_verdict_details(ledger, reqs)["REQ-2"]
    assert (details["model_outcome"], details["reason_code"], details["evidence_id"]) == (
        "unverified", CLAIM_CONTRADICTS_STRONGER_AUTHORITY, "cand-1")


def test_the_migration_gate_closes_only_an_unverified_requirement_of_the_same_candidate():
    """Mutants s25/s26: a requirement that is not UNVERIFIED (no verdict yet), or whose verdict judged another
    candidate, is never closed by the migration gate."""
    from types import SimpleNamespace

    from kriya.workflow.obligations import ObligationStatus
    from kriya.workflow.requirements import RequirementOutcome, requirement_closure_id, requirement_outcomes

    def closures(outcome, verdict_candidate, *gate_candidates):
        """(closed?, the number of closure records written) after the gate ran for each candidate."""
        reqs, ledger = _requirements({"REQ-1": outcome} if outcome else {}, evidence_id=verdict_candidate)
        for record in _migration_ledger(ObligationStatus.SATISFIED, source="gson")._history.values():
            ledger.record(record[-1])
        ctx = SimpleNamespace(requirement_set=reqs, obligation_ledger=ledger)
        for candidate in gate_candidates:
            attempt._close_requirements_by_migration_gate(SimpleNamespace(attempt_number=1), ctx, candidate)
        return (requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE,
                len(ledger._history.get(requirement_closure_id("REQ-1"), [])))

    assert closures(RequirementOutcome.UNVERIFIED, "cand-1", "cand-1") == (True, 1)
    assert closures(RequirementOutcome.UNVERIFIED, "cand-1", "cand-2") == (False, 0)   # another candidate's verdict
    assert closures(RequirementOutcome.UNVERIFIED, "cand-1", "cand-1", "cand-1") == (True, 1)  # closed: not again
    assert closures(None, "cand-1", "cand-1") == (False, 0)   # no verdict yet: UNKNOWN, not UNVERIFIED


def test_an_unscoped_spec_goal_needs_a_current_subtask():
    """Mutant s16: without a current subtask id the whole goal is judged, whatever the plan's subtasks."""
    ctx = _stage_plan()
    ctx.current_subtask_id = ""
    ctx.structured_plan.subtasks[0].id = ""
    assert attempt._stage_scoped_spec_compliance_goal(ctx) == ("the whole goal", [])


def test_the_ownership_redirect_restoration_restores_only_changed_tests_and_records_it(tmp_path):
    """Mutants s30/s32: a redirected test is restored only when its sandbox bytes differ from its baseline, and the
    restoration is recorded."""
    from types import SimpleNamespace

    run = _Run(tmp_path, [])
    state = run.state
    state.attempt_number = 3
    state.all_original_contents = {A: A_SRC, B: B_SRC}
    state.all_original_raw = {A: A_SRC.encode(), B: B_SRC.encode()}
    state.ownership_redirect_recovery = {"redirected_tests": [A, B], "abandoned_candidates": [], "owners": ["pkg/o.py"]}
    (run.worktree / B).write_text("def b():\n    return 99\n", encoding="utf-8")
    staged: List[Any] = []
    attempt._stage_ownership_redirect_restoration(state, SimpleNamespace(worktree_path=str(run.worktree),
                                                                         workspace_path=str(run.workspace),
                                                                         structured_plan=None), staged)
    assert [(os.path.relpath(w.target_path, run.worktree), w.content_bytes) for w in staged] == [(B, B_SRC.encode())]
    event = state.run_events[-1]
    assert (event.kind, event.source, event.attempt) == ("ownership.redirect_restored", "attempt.staged_writes", 3)
    assert event.details == {"restored_tests": [B], "removed_candidates": [], "owners": ["pkg/o.py"]}


def test_the_obligations_snapshot_lists_the_unresolved_terminal_obligations_sorted():
    """Mutants e03/e10: the snapshot carries the ledger's revision and digest and, per kind, the sorted ids of the
    unresolved terminal obligations."""
    from types import SimpleNamespace

    from kriya.workflow.obligations import ObligationAuthority, ObligationKind, ObligationRecord, ObligationStatus

    ledger = ObligationLedger()
    for oid in ("z.last", "a.first"):
        ledger.record(ObligationRecord(id=oid, kind=ObligationKind.MIGRATION_COMPLETION, status=ObligationStatus.PENDING,
                                       authority=ObligationAuthority.DETERMINISTIC, description=oid, source="test",
                                       revision=1, terminal_required=True))
    emitted: List[Tuple[str, Dict[str, Any]]] = []
    with patch.object(scope, "_active_writer", return_value=_Writer()), \
            patch.object(scope, "emit", lambda kind, payload, **_kw: emitted.append((kind, dict(payload)))):
        attempt._record_obligations_snapshot(SimpleNamespace(obligation_ledger=ledger))
    revision, digest = ledger.fingerprint()
    assert emitted == [("obligations.snapshot", {"ledger_revision": revision, "ledger_digest": digest,
                                                 "unresolved_terminal": {"migration_completion": ["a.first", "z.last"]}})]


# ======================================================================================================================
# The structure (added with the move, kriya/workflow/candidate_transaction.py): run_attempt's per-file loops run as one
# CandidateTransaction. These pins capture the transaction run_attempt builds (a recording subclass on the owner module,
# no production hook) and assert what it recorded on the same runs as above: one proposal per file in response order,
# the decision equal to TODAY's outcome (refused / staged) and the refusing file.

def _transactions(monkeypatch) -> List[Any]:
    from kriya.workflow import candidate_transaction

    made: List[Any] = []
    real = candidate_transaction.CandidateTransaction

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(candidate_transaction, "CandidateTransaction", Recording)
    return made


def _proposal(path, seq, parse="ok", reason=None, result=None, digest=None):
    from kriya.workflow.candidate_transaction import FileProposal

    return FileProposal(path=path, response_seq=seq, parse=parse, reason_code=reason, edit_result=result,
                        staged_digest=digest)


def test_a_contract_refusal_is_the_transactions_refusal(tmp_path, monkeypatch):
    made = _transactions(monkeypatch)
    run = _Run(tmp_path, [{"filepath": C, "content": "C = 3\n"}, {"filepath": B, **INVALID}], architect=[C, B]).run()
    assert run.failure is not None and len(made) == 1
    transaction = made[0]
    assert transaction.proposals == (_proposal(C, 0), _proposal(B, 1, "invalid", "INVALID_EDIT_PROTOCOL", "refused"))
    assert (transaction.decision, transaction.reason_code) == ("refused", "INVALID_EDIT_PROTOCOL")
    assert transaction.refused == ((B, "INVALID_EDIT_PROTOCOL"),)


def test_a_staging_refusal_is_the_transactions_refusal_and_earlier_files_stay_unstaged(tmp_path, monkeypatch):
    made = _transactions(monkeypatch)
    run = _Run(tmp_path, [_edit(A, A_EDIT), _edit(B, FABRICATED)]).run()
    reason = run.failure.diagnostics["reason_code"]
    transaction = made[0]
    assert transaction.proposals == (_proposal(A, 0), _proposal(B, 1, "ok", reason, "refused"))
    assert (transaction.decision, transaction.reason_code, transaction.refused) == ("refused", reason, ((B, reason),))


def test_a_refusal_by_the_first_file_leaves_the_later_ones_unevaluated(tmp_path, monkeypatch):
    made = _transactions(monkeypatch)
    run = _Run(tmp_path, [_edit(B, FABRICATED), _edit(A, A_EDIT)]).run()
    reason = run.failure.diagnostics["reason_code"]
    assert made[0].proposals == (_proposal(B, 0, "ok", reason, "refused"), _proposal(A, 1))


def test_a_write_scope_refusal_names_the_refused_file(tmp_path, monkeypatch):
    made = _transactions(monkeypatch)
    _Run(tmp_path, [_edit(A, A_EDIT), _edit(B, B_EDIT)], write_scope_mode=WriteScopeMode.ALLOWLIST,
         allowed_write_relpaths=[A]).run()
    transaction = made[0]
    assert transaction.refused == ((B, "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"),)
    assert transaction.proposals[1].edit_result == "refused" and transaction.proposals[0].edit_result is None


def test_a_staged_transaction_records_the_committed_digests(tmp_path, monkeypatch):
    made = _transactions(monkeypatch)
    files = [_edit(A, A_EDIT), {"filepath": B, "content": B_SRC}, {"filepath": C, "content": "C = 3\n"}]
    run = _Run(tmp_path, files, architect=[A, B, C, "pkg/missing.py"]).run()
    on_disk = run.disk()
    transaction = made[0]
    assert transaction.proposals == (_proposal(A, 0, result="staged", digest=on_disk[A]),
                                     _proposal(B, 1, result="identical", digest=on_disk[B]),
                                     _proposal(C, 2, result="staged", digest=on_disk[C]))
    assert (transaction.decision, transaction.reason_code, transaction.refused) == ("staged", None, ())


def test_a_refusal_at_the_guarded_commit_is_the_transactions_refusal(tmp_path, monkeypatch):
    made = _transactions(monkeypatch)
    run = _Run(tmp_path, [_edit(A, A_EDIT), _edit(B, B_EDIT)], protected_relpath=B).run()
    assert isinstance(run.error, PolicyDeniedError)
    transaction = made[0]
    assert transaction.decision == "refused" and transaction.reason_code == run.error.result.reason_code
    assert transaction.refused == ()   # no single file was under evaluation: the whole batch was refused
    assert [p.edit_result for p in transaction.proposals] == ["prepared", "prepared"]
    assert run.disk() == BASELINE and run.state.candidate_digests == {}


def test_the_scenario04_attempts_are_refused_transactions(tmp_path, monkeypatch):
    """The recorded scenario-04 attempts 3 and 4 (the A3 gate's runs): the transaction decision equals TODAY's outcome,
    ``refused``, with the invalid sibling as the refusing file and the valid parser proposal never staged."""
    from integration import test_scenario04_replay as s04

    made = _transactions(monkeypatch)
    previous3 = s04.Failure(
        type="anchored_edit", message=f"ANCHORED EDIT FAILURE in {s04.PARSER}: ANCHOR_NOT_IN_FILE",
        raw_output="ANCHOR_NOT_IN_FILE", file_locations=[s04.FileLocation(filepath=s04.PARSER)],
        likely_files=[s04.PARSER], attempted_edits=[], diagnostics={"reason_code": "ANCHOR_NOT_IN_FILE"}, attempt=2)
    s04._replay_attempt(tmp_path / "a3", {s04.PROTOCOL: 9, s04.PARSER: 10}, 2, previous3)
    previous4 = s04.Failure(
        type="operation_contract", message=s04._logged_failure("targeted, full-set 1/4 + targeted 1/3: "),
        raw_output="INVALID_EDIT_PROTOCOL", file_locations=[s04.FileLocation(filepath=s04.PROTOCOL)],
        likely_files=[s04.PROTOCOL], attempted_edits=[], diagnostics={"reason_code": "INVALID_EDIT_PROTOCOL"}, attempt=3)
    s04._replay_attempt(tmp_path / "a4", {s04.PROTOCOL: 11, s04.PARSER: 12}, 3, previous4)
    third, fourth = made
    assert third.proposals == (_proposal(s04.PROTOCOL, 0, "invalid", "INVALID_EDIT_PROTOCOL", "refused"),
                               _proposal(s04.PARSER, 1))
    assert (third.decision, third.refused) == ("refused", ((s04.PROTOCOL, "INVALID_EDIT_PROTOCOL"),))
    assert fourth.proposals == (_proposal(s04.PROTOCOL, 0, "ok", "INDENTATION_STYLE_MISMATCH", "refused"),
                                _proposal(s04.PARSER, 1))
    assert (fourth.decision, fourth.refused) == ("refused", ((s04.PROTOCOL, "INDENTATION_STYLE_MISMATCH"),))
