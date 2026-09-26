"""PRD-020: deterministic mutation-scope evidence for file-boundary requirements.

"Do not modify any other file in the repository" names nothing the verifier
can find in source, so it would stay CANNOT_CONFIRM_FROM_CODE forever. Kriya
can prove it from its own mutation record instead: the authorized paths are
the files the user's goal itself names (tracked at the run's base - never
the Planner's choice), the actual paths are what the candidate and the run's
committed history changed (git + commit evidence), and
``actual ⊆ authorized`` with nothing foreign closes it for the candidate the
verdict judged. A path outside the set is deterministic VIOLATED evidence.
"""
import json
import re
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from kriya.config import AppConfig
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.plan_schema import ChangeKind, EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.requirements import (
    MUTATION_SCOPE,
    RequirementOutcome,
    blocking_requirements,
    close_mutation_scope_requirements,
    derive_requirements,
    is_mutation_scope_requirement,
    mutation_path_roles,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
)

SCOPED_GOAL = "Fix the greeting in `greeting.py` so it says Hello.\n\nDo not modify any other file in the repository.\n"
UNSCOPED_GOAL = "Fix the greeting so it says Hello.\n\nDo not modify any other file in the repository.\n"
PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}
TRACKED = ["greeting.py", "other.py", "README.md"]


def _ledger(goal, outcome=RequirementOutcome.UNVERIFIED, evidence_id="cand-1"):
    reqs = derive_requirements(goal)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    verdicts = {r.id: (RequirementOutcome.SATISFIED, "") for r in reqs.requirements}
    verdicts[reqs.requirements[-1].id] = (outcome, "")
    record_requirement_verdicts(ledger, reqs, verdicts, revision=1, evidence_fingerprint=evidence_id, source="test")
    return reqs, ledger


def _scope(actual, foreign=()):
    return {"actual_paths": list(actual), "foreign_paths": list(foreign), "run_id": "run-1",
            "base_revision": "base", "candidate_revision": "base"}


# ------------------------------------------------------------ recognition and referent

@pytest.mark.parametrize("text, scoped", [
    ("Do not modify any other file in the repository.", True),
    ("Don't change any other files.", True),
    ("Never touch any other file in this project", True),
    # Not a file boundary: other methods, a size wish, a partial sentence.
    ("Do not change the method's signature, its annotations, or any other method in this class.", False),
    ("Keep the change small.", False),
    ("Do not modify any other file unless the tests need it.", False),
])
def test_only_a_whole_file_boundary_statement_is_a_mutation_scope_requirement(text, scoped):
    assert is_mutation_scope_requirement(text) is scoped


ROLE_TRACKED = ["src/A.java", "src/B.java", "tests/test_a.py", "lib/service.py"]
SCOPE = " Do not modify any other file in the repository."


def _roles(goal):
    return mutation_path_roles(derive_requirements(goal), ROLE_TRACKED)


def test_an_explicit_mutation_target_is_allowed():
    assert _roles("Modify src/A.java." + SCOPE)["authorized"] == ["src/A.java"]


def test_two_explicit_mutation_targets_are_allowed():
    assert _roles("Modify src/A.java and src/B.java." + SCOPE)["authorized"] == ["src/A.java", "src/B.java"]


def test_a_reference_path_is_not_allowed():
    roles = _roles("Modify src/A.java. Compare it with src/B.java." + SCOPE)
    assert roles["authorized"] == ["src/A.java"] and roles["references"] == ["src/B.java"]
    assert roles["ambiguous"] == []


@pytest.mark.parametrize("goal, ambiguous", [
    # No cue in the path's own clause.
    ("Fix src/A.java; the failure shows in tests/test_a.py." + SCOPE, ["tests/test_a.py"]),
    # A relational word never decides a role.
    ("Replace src/A.java logic with src/B.java." + SCOPE, ["src/B.java"]),
    ("Create src/New.java next to src/A.java." + SCOPE, ["src/A.java"]),
    # Different roles at different mentions.
    ("Compare src/A.java with src/B.java and then modify src/A.java." + SCOPE, ["src/A.java"]),
])
def test_an_undeterminable_path_role_is_ambiguous_not_allowed(goal, ambiguous):
    roles = _roles(goal)
    assert roles["ambiguous"] == ambiguous
    assert not set(ambiguous) & set(roles["authorized"])


