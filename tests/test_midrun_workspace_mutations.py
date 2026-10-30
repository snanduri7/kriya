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
import os
import subprocess
from contextlib import ExitStack
from types import SimpleNamespace
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
    find_conflict_resolution,
    load_rule_provenance,
    mark_rules_verified,
    record_conflict_resolution,
    record_rule_provenance,
)
from kriya.tools.git_metadata import GIT_METADATA_STALE, GitMetadataError, bound_git_metadata
from kriya.tools.knowledge import KnowledgeGuard
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
        "record_conflict_resolution": lambda: record_conflict_resolution(
            engine.effective_skills_dir(skills), "widgetlib", RULE, "otherlib", RULE, "prefer_a"),
        "record_rule_provenance": lambda: record_rule_provenance(widget.source_path, RULE, "human_text"),
        "mark_rules_verified": lambda: (record_rule_provenance(widget.source_path, RULE, "human_text"),
                                        mark_rules_verified(widget.source_path, [RULE])),
        "generate_skill_template": lambda: KnowledgeGuard(
            skills_dir=engine.effective_skills_dir(skills), cutoff_date_str="2023-12-01", offline=True,
            memory_dir=str(ws.parent / "memory")).generate_skill_template("org.example:mylib-core", "1.4.2"),
        "stage_facts": lambda: knowledge_staging.stage_facts(engine.effective_skills_dir(str(ws / "skills")) + "/auto-ws", _facts()),
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
    "create_skill_skeleton": 1, "write_skill_extraction": 3, "stage_skill_conflicts": 1,
    "record_conflict_resolution": 1, "record_rule_provenance": 1, "mark_rules_verified": 2, "apply_held_verifications": 1,
}


@pytest.mark.parametrize("site", sorted(MEASURED_COMMITS))
def test_every_committing_skill_writer_moves_the_workspace_head_so_a_later_bound_gate_over_an_earlier_sandbox_is_stale(tmp_path, site):
    ws, sandbox, engine = _world(tmp_path)

    result = _measure(ws, sandbox, tmp_path, _drivers(ws, engine)[site])

    assert result == {"commits": MEASURED_COMMITS[site], "head_moved": True, "content_changed": True, "bind_before": "bound",
                      "bind_after": GIT_METADATA_STALE, "sandbox_head_unchanged": True}


def test_a_workspace_skill_gap_decline_is_run_local_so_it_changes_nothing_the_gates_bind_to(tmp_path):
    """W3-1: the decline used to be a committing writer (one commit, HEAD moved, sandbox STALE). It is now held by the engine for the
    run: no commit, no HEAD move, no content change, the binding holds - and the engine still remembers it."""
    ws, sandbox, engine = _world(tmp_path)

    result = _measure(ws, sandbox, tmp_path, _drivers(ws, engine)["mark_gap_acknowledged"])

    assert result == {"commits": 0, "head_moved": False, "content_changed": False, "bind_before": "bound", "bind_after": "bound",
                      "sandbox_head_unchanged": True}
    assert engine.is_gap_acknowledged(engine.get_skill("widgetlib")) is True
    assert _git(ws, "status", "--porcelain", "--", "skills") == ""


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


async def _run_standalone(tmp_path, skills, answers, goal, kriya_verified=(), **callbacks):
    """`kriya_verified`: skill names Kriya itself already verified earlier in this run (a held verification, the run-local owner);
    B3: a `verified: true` line in a workspace skill.yaml no longer stands for that."""
    from kriya.skills.skill import HeldSkillVerification
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
        if kriya_verified:
            engine = SkillEngine.from_config(cfg, workspace_path=str(ws))
            engine.discover_and_load()
            engine.hold_verifications([HeldSkillVerification(name, "held earlier in this run", None) for name in kriya_verified])
            callbacks["skill_engine_override"] = engine
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
async def test_a_standalone_skill_gap_decline_commits_nothing_so_every_gate_binds(tmp_path):
    """W3-1: the decline of a workspace skill is run-local. It is still asked (the callback runs), but no skill commit happens."""
    skills = {"widgetlib": ("", RULE + "\n")}
    asked = []

    def decline(reason, names):
        asked.append(names)

    result, timeline = await _run_standalone(tmp_path, skills, GENERATION, "Build something with the widgetlib skill", skill_gap_callback=decline)

    assert result["quality_gates_passed"] is True
    assert asked == [["widgetlib"]]
    assert timeline.index("commit") == [] and len(timeline.index("sandbox")) == 1, timeline.events
    gates = [event for event in timeline.events if event[0] in ("gate", "terminal_apply")]
    assert gates and {event[2] for event in gates} == {"bound"}, timeline.events
    assert "verification_gap_acknowledged" not in (timeline.ws / "skills" / "widgetlib" / "skill.yaml").read_text()


