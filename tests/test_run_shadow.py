"""The run-local shadow of the skills root (registry: GIT-MIDRUN-COMMITS-S3-001).

The enforce-route acceptance (zero HEAD/content drift between the first bind and the terminal gates, later-subtask visibility,
terminal failure / flush conflict / commit failure / partial write) lives in tests/test_midrun_workspace_mutations.py. This file
holds the engine- and flush-level contract and drives the REAL analysis stages with a shadowed engine, so a same-run reader or
writer that still used the canonical `paths.skills` is caught.
"""
import json
import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from test_midrun_workspace_mutations import (
    ALPHA_RULE,
    BETA_RULE,
    CONFLICT,
    GENERATION,
    RULE,
    _git,
    _repo,
)

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.skills.run_shadow import (
    REASON_ROOT_ENCLOSES_REPOSITORY,
    REASON_ROOT_IN_WORK_TREE,
    RunShadowRefused,
    manifest,
)
from kriya.skills.skill import SkillEngine
from kriya.workflow.resume_fingerprints import skills_fingerprint


def _state(tmp_path):
    return str(tmp_path / "kriya-state")


def _shadowed(tmp_path, skills=None):
    ws = _repo(tmp_path, skills or {"widgetlib": ("", RULE + "\n"), "otherlib": ("", RULE + "\n")})
    engine = SkillEngine(str(ws / "skills"), load_global=False, workspace_path=str(ws))
    engine.discover_and_load()
    return ws, engine


def _canonical_unchanged(ws):
    return _git(ws, "status", "--porcelain", "--", "skills") == ""


# ------------------------------------------------------------------------------------------------ activation boundary

def test_a_skills_root_inside_the_bound_repository_is_shadowed_and_every_loaded_skill_points_into_the_copy(tmp_path):
    ws, engine = _shadowed(tmp_path)

    assert engine.activate_run_shadow(str(ws), _state(tmp_path)) is True

    shadow = engine.skills_dir
    assert shadow.startswith(os.path.realpath(_state(tmp_path))) and shadow != str(ws / "skills")
    assert engine.canonical_skills_dir == str(ws / "skills") and engine.skills_dirs[-1] == str(ws / "skills")
    assert {s.source_path for s in engine.list_skills()} == {os.path.join(shadow, "widgetlib"), os.path.join(shadow, "otherlib")}
    assert engine.effective_skills_dir(str(ws / "skills")) == shadow and engine.canonical_path(shadow + "/widgetlib") == str(ws / "skills" / "widgetlib")
    assert {m["source_path"] for m in engine.manifest_for(["widgetlib"])} == {str(ws / "skills" / "widgetlib")}   # reports name the durable path


def test_a_skills_root_outside_the_candidate_repository_keeps_the_existing_behaviour(tmp_path):
    ws = _repo(tmp_path, {})
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "widgetlib").mkdir(parents=True)
    (elsewhere / "widgetlib" / "skill.yaml").write_text("name: widgetlib\ndescription: t\ntags: [widgetlib]\n")
    engine = SkillEngine(str(elsewhere), load_global=False, workspace_path=str(ws))
    engine.discover_and_load()

    assert engine.activate_run_shadow(str(ws), _state(tmp_path)) is False

    assert engine.run_shadow_active is False and engine.skills_dir == str(elsewhere)
    assert engine.get_skill("widgetlib").source_path == str(elsewhere / "widgetlib")
    assert engine.mark_gap_acknowledged("widgetlib") is True
    assert "verification_gap_acknowledged: true" in (elsewhere / "widgetlib" / "skill.yaml").read_text()     # written in place, as before
    assert not os.path.exists(os.path.join(_state(tmp_path), "run_shadow"))


def test_a_skills_root_in_the_same_git_work_tree_as_a_sub_directory_workspace_is_shadowed(tmp_path):
    repo = _repo(tmp_path, {"widgetlib": ("", RULE + "\n")})
    workspace = repo / "service"
    workspace.mkdir()
    engine = SkillEngine(str(repo / "skills"), load_global=False, workspace_path=str(workspace))
    engine.discover_and_load()

    assert engine.activate_run_shadow(str(workspace), _state(tmp_path)) is True        # its commits would still move the repository's HEAD


