"""VAL-001 G1-R3 (Kriya run 0c18ac70 against Graphify @ 67f99bd0, checkpoint
4507eb2b0936271f259789d617ded6d197a56e42) - deterministic regression tests
for the retry-context-starvation fix.

Root cause (proven by direct reproduction during the investigation phase,
never inferred): `_retry_package_for_attempt()`'s own source universe was
`all_files_written | established_files` - a pre-existing brownfield repair
target that fails BEFORE its first successful write belongs to neither set,
so `build_retry_package()` silently produced ZERO target/reference
projections for a path `target_files` explicitly named, even though nothing
in `build_retry_package()` itself is broken (see TestTargetReachability's
own "generic function, fixed caller" pair below). Every attempt after the
first then reasoned about a 331KB real file using only attempt 1's own
skeleton rendering - 8 real Developer calls, zero of which ever saw the
target's real current source.

Contributing defects also covered here: `fallback_targeted` never called
_resolve_retry_member_hints() at all (a real C3 branch-coverage gap, closed
by centralizing retry-context preparation into _prepare_retry_context());
and repeated retries could spend LLM calls with byte-identical evidence (the
new RetryEvidenceFingerprint no-progress gate).

No Graphify production source is copied anywhere in this file - every
fixture is synthetic, mirroring the *structural shape* the real incident
exercised (a large function containing a nested closure with a
grounded-vs-ungrounded branch), never real content. No live model/Ollama/
embedding calls anywhere in this file.
"""
from typing import Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import (
    AttemptContext,
    _classify_retry_target_source_origin,
    _compute_retry_evidence_fingerprint,
    _completeness_gated_operation,
    _prepare_retry_context,
    _record_retry_projection_context_items,
    _resolve_retry_member_hints,
    _retry_package_for_attempt,
)
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.operations import CodeOperation
from kriya.workflow.retry_package import build_retry_package
from kriya.workflow.state import GenerationState


# ---------------------------------------------------------------------------
# Shared fixtures (this file's own copies - see _minimal_attempt_ctx's own
# docstring in test_val001_g1_remediation.py for why this repo keeps these
# per-file rather than importing across test modules).
# ---------------------------------------------------------------------------

