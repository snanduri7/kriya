"""CONC-001 - repository/workspace run-ownership guard.

Two layers of coverage:
  1. `kriya.control.run_ownership.acquire_run_lock` itself - real OS-level
     flock semantics, exercised across real separate processes (never a mock
     of `flock` itself; mocking is only used for targeted error injection).
  2. CLI wiring (`generate`/`fix`/`proposal execute`/milestone execution) -
     that each mutating command actually acquires the same guard before it
     can mutate anything, and that read-only commands never do.

Cross-process tests use `multiprocessing.get_context("fork")` deliberately
(not the platform default, "spawn" on macOS) - fork does not re-import this
test module in the child, avoiding re-execution of every top-level test
collection; this is a local test-harness choice, unrelated to the module
under test, which never touches multiprocessing itself.
"""
import asyncio
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.control.run_coordinator import (
    InvalidRunContextError,
    begin_mutating_run,
    coordinated_mutation,
    current_run_context,
    require_mutating_run,
)
from kriya.control.run_ownership import WorkspaceLockHeldError, acquire_run_lock
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController
from _strict_doubles import strict_kernel

MP = multiprocessing.get_context("fork")


@coordinated_mutation
async def _direct_mutation_probe(workspace_path, observed):
    observed.append(current_run_context())
    return "mutated"


# ---------------------------------------------------------------------------
# Layer 1: the primitive itself
# ---------------------------------------------------------------------------

def test_acquire_and_release_roundtrip(tmp_path):
    with acquire_run_lock(str(tmp_path)) as run_id:
        assert isinstance(run_id, str) and run_id
    assert os.path.exists(tmp_path / ".kriya" / "run.lock")


# ---------------------------------------------------------------------------
# Layer 2: RunCoordinator and mandatory public API ownership gateway
# ---------------------------------------------------------------------------

