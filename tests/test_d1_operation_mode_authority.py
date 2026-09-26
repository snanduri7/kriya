"""D1 operation-mode authority: investigation AND fix (2026-09-18/2026-09-19,
VAL-001 G1-R2 post-mortem through G1-R3 pre-work).

VAL-001 G1-R2 (Kriya run 8cc2018a against Graphify @ 67f99bd0) proved a
full-file candidate identical in shape to one correctly rejected at attempt 2
(operation_contract: "mandatory REPAIR_WITH_PATCH, got repair_with_full_file")
was instead ACCEPTED through candidate gates at attempt 6 (fallback_targeted
mode) and again at attempts 8/9 (full_set mode) - reaching human approval
with an ~88% file-content deletion, correctly rejected by a human but never
even reaching that human's attention with D1's own invariant having actually
fired.

TWO defects, now BOTH CLOSED (kriya/workflow/attempt.py):

D1-A: authority previously followed the REQUESTED operation
(_completeness_gated_operation()'s own `mandatory_patch`, which is
structurally always False for targeted/fallback_targeted modes - their own
base operation is ALREADY patch-shaped, so that function's first-line
early-return fires before the authoritative-source question is ever asked)
rather than the ACTUAL RETURNED mutation shape. Fixed by
`_validate_actual_mutation_authority()` - a new, independent, unconditional
check keyed on `actual_operation` (post-parse), called for every mode, every
attempt, sharing the exact same `_has_authoritative_full_source()`/
`_is_restore_public_contract_phase()` primitives `_completeness_gated_
operation()` itself now uses (extracted, not duplicated) so request-time and
response-time authority can never independently drift on what
"authoritative" means.

D1-B: an unvalidated/rejected candidate could, once written to the sandbox
worktree, later be read back by a retry-package build as "current" content
small enough to fit the budget unelided, and get recorded tier=full/
is_exact=True - falsely bootstrapping whole-file authority for a LATER
attempt from content that was never validated. Proven (this file's own
TestD1StateTransitionReproduction) to be a REAL, reachable propagation
mechanism GIVEN an already-poisoned sandbox - but D1-A's fix makes the
sandbox structurally unpoisonable via the real code path in the first
place: `_validate_actual_mutation_authority()` runs strictly BEFORE the
batch commit to the sandbox (kriya/workflow/attempt.py's own single
AuthorizedFileWriter.commit_batch() call site, confirmed the ONE write path
for every mode), so a candidate lacking authority now never reaches disk at
all - closing D1-A closes D1-B's propagation route as a direct, proven
structural consequence, not a second independent code change, FOR THE
DIRECT WRITE PATH. See TestCriticalPropagation below for the deterministic,
end-to-end proof of both together.

D1-B also had a SECOND, independent propagation route, found while closing
the first: kriya/workflow/attempt.py's own pre-existing P9-R1 completeness-
preservation mechanism (`state.last_candidate_contents`, folded back into a
later, unrelated attempt's own `files` as `_kriya_carried_forward_content`
whenever an active api_contract_recovery/coordinated-repair attempt
narrows the Developer's scope away from a file) recorded EVERY attempt's
own candidate content unconditionally, BEFORE the new authority check ever
ran for that file - so a candidate this attempt correctly rejected
PRE-WRITE could still be cached, then silently resurrected with zero
authority check on a LATER attempt that never asked the Developer for that
file at all. Required authority case #6 ("failed/rejected candidate-
derived full projection, later retry -> REJECT") names exactly this. Fixed
by moving the recording site from an unconditional pre-loop into the
per-file enforcement loop itself, reached only once that file's own actual
mutation shape has cleared authority THIS attempt - never for a file the
loop rejected or never reached. See
TestRejectedCandidateCannotResurrectViaCarryForward below for the
deterministic, end-to-end proof.

This file retains every original investigation test proving the individual
COMPONENTS' own behavior (which is DELIBERATELY UNCHANGED -
`_completeness_gated_operation()`'s own `mandatory_patch` output, and
`validate_operation_result()`'s own permissive patch-to-full-file
transition, both still behave exactly as before; the fix is an ADDITIONAL,
independent check, not a change to either) - their docstrings are updated
to say so precisely, not deleted or weakened, per this task's own explicit
"retain all existing D1 adversarial tests... convert proven-defect
expectations to corrected expectations where appropriate" instruction.

No live model/Ollama calls anywhere in this file. No Graphify production
source is copied here - every fixture is synthetic, sized to make budget
math explicit and reproducible rather than dependent on any specific real
file's byte count.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.attempt import (
    AttemptContext,
    _completeness_gated_operation,
    _record_retry_projection_context_items,
    _validate_actual_mutation_authority,
    run_attempt,
)
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.file_resolution import IncompleteGenerationError
from kriya.workflow.operations import CodeOperation, operation_for_attempt, validate_operation_result
from kriya.workflow.retry_package import build_retry_package
from kriya.workflow.state import APIContractRecovery, GenerationState
from kriya.workflow.workflow import WorkflowEngine

# ---------------------------------------------------------------------------
# Fixtures - large enough that a full 6000+ char reference clearly exceeds a
# small retry budget, small enough that a truncated "candidate" clearly fits
# one, mirroring the real G1-R2 shape (6318-line file vs ~744-line candidate)
# at a scale that doesn't depend on any specific real file's byte count.
# ---------------------------------------------------------------------------

def _large_baseline_content() -> str:
    body = "\n".join(f"def helper_{i}(x):\n    return x + {i}\n" for i in range(400))
    return f'"""Large synthetic module - {len(body)} chars of body."""\n{body}'


def _truncated_candidate_content() -> str:
    body = "\n".join(f"def helper_{i}(x):\n    return x + {i}\n" for i in range(10))
    return f'"""Truncated synthetic module."""\n{body}'


def _minimal_attempt_ctx(tmp_path, **overrides) -> AttemptContext:
    from kriya.workflow.migration import resolve_migration_resolution
    defaults = dict(
        goal="Fix a narrow bug in an existing file",
        plan="Step 1: fix it",
        design="Design: minimal change",
        workspace_path=str(tmp_path),
        worktree_path=str(tmp_path),
        architect_files=["target.py"],
        resume_state=None,
        run_id="test-run-id",
        skills_prompt="",
        learned_rag_context="",
        matched_files=[],
        related_files=[],
        ecosystem_invariant_block="",
        resource_lifecycle_block="",
        verification_contract_block="",
        recovery_contract_block="",
        required_files_prompt_block="",
        required_dependencies_prompt_block="",
        expected_files_upfront=["target.py"],
        architect_basename_to_path={"target.py": "target.py"},
        chain=[],
        targeted_max_retries=3,
        stream_callback=None,
        approval_callback=None,
        active_skills=[],
        active_skill_rules_snapshot={},
        developer=AsyncMock(),
        run_verifier=AsyncMock(),
        spec_compliance=AsyncMock(),
        skill_engine=MagicMock(),
        kernel=Kernel(config=AppConfig()),
        max_retries=4,
        web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
    )
    defaults.update(overrides)
    defaults["migration_resolution"] = resolve_migration_resolution(
        defaults.get("grounding_goal") or defaults["goal"], defaults["workspace_path"],
    )
    return AttemptContext(**defaults)


def _write(tmp_path, filepath: str, content: str) -> str:
    full = tmp_path / filepath
    full.write_text(content)
    return content


# ---------------------------------------------------------------------------
# Task 5 - the 8 named adversarial scenarios, current actual behavior
# ---------------------------------------------------------------------------

class TestAdversarial1SkeletonToFullFile:
    """1. skeleton -> full-file response: correctly gated."""

    def test_skeleton_context_rejects_full_file_mandatorily(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_0(x): ...  # elided", reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="skeleton", is_exact=False, omitted_regions=True,
            revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True


class TestAdversarial2MemberExactToFullFile:
    """2. member_exact -> full-file response: correctly gated (a member
    slice is real, exact evidence for ITS member, never authority to
    rewrite the surrounding file)."""

    def test_member_exact_context_rejects_full_file_mandatorily(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_3(x):\n    return x + 3\n",
            reason="known_target", source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="helper_3", omitted_regions=False,
            revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True


class TestAdversarial3CompleteFullExactToFullFile:
    """3. complete full exact -> full-file response: the ONE legitimate
    authorization case - genuinely correct current-source evidence."""

    def test_full_exact_current_context_authorizes_full_file(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content=content, reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, omitted_regions=False,
            revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE
        assert mandatory is False


class TestAdversarial4ReferenceProjectionIncomplete:
    """4. reference projection derived from incomplete source -> full-file
    response: correctly gated for a FULL_SET-mode attempt (base operation
    already full-file-shaped, so _completeness_gated_operation is
    meaningfully consulted)."""

    def test_large_file_retry_projection_is_incomplete_and_gates_full_file(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        assert len(content) > 6000, "fixture must genuinely exceed a small retry budget"
        failure = Failure(
            type="anchored_edit", message="x", raw_output="x",
            file_locations=[FileLocation(filepath="target.py")],
            likely_files=["target.py"], attempted_edits=[], attempt=2,
        )
        pkg = build_retry_package(
            failure=failure, worktree_path=str(tmp_path),
            all_files=["target.py"], target_files=["target.py"],
            source_context=None, max_chars=2000,  # deliberately tight
        )
        assert pkg.target_projections[0].omitted_regions is True, (
            "fixture assumption violated - the large baseline must NOT fit a 2000-char budget"
        )

        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        _record_retry_projection_context_items(state, pkg)
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True


class TestAdversarial5RetryAfterAnchorFailurePatchModeBypass:
    """5. retry after anchor failure -> full-file response.

    *** D1-A: FIXED, closed by _validate_actual_mutation_authority() ***

    operation_for_attempt("targeted"/"fallback_targeted", ...) ALWAYS
    returns CodeOperation.REPAIR_WITH_PATCH as the attempt's own BASE
    operation (kriya/workflow/operations.py:81-82) - unconditionally, with
    NO reference to context completeness at all. _completeness_gated_
    operation()'s own FIRST check therefore ALWAYS short-circuits for
    these two modes, so `mandatory` (a REQUEST-time signal) is
    structurally False regardless of whether the context shown was ever
    complete/exact/current. This component-level fact is UNCHANGED by the
    fix, deliberately - `mandatory_patch` genuinely is about what was
    asked for, and stays correctly False here.

    validate_operation_result()'s own PATCH -> FULL_FILE transition is
    ALSO still documented as "intentionally permissive... the model may
    legitimately decide a small patch isn't enough" - also UNCHANGED.

    What DID change: `_validate_actual_mutation_authority()` is now called
    independently, keyed on the ACTUAL RETURNED shape, for every mode -
    proven below to correctly reject the exact same full-file response
    that reached human approval unrejected at the real G1-R2 run's
    attempt 6, DESPITE `mandatory_patch` being False and `validate_
    operation_result` being permissive, because authority is no longer
    keyed on either of those REQUEST-time signals at all."""

    def test_fallback_targeted_base_operation_bypasses_completeness_gate(self, tmp_path):
        """Component-level fact, deliberately unchanged - see class
        docstring. `mandatory_patch` alone was never trustworthy for
        targeted/fallback_targeted modes; TestAdversarial5's SECOND and
        THIRD tests below prove the real, fixed system-level behavior."""
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        # Same skeleton context TestAdversarial1 proved correctly rejects a
        # full_set-mode CREATE/REPAIR_WITH_FULL_FILE request.
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_0(x): ...  # elided", reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="skeleton", is_exact=False, omitted_regions=True,
            revision=content_revision(content),
        )

        attempt_operation = operation_for_attempt("fallback_targeted", has_prior_failure=True)
        assert attempt_operation is CodeOperation.REPAIR_WITH_PATCH

        op, mandatory = _completeness_gated_operation(
            "target.py", attempt_operation,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is False, (
            "component-level fact, unchanged: _completeness_gated_operation never meaningfully "
            "evaluates targeted/fallback_targeted-mode REQUESTS, because their own base operation "
            "is already patch-shaped - this is fine, since authority is no longer sourced from "
            "this signal alone (see _validate_actual_mutation_authority instead)"
        )

    def test_full_file_model_response_under_fallback_targeted_mode_is_accepted_by_validate_operation_result_alone(self, tmp_path):
        """Component-level fact, deliberately unchanged: validate_operation_
        result() itself still raises no contract_error for a full-file
        response when expected=PATCH - this layer's own permissiveness is
        BY DESIGN (an ordinary targeted retry may legitimately need more
        than a small patch) and is not where authority is decided."""
        truncated = _truncated_candidate_content()
        file_obj = {"filepath": "target.py", "content": truncated, "edits": None}
        actual_operation, contract_error = validate_operation_result(
            file_obj, expected=CodeOperation.REPAIR_WITH_PATCH, file_exists=True,
        )
        assert actual_operation is CodeOperation.REPAIR_WITH_FULL_FILE
        assert contract_error is None

    def test_actual_mutation_authority_rejects_the_full_file_response_fallback_targeted_mode_could_not(self, tmp_path):
        """*** THE FIX, proven directly ***: even though mandatory_patch is
        False (test 1 above) and validate_operation_result is permissive
        (test 2 above), _validate_actual_mutation_authority() - keyed on
        the ACTUAL returned operation, never the request - correctly
        rejects this exact candidate. This is the precise scenario that
        reached human approval unrejected at the real G1-R2 run's attempt
        6, now closed."""
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_0(x): ...  # elided", reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="skeleton", is_exact=False, omitted_regions=True,
            revision=content_revision(content),
        )
        truncated = _truncated_candidate_content()
        file_obj = {"filepath": "target.py", "content": truncated, "edits": None}
        actual_operation, contract_error = validate_operation_result(
            file_obj, expected=CodeOperation.REPAIR_WITH_PATCH, file_exists=True,
        )
        assert contract_error is None  # still accepted at THIS layer, by design

        reason = _validate_actual_mutation_authority(
            "target.py", actual_operation, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is not None, (
            "D1-A FIX: the actual returned full-file shape must be rejected regardless of the "
            "fallback_targeted mode's own patch-shaped request"
        )


class TestAdversarial6C3MemberExactToFullFile:
    """6. C3 member_exact -> full-file response: correctly gated,
    regardless of provenance (search_token_containment vs vector_chunk_
    header/failure_location) - D1 reads tier/is_exact/member_id, never
    provenance, so a C3-sourced member_exact item is exactly as protective
    as any other source's."""

    def test_c3_sourced_member_exact_still_rejects_full_file(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_7(x):\n    return x + 7\n",
            reason="retry_package:search_token_containment",
            source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="helper_7", omitted_regions=False,
            revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True


class TestAdversarial7C3MemberExactToPatchWithinMember:
    """7. C3 member_exact -> patch within grounded member: the intended
    HAPPY PATH - a well-formed patch whose SEARCH text exactly matches the
    grounded member's real, current body must be authorized (never
    downgraded further, never rejected as mandatory-patch-violating, since
    it already IS the mandated shape)."""

    def test_patch_matching_grounded_member_body_is_not_downgraded(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_7(x):\n    return x + 7\n",
            reason="retry_package:search_token_containment",
            source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="helper_7", omitted_regions=False,
            revision=content_revision(content),
        )
        # A targeted-mode attempt's own base operation is already PATCH -
        # _completeness_gated_operation's first-line check passes it through
        # unchanged (never downgrades an ALREADY-patch request further).
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_PATCH,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is False

        file_obj = {
            "filepath": "target.py",
            "edits": [{
                "search": "def helper_7(x):\n    return x + 7\n",
                "replace": "def helper_7(x):\n    return x + 700\n",
            }],
        }
        actual_operation, contract_error = validate_operation_result(
            file_obj, expected=CodeOperation.REPAIR_WITH_PATCH, file_exists=True,
        )
        assert actual_operation is CodeOperation.REPAIR_WITH_PATCH
        assert contract_error is None


class TestAdversarial8StaleRevisionToFullFile:
    """8. stale revision -> full-file response: correctly gated - a
    recorded tier=full/is_exact=True item whose revision no longer matches
    the file's REAL current content must not authorize anything."""

    def test_stale_revision_rejects_full_file_mandatorily(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content=content, reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, member_id=None, omitted_regions=False,
            revision="stale-revision-does-not-match-current-content",
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True


# ---------------------------------------------------------------------------
# Task 3 - full D1 state-transition reproduction (attempt 2 -> attempt 6)
# ---------------------------------------------------------------------------

class TestD1StateTransitionReproduction:
    """Deterministic reconstruction of the G1-R2 attempt-2 -> attempt-6
    progression, as it stood BEFORE the fix - retained to characterize the
    D1-B PROPAGATION MECHANISM in isolation (a component-level fact about
    _record_retry_projection_context_items() that is, by itself, still
    true and UNCHANGED: it has no way to distinguish validated pristine
    content from an already-on-disk candidate purely by reading the
    worktree). See TestCriticalPropagation below for the proof that this
    mechanism is now UNREACHABLE via the real code path, because D1-A's
    fix (_validate_actual_mutation_authority) never lets a candidate
    lacking authority reach the sandbox worktree in the first place -
    D1-B's own propagation route is closed as a direct, structural
    consequence of D1-A, not a second independent code change (confirmed:
    kriya/workflow/attempt.py has exactly ONE write-to-sandbox call site,
    AuthorizedFileWriter.commit_batch(), and it runs strictly AFTER the
    per-file operation-contract enforcement loop this fix lives in - a
    rejected candidate's `raise QualityGateFailure` never reaches it)."""

    def test_full_set_mode_correctly_rejects_full_file_against_true_baseline(self, tmp_path):
        """Attempt-2 equivalent: proves the gate DOES work correctly
        BEFORE any poisoning has occurred - baseline-correctness control."""
        baseline = _write(tmp_path, "target.py", _large_baseline_content())
        assert len(baseline) > 6000
        failure = Failure(
            type="anchored_edit", message="x", raw_output="x",
            file_locations=[FileLocation(filepath="target.py")],
            likely_files=["target.py"], attempted_edits=[], attempt=2,
        )
        pkg = build_retry_package(
            failure=failure, worktree_path=str(tmp_path),
            all_files=["target.py"], target_files=["target.py"],
            source_context=None, max_chars=2000,
        )
        assert pkg.target_projections[0].omitted_regions is True

        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        _record_retry_projection_context_items(state, pkg)
        attempt_operation = operation_for_attempt("full_set", has_prior_failure=True)
        assert attempt_operation is CodeOperation.REPAIR_WITH_FULL_FILE
        op, mandatory = _completeness_gated_operation(
            "target.py", attempt_operation, file_exists=True, ctx=ctx, state=state,
        )
        assert mandatory is True, "attempt-2 equivalent: correctly gated against the true baseline"

    def test_poisoned_sandbox_content_legitimizes_next_full_set_attempt(self, tmp_path):
        """HYPOTHETICAL, given an ALREADY-poisoned sandbox (see
        TestCriticalPropagation below for the proof this can no longer
        happen via the real code path): IF a truncated candidate were to
        reach disk, the VERY NEXT retry-package build, reading that
        now-small file fresh from the worktree, would mark it tier=full/
        is_exact=True, and a SUBSEQUENT full_set-mode attempt would be
        authorized against it. This is _record_retry_projection_context_
        items' own component-level behavior, unchanged by the fix (it
        still has no provenance concept of its own) - the fix's job is
        making sure this function is never HANDED poisoned content, not
        rewriting this function itself."""
        _write(tmp_path, "target.py", _large_baseline_content())

        # Simulated directly here (bypassing run_attempt() and its now-
        # fixed gate) purely to characterize this ONE downstream
        # component's own behavior in isolation - not a claim that a real
        # run can still reach this state.
        truncated = _truncated_candidate_content()
        (tmp_path / "target.py").write_text(truncated)
        assert len(truncated) < 2000, "fixture must genuinely fit the SAME 2000-char budget unelided"

        failure = Failure(
            type="anchored_edit", message="x", raw_output="x",
            file_locations=[FileLocation(filepath="target.py")],
            likely_files=["target.py"], attempted_edits=[], attempt=8,
        )
        pkg = build_retry_package(
            failure=failure, worktree_path=str(tmp_path),
            all_files=["target.py"], target_files=["target.py"],
            source_context=None, max_chars=2000,  # SAME budget as the attempt-2 control above
        )
        assert pkg.target_projections[0].omitted_regions is False, (
            "DEFECT B fixture assumption: the truncated candidate must now fit unelided"
        )

        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        _record_retry_projection_context_items(state, pkg)
        item = state.known_target_context_items["target.py"]
        assert item.tier == "full" and item.is_exact is True and item.member_id is None, (
            "component-level fact, unchanged: IF poisoned content reached disk, it would be "
            "recorded with the SAME tier=full/is_exact=True/member_id=None shape genuine "
            "baseline content carries - this is exactly why D1-A's fix must stop the poisoning "
            "itself, not try to detect it after the fact here"
        )

        attempt_operation = operation_for_attempt("full_set", has_prior_failure=True)
        op, mandatory = _completeness_gated_operation(
            "target.py", attempt_operation, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE
        assert mandatory is False, (
            "component-level fact, unchanged, given this HYPOTHETICAL already-poisoned state: a "
            "full_set-mode attempt would be authorized to replace the file wholesale - "
            "TestCriticalPropagation below proves this state is never actually reached"
        )


# ---------------------------------------------------------------------------
# D1-A specific-scenario matrix (Task's own REQUIRED TESTS 1-4/13)
# ---------------------------------------------------------------------------

class TestActualShapeMatrix:
    """The requested-vs-returned matrix from the fix task itself, each
    scenario checked directly against _validate_actual_mutation_authority()
    - the one function all of them now funnel through, regardless of mode."""

    def test_1_patch_requested_patch_returned_member_exact_allowed(self, tmp_path):
        """PATCH requested + PATCH returned + valid member_exact -> allow.
        _validate_actual_mutation_authority() never even evaluates a
        non-full-file actual_operation - patch authority (apply_anchored_
        edits' own exact-match requirement) governs it instead, unchanged."""
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_7(x):\n    return x + 7\n",
            reason="known_target", source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="helper_7", omitted_regions=False,
            revision=content_revision(content),
        )
        reason = _validate_actual_mutation_authority(
            "target.py", CodeOperation.REPAIR_WITH_PATCH, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is None

    def test_2_patch_requested_full_returned_skeleton_rejected(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_0(x): ...  # elided", reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="skeleton", is_exact=False, omitted_regions=True,
            revision=content_revision(content),
        )
        reason = _validate_actual_mutation_authority(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is not None

    def test_3_patch_requested_full_returned_member_exact_rejected(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_7(x):\n    return x + 7\n",
            reason="known_target", source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="helper_7", omitted_regions=False,
            revision=content_revision(content),
        )
        # A member slice is real, exact evidence for ITS member ONLY - never
        # authority to replace the surrounding whole file, regardless of
        # what was requested.
        reason = _validate_actual_mutation_authority(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is not None

    def test_4_patch_requested_full_returned_authoritative_source_allowed(self, tmp_path):
        """PATCH requested + FULL returned + authoritative full current
        source -> evaluated under full-file rules, and ALLOWED (the
        context genuinely was complete/exact/current - the model choosing
        to over-deliver a full file it was fully justified in seeing is
        not itself unsafe; existing tests already prove D1 makes NO
        distinction based on which mode requested it, only on whether
        authority actually exists)."""
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content=content, reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, member_id=None, omitted_regions=False,
            revision=content_revision(content),
        )
        reason = _validate_actual_mutation_authority(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is None

    def test_5_full_requested_full_returned_authoritative_source_allowed(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content=content, reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, member_id=None, omitted_regions=False,
            revision=content_revision(content),
        )
        reason = _validate_actual_mutation_authority(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is None

    def test_6_full_requested_full_returned_stale_source_rejected(self, tmp_path):
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content=content, reason="known_target",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, member_id=None, omitted_regions=False,
            revision="stale-revision-does-not-match-current-content",
        )
        reason = _validate_actual_mutation_authority(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is not None

    def test_13_c3_member_exact_patch_happy_path_unchanged(self, tmp_path):
        """Item 13: the CTX-001-P1-C3 grounded-member patch path must be
        completely unaffected by this fix - a patch response for a
        genuinely grounded member is never even routed through the
        full-file authority check."""
        content = _write(tmp_path, "target.py", _large_baseline_content())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def helper_7(x):\n    return x + 7\n",
            reason="retry_package:search_token_containment",
            source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="helper_7", omitted_regions=False,
            revision=content_revision(content),
        )
        file_obj = {
            "filepath": "target.py",
            "edits": [{
                "search": "def helper_7(x):\n    return x + 7\n",
                "replace": "def helper_7(x):\n    return x + 700\n",
            }],
        }
        actual_operation, contract_error = validate_operation_result(
            file_obj, expected=CodeOperation.REPAIR_WITH_PATCH, file_exists=True,
        )
        assert contract_error is None
        assert actual_operation is CodeOperation.REPAIR_WITH_PATCH
        reason = _validate_actual_mutation_authority(
            "target.py", actual_operation, file_exists=True, ctx=ctx, state=state,
        )
        assert reason is None, "a grounded member-exact patch must never be rejected by this check"


# ---------------------------------------------------------------------------
# The CRITICAL PROPAGATION TEST - real run_attempt(), real sandbox write
# path, both D1-A and D1-B closed together (Tasks 8/9/10/12/14/15)
# ---------------------------------------------------------------------------

def _very_large_baseline_content() -> str:
    """~80,000 chars - comfortably exceeds even the largest real
    known_target/retry-package budget (48,000 chars, from context_window=
    32768 * 1.5 capped at 48000) regardless of exact config, so this
    fixture genuinely cannot fit unelided under any realistic production
    budget - unlike _large_baseline_content() above (~14,000 chars, fine
    for the unit-level tests above which pass an explicit small max_chars,
    but NOT large enough to force omission under run_attempt()'s own real,
    uncapped budget computation)."""
    body = "\n".join(f"def helper_{i}(x):\n    return x + {i}\n" for i in range(2000))
    return f'"""Large synthetic brownfield module."""\n{body}'


class TestCriticalPropagation:
    """Reproduces the proven G1 sequence deterministically through the
    REAL run_attempt() (not a simulation of its effects) - closing D1-A
    and D1-B together, exactly as the fix task's own "CRITICAL PROPAGATION
    TEST" section requires:

        attempt N: skeleton/excerpted context -> targeted mode requests
        patch -> model returns a truncated full file
        EXPECTED: candidate rejected PRE-WRITE.

    Then proves: sandbox content remains the pristine pre-attempt content;
    the retry projection built AS PART OF THIS SAME attempt cannot observe
    the (never-written) truncated candidate as current authority; a
    subsequent full_set attempt's own completeness gate remains correctly
    mandatory."""

    def test_unauthorized_full_file_candidate_rejected_pre_write_sandbox_unchanged(self, tmp_path):
        baseline = _write(tmp_path, "target.py", _very_large_baseline_content())
        ctx = _minimal_attempt_ctx(
            tmp_path, architect_files=["target.py"], expected_files_upfront=["target.py"],
        )
        state = GenerationState()
        state.attempt_number = 2
        state.last_attempt_mode = "targeted"  # same vulnerable mode class as fallback_targeted
        state.last_implicated_files = ["target.py"]
        state.all_files_written = {"target.py"}
        state.last_failure = Failure(
            type="anchored_edit", message="x", raw_output="x",
            file_locations=[FileLocation(filepath="target.py")],
            likely_files=["target.py"], attempted_edits=[], attempt=1,
        )
        truncated = _truncated_candidate_content()
        ctx.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "target.py", "content": truncated}],
        )

        with pytest.raises(QualityGateFailure) as excinfo:
            asyncio.run(run_attempt(state, ctx))

        # PRE-WRITE rejection, with the exact reason code this fix introduces.
        assert excinfo.value.failure.type == "operation_contract"
        assert excinfo.value.failure.diagnostics.get("reason_code") == "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"

        # candidate_applied = False, content hash unchanged - the real
        # sandbox file, read fresh from disk, is byte-identical to the
        # pristine pre-attempt content. No compile/test/regression/human
        # approval gate was needed to catch this - the rejection happened
        # before any of them could even run.
        current_on_disk = (tmp_path / "target.py").read_text()
        assert current_on_disk == baseline
        assert content_revision(current_on_disk) == content_revision(baseline)
        assert state.files_written == [], "candidate_applied=False - nothing was ever committed"

        # Trace evidence (item 15): distinguishes requested vs actual shape,
        # context tier/exactness/provenance-adjacent revision, and the
        # rejection reason - without persisting the raw truncated candidate
        # content anywhere in the event.
        rejection_events = [e for e in state.run_events if e.kind == "operation_authority.rejected"]
        assert len(rejection_events) == 1
        details = rejection_events[0].details
        assert details["requested_operation"] == "repair_with_patch"
        assert details["actual_operation"] == "repair_with_full_file"
        assert details["mandatory_patch_from_request"] is False, (
            "proves this is EXACTLY the case the old code missed - request-time gating alone "
            "said False, and the NEW response-time check is what actually rejected it"
        )
        assert details["known_context_tier"] in ("implementation_excerpt", "skeleton")
        assert details["known_context_is_exact"] is False
        assert "content" not in details and truncated not in str(details), (
            "the rejected candidate's own text must never be persisted into trace evidence"
        )

        # Retry projection built AS PART OF THIS SAME (rejected) attempt
        # cannot have observed the truncated candidate as current
        # authority - it was never written, so the projection still
        # honestly reflects the true, oversized, unelided-incomplete
        # pristine file.
        item = state.known_target_context_items.get("target.py")
        assert item is not None
        assert item.tier != "full" or item.is_exact is False, (
            "the retry projection must still be honest about incompleteness - never falsely full/exact"
        )

        # Subsequent full_set attempt cannot acquire false full/is_exact
        # authority from the rejected candidate (it was never written, so
        # there is nothing poisoned to inherit).
        attempt_op = operation_for_attempt("full_set", has_prior_failure=True)
        op, mandatory = _completeness_gated_operation(
            "target.py", attempt_op, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True, (
            "D1-B CLOSED: a subsequent full_set attempt remains correctly gated - no false "
            "authority was ever bootstrapped, because D1-A prevented the poisoning write itself"
        )


class TestRejectedCandidateCannotResurrectViaCarryForward:
    """Required authority case #6: "failed/rejected candidate-derived full
    projection, later retry -> REJECT". A second production defect found
    while closing D1-A/D1-B: kriya/workflow/attempt.py's own P9-R1
    completeness-preservation mechanism (`state.last_candidate_contents`,
    folded back into a LATER attempt's own `files` as
    `_kriya_carried_forward_content` whenever that attempt's own recovery
    narrows the Developer's scope away from this file) recorded EVERY
    attempt's own candidate content unconditionally, BEFORE
    `_validate_actual_mutation_authority()` ever ran for that same file. A
    candidate this attempt correctly rejects PRE-WRITE could still land in
    that cache, then be silently resurrected - written to disk with zero
    authority check at all - on a later, unrelated attempt that never even
    asked the Developer for this file. Fixed by moving the recording site
    from an unconditional pre-loop into the per-file enforcement loop
    itself, reached only once that file's own actual mutation shape has
    cleared authority (or was itself already-authorized carried-forward
    content) THIS attempt - never for a file the enforcement loop rejected
    or never reached."""

    def test_rejected_full_file_candidate_never_resurfaces_via_later_recovery_fold_in(self, tmp_path):
        target_baseline = _write(tmp_path, "target.py", _very_large_baseline_content())
        owner_baseline = "def format(x):\n    return x\n"
        _write(tmp_path, "owner.py", owner_baseline)
        ctx = _minimal_attempt_ctx(
            tmp_path, architect_files=["target.py", "owner.py"],
            expected_files_upfront=["target.py", "owner.py"],
            architect_basename_to_path={"target.py": "target.py", "owner.py": "owner.py"},
        )
        state = GenerationState()
        state.attempt_number = 2
        state.last_attempt_mode = "targeted"
        state.last_implicated_files = ["target.py"]
        state.all_files_written = {"target.py", "owner.py"}
        state.last_failure = Failure(
            type="anchored_edit", message="x", raw_output="x",
            file_locations=[FileLocation(filepath="target.py")],
            likely_files=["target.py"], attempted_edits=[], attempt=1,
        )
        truncated = _truncated_candidate_content()
        ctx.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "target.py", "content": truncated}],
        )

        with pytest.raises(QualityGateFailure) as excinfo:
            asyncio.run(run_attempt(state, ctx))
        assert excinfo.value.failure.diagnostics.get("reason_code") == "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"

        # The decisive assertion: the rejected candidate must never enter
        # the cross-attempt carry-forward cache at all.
        assert "target.py" not in state.last_candidate_contents, (
            "D1-B CLOSED (required case #6): a PRE-WRITE-rejected candidate must never be "
            "recorded into state.last_candidate_contents - recording it would let a LATER, "
            "unrelated attempt resurrect it via the _kriya_carried_forward_content fold-in "
            "with zero authority check at all"
        )
        assert (tmp_path / "target.py").read_text() == target_baseline

        # A later, unrelated attempt: api_contract_recovery narrows the
        # Developer to owner.py only - target.py is neither the recovery
        # owner nor part of this attempt's own Developer response at all.
        state.api_contract_recovery = APIContractRecovery.detected(
            [{"owner": "owner.py", "removed_signature": "format(x)"}], [], {"owner.py": "API_OWNER"},
        )
        state.api_contract_recovery.begin_restoration()
        state.api_contract_recovery.owner_contract_restored()
        repaired_owner = "def format(x):\n    return str(x)\n"
        state.known_target_context_items["owner.py"] = make_context_item(
            path="owner.py", content=owner_baseline, reason="known_target_full_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision=content_revision(owner_baseline),
        )
        ctx.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "owner.py", "content": repaired_owner}],
        )
        # This second attempt proceeds further than the first (which
        # rejected before ever reaching runtime verification) - give
        # run_verifier/spec_compliance real, non-coroutine-shaped return
        # values, mirroring test_workflow.py's own _minimal_attempt_ctx
        # defaults, so this test exercises the carry-forward fold-in
        # specifically rather than tripping over an unconfigured mock.
        ctx.run_verifier.judge = AsyncMock(return_value={
            "should_run": False, "run_commands": [], "command_source": "inferred",
            "success_criteria": "",
        })
        ctx.run_verifier.grade = AsyncMock(return_value={
            "passed": False, "reasoning": "not requested", "likely_files": [],
        })
        ctx.spec_compliance.check = AsyncMock(return_value={
            "compliant": True, "reasoning": "not requested",
            "missing_requirements": [], "likely_files": [],
        })

        from unittest.mock import patch
        try:
            with patch(
                "kriya.tools.validate.PolymorphicValidator.run_compile_check",
                return_value={"success": True, "output": ""},
            ), patch(
                "kriya.tools.validate.PolymorphicValidator.run_tests",
                return_value={"success": True, "output": ""},
            ):
                asyncio.run(run_attempt(state, ctx))
        except IncompleteGenerationError:
            # Acceptable: target.py correctly reported missing rather than
            # fabricated from the rejected candidate - proves the same
            # thing the positive assertion below proves either way.
            pass

        # The one assertion that actually discriminates: whatever happened,
        # target.py's real content on disk must still be the pristine
        # baseline - never the truncated, never-authorized candidate.
        assert (tmp_path / "target.py").read_text() == target_baseline, (
            "the rejected candidate must never reach disk via a later attempt's own "
            "carry-forward fold-in, even when that attempt is otherwise unrelated to target.py"
        )


