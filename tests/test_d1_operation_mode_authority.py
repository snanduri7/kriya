"""D1 operation-mode authority investigation (2026-09-18, VAL-001 G1-R2
post-mortem, Tasks 3/4/5).

VAL-001 G1-R2 (Kriya run 8cc2018a against Graphify @ 67f99bd0) proved a
full-file candidate identical in shape to one correctly rejected at attempt 2
(operation_contract: "mandatory REPAIR_WITH_PATCH, got repair_with_full_file")
was instead ACCEPTED through candidate gates at attempt 6 (fallback_targeted
mode) and again at attempts 8/9 (full_set mode) - reaching human approval
with an ~88% file-content deletion, correctly rejected by a human but never
even reaching that human's attention with D1's own invariant having actually
fired.

This file is INVESTIGATION-ONLY: it proves CURRENT actual behavior with real
production functions and synthetic (never real Graphify) fixtures, never
weakened to make a defect look absent. Where a test demonstrates whole-file
replacement can be authorized without complete authoritative current-file
source, its own docstring says so explicitly - `PROVEN_D1_DEFECT` in this
package's own investigation record. D1's decision LOGIC is not modified
anywhere in this file or elsewhere this session - see the investigation's
own STOP-before-fixing instruction.

No live model/Ollama calls anywhere in this file. No Graphify production
source is copied here - every fixture is synthetic, sized to make budget
math explicit and reproducible rather than dependent on any specific real
file's byte count.
"""
import os
import tempfile

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import (
    AttemptContext,
    _completeness_gated_operation,
    _operation_map,
    _record_retry_projection_context_items,
)
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.operations import CodeOperation, operation_for_attempt, validate_operation_result
from kriya.workflow.retry_package import build_retry_package
from kriya.workflow.state import GenerationState

from unittest.mock import AsyncMock, MagicMock


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

    *** PROVEN_D1_DEFECT ***

    operation_for_attempt("targeted"/"fallback_targeted", ...) ALWAYS
    returns CodeOperation.REPAIR_WITH_PATCH as the attempt's own BASE
    operation (kriya/workflow/operations.py:81-82) - unconditionally, with
    NO reference to context completeness at all. _completeness_gated_
    operation()'s own FIRST check
    (`if ... base_operation not in (CREATE_FULL_FILE, REPAIR_WITH_FULL_FILE):
    return base_operation, False`) then ALWAYS short-circuits for these two
    modes, so `mandatory` is structurally False regardless of whether the
    context shown was ever complete/exact/current.

    validate_operation_result()'s own PATCH -> FULL_FILE transition is
    documented as "intentionally permissive... the model may legitimately
    decide a small patch isn't enough" - a deliberate design choice for the
    ORDINARY (mandatory=False) case. The consequence, proven here: a
    targeted/fallback_targeted-mode model response that ignores the
    SEARCH:/REPLACE: instruction and returns full file content instead is
    ALWAYS accepted by the operation-contract layer, with ZERO ability for
    D1's own invariant to object - even against a skeleton/incomplete
    context that would have been correctly rejected had the SAME candidate
    arrived via a full_set-mode attempt (see TestAdversarial1 above,
    proving the SAME skeleton context correctly rejects a full_set-mode
    request).

    This is exactly what happened at the real G1-R2 run's attempt 6."""

    def test_fallback_targeted_base_operation_bypasses_completeness_gate(self, tmp_path):
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
        # THE DEFECT: mandatory is False here even though the recorded
        # context is skeleton/incomplete - the SAME context that made
        # TestAdversarial1's mandatory=True. The only difference is which
        # base_operation the calling MODE started from.
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is False, (
            "PROVEN_D1_DEFECT: _completeness_gated_operation never meaningfully evaluates "
            "targeted/fallback_targeted-mode attempts, because their own base operation is "
            "already patch-shaped - mandatory is unconditionally False for these modes"
        )

    def test_full_file_model_response_under_fallback_targeted_mode_is_accepted_unconditionally(self, tmp_path):
        """The consequence: validate_operation_result() itself raises no
        contract_error for a full-file response when expected=PATCH - the
        ONLY thing that would have rejected it (mandatory_patch, proven
        False above) never fires downstream in run_attempt()'s own
        enforcement block either."""
        truncated = _truncated_candidate_content()
        file_obj = {"filepath": "target.py", "content": truncated, "edits": None}
        actual_operation, contract_error = validate_operation_result(
            file_obj, expected=CodeOperation.REPAIR_WITH_PATCH, file_exists=True,
        )
        assert actual_operation is CodeOperation.REPAIR_WITH_FULL_FILE
        assert contract_error is None, (
            "validate_operation_result's own permissive patch-to-full-file transition accepts "
            "this with no contract_error - the mandatory_patch check proven False above is the "
            "ONLY remaining backstop, and it never fires for this mode"
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
    progression, proving the TWO-PART causal chain end to end:

    DEFECT A (structural, proven in TestAdversarial5): a fallback_targeted/
    targeted-mode attempt's full-file response is unconditionally accepted
    by the operation-contract layer, regardless of context completeness.

    DEFECT B (propagation, proven here): once DEFECT A lets one truncated
    full-file candidate reach the sandbox worktree, the NEXT retry's own
    retry-package projection reads that truncated (small) file fresh from
    disk, finds it fits the budget UNELIDED, and legitimately (by its own
    documented rules) records tier=full/is_exact=True for it -
    authorizing a SUBSEQUENT full_set-mode attempt's own full-file
    candidate too, even though the "complete current source" it was
    authorized against is itself a corrupted, never-validated candidate,
    not the true original baseline."""

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
        """Attempt-6-then-8 equivalent: DEFECT A lets a truncated candidate
        onto disk (simulated directly here, since DEFECT A itself is
        already proven in TestAdversarial5 without needing to re-derive it
        through the full retry-loop machinery) - THEN proves DEFECT B: the
        VERY NEXT retry-package build, reading that now-small file fresh
        from the worktree, marks it tier=full/is_exact=True, and a
        SUBSEQUENT full_set-mode attempt is authorized against it."""
        _write(tmp_path, "target.py", _large_baseline_content())

        # Simulate DEFECT A's real-world effect: a fallback_targeted-mode
        # candidate (never gated, per TestAdversarial5) gets committed to
        # the sandbox worktree - the exact write run_attempt() itself
        # performs once "CANDIDATE GATES: PASSED" for real.
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
            "PROVEN_D1_DEFECT (propagation): the poisoned, truncated 'current' content is now "
            "recorded with the SAME tier=full/is_exact=True/member_id=None shape genuine "
            "baseline content would carry"
        )

        attempt_operation = operation_for_attempt("full_set", has_prior_failure=True)
        op, mandatory = _completeness_gated_operation(
            "target.py", attempt_operation, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE
        assert mandatory is False, (
            "PROVEN_D1_DEFECT: a full_set-mode attempt is now authorized to replace the file "
            "wholesale, against 'complete exact current source' that is itself an unvalidated, "
            "already-corrupted prior candidate - not the true original baseline"
        )
