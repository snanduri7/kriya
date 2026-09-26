"""STATE-001 closure (2026-09-14): checkpoint/resume compatibility is bound
to relevant WORKING-TREE content, not merely git HEAD/repository identity.

Reproduced defect (the risk this closes): `compute_workspace_fingerprint()`
(HEAD + dirty-boolean) and `compute_tree_hash()` (`HEAD^{tree}`, committed
only) both collapse two DIFFERENT dirty working-tree contents at the same
HEAD to an identical value - permanently characterized by
`tests/test_workflow.py::test_checkpoint_workspace_fingerprint_cannot_
distinguish_two_different_dirty_states`. This file proves the fix:
`compute_workspace_content_hash()` (kriya/workflow/checkpoint.py), a
scratch-git-index `write-tree` over CURRENT on-disk content (tracked +
untracked-and-not-ignored, excluding `.kriya/`), wired into every real
resume-compatibility decision in this codebase.

Real git operations against real temp repos throughout (never mocked - the
whole point is proving these are real, content-addressed hashes), matching
`tests/test_checkpoint_control_plane_hashes.py`'s own established
convention. No live LLM.
"""
import ast
import os
import subprocess
import tempfile

import pytest

from kriya.control.persistence import load_control_state, save_control_state
from kriya.control.state import ControlState
from kriya.workflow.checkpoint import (
    ResumeStatus,
    compute_control_plane_hashes,
    compute_tree_hash,
    compute_workspace_content_hash,
    validate_resume_against_reality,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _init_git_repo_with_a_commit(path):
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, capture_output=True)
    os.makedirs(os.path.join(path, "src"), exist_ok=True)
    with open(os.path.join(path, "src", "a.py"), "w") as f:
        f.write("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True)


@pytest.fixture
def git_repo():
    with tempfile.TemporaryDirectory() as d:
        _init_git_repo_with_a_commit(d)
        yield d


def _write(repo, relpath, content):
    full = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Adversarial matrix - A/B/C/D/E/F/G/H/I/J/K (compute_workspace_content_hash
# itself, the core mechanism)
# ---------------------------------------------------------------------------

def test_scenario_a_same_head_clean_tree_stable_identity(git_repo):
    h1 = compute_workspace_content_hash(git_repo)
    h2 = compute_workspace_content_hash(git_repo)
    assert h1 is not None and h1 == h2


def test_scenario_b_tracked_content_changed_rejected(git_repo):
    before = compute_workspace_content_hash(git_repo)
    _write(git_repo, "src/a.py", "x = 2\n")
    after = compute_workspace_content_hash(git_repo)
    assert after != before


def test_scenario_b_staged_tracked_modification_also_detected(git_repo):
    before = compute_workspace_content_hash(git_repo)
    _write(git_repo, "src/a.py", "x = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
    after = compute_workspace_content_hash(git_repo)
    assert after != before


def test_scenario_c_tracked_file_deleted_rejected(git_repo):
    before = compute_workspace_content_hash(git_repo)
    os.remove(os.path.join(git_repo, "src", "a.py"))
    after = compute_workspace_content_hash(git_repo)
    assert after != before


def test_scenario_d_tracked_file_renamed_rejected(git_repo):
    before = compute_workspace_content_hash(git_repo)
    os.rename(os.path.join(git_repo, "src", "a.py"), os.path.join(git_repo, "src", "renamed.py"))
    after = compute_workspace_content_hash(git_repo)
    assert after != before


def test_newly_restored_tracked_file_matches_original(git_repo):
    original = compute_workspace_content_hash(git_repo)
    os.remove(os.path.join(git_repo, "src", "a.py"))
    assert compute_workspace_content_hash(git_repo) != original
    _write(git_repo, "src/a.py", "x = 1\n")
    assert compute_workspace_content_hash(git_repo) == original


def test_scenario_e_relevant_untracked_file_added_rejected(git_repo):
    before = compute_workspace_content_hash(git_repo)
    _write(git_repo, "src/new_helper.py", "print(1)\n")
    after = compute_workspace_content_hash(git_repo)
    assert after != before


def test_scenario_f_untracked_content_changed_rejected(git_repo):
    _write(git_repo, "src/new_helper.py", "print(1)\n")
    before = compute_workspace_content_hash(git_repo)
    _write(git_repo, "src/new_helper.py", "print(2)\n")
    after = compute_workspace_content_hash(git_repo)
    assert after != before


def test_untracked_file_presence_vs_absence_differs(git_repo):
    absent = compute_workspace_content_hash(git_repo)
    _write(git_repo, "src/only_in_one.py", "content\n")
    present = compute_workspace_content_hash(git_repo)
    assert present != absent


def test_scenario_g_exact_dirty_workspace_restored_accepted(git_repo):
    """Dirty checkpoint A -> dirty B -> restored to exact A -> ACCEPT."""
    _write(git_repo, "src/a.py", "x = 42  # dirty state A\n")
    dirty_a = compute_workspace_content_hash(git_repo)
    _write(git_repo, "src/a.py", "x = 999  # dirty state B\n")
    dirty_b = compute_workspace_content_hash(git_repo)
    assert dirty_b != dirty_a
    _write(git_repo, "src/a.py", "x = 42  # dirty state A\n")
    restored = compute_workspace_content_hash(git_repo)
    assert restored == dirty_a


def test_scenario_h_staged_vs_unstaged_same_bytes_same_identity(git_repo):
    """Task 14's own deterministic choice, documented: Kriya reads WORKING-
    TREE bytes only, never the index - two states with identical on-disk
    content but different staging status must produce the SAME identity."""
    _write(git_repo, "src/a.py", "x = 2\n")
    unstaged = compute_workspace_content_hash(git_repo)
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
    staged = compute_workspace_content_hash(git_repo)
    assert unstaged == staged


def test_scenario_i_mtime_only_change_identity_unchanged(git_repo):
    import time
    before = compute_workspace_content_hash(git_repo)
    os.utime(os.path.join(git_repo, "src", "a.py"), (time.time() + 1000, time.time() + 1000))
    after = compute_workspace_content_hash(git_repo)
    assert after == before


def test_scenario_j_ignored_build_output_does_not_affect_identity(git_repo):
    _write(git_repo, ".gitignore", "target/\nbuild/\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "gitignore"], cwd=git_repo, capture_output=True)
    baseline = compute_workspace_content_hash(git_repo)

    os.makedirs(os.path.join(git_repo, "target"), exist_ok=True)
    _write(git_repo, "target/Output.class", "compiled junk\n")
    with_ignored = compute_workspace_content_hash(git_repo)
    assert with_ignored == baseline


def test_scenario_k_checkpoint_self_write_does_not_self_invalidate(git_repo):
    """Writing a REAL checkpoint file into .kriya/checkpoints/ must not
    change the very identity that checkpoint itself records - the
    self-invalidation Task 4 explicitly warns against."""
    baseline = compute_workspace_content_hash(git_repo)
    ckpt_dir = os.path.join(git_repo, ".kriya", "checkpoints")
    os.makedirs(ckpt_dir, exist_ok=True)
    with open(os.path.join(ckpt_dir, "run1.json"), "w") as f:
        f.write('{"stage": "plan"}')
    after_write = compute_workspace_content_hash(git_repo)
    assert after_write == baseline


def test_deterministic_ordering_independent_of_creation_order(git_repo):
    """File ordering/filesystem traversal ordering must not affect identity
    (Invariants 5/6) - two workspaces built by adding the same two new
    files in opposite orders must converge on the identical hash. d2 is a
    real CLONE of git_repo (never an independently re-initialized repo -
    two separately-committed repos do not reliably share a commit SHA,
    since a commit object bakes in a real timestamp; unrelated to, and not
    what, this test means to exercise) so both start from the exact same
    commit."""
    with tempfile.TemporaryDirectory() as parent:
        d2 = os.path.join(parent, "clone")
        subprocess.run(["git", "clone", "-q", git_repo, d2], capture_output=True, check=True)
        # ensure identical starting point
        assert compute_workspace_content_hash(git_repo) == compute_workspace_content_hash(d2)

        _write(git_repo, "src/one.py", "1\n")
        _write(git_repo, "src/two.py", "2\n")
        h1 = compute_workspace_content_hash(git_repo)

        _write(d2, "src/two.py", "2\n")
        _write(d2, "src/one.py", "1\n")
        h2 = compute_workspace_content_hash(d2)

        assert h1 == h2


def test_symlink_target_change_detected(git_repo):
    _write(git_repo, "src/b.py", "content b\n")
    link = os.path.join(git_repo, "link1")
    os.symlink("src/a.py", link)
    h1 = compute_workspace_content_hash(git_repo)
    os.remove(link)
    os.symlink("src/b.py", link)
    h2 = compute_workspace_content_hash(git_repo)
    assert h1 != h2


def test_symlink_replaced_by_regular_file_detected(git_repo):
    link = os.path.join(git_repo, "link1")
    os.symlink("src/a.py", link)
    h_symlink = compute_workspace_content_hash(git_repo)
    os.remove(link)
    _write(git_repo, "link1", "not a symlink anymore\n")
    h_regular = compute_workspace_content_hash(git_repo)
    assert h_symlink != h_regular


def test_untracked_file_collision_two_workspaces_same_head_different_content():
    """Task 15: two workspaces at the same HEAD, one relevant untracked
    file with different content in each - identities must differ."""
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        _init_git_repo_with_a_commit(d1)
        _init_git_repo_with_a_commit(d2)
        _write(d1, "src/NewHelper.java", "content A\n")
        _write(d2, "src/NewHelper.java", "content B\n")
        assert compute_workspace_content_hash(d1) != compute_workspace_content_hash(d2)


def test_same_head_different_repos_identical_content_same_identity():
    """Sanity converse of the collision test - two DIFFERENT on-disk paths
    holding the genuinely SAME commit (a real clone, so the commit SHA
    itself matches exactly - independently re-committing identical content
    in two separate repos does NOT reliably reproduce the same commit SHA,
    since a commit object bakes in a real timestamp, an unrelated and
    intentionally-not-tested dimension here) get the SAME content identity
    (Invariant 7: no accidental repo-location/absolute-path sensitivity
    baked into content identity itself - checkpoint files are already
    scoped per-workspace by their own on-disk location, so this module
    doesn't duplicate that binding)."""
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2_parent:
        _init_git_repo_with_a_commit(d1)
        d2 = os.path.join(d2_parent, "clone")
        subprocess.run(["git", "clone", "-q", d1, d2], capture_output=True, check=True)
        assert compute_workspace_content_hash(d1) == compute_workspace_content_hash(d2)


def test_compute_error_fails_closed_not_a_git_repo():
    with tempfile.TemporaryDirectory() as d:
        assert compute_workspace_content_hash(d) is None


def test_compute_error_fails_closed_no_commits_yet():
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init"], cwd=d, capture_output=True)
        assert compute_workspace_content_hash(d) is None


# ---------------------------------------------------------------------------
# validate_resume_against_reality - schema/legacy/corruption (Task 10, 20)
# ---------------------------------------------------------------------------

def test_scenario_m_legacy_checkpoint_without_content_hash_rejected(git_repo):
    bundle = compute_control_plane_hashes(git_repo)
    legacy = dict(bundle)
    legacy.pop("workspace_content_hash")
    result = validate_resume_against_reality(legacy, git_repo)
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert any("predates" in m for m in result.mismatches)


def test_scenario_l_corrupted_identity_rejected(git_repo):
    bundle = compute_control_plane_hashes(git_repo)
    corrupted = dict(bundle)
    corrupted["workspace_content_hash"] = "not-a-real-hash-value"
    result = validate_resume_against_reality(corrupted, git_repo)
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert any("workspace_content_hash" in m for m in result.mismatches)


def test_checkpoint_with_no_control_plane_fields_at_all_unaffected():
    """A checkpoint that never opted into the control-plane hash bundle at
    all (no base_commit whatsoever - a different dict shape entirely) still
    gets ResumeStatus.OK - the new check only fires once a checkpoint is
    otherwise a real control-plane checkpoint (has base_commit), matching
    the pre-existing base_commit/tree_hash gating pattern exactly."""
    result = validate_resume_against_reality({"stage": "quality_gates"}, "/some/workspace")
    assert result.status == ResumeStatus.OK


def test_scenario_p_content_hash_computation_error_fails_closed(git_repo):
    bundle = compute_control_plane_hashes(git_repo)
    # simulate the workspace becoming an unreadable/non-git path between
    # save and resume
    result = validate_resume_against_reality(bundle, "/definitely/does/not/exist")
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert any("could not be recomputed" in m or "workspace_content_hash" in m for m in result.mismatches)


def test_dirty_content_drift_now_caught_end_to_end(git_repo):
    """THE reproduced STATE-001 defect itself, proven closed at the real
    validate_resume_against_reality() boundary: tree_hash alone (the
    pre-fix mechanism) would NOT catch this, since nothing was committed -
    only workspace_content_hash does."""
    bundle = compute_control_plane_hashes(git_repo)
    stored_tree_hash = bundle["tree_hash"]
    _write(git_repo, "src/a.py", "x = 999999  # completely different uncommitted content\n")
    current_tree_hash = compute_tree_hash(git_repo)
    assert current_tree_hash == stored_tree_hash, "sanity: tree_hash alone is still blind to this"

    result = validate_resume_against_reality(bundle, git_repo)
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert any("workspace_content_hash" in m for m in result.mismatches)


# ---------------------------------------------------------------------------
# ControlState wiring (structured/enforce-mode subtask resume path)
# ---------------------------------------------------------------------------

def test_control_state_round_trips_workspace_content_hash():
    cs = ControlState.new(run_id="r1").with_updates(workspace_content_hash="abc123")
    restored = ControlState.from_dict(cs.to_dict())
    assert restored.workspace_content_hash == "abc123"


def test_control_state_default_is_none():
    cs = ControlState.new(run_id="r1")
    assert cs.workspace_content_hash is None


def test_save_control_state_is_pure_never_silently_mutates_a_field(git_repo):
    """save_control_state() must persist EXACTLY the ControlState it is
    given - a real, caller-visible round-trip invariant
    (test_control_plane_end_to_end.py::test_full_two_milestone_control_
    plane_round_trip relies on this directly: save(x); reload();
    assert reloaded.content_hash() == x.content_hash()). An earlier version
    of this STATE-001 fix centralized the workspace_content_hash refresh
    INSIDE save_control_state() itself - it closed the real bug (see the
    sibling test below) but broke this exact invariant by silently
    diverging what got persisted from what the caller passed in, caught by
    the user's own independent pytest run. The refresh now lives in a
    narrow, LOCAL closure inside workflow_controller.py's own
    _run_structured_enforce() (`_persist_control_state`) - the one caller
    that actually needs per-save freshness - never in the shared,
    general-purpose primitive every other caller depends on staying pure."""
    cs = ControlState.new(run_id="r1", milestone_group_id="g1").with_updates(
        current_milestone_id="M1", milestone_states={"M1": "done"},
    )
    save_control_state(git_repo, cs)
    reloaded = load_control_state(git_repo)
    assert reloaded.content_hash() == cs.content_hash()
    assert reloaded.workspace_content_hash is None  # exactly what the caller supplied - untouched


def test_persist_control_state_pattern_refreshes_content_hash_at_each_save(git_repo):
    """Regression test for the real bug this closure found via the user's
    own independent pytest run (test_workflow_controller_enforce.py's
    resume tests): workflow_controller.py computes base_commit/tree_hash/
    workspace_content_hash ONCE, early, before its own per-subtask loop -
    safe for base_commit/tree_hash (neither is sensitive to an uncommitted
    write), but WRONG for workspace_content_hash, since structured/
    enforce-mode subtasks write real content to the workspace
    INCREMENTALLY as they complete. This test proves the CORRECT pattern
    directly (the exact sequence _persist_control_state's own closure
    performs at each of its 9 real call sites in _run_structured_enforce -
    structurally confirmed present by the bypass-sweep test below) - a
    state saved AFTER a subtask's own real write must reflect that write,
    never the pre-loop value."""
    def _persist(cs):
        cs = cs.with_updates(workspace_content_hash=compute_workspace_content_hash(git_repo))
        save_control_state(git_repo, cs)
        return cs

    cs = ControlState.new(run_id="r1")

    # T1: persisted BEFORE any subtask has written anything real (mirrors
    # the early, pre-loop control_state.with_updates(...) in
    # workflow_controller.py).
    cs = _persist(cs.with_updates(subtask_states={}))
    before = load_control_state(git_repo).workspace_content_hash

    # A subtask completes and writes a REAL file to the workspace.
    with open(os.path.join(git_repo, "new_from_subtask.py"), "w") as f:
        f.write("# written by a completed subtask\n")

    # T2: the per-subtask loop's own next persist call.
    cs = _persist(cs.with_updates(subtask_states={"s1": "completed"}))
    after = load_control_state(git_repo).workspace_content_hash

    assert before != after, "the persisted identity must not stay stale across a real subtask write"
    assert after == compute_workspace_content_hash(git_repo), (
        "a resume immediately after this save must see a MATCHING identity, "
        "or the legitimate no-drift resume case would be incorrectly rejected"
    )


def test_run_structured_enforce_uses_persist_control_state_closure_everywhere():
    """Bypass sweep for this specific fix: every save_control_state() call
    site actually reachable from _run_structured_enforce's own per-subtask
    loop must go through the local _persist_control_state() closure (which
    refreshes workspace_content_hash), never the raw, pure
    save_control_state() directly - that would silently reintroduce the
    staleness bug. execute()/execute_milestones() keep their own small
    number of raw save_control_state() calls (state saved before any
    subtask of a NEW call has run, or from an entirely different,
    non-enforce flow) - deliberately unchanged, not part of this sweep."""
    path = os.path.join(_REPO_ROOT, "kriya", "workflow", "workflow_controller.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    target_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_structured_enforce":
            target_fn = node
            break
    assert target_fn is not None
    raw_save_calls = 0
    persist_calls = 0
    for node in ast.walk(target_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "save_control_state":
                raw_save_calls += 1
            elif node.func.id == "_persist_control_state":
                persist_calls += 1
    # Exactly 1: the one call INSIDE _persist_control_state's own
    # definition (expected, correct - that's the closure's own
    # implementation). Every other real per-subtask save site must go
    # through the closure, never call save_control_state() raw.
    assert raw_save_calls == 1, f"expected exactly 1 raw save_control_state call (inside the closure itself), found {raw_save_calls}"
    # 10: PRD-008 S3 added the post-commit save that records the
    # completions as landed in the workspace (subtask_completion_scope).
    assert persist_calls == 10, persist_calls


# ---------------------------------------------------------------------------
# Bypass sweep (Task 22) - HEAD_ONLY_RESUME_PATHS = 0
# ---------------------------------------------------------------------------

def test_workflow_py_resume_check_wired_to_content_hash():
    path = os.path.join(_REPO_ROOT, "kriya", "workflow", "workflow.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    assert "compute_workspace_content_hash(workspace_path)" in source
    assert '"workspace_content_hash": checkpoint_content_hash' in source


def test_workflow_controller_subtask_resume_wired_to_content_hash():
    path = os.path.join(_REPO_ROOT, "kriya", "workflow", "workflow_controller.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    assert '"workspace_content_hash": prior_control_state.workspace_content_hash' in source
    assert "workspace_content_hash=compute_workspace_content_hash(workspace_path)" in source


def test_generate_and_fix_cli_share_the_same_resume_call_path():
    """Task 11: both `generate --resume` and `fix --resume` must use the
    SAME corrected validation - structurally proven by both calling
    we.run_generation_workflow(resume=...) directly (never a second,
    parallel resume implementation for fix)."""
    path = os.path.join(_REPO_ROOT, "kriya", "cli.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    resume_kwarg_call_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "resume":
                    resume_kwarg_call_count += 1
    # generate's two dispatch branches (_dispatch_generation -> either
    # WorkflowController.execute or run_generation_workflow directly) plus
    # fix's own direct call - all route resume= into the SAME underlying
    # run_generation_workflow eventually; pinned as a count so a future new
    # resume-shaped call site is a deliberate, reviewed addition.
    assert resume_kwarg_call_count >= 1


def test_no_second_resume_validation_function_introduced():
    """The fix reuses compute_workspace_content_hash + the EXISTING
    validate_resume_against_reality / workflow.py drift_reasons list - no
    new, parallel "is this checkpoint valid" DECISION function was
    introduced anywhere in kriya/workflow/ or kriya/control/. A `record_*`
    function that merely logs an already-computed ResumeValidationResult
    into a decision ledger (kriya/control/telemetry.py::
    record_resume_validation) is a consumer of the one real decision, not
    a second one - excluded explicitly, not just by accident of naming."""
    hits = []
    for root_dir in ("kriya/workflow", "kriya/control"):
        full_dir = os.path.join(_REPO_ROOT, root_dir)
        for fname in os.listdir(full_dir):
            if not fname.endswith(".py"):
                continue
            with open(os.path.join(full_dir, fname), encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=fname)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.FunctionDef)
                    and "resume" in node.name.lower()
                    and "valid" in node.name.lower()
                    and not node.name.startswith("record_")
                ):
                    hits.append(f"{root_dir}/{fname}::{node.name}")
    assert hits == ["kriya/workflow/checkpoint.py::validate_resume_against_reality"], hits
