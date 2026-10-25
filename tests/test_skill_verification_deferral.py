"""P-B (S7 close #2, 2026-10-25): skill verification must not move the repository under a candidate's gates.

A passing Runtime Verification run marks the skills that contributed verified. `SkillEngine.mark_verified` persists that
as a commit into whatever git work tree holds the skill - by default the run's own workspace repository
(`paths.skills=./skills`) - which advanced the workspace HEAD in the MIDDLE of the run. Under mandatory containment the
next contained gate then refused with GIT_METADATA_STALE (the mounted checkout is behind the run's workspace), so a
passing run could not finish verifying its own candidate.

The invariant under test: the candidate is verified under one stable repository identity from the first bound gate through
the terminal apply/commit; the skill verification is only HELD until then, persisted after, never claimed when it could not
be persisted, and never persisted for a run whose candidate did not pass.

The S3 binding itself (`GIT_METADATA_STALE`) is not weakened here: the first test shows it still refuses an advanced HEAD.
"""
import json
import pathlib
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_kernel

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.skills.skill import HeldSkillVerification, SkillEngine, load_rule_provenance
from kriya.tools.git_metadata import GIT_METADATA_STALE, GitMetadataError, bound_git_metadata
from kriya.workflow.attempt_lifecycle import begin_attempt
from kriya.workflow.workflow import WorkflowEngine


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _head(cwd):
    return _git(cwd, "rev-parse", "HEAD")


def _init_repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "Test")


