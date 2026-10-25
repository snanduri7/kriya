"""Mid-run workspace commits vs the S3 Git binding (registry: GIT-MIDRUN-COMMITS-S3-001).

S3 binds a contained gate to the Git identity of the checkout it verifies: the mounted checkout's HEAD must equal the run's
original workspace HEAD (`bound_git_metadata` -> GIT_METADATA_STALE). Skills and learned knowledge live under `paths.skills`,
which by default is `./skills` INSIDE the run's own workspace repository, and the skill writers commit into whatever git work
tree holds them. A skill/knowledge write between a run's sandbox creation and its last bound gate therefore moves the workspace
under the gates (P-B was one such site: skill verification).

This file is the DYNAMIC half of the census (tests/architecture/test_workspace_commit_sites.py is the static half). It drives
the REAL production writers and the REAL runs against a git workspace whose `paths.skills` is inside the repository, with the
real isolated sandbox worktree (`create_git_worktree`), and measures for each site: did HEAD move, did the content change, how
many commits, and would a bound gate over the sandbox now be refused (the real `bound_git_metadata` predicate).
"""
import json
import subprocess
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.knowledge import staging as knowledge_staging
from kriya.knowledge.schema import KnowledgeFact
from kriya.skills.skill import (
    HeldSkillVerification,
    SkillEngine,
    mark_rules_verified,
    record_conflict_resolution,
    record_rule_provenance,
)
from kriya.tools.git_metadata import GIT_METADATA_STALE, GitMetadataError, bound_git_metadata
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.skill_extraction import _stage_skill_conflicts, _write_skill_extraction
from kriya.workflow.worktree import create_git_worktree

RULE = "Widgets must be printed in uppercase."


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path, skills):
    """A git workspace tracking its own ./skills (the default `paths.skills` arrangement): name -> (extra yaml, rules text)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "test@test.com")
    _git(ws, "config", "user.name", "Test")
    for name, (extra, rules) in skills.items():
        folder = ws / "skills" / name
        folder.mkdir(parents=True)
        (folder / "skill.yaml").write_text(f"name: {name}\ndescription: Test\ntags: [{name}]\n{extra}")
        (folder / "rules.txt").write_text(rules)
    (ws / "README.md").write_text("placeholder\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "initial")
    return ws


def _world(tmp_path):
    """The workspace, the run's sandbox of it (as `create_git_worktree` makes it) and a skill engine over the tracked skills."""
    ws = _repo(tmp_path, {"widgetlib": ("", RULE + "\n"), "otherlib": ("", RULE + "\n")})
    sandbox = create_git_worktree(str(ws))
    engine = SkillEngine(str(ws / "skills"), load_global=False, workspace_path=str(ws))
    engine.discover_and_load()
    return ws, sandbox, engine


def _bind(ws, sandbox, tmp_path):
    """The real S3 predicate at a bound gate over the sandbox: 'bound', or the typed refusal's reason code."""
    try:
        bound_git_metadata(mounted_workspace=sandbox, original_workspace=str(ws), state_root=str(tmp_path / "state"), enabled=True)
    except GitMetadataError as refusal:
        return refusal.reason_code
    return "bound"


# ------------------------------------------------------------------------------------------ the site-by-site measurement

def _facts():
    return [KnowledgeFact(category="APIs", key="k", value="Use Widget.render() not Widget.draw().", source_channel="live_failure",
                          extraction_confidence="llm_from_quote", provenance="run")]


def _drivers(ws, engine):
    skills = str(ws / "skills")
    widget = engine.get_skill("widgetlib")
    held = HeldSkillVerification("widgetlib", "widgetlib 2.0.0", None)
    return {
        "create_skill_skeleton": lambda: engine.create_skill_skeleton("newlib"),
        "mark_gap_acknowledged": lambda: engine.mark_gap_acknowledged("widgetlib"),
        "write_skill_extraction": lambda: _write_skill_extraction(
            widget, {"rules": ["Always close every widget handle after use."], "examples": {"ex.py": "print(2)\n"}, "conflicts": []},
            source="human_text"),
        "stage_skill_conflicts": lambda: _stage_skill_conflicts(
            widget, [{"candidate_rule": "Widgets are lowercase.", "conflicts_with": RULE, "reason": "opposite"}]),
        "record_conflict_resolution": lambda: record_conflict_resolution(skills, "widgetlib", RULE, "otherlib", RULE, "prefer_a"),
        "record_rule_provenance": lambda: record_rule_provenance(widget.source_path, RULE, "human_text"),
        "mark_rules_verified": lambda: (record_rule_provenance(widget.source_path, RULE, "human_text"),
                                        mark_rules_verified(widget.source_path, [RULE])),
        "stage_facts": lambda: knowledge_staging.stage_facts(str(ws / "skills" / "auto-ws"), _facts()),
        "apply_held_verifications": lambda: (engine.hold_verifications([held]), engine.apply_held_verifications()),
    }