def test_a_cwd_skills_directory_inside_the_workspace_is_shadowed_too_not_only_the_configured_one(tmp_path, monkeypatch):
    ws = _repo(tmp_path, {"widgetlib": ("", RULE + "\n")})
    configured = tmp_path / "configured"
    (configured / "outside").mkdir(parents=True)
    (configured / "outside" / "skill.yaml").write_text("name: outside\ndescription: t\ntags: [outside]\n")
    monkeypatch.chdir(ws)
    engine = SkillEngine(str(configured), load_global=False, load_cwd=True, workspace_path=str(ws))
    engine.discover_and_load()
    assert str(ws / "skills") in engine.skills_dirs and engine.get_skill("widgetlib").source_path == str(ws / "skills" / "widgetlib")

    assert engine.activate_run_shadow(str(ws), _state(tmp_path)) is True

    assert engine.get_skill("widgetlib").source_path.startswith(os.path.realpath(_state(tmp_path)))
    assert engine.get_skill("outside").source_path == str(configured / "outside")           # a root outside the repository is not copied
    assert engine.mark_gap_acknowledged("widgetlib") is True and _canonical_unchanged(ws)


def test_activation_is_idempotent_and_a_second_activation_does_not_re_copy(tmp_path):
    ws, engine = _shadowed(tmp_path)
    assert engine.activate_run_shadow(str(ws), _state(tmp_path)) is True
    engine.mark_gap_acknowledged("widgetlib")

    assert engine.activate_run_shadow(str(ws), _state(tmp_path)) is False
    assert engine.get_skill("widgetlib").verification_gap_acknowledged is True


@pytest.mark.parametrize("skills_root, reason", [("ws", REASON_ROOT_ENCLOSES_REPOSITORY), ("holds_git", REASON_ROOT_ENCLOSES_REPOSITORY)])
def test_a_skills_root_that_is_or_holds_the_repository_is_refused_before_anything_is_copied(tmp_path, skills_root, reason):
    ws = _repo(tmp_path, {})
    root = ws if skills_root == "ws" else ws / "vendored"
    if skills_root == "holds_git":
        (root / ".git").mkdir(parents=True)                      # a nested repository: its metadata must never be copied or flushed
    engine = SkillEngine(str(root), load_global=False, workspace_path=str(ws))

    with pytest.raises(RunShadowRefused) as refusal:
        engine.activate_run_shadow(str(ws), _state(tmp_path))

    assert refusal.value.reason_code == reason and engine.run_shadow_active is False
    assert not os.path.exists(os.path.join(_state(tmp_path), "run_shadow"))


def test_a_shadow_location_inside_a_git_work_tree_is_refused_and_leaves_nothing_behind(tmp_path):
    ws, engine = _shadowed(tmp_path)
    inside = str(ws / "state")                                  # the state directory lives in the repository: its writes would be committed

    with pytest.raises(RunShadowRefused) as refusal:
        engine.activate_run_shadow(str(ws), inside)

    assert refusal.value.reason_code == REASON_ROOT_IN_WORK_TREE and engine.run_shadow_active is False
    assert not os.path.exists(os.path.join(inside, "run_shadow")) and _canonical_unchanged(ws)


# ------------------------------------------------------------------------------------------------ one effective root

def test_the_resume_fingerprint_is_over_the_canonical_skills_and_does_not_move_with_the_shadow(tmp_path):
    ws, engine = _shadowed(tmp_path)
    before = skills_fingerprint(engine.skills_dirs)

    engine.activate_run_shadow(str(ws), _state(tmp_path))
    engine.mark_gap_acknowledged("widgetlib")                     # run-local learning

    assert skills_fingerprint(engine.skills_dirs) == before


def test_every_same_run_write_lands_in_the_shadow_and_none_in_the_candidate_repository(tmp_path):
    from kriya.skills.skill import record_conflict_resolution, record_rule_provenance
    ws, engine = _shadowed(tmp_path)
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    widget = engine.get_skill("widgetlib")
    before = {"head": _git(ws, "rev-parse", "HEAD"), "files": manifest(str(ws / "skills"))}

    engine.create_skill_skeleton("newlib")
    engine.mark_gap_acknowledged("widgetlib")
    record_rule_provenance(widget.source_path, RULE, "human_text")
    record_conflict_resolution(engine.skills_dir, "widgetlib", RULE, "otherlib", RULE, "prefer_a")

    assert _git(ws, "rev-parse", "HEAD") == before["head"] and manifest(str(ws / "skills")) == before["files"] and _canonical_unchanged(ws)
    assert {"newlib/skill.yaml", ".skill_conflicts.json", "widgetlib/rule_provenance.json"} <= set(manifest(engine.skills_dir))


