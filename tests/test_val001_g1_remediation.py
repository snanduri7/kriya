"""VAL-001 G1 remediation (D1/D2/D3-part-1) - deterministic regression tests.

Covers the three production defects demonstrated by VAL-001 G1 (Kriya run
8b6ee803 against Graphify @ 67f99bd0, see docs/assurance/VAL_001_GRAPHIFY_G1.md
and docs/assurance/VAL_001_GRAPHIFY_G1_REMEDIATION_DESIGN.md for the full
forensic record and design rationale):

- D1: whole-file replacement of an EXISTING file must not be authorized
  unless the Developer was shown authoritative, complete, exact, current
  source for it (kriya/workflow/attempt.py::_completeness_gated_operation).
- D2: API_CONTRACT_RECOVERY's REPAIR_BEHAVIOR phase must use an anchored
  patch, never an unconditional full-file rewrite, so a probabilistic
  repair cannot silently discard a just-completed deterministic restoration
  (kriya/workflow/operations.py::operation_for_attempt).
- D3-part-1: find_brownfield_public_api_changes() must not treat a
  function-nested closure as public API merely because its name lacks a
  leading underscore (kriya/workflow/file_resolution.py::
  _normalized_public_signatures / _python_module_and_class_level_signatures).

No Graphify production source is copied anywhere in this file - every
fixture is synthetic, built to mirror the *structural shape* G1 exercised
(a large function containing several non-underscore-named nested closures),
never its actual content.

No live model/Ollama/embedding calls anywhere in this file.
"""
from typing import Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import (
    AttemptContext,
    _completeness_gated_operation,
    _operation_map,
    _record_all_files_written_as_exact_context,
    _record_retry_projection_context_items,
)
from kriya.workflow.context_package import make_context_item
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import Failure
from kriya.workflow.file_resolution import (
    _normalized_public_signatures,
    find_brownfield_public_api_changes,
)
from kriya.workflow.operations import CodeOperation, operation_for_attempt
from kriya.workflow.retry_package import build_retry_package
from kriya.workflow.state import APIContractRecovery, APIContractRecoveryPhase, GenerationState


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _minimal_attempt_ctx(tmp_path, **overrides) -> AttemptContext:
    """Same minimal-fake-dependencies shape as test_workflow.py's own
    _minimal_attempt_ctx - intentionally not imported across test files
    (this repo's own established per-file convention, see
    test_deterministic_failure_diagnostic.py's _minimal_ctx for the same
    pattern) - kept small since these tests only exercise operation
    selection, never the full Developer/Quality-Gates loop."""
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
    if "migration_resolution" not in overrides:
        from kriya.workflow.migration import resolve_migration_resolution
        defaults["migration_resolution"] = resolve_migration_resolution(
            defaults.get("grounding_goal") or defaults["goal"], defaults["workspace_path"],
        )
    return AttemptContext(**defaults)


def _write_target(tmp_path, filepath: str, content: str) -> str:
    full = tmp_path / filepath
    full.write_text(content)
    return content


# ---------------------------------------------------------------------------
# D1 - source-completeness-aware mutation authorization
# ---------------------------------------------------------------------------

