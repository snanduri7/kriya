"""W2-04 real-containment proof (REAL DOCKER): the approved milestone replay executes through the SAME contained runtime-verification owner as attempt-time verification.

MEASURED 2026-10-28 (probe_replay_containment.py, WAVE2_W204_REPLAY_AUTHORITY_2026-10-28): the replay-shaped validator (owner workspace) and the attempt-time-shaped validator
(isolated worktree) have an identical envelope - inside a container, no network interface but loopback and an outbound connect fails, ``.git`` and ``.kriya/control`` read-only,
a host path not visible, and every write the command makes lands in the disposable gate view (OI-142), never in the host workspace. These tests pin that with the real
validator and a real container; they are the replay-side half of the containment proof, so a regression that ran the replay on the host or in the shared workspace fails here.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from _strict_doubles import strict_config

from kriya.workflow.milestones import MilestoneRunState, replay_prior_milestone_report

PLAN = {"group_id": "g1", "original_goal": "x", "milestones": [{"id": "M1", "goal": "g", "depends_on": []}]}


def _daemon_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


REAL_DOCKER = pytest.mark.skipif(not _daemon_ok(), reason="docker daemon not reachable")


def _workspace(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "keep.txt").write_text("original\n")
    (path / "other.txt").write_text("untouched\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=path, check=True)
    return path


def _script(host_sentinel: Path) -> str:
    """Prints PASS only if the command really ran inside the container with the expected envelope; FAIL names the first violated fact."""
    return f"""
fail() {{ echo "[VERIFICATION] FAIL: $1"; exit 0; }}
test -f /.dockerenv || fail "not inside a container (ran on the host)"
python3 -c 'import socket; socket.create_connection(("1.1.1.1", 53), 3)' 2>/dev/null && fail "the network is reachable"
(echo x > .git/PROBE) 2>/dev/null && fail ".git is writable"
(mkdir -p .kriya/control && echo x > .kriya/control/PROBE) 2>/dev/null && fail ".kriya/control is writable"
(echo x > "{host_sentinel}") 2>/dev/null && fail "a host path is writable by name"
echo changed > keep.txt
rm -f other.txt
echo created > CREATED_BY_REPLAY.txt
echo "[VERIFICATION] PASS"
"""


def _approve(files, reason):  # the run's approval channel (human-in-the-loop is the default mode); the exact set it was shown is recorded
    _approve.reasons.append(reason)
    return True


_approve.reasons = []


def _replay(workspace: Path, script: str):
    state = MilestoneRunState.from_dict(PLAN)
    state.verification_commands["M1"] = [["sh", "-c", script]]
    _approve.reasons = []
    autonomy = strict_config(autonomy={}).autonomy
    assert autonomy.mode == "human-in-the-loop" and autonomy.contained_execution_required is True
    return asyncio.run(replay_prior_milestone_report(str(workspace), state, autonomy_cfg=autonomy, approval_callback=_approve))


def _host_unchanged(workspace: Path):
    assert (workspace / "keep.txt").read_text() == "original\n", "the replay's write reached the host workspace"
    assert (workspace / "other.txt").read_text() == "untouched\n", "the replay's delete reached the host workspace"
    assert not (workspace / "CREATED_BY_REPLAY.txt").exists(), "the replay's new file reached the host workspace"
    assert not (workspace / ".git" / "PROBE").exists() and not (workspace / ".kriya" / "control" / "PROBE").exists()


@REAL_DOCKER
def test_the_approved_replay_runs_in_a_real_container_with_no_host_fallback_and_no_host_effect(tmp_path):
    workspace = _workspace(tmp_path, "owner_ws")
    sentinel = tmp_path / "HOST_OUTSIDE_SENTINEL"
    script = _script(sentinel)
    report = _replay(workspace, script)
    assert [(e["milestone_id"], e["outcome"]) for e in report.entries] == [("M1", "passed")], report.entries  # PASS is printed only from inside the container with the whole envelope
    assert len(_approve.reasons) == 1 and "echo changed > keep.txt" in _approve.reasons[0], "the approval was shown the exact script that ran"
    _host_unchanged(workspace)
    assert not sentinel.exists()


@REAL_DOCKER
def test_the_replay_envelope_equals_the_attempt_time_envelope(tmp_path):
    """The same script through the validator built as attempt-time runtime verification builds it (isolated worktree, owner workspace as the original) gives the same verdict
    and the same host effect: the replay is not a broader envelope."""
    from kriya.tools.validate import PolymorphicValidator

    owner, worktree = _workspace(tmp_path, "owner_ws"), _workspace(tmp_path, "isolated_worktree")
    script = _script(tmp_path / "HOST_OUTSIDE_SENTINEL")
    autonomy = strict_config(autonomy={}).autonomy
    attempt_shaped = PolymorphicValidator(str(worktree), original_workspace_path=str(owner), autonomy_cfg=autonomy).run_app_sequence([["sh", "-c", script]], timeout=180)
    assert "[VERIFICATION] PASS" in attempt_shaped["output"], attempt_shaped["output"][-600:]
    replay = _replay(owner, script)
    assert replay.entries[0]["outcome"] == "passed", replay.entries
    _host_unchanged(owner)
    _host_unchanged(worktree)