# ------------------------------------------------------------------------------------------------ the flush

def test_a_successful_flush_commits_exactly_the_runs_delta_and_removes_the_shadow(tmp_path):
    ws, engine = _shadowed(tmp_path)
    (ws / "skills" / "scratch.txt").write_text("the user's own untracked file\n")          # present at the snapshot, unchanged by the run
    (ws / "unrelated.txt").write_text("staged by the user\n")
    _git(ws, "add", "unrelated.txt")
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    engine.mark_gap_acknowledged("widgetlib")
    engine.create_skill_skeleton("newlib")
    head = _git(ws, "rev-parse", "HEAD")

    failures = engine.persist_run_learning()

    assert failures == []
    committed = set(_git(ws, "show", "--name-only", "--format=", "HEAD").splitlines())
    assert committed == {"skills/widgetlib/skill.yaml", "skills/newlib/skill.yaml", "skills/newlib/instructions.md", "skills/newlib/rules.txt"}
    assert int(_git(ws, "rev-list", "--count", f"{head}..HEAD")) == 1                       # one transaction, one commit
    assert "A  unrelated.txt" in _git(ws, "status", "--porcelain") and "?? skills/scratch.txt" in _git(ws, "status", "--porcelain")
    assert engine.run_shadow_active is False and engine.skills_dir == str(ws / "skills")
    assert engine.get_skill("widgetlib").verification_gap_acknowledged is True and engine.get_skill("widgetlib").source_path == str(ws / "skills" / "widgetlib")
    assert not os.path.exists(os.path.join(_state(tmp_path), "run_shadow"))


def test_a_deletion_in_the_shadow_is_flushed_as_a_deletion(tmp_path):
    ws, engine = _shadowed(tmp_path)
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    os.remove(os.path.join(engine.skills_dir, "otherlib", "rules.txt"))

    assert engine.persist_run_learning() == []

    assert not (ws / "skills" / "otherlib" / "rules.txt").exists()
    assert _git(ws, "show", "--name-status", "--format=", "HEAD") == "D\tskills/otherlib/rules.txt"


def test_a_git_ignored_skills_root_is_persisted_as_files_without_a_commit_and_is_not_a_failure(tmp_path):
    ws, engine = _shadowed(tmp_path)
    (ws / ".gitignore").write_text("skills/newlib/\n")
    _git(ws, "add", ".gitignore")
    _git(ws, "commit", "-q", "-m", "ignore")
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    engine.create_skill_skeleton("newlib")
    head = _git(ws, "rev-parse", "HEAD")

    assert engine.persist_run_learning() == []

    assert (ws / "skills" / "newlib" / "skill.yaml").is_file() and _git(ws, "rev-parse", "HEAD") == head


def test_a_workspace_that_is_not_a_git_repository_is_flushed_as_files(tmp_path):
    ws = tmp_path / "plain"
    (ws / "skills" / "widgetlib").mkdir(parents=True)
    (ws / "skills" / "widgetlib" / "skill.yaml").write_text("name: widgetlib\ndescription: t\ntags: [widgetlib]\n")
    engine = SkillEngine(str(ws / "skills"), load_global=False, workspace_path=str(ws))
    engine.discover_and_load()
    assert engine.activate_run_shadow(str(ws), _state(tmp_path)) is True
    engine.mark_gap_acknowledged("widgetlib")
    assert "verification_gap_acknowledged" not in (ws / "skills" / "widgetlib" / "skill.yaml").read_text()

    assert engine.persist_run_learning() == []

    assert "verification_gap_acknowledged: true" in (ws / "skills" / "widgetlib" / "skill.yaml").read_text()