def test_exact_paths_only_and_negated_or_created_paths_keep_their_role():
    roles = _roles("Modify src/A.java but do not modify src/B.java; see service.py." + SCOPE)
    assert roles == {"authorized": ["src/A.java"], "references": [], "forbidden": ["src/B.java"],
                     "ambiguous": []}  # service.py is not a tracked path (lib/service.py is)
    assert _roles("Create src/New.java." + SCOPE)["authorized"] == ["src/New.java"]
    assert _roles("Compare src/New.java." + SCOPE)["authorized"] == []  # untracked, not created


def test_modifying_a_referenced_but_unauthorized_path_is_violated():
    goal = "Modify src/A.java. Compare it with src/B.java." + SCOPE
    reqs, ledger = _ledger(goal)
    [attempt] = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=ROLE_TRACKED, scope_evidence=_scope(["src/A.java", "src/B.java"]),
        source="test", revision=1)
    assert attempt["out_of_scope_paths"] == ["src/B.java"] and attempt["reference_paths"] == ["src/B.java"]
    assert requirement_outcomes(ledger, reqs)[reqs.requirements[-1].id] is RequirementOutcome.VIOLATED


def test_an_ambiguous_path_role_leaves_the_requirement_unresolved_even_when_in_scope():
    goal = "Fix src/A.java; the failure shows in tests/test_a.py." + SCOPE
    reqs, ledger = _ledger(goal)
    [attempt] = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=ROLE_TRACKED, scope_evidence=_scope(["src/A.java"]),
        source="test", revision=1)
    rid = reqs.requirements[-1].id
    assert attempt["closed"] is False and "cannot be determined" in attempt["reason"]
    assert attempt["ambiguous_paths"] == ["tests/test_a.py"]
    assert requirement_outcomes(ledger, reqs)[rid] is RequirementOutcome.UNVERIFIED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == [rid]


def test_the_demo03_goal_allows_only_the_file_it_asks_to_fix():
    goal = (
        "Fix a bug in the existing Spring Boot driver service: `DefaultDriverService.delete(Long driverId)` (in "
        "`src/main/java/com/myapp/service/driver/DefaultDriverService.java`) marks a driver as deleted in memory "
        "via `driverDO.setDeleted(true)`, but never persists that change - it does not call "
        "`driverRepository.save(driverDO)`.\n\nFix only this method so that the deleted flag is actually "
        "persisted, by saving the driver through `driverRepository` after setting `deleted` to true.\n\n"
        "Do not change the method's signature, its `@Transactional`/`@Override` annotations, or any other method "
        "in this class. Do not modify any other file in the repository.\n"
    )
    path = "src/main/java/com/myapp/service/driver/DefaultDriverService.java"
    assert mutation_path_roles(derive_requirements(goal), [path, "pom.xml"]) == {
        "authorized": [path], "references": [], "forbidden": [], "ambiguous": []}


# ------------------------------------------------------------ the five required cases (pure)

@pytest.mark.parametrize("verdict", [RequirementOutcome.UNVERIFIED, RequirementOutcome.SATISFIED])
def test_exact_one_file_scope_with_only_that_file_changed_closes(verdict):
    reqs, ledger = _ledger(SCOPED_GOAL, verdict)
    [attempt] = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence=_scope(["greeting.py"]), source="test", revision=1)
    assert attempt["closed"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert blocking_requirements(ledger, reqs, **PRODUCTION) == []
    evidence = requirement_evidence(ledger, reqs)["REQ-2"]
    assert evidence["kind"] == MUTATION_SCOPE and evidence["requirement"] == "REQ-2"
    assert evidence["authorized_paths"] == ["greeting.py"] and evidence["actual_paths"] == ["greeting.py"]
    assert evidence["evidence_id"] == "cand-1" and evidence["run_id"] == "run-1"


@pytest.mark.parametrize("verdict", [RequirementOutcome.UNVERIFIED, RequirementOutcome.SATISFIED])
def test_a_second_changed_file_is_violated_and_blocks_whatever_the_verifier_said(verdict):
    reqs, ledger = _ledger(SCOPED_GOAL, verdict)
    [attempt] = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence=_scope(["greeting.py", "other.py"]),
        source="test", revision=1)
    assert attempt["closed"] is False and attempt["out_of_scope_paths"] == ["other.py"]
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.VIOLATED
    # VIOLATED blocks under every policy.
    assert [r.id for r, _ in blocking_requirements(ledger, reqs)] == ["REQ-2"]