def _minimal_attempt_ctx(tmp_path, **overrides) -> AttemptContext:
    defaults = dict(
        goal="Fix a narrow bug in an existing file",
        plan="Step 1: fix it",
        design="Design: minimal change",
        workspace_path=str(tmp_path),
        worktree_path=str(tmp_path),
        architect_files=["engine.py"],
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
        expected_files_upfront=["engine.py"],
        architect_basename_to_path={"engine.py": "engine.py"},
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
    if "migration_resolution" not in overrides:
        from kriya.workflow.migration import resolve_migration_resolution
        defaults["migration_resolution"] = resolve_migration_resolution(
            defaults.get("grounding_goal") or defaults["goal"], defaults["workspace_path"],
        )
    return AttemptContext(**defaults)


def _write_target(tmp_path, filepath: str, content: str) -> str:
    full = tmp_path / filepath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return content


def _g1_shaped_module() -> str:
    """Mirrors the real incident's structural shape (a large function
    containing a nested closure with a grounded-vs-ungrounded generic-call
    branch) - never real Graphify content."""
    return (
        "def unrelated_helper(value):\n"
        "    return value * 2\n"
        "\n"
        "def outer_extractor(nodes, config):\n"
        "    def add_node(node_id):\n"
        "        return node_id\n"
        "\n"
        "    def walk_calls(node, config, source):\n"
        "        callee_name = None\n"
        "        fn_node = node.child_by_field_name('function')\n"
        "        if fn_node is not None and fn_node.type == 'identifier':\n"
        "            callee_name = read_text(fn_node, source)\n"
        "        elif fn_node is not None and fn_node.type == 'member_access_expression':\n"
        "            mname = fn_node.child_by_field_name('name')\n"
        "            if mname is not None:\n"
        "                callee_name = read_text(mname, source)\n"
        "        return callee_name\n"
        "\n"
        "    for node in nodes:\n"
        "        walk_calls(node, config, node.source)\n"
        "    return add_node\n"
    )


_SEARCH_TEXT = (
    "if fn_node is not None and fn_node.type == 'identifier':\n"
    "    callee_name = read_text(fn_node, source)\n"
    "elif fn_node is not None and fn_node.type == 'member_access_expression':\n"
    "    mname = fn_node.child_by_field_name('name')\n"
    "    callee_name = read_text(mname, source)"
)


def _g1_shaped_module_large() -> str:
    """Same structural shape/members as _g1_shaped_module(), padded with
    inert filler functions so the whole file exceeds every retry-package
    budget this file's tests use (prompt_window up to 32768 ->
    retry evidence up to 19,660 chars) - mirrors the real incident's own
    331,064-char file staying an IMPLEMENTATION_EXCERPT (omitted_regions=
    True) across every retry, which is what keeps CTX-001-P1-C3's own
    SOURCE 3 gate (state.known_target_context_items[path].omitted_regions)
    open on a SECOND retry the way _g1_shaped_module()'s own ~700 chars -
    which fits fully within any of these budgets and so becomes tier="full"
    after only one retry-package recording - cannot."""
    filler = "".join(
        f"def _filler_function_{i}(value):\n    return value + {i}\n\n\n"
        for i in range(1200)
    )
    return filler + _g1_shaped_module()


def _skeleton_context_item(revision: str, path: str = "engine.py"):
    return make_context_item(
        path=path, content="def outer_extractor(...): ...  # elided",
        reason="known_target_bounded_excerpt",
        source_type="named_in_request", trust_level="repository",
        tier="skeleton", is_exact=False, omitted_regions=True, revision=revision,
    )


def _anchored_edit_failure(search_text: str = _SEARCH_TEXT, path: str = "engine.py") -> Failure:
    return Failure(
        type="anchored_edit",
        message=f"ANCHORED EDIT FAILURE in {path}: anchor mismatch",
        raw_output="anchor mismatch",
        file_locations=[FileLocation(filepath=path)],
        likely_files=[path],
        attempted_edits=[{"search": search_text, "replace": search_text}] if search_text else [],
        attempt=3,
    )


# ---------------------------------------------------------------------------
# Items 1/A-F: target reachability
# ---------------------------------------------------------------------------

class TestTargetReachability:
    def test_build_retry_package_itself_is_unchanged_and_generic(self, tmp_path):
        """Characterizes the ROOT CAUSE directly against the real,
        untouched build_retry_package(): given the OLD-style universe (a
        target absent from all_files - exactly what all_files_written |
        established_files produced for a never-written brownfield target),
        it correctly, generically produces zero projections and doesn't
        even record the path in omitted_files - proving the defect lived in
        the CALLER's universe construction, not in this function, and that
        this function needed no change (task item 2)."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        failure = _anchored_edit_failure()
        pkg = build_retry_package(
            failure=failure, worktree_path=str(tmp_path),
            all_files=[], target_files=["engine.py"],
            source_context=None, max_chars=36864,
        )
        assert pkg.target_projections == ()
        assert pkg.reference_projections == ()
        assert "engine.py" not in pkg.omitted_files
        assert pkg.target_files == ("engine.py",)

    def test_A_unwritten_preexisting_target_is_projected(self, tmp_path):
        """The fix: the SAME scenario through the real, fixed caller now
        reaches the target from the current worktree."""
        _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_failure = _anchored_edit_failure()
        # Exactly the real incident's state: nothing ever successfully
        # written, nothing established from an earlier milestone.
        assert state.all_files_written == set()
        assert ctx.established_files == []

        pkg = _retry_package_for_attempt(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
        )
        assert pkg is not None
        assert len(pkg.target_projections) == 1
        assert pkg.target_projections[0].path == "engine.py"
        assert pkg.target_projections[0].content

        _record_retry_projection_context_items(state, pkg)
        item = state.known_target_context_items["engine.py"]
        assert item.tier != "skeleton"
        assert _classify_retry_target_source_origin(state, ctx, "engine.py") == "current_worktree"

    def test_B_already_written_target_behavior_unchanged(self, tmp_path):
        """No regression: a target already in all_files_written behaves
        exactly as before (still reached, still classified by its real
        origin - not "current_worktree", since it wasn't newly reached)."""
        _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_failure = _anchored_edit_failure()
        state.all_files_written = {"engine.py"}

        pkg = _retry_package_for_attempt(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
        )
        assert len(pkg.target_projections) == 1
        assert _classify_retry_target_source_origin(state, ctx, "engine.py") == "already_written"

    def test_C_established_file_behavior_unchanged(self, tmp_path):
        """No regression: a prior-milestone established_files path is still
        reached and classified as "established", not "current_worktree"."""
        _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path, established_files=["engine.py"])
        state = GenerationState()
        state.last_failure = _anchored_edit_failure()

        pkg = _retry_package_for_attempt(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
        )
        assert len(pkg.target_projections) == 1
        assert _classify_retry_target_source_origin(state, ctx, "engine.py") == "established"

    def test_D_excluded_path_remains_excluded(self, tmp_path):
        """The pre-existing `exclude` (DUPLICATE_SOURCE_CONTEXT_PATHS)
        semantics still work identically after unioning target_files into
        the universe - a path already given a higher-fidelity rendering
        elsewhere (e.g. a member-exact package) is still dropped entirely,
        never a second, coarser duplicate."""
        _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_failure = _anchored_edit_failure()

        pkg = _retry_package_for_attempt(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            exclude=["engine.py"],
        )
        assert pkg.target_projections == ()
        assert pkg.reference_projections == ()

    def test_E_no_duplicate_source_context_between_member_hint_and_package(self, tmp_path):
        """_prepare_retry_context() excludes a member-hint-grounded path
        from the general retry package (its own member-exact rendering
        already covers it at higher fidelity) - proven end to end."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.budgets.anchor_failure_counts["engine.py"] = 1
        state.last_failure = _anchored_edit_failure()

        prep = _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        assert prep.member_hints == {"engine.py": ["outer_extractor.walk_calls"]}
        # Excluded from the general package - never shown at two fidelities.
        assert prep.retry_package.target_projections == ()
        assert "member_exact" in prep.member_hint_rendered or prep.member_hint_rendered != ""
        assert state.known_target_context_items["engine.py"].member_id == "outer_extractor.walk_calls"

    def test_F_large_target_remains_budget_constrained(self, tmp_path):
        """The fix widens REACHABILITY only, never the budget/truncation
        logic itself (no larger-context-window workaround) - a target far
        bigger than the retry budget still projects as a bounded, non-exact
        excerpt, exactly like every other build_retry_package() caller."""
        huge_body = "\n".join(f"    x{i} = {i}" for i in range(4000))
        content = f"def big_function():\n{huge_body}\n    return x0\n"
        _write_target(tmp_path, "engine.py", content)
        assert len(content) > 40000
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_failure = _anchored_edit_failure()

        pkg = _retry_package_for_attempt(
            state, ctx, target_files=["engine.py"], prompt_window=16384,
        )
        assert len(pkg.target_projections) == 1
        projection = pkg.target_projections[0]
        assert len(projection.content) < len(content)
        assert projection.omitted_regions is True


# ---------------------------------------------------------------------------
# Items G/H/I: member escalation
# ---------------------------------------------------------------------------

class TestMemberEscalation:
    def test_G_unique_grounding_reaches_member_exact(self, tmp_path):
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.budgets.anchor_failure_counts["engine.py"] = 1
        state.last_failure = _anchored_edit_failure()

        hints = _resolve_retry_member_hints(ctx, state, ["engine.py"])
        assert hints == {"engine.py": ["outer_extractor.walk_calls"]}

    def test_H_ambiguous_grounding_fails_closed(self, tmp_path):
        """Two equally-plausible members both contain the same distinctive
        tokens - grounding must return no hint, never a guess."""
        content = (
            "def handler_one(node, config, source):\n"
            "    callee_name = None\n"
            "    fn_node = node.child_by_field_name('function')\n"
            "    generic_marker_token = 1\n"
            "    return callee_name\n"
            "\n"
            "def handler_two(node, config, source):\n"
            "    callee_name = None\n"
            "    fn_node = node.child_by_field_name('function')\n"
            "    generic_marker_token = 2\n"
            "    return callee_name\n"
        )
        _write_target(tmp_path, "engine.py", content)
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.budgets.anchor_failure_counts["engine.py"] = 1
        ambiguous_search = "callee_name = None\nfn_node = node.child_by_field_name('function')\ngeneric_marker_token"
        state.last_failure = _anchored_edit_failure(search_text=ambiguous_search)

        assert _resolve_retry_member_hints(ctx, state, ["engine.py"]) == {}

    def test_I_fabricated_search_does_not_become_authority(self, tmp_path):
        """Real, direct reproduction of the actual G1-R3 mechanism: SEARCH
        text that does not literally exist anywhere in real current content
        (the model reasoning from a skeleton, exactly as observed live)
        must never ground - proven against the pristine fixture content
        itself, mirroring the real run 0c18ac70 reproduction
        (`search literally in content: False` -> outcome: no_containment_match)."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.budgets.anchor_failure_counts["engine.py"] = 1
        invented_search = (
            "if mname.type == 'generic_name':\n"
            "    callee_name = strip_type_arguments(mname, source)\n"
            "    validate_generic_bounds(callee_name)"
        )
        assert invented_search not in content
        state.last_failure = _anchored_edit_failure(search_text=invented_search)

        assert _resolve_retry_member_hints(ctx, state, ["engine.py"]) == {}

    def test_exact_unrelated_member_does_not_authorize_mutation_elsewhere(self, tmp_path):
        """MUTATION_RELEVANCE_GATE (2026-09-19, VAL-001 G1 DEV-INV rerun):
        a member_exact record for a DIFFERENT, unrelated member of the same
        path must never be treated as "nothing left to escalate to" for a
        CURRENTLY failing edit that actually concerns a different member.
        Before this fix, once ANY member_exact was recorded for a path
        (here: outer_extractor.add_node, from some earlier, unrelated
        escalation), SOURCE 3 was blanket-suppressed for the rest of the
        run - so a LATER anchor failure whose real SEARCH text grounds to
        outer_extractor.walk_calls instead would be silently starved of the
        escalation it needs, and the stale, irrelevant add_node record
        would remain in known_target_context_items unchanged - exactly
        "merely any member_exact from the same file" authorizing silence
        for an unrelated member. The fix: only tier="full" is genuinely
        terminal; a member_exact record for the WRONG member must not block
        re-grounding for the RIGHT one."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        # Simulates an earlier, unrelated grounding (e.g. inspect_member
        # named a different member, or an earlier failure grounded here) -
        # genuinely exact, genuinely real, genuinely NOT the member the
        # CURRENT failing edit concerns.
        state.known_target_context_items["engine.py"] = make_context_item(
            path="engine.py", content="def add_node(node_id):\n    return node_id\n",
            reason="developer_investigation:inspect_member:outer_extractor.add_node",
            source_type="named_in_request", trust_level="repository",
            member_id="outer_extractor.add_node", tier="member_exact", is_exact=True,
            revision=content_revision(content),
        )
        state.budgets.anchor_failure_counts["engine.py"] = 1
        # The REAL failing edit's own SEARCH text is real content that
        # exists in walk_calls, not add_node - the exact G1 shape (a
        # different member than the one already recorded as "exact").
        state.last_failure = _anchored_edit_failure()

        hints = _resolve_retry_member_hints(ctx, state, ["engine.py"])
        assert hints == {"engine.py": ["outer_extractor.walk_calls"]}

        # The previously-recorded, unrelated member_exact record must not
        # survive as the FINAL authority once the actually-relevant member
        # is re-grounded through the full retry-preparation pipeline.
        prep = _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        assert prep.member_hints == {"engine.py": ["outer_extractor.walk_calls"]}
        assert state.known_target_context_items["engine.py"].member_id == "outer_extractor.walk_calls"


# ---------------------------------------------------------------------------
# Items J/K: centralized preparation, mode transitions
# ---------------------------------------------------------------------------

class TestCentralizedPreparation:
    def test_J_common_preparation_across_all_three_modes(self, tmp_path):
        """The single, real production defect this closes: fallback_targeted
        previously never called _resolve_retry_member_hints() at all.
        _prepare_retry_context() is the one function all three retry modes
        now call - proven by invoking it directly with each mode's own
        realistic parameters and asserting identical member-hint escalation
        behavior in every case, not just the primary-model targeted one."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        for mode, model, window in (
            ("targeted", "primary-model", 32768),
            ("fallback_targeted", "fallback-model", 16384),
            ("full_set", "fallback-model", 16384),
        ):
            ctx = _minimal_attempt_ctx(tmp_path)
            state = GenerationState()
            state.last_attempt_mode = mode
            state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
            state.budgets.anchor_failure_counts["engine.py"] = 1
            state.last_failure = _anchored_edit_failure()

            prep = _prepare_retry_context(
                state, ctx, target_files=["engine.py"], prompt_window=window,
                model_identity=model,
            )
            assert prep.member_hints == {"engine.py": ["outer_extractor.walk_calls"]}, mode

    def test_K_member_exact_survives_mode_transition(self, tmp_path):
        """Member escalation is available identically across a real mode
        transition (targeted, primary model -> full_set, fallback model,
        exactly like the real incident's own attempt4 -> attempt6/7/8
        shift): the SAME anchor-failure evidence grounds the SAME member on
        both sides of the transition, proving generation strategy never
        gates whether authoritative context recovery is available (item J's
        own invariant, exercised here across a mode change specifically)."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module_large())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "targeted"
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.budgets.anchor_failure_counts["engine.py"] = 1
        state.last_failure = _anchored_edit_failure()
        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        assert state.known_target_context_items["engine.py"].member_id == "outer_extractor.walk_calls"

        # Transition to full_set on the fallback model - the SAME grounded
        # SEARCH evidence (the model tried the same fix again), different
        # mode/model entirely. MUTATION_RELEVANCE_GATE residual (2026-09-19,
        # VAL-001 G1 DEV-INV rerun): SOURCE 3 now correctly RE-EVALUATES
        # here rather than being blanket-suppressed just because SOME
        # member_exact is already recorded for this path (a prior version
        # of this gate treated any member_exact as "nothing left to
        # escalate to", which silently froze a WRONG recorded member in
        # place across the rest of a run - see _resolve_retry_member_hints'
        # own updated docstring). Re-evaluating the SAME unchanged SEARCH
        # text against the SAME real content deterministically re-derives
        # the SAME single member again - a harmless, idempotent
        # re-confirmation, not a new escalation - so the real property
        # under test is unchanged: the general retry package's own
        # re-inclusion of this now-unexcluded path does not silently
        # DOWNGRADE the already-achieved member_exact record back to a
        # coarser tier.
        state.last_attempt_mode = "full_set"
        state.last_failure = _anchored_edit_failure()
        prep = _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=16384,
            model_identity="fallback-model",
        )
        assert prep.member_hints == {"engine.py": ["outer_extractor.walk_calls"]}
        assert state.known_target_context_items["engine.py"].member_id == "outer_extractor.walk_calls"
        assert state.known_target_context_items["engine.py"].tier == "member_exact"


# ---------------------------------------------------------------------------
# Items L/M/N: no-progress gate
# ---------------------------------------------------------------------------

class TestNoProgressGate:
    def test_L_unchanged_retry_evidence_blocks_redundant_call(self, tmp_path):
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "full_set"
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.last_failure = Failure(
            type="operation_contract", message="whole-file authority rejected",
            raw_output="whole-file authority rejected", likely_files=["engine.py"], attempt=7,
            diagnostics={"reason_code": "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"},
        )
        state.budgets.last_failure_signature = ("operation_contract", "whole-file authority rejected")

        # First attempt with this exact evidence proceeds normally.
        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=16384,
            model_identity="fallback-model",
        )
        # A second, immediately-following attempt in the SAME mode/model
        # with byte-identical evidence (nothing written, same tier/revision,
        # same failure signature - exactly attempt 7 -> attempt 8 in the
        # real run) must be turned into a Failure BEFORE any Developer call.
        with pytest.raises(QualityGateFailure) as exc_info:
            _prepare_retry_context(
                state, ctx, target_files=["engine.py"], prompt_window=16384,
                model_identity="fallback-model",
            )
        assert exc_info.value.failure.type == "no_progress_retry"

    def test_M_changed_member_hint_permits_retry(self, tmp_path):
        """A newly-grounded member hint (real evidence progress) is never
        blocked, even in the same mode/model."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module_large())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "targeted"
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.last_failure = _anchored_edit_failure()
        state.budgets.last_failure_signature = ("anchored_edit", "anchor mismatch")

        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        # Now a real anchor failure has occurred and SOURCE 3 can ground -
        # materially different evidence than the first call.
        state.budgets.anchor_failure_counts["engine.py"] = 1
        prep = _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        assert prep.member_hints == {"engine.py": ["outer_extractor.walk_calls"]}

    def test_N_changed_failure_evidence_permits_retry(self, tmp_path):
        """A genuinely different failure signature (a new defect family) is
        never blocked, even in the same mode/model with unchanged source."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "full_set"
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.last_failure = Failure(
            type="operation_contract", message="whole-file authority rejected",
            raw_output="whole-file authority rejected", likely_files=["engine.py"], attempt=7,
            diagnostics={"reason_code": "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"},
        )
        state.budgets.last_failure_signature = ("operation_contract", "whole-file authority rejected")
        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=16384,
            model_identity="fallback-model",
        )

        state.last_failure = Failure(
            type="compile", message="a genuinely new compile error",
            raw_output="a genuinely new compile error", likely_files=["engine.py"], attempt=8,
        )
        state.budgets.last_failure_signature = ("compile", "a genuinely new compile error")
        # Does not raise - the failure signature progressed.
        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=16384,
            model_identity="fallback-model",
        )

    def test_operation_contract_malformed_response_is_not_deterministic_verdict(self, tmp_path):
        """VAL-001 G1-R3 review (2026-09-19): Failure.type == 'operation_
        contract' is shared by TWO structurally different raise sites -
        _validate_actual_mutation_authority()'s own fixed D1 rejection
        (reason_code=ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED, genuinely
        deterministic given unchanged evidence) and validate_operation_
        result()'s own contract_error (a malformed/mismatched RESPONSE
        SHAPE classification - content-dependent, no reason_code at all).
        Only the first is eligible for the no-progress gate; a repeat of
        the second, with byte-identical evidence, must NOT be blocked -
        resampling a malformed response is exactly as probabilistic as
        resampling a compile failure."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "targeted"
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.last_failure = Failure(
            type="operation_contract",
            message="OPERATION CONTRACT FAILURE in engine.py: malformed repair response: "
            "missing repair outcome marker",
            raw_output="malformed repair response: missing repair outcome marker",
            likely_files=["engine.py"], attempt=3,
        )
        state.budgets.last_failure_signature = (
            "operation_contract", "malformed repair response: missing repair outcome marker",
        )

        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        # Second call, byte-identical evidence and failure signature - must
        # NOT raise, unlike the real D1-rejection case in test_L.
        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )

    def test_anchored_edit_repeat_is_deliberately_not_a_deterministic_verdict(self, tmp_path):
        """UNCHANGED_EVIDENCE_RETRY_BLOCK (2026-09-19, VAL-001 G1 DEV-INV
        rerun): considered and REJECTED adding "anchored_edit" (or a new,
        narrower reason_code for its "search text matched nothing real"
        subclass) to _DETERMINISTIC_VERDICT_REASON_CODES, to make the
        no-progress gate above also block a repeated anchored-edit failure.
        Real counter-evidence from the live G1 rerun this package
        investigates: two consecutive anchored-edit failures against
        materially the same context produced textually DIFFERENT SEARCH
        blocks each time (the model resampled, quoting different - still
        wrong - fabricated identifiers each time) - exactly the
        "resampling with unchanged evidence CAN change the model's own
        output" case _DETERMINISTIC_VERDICT_REASON_CODES' own module
        comment already documents and guards (confirmed regression-tested
        via test_workflow_fallback_chain). Blocking here would also cut off
        SOURCE 3's own re-grounding path (this file's own MUTATION_
        RELEVANCE_GATE fix): a resampled SEARCH block that grounds to a
        DIFFERENT, more relevant member is real evidence progress, not a
        repeat, and _resolve_retry_member_hints must get the chance to see
        it. Fixing the structural cause of frozen evidence (this file's own
        member-escalation/relevance-gate fixes) is the real closure here,
        not an additional blocking gate on top of a now-unstuck mechanism."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "targeted"
        state.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        state.last_failure = _anchored_edit_failure()
        assert state.last_failure.diagnostics is None  # no reason_code attached, by design
        state.budgets.last_failure_signature = ("anchored_edit", "anchor mismatch")

        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        # Second call, byte-identical evidence and failure signature - must
        # NOT raise. Unlike test_L's genuine D1-rejection case, an anchored-
        # edit failure is never eligible for this gate at all.
        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )


# ---------------------------------------------------------------------------
# Items O/P/Q: authority invariants preserved
# ---------------------------------------------------------------------------

class TestAuthorityInvariantsPreserved:
    def test_O_fingerprint_never_carries_raw_candidate_or_search_text(self, tmp_path):
        """Two different, both-ungrounded SEARCH texts (different raw model
        output) must produce the SAME fingerprint for the same real
        grounding OUTCOME (no hint either way) - the fingerprint is built
        from deterministic, worktree-derived grounding results, never raw
        model/candidate bytes."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        state_a = GenerationState()
        state_a.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        fp_a = _compute_retry_evidence_fingerprint(
            state_a, ["engine.py"], "targeted", "primary-model", {},
        )
        state_b = GenerationState()
        state_b.known_target_context_items["engine.py"] = _skeleton_context_item(content_revision(content))
        fp_b = _compute_retry_evidence_fingerprint(
            state_b, ["engine.py"], "targeted", "primary-model", {},
        )
        assert fp_a == fp_b

    def test_P_member_exact_plus_whole_file_response_still_rejected_by_D1(self, tmp_path):
        """D1's own whole-file authority threshold is untouched by this
        pass: a member_exact ContextItem (even a REAL one this fix now
        makes reachable) never authorizes REPAIR_WITH_FULL_FILE."""
        content = _write_target(
            tmp_path, "engine.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n",
        )
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["engine.py"] = make_context_item(
            path="engine.py", content="def b():\n    return 2\n", reason="member_exact",
            source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="b",
            revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "engine.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True

    def test_Q_stale_revision_fails_closed(self, tmp_path):
        """A recorded "full" ContextItem whose revision no longer matches
        the file's real current content must still fail closed - the
        target-reachability fix never weakens this."""
        content = _write_target(tmp_path, "engine.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["engine.py"] = make_context_item(
            path="engine.py", content=content, reason="known_target_full_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision="stale-revision-does-not-match",
        )
        op, mandatory = _completeness_gated_operation(
            "engine.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True


# ---------------------------------------------------------------------------
# Item R: observability
# ---------------------------------------------------------------------------

class TestObservability:
    def test_R_trace_events_capture_source_and_progress_decision(self, tmp_path):
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module())
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "full_set"
        state.attempt_number = 6
        state.last_failure = _anchored_edit_failure()

        _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=16384,
            model_identity="fallback-model",
        )
        kinds = [event.kind for event in state.run_events]
        assert "context.retry_target_source" in kinds
        assert "retry.progress_decision" in kinds

        source_event = next(e for e in state.run_events if e.kind == "context.retry_target_source")
        targets = source_event.details["targets"]
        assert targets[0]["path"] == "engine.py"
        assert targets[0]["source_origin"] == "current_worktree"
        assert targets[0]["known"] is True

        progress_event = next(e for e in state.run_events if e.kind == "retry.progress_decision")
        assert progress_event.details["no_progress"] is False