def test_held_verifications_flush_through_the_shadow_as_one_commit(tmp_path):
    from kriya.skills.skill import HeldSkillVerification
    ws, engine = _shadowed(tmp_path)
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    engine.hold_verifications([HeldSkillVerification("widgetlib", "widgetlib 2.0.0", None)])
    head = _git(ws, "rev-parse", "HEAD")

    assert engine.is_effectively_verified(engine.get_skill("widgetlib")) is True and _canonical_unchanged(ws)
    assert engine.persist_run_learning() == []

    assert "verified: true" in (ws / "skills" / "widgetlib" / "skill.yaml").read_text()
    assert int(_git(ws, "rev-list", "--count", f"{head}..HEAD")) == 1


def test_a_failed_flush_does_not_leave_the_engine_claiming_the_learning(tmp_path):
    from kriya.skills.skill import HeldSkillVerification
    ws, engine = _shadowed(tmp_path)
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    engine.hold_verifications([HeldSkillVerification("widgetlib", "widgetlib 2.0.0", None)])
    (ws / ".git" / "index.lock").write_text("")                      # git add and git commit both fail

    [failure] = engine.persist_run_learning()

    assert failure["operation"] == "skill_learning_flush" and failure["reason_code"] == "SKILLS_FLUSH_FAILED"
    assert "verified" not in (ws / "skills" / "widgetlib" / "skill.yaml").read_text()
    assert engine.get_skill("widgetlib").verified is False and engine.is_effectively_verified(engine.get_skill("widgetlib")) is False
    assert _canonical_unchanged(ws)


def test_a_new_engine_never_infers_the_shadow_or_a_held_verification_from_what_an_earlier_run_left(tmp_path):
    from kriya.skills.skill import HeldSkillVerification
    ws, engine = _shadowed(tmp_path)
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    engine.mark_gap_acknowledged("widgetlib")
    engine.hold_verifications([HeldSkillVerification("widgetlib", "c", None)])
    # the process dies here: no flush, no discard

    restarted = SkillEngine(str(ws / "skills"), load_global=False, workspace_path=str(ws))
    restarted.discover_and_load()

    assert restarted.run_shadow_active is False and restarted.skills_dir == str(ws / "skills")
    skill = restarted.get_skill("widgetlib")
    assert skill.verification_gap_acknowledged is False and skill.verified is False and restarted.is_effectively_verified(skill) is False
    restarted.activate_run_shadow(str(ws), _state(tmp_path))                 # the next run starts from the canonical skills, sweeping the stale copy
    assert restarted.get_skill("widgetlib").verification_gap_acknowledged is False


def test_discarding_a_run_changes_nothing_canonical_and_drops_the_engines_view_of_the_learning(tmp_path):
    ws, engine = _shadowed(tmp_path)
    before = manifest(str(ws / "skills"))
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    engine.create_skill_skeleton("newlib")
    engine.mark_gap_acknowledged("widgetlib")

    engine.discard_run_learning()

    assert manifest(str(ws / "skills")) == before and _canonical_unchanged(ws) and engine.run_shadow_active is False
    assert engine._resolve_skill("newlib") is None and engine.get_skill("widgetlib").verification_gap_acknowledged is False


# ------------------------------------------------------------------------------------------------ the real stages use the shadow

def _shadowed_workflow_engine(tmp_path, skills, answers, kriya_verified=()):
    """`kriya_verified`: skills Kriya itself already verified earlier in this run (a held verification); B3: a workspace skill.yaml's
    `verified: true` no longer stands for that."""
    from kriya.skills.skill import HeldSkillVerification
    from kriya.workflow.workflow import WorkflowEngine
    ws = _repo(tmp_path, skills)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(ws / "skills")
    cfg.autonomy.run_verification_enabled = False
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=answers)
    engine = SkillEngine.from_config(cfg, workspace_path=str(ws))
    engine.discover_and_load()
    assert engine.activate_run_shadow(str(ws), _state(tmp_path)) is True
    engine.hold_verifications([HeldSkillVerification(name, "held earlier in this run", None) for name in kriya_verified])
    return ws, engine, WorkflowEngine(Kernel(config=cfg), llm)