ALPHA_RULE = "Broker must bind AMQP to port 5672."
BETA_RULE = "Configure the broker to listen on port 5673 for AMQP clients."
CONFLICT = json.dumps({"conflicts": [{"rule_a_index": 1, "rule_b_index": 1, "explanation": "Both configure the same broker port."}]})


@pytest.mark.asyncio
async def test_a_standalone_extraction_and_conflict_resolution_commit_before_the_sandbox_exists_so_every_gate_binds(tmp_path):
    skills = {"brokeralpha": ("", ""), "brokerbeta": ("", BETA_RULE + "\n")}
    extraction = json.dumps({"rules": [ALPHA_RULE], "examples": {}, "conflicts": []})

    result, timeline = await _run_standalone(
        tmp_path, skills, [extraction, CONFLICT, *GENERATION], "Build something using both the brokeralpha and brokerbeta skills",
        kriya_verified=("brokerbeta",),
        skill_gap_callback=lambda reason, names: "Reference material establishing the alpha broker's port rule.",
        skill_conflict_callback=lambda *args: "prefer_b")

    assert result["quality_gates_passed"] is True
    messages = [e[1] for e in timeline.events if e[0] == "commit"]
    assert any("add 1 rule(s) to skill 'brokeralpha'" in m for m in messages)
    assert any("record skill-conflict resolution" in m for m in messages)
    _assert_every_skill_commit_precedes_the_sandbox_and_every_bound_gate_saw_one_identity(timeline, len(messages))


# ------------------------------------------------------------------------------------------ real enforce flow