def test_a_planner_selected_file_without_an_authoritative_scope_cannot_close():
    """The goal names no file: whatever the Planner chose, "other" has no
    referent, so nothing closes (and nothing is invented as a violation)."""
    reqs, ledger = _ledger(UNSCOPED_GOAL)
    [attempt] = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence=_scope(["greeting.py"]), source="test", revision=1)
    assert attempt["closed"] is False and "no authoritative referent" in attempt["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-2"]


def test_stale_evidence_from_an_earlier_candidate_never_closes_or_violates_a_later_one():
    reqs, ledger = _ledger(SCOPED_GOAL, evidence_id="cand-1")
    close_mutation_scope_requirements(ledger, reqs, tracked_paths=TRACKED,
                                      scope_evidence=_scope(["greeting.py"]), source="test", revision=1)
    record_requirement_verdicts(ledger, reqs, {"REQ-2": (RequirementOutcome.UNVERIFIED, "")}, revision=2,
                                evidence_fingerprint="cand-2", source="test", only=["REQ-2"])
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    # Counter-evidence is candidate-bound the same way.
    close_mutation_scope_requirements(ledger, reqs, tracked_paths=TRACKED,
                                      scope_evidence=_scope(["other.py"]), source="test", revision=2)
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.VIOLATED
    record_requirement_verdicts(ledger, reqs, {"REQ-2": (RequirementOutcome.UNVERIFIED, "")}, revision=3,
                                evidence_fingerprint="cand-3", source="test", only=["REQ-2"])
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("scope, reason", [
    (_scope(["greeting.py"], foreign=["notes.txt"]), "did not make"),
    ({"unavailable": "candidate is at x, not the run base y"}, "unavailable"),
    (None, "unavailable"),
])
def test_foreign_changes_or_missing_evidence_leave_it_unresolved(scope, reason):
    reqs, ledger = _ledger(SCOPED_GOAL)
    [attempt] = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence=scope, source="test", revision=1)
    assert attempt["closed"] is False and reason in attempt["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_no_verdict_is_never_closed_by_scope_evidence():
    reqs = derive_requirements(SCOPED_GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "")}, revision=1,
                                evidence_fingerprint="cand-1", source="test")  # REQ-2: no verdict
    [attempt] = close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence=_scope(["greeting.py"]), source="test", revision=1)
    assert attempt["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNKNOWN


# ------------------------------------------------------------ real git, end to end

def _git_repo(path, files):
    path.mkdir(parents=True)
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=path, check=True)
    for name, content in files.items():
        (path / name).write_text(content)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


def _verdicts(prompt, verdict="unverifiable"):
    ids = re.findall(r"^(REQ-C?\d+):", prompt, flags=re.MULTILINE)
    return json.dumps({"compliant": True, "reasoning": "checked", "missing_requirements": [], "likely_files": [],
                       "requirement_verdicts": [{"id": rid, "verdict": verdict if rid == ids[-1] else "satisfied",
                                                 "evidence": "greeting.py"} for rid in ids]})


def _direct_engine(tmp_path, files):
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    cfg.llm_chain = []
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        if "Goal Spec Compliance Checker" in (system_prompt or ""):
            return _verdicts(user_prompt)
        if "Kriya Planner Agent" in (system_prompt or ""):
            return "Step 1: update greeting.py"
        return "Review: Approved"

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.developer.run_generation = AsyncMock(return_value=[
        {"filepath": path, "content": content} for path, content in files.items()])
    return engine