class TestApprovalNeverReached:
    """Item 11: a candidate violating deterministic whole-file authority
    must never reach human approval - proven through the real
    run_generation_workflow() orchestration, not merely inferred from
    run_attempt() raising."""

    def test_unauthorized_full_file_candidate_never_reaches_approval_callback(self, tmp_path):
        import json
        import subprocess

        cfg = AppConfig()
        cfg.autonomy.mode = "human-in-the-loop"
        cfg.autonomy.run_verification_enabled = False
        # A small, EXPLICIT context_window (rather than a huge multi-hundred-
        # KB fixture) keeps this test fast and deterministic - the known-
        # target budget (context_window * 0.75, in tokens) is what attempt
        # 1's own "does this fit unelided" check is actually measured
        # against, not raw file size in isolation.
        cfg.llm.context_window = 2000
        kernel = Kernel(config=cfg)
        llm = LLMClient(cfg)

        large_content = "\n".join(f"def helper_{i}(x):\n    return x + {i}\n" for i in range(300))
        (tmp_path / "target.py").write_text(large_content)
        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=str(tmp_path), check=True)

        truncated = "\n".join(f"def helper_{i}(x):\n    return x + {i}\n" for i in range(3))
        # Planner, Architect, then the SAME bad truncated full-file
        # response (real, valid JSON - not a Python repr, which pytest's
        # own JSON parser would reject for an UNRELATED reason) repeated
        # for every remaining Developer call this run makes (however many
        # retries it takes to exhaust - never rewarded with authority
        # regardless of how many times it's offered, proving the invariant
        # holds across the WHOLE retry budget, not just once).
        call_count = {"n": 0}

        async def scripted_complete(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "Step 1: Fix the bug in target.py"
            if call_count["n"] == 2:
                return "Design: modify target.py"
            return json.dumps([{"filepath": "target.py", "content": truncated}])

        llm.complete = scripted_complete
        approval_callback = MagicMock(return_value=True)  # would APPROVE if ever asked - proves it's never asked

        we = WorkflowEngine(kernel, llm)
        result = asyncio.run(we.run_generation_workflow(
            goal="Fix a narrow bug in target.py",
            workspace_path=str(tmp_path),
            approval_callback=approval_callback,
        ))

        assert result["quality_gates_passed"] is False
        assert result["failure_category"] == "quality_gates_exhausted"
        assert approval_callback.call_count == 0, (
            "an unauthorized full-file candidate must never reach human approval at all - "
            "approval must not be relied on, or even reached, to contain this class of defect"
        )
        # Real workspace file untouched throughout.
        assert (tmp_path / "target.py").read_text() == large_content


class TestValidationBaselinePreservedByD1Rejection:
    """Item 14: D1 rejection happens strictly before any candidate could
    contaminate baseline-validation state - the newly implemented PRE/POST
    validation architecture (kriya/workflow/validation_baseline.py) is not
    touched anywhere in this fix, and a captured baseline on `state` must
    survive an unrelated D1 rejection completely unchanged."""

    def test_captured_baseline_unchanged_by_a_d1_rejected_attempt(self, tmp_path):
        from kriya.workflow.validation_baseline import (
            ValidationInvocation,
            capture_validation_baseline,
        )

        baseline = _write(tmp_path, "target.py", _very_large_baseline_content())
        pristine_validation_baseline = capture_validation_baseline(
            workspace_revision="pristine-rev-unchanged", run_id="r1",
            invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"),
            raw_result={"success": True, "output": ""},
        )

        ctx = _minimal_attempt_ctx(
            tmp_path, architect_files=["target.py"], expected_files_upfront=["target.py"],
        )
        state = GenerationState()
        state.attempt_number = 2
        state.last_attempt_mode = "targeted"
        state.last_implicated_files = ["target.py"]
        state.all_files_written = {"target.py"}
        state.last_failure = Failure(
            type="anchored_edit", message="x", raw_output="x",
            file_locations=[FileLocation(filepath="target.py")],
            likely_files=["target.py"], attempted_edits=[], attempt=1,
        )
        state.validation_baseline_targeted = pristine_validation_baseline
        state.validation_baseline_full_regression = pristine_validation_baseline

        truncated = _truncated_candidate_content()
        ctx.developer.run_generation = AsyncMock(
            return_value=[{"filepath": "target.py", "content": truncated}],
        )

        with pytest.raises(QualityGateFailure):
            asyncio.run(run_attempt(state, ctx))

        assert state.validation_baseline_targeted == pristine_validation_baseline
        assert state.validation_baseline_full_regression == pristine_validation_baseline