def _skill_workspace(tmp_path, rules="Widgets must be printed in uppercase.\n"):
    """A git workspace that tracks its own ./skills/widgetlib (the default `paths.skills` arrangement)."""
    _init_repo(tmp_path)
    folder = tmp_path / "skills" / "widgetlib"
    folder.mkdir(parents=True)
    (folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\n")
    (folder / "rules.txt").write_text(rules)
    (tmp_path / "README.md").write_text("placeholder\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return folder


def _engine(tmp_path):
    engine = SkillEngine(str(tmp_path / "skills"), load_global=False, workspace_path=str(tmp_path))
    engine.discover_and_load()
    return engine


def _held(name="widgetlib", context="widgetlib 2.0.0", rules=None):
    return HeldSkillVerification(skill_name=name, context=context, rule_texts=rules)


def _yaml_text(folder):
    return (folder / "skill.yaml").read_text()


# ---------------------------------------------------------------------------------------------- the S3 seam

def test_s3_still_refuses_an_advanced_workspace_head_and_a_held_verification_never_advances_it(tmp_path):
    """Characterizes WHY (immediate persistence trips S3, which stays strict) and shows the held path does not."""
    repo = tmp_path / "ws"
    repo.mkdir()
    folder = _skill_workspace(repo)
    mounted = tmp_path / "mounted"
    _git(repo, "worktree", "add", "-q", "--detach", str(mounted))
    state_root = str(tmp_path / "state")

    def bind():
        return bound_git_metadata(
            mounted_workspace=str(mounted), original_workspace=str(repo), state_root=state_root, enabled=True)

    assert bind() is not None                                   # the candidate's checkout is bound to the workspace HEAD
    engine = _engine(repo)

    # Held: nothing was written, HEAD did not move, the next contained gate still binds the SAME identity.
    engine.hold_verifications([_held()])
    assert _head(repo) == _head(mounted)
    assert "verified" not in _yaml_text(folder)
    assert bind() is not None

    # Immediate persistence (the pre-fix behavior, reproduced on a fresh engine): the workspace HEAD advances and S3 refuses.
    assert _engine(repo).mark_verified("widgetlib", context="widgetlib 2.0.0") is True
    assert _head(repo) != _head(mounted)
    with pytest.raises(GitMetadataError) as refusal:
        bind()
    assert refusal.value.reason_code == GIT_METADATA_STALE


# ---------------------------------------------------------------------------------------------- the engine queue

def test_holding_a_verification_writes_nothing_and_changes_no_state(tmp_path):
    folder = _skill_workspace(tmp_path)
    engine = _engine(tmp_path)
    head, yaml_before = _head(tmp_path), _yaml_text(folder)

    engine.hold_verifications([_held()])

    assert _head(tmp_path) == head and _yaml_text(folder) == yaml_before
    assert engine.get_skill("widgetlib").verified is False       # not even in memory: a later gap check still sees it open
    assert _git(tmp_path, "status", "--porcelain") == ""


def test_applying_persists_through_the_unchanged_mark_verified_and_empties_the_queue(tmp_path):
    folder = _skill_workspace(tmp_path)
    engine = _engine(tmp_path)
    engine.hold_verifications([_held()])

    assert engine.apply_held_verifications() == []

    assert "verified: true" in _yaml_text(folder) and "widgetlib 2.0.0" in _yaml_text(folder)
    assert _git(tmp_path, "log", "-1", "--format=%s") == "Kriya: mark skill 'widgetlib' verified (widgetlib 2.0.0)"
    head = _head(tmp_path)
    assert engine.discard_held_verifications() == 0              # the queue is empty: nothing can be persisted twice
    assert engine.apply_held_verifications() == []
    assert _head(tmp_path) == head


def test_the_latest_hold_per_skill_wins_and_several_skills_are_all_persisted(tmp_path):
    folder = _skill_workspace(tmp_path)
    other = tmp_path / "skills" / "other"
    other.mkdir()
    (other / "skill.yaml").write_text("name: other\ndescription: Test\ntags: [other]\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "second skill")
    engine = _engine(tmp_path)

    engine.hold_verifications([_held(context="first"), _held("other", "other ctx")])
    engine.hold_verifications([_held(context="second")])
    assert engine.apply_held_verifications() == []

    assert "second" in _yaml_text(folder) and "first" not in _yaml_text(folder)
    assert "other ctx" in _yaml_text(other)


def test_discarding_persists_nothing(tmp_path):
    folder = _skill_workspace(tmp_path)
    engine = _engine(tmp_path)
    head, yaml_before = _head(tmp_path), _yaml_text(folder)
    engine.hold_verifications([_held()])

    assert engine.discard_held_verifications() == 1
    assert engine.discard_held_verifications() == 0
    assert engine.apply_held_verifications() == []

    assert _head(tmp_path) == head and _yaml_text(folder) == yaml_before


def test_rule_provenance_flips_only_for_the_held_snapshot_and_only_after_the_skill_is_marked(tmp_path):
    from kriya.skills.skill import record_rule_provenance
    folder = _skill_workspace(tmp_path)
    record_rule_provenance(str(folder), "The magic widget constant is 42.", "test")
    record_rule_provenance(str(folder), "Never flipped.", "test")
    engine = _engine(tmp_path)

    engine.hold_verifications([_held(rules=("The magic widget constant is 42.",))])
    before = {r["text"]: r["verified"] for r in load_rule_provenance(str(folder))}
    assert before == {"The magic widget constant is 42.": False, "Never flipped.": False}     # held: not flipped yet

    assert engine.apply_held_verifications() == []
    after = {r["text"]: r["verified"] for r in load_rule_provenance(str(folder))}
    assert after == {"The magic widget constant is 42.": True, "Never flipped.": False}


def test_a_failed_persistence_is_reported_and_the_skill_is_never_claimed_verified(tmp_path):
    from kriya.skills.skill import record_rule_provenance
    folder = _skill_workspace(tmp_path)
    record_rule_provenance(str(folder), "Rule A.", "test")
    engine = _engine(tmp_path)
    engine.hold_verifications([_held(rules=("Rule A.",))])

    with patch.object(SkillEngine, "mark_verified", return_value=False):
        failures = engine.apply_held_verifications()

    assert failures == [{
        "operation": "skill_verification", "skill": "widgetlib",
        "error": "skill.yaml was not updated; the skill was not marked verified",
    }]
    assert "verified" not in _yaml_text(folder)
    assert [r["verified"] for r in load_rule_provenance(str(folder))] == [False]     # no rule claimed either


def test_a_raising_persistence_is_reported_without_blocking_the_other_skills(tmp_path):
    folder = _skill_workspace(tmp_path)
    other = tmp_path / "skills" / "other"
    other.mkdir()
    (other / "skill.yaml").write_text("name: other\ndescription: Test\ntags: [other]\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "second skill")
    engine = _engine(tmp_path)
    real = SkillEngine.mark_verified

    def flaky(self, name, context=""):
        if name == "widgetlib":
            raise OSError("disk full")
        return real(self, name, context=context)

    engine.hold_verifications([_held(), _held("other", "other ctx")])
    with patch.object(SkillEngine, "mark_verified", flaky):
        failures = engine.apply_held_verifications()

    assert failures == [{"operation": "skill_verification", "skill": "widgetlib", "error": "OSError: disk full"}]
    assert "verified" not in _yaml_text(folder)
    assert "verified: true" in _yaml_text(other)


def test_begin_attempt_drops_a_promotion_earned_by_a_superseded_candidate():
    from kriya.workflow.state import GenerationState
    state = GenerationState()
    state.pending_skill_verifications["widgetlib"] = _held()

    begin_attempt(state, MagicMock())

    assert state.pending_skill_verifications == {}


# ---------------------------------------------------------------------------------------------- the standalone run

def _run_inputs(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    judge = json.dumps({
        "should_run": True, "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit", "success_criteria": "Output contains [SUCCESS]"})
    grade = json.dumps({"passed": True, "reasoning": "Output contains the expected line."})
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code", "Design: Write app.py", "print('[SUCCESS] WIDGET')\n", judge, grade, "Review: Approved"])
    return Kernel(config=cfg), llm


GOAL = "Run with python app.py; the widgetlib==2.0 skill applies here"


@pytest.mark.asyncio
async def test_a_passing_run_keeps_one_repository_identity_until_the_terminal_apply_then_persists_the_skill(tmp_path):
    folder = _skill_workspace(tmp_path)
    initial = _head(tmp_path)
    kernel, llm = _run_inputs(tmp_path)
    observed = []
    real_record = __import__("kriya.workflow.state", fromlist=["GenerationState"]).GenerationState.record_gate_outcome

    def spy_record(self, outcome, *a, **k):
        observed.append((outcome.get("type"), _head(tmp_path)))
        return real_record(self, outcome, *a, **k)

    import kriya.workflow.development_loop as development_loop
    real_apply = development_loop.apply_terminal_candidate

    def spy_apply(*a, **k):
        observed.append(("terminal_apply:before", _head(tmp_path)))
        result = real_apply(*a, **k)
        observed.append(("terminal_apply:after", _head(tmp_path)))
        return result

    with patch("kriya.workflow.state.GenerationState.record_gate_outcome", spy_record), \
            patch.object(development_loop, "apply_terminal_candidate", spy_apply):
        res = await WorkflowEngine(kernel, llm).run_generation_workflow(goal=GOAL, workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    types = [t for t, _ in observed]
    assert "run_verification" in types or any("verif" in str(t) for t in types), types   # the runtime gate really ran
    assert types.index("terminal_apply:after") > 0
    # EVERY gate outcome and the whole terminal apply saw the identity the run started with.
    assert {head for _, head in observed} == {initial}, observed
    # Only afterwards did the bookkeeping land: one commit, the skill verified, no persistence error.
    assert _head(tmp_path) != initial
    assert _git(tmp_path, "log", "-1", "--format=%s") == "Kriya: mark skill 'widgetlib' verified (widgetlib 2.0.0)"
    assert "verified: true" in _yaml_text(folder)
    assert "post_commit_persistence_errors" not in res


@pytest.mark.asyncio
async def test_a_failed_skill_persistence_does_not_rewrite_the_passing_verification(tmp_path):
    folder = _skill_workspace(tmp_path)
    kernel, llm = _run_inputs(tmp_path)

    with patch.object(SkillEngine, "mark_verified", return_value=False):
        res = await WorkflowEngine(kernel, llm).run_generation_workflow(goal=GOAL, workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert res["post_commit_persistence_errors"] == [{
        "operation": "skill_verification", "skill": "widgetlib",
        "error": "skill.yaml was not updated; the skill was not marked verified",
    }]
    assert "verified" not in _yaml_text(folder)


@pytest.mark.asyncio
async def test_a_run_that_ends_failed_with_a_promotion_still_pending_persists_nothing(tmp_path):
    """A run whose gates never passed still reaches its end ("Continuing to review with errors"). Whatever promotion is
    pending then (seeded at the final review, the last point before the flush) must not be persisted."""
    from kriya.workflow.failure import Failure, QualityGateFailure
    folder = _skill_workspace(tmp_path)
    initial = _head(tmp_path)
    kernel, llm = _run_inputs(tmp_path)
    import kriya.workflow.development_loop as development_loop
    import kriya.workflow.workflow as workflow_module
    real_final_review = workflow_module.final_review

    def late_gate_failure(*a, **k):
        raise QualityGateFailure(Failure(type="test", message="a gate failed", raw_output="x", attempt=1))

    async def final_review_with_pending(stage_ctx, deps):
        stage_ctx.state.pending_skill_verifications["widgetlib"] = _held()
        return await real_final_review(stage_ctx, deps)

    with patch.object(development_loop, "apply_terminal_candidate", late_gate_failure), \
            patch.object(workflow_module, "final_review", final_review_with_pending):
        res = await WorkflowEngine(kernel, llm).run_generation_workflow(goal=GOAL, workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is False and res["failure_category"]      # it ended as a failed run
    assert _head(tmp_path) == initial and "verified" not in _yaml_text(folder)


@pytest.mark.asyncio
async def test_a_controller_owned_run_holds_its_verification_for_the_controller_instead_of_persisting_it(tmp_path):
    """The enforce subtasks share one engine (skill_engine_override): a passing subtask run only HANDS OVER."""
    folder = _skill_workspace(tmp_path)
    initial = _head(tmp_path)
    kernel, llm = _run_inputs(tmp_path)
    shared = _engine(tmp_path)

    res = await WorkflowEngine(kernel, llm).run_generation_workflow(
        goal=GOAL, workspace_path=str(tmp_path), skill_engine_override=shared)

    assert res["quality_gates_passed"] is True
    assert _head(tmp_path) == initial and "verified" not in _yaml_text(folder)       # nothing persisted by the subtask run
    assert shared.apply_held_verifications() == []                                    # ... but it IS held for the controller
    assert "verified: true" in _yaml_text(folder) and _head(tmp_path) != initial


@pytest.mark.asyncio
async def test_a_run_that_never_reaches_a_committed_candidate_promotes_nothing(tmp_path):
    folder = _skill_workspace(tmp_path)
    initial = _head(tmp_path)
    kernel, llm = _run_inputs(tmp_path)
    import kriya.workflow.development_loop as development_loop

    def failing_apply(*a, **k):
        raise RuntimeError("terminal commit blew up")

    with patch.object(development_loop, "apply_terminal_candidate", failing_apply):
        res = await WorkflowEngine(kernel, llm).run_generation_workflow(goal=GOAL, workspace_path=str(tmp_path))

    # The runtime verification DID pass before the commit failed; the old behavior had already persisted the skill by now.
    assert res["quality_gates_passed"] is False
    assert _head(tmp_path) == initial
    assert "verified" not in _yaml_text(folder)


# ---------------------------------------------------------------------------------------------- the enforce run

def _enforce_plan():
    from test_workflow_controller_enforce import _two_subtask_plan
    return _two_subtask_plan()


async def _enforce(tmp_path, *, commit_completes, config, engine):
    from test_workflow_controller_enforce import _patched, _workflow_engine

    from kriya.tools.knowledge import GapReport
    from kriya.workflow.workflow_controller import WorkflowController
    we = _workflow_engine()
    we.kernel = strict_kernel(config)
    heads = []

    async def fake_run(**kwargs):
        heads.append(_head(tmp_path))                        # the identity each subtask's gates would bind
        engine = kwargs["skill_engine_override"]
        engine.hold_verifications([_held()])                 # what a passing runtime verification does
        # run_generation_workflow's own hand-off: a controller-owned run only HOLDS, the controller persists.
        path = "a.py" if len(heads) == 1 else "b.py"
        (pathlib.Path(kwargs["workspace_path"]) / path).write_text(f"# {path}")      # the plan's sandbox, as a real subtask
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(_enforce_plan())
    contexts = [p1, p2, p3, patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=GapReport()),
                patch("kriya.skills.skill.SkillEngine.from_config", return_value=engine)]
    if not commit_completes:
        failed = MagicMock(completed=False, failure={"reason_code": "TEST_COMMIT_FAILED", "workspace_state": "UNCHANGED", "error": "boom"}, evidence=None,
                           contract_registry=None, record_errors=[])
        contexts.append(patch("kriya.workflow.workflow_controller.commit_verified_candidate", return_value=failed))
    from contextlib import ExitStack
    with ExitStack() as stack:
        for context in contexts:
            stack.enter_context(context)
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")
    return result, heads


def _enforce_config(tmp_path):
    config = AppConfig()
    config.paths.memory = str(tmp_path / "memory")
    config.paths.skills = str(tmp_path / "skills")
    return config


@pytest.mark.asyncio
async def test_an_enforce_run_persists_the_skill_after_the_terminal_commit_not_between_subtasks(tmp_path):
    folder = _skill_workspace(tmp_path)
    initial = _head(tmp_path)

    engine = _engine(tmp_path)
    result, heads = await _enforce(tmp_path, commit_completes=True, config=_enforce_config(tmp_path), engine=engine)

    assert result.legacy_result["status"] == "success"
    assert heads == [initial, initial]                        # subtask 2's gates bound the SAME identity as subtask 1's
    assert _head(tmp_path) != initial
    assert "verified: true" in _yaml_text(folder)
    assert "post_commit_persistence_errors" not in result.legacy_result


@pytest.mark.asyncio
async def test_an_enforce_run_whose_candidate_was_not_committed_promotes_nothing(tmp_path):
    folder = _skill_workspace(tmp_path)
    initial = _head(tmp_path)

    engine = _engine(tmp_path)
    result, heads = await _enforce(tmp_path, commit_completes=False, config=_enforce_config(tmp_path), engine=engine)

    assert result.legacy_result["status"] != "success"
    assert heads == [initial, initial]
    assert _head(tmp_path) == initial and "verified" not in _yaml_text(folder)
    assert engine.discard_held_verifications() == 0                  # the uncommitted run left nothing held behind


@pytest.mark.asyncio
async def test_an_enforce_run_reports_a_failed_skill_persistence_without_rewriting_the_committed_result(tmp_path):
    folder = _skill_workspace(tmp_path)
    engine = _engine(tmp_path)

    with patch.object(SkillEngine, "mark_verified", return_value=False):
        result, _ = await _enforce(tmp_path, commit_completes=True, config=_enforce_config(tmp_path), engine=engine)

    assert result.legacy_result["status"] == "success" and result.legacy_result["quality_gates_passed"] is True
    assert result.legacy_result["post_commit_persistence_errors"] == [{
        "operation": "skill_verification", "skill": "widgetlib",
        "error": "skill.yaml was not updated; the skill was not marked verified",
    }]
    assert "verified" not in _yaml_text(folder)