@pytest.mark.parametrize("written, closes", [
    ({"greeting.py": "GREETING = 'Hello'\n"}, True),
    ({"greeting.py": "GREETING = 'Hello'\n", "other.py": "OTHER = 2\n"}, False),
])
@pytest.mark.asyncio
async def test_direct_run_decides_the_scope_requirement_from_what_the_candidate_changed(tmp_path, written, closes):
    workspace = _git_repo(tmp_path / "ws", {"greeting.py": "GREETING = 'Hi'\n", "other.py": "OTHER = 1\n",
                                            ".gitignore": "__pycache__/\n"})
    engine = _direct_engine(tmp_path, written)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               return_value={"success": True, "output": "1 passed"}):
        res = await engine.run_generation_workflow(goal=SCOPED_GOAL, workspace_path=str(workspace))

    outcome = res["requirements"]["outcomes"]["REQ-2"]
    evidence = res["requirements"]["evidence"]["REQ-2"]
    assert evidence["kind"] == MUTATION_SCOPE and evidence["authorized_paths"] == ["greeting.py"]
    if closes:
        assert res["quality_gates_passed"] is True, res.get("environment_failure")
        assert outcome == "closed_by_evidence" and evidence["actual_paths"] == ["greeting.py"]
        assert (workspace / "greeting.py").read_text() == "GREETING = 'Hello'\n"
    else:
        assert res["quality_gates_passed"] is False and outcome == "violated"
        assert evidence["out_of_scope_paths"] == ["other.py"]
        assert "REQUIREMENTS_UNRESOLVED" in json.dumps(res, default=str)
        assert (workspace / "other.py").read_text() == "OTHER = 1\n"  # nothing applied


async def _enforce(tmp_path, monkeypatch, goal, planned, verdict="unverifiable"):
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    workspace = _git_repo(tmp_path / "ws", {"app.py": "original\n", "other.py": "other\n"})
    monkeypatch.setattr(wc, "create_git_worktree", lambda path: path)
    plan = EngineeringPlan(plan_id="scope", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="change", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=path, action=FileAction.MODIFY) for path in planned],
        requirement_ids=["REQ-1"],
    )])
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    ))
    we.kernel = SimpleNamespace(config=cfg)

    async def check(**kwargs):
        prompt = "\n".join(f"{r.id}: {r.text}" for r in kwargs["requirements"].requirements)
        return json.loads(_verdicts(prompt, verdict))

    we.spec_compliance.check = check

    async def fake_run(**kwargs):
        for path in planned:
            (workspace / path).write_text("def run():\n    pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": list(planned)}

    we.run_generation_workflow = fake_run
    we.planner.run = AsyncMock(return_value="fake plan text")
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        return await wc.WorkflowController(we).execute(goal, str(workspace), migration_mode="enforce")


@pytest.mark.parametrize("goal, planned, outcome", [
    ("Add a run() entry point to `app.py`.\n\nDo not modify any other file in the repository.\n",
     ["app.py"], "closed_by_evidence"),
    # The Planner also selected other.py: planning never authorizes a path.
    ("Add a run() entry point to `app.py`.\n\nDo not modify any other file in the repository.\n",
     ["app.py", "other.py"], "violated"),
    # The goal names no file: the Planner's app.py is no referent for "other".
    ("Add a run() entry point.\n\nDo not modify any other file in the repository.\n",
     ["app.py"], "unverified"),
])
@pytest.mark.asyncio
async def test_enforce_terminal_gate_decides_the_scope_requirement(tmp_path, monkeypatch, goal, planned, outcome):
    result = await _enforce(tmp_path, monkeypatch, goal, planned)
    outcomes = result.legacy_result["requirements"]["outcomes"]
    gap = result.legacy_result.get("global_requirement_gap")
    assert outcomes["REQ-2"] == outcome
    if outcome == "closed_by_evidence":
        assert not gap, gap
        evidence = result.legacy_result["requirements"]["evidence"]["REQ-2"]
        assert evidence["kind"] == MUTATION_SCOPE and evidence["actual_paths"] == ["app.py"]
        assert evidence["authorized_paths"] == ["app.py"] and evidence["base_revision"]
    else:
        assert f"REQ-2 ({outcome})" in gap and result.legacy_result["quality_gates_passed"] is False
    [attempt] = [a for a in result.legacy_result["requirements"]["closure_attempts"]
                 if a.get("kind") == MUTATION_SCOPE]
    assert attempt["closed"] is (outcome == "closed_by_evidence")
    if outcome == "unverified":
        assert "no authoritative referent" in attempt["reason"]