@pytest.mark.asyncio
async def test_the_real_gap_stage_writes_its_acknowledgement_to_the_shadow_and_a_later_unit_does_not_re_ask(tmp_path):
    ws, engine, workflow = _shadowed_workflow_engine(tmp_path, {"widgetlib": ("", RULE + "\n")}, GENERATION + GENERATION)
    asked = []

    def decline(reason, names):
        asked.append(names)

    head = _git(ws, "rev-parse", "HEAD")
    goal = "Build something with the widgetlib skill"
    await workflow.run_generation_workflow(goal=goal, workspace_path=str(ws), skill_gap_callback=decline, skill_engine_override=engine)
    skills_after_first_unit = (_git(ws, "status", "--porcelain", "--", "skills"), _git(ws, "log", "--oneline", f"{head}..HEAD", "--", "skills"))
    assert "verification_gap_acknowledged: true" in open(os.path.join(engine.skills_dir, "widgetlib", "skill.yaml")).read()
    await workflow.run_generation_workflow(goal=goal, workspace_path=str(ws), skill_gap_callback=decline, skill_engine_override=engine)

    assert skills_after_first_unit == ("", "")                                  # the repository's skills were never touched
    assert len(asked) == 1                                                       # the second unit saw the first unit's acknowledgement
    assert "verification_gap_acknowledged" not in (ws / "skills" / "widgetlib" / "skill.yaml").read_text()


@pytest.mark.asyncio
async def test_the_real_extraction_and_conflict_stages_use_the_shadow_for_rules_the_registry_and_the_next_unit(tmp_path):
    skills = {"brokeralpha": ("", ""), "brokerbeta": ("", BETA_RULE + "\n")}
    extraction = json.dumps({"rules": [ALPHA_RULE], "examples": {}, "conflicts": []})
    ws, engine, workflow = _shadowed_workflow_engine(tmp_path, skills, [extraction, CONFLICT, *GENERATION, *GENERATION], kriya_verified=("brokerbeta",))
    resolutions = []

    def resolve(*args):
        resolutions.append(args)
        return "prefer_b"

    goal = "Build something using both the brokeralpha and brokerbeta skills"
    gap = "Reference material establishing the alpha broker's port rule."
    await workflow.run_generation_workflow(goal=goal, workspace_path=str(ws), skill_gap_callback=lambda reason, names: gap,
                                           skill_conflict_callback=resolve, skill_engine_override=engine)

    assert _git(ws, "status", "--porcelain", "--", "skills") == "" and (ws / "skills" / "brokeralpha" / "rules.txt").read_text() == ""
    assert not (ws / "skills" / ".skill_conflicts.json").exists()
    assert ALPHA_RULE in (open(os.path.join(engine.skills_dir, "brokeralpha", "rules.txt")).read())
    assert os.path.isfile(os.path.join(engine.skills_dir, ".skill_conflicts.json"))
    engine.discover_and_load()
    assert ALPHA_RULE in engine.get_skill("brokeralpha").rules and len(resolutions) == 1

    assert engine.persist_run_learning() == []
    assert ALPHA_RULE in (ws / "skills" / "brokeralpha" / "rules.txt").read_text() and (ws / "skills" / ".skill_conflicts.json").is_file()


def test_the_git_helpers_of_the_flush_are_scoped_to_the_exact_paths(tmp_path):
    from kriya.skills.skill import git_commit_paths
    ws = _repo(tmp_path, {"widgetlib": ("", RULE + "\n")})
    (ws / "skills" / "widgetlib" / "rules.txt").write_text("changed\n")
    (ws / "other.txt").write_text("not mine\n")
    _git(ws, "add", "other.txt")

    assert git_commit_paths(str(ws / "skills"), [str(ws / "skills" / "widgetlib" / "rules.txt")], "scoped") is True

    assert _git(ws, "show", "--name-only", "--format=", "HEAD") == "skills/widgetlib/rules.txt"
    assert "A  other.txt" in _git(ws, "status", "--porcelain")
    assert subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=ws, capture_output=True).returncode == 0


def test_manifest_ignores_what_the_fingerprint_ignores_and_records_symlinks(tmp_path):
    root = tmp_path / "skills"
    (root / "a" / "__pycache__").mkdir(parents=True)
    (root / "a" / "__pycache__" / "x.pyc").write_bytes(b"x")
    (root / "a" / ".DS_Store").write_bytes(b"x")
    (root / "a" / "rules.txt").write_text("r\n")
    os.symlink("rules.txt", root / "a" / "link.txt")

    assert sorted(manifest(str(root))) == ["a/link.txt", "a/rules.txt"] and manifest(str(root))["a/link.txt"] == "symlink:rules.txt"
    with patch("kriya.skills.run_shadow.os.path.isdir", return_value=False):
        assert manifest(str(root)) == {}