def _measure(ws, sandbox, tmp_path, act):
    def identity():
        return {"head": _git(ws, "rev-parse", "HEAD"), "content": compute_workspace_content_hash(str(ws)),
                "commits": int(_git(ws, "rev-list", "--count", "HEAD")), "bind": _bind(ws, sandbox, tmp_path)}
    before = identity()
    act()
    after = identity()
    return {"commits": after["commits"] - before["commits"], "head_moved": before["head"] != after["head"],
            "content_changed": before["content"] != after["content"], "bind_before": before["bind"], "bind_after": after["bind"],
            "sandbox_head_unchanged": _git(sandbox, "rev-parse", "HEAD") == before["head"]}


# site -> the number of commits it makes. Measured with a real sandbox worktree over a real git workspace.
MEASURED_COMMITS = {
    "create_skill_skeleton": 1, "mark_gap_acknowledged": 1, "write_skill_extraction": 3, "stage_skill_conflicts": 1,
    "record_conflict_resolution": 1, "record_rule_provenance": 1, "mark_rules_verified": 2, "apply_held_verifications": 1,
}


@pytest.mark.parametrize("site", sorted(MEASURED_COMMITS))
def test_every_committing_skill_writer_moves_the_workspace_head_so_a_later_bound_gate_over_an_earlier_sandbox_is_stale(tmp_path, site):
    ws, sandbox, engine = _world(tmp_path)

    result = _measure(ws, sandbox, tmp_path, _drivers(ws, engine)[site])

    assert result == {"commits": MEASURED_COMMITS[site], "head_moved": True, "content_changed": True, "bind_before": "bound",
                      "bind_after": GIT_METADATA_STALE, "sandbox_head_unchanged": True}


def test_staging_a_lesson_writes_a_file_but_commits_nothing_so_the_binding_holds_and_only_the_workspace_content_moves(tmp_path):
    ws, sandbox, engine = _world(tmp_path)

    result = _measure(ws, sandbox, tmp_path, _drivers(ws, engine)["stage_facts"])

    assert result == {"commits": 0, "head_moved": False, "content_changed": True, "bind_before": "bound", "bind_after": "bound",
                      "sandbox_head_unchanged": True}
    assert (ws / "skills" / "auto-ws" / "staged_knowledge.json").is_file()
    status = _git(ws, "status", "--porcelain").splitlines()
    assert "?? skills/auto-ws/" in status and not [line for line in status if not line.startswith("??")]   # untracked only


# ------------------------------------------------------------------------------------------ real standalone flows

class _Timeline:
    """The ordered facts of one real run, recorded where they happen: skill commits (with the workspace HEAD they left), the
    sandbox's creation and, at every gate outcome and the terminal apply, the real S3 predicate over that sandbox."""

    def __init__(self, ws, tmp_path):
        self.ws, self.tmp_path, self.events, self.sandbox = ws, tmp_path, [], None

    def bind(self):
        return _bind(self.ws, self.sandbox, self.tmp_path) if self.sandbox else "no-sandbox"

    def index(self, kind):
        return [i for i, event in enumerate(self.events) if event[0] == kind]


def _observers(timeline):
    """Patches that only OBSERVE (each delegates to the real function): the commit helper, the sandbox creation, the gate
    outcomes and the terminal apply of a real `run_generation_workflow`."""
    import kriya.skills.skill as skill_module
    import kriya.workflow.development_loop as development_loop
    import kriya.workflow.workflow as workflow_module
    from kriya.workflow.state import GenerationState

    real_commit, real_sandbox = skill_module.git_commit_if_tracked, workflow_module.create_git_worktree
    real_record, real_apply = GenerationState.record_gate_outcome, development_loop.apply_terminal_candidate

    def commit(path, message):
        committed = real_commit(path, message)
        timeline.events.append(("commit", message, _git(timeline.ws, "rev-parse", "HEAD")))
        return committed

    def sandbox(repo_path):
        timeline.sandbox = real_sandbox(repo_path)
        timeline.events.append(("sandbox", timeline.sandbox, _git(timeline.ws, "rev-parse", "HEAD")))
        return timeline.sandbox

    def record(self, outcome, *args, **kwargs):
        timeline.events.append(("gate", outcome.get("type"), timeline.bind()))
        return real_record(self, outcome, *args, **kwargs)

    def apply(*args, **kwargs):
        timeline.events.append(("terminal_apply", None, timeline.bind()))
        return real_apply(*args, **kwargs)

    return [patch.object(skill_module, "git_commit_if_tracked", commit),
            patch.object(workflow_module, "create_git_worktree", sandbox),
            patch.object(GenerationState, "record_gate_outcome", record),
            patch.object(development_loop, "apply_terminal_candidate", apply)]