def test_begin_mutating_run_builds_workspace_bound_context(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("v1\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    expected_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    with begin_mutating_run(str(tmp_path), run_id="coordinated-run") as context:
        assert context.run_id == "coordinated-run"
        assert context.workspace_path == os.path.realpath(tmp_path)
        assert context.base_revision == expected_head
        assert context.active
        assert current_run_context() is context
        assert require_mutating_run(str(tmp_path)) is context

    assert not context.active
    assert current_run_context() is None
    with pytest.raises(InvalidRunContextError, match="expired"):
        require_mutating_run(str(tmp_path), context)


@pytest.mark.parametrize("failure", [RuntimeError("boom"), KeyboardInterrupt()])
def test_coordinator_releases_on_exception_and_keyboard_interrupt(tmp_path, failure):
    with pytest.raises(type(failure)):
        with begin_mutating_run(str(tmp_path)):
            raise failure
    with begin_mutating_run(str(tmp_path)):
        pass


def test_direct_mutation_adapter_acquires_context_and_expires_it(tmp_path):
    observed = []
    assert asyncio.run(_direct_mutation_probe(str(tmp_path), observed)) == "mutated"
    assert len(observed) == 1
    assert observed[0].workspace_path == os.path.realpath(tmp_path)
    assert not observed[0].active


def test_nested_mutation_boundary_reuses_one_run_context(tmp_path):
    observed = []
    with begin_mutating_run(str(tmp_path), run_id="outer") as outer:
        asyncio.run(_direct_mutation_probe(str(tmp_path), observed))
        assert observed == [outer]
        assert outer.active


def test_explicit_context_cannot_authorize_another_workspace(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with begin_mutating_run(str(tmp_path)) as context:
        with pytest.raises(InvalidRunContextError, match="different workspace"):
            asyncio.run(
                _direct_mutation_probe(
                    str(other), [], run_context=context,
                )
            )


def test_direct_workflow_engine_api_cannot_bypass_held_lock(tmp_path):
    with acquire_run_lock(str(tmp_path)):
        with pytest.raises(WorkspaceLockHeldError):
            asyncio.run(
                WorkflowEngine.run_generation_workflow(
                    MagicMock(), "goal", str(tmp_path)
                )
            )


def test_direct_workflow_controller_api_cannot_bypass_held_lock(tmp_path):
    controller = WorkflowController(MagicMock())
    with acquire_run_lock(str(tmp_path)):
        with pytest.raises(WorkspaceLockHeldError):
            asyncio.run(controller.execute("goal", str(tmp_path)))


def test_reacquire_after_release_succeeds(tmp_path):
    with acquire_run_lock(str(tmp_path)) as run_id1:
        pass
    with acquire_run_lock(str(tmp_path)) as run_id2:
        assert run_id2 != run_id1


def test_lock_file_contains_diagnostic_metadata(tmp_path):
    with acquire_run_lock(str(tmp_path), run_id="fixed-run-id"):
        payload = json.loads((tmp_path / ".kriya" / "run.lock").read_text())
    assert payload["run_id"] == "fixed-run-id"
    assert payload["pid"] == os.getpid()
    assert "hostname" in payload
    assert "acquired_at" in payload
    assert payload["_workspace"]["workspace_id"]  # reused workspace_identity() stamp


def test_normal_exception_inside_with_block_releases_lock(tmp_path):
    with pytest.raises(ValueError):
        with acquire_run_lock(str(tmp_path)):
            raise ValueError("boom")
    with acquire_run_lock(str(tmp_path)):
        pass  # would raise WorkspaceLockHeldError if the prior lock leaked


def test_two_different_workspaces_do_not_contend(tmp_path):
    ws1 = tmp_path / "a"
    ws2 = tmp_path / "b"
    ws1.mkdir()
    ws2.mkdir()
    with acquire_run_lock(str(ws1)):
        with acquire_run_lock(str(ws2)):
            pass  # both held simultaneously - no exception


def test_permission_failure_fails_closed(tmp_path):
    locked_dir = tmp_path / ".kriya"
    locked_dir.mkdir()
    os.chmod(locked_dir, 0o000)
    try:
        with pytest.raises(PermissionError):
            with acquire_run_lock(str(tmp_path)):
                pass
    finally:
        os.chmod(locked_dir, 0o755)


def test_corrupt_diagnostic_content_does_not_block_uncontended_acquisition(tmp_path):
    (tmp_path / ".kriya").mkdir()
    (tmp_path / ".kriya" / "run.lock").write_text("{not valid json at all")
    with acquire_run_lock(str(tmp_path)):
        pass  # must not raise despite unparsable pre-existing content


def test_relative_path_alias_contends_with_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def _hold(ready_evt, release_evt):
        with acquire_run_lock(os.path.abspath(str(tmp_path))):
            ready_evt.set()
            release_evt.wait(timeout=10)

    ready = MP.Event()
    release = MP.Event()
    p = MP.Process(target=_hold, args=(ready, release))
    p.start()
    try:
        assert ready.wait(timeout=10), "child never signaled it held the lock"
        with pytest.raises(WorkspaceLockHeldError):
            with acquire_run_lock("."):  # relative-path alias to the same real dir
                pass
    finally:
        release.set()
        p.join(timeout=10)
    assert p.exitcode == 0


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink not supported on this platform")
def test_symlink_alias_contends_with_real_path(tmp_path):
    real_ws = tmp_path / "real"
    real_ws.mkdir()
    alias_ws = tmp_path / "alias"
    os.symlink(real_ws, alias_ws)

    def _hold(path, ready_evt, release_evt):
        with acquire_run_lock(path):
            ready_evt.set()
            release_evt.wait(timeout=10)

    ready = MP.Event()
    release = MP.Event()
    p = MP.Process(target=_hold, args=(str(real_ws), ready, release))
    p.start()
    try:
        assert ready.wait(timeout=10)
        with pytest.raises(WorkspaceLockHeldError):
            with acquire_run_lock(str(alias_ws)):
                pass
    finally:
        release.set()
        p.join(timeout=10)
    assert p.exitcode == 0


def test_real_two_process_contention_second_process_rejected(tmp_path):
    def _hold(path, ready_evt, release_evt):
        with acquire_run_lock(path):
            ready_evt.set()
            release_evt.wait(timeout=10)

    ready = MP.Event()
    release = MP.Event()
    p = MP.Process(target=_hold, args=(str(tmp_path), ready, release))
    p.start()
    try:
        assert ready.wait(timeout=10)
        with pytest.raises(WorkspaceLockHeldError) as excinfo:
            with acquire_run_lock(str(tmp_path)):
                pass
        assert "Kriya" in str(excinfo.value)
    finally:
        release.set()
        p.join(timeout=10)
    assert p.exitcode == 0
    # released cleanly afterward
    with acquire_run_lock(str(tmp_path)):
        pass


def test_contention_fails_promptly_not_blocking(tmp_path):
    def _hold(path, ready_evt, release_evt):
        with acquire_run_lock(path):
            ready_evt.set()
            release_evt.wait(timeout=10)

    ready = MP.Event()
    release = MP.Event()
    p = MP.Process(target=_hold, args=(str(tmp_path), ready, release))
    p.start()
    try:
        assert ready.wait(timeout=10)
        t0 = time.monotonic()
        with pytest.raises(WorkspaceLockHeldError):
            with acquire_run_lock(str(tmp_path)):
                pass
        assert time.monotonic() - t0 < 1.0, "acquisition attempt blocked instead of failing immediately"
    finally:
        release.set()
        p.join(timeout=10)


def test_owner_sigkill_releases_lock_for_next_acquirer(tmp_path):
    def _hold_forever(path, ready_evt):
        with acquire_run_lock(path):
            ready_evt.set()
            time.sleep(30)

    ready = MP.Event()
    p = MP.Process(target=_hold_forever, args=(str(tmp_path), ready))
    p.start()
    assert ready.wait(timeout=10)
    os.kill(p.pid, signal.SIGKILL)
    p.join(timeout=10)
    assert p.exitcode is not None and p.exitcode != 0
    # No stale-PID/heartbeat wait needed - the kernel already released it.
    with acquire_run_lock(str(tmp_path)):
        pass


def test_pid_reuse_is_never_consulted_for_the_ownership_decision(tmp_path):
    """A dead process's PID being coincidentally reused by an unrelated,
    live process must never cause a false 'still owned' rejection - because
    ownership is proven by the kernel flock, not by comparing PID values."""
    def _hold_forever(path, ready_evt):
        with acquire_run_lock(path):
            ready_evt.set()
            time.sleep(30)

    ready = MP.Event()
    p = MP.Process(target=_hold_forever, args=(str(tmp_path), ready))
    p.start()
    assert ready.wait(timeout=10)
    dead_pid = p.pid
    os.kill(dead_pid, signal.SIGKILL)
    p.join(timeout=10)

    # Simulate PID reuse: forge a lock-file payload naming the now-dead PID,
    # but do NOT actually hold the flock (nothing does, it was released).
    lock_path = tmp_path / ".kriya" / "run.lock"
    lock_path.write_text(json.dumps({"run_id": "stale", "pid": dead_pid, "acquired_at": time.time()}))

    # A real process may now legitimately be running under that same PID
    # (the OS is free to reuse it) - acquisition must succeed regardless,
    # since the module never reads "pid" to decide anything.
    with acquire_run_lock(str(tmp_path)):
        pass


def test_two_git_worktrees_do_not_contend(tmp_path):
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main_repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                     "--allow-empty", "-q", "-m", "init"], cwd=main_repo, check=True)
    subprocess.run(["git", "branch", "second"], cwd=main_repo, check=True)
    second_wt = tmp_path / "second-worktree"
    subprocess.run(["git", "worktree", "add", "-q", str(second_wt), "second"],
                    cwd=main_repo, check=True)

    def _hold(path, ready_evt, release_evt):
        with acquire_run_lock(path):
            ready_evt.set()
            release_evt.wait(timeout=10)

    ready = MP.Event()
    release = MP.Event()
    p = MP.Process(target=_hold, args=(str(main_repo), ready, release))
    p.start()
    try:
        assert ready.wait(timeout=10)
        # Different worktree of the SAME repository - must acquire independently.
        with acquire_run_lock(str(second_wt)):
            pass
    finally:
        release.set()
        p.join(timeout=10)
    assert p.exitcode == 0


# ---------------------------------------------------------------------------
# Layer 2: CLI wiring (in-process CliRunner - normal-execution and read-only
# coverage; real cross-process contention is covered separately below)
# ---------------------------------------------------------------------------

def _mock_workflow_engine(fake_result=None, side_effect=None):
    mock_we = MagicMock()
    if side_effect is not None:
        mock_we.run_generation_workflow = AsyncMock(side_effect=side_effect)
    else:
        mock_we.run_generation_workflow = AsyncMock(return_value=fake_result or _FAKE_RESULT)
    return mock_we


def _mock_kernel():
    mock_kernel = strict_kernel()
    mock_kernel.start = AsyncMock()
    mock_kernel.stop = AsyncMock()
    return mock_kernel


_FAKE_RESULT = {
    "plan": "p", "design": "d", "files": ["a.py"], "quality_gates_passed": True,
    "environment_failure": None, "failure_category": None, "toolchain_warning": None,
    "lsp_warning": None, "unresolved_skill_gaps": None, "skill_staleness_warnings": None,
    "review": "ok", "run_id": "r1",
}


@pytest.fixture
def runner():
    return CliRunner()


def test_generate_acquires_and_releases_workspace_lock(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["generate", "do a thing", "-y"])
        assert result.exit_code == 0, result.output
        # Released on normal completion - reacquiring must succeed.
        with acquire_run_lock(cwd):
            pass


def test_fix_acquires_and_releases_workspace_lock(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["fix", "-e", "some error", "-y"])
        assert result.exit_code == 0, result.output
        with acquire_run_lock(cwd):
            pass


def test_proposal_execute_acquires_and_releases_workspace_lock(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        with patch("kriya.workflow.proposal_promotion.execute_approved_proposal",
                    new=AsyncMock(return_value=_FAKE_RESULT)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["proposal", "execute", "P1", "-y"])
        assert result.exit_code == 0, result.output
        with acquire_run_lock(cwd):
            pass


def test_lock_held_error_surfaces_as_clean_nonzero_exit_not_a_traceback(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        with acquire_run_lock(cwd):
            with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
                 patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
                 patch("kriya.cli.LLMClient"):
                result = runner.invoke(main, ["generate", "do a thing", "-y"])
        assert result.exit_code == 1
        assert "Workspace Locked" in result.output
        assert "another Kriya" in result.output


def test_review_does_not_acquire_workspace_lock(runner, tmp_path):
    java_file = tmp_path / "T.java"
    java_file.write_text("class T { void m() {} }")
    with acquire_run_lock(str(tmp_path)):
        # A held mutating-run lock must never block a read-only review.
        with patch("kriya.cli.LLMClient") as mock_llm_cls:
            mock_llm_cls.return_value.complete = AsyncMock(
                return_value='{"member_reviews": [], "overview": "x"}'
            )
            result = runner.invoke(main, ["review", str(java_file)])
    # Whatever review itself does or doesn't find, it must not fail with the
    # workspace-locked error - it never attempts to acquire the guard at all.
    assert "Workspace Locked" not in result.output


def test_proposal_show_and_approve_do_not_acquire_workspace_lock(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        os.makedirs(os.path.join(cwd, ".kriya", "proposals"), exist_ok=True)
        with acquire_run_lock(cwd):
            result_show = runner.invoke(main, ["proposal", "show", "does-not-exist"])
            result_approve = runner.invoke(main, ["proposal", "approve", "does-not-exist"])
    # Both fail (no such proposal) but never on the lock - proves they never
    # attempt acquisition while a real mutating run holds it.
    assert "Workspace Locked" not in result_show.output
    assert "Workspace Locked" not in result_approve.output


# ---------------------------------------------------------------------------
# Layer 3: real cross-process CLI contention (the actual CONC-001 guarantee,
# end to end through the real Click commands - not just the bare primitive)
# ---------------------------------------------------------------------------

def _run_generate_blocking_until_released(cwd, ready_evt, release_evt, result_q):
    async def _blocking(*args, **kwargs):
        ready_evt.set()
        release_evt.wait(timeout=15)
        return _FAKE_RESULT

    os.chdir(cwd)
    runner = CliRunner()
    with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(side_effect=_blocking)), \
         patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
         patch("kriya.cli.LLMClient"):
        result = runner.invoke(main, ["generate", "do a thing", "-y"])
    result_q.put(result.exit_code)


def test_generate_vs_generate_real_cross_process_contention(tmp_path, monkeypatch):
    ready = MP.Event()
    release = MP.Event()
    result_q = MP.Queue()
    p = MP.Process(target=_run_generate_blocking_until_released,
                    args=(str(tmp_path), ready, release, result_q))
    p.start()
    try:
        assert ready.wait(timeout=15), "child generate never reached the mocked generation call"
        monkeypatch.chdir(tmp_path)  # same directory the child locked, not a fresh one
        runner = CliRunner()
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            loser = runner.invoke(main, ["generate", "do a thing", "-y"])
        assert loser.exit_code == 1
        assert "Workspace Locked" in loser.output
    finally:
        release.set()
        p.join(timeout=15)
    assert result_q.get(timeout=5) == 0  # the child (winner) completed successfully


def test_proposal_execute_vs_generate_real_cross_process_contention(tmp_path, monkeypatch):
    ready = MP.Event()
    release = MP.Event()
    result_q = MP.Queue()
    p = MP.Process(target=_run_generate_blocking_until_released,
                    args=(str(tmp_path), ready, release, result_q))
    p.start()
    try:
        assert ready.wait(timeout=15)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch("kriya.workflow.proposal_promotion.execute_approved_proposal",
                    new=AsyncMock(return_value=_FAKE_RESULT)), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            loser = runner.invoke(main, ["proposal", "execute", "P1", "-y"])
        assert loser.exit_code == 1
        assert "Workspace Locked" in loser.output
    finally:
        release.set()
        p.join(timeout=15)
    assert result_q.get(timeout=5) == 0


def test_resume_flag_contends_the_same_as_a_fresh_run(tmp_path, monkeypatch):
    ready = MP.Event()
    release = MP.Event()
    result_q = MP.Queue()
    p = MP.Process(target=_run_generate_blocking_until_released,
                    args=(str(tmp_path), ready, release, result_q))
    p.start()
    try:
        assert ready.wait(timeout=15)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            loser = runner.invoke(main, ["generate", "do a thing", "-y", "--resume"])
        assert loser.exit_code == 1
        assert "Workspace Locked" in loser.output
    finally:
        release.set()
        p.join(timeout=15)
    assert result_q.get(timeout=5) == 0


def test_losing_process_makes_zero_source_mutation(tmp_path, monkeypatch):
    (tmp_path / "existing.txt").write_text("original content\n")
    before = (tmp_path / "existing.txt").read_bytes()

    ready = MP.Event()
    release = MP.Event()
    result_q = MP.Queue()
    p = MP.Process(target=_run_generate_blocking_until_released,
                    args=(str(tmp_path), ready, release, result_q))
    p.start()
    try:
        assert ready.wait(timeout=15)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            loser = runner.invoke(main, ["generate", "do a thing", "-y"])
        assert loser.exit_code == 1
    finally:
        release.set()
        p.join(timeout=15)

    after = (tmp_path / "existing.txt").read_bytes()
    assert after == before


def _run_milestone_sequence_blocking(cwd, plan_path, ready_evt, release_evt, result_q):
    async def _blocking_dispatch(*args, **kwargs):
        ready_evt.set()
        release_evt.wait(timeout=15)
        return {"status": "success"}

    os.chdir(cwd)
    runner = CliRunner()
    with patch("kriya.cli._dispatch_milestones", new=AsyncMock(side_effect=_blocking_dispatch)), \
         patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
         patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
         patch("kriya.cli.LLMClient"):
        result = runner.invoke(main, ["generate", "--from-milestones", plan_path, "-y"])
    result_q.put(result.exit_code)


def test_milestone_execution_holds_lock_across_the_whole_command(tmp_path, monkeypatch):
    plan = {
        "group_id": "g1",
        "original_goal": "do the thing",
        "milestones": [{"id": "m1", "goal": "step one"}],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    ready = MP.Event()
    release = MP.Event()
    result_q = MP.Queue()
    p = MP.Process(target=_run_milestone_sequence_blocking,
                    args=(str(tmp_path), str(plan_path), ready, release, result_q))
    p.start()
    try:
        assert ready.wait(timeout=15), "child milestone run never reached the blocking dispatch"
        # Still inside the single milestone dispatch call (i.e. before any
        # later milestone would run) - a competing command must still see
        # the workspace as owned for the command's entire duration.
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine()), \
             patch("kriya.cli.Kernel", return_value=_mock_kernel()), \
             patch("kriya.cli.LLMClient"):
            loser = runner.invoke(main, ["generate", "do a thing", "-y"])
        assert loser.exit_code == 1
        assert "Workspace Locked" in loser.output
    finally:
        release.set()
        p.join(timeout=15)
    assert result_q.get(timeout=5) == 0