# ------------------------------------------------------------------------------------------------ the stage-level readers

def _stage_engine_with_shadow(tmp_path):
    from test_work_unit_stages import _engine
    workflow = _engine(tmp_path)
    (tmp_path / "repo").mkdir()
    ws = _repo(tmp_path / "repo", {"widgetlib": ("", RULE + "\n")})
    workflow.kernel.config.paths.skills = str(ws / "skills")
    skills = SkillEngine.from_config(workflow.kernel.config, workspace_path=str(ws))
    skills.discover_and_load()
    assert skills.activate_run_shadow(str(ws), _state(tmp_path)) is True
    return workflow, skills, ws


def test_the_knowledge_guard_of_a_shadowed_run_reads_and_scaffolds_into_the_shadow(tmp_path):
    import asyncio

    from test_work_unit_stages import _ctx, _deps, _Guard

    workflow, skills, ws = _stage_engine_with_shadow(tmp_path)
    guard = _Guard(lambda text: [])

    asyncio.run(__import__("kriya.workflow.stage_knowledge_guard", fromlist=["check_knowledge_guard"]).check_knowledge_guard(
        _ctx(tmp_path, skill_engine_override=skills), _deps(workflow, KnowledgeGuard=guard)))

    assert guard.kwargs["skills_dir"] == skills.skills_dir != str(ws / "skills")


def test_the_knowledge_guard_of_a_run_without_a_shadow_keeps_the_configured_root(tmp_path):
    import asyncio

    from test_work_unit_stages import _ctx, _deps, _engine, _Guard

    from kriya.workflow.stage_knowledge_guard import check_knowledge_guard
    workflow = _engine(tmp_path)
    guard = _Guard(lambda text: [])

    asyncio.run(check_knowledge_guard(_ctx(tmp_path), _deps(workflow, KnowledgeGuard=guard)))
    assert guard.kwargs["skills_dir"] == workflow.kernel.config.paths.skills
    external = SkillEngine(str(tmp_path / "external"), load_global=False, workspace_path=str(tmp_path))
    asyncio.run(check_knowledge_guard(_ctx(tmp_path, skill_engine_override=external), _deps(workflow, KnowledgeGuard=guard)))
    assert guard.kwargs["skills_dir"] == workflow.kernel.config.paths.skills          # an engine with no shadow changes nothing


def test_the_lesson_staging_of_a_shadowed_run_goes_to_the_shadow(tmp_path):
    import asyncio

    from test_work_unit_stages import CALC_NEW, _apply_ctx, _late_deps

    from kriya.knowledge.channels.live_failure import LiveFailureContext
    from kriya.workflow.stage_apply import extract_lessons
    workflow, skills, ws = _stage_engine_with_shadow(tmp_path)
    staged = []

    class Staging:
        @staticmethod
        def stage_facts(folder, facts):
            staged.append(folder)
            return list(facts)

    class Channel:
        def __init__(self, llm):
            pass

        async def extract(self, context):
            return ["a fact"]

    ctx = _apply_ctx(tmp_path, repo_slug="calc-repo", skills_dir=skills.skills_dir)     # what the analysis stage records for the unit
    (tmp_path / "ws" / "calc.py").write_text(CALC_NEW)
    asyncio.run(extract_lessons(ctx, _late_deps(workflow, load_lesson_extraction=lambda: (Staging, Channel, LiveFailureContext)),
                                should_extract_hard_won_knowledge=True, self_corrected_outcome=None))

    assert staged == [os.path.join(skills.skills_dir, "auto-calc-repo")] and str(ws) not in staged[0]


def test_the_auto_accrual_scaffold_of_a_shadowed_run_lands_in_the_shadow(tmp_path):
    from kriya.tools.knowledge import KnowledgeGuard
    workflow, skills, ws = _stage_engine_with_shadow(tmp_path)
    guard = KnowledgeGuard(skills_dir=skills.effective_skills_dir(workflow.kernel.config.paths.skills), cutoff_date_str="2023-12-01",
                           offline=True, memory_dir=str(tmp_path / "memory"))
    head = _git(ws, "rev-parse", "HEAD")

    template = guard.generate_skill_template("org.example:mylib-core", "1.4.2")

    assert template.startswith(skills.skills_dir) and os.path.isfile(os.path.join(template, "skill.yaml"))
    assert _canonical_unchanged(ws) and _git(ws, "status", "--porcelain") == "" and _git(ws, "rev-parse", "HEAD") == head
    skills.discover_and_load()
    assert skills.persist_run_learning() == []
    assert (ws / "skills" / os.path.basename(template) / "skill.yaml").is_file()