async def _run_standalone(tmp_path, skills, answers, goal, **callbacks):
    from kriya.workflow.workflow import WorkflowEngine
    ws = _repo(tmp_path, skills)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(ws / "skills")
    cfg.autonomy.run_verification_enabled = False
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=answers)
    timeline = _Timeline(ws, tmp_path)
    with ExitStack() as stack:
        for observer in _observers(timeline):
            stack.enter_context(observer)
        result = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(goal=goal, workspace_path=str(ws), **callbacks)
    return result, timeline


GENERATION = ["Step 1: Write code", "Design: Write app.py", '[{"filepath": "app.py", "content": "print(1)\\n"}]', "Review: Approved"]


def _assert_every_skill_commit_precedes_the_sandbox_and_every_bound_gate_saw_one_identity(timeline, expected_commits):
    sandbox_at, commits = timeline.index("sandbox"), timeline.index("commit")
    assert len(commits) == expected_commits and len(sandbox_at) == 1, timeline.events
    assert max(commits) < sandbox_at[0], timeline.events                      # the sandbox is created from the final HEAD
    assert timeline.events[sandbox_at[0]][2] == timeline.events[commits[-1]][2]
    gates = [event for event in timeline.events if event[0] in ("gate", "terminal_apply")]
    assert gates and {event[2] for event in gates} == {"bound"}, timeline.events


@pytest.mark.asyncio
async def test_a_standalone_skill_gap_decline_commits_before_the_sandbox_exists_so_every_gate_binds(tmp_path):
    skills = {"widgetlib": ("", RULE + "\n")}

    result, timeline = await _run_standalone(
        tmp_path, skills, GENERATION, "Build something with the widgetlib skill", skill_gap_callback=lambda reason, names: None)

    assert result["quality_gates_passed"] is True
    assert [e[1] for e in timeline.events if e[0] == "commit"] == [
        "Kriya: set verification_gap_acknowledged=True on skill 'widgetlib'"]
    _assert_every_skill_commit_precedes_the_sandbox_and_every_bound_gate_saw_one_identity(timeline, 1)


ALPHA_RULE = "Broker must bind AMQP to port 5672."
BETA_RULE = "Configure the broker to listen on port 5673 for AMQP clients."
CONFLICT = json.dumps({"conflicts": [{"rule_a_index": 1, "rule_b_index": 1, "explanation": "Both configure the same broker port."}]})


@pytest.mark.asyncio
async def test_a_standalone_extraction_and_conflict_resolution_commit_before_the_sandbox_exists_so_every_gate_binds(tmp_path):
    skills = {"brokeralpha": ("", ""), "brokerbeta": ("verified: true\n", BETA_RULE + "\n")}
    extraction = json.dumps({"rules": [ALPHA_RULE], "examples": {}, "conflicts": []})

    result, timeline = await _run_standalone(
        tmp_path, skills, [extraction, CONFLICT, *GENERATION], "Build something using both the brokeralpha and brokerbeta skills",
        skill_gap_callback=lambda reason, names: "Reference material establishing the alpha broker's port rule.",
        skill_conflict_callback=lambda *args: "prefer_b")

    assert result["quality_gates_passed"] is True
    messages = [e[1] for e in timeline.events if e[0] == "commit"]
    assert any("add 1 rule(s) to skill 'brokeralpha'" in m for m in messages)
    assert any("record skill-conflict resolution" in m for m in messages)
    _assert_every_skill_commit_precedes_the_sandbox_and_every_bound_gate_saw_one_identity(timeline, len(messages))


# ------------------------------------------------------------------------------------------ real enforce flow

