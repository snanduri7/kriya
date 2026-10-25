"""Commit-failure truth (S7 close #3 owner decision, item 6): a skill file is written but its Git commit fails.

`git_commit_if_tracked` swallowed every failure (it returned None; a failed `git add` was ignored and a failed `git commit` was
only debug-logged as "commonly just nothing to commit"). So a promotion whose `skill.yaml` was updated but never committed was
reported exactly like a clean one: no error anywhere, while the user's repository was left with an uncommitted tracked change.

MEASURED reproducer (a pre-created `.git/index.lock`, which makes both `git add` and `git commit` fail):
  * `apply_held_verifications()` returned `[]` and the run reported no `post_commit_persistence_errors`;
  * a fresh engine read `verified: true`;
  * `git status` showed ` M skills/widgetlib/skill.yaml`.

The first owner to lose the failure truth is the commit helper itself. It still never raises (a commit is an audit trail, not a
correctness requirement), but it now says whether the change was committed, and the verification flush reports a committed-less
promotion DISTINCTLY from one that could not be persisted at all.
"""
import pytest
from test_skill_verification_deferral import (
    _engine,
    _git,
    _head,
    _held,
    _skill_workspace,
    _yaml_text,
)

from kriya.skills.skill import git_commit_if_tracked, load_rule_provenance, record_rule_provenance


def _lock_the_index(repo):
    (repo / ".git" / "index.lock").write_text("")


def test_the_commit_helper_says_whether_the_change_was_committed(tmp_path):
    folder = _skill_workspace(tmp_path)
    (folder / "rules.txt").write_text("A new rule.\n")
    assert git_commit_if_tracked(str(folder / "rules.txt"), "a real change") is True            # committed
    assert git_commit_if_tracked(str(folder / "rules.txt"), "nothing changed") is True          # nothing to commit is not a failure

    (folder / "rules.txt").write_text("Another rule.\n")
    _lock_the_index(tmp_path)
    head = _head(tmp_path)
    assert git_commit_if_tracked(str(folder / "rules.txt"), "locked") is False                  # inside a work tree and NOT committed
    assert _head(tmp_path) == head


def test_a_path_outside_any_work_tree_has_nothing_to_commit_and_is_not_a_failure(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "rules.txt").write_text("x\n")
    assert git_commit_if_tracked(str(loose / "rules.txt"), "not tracked") is True


def test_a_promotion_whose_file_was_written_but_whose_commit_failed_is_reported_distinctly(tmp_path):
    folder = _skill_workspace(tmp_path)
    engine = _engine(tmp_path)
    engine.hold_verifications([_held()])
    _lock_the_index(tmp_path)
    head = _head(tmp_path)

    failures = engine.apply_held_verifications()

    assert "verified: true" in _yaml_text(folder)                                  # the verification IS durable in the file ...
    assert _head(tmp_path) == head and "M skills/widgetlib/skill.yaml" in _git(tmp_path, "status", "--porcelain")   # ... uncommitted
    assert failures == [{
        "operation": "skill_verification_commit", "skill": "widgetlib",
        "error": "skill.yaml was updated but its Git commit failed; the repository holds an uncommitted change",
    }]


def test_a_clean_promotion_reports_nothing_and_a_not_persisted_one_keeps_its_own_distinct_record(tmp_path):
    folder = _skill_workspace(tmp_path)
    engine = _engine(tmp_path)
    engine.hold_verifications([_held()])
    assert engine.apply_held_verifications() == []                                  # the clean case: no commit record

    from unittest.mock import patch

    from kriya.skills.skill import SkillEngine
    other = _engine(tmp_path)
    other.hold_verifications([_held()])
    with patch.object(SkillEngine, "mark_verified", return_value=False):
        assert [f["operation"] for f in other.apply_held_verifications()] == ["skill_verification"]
    assert "verified" in _yaml_text(folder)


def test_a_failed_rule_provenance_commit_is_reported_and_the_rule_still_flips_in_the_file(tmp_path):
    folder = _skill_workspace(tmp_path)
    rule = "Widgets must be printed in uppercase."
    record_rule_provenance(str(folder), rule, "human_text")
    engine = _engine(tmp_path)
    engine.hold_verifications([_held(rules=(rule,))])

    from unittest.mock import patch

    import kriya.skills.skill as skill_module
    real = skill_module.git_commit_if_tracked

    def fail_only_the_provenance_commit(path, message):
        return False if "mark rule(s) verified" in message else real(path, message)

    with patch.object(skill_module, "git_commit_if_tracked", fail_only_the_provenance_commit):
        failures = engine.apply_held_verifications()

    assert [r["verified"] for r in load_rule_provenance(str(folder)) if r["text"] == rule] == [True]
    assert failures == [{
        "operation": "skill_verification_commit", "skill": "widgetlib",
        "error": "the rule provenance was updated but its Git commit failed; the repository holds an uncommitted change",
    }]


@pytest.mark.asyncio
async def test_a_run_reports_a_promotion_commit_failure_in_its_persistence_errors(tmp_path):
    from test_skill_verification_deferral import GOAL, _run_inputs

    from kriya.workflow.workflow import WorkflowEngine
    _skill_workspace(tmp_path)
    kernel, llm = _run_inputs(tmp_path)
    from unittest.mock import patch

    import kriya.skills.skill as skill_module
    real = skill_module.git_commit_if_tracked

    with patch.object(skill_module, "git_commit_if_tracked", lambda path, message: False if "mark skill" in message else real(path, message)):
        res = await WorkflowEngine(kernel, llm).run_generation_workflow(goal=GOAL, workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True                                       # the candidate's verification truth is unchanged
    assert [e["operation"] for e in res["post_commit_persistence_errors"]] == ["skill_verification_commit"]


def test_a_commit_that_git_itself_rejects_after_a_successful_add_is_a_failure_too(tmp_path):
    folder = _skill_workspace(tmp_path)
    branch = _git(tmp_path, "symbolic-ref", "HEAD")                  # refs/heads/<branch>
    (tmp_path / ".git" / f"{branch}.lock").write_text("")            # the index is free, so `git add` succeeds; `git commit` cannot move the ref
    (folder / "rules.txt").write_text("A new rule.\n")
    head = _head(tmp_path)

    assert git_commit_if_tracked(str(folder / "rules.txt"), "unsignable") is False
    assert _head(tmp_path) == head and "skills/widgetlib/rules.txt" in _git(tmp_path, "status", "--porcelain")   # staged, not committed


def test_the_engine_remembers_the_commit_outcome_of_its_last_write_and_clears_it_when_no_write_happened(tmp_path):
    _skill_workspace(tmp_path)
    engine = _engine(tmp_path)
    assert engine.last_write_committed is None
    assert engine.mark_verified("widgetlib", context="c") is True and engine.last_write_committed is True

    _lock_the_index(tmp_path)
    assert engine.mark_gap_acknowledged("widgetlib") is True and engine.last_write_committed is False

    assert engine.mark_verified("ghost", context="c") is False and engine.last_write_committed is None      # no write: no stale outcome