def test_the_shared_install_skills_write_refusal_is_judged_on_the_durable_path_not_the_shadow_copy(tmp_path):
    """A monorepo that contains the Kriya install: a workspace sibling of the install's shared skills shares its git work tree, so the
    shared skills directory is shadowed - but a write to it must still be REFUSED (MA7.4), which only the canonical path shows."""
    repo = _repo(tmp_path, {})
    shared = repo / "kriya_install" / "skills"
    (shared / "sharedlib").mkdir(parents=True)
    (shared / "sharedlib" / "skill.yaml").write_text("name: sharedlib\ndescription: t\ntags: [sharedlib]\n")
    workspace = repo / "proj"
    workspace.mkdir()
    with patch("kriya.skills.skill.get_global_skills_dir", return_value=str(shared)):
        engine = SkillEngine(str(shared), load_global=False, workspace_path=str(workspace))
        engine.discover_and_load()
        assert engine.activate_run_shadow(str(workspace), _state(tmp_path)) is True
        assert engine.get_skill("sharedlib").source_path.startswith(os.path.realpath(_state(tmp_path)))

        assert engine.mark_gap_acknowledged("sharedlib") is False                 # refused, exactly as without a shadow

    assert "verification_gap_acknowledged" not in open(os.path.join(engine.skills_dir, "sharedlib", "skill.yaml")).read()


def test_a_state_directory_in_some_other_git_work_tree_is_allowed_and_nothing_of_the_shadow_is_ever_committed_there(tmp_path):
    """A home directory that is a dotfiles repository holds ~/.kriya/state: the shadow must work there, and the commit helper must
    never add or commit a run-local copy to that repository."""
    from kriya.skills.skill import git_commit_if_tracked
    other = tmp_path / "dotfiles"
    other.mkdir()
    _git(other, "init", "-q")
    _git(other, "config", "user.email", "t@t")
    _git(other, "config", "user.name", "T")
    (other / "seed").write_text("x")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "seed")
    ws, engine = _shadowed(tmp_path)

    assert engine.activate_run_shadow(str(ws), str(other / "state")) is True
    engine.create_skill_skeleton("newlib")
    engine.mark_gap_acknowledged("widgetlib")
    assert git_commit_if_tracked(engine.skills_dir, "must be a no-op") is True

    assert int(_git(other, "rev-list", "--count", "HEAD")) == 1
    assert not [line for line in _git(other, "status", "--porcelain").splitlines() if not line.startswith("??")]     # nothing staged either
    assert _canonical_unchanged(ws)
    assert engine.persist_run_learning() == []
    assert (ws / "skills" / "newlib" / "skill.yaml").is_file()


def test_a_shadow_path_stops_being_one_once_the_shadow_is_gone(tmp_path):
    from kriya.skills.run_shadow import is_shadow_path
    ws, engine = _shadowed(tmp_path)
    engine.activate_run_shadow(str(ws), _state(tmp_path))
    copy = engine.skills_dir
    assert is_shadow_path(copy) and is_shadow_path(copy + "/widgetlib/skill.yaml") and not is_shadow_path(str(ws / "skills"))

    engine.discard_run_learning()

    assert not is_shadow_path(copy)


def test_a_state_directory_in_the_same_work_tree_as_a_sub_directory_workspace_is_refused(tmp_path):
    repo = _repo(tmp_path, {"widgetlib": ("", RULE + "\n")})
    workspace = repo / "service"
    workspace.mkdir()
    engine = SkillEngine(str(repo / "skills"), load_global=False, workspace_path=str(workspace))
    engine.discover_and_load()

    with pytest.raises(RunShadowRefused) as refusal:
        engine.activate_run_shadow(str(workspace), str(repo / "state"))       # outside the workspace directory, inside its repository

    assert refusal.value.reason_code == REASON_ROOT_IN_WORK_TREE and not os.path.exists(repo / "state" / "run_shadow")