async def _run_enforce_with_site(tmp_path, site):
    """The real enforce route (WorkflowController -> EnforceLoop -> TerminalGateService), two subtasks, `paths.skills` inside the
    repository. Subtask 1's stand-in engine runs the production writer `site` the way its stage does (inside the run, after the
    plan's sandbox exists); the real terminal gate service is observed with the real S3 predicate over the plan's sandbox."""
    from _strict_doubles import strict_kernel
    from test_workflow_controller_enforce import _patched, _two_subtask_plan, _workflow_engine

    from kriya.tools.knowledge import GapReport
    from kriya.workflow.terminal_gate_service import TerminalGateService
    from kriya.workflow.workflow_controller import WorkflowController

    ws = _repo(tmp_path, {"widgetlib": ("", RULE + "\n"), "otherlib": ("", RULE + "\n")})
    config = AppConfig()
    config.paths.memory = str(tmp_path / "memory")
    config.paths.skills = str(ws / "skills")
    engine = SkillEngine(str(ws / "skills"), load_global=False, workspace_path=str(ws))
    engine.discover_and_load()
    we = _workflow_engine()
    we.kernel = strict_kernel(config)
    seen = {"sandbox": None, "subtask_binds": [], "terminal_bind": None, "head_at_sandbox": None}

    async def fake_run(**kwargs):
        sandbox = kwargs["workspace_path"]
        if seen["sandbox"] is None:
            seen["sandbox"], seen["head_at_sandbox"] = sandbox, _git(sandbox, "rev-parse", "HEAD")
            _drivers(ws, engine)[site]()                                   # the stage's own writer, after the sandbox exists
        seen["subtask_binds"].append(_bind(ws, sandbox, tmp_path))
        name = "a.py" if len(seen["subtask_binds"]) == 1 else "b.py"
        (__import__("pathlib").Path(sandbox) / name).write_text(f"# {name}")
        return {"status": "success", "quality_gates_passed": True, "files": [name]}

    real_run = TerminalGateService.run

    from kriya.tools.validate import PolymorphicValidator
    real_init, terminal = PolymorphicValidator.__init__, {"active": False, "validators": []}

    def spy_init(validator, *args, **kwargs):
        real_init(validator, *args, **kwargs)
        if terminal["active"]:
            terminal["validators"].append(validator)

    async def observed_run(self, request, emit):
        seen["terminal_bind"] = _bind(ws, seen["sandbox"], tmp_path)
        terminal["active"] = True
        try:
            return await real_run(self, request, emit)
        finally:
            terminal["active"] = False
            # the production S3 owner, asked by the validators the terminal gates actually built
            for validator in terminal["validators"]:
                try:
                    validator._bound_git_metadata(validator.workspace_path)
                    seen.setdefault("validator_binds", []).append(("bound", validator.workspace_path, validator.original_workspace_path))
                except GitMetadataError as refusal:
                    seen.setdefault("validator_binds", []).append((refusal.reason_code, validator.workspace_path, validator.original_workspace_path))

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(_two_subtask_plan())
    with ExitStack() as stack:
        for context in (p1, p2, p3, patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=GapReport()),
                        patch("kriya.skills.skill.SkillEngine.from_config", return_value=engine),
                        patch.object(TerminalGateService, "run", observed_run),
                        patch.object(PolymorphicValidator, "__init__", spy_init)):
            stack.enter_context(context)
        result = await WorkflowController(we).execute("goal", str(ws), migration_mode="enforce")
    return result, seen


OPEN_DEFECT = ("GIT-MIDRUN-COMMITS-S3-001: an in-run learning write commits into the workspace after the enforce plan's sandbox "
               "exists, so the terminal gates' S3 binding (sandbox vs workspace HEAD) is stale")
# the in-run learning writers whose commit lands between the plan's sandbox and its terminal gates (static row status "open")
ENFORCE_REACHABLE = ["create_skill_skeleton", "mark_gap_acknowledged", "write_skill_extraction", "stage_skill_conflicts",
                     "record_conflict_resolution"]


@pytest.mark.asyncio
@pytest.mark.parametrize("site", [pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=OPEN_DEFECT)) for name in ENFORCE_REACHABLE]
                         + ["stage_facts"])
async def test_an_enforce_run_hands_its_terminal_gates_the_identity_its_plan_sandbox_was_created_from(tmp_path, site):
    result, seen = await _run_enforce_with_site(tmp_path, site)

    assert result.legacy_result["status"] == "success"
    assert _git(seen["sandbox"], "rev-parse", "HEAD") == seen["head_at_sandbox"]
    assert seen["terminal_bind"] == "bound"                    # the real S3 predicate over the plan sandbox, at the terminal gates