# ---------------------------------------------------------------------------
# Item 8: real G1-R3 deterministic replay (no LLM)
# ---------------------------------------------------------------------------

class TestG1DeterministicReplay:
    def test_starvation_condition_fails_before_fix_and_resolves_after(self, tmp_path):
        """The full chain the real incident's own attempts 2-8 needed and
        never got, reproduced deterministically end to end:

        requested target -> current-worktree reachable -> non-empty
        budgeted projection -> recorded ContextItem -> deterministic
        grounding reaches member_exact when supplied real, uniquely-
        groundable evidence -> the NEXT retry's own context contains the
        exact current member.

        The "before fix" half is proven directly against the real, still-
        unchanged build_retry_package() with the OLD universe shape (see
        test_build_retry_package_itself_is_unchanged_and_generic above,
        repeated here inline for a single, self-contained before/after
        narrative) - this is the actual production defect, not a mock."""
        content = _write_target(tmp_path, "engine.py", _g1_shaped_module_large())
        revision = content_revision(content)

        # BEFORE: the exact real-incident universe (nothing ever written,
        # nothing established) fed straight to build_retry_package().
        before = build_retry_package(
            failure=_anchored_edit_failure(), worktree_path=str(tmp_path),
            all_files=[], target_files=["engine.py"],
            source_context=None, max_chars=36864,
        )
        assert before.target_projections == (), (
            "pre-fix universe construction reproduces the real starvation condition"
        )

        # AFTER: the real, fixed caller reaches the target regardless.
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.last_attempt_mode = "targeted"
        state.last_failure = _anchored_edit_failure()

        after = _retry_package_for_attempt(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
        )
        assert len(after.target_projections) == 1
        _record_retry_projection_context_items(state, after)
        item = state.known_target_context_items["engine.py"]
        assert item.tier != "skeleton"
        assert item.revision == revision

        # A real anchor-match failure now occurs against this real content -
        # SOURCE 3 can ground against REAL text this time (not a fabricated
        # guess over a never-shown skeleton).
        state.budgets.anchor_failure_counts["engine.py"] = 1
        state.last_failure = _anchored_edit_failure()
        hints = _resolve_retry_member_hints(ctx, state, ["engine.py"])
        assert hints == {"engine.py": ["outer_extractor.walk_calls"]}

        # The NEXT retry's own centralized preparation renders that member
        # exact and records it as such.
        prep = _prepare_retry_context(
            state, ctx, target_files=["engine.py"], prompt_window=32768,
            model_identity="primary-model",
        )
        assert prep.member_hints == {"engine.py": ["outer_extractor.walk_calls"]}
        final_item = state.known_target_context_items["engine.py"]
        assert final_item.tier == "member_exact"
        assert final_item.member_id == "outer_extractor.walk_calls"
        assert final_item.is_exact is True
        assert "walk_calls" in prep.member_hint_rendered