# ------------------------------------------------------------ milestone plans: committed history

MILESTONE_GOAL = "Update `m1.py` so VALUE is final.\n\nDo not modify any other file in the repository.\n"


class _MilestoneTransport:
    """LLMClient._request_once stand-in: each unit writes the file its goal
    names (``targets``: goal fragment -> file); the integration unit writes
    m1.py. The verifier satisfies every requirement it is shown."""

    def __init__(self, targets):
        self.targets = targets

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        prompt = user_prompt or ""
        target = next((path for key, path in self.targets.items() if key in prompt), "m1.py")
        if "Goal Spec Compliance Checker" in first:
            ids = re.findall(r"^(REQ-\d+):", prompt, flags=re.MULTILINE)
            content = json.dumps({"compliant": True, "reasoning": "ok", "missing_requirements": [],
                                  "likely_files": [],
                                  "requirement_verdicts": [{"id": i, "verdict": "satisfied"} for i in ids]})
        elif "File List Planner" in first:
            content = json.dumps({"files": [target]})
        elif "Planner Agent" in first:
            content = f"Step 1: update {target}"
        elif model == "dev-model":
            content = f"VALUE = 'final {target}'\n"
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}


@pytest.mark.parametrize("m2_target, outcome", [("m1.py", "closed_by_evidence"), ("m2.py", "violated")])
def test_milestone_final_check_uses_the_runs_exact_committed_path_history(tmp_path, monkeypatch, m2_target, outcome):
    """M1 changes m1.py; M2 changes ``m2_target``; the integration candidate
    itself only touches m1.py. The original requirement is judged on every
    path the run committed, so M2's earlier commit to m2.py is a violation
    even though the final candidate alone would look in scope."""
    from test_prd020_milestone_requirements import _probe

    from kriya.cli import main
    from kriya.core import model_runtime
    from kriya.core.llm import LLMClient

    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    workspace = _git_repo(tmp_path / "ws", {"m1.py": "VALUE = 1\n", "m2.py": "VALUE = 2\n"})
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"group_id": "scope", "original_goal": MILESTONE_GOAL, "milestones": [
        {"id": "M1", "goal": "build M1: update m1.py", "success_criterion": "m1.py final", "depends_on": []},
        {"id": "M2", "goal": f"build M2: update {m2_target}", "success_criterion": "done", "depends_on": ["M1"]},
    ]}))
    monkeypatch.chdir(workspace)
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = True
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    transport = _MilestoneTransport({"build M2": m2_target, f"update {m2_target}": m2_target})
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch.object(LLMClient, "_request_once", new=transport):
        result = CliRunner().invoke(main, ["generate", "--from-milestones", str(plan), "-y"])

    payload = json.loads(result.output[result.output.index("{", result.output.index("=== Milestone sequence")):])
    if outcome == "closed_by_evidence":
        assert result.exit_code == 0, result.output
        assert payload["status"] != "integration_failed"
    else:
        assert result.exit_code != 0
        assert payload["status"] == "integration_failed"
        assert "REQ-2 (violated)" in result.output
        assert payload["committed_work_units"] == ["M1", "M2"]
    # The decision came from the run's committed history, not a model.
    from test_prd020_milestone_requirements import _events

    closures = [c for event in _events(cfg, "requirement.closure") for c in event["closures"]]
    [scope] = [c for c in closures if c.get("kind") == MUTATION_SCOPE]
    assert scope["authorized_paths"] == ["m1.py"]
    assert m2_target in scope["committed_history"] and "m1.py" in scope["committed_history"]
    assert sorted(scope["actual_paths"]) == sorted({"m1.py", m2_target})
    assert scope["run_id"]