async def _run_enforce_with_site(tmp_path, site, extra_patches=(), terminal_fails=False, during_second_subtask=None, seen_out=None, explode=False,
                                 goal="goal", repo_files=None, spec_compliance=False):
    """The real enforce route (WorkflowController -> EnforceLoop -> TerminalGateService), two subtasks, `paths.skills` inside the
    repository. Subtask 1's stand-in engine runs the production writer `site` the way its stage does (inside the run, after the
    plan's sandbox exists); the real terminal gate service is observed with the real S3 predicate over the plan's sandbox."""
    from _strict_doubles import strict_kernel
    from test_workflow_controller_enforce import _patched, _two_subtask_plan, _workflow_engine

    from kriya.tools.knowledge import GapReport
    from kriya.workflow.terminal_gate_service import TerminalGateService
    from kriya.workflow.workflow_controller import WorkflowController

    ws = _repo(tmp_path, {"widgetlib": ("", RULE + "\n"), "otherlib": ("", RULE + "\n")})
    for rel, text in (repo_files or {}).items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    if repo_files:
        _git(ws, "add", "-A")
        _git(ws, "commit", "-q", "-m", "project files")
    config = AppConfig()
    config.autonomy.spec_compliance_enabled = spec_compliance
    config.paths.memory = str(tmp_path / "memory")
    config.paths.skills = str(ws / "skills")
    engine = SkillEngine(str(ws / "skills"), load_global=False, workspace_path=str(ws))
    engine.discover_and_load()
    we = _workflow_engine()
    we.kernel = strict_kernel(config)
    if spec_compliance:
        verdicts = [{"id": "REQ-1", "verdict": "satisfied", "evidence": "the candidate adds the feature"},
                    {"id": "REQ-2", "verdict": "unverifiable", "evidence": "only the named test can say"}]
        we.spec_compliance = SimpleNamespace(check=AsyncMock(return_value={"requirement_verdicts": verdicts}))   # REQ-2 stays UNVERIFIED, so the named-test closer runs
    seen = seen_out if seen_out is not None else {}
    seen.update({"sandbox": None, "subtask_binds": [], "terminal_bind": None, "head_at_sandbox": None, "ws_at_sandbox": None,
                 "ws_at_terminal": None, "later_sees": None, "engine": engine, "ws": ws})
    probe = PROBES[site]

    def workspace_identity():
        return {"head": _git(ws, "rev-parse", "HEAD"), "content": compute_workspace_content_hash(str(ws)),
                "status": _git(ws, "status", "--porcelain")}

    async def fake_run(**kwargs):
        sandbox = kwargs["workspace_path"]
        if seen["sandbox"] is None:
            seen["sandbox"], seen["head_at_sandbox"] = sandbox, _git(sandbox, "rev-parse", "HEAD")
            seen["ws_at_sandbox"] = workspace_identity()
            _drivers(ws, engine)[site]()                                   # the stage's own writer, after the sandbox exists
        else:
            engine.discover_and_load()                                     # what the next unit's analysis stage does
            seen["later_sees"] = probe(engine, ws)
        seen["subtask_binds"].append(_bind(ws, sandbox, tmp_path))
        if during_second_subtask is not None and len(seen["subtask_binds"]) == 2:
            during_second_subtask(ws)
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
        seen["ws_at_terminal"] = workspace_identity()
        if explode:
            raise RuntimeError("terminal gates exploded")
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
    if terminal_fails:
        from kriya.workflow.terminal_gate_service import TerminalGateReport

        async def failing_gates(self, request, emit):
            seen["ws_at_terminal"] = workspace_identity()
            return TerminalGateReport(ran=True, requirement_gap="a requirement is unmet")
        extra_patches = (*extra_patches, patch.object(TerminalGateService, "run", failing_gates))
    with ExitStack() as stack:
        for context in (*extra_patches, p1, p2, p3, patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=GapReport()),
                        patch("kriya.skills.skill.SkillEngine.from_config", return_value=engine),
                        *([patch.object(TerminalGateService, "run", observed_run)] if not terminal_fails else []),
                        patch.object(PolymorphicValidator, "__init__", spy_init)):
            stack.enter_context(context)
        result = await WorkflowController(we).execute(goal, str(ws), migration_mode="enforce")
    seen["engine"], seen["ws"] = engine, ws
    return result, seen



# what each in-run learning write leaves for a LATER reader: probe(engine, ws) -> True when the engine's own view holds it
def _skill_dir(engine, name="widgetlib"):
    return engine.get_skill(name).source_path


def _read(path):
    return open(path).read() if os.path.exists(path) else ""


PROBES = {
    "create_skill_skeleton": lambda engine, ws: engine._resolve_skill("newlib") is not None,
    "mark_gap_acknowledged": lambda engine, ws: engine.is_gap_acknowledged(engine.get_skill("widgetlib")),   # W3-1: run-local, survives discover_and_load
    "write_skill_extraction": lambda engine, ws: "Always close every widget handle after use." in engine.get_skill("widgetlib").rules,
    "stage_skill_conflicts": lambda engine, ws: "Widgets are lowercase." in _read(_skill_dir(engine) + "/staged_rules.txt"),
    "record_conflict_resolution": lambda engine, ws: find_conflict_resolution(
        engine.effective_skills_dir(str(ws / "skills")), "widgetlib", RULE, "otherlib", RULE) == "prefer_a",
    "record_rule_provenance": lambda engine, ws: [r["text"] for r in load_rule_provenance(_skill_dir(engine))] == [RULE],
    "mark_rules_verified": lambda engine, ws: [r["verified"] for r in load_rule_provenance(_skill_dir(engine))] == [True],
    "generate_skill_template": lambda engine, ws: os.path.isfile(engine.effective_skills_dir(str(ws / "skills")) + "/org.example-mylib-core-1.4.2/skill.yaml"),
    "stage_facts": lambda engine, ws: bool(knowledge_staging.load_staged(engine.effective_skills_dir(str(ws / "skills")) + "/auto-ws")),
    # B3: persistence still writes the claim into skill.yaml (display/compat), but a reload never believes it
    "apply_held_verifications": lambda engine, ws: engine.get_skill("widgetlib").verified_claim is True and engine.get_skill("widgetlib").verified is False,
}
IN_RUN_LEARNING_WRITERS = sorted(PROBES)
DURABLE_LEARNING_WRITERS = [site for site in IN_RUN_LEARNING_WRITERS if site != "mark_gap_acknowledged"]     # W3-1: the decline is run-local only


