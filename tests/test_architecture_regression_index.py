"""MA7.5 - index, not a duplicate test suite.

MA6 spec section 72 named 6 permanent architecture-regression categories
("Historical planner/regression failures remain fixed"). Rather than move
or duplicate existing, working tests into one file (real reorganization
risk for zero coverage gain), this file is a discoverability index: each
entry below names the real test(s) that already guard one category, with
enough context that deleting or weakening one of them without noticing its
purpose should trip a reviewer's attention. If you are looking for "is
category X covered," start here before assuming it needs a new test.

1. Single-entrypoint goal stays single-module / doesn't invent a second
   physical build boundary:
     tests/test_milestone_validation.py::test_historical_incident_competing_entrypoints_rejected
     tests/test_milestone_validation.py::test_single_extended_entrypoint_is_valid
     tests/test_milestone_validation.py::test_legitimate_existing_multi_module_repo_allows_composition
     tests/test_repository_topology.py (8 tests - the underlying detection
       detect_repository_topology() these validator tests depend on)

2. Django/Spring/any-framework goal doesn't silently switch build
   ecosystem (MA6 spec's own literal examples: "Django doesn't drift to
   Spring, Python doesn't invent Maven layout") - generalized 2026-08-24
   (MA7.5) to one marker-based rule with no per-framework-pair logic,
   per explicit user direction ("we cannot afford tests for all kinds of
   combinations, the design should handle it"):
     kriya/workflow/static_checks.py::find_established_stack_drift
     tests/test_stack_drift.py (10 tests)
   Real scope boundary, not yet closed: only fires when the workspace
   already has an ESTABLISHED marker (2nd+ milestone or an existing repo) -
   a first-milestone goal-text-vs-generated-language mismatch (nothing
   established yet to contradict) is a materially different, weaker
   signal (keyword-based, not file-existence-based) and remains
   unimplemented. Live-model validation of this check has NOT been done
   yet - the user's own stated expectation is that this is exactly the
   kind of thing likely to surface something under a real model.

3. Dependency/import preservation across milestones (an earlier
   milestone's file must stay visible/correct to later ones):
     tests/test_workflow.py::test_run_attempt_raises_cross_package_mismatch_end_to_end
     tests/test_workflow.py::test_run_attempt_cross_package_mismatch_fires_without_any_dependency_graph_indexing
     tests/test_workflow.py::test_run_attempt_includes_established_files_in_compile_check
     tests/test_workflow.py::test_run_attempt_includes_established_files_in_self_correction_scope
     tests/test_workflow.py::test_workflow_run_verifier_judge_sees_established_files_too
     tests/test_workflow.py (find_cross_package_symbol_mismatch* cluster, ~8 tests)
   Real historical incidents behind these: durable lessons in
   kriya_backlog_and_lessons.md's memory, commits a1f6a10/0a390a3/etc.

4. LLM egress stays local-only (no remote endpoint reachable regardless
   of config):
     tests/test_llm_egress_policy_integration.py (9 tests)
     tests/test_learn_egress_policy.py (1 test)
   Hard boundary independent of ExecutionPolicy's audit/enforce state -
   kriya/core/llm.py's is_local_url/EgressViolationError, see that
   module's own docstring.

5. Full regression suite stays unconditional (the mocked suite is never
   silently skipped or made opt-in):
     pyproject.toml's own `addopts = '-m "not live_model"'` - excludes
     ONLY the explicitly live_model-marked tier by design (see CLAUDE.md's
     "Live-model CI tier" section); every other test always runs.
     .github/workflows/ci.yml's `test` job - no live_model tier caveat.
   Not a python test to guard with an assertion - a CI/tooling-config
   invariant. Check pyproject.toml/ci.yml directly if this is ever in
   doubt, don't assume a test file covers it.

6. Workspace-state cross-contamination (a run for one workspace must
   never silently corrupt a DIFFERENT project's/the shared install's own
   state) - CLOSED 2026-08-24 (MA7.4):
     kriya/skills/skill.py::is_accidental_shared_skill_write
     tests/test_shared_skills_dir_guard.py (20 tests, including the 4 new
       MA7.4 ones specifically reproducing durable lesson #4's real
       incident and confirming it's now hard-refused, not just warned)

Self-verifying, not just narrative: a smoke check below confirms every
named function above still exists by that name, so a careless rename/
removal fails loudly here instead of silently rotting this index.
"""
import importlib


def test_every_named_regression_guard_still_exists():
    checks = [
        ("kriya.workflow.repository_topology", "detect_repository_topology"),
        ("kriya.workflow.milestone_validation", "MilestonePlanValidator"),
        ("kriya.workflow.static_checks", "find_established_stack_drift"),
        ("kriya.core.llm", "is_local_url"),
        ("kriya.skills.skill", "is_accidental_shared_skill_write"),
    ]
    missing = []
    for module_name, attr_name in checks:
        module = importlib.import_module(module_name)
        if not hasattr(module, attr_name):
            missing.append(f"{module_name}.{attr_name}")
    assert not missing, f"Named regression guard(s) no longer exist - update this index: {missing}"


def test_every_named_regression_test_file_still_exists():
    import os
    test_dir = os.path.dirname(__file__)
    for filename in (
        "test_milestone_validation.py", "test_repository_topology.py",
        "test_stack_drift.py", "test_workflow.py",
        "test_llm_egress_policy_integration.py", "test_learn_egress_policy.py",
        "test_shared_skills_dir_guard.py",
    ):
        assert os.path.isfile(os.path.join(test_dir, filename)), f"{filename} referenced by this index is missing"