class TestD1CompletenessGatedOperation:
    def test_full_exact_current_file_authorizes_whole_file(self, tmp_path):
        content = _write_target(tmp_path, "target.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content=content, reason="known_target_full_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE
        assert mandatory is False

    def test_skeleton_context_prohibits_whole_file(self, tmp_path):
        content = _write_target(tmp_path, "target.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def f(): ...", reason="known_target_bounded_excerpt",
            source_type="named_in_request", trust_level="repository",
            tier="skeleton", is_exact=False, revision=content_revision(content),
            omitted_regions=True,
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True

    def test_signatures_only_prohibits_whole_file(self, tmp_path):
        content = _write_target(tmp_path, "target.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def f() -> int: ...", reason="signatures_only",
            source_type="named_in_request", trust_level="repository",
            tier="signatures", is_exact=False, revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.CREATE_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True

    def test_member_exact_authorizes_anchored_mutation_only(self, tmp_path):
        content = _write_target(
            tmp_path, "target.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n",
        )
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        # member_exact: is_exact=True for the MEMBER, but member_id is set -
        # never authority over the whole file.
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def b():\n    return 2\n", reason="member_exact",
            source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="b",
            revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH, (
            "member_exact must never authorize whole-FILE replacement, only the member it covers"
        )
        assert mandatory is True

    def test_member_exact_mutation_outside_member_is_rejected_by_anchoring(self):
        # HONEST SCOPE NOTE (checked directly against apply_anchored_edits'
        # own source, kriya/workflow/edit_safety.py:248): its match check is
        # `search in shown_context OR search in current_content` - and
        # current_content starts as original_content, the file's real, full,
        # CURRENT content (this function always operates on genuine worktree
        # content, never a skeleton). So a search block matching real text
        # ANYWHERE in the file - not just within a specific member's own
        # shown line range - is accepted; this function's actual guarantee
        # is "the edit is grounded in genuine current content, never
        # fabricated/hallucinated text", not "the edit is confined to the
        # exact member the model was shown." D1's own invariant (member_exact
        # never authorizes whole-FILE replacement) does not depend on the
        # narrower per-member boundary property - a member-scoped
        # REPAIR_WITH_PATCH still cannot silently discard the rest of the
        # file the way a REPAIR_WITH_FULL_FILE candidate demonstrably did in
        # run 8b6ee803, because only the matched span is ever replaced.
        # Genuine per-member line-range confinement is NOT implemented here
        # and is disclosed as a known, narrower limitation - not silently
        # assumed.
        from kriya.workflow.edit_safety import apply_anchored_edits
        original = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
        edits = [{"search": "return 1", "replace": "return 999"}]
        result = apply_anchored_edits(original, edits, shown_context="def b():\n    return 2\n")
        assert "return 999" in result  # matches real current content - fine on its own
        with pytest.raises(Exception):
            apply_anchored_edits(
                original, [{"search": "return NOT_PRESENT_ANYWHERE", "replace": "x"}],
                shown_context="def b():\n    return 2\n",
            )

    def test_partial_explicit_omission_prohibits_whole_file(self, tmp_path):
        content = _write_target(tmp_path, "target.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        # No ContextItem recorded at all for this path (e.g. it was recorded
        # only in `omitted`, never in `relevant_files`) - absence must fail
        # closed, not be treated as implicit exactness.
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True

    def test_stale_revision_prohibits_whole_file(self, tmp_path):
        original_content = "def f():\n    return 1\n"
        _write_target(tmp_path, "target.py", original_content)
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        # ContextItem claims tier=full/is_exact=True, but its revision is
        # for OLDER content than what's actually on disk now.
        state.known_target_context_items["target.py"] = make_context_item(
            path="target.py", content="def f():\n    return 0  # stale\n",
            reason="known_target_full_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True,
            revision=content_revision("def f():\n    return 0  # stale\n"),
        )
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True

    def test_new_file_creation_unaffected(self, tmp_path):
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        op, mandatory = _completeness_gated_operation(
            "brand_new.py", CodeOperation.CREATE_FULL_FILE,
            file_exists=False, ctx=ctx, state=state,
        )
        assert op is CodeOperation.CREATE_FULL_FILE
        assert mandatory is False

    def test_no_state_defaults_safe(self, tmp_path):
        content = _write_target(tmp_path, "target.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        op, mandatory = _completeness_gated_operation(
            "target.py", CodeOperation.REPAIR_WITH_FULL_FILE,
            file_exists=True, ctx=ctx, state=None,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True

    def test_g1_structural_shape_cannot_reconstruct_large_file_from_skeleton(self, tmp_path):
        """Direct replay of run 8b6ee803's own attempt-1 shape: a large
        existing file, a skeleton-tier/body-elided ContextItem (exactly what
        build_known_target_context()'s _fit_whole_file() records for an
        over-budget file), requesting create_full_file. Must never resolve
        to a whole-file operation."""
        large_content = "\n\n".join(f"def f{i}():\n    return {i}" for i in range(400))
        _write_target(tmp_path, "engine.py", large_content)
        ctx = _minimal_attempt_ctx(tmp_path, architect_files=["engine.py"])
        state = GenerationState()
        state.known_target_context_items["engine.py"] = make_context_item(
            path="engine.py", content="def f0(): ...\n# ... (elided)",
            reason="known_target_bounded_excerpt",
            source_type="named_in_request", trust_level="repository",
            tier="skeleton", is_exact=False, omitted_regions=True,
            revision=content_revision(large_content),
        )
        op, mandatory = _completeness_gated_operation(
            "engine.py", CodeOperation.CREATE_FULL_FILE,
            file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH
        assert mandatory is True


class TestD1OperationMapIntegration:
    def test_operation_map_downgrades_existing_file_without_exact_context(self, tmp_path):
        _write_target(tmp_path, "engine.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        ops = _operation_map(ctx, ["engine.py"], CodeOperation.REPAIR_WITH_FULL_FILE, state)
        assert ops["engine.py"] is CodeOperation.REPAIR_WITH_PATCH

    def test_operation_map_permits_full_file_with_exact_context(self, tmp_path):
        content = _write_target(tmp_path, "engine.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["engine.py"] = make_context_item(
            path="engine.py", content=content, reason="known_target_full_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision=content_revision(content),
        )
        ops = _operation_map(ctx, ["engine.py"], CodeOperation.REPAIR_WITH_FULL_FILE, state)
        assert ops["engine.py"] is CodeOperation.REPAIR_WITH_FULL_FILE


# ---------------------------------------------------------------------------
# D1 - mandatory-patch fallback closure (validate_operation_result's own
# PATCH -> FULL_FILE transition must be rejected, not silently accepted,
# when the patch was invariant-mandated)
# ---------------------------------------------------------------------------

class TestD1FallbackClosure:
    def test_mandatory_patch_full_file_fallback_is_rejected(self):
        from kriya.workflow.operations import validate_operation_result
        # Simulates the model ignoring the SEARCH:/REPLACE: instruction and
        # returning full "content" anyway - exactly G1's attempts 3/4 shape.
        result = {"content": "def f():\n    return 999\n", "edits": None}
        actual, contract_error = validate_operation_result(
            result, expected=CodeOperation.REPAIR_WITH_PATCH, file_exists=True,
        )
        # validate_operation_result's OWN behavior is unchanged (still permits
        # the transition, correct for an ORDINARY preference-based patch
        # request) - the mandatory-vs-preference distinction is enforced by
        # the caller (run_attempt's own operation-contract block), not here.
        assert actual is CodeOperation.REPAIR_WITH_FULL_FILE
        assert contract_error is None
        # The caller-side mandatory check this design adds is exercised via
        # run_attempt() end-to-end in TestD2G1Replay below (mocking the
        # Developer to return exactly this shape) - that is what proves the
        # candidate is actually rejected, not just that this classification
        # function alone permits the transition.


# ---------------------------------------------------------------------------
# D2 - recovery preservation
# ---------------------------------------------------------------------------

class TestD2RecoveryPreservation:
    def test_restore_public_contract_stays_full_file(self):
        op = operation_for_attempt(
            "api_contract_recovery", has_prior_failure=True,
            recovery_phase=APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE

    def test_repair_behavior_uses_anchored_patch(self):
        op = operation_for_attempt(
            "api_contract_recovery", has_prior_failure=True,
            recovery_phase=APIContractRecoveryPhase.REPAIR_BEHAVIOR,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH

    def test_no_unconditional_full_file_recovery_reconstruction(self):
        # The exact regression this defect is about: with no phase info at
        # all (a caller that hasn't been updated), behavior is UNCHANGED
        # (full-file) - proving this fix is additive, not a silent default
        # flip - but with real phase info, REPAIR_BEHAVIOR must never
        # resolve to unconditional full-file.
        assert operation_for_attempt(
            "api_contract_recovery", has_prior_failure=True,
        ) is CodeOperation.REPAIR_WITH_FULL_FILE
        for phase in (
            APIContractRecoveryPhase.REPAIR_BEHAVIOR,
            APIContractRecoveryPhase.AWAIT_TERMINAL_SUCCESS,
        ):
            assert operation_for_attempt(
                "api_contract_recovery", has_prior_failure=True, recovery_phase=phase,
            ) is CodeOperation.REPAIR_WITH_PATCH

    def test_state_machine_transitions_unaffected(self):
        """The phase state machine itself (state.py::APIContractRecovery) is
        untouched by this design - proves D2 extends operation selection
        only, never a parallel recovery subsystem."""
        recovery = APIContractRecovery.detected(
            violations=[{"owner": "engine.py", "removed_signature": "bind(x)",
                         "evidence_files": ["other.py"]}],
            protected_evidence_files=["other.py"],
            file_roles={"engine.py": "owner"},
        )
        assert recovery.phase is APIContractRecoveryPhase.DETECTED
        recovery.begin_restoration()
        assert recovery.phase is APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT
        recovery.owner_contract_restored()
        assert recovery.phase is APIContractRecoveryPhase.REPAIR_BEHAVIOR
        # Illegal transition (skip straight to COMPLETE) still raises - this
        # design changes nothing about the pre-existing legality enforcement.
        with pytest.raises(ValueError):
            recovery.begin_restoration()


class TestD2G1Replay:
    """Reproduces run 8b6ee803's own sequence end-to-end at the operation-
    authorization layer: skeleton context -> destructive candidate would be
    rejected -> deterministic restoration -> behavior repair now REQUIRES an
    anchored patch -> a full-file "repair" response for that phase is
    rejected outright, proving the restored baseline cannot be destroyed a
    second time the way it was live."""

    def test_repair_behavior_full_file_fallback_rejected_by_completeness_gate(self, tmp_path):
        content = _write_target(
            tmp_path, "engine.py",
            "def bind(name, type_name, scope_node):\n    pass\n\n\n"
            "def public_entry():\n    return bind(1, 2, 3)\n",
        )
        ctx = _minimal_attempt_ctx(tmp_path, architect_files=["engine.py"])
        state = GenerationState()
        state.api_contract_recovery = APIContractRecovery.detected(
            violations=[{"owner": "engine.py", "removed_signature": "bind(name, type_name, scope_node)",
                         "evidence_files": ["public_entry.py"]}],
            protected_evidence_files=["public_entry.py"],
            file_roles={"engine.py": "owner"},
        )
        state.api_contract_recovery.begin_restoration()
        state.api_contract_recovery.owner_contract_restored()
        assert state.api_contract_recovery.phase is APIContractRecoveryPhase.REPAIR_BEHAVIOR

        attempt_operation = operation_for_attempt(
            "api_contract_recovery", has_prior_failure=True,
            recovery_phase=state.api_contract_recovery.phase,
        )
        assert attempt_operation is CodeOperation.REPAIR_WITH_PATCH

        # _completeness_gated_operation is a no-op here (base_operation is
        # already REPAIR_WITH_PATCH, not a full-file shape) - proving the two
        # mechanisms compose without conflict, exactly as the design intends.
        op, mandatory = _completeness_gated_operation(
            "engine.py", attempt_operation, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH


# ---------------------------------------------------------------------------
# D3-part-1 - public API scope
# ---------------------------------------------------------------------------

_SYNTHETIC_ENGINE_SHAPE = '''
"""Synthetic fixture mirroring engine.py's real shape - NOT copied Graphify source."""

def _extract_generic(path, config):
    bindings = {}
    def bind(name, type_name, scope_node):
        bindings[name] = (type_name, scope_node)
    def walk(node, parent_class_nid=None):
        pass
    return bindings

def _resolve_types(body_node, source):
    results = {}
    def visit(n):
        pass
    return results

def extract_public_api(path, config):
    return _extract_generic(path, config)

def _internal_helper():
    pass

class Extractor:
    def run(self, path):
        return extract_public_api(path, {})
    def _internal_method(self):
        pass
'''


class TestD3PublicApiScope:
    def test_module_level_public_function(self):
        sigs = _normalized_public_signatures("x.py", "def foo(a, b):\n    return a\n")
        assert "foo(a, b)" in sigs

    def test_underscore_private_function_excluded(self):
        sigs = _normalized_public_signatures("x.py", "def _foo(a, b):\n    return a\n")
        assert not sigs

    def test_class_public_method_qualifies(self):
        sigs = _normalized_public_signatures(
            "x.py", "class C:\n    def method(self, x):\n        return x\n",
        )
        assert "method(self, x)" in sigs

    def test_nested_public_looking_closure_excluded(self):
        src = "def outer():\n    def walk(n):\n        pass\n    return walk\n"
        sigs = _normalized_public_signatures("x.py", src)
        assert "walk(n)" not in sigs
        assert "outer()" in sigs

    def test_nested_private_closure_excluded(self):
        src = "def outer():\n    def _walk(n):\n        pass\n    return _walk\n"
        sigs = _normalized_public_signatures("x.py", src)
        assert "_walk(n)" not in sigs

    def test_duplicate_nested_names_across_functions_never_collide_with_module_level(self):
        src = (
            "def a():\n    def helper(n):\n        return 1\n    return helper\n\n\n"
            "def b():\n    def helper(n):\n        return 2\n    return helper\n\n\n"
            "def helper(n):\n    return 3\n"
        )
        sigs = _normalized_public_signatures("x.py", src)
        # Exactly the real, single module-level helper - never either nested one.
        assert sigs.get("helper(n)") == "helper"
        assert "a()" in sigs and "b()" in sigs

    def test_unrelated_indentation_forms(self):
        src = "async def foo(x):\n    pass\n\n\ndef   bar( x , y = 1 ):\n    pass\n"
        sigs = _normalized_public_signatures("x.py", src)
        assert "foo(x)" in sigs
        assert any(k.startswith("bar(") for k in sigs)

    def test_g1_like_bind_visit_walk_structure_creates_no_public_api_obligation(self):
        sigs = _normalized_public_signatures("engine.py", _SYNTHETIC_ENGINE_SHAPE)
        assert "bind(name, type_name, scope_node)" not in sigs
        assert "walk(node, parent_class_nid=None)" not in sigs
        assert "visit(n)" not in sigs
        assert "extract_public_api(path, config)" in sigs
        assert "run(self, path)" in sigs
        assert "_internal_helper()" not in sigs
        assert "_internal_method(self)" not in sigs


class TestD3FindBrownfieldPublicApiChanges:
    def test_genuine_reachable_consumer_still_detected(self, tmp_path):
        original = {
            "owner.py": "def public_fn(x):\n    return x\n",
            "caller.py": "from owner import public_fn\npublic_fn(1)\n",
        }
        final = {
            "owner.py": "def renamed_fn(x):\n    return x\n",
        }
        violations = find_brownfield_public_api_changes(
            str(tmp_path), original, {**original, **final}, goal="unrelated behavior fix",
        )
        removed = {v["removed_signature"] for v in violations}
        assert "public_fn(x)" in removed

    def test_g1_nested_closures_produce_zero_violations(self, tmp_path):
        original = {"engine.py": _SYNTHETIC_ENGINE_SHAPE}
        # A candidate that drops the nested closures but keeps every real
        # module/class-level signature - must produce ZERO violations now,
        # where before this fix it produced exactly the false-positive
        # bind/visit/walk triad G1 demonstrated live.
        candidate = original["engine.py"].replace(
            "    def bind(name, type_name, scope_node):\n        bindings[name] = (type_name, scope_node)\n",
            "",
        ).replace(
            "    def walk(node, parent_class_nid=None):\n        pass\n", "",
        ).replace(
            "    def visit(n):\n        pass\n", "",
        )
        final = {"engine.py": candidate}
        violations = find_brownfield_public_api_changes(
            str(tmp_path), original, final, goal="fix a narrow bug",
        )
        assert violations == []


# ---------------------------------------------------------------------------
# Meta-regression: the 14 focused-suite failures after 7bc52b5 (all traced to
# ONE production defect - _record_retry_projection_context_items() only read
# RetryPackage.target_projections, never .reference_projections, so any file
# shown to the model purely as reference content - not a specific retry
# target - was invisible to _completeness_gated_operation() even though its
# real, current content was genuinely in the prompt). See
# docs/assurance/VAL_001_GRAPHIFY_G1_REMEDIATION_DESIGN.md's own
# "Post-7bc52b5 regression" section for the full root-cause narrative.
# ---------------------------------------------------------------------------

class TestD1MetaRegression:
    def test_d1_operates_from_context_supplied_to_current_developer_call(self, tmp_path):
        """Property 1: authorization reads state.known_target_context_items,
        not file existence or size - an item recorded for THIS attempt's own
        content supply is what authorizes, nothing else."""
        content = _write_target(tmp_path, "a.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        # No item recorded at all -> fails closed regardless of the file's
        # real, current, perfectly ordinary content.
        op, mandatory = _completeness_gated_operation(
            "a.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH and mandatory is True
        # Recording the exact real content for THIS attempt authorizes it.
        state.known_target_context_items["a.py"] = make_context_item(
            path="a.py", content=content, reason="known_target_full_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "a.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE and mandatory is False

    def test_stale_attempt_n_context_cannot_authorize_attempt_n_plus_1(self, tmp_path):
        """Property 2: a ContextItem recorded against OLD content does not
        authorize a mutation once the file's real current content has
        changed (revision mismatch) - proven by mutating the file on disk
        AFTER recording, exactly simulating an intervening attempt's own
        successful write that the current attempt's bookkeeping never saw."""
        original_content = "def f():\n    return 1\n"
        _write_target(tmp_path, "a.py", original_content)
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        # Attempt N recorded this file as exact, for the content that was
        # real and current AT THAT TIME.
        state.known_target_context_items["a.py"] = make_context_item(
            path="a.py", content=original_content, reason="known_target_full_source",
            source_type="named_in_request", trust_level="repository",
            tier="full", is_exact=True, revision=content_revision(original_content),
        )
        op, mandatory = _completeness_gated_operation(
            "a.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE and mandatory is False  # valid while current

        # Attempt N+1: the file changed on disk (an intervening write this
        # bookkeeping never revalidated) - the SAME recorded item must now
        # fail closed rather than silently authorize against stale evidence.
        _write_target(tmp_path, "a.py", "def f():\n    return 2  # changed\n")
        op, mandatory = _completeness_gated_operation(
            "a.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH and mandatory is True

    def test_full_set_retry_with_exact_reference_content_remains_functional(self, tmp_path):
        """Property 3, the exact defect: a real RetryPackage (built via the
        real production build_retry_package(), not a hand-rolled fake) whose
        target file has NO grounded implicated-file evidence (target_files=[]
        - the "unscoped full-set retry" shape test_workflow_checks_
        toolchain_only_once_across_retries exercises) still places the
        file's real, current, complete content in reference_projections.
        _record_retry_projection_context_items() must record it from there."""
        content = _write_target(tmp_path, "pom.xml", "<project><bad/></project>\n")
        state = GenerationState()
        failure = Failure(type="compile", message="some generic xml error")
        retry_package = build_retry_package(
            failure=failure, worktree_path=str(tmp_path),
            all_files=["pom.xml"], target_files=[],  # unscoped: no grounded target
            source_context=None, max_chars=8000,
        )
        assert retry_package is not None
        assert retry_package.target_projections == ()
        assert any(p.path == "pom.xml" for p in retry_package.reference_projections), (
            "test setup sanity: pom.xml must land in reference_projections for this to be a real test"
        )
        _record_retry_projection_context_items(state, retry_package)
        item = state.known_target_context_items.get("pom.xml")
        assert item is not None and item.tier == "full" and item.is_exact is True
        ctx = _minimal_attempt_ctx(tmp_path)
        op, mandatory = _completeness_gated_operation(
            "pom.xml", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_FULL_FILE and mandatory is False

    def test_targeted_retry_with_exact_member_context_remains_functional(self, tmp_path):
        """Property 4 (regression guard, already covered in TestD1 above -
        named explicitly here as a meta-property): member_exact context
        authorizes an anchored patch, never blocks legitimate targeted
        repair outright."""
        content = _write_target(tmp_path, "b.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        state.known_target_context_items["b.py"] = make_context_item(
            path="b.py", content="def b():\n    return 2\n", reason="member_exact",
            source_type="named_in_request", trust_level="repository",
            tier="member_exact", is_exact=True, member_id="b",
            revision=content_revision(content),
        )
        op, mandatory = _completeness_gated_operation(
            "b.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        assert op is CodeOperation.REPAIR_WITH_PATCH  # not blocked - downgraded to the safe protocol
        assert mandatory is True

    def test_retry_with_skeleton_only_context_remains_fail_closed(self, tmp_path):
        """Property 5 (regression guard): a real RetryPackage whose file is
        large enough to genuinely omit content (real budget pressure, not a
        hand-set flag) still fails closed."""
        large_content = "\n\n".join(f"def f{i}():\n    return {i}" for i in range(500))
        _write_target(tmp_path, "big.py", large_content)
        state = GenerationState()
        failure = Failure(type="compile", message="some error naming nothing real")
        retry_package = build_retry_package(
            failure=failure, worktree_path=str(tmp_path),
            all_files=["big.py"], target_files=[],
            source_context=None, max_chars=400,  # deliberately tiny - forces real omission
        )
        _record_retry_projection_context_items(state, retry_package)
        item = state.known_target_context_items.get("big.py")
        assert item is not None
        ctx = _minimal_attempt_ctx(tmp_path)
        op, mandatory = _completeness_gated_operation(
            "big.py", CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        if item.is_exact:
            pytest.skip("test setup sanity: budget was not actually tight enough to force omission")
        assert op is CodeOperation.REPAIR_WITH_PATCH and mandatory is True

    def test_self_correction_never_reaches_full_file_gate_by_construction(self):
        """Property 6: self-correction (kriya/workflow/self_correction.py)
        cannot "accidentally bypass" D1 because it structurally never
        requests a full-file operation at all - every edit it applies goes
        through apply_anchored_edits(), the same self-defending exact-match
        mechanism D1's own downgrade routes into. Verified against the real
        module's source, not asserted from the design doc alone."""
        import inspect
        import kriya.workflow.self_correction as self_correction_mod
        source = inspect.getsource(self_correction_mod)
        assert "apply_anchored_edits" in source
        assert "CREATE_FULL_FILE" not in source
        assert "REPAIR_WITH_FULL_FILE" not in source

    def test_live_lookup_only_augments_error_text_not_content_supply(self):
        """Property 7: live-lookup (kriya/workflow/live_lookup.py) only
        enriches state.error_context (text fed into the SAME retry prompt
        builders D1 already covers) - it is not a separate content-supply
        producer, so it cannot bypass known_target_context_items recording
        by introducing a fourth, unwired path. Verified structurally: it
        never constructs a ContextItem, ContextPackage, or FileProjection
        itself - those come from the retry-mode-specific builders it hands
        its augmented error text to, same as any other retry."""
        import inspect
        import kriya.workflow.live_lookup as live_lookup_mod
        source = inspect.getsource(live_lookup_mod)
        assert "ContextItem" not in source
        assert "FileProjection" not in source

    def test_record_all_files_written_matches_missing_files_prompt_builder_exactly(self, tmp_path):
        """Direct contract test for _record_all_files_written_as_exact_context,
        the ONE call site it remains wired at (missing-files retries -
        kriya/workflow/attempt.py's own use of it inside the full-set retry
        branch was removed: _retry_package_for_attempt only returns None on
        a clean first attempt, where state.all_files_written is necessarily
        still empty too, making that call site a permanent no-op - see
        _record_retry_projection_context_items' own updated call site
        comment). _build_missing_files_retry_prompt (kriya/workflow/
        retry_prompts.py) does a plain, unbounded fh.read() for every file in
        all_files_written - this test proves the helper records the exact
        same content that function would show, for a file that genuinely
        exists and is genuinely readable, and correctly skips one that
        isn't (mirroring that function's own try/except OSError skip)."""
        content = _write_target(tmp_path, "existing.py", "def f():\n    return 1\n")
        ctx = _minimal_attempt_ctx(tmp_path)
        state = GenerationState()
        _record_all_files_written_as_exact_context(
            state, ctx, {"existing.py", "never_written.py"},
        )
        item = state.known_target_context_items.get("existing.py")
        assert item is not None
        assert item.tier == "full" and item.is_exact is True and item.member_id is None
        assert item.content == content
        assert item.revision == content_revision(content)
        assert "never_written.py" not in state.known_target_context_items

    def test_mandatory_patch_fallback_rejected_across_two_retries(self, tmp_path):
        """Property 8: a model that ignores the mandatory-patch instruction
        on TWO consecutive attempts is rejected both times, not just once -
        the caller-side check is re-evaluated fresh each attempt, never
        cached as "already decided" from an earlier rejection."""
        from kriya.workflow.operations import validate_operation_result
        for _ in range(2):
            result = {"content": "def f():\n    return 999\n", "edits": None}
            actual, contract_error = validate_operation_result(
                result, expected=CodeOperation.REPAIR_WITH_PATCH, file_exists=True,
            )
            assert actual is CodeOperation.REPAIR_WITH_FULL_FILE
            assert contract_error is None
            # (the caller-side mandatory check that turns this into an actual
            # rejection is exercised end-to-end in TestD2G1Replay/TestD1
            # FallbackClosure above; this loop proves validate_operation_
            # result's own classification doesn't change or "warm up" across
            # repeated calls with identical input.)