@pytest.mark.asyncio
@pytest.mark.parametrize("site", IN_RUN_LEARNING_WRITERS)
async def test_an_enforce_run_keeps_the_candidate_repository_untouched_until_its_terminal_verification(tmp_path, site):
    """GIT-MIDRUN-COMMITS-S3-001 acceptance: `paths.skills` inside the repository, one in-run learning write after the plan's
    sandbox exists. From the first bind to the terminal gates the workspace has zero HEAD drift and zero content drift, the
    S3 predicate over the sandbox holds at every gate, and the write is nonetheless visible to the next subtask."""
    result, seen = await _run_enforce_with_site(tmp_path, site)

    assert result.legacy_result["status"] == "success"
    assert seen["ws_at_terminal"] == seen["ws_at_sandbox"]                       # no HEAD drift, no content drift, no dirty tree
    assert _git(seen["sandbox"], "rev-parse", "HEAD") == seen["head_at_sandbox"]
    assert seen["subtask_binds"] == ["bound", "bound"] and seen["terminal_bind"] == "bound"
    assert seen["later_sees"] is True                                            # subtask 2 consumes what subtask 1 learned


NAMED_TEST_GOAL = "Add widget support.\n- test_widget keeps passing\n"
NAMED_TEST_FILES = {"tests/test_widget.py": "def test_widget():\n    assert True\n", "widget.py": "WIDGET = 1\n"}


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["create_skill_skeleton", "write_skill_extraction", "stage_skill_conflicts", "stage_facts"])
async def test_a_contained_terminal_gate_verifies_the_identity_the_run_first_bound_while_the_learning_stays_visible(tmp_path, site, monkeypatch):
    """GIT-MIDRUN-COMMITS-S3-001 integrated detector, under the S7 containment seam. The goal names an existing test, so the REAL terminal gate service builds
    its REAL validators (the candidate's and the oracle's base copy, both with `original_workspace_path` = the workspace) and runs the named test THROUGH the
    contained command flow (the census stand-in registered as "oci"). The S3 predicate must hold for every one of them, the repository must be unchanged between
    the first bind and that terminal verification, and the learning written in between must still be visible to the next subtask."""
    from kriya.tools import containment
    standin = containment._PRODUCTION_BACKEND_REGISTRY["oci"]()          # noqa: SLF001 - the census seam's instance for this test
    launches = []
    real_prepare = standin.prepare
    monkeypatch.setattr(standin, "prepare", lambda profile, command: (launches.append(profile.workspace_path), real_prepare(profile, command))[1])

    result, seen = await _run_enforce_with_site(tmp_path, site, goal=NAMED_TEST_GOAL, repo_files=NAMED_TEST_FILES, spec_compliance=True)

    assert result.legacy_result["status"] == "success"
    assert launches, "the terminal gate launched no contained command: this run did not exercise the contained terminal gate"
    binds = seen["validator_binds"]
    assert len(binds) >= 2 and {state for state, *_ in binds} == {"bound"}                    # the candidate's validator AND the oracle base's, none STALE
    assert {original for _state, _root, original in binds} == {str(seen["ws"].resolve())}      # every one is judged against the run's own workspace
    assert seen["subtask_binds"] == ["bound", "bound"] and seen["terminal_bind"] == "bound"
    assert seen["ws_at_terminal"] == seen["ws_at_sandbox"]                                      # zero HEAD drift, zero content drift from the first bind to the gate
    assert _git(seen["sandbox"], "rev-parse", "HEAD") == seen["head_at_sandbox"]
    assert seen["later_sees"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("site", DURABLE_LEARNING_WRITERS)
async def test_the_learning_becomes_durable_only_after_the_terminal_commit_as_exactly_its_own_delta(tmp_path, site):
    result, seen = await _run_enforce_with_site(tmp_path, site)
    ws, before = seen["ws"], seen["ws_at_sandbox"]

    assert "post_commit_persistence_errors" not in result.legacy_result
    assert PROBES[site](_fresh_engine(ws), ws)                                  # a fresh engine over the canonical skills sees it
    assert _git(ws, "status", "--porcelain", "--", "skills") == ""             # everything the run learned is committed ...
    assert int(_git(ws, "rev-list", "--count", f"{before['head']}..HEAD")) >= 1  # ... after the candidate's own commit
    assert not (seen["engine"].run_shadow_active)
    assert not list(_shadow_dirs(tmp_path))                                     # the shadow is gone


@pytest.mark.asyncio
async def test_a_skill_gap_decline_never_becomes_durable_even_after_a_successful_terminal_commit(tmp_path):
    """W3-1: the next subtask saw the decline (run-local), the run succeeded and committed, and a fresh engine - the next run - still asks."""
    result, seen = await _run_enforce_with_site(tmp_path, "mark_gap_acknowledged")
    ws = seen["ws"]

    assert result.legacy_result["status"] == "success" and seen["later_sees"] is True
    assert "post_commit_persistence_errors" not in result.legacy_result
    assert not PROBES["mark_gap_acknowledged"](_fresh_engine(ws), ws)
    assert "verification_gap_acknowledged" not in (ws / "skills" / "widgetlib" / "skill.yaml").read_text()
    assert _git(ws, "status", "--porcelain", "--", "skills") == "" and not list(_shadow_dirs(tmp_path))


def _fresh_engine(ws):
    fresh = SkillEngine(str(ws / "skills"), load_global=False, workspace_path=str(ws))
    fresh.discover_and_load()
    return fresh


def _shadow_dirs(tmp_path):
    import os
    state = os.environ["KRIYA_STATE_DIR"]
    base = os.path.join(state, "run_shadow")
    return [d for d in (os.listdir(base) if os.path.isdir(base) else []) if os.listdir(os.path.join(base, d))]


# ------------------------------------------------------------------------------------------ the end of the run: the transaction

def _skills_state(ws):
    return {"head": _git(ws, "rev-parse", "HEAD"), "status": _git(ws, "status", "--porcelain", "--", "skills"),
            "tree": _git(ws, "ls-tree", "-r", "HEAD", "--", "skills"),
            "files": {str(p.relative_to(ws)): p.read_bytes() for p in sorted((ws / "skills").rglob("*")) if p.is_file()}}


@pytest.mark.asyncio
@pytest.mark.parametrize("site", IN_RUN_LEARNING_WRITERS)
async def test_a_terminal_verification_failure_discards_the_shadow_and_changes_no_canonical_skill(tmp_path, site):
    result, seen = await _run_enforce_with_site(tmp_path, site, terminal_fails=True)
    ws, engine = seen["ws"], seen["engine"]

    assert result.legacy_result["status"] != "success"
    assert seen["ws_at_terminal"] == seen["ws_at_sandbox"]
    assert _skills_state(ws)["status"] == "" and _git(ws, "rev-parse", "HEAD") == seen["head_at_sandbox"]
    assert not PROBES[site](_fresh_engine(ws), ws)                         # nothing learned is durable
    assert engine.run_shadow_active is False and engine.skills_dir == engine.canonical_skills_dir
    assert not list(_shadow_dirs(tmp_path))
    assert engine.get_skill("widgetlib").verified is False and engine.is_effectively_verified(engine.get_skill("widgetlib")) is False


@pytest.mark.asyncio
async def test_a_commit_that_fails_after_the_flush_wrote_the_files_restores_the_canonical_skills_and_claims_nothing(tmp_path):
    """Branch ref locked: `git add` succeeds, `git commit` cannot move the ref. The flush has already written the files and
    staged them; it must put files AND index back, report it, and never claim the learning durable."""
    def lock_the_branch(ws):
        branch = _git(ws, "symbolic-ref", "HEAD")
        (ws / ".git" / f"{branch}.lock").write_text("")

    result, seen = await _run_enforce_with_site(tmp_path, "write_skill_extraction", during_second_subtask=lock_the_branch)
    ws, engine = seen["ws"], seen["engine"]
    errors = result.legacy_result["post_commit_persistence_errors"]

    assert [e["operation"] for e in errors] == ["skill_learning_flush"] and errors[0]["reason_code"] == "SKILLS_FLUSH_FAILED"
    assert "restored to their pre-flush state" in errors[0]["error"]
    assert _git(ws, "rev-parse", "HEAD") == seen["head_at_sandbox"]
    assert _git(ws, "status", "--porcelain", "--", "skills") == ""          # no dirty learning residue, nothing left staged
    assert not PROBES["write_skill_extraction"](_fresh_engine(ws), ws)
    assert not PROBES["write_skill_extraction"](engine, ws)                  # the engine does not claim it either
    assert not list(_shadow_dirs(tmp_path))


@pytest.mark.asyncio
async def test_a_failing_commit_helper_is_reported_and_restores_every_path_it_wrote(tmp_path):
    result, seen = await _run_enforce_with_site(
        tmp_path, "write_skill_extraction", extra_patches=(patch("kriya.skills.skill.git_commit_paths", return_value=False),))
    ws = seen["ws"]

    assert [e["operation"] for e in result.legacy_result["post_commit_persistence_errors"]] == ["skill_learning_flush"]
    assert _git(ws, "status", "--porcelain", "--", "skills") == "" and not PROBES["write_skill_extraction"](_fresh_engine(ws), ws)


@pytest.mark.asyncio
async def test_a_write_that_fails_part_way_through_the_flush_restores_what_it_had_already_written(tmp_path):
    import shutil
    real, calls = shutil.copy2, []

    def failing_second_copy(source, target, *args, **kwargs):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(source, target, *args, **kwargs)

    result, seen = await _run_enforce_with_site(
        tmp_path, "write_skill_extraction", extra_patches=(patch("kriya.skills.run_shadow.shutil.copy2", failing_second_copy),))
    ws = seen["ws"]

    assert len(calls) == 2
    errors = result.legacy_result["post_commit_persistence_errors"]
    assert [e["operation"] for e in errors] == ["skill_learning_flush"] and "disk full" in errors[0]["error"]
    assert _git(ws, "status", "--porcelain", "--", "skills") == "" and not PROBES["write_skill_extraction"](_fresh_engine(ws), ws)


@pytest.mark.asyncio
async def test_a_canonical_skill_changed_during_the_run_is_a_typed_conflict_and_nothing_is_overwritten(tmp_path):
    def a_human_edits_a_skill(ws):
        (ws / "skills" / "otherlib" / "rules.txt").write_text(RULE + "\nA human-added rule.\n")

    result, seen = await _run_enforce_with_site(tmp_path, "write_skill_extraction", during_second_subtask=a_human_edits_a_skill)
    ws = seen["ws"]
    [error] = result.legacy_result["post_commit_persistence_errors"]

    assert error["operation"] == "skill_learning_flush" and error["reason_code"] == "SKILLS_CANONICAL_CHANGED"
    assert error["paths"] == ["otherlib/rules.txt"]
    assert (ws / "skills" / "otherlib" / "rules.txt").read_text() == RULE + "\nA human-added rule.\n"      # the human's edit survives
    assert not PROBES["write_skill_extraction"](_fresh_engine(ws), ws)                                         # and nothing of the run's was applied
    assert _git(ws, "rev-parse", "HEAD") == seen["head_at_sandbox"] and not list(_shadow_dirs(tmp_path))


@pytest.mark.asyncio
async def test_an_error_in_the_terminal_phase_ends_the_run_with_the_shadow_dropped_and_nothing_persisted(tmp_path):
    seen = {}
    with pytest.raises(RuntimeError, match="terminal gates exploded"):
        await _run_enforce_with_site(tmp_path, "write_skill_extraction", explode=True, seen_out=seen)
    ws, engine = seen["ws"], seen["engine"]

    assert engine.run_shadow_active is False and engine.skills_dir == engine.canonical_skills_dir
    assert _skills_state(ws)["status"] == "" and _git(ws, "rev-parse", "HEAD") == seen["head_at_sandbox"]
    assert not PROBES["write_skill_extraction"](_fresh_engine(ws), ws) and not list(_shadow_dirs(tmp_path))
