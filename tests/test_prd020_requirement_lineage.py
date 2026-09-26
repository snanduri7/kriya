"""PRD-020: immutable requirement and obligation lineage.

The user's own goal statements become REQ-n records before any model sees
the goal. Planner/Architect prose may cite, paraphrase or omit them; only the
verifier (Goal Spec Compliance, after the deterministic gates) records an
outcome, and the terminal decision reads the requirement set, not the plan.
The end-to-end tests drive the real WorkflowEngine (and, for enforce, the
real WorkflowController terminal gates) with the model calls stubbed.
"""
import json
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.agents.agent import SpecComplianceAgent
from kriya.config import AppConfig
from kriya.config.authority import classify_field
from kriya.config.config import runtime_profile_preset_fields
from kriya.core.kernel import Kernel
from kriya.core.state_paths import trace_db_path
from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationStatus
from kriya.workflow.plan_schema import (
    ChangeKind,
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
)
from kriya.workflow.requirements import (
    REQUIREMENT_DERIVATION_VERSION,
    RequirementOutcome,
    blocking_requirements,
    cited_requirement_ids,
    derive_requirements,
    parse_requirement_verdicts,
    record_requirement_verdicts,
    requirement_lineage,
    requirement_obligation_id,
    requirement_outcomes,
    seed_requirement_obligations,
)
from _strict_doubles import strict_kernel

GOAL = (
    "Create greeting.py with a greet(name) function.\n"
    "- greet returns the text 'Hello, <name>'\n"
    "- Add a DEFAULT_NAME constant set to 'World'\n"
)


# ------------------------------------------------------------ derivation

def test_list_items_and_prose_become_ordered_requirements_in_the_users_words():
    reqs = derive_requirements(GOAL)
    assert [(r.id, r.text) for r in reqs.requirements] == [
        ("REQ-1", "Create greeting.py with a greet(name) function."),
        ("REQ-2", "greet returns the text 'Hello, <name>'"),
        ("REQ-3", "Add a DEFAULT_NAME constant set to 'World'"),
    ]
    assert {r.source for r in reqs.requirements} == {"goal"}
    assert reqs.version == REQUIREMENT_DERIVATION_VERSION


def test_derivation_is_deterministic_and_revision_bound():
    assert derive_requirements(GOAL) == derive_requirements(GOAL)
    assert derive_requirements(GOAL).digest == derive_requirements(GOAL).digest
    assert derive_requirements(GOAL + "- also log calls\n").digest != derive_requirements(GOAL).digest


def test_sentences_split_but_not_inside_code_spans_or_abbreviations():
    reqs = derive_requirements(
        "Rename `Order.total. Value` to amount, e.g. in the service. Do not change the REST API."
    )
    assert [r.text for r in reqs.requirements] == [
        "Rename `Order.total. Value` to amount, e.g. in the service.",
        "Do not change the REST API.",
    ]
    assert [r.kind for r in reqs.requirements] == ["requirement", "constraint"]


def test_a_single_statement_goal_is_one_requirement_and_numbered_items_split():
    assert derive_requirements("Fix compilation/test failure").ids == ["REQ-1"]
    reqs = derive_requirements("1. add a cache\n2) keep the public API unchanged\n   across modules")
    assert [r.text for r in reqs.requirements] == ["add a cache", "keep the public API unchanged across modules"]
    assert reqs.requirements[1].kind == "constraint"


def test_accepted_clarifications_extend_the_set_without_renumbering_the_goal():
    reqs = derive_requirements(GOAL, clarifications=["Names are ASCII only."])
    assert reqs.ids == ["REQ-1", "REQ-2", "REQ-3", "REQ-C1"]
    assert reqs.get("REQ-C1").source == "clarification"
    assert derive_requirements(GOAL).ids == ["REQ-1", "REQ-2", "REQ-3"]


# ------------------------------------------------------------ ledger/policy

def test_the_verifiers_verdict_becomes_authoritative_over_the_seed():
    reqs = derive_requirements(GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    assert set(requirement_outcomes(ledger, reqs).values()) == {RequirementOutcome.PENDING}
    record_requirement_verdicts(
        ledger, reqs, {rid: (RequirementOutcome.SATISFIED, "found") for rid in reqs.ids},
        revision=1, evidence_fingerprint="fp1", source="test",
    )
    assert set(requirement_outcomes(ledger, reqs).values()) == {RequirementOutcome.SATISFIED}
    assert blocking_requirements(ledger, reqs, unknown_policy="block", unverified_policy="block") == []
    record = ledger.current(requirement_obligation_id("REQ-2"))
    assert record.kind is ObligationKind.ORIGINAL_REQUIREMENT and record.terminal_required
    assert record.evidence["evidence_id"] == "fp1"


def test_a_later_violation_is_a_recorded_regression_and_always_blocks():
    reqs = derive_requirements(GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {rid: (RequirementOutcome.SATISFIED, "") for rid in reqs.ids},
                                revision=1, evidence_fingerprint="a", source="test")
    record_requirement_verdicts(ledger, reqs, {"REQ-3": (RequirementOutcome.VIOLATED, "absent")},
                                revision=2, evidence_fingerprint="b", source="test", only=["REQ-3"])
    assert [event.obligation_id for event in ledger.regressions] == [requirement_obligation_id("REQ-3")]
    blocking = blocking_requirements(ledger, reqs)  # default policies: record
    assert [(req.id, outcome) for req, outcome in blocking] == [("REQ-3", RequirementOutcome.VIOLATED)]


def test_unknown_and_unverified_block_only_under_their_policy():
    reqs = derive_requirements(GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(
        ledger, reqs,
        {"REQ-1": (RequirementOutcome.SATISFIED, ""), "REQ-2": (RequirementOutcome.UNVERIFIED, "behaviour")},
        revision=1, evidence_fingerprint="fp", source="test",
    )  # REQ-3 had no verdict: UNKNOWN
    assert blocking_requirements(ledger, reqs) == []
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, unknown_policy="block")] == ["REQ-3"]
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, unverified_policy="block")] == ["REQ-2"]


def test_the_generic_terminal_aggregation_leaves_original_requirements_to_their_policy():
    reqs = derive_requirements(GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    assert ledger.unresolved_terminal_obligations() == []


def test_seeding_is_idempotent_and_survives_a_checkpoint_round_trip():
    reqs = derive_requirements(GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "x")},
                                revision=1, evidence_fingerprint="fp", source="test", only=["REQ-1"])
    restored = ObligationLedger.from_snapshot(json.loads(json.dumps(ledger.to_snapshot())))
    seed_requirement_obligations(restored, derive_requirements(GOAL))  # a resumed run seeds again
    assert restored.fingerprint() == ledger.fingerprint()
    assert requirement_outcomes(restored, reqs)["REQ-1"] is RequirementOutcome.SATISFIED


def test_lineage_is_citation_only_and_an_omitted_requirement_stays_listed():
    reqs = derive_requirements(GOAL)
    plan = "Step 1: create greeting.py (REQ-1)\nStep 2: add a friendly greeting (REQ-9)"
    assert cited_requirement_ids(plan, reqs) == ["REQ-1"]  # REQ-9 is not the user's
    lineage = requirement_lineage(reqs, "plan", cited_requirement_ids(plan, reqs))
    assert lineage["omitted"] == ["REQ-2", "REQ-3"]


def test_verdict_parsing_ignores_invented_ids_and_unreadable_verdicts():
    reqs = derive_requirements(GOAL)
    verdicts, findings = parse_requirement_verdicts([
        {"id": "REQ-1", "verdict": "Satisfied", "evidence": "greeting.py greet"},
        {"id": "REQ-7", "verdict": "satisfied"},
        {"id": "REQ-2", "verdict": "probably"},
        "junk",
    ], reqs)
    assert verdicts == {"REQ-1": (RequirementOutcome.SATISFIED, "greeting.py greet")}
    assert len(findings) == 3


def test_policies_are_security_authority_and_production_seals_both():
    assert classify_field("autonomy", "requirement_unknown_policy").name == "SECURITY_AUTHORITY"
    assert classify_field("autonomy", "requirement_unverified_policy").name == "SECURITY_AUTHORITY"
    assert runtime_profile_preset_fields("production")[("autonomy", "requirement_unknown_policy")] == "block"
    assert runtime_profile_preset_fields("production")[("autonomy", "requirement_unverified_policy")] == "block"
    with pytest.raises(ValueError):
        AppConfig(autonomy={"requirement_unknown_policy": "ignore"})


# ------------------------------------------------------------ verifier contract

def test_a_missing_claim_counts_only_for_a_requirement_naming_something_concrete():
    reqs = derive_requirements("Add a DEFAULT_NAME constant.\n- Make the module more robust\n")
    verdicts, findings = parse_requirement_verdicts(
        [{"id": "REQ-1", "verdict": "missing"}, {"id": "REQ-2", "verdict": "missing"}], reqs)
    assert verdicts["REQ-1"][0] is RequirementOutcome.VIOLATED
    assert verdicts["REQ-2"][0] is RequirementOutcome.UNVERIFIED  # general prose: never a gate failure
    assert findings and "REQ-2" in findings[0]


@pytest.mark.asyncio
async def test_the_verifier_cannot_fail_the_gate_on_general_prose():
    reqs = derive_requirements("Add a DEFAULT_NAME constant.\n- Make the module more robust\n")
    agent = SpecComplianceAgent("spec_compliance", MagicMock())

    async def escalation(*args, **kwargs):
        return json.dumps({"compliant": True, "reasoning": "ok", "missing_requirements": [],
                           "requirement_verdicts": [{"id": "REQ-1", "verdict": "satisfied"},
                                                    {"id": "REQ-2", "verdict": "missing"}]})

    with patch("kriya.agents.agent.call_with_escalation", new=escalation):
        result = await agent.check("goal", ["a.py"], {"a.py": "DEFAULT_NAME = 1"}, requirements=reqs)
    assert result["compliant"] is True and result["missing_requirements"] == []


@pytest.mark.asyncio
async def test_the_verifier_names_a_missing_requirement_by_id_and_original_text():
    reqs = derive_requirements(GOAL)
    agent = SpecComplianceAgent("spec_compliance", MagicMock())
    prompts = []

    async def escalation(llm, system, prompt, *args, **kwargs):
        prompts.append(prompt)
        return json.dumps({"compliant": True, "reasoning": "ok", "missing_requirements": [],
                           "requirement_verdicts": [
                               {"id": "REQ-1", "verdict": "satisfied"},
                               {"id": "REQ-3", "verdict": "missing", "evidence": "no DEFAULT_NAME"}]})

    with patch("kriya.agents.agent.call_with_escalation", new=escalation):
        result = await agent.check(GOAL, ["greeting.py"], {"greeting.py": "def greet(n): ..."},
                                   requirements=reqs)
        plain = await agent.check(GOAL, ["greeting.py"], {"greeting.py": "def greet(n): ..."})

    assert "REQ-3: Add a DEFAULT_NAME constant set to 'World'" in prompts[0]
    assert result["compliant"] is False
    assert result["missing_requirements"] == ["REQ-3: Add a DEFAULT_NAME constant set to 'World'"]
    assert "Original Requirements" not in prompts[1] and plain["requirement_verdicts"] is None


# ------------------------------------------------------------ plan validation

@pytest.mark.asyncio
async def test_a_plan_may_not_invent_a_requirement_id_but_may_leave_one_unmapped(tmp_path):
    from kriya.workflow.plan_validation import validate_plan

    def plan(requirement_ids):
        return EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            requirement_ids=requirement_ids,
        )])

    known = derive_requirements(GOAL).ids
    invented = await validate_plan(plan(["REQ-1", "REQ-42"]), workspace_path=str(tmp_path),
                                   known_requirement_ids=known)
    assert "PLAN_REQUIREMENT_ID_UNKNOWN" in invented.reason_codes
    assert any("REQ-42" in error for error in invented.errors)
    partial = await validate_plan(plan(["REQ-1"]), workspace_path=str(tmp_path), known_requirement_ids=known)
    assert "PLAN_REQUIREMENT_ID_UNKNOWN" not in partial.reason_codes


# ------------------------------------------------------------ direct path, end to end

CONTENT = "DEFAULT_NAME = 'World'\n\ndef greet(name):\n    return f'Hello, {name}'\n"


def _verdict_json(prompt, missing=(), omit=()):
    ids = re.findall(r"^(REQ-C?\d+):", prompt, flags=re.MULTILINE)
    verdicts = [{"id": rid, "verdict": "missing" if rid in missing else "satisfied", "evidence": "greeting.py"}
                for rid in ids if rid not in omit]
    return json.dumps({"compliant": not missing, "reasoning": "checked",
                       "missing_requirements": [], "likely_files": [], "requirement_verdicts": verdicts})


def _engine(tmp_path, spec_responder, **autonomy):
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = True
    for key, value in autonomy.items():
        setattr(cfg.autonomy, key, value)
    cfg.llm_chain = []
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    calls = {"planner": [], "architect": [], "spec": []}

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system = system_prompt or ""
        if "Goal Spec Compliance Checker" in system:
            calls["spec"].append(user_prompt)
            return spec_responder(len(calls["spec"]), user_prompt)
        if "Kriya Planner Agent" in system:
            calls["planner"].append(user_prompt)
            # The plan cites REQ-1 and paraphrases the rest away.
            return "Step 1: create greeting.py with greet (REQ-1)\nStep 2: make the greeting friendly"
        if "Kriya Architect Agent" in system:
            calls["architect"].append(user_prompt)
            return "Design: greeting.py exposes greet (REQ-1)"
        return "Review: Approved"

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.developer.run_generation = AsyncMock(return_value=[{"filepath": "greeting.py", "content": CONTENT}])
    return cfg, engine, calls


def _gates_pass():
    return (
        patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
              return_value={"success": True, "output": ""}),
        patch("kriya.tools.validate.PolymorphicValidator.run_tests",
              return_value={"success": True, "output": ""}),
    )


def _events(cfg, kind):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_events FROM runs ORDER BY rowid").fetchall()
    return [e["details"] for (payload,) in rows for e in json.loads(payload or "[]") if e["kind"] == kind]


@pytest.mark.asyncio
async def test_a_paraphrasing_plan_cannot_drop_a_requirement_and_the_retry_names_it(tmp_path):
    """The plan cites only REQ-1. The Architect still sees all three; the
    verifier finds REQ-3 missing on the first candidate, the retry is told
    exactly REQ-3 and its original text, and the run passes only once every
    requirement has the verifier's evidence."""
    responses = lambda n, prompt: _verdict_json(prompt, missing=("REQ-3",) if n == 1 else ())
    cfg, engine, calls = _engine(tmp_path, responses)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace))

    assert res["quality_gates_passed"] is True
    assert res["requirements"]["outcomes"] == {"REQ-1": "satisfied", "REQ-2": "satisfied", "REQ-3": "satisfied"}
    assert "REQ-2: greet returns the text 'Hello, <name>'" in calls["planner"][0]
    assert "REQ-3: Add a DEFAULT_NAME constant set to 'World'" in calls["architect"][0]
    lineage = {e["stage"]: e for e in _events(cfg, "requirement.lineage")}
    assert lineage["plan"]["cited"] == ["REQ-1"] and lineage["plan"]["omitted"] == ["REQ-2", "REQ-3"]
    verdicts = _events(cfg, "requirement.verdicts")
    assert [v["outcomes"]["REQ-3"] for v in verdicts] == ["violated", "satisfied"]
    assert verdicts[0]["evidence_id"] != "" and verdicts[0]["requirement_set_digest"] == derive_requirements(GOAL).digest
    # The retry is driven by the unresolved id and the user's own text.
    retry_kwargs = engine.developer.run_generation.await_args_list[1].kwargs
    assert "REQ-3: Add a DEFAULT_NAME constant set to 'World'" in json.dumps(retry_kwargs, default=str)
    assert len(calls["spec"]) == 2 and all("REQ-1:" in p and "REQ-3:" in p for p in calls["spec"])


@pytest.mark.asyncio
async def test_a_requirement_without_a_verdict_blocks_before_apply_under_the_block_policy(tmp_path):
    cfg, engine, calls = _engine(
        tmp_path, lambda n, prompt: _verdict_json(prompt, omit=("REQ-2",)),
        requirement_unknown_policy="block",
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace))

    assert res["quality_gates_passed"] is False
    assert res["failure_category"] == "requirements_unresolved"
    assert "REQ-2 (unknown)" in res["environment_failure"]
    assert res["requirements"]["outcomes"]["REQ-2"] == "unknown"
    assert not (workspace / "greeting.py").exists()  # nothing applied
    assert len(calls["spec"]) == 1  # terminal: no Developer retry for the verifier's omission


@pytest.mark.asyncio
async def test_the_record_policy_reports_an_unknown_requirement_without_blocking(tmp_path):
    cfg, engine, _ = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt, omit=("REQ-2",)))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace))

    assert res["quality_gates_passed"] is True
    assert res["requirements"]["outcomes"]["REQ-2"] == "unknown"


@pytest.mark.asyncio
async def test_a_resumed_run_keeps_the_same_requirement_ids_not_the_saved_plans_wording(tmp_path):
    """Run 1 fails every attempt on REQ-3 and leaves checkpoints. The resumed
    run reuses the saved plan (which never mentioned REQ-2/REQ-3) yet tracks
    the same three requirements, derived from the goal again."""
    import subprocess

    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt, missing=("REQ-3",)))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=workspace, check=True)
    p1, p2 = _gates_pass()
    with p1, p2:
        first = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace))
    assert first["quality_gates_passed"] is False
    planner_calls = len(calls["planner"])

    cfg2, engine2, calls2 = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt))
    with p1, p2:
        second = await engine2.run_generation_workflow(goal=GOAL, workspace_path=str(workspace), resume=True)

    assert calls2["planner"] == [] and planner_calls == 1  # the saved plan was reused
    derived = _events(cfg2, "requirement.derived")
    assert [d["digest"] for d in derived] == [derive_requirements(GOAL).digest] * 2
    assert second["requirements"]["outcomes"] == {
        "REQ-1": "satisfied", "REQ-2": "satisfied", "REQ-3": "satisfied"}


# ------------------------------------------------------------ enforce path, terminal gate

@pytest.mark.asyncio
async def test_enforce_terminal_gate_holds_a_requirement_no_subtask_mapped(tmp_path, monkeypatch):
    """The structured plan maps its only subtask to REQ-1; REQ-2 is on no
    subtask. The terminal gate still judges REQ-2 against the candidate and,
    reported missing, blocks the commit."""
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    goal = "Create app.py with a run() entry point.\n- Log every call to run()\n"
    (tmp_path / "app.py").write_text("original\n")
    monkeypatch.setattr(wc, "create_git_worktree", lambda workspace: workspace)
    plan = EngineeringPlan(plan_id="prd020", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="add run()", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)], requirement_ids=["REQ-1"],
    )])
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    ))
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = SimpleNamespace(config=cfg)
    checked = []

    async def check(**kwargs):
        checked.append(kwargs)
        return json.loads(_verdict_json("\n".join(f"{r.id}: {r.text}" for r in kwargs["requirements"].requirements),
                                        missing=("REQ-2",)))

    we.spec_compliance.check = check

    async def fake_run(**kwargs):
        (tmp_path / "app.py").write_text("def run():\n    pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    we.run_generation_workflow = fake_run
    planner_requests = []

    async def planner(request, **kwargs):
        planner_requests.append(request)
        return "fake plan text"

    we.planner.run = planner
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await wc.WorkflowController(we).execute(goal, str(tmp_path), migration_mode="enforce")

    assert "REQ-2: Log every call to run()" in planner_requests[0]
    assert result.legacy_result["quality_gates_passed"] is False
    assert "REQ-2 (violated): Log every call to run()" in result.legacy_result["global_requirement_gap"]
    assert checked and checked[0]["files_written"] == ["app.py"]


@pytest.mark.asyncio
async def test_kriyas_own_placeholder_goal_derives_no_requirements(tmp_path):
    """`kriya fix` runs with Kriya's own wording around an error log; the
    run carries no original requirement set (nothing to verify or block)."""
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt), requirement_unknown_policy="block")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal="Fix compilation/test failure", workspace_path=str(workspace),
                                                   requirements_from_goal=False)

    assert "requirements" not in res and _events(cfg, "requirement.derived") == []
    assert all("Original Requirements" not in p for p in calls["planner"] + calls["spec"])


def test_the_fix_command_marks_its_goal_as_kriyas_own():
    from click.testing import CliRunner

    from kriya.cli import main

    captured = {}

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        async def run_generation_workflow(self, **kwargs):
            captured.update(kwargs)
            return {"quality_gates_passed": True, "files": [], "review": "ok", "plan": "", "design": ""}

    kernel = strict_kernel()
    kernel.start = AsyncMock()
    kernel.stop = AsyncMock()
    runner = CliRunner()
    with runner.isolated_filesystem():
        with patch("kriya.cli.WorkflowEngine", Engine), patch("kriya.cli.Kernel", return_value=kernel), \
             patch("kriya.cli.LLMClient"):
            runner.invoke(main, ["fix", "--error", "some compile error", "-y"])
    assert captured.get("requirements_from_goal") is False


# ------------------------------------------------------------ production semantics and closure evidence

from kriya.workflow.requirements import (  # noqa: E402
    close_unverified_requirements_with_named_tests,
    named_existing_tests,
    record_requirement_closure,
)

PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}
CLOSE_GOAL = "Add a DEFAULT_NAME constant.\n- Behaviour stays compatible with the legacy check test_legacy\n"


def _ledger_with(outcomes, goal=GOAL, evidence_id="cand-1"):
    reqs = derive_requirements(goal)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {rid: (o, "") for rid, o in outcomes.items()},
                                revision=1, evidence_fingerprint=evidence_id, source="test")
    return reqs, ledger


def test_production_blocks_no_verdict_cannot_confirm_and_violated():
    reqs, ledger = _ledger_with({"REQ-1": RequirementOutcome.SATISFIED, "REQ-2": RequirementOutcome.UNVERIFIED,
                                 "REQ-3": RequirementOutcome.VIOLATED}, )
    # REQ-4 does not exist in GOAL; every id without a verdict is NO_VERDICT.
    blocking = {req.id: outcome for req, outcome in blocking_requirements(ledger, reqs, **PRODUCTION)}
    assert blocking == {"REQ-2": RequirementOutcome.UNVERIFIED, "REQ-3": RequirementOutcome.VIOLATED}
    reqs, ledger = _ledger_with({"REQ-1": RequirementOutcome.SATISFIED})
    blocking = {req.id: outcome for req, outcome in blocking_requirements(ledger, reqs, **PRODUCTION)}
    assert blocking == {"REQ-2": RequirementOutcome.UNKNOWN, "REQ-3": RequirementOutcome.UNKNOWN}


def test_cannot_confirm_from_code_is_never_satisfied_without_other_evidence():
    reqs, ledger = _ledger_with({rid: RequirementOutcome.UNVERIFIED for rid in ("REQ-1", "REQ-2", "REQ-3")})
    assert set(requirement_outcomes(ledger, reqs).values()) == {RequirementOutcome.UNVERIFIED}
    assert len(blocking_requirements(ledger, reqs, **PRODUCTION)) == 3


def test_closure_evidence_for_the_same_candidate_closes_cannot_confirm():
    reqs, ledger = _ledger_with({"REQ-1": RequirementOutcome.SATISFIED, "REQ-2": RequirementOutcome.UNVERIFIED,
                                 "REQ-3": RequirementOutcome.SATISFIED})
    record_requirement_closure(ledger, reqs, "REQ-2", evidence_id="cand-1", method="named_test_run",
                               detail={"tests": ["tests/test_x.py"]}, source="test", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert blocking_requirements(ledger, reqs, **PRODUCTION) == []
    # The verdict record itself is untouched: still the verifier's UNVERIFIED.
    assert ledger.current(requirement_obligation_id("REQ-2")).evidence["outcome"] == "unverified"


def test_closure_evidence_never_carries_over_to_another_candidate():
    reqs, ledger = _ledger_with({"REQ-2": RequirementOutcome.UNVERIFIED})
    record_requirement_closure(ledger, reqs, "REQ-2", evidence_id="cand-1", method="named_test_run",
                               detail={}, source="test", revision=1)
    record_requirement_verdicts(ledger, reqs, {"REQ-2": (RequirementOutcome.UNVERIFIED, "")}, revision=2,
                                evidence_fingerprint="cand-2", source="test", only=["REQ-2"])
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_violated_and_no_verdict_always_block_even_with_closure_evidence():
    reqs, ledger = _ledger_with({"REQ-2": RequirementOutcome.UNVERIFIED})
    record_requirement_closure(ledger, reqs, "REQ-2", evidence_id="cand-1", method="named_test_run",
                               detail={}, source="test", revision=1)
    record_requirement_closure(ledger, reqs, "REQ-3", evidence_id="cand-1", method="named_test_run",
                               detail={}, source="test", revision=1)  # REQ-3 has no verdict
    record_requirement_verdicts(ledger, reqs, {"REQ-2": (RequirementOutcome.VIOLATED, "")}, revision=1,
                                evidence_fingerprint="cand-1", source="test", only=["REQ-2"])
    blocking = {req.id: outcome for req, outcome in blocking_requirements(
        ledger, reqs, unknown_policy="block", unverified_policy="record")}
    assert blocking["REQ-2"] is RequirementOutcome.VIOLATED
    assert blocking["REQ-3"] is RequirementOutcome.UNKNOWN
    # VIOLATED blocks under every policy.
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, unknown_policy="record",
                                                   unverified_policy="record")] == ["REQ-2"]


def test_named_tests_come_from_the_requirement_text_only():
    files = ["tests/test_legacy.py", "tests/test_other.py", "src/test/java/PricingTest.java"]
    assert named_existing_tests("stays compatible with tests/test_legacy.py", files) == ["tests/test_legacy.py"]
    assert named_existing_tests("PricingTest keeps passing", files) == ["src/test/java/PricingTest.java"]
    assert named_existing_tests("keep the legacy behaviour", files) == []


@pytest.mark.parametrize("modified, result, closed, reason", [
    ((), {"success": True, "output": "1 passed"}, True, None),
    (("tests/test_legacy.py",), {"success": True, "output": "1 passed"}, False, "written or changed"),
    ((), {"success": True, "output": "no tests ran"}, False, "did not execute"),
    ((), {"success": False, "output": "1 failed"}, False, "failed"),
])
def test_a_named_test_closes_only_when_unmodified_executed_and_passing(modified, result, closed, reason):
    reqs, ledger = _ledger_with({"REQ-1": RequirementOutcome.SATISFIED, "REQ-2": RequirementOutcome.UNVERIFIED},
                                goal=CLOSE_GOAL)
    runs = []

    def run_tests(paths):
        runs.append(list(paths))
        return result

    from kriya.workflow.acceptance import output_confirms_nonzero_test_execution

    [attempt] = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=["tests/test_legacy.py", "tests/test_other.py"], modified=modified,
        run_tests=run_tests, confirms_execution=output_confirms_nonzero_test_execution,
        source="test", revision=1)
    assert attempt["closed"] is closed and (reason is None or reason in attempt["reason"])
    assert runs == ([] if modified else [["tests/test_legacy.py"]])  # exactly the named test, never more
    expected = RequirementOutcome.CLOSED_BY_EVIDENCE if closed else RequirementOutcome.UNVERIFIED
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is expected


def _verdicts_json(prompt, unverifiable=()):
    ids = re.findall(r"^(REQ-C?\d+):", prompt, flags=re.MULTILINE)
    return json.dumps({"compliant": True, "reasoning": "checked", "missing_requirements": [],
                       "likely_files": [], "requirement_verdicts": [
                           {"id": rid, "verdict": "unverifiable" if rid in unverifiable else "satisfied",
                            "evidence": "greeting.py"} for rid in ids]})


def _legacy_workspace(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "test_legacy.py").write_text("def test_legacy():\n    assert True\n")
    return workspace


@pytest.mark.parametrize("named_result, passes", [
    ({"success": True, "output": "1 passed"}, True),
    ({"success": False, "output": "1 failed"}, False),
])
@pytest.mark.asyncio
async def test_production_run_closes_cannot_confirm_only_by_running_the_named_test(tmp_path, named_result, passes):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt, unverifiable=("REQ-2",)),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = _legacy_workspace(tmp_path)
    runs = []

    def run_tests(self, target_test=None, *args, **kwargs):
        runs.append(target_test)
        return named_result if target_test == ["tests/test_legacy.py"] else {"success": True, "output": "3 passed"}

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests):
        res = await engine.run_generation_workflow(goal=CLOSE_GOAL, workspace_path=str(workspace))

    assert ["tests/test_legacy.py"] in runs
    [closure] = _events(cfg, "requirement.closure")[:1]
    assert closure["closures"][0]["requirement"] == "REQ-2" and closure["closures"][0]["closed"] is passes
    assert res["quality_gates_passed"] is passes
    if passes:
        assert res["requirements"]["outcomes"]["REQ-2"] == "closed_by_evidence"
    else:
        assert res.get("failure_category") and "REQUIREMENTS_UNRESOLVED" in json.dumps(res, default=str)


@pytest.mark.asyncio
async def test_production_run_blocks_cannot_confirm_with_no_other_evidence(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt, unverifiable=("REQ-2",)),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace))

    assert res["quality_gates_passed"] is False
    assert "REQ-2 (unverified)" in json.dumps(res, default=str)
    assert _events(cfg, "requirement.closure") == []
    assert engine.developer.run_generation.await_count == 1  # an unfixable block is not retried


def test_the_migration_gate_closes_only_the_requirement_stating_the_migration():
    """An UNVERIFIED requirement naming both the migration's source and
    target is positively verified by the deterministic migration gate (all
    obligations SATISFIED); one naming only the target is a different claim
    and stays UNVERIFIED."""
    from kriya.workflow.attempt import _close_requirements_by_migration_gate
    from kriya.workflow.obligations import ObligationAuthority, ObligationRecord

    goal = "Migrate the JSON layer from gson to jackson-databind.\n- Use jackson snake_case naming everywhere\n"
    reqs, ledger = _ledger_with({"REQ-1": RequirementOutcome.UNVERIFIED, "REQ-2": RequirementOutcome.UNVERIFIED},
                                goal=goal)
    ledger.record(ObligationRecord(
        id="migration.gson", kind=ObligationKind.MIGRATION_COMPLETION, status=ObligationStatus.SATISFIED,
        authority=ObligationAuthority.DETERMINISTIC, description="gson -> jackson", source="test", revision=1,
        evidence={"source_identity": "gson", "target_identity": "jackson-databind"}))
    ctx = SimpleNamespace(requirement_set=reqs, obligation_ledger=ledger)
    _close_requirements_by_migration_gate(SimpleNamespace(attempt_number=1), ctx, "cand-1")
    outcomes = requirement_outcomes(ledger, reqs)
    assert outcomes["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert outcomes["REQ-2"] is RequirementOutcome.UNVERIFIED
    # Another candidate's verdict is not closed by it.
    _close_requirements_by_migration_gate(SimpleNamespace(attempt_number=2), ctx, "cand-2")
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("named_result, passes", [
    ({"success": True, "output": "1 passed"}, True),
    ({"success": False, "output": "1 failed"}, False),
])
@pytest.mark.asyncio
async def test_enforce_terminal_gate_closes_cannot_confirm_only_by_the_named_test(
        tmp_path, monkeypatch, named_result, passes):
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    goal = "Create app.py with a run() entry point.\n- Behaviour stays compatible with the legacy check test_legacy\n"
    (tmp_path / "app.py").write_text("original\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_legacy.py").write_text("def test_legacy():\n    assert True\n")
    monkeypatch.setattr(wc, "create_git_worktree", lambda workspace: workspace)
    plan = EngineeringPlan(plan_id="prd020", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="add run()", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)], requirement_ids=["REQ-1"],
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
        return json.loads(_verdicts_json(prompt, unverifiable=("REQ-2",)))

    we.spec_compliance.check = check

    async def fake_run(**kwargs):
        (tmp_path / "app.py").write_text("def run():\n    pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    we.run_generation_workflow = fake_run
    we.planner.run = AsyncMock(return_value="fake plan text")
    runs = []

    def run_tests(self, target_test=None, *args, **kwargs):
        runs.append(target_test)
        return named_result if target_test == ["tests/test_legacy.py"] else {"success": True, "output": "2 passed"}

    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests):
        result = await wc.WorkflowController(we).execute(goal, str(tmp_path), migration_mode="enforce")

    assert ["tests/test_legacy.py"] in runs
    gap = result.legacy_result.get("global_requirement_gap")
    outcomes = result.legacy_result["requirements"]["outcomes"]
    assert outcomes["REQ-2"] == ("closed_by_evidence" if passes else "unverified")
    if passes:
        assert not gap, gap
    else:
        assert "REQ-2 (unverified)" in gap and result.legacy_result["quality_gates_passed"] is False


@pytest.mark.asyncio
async def test_enforce_terminal_migration_gate_closes_the_migration_requirement(tmp_path, monkeypatch):
    """Production runs are enforce runs, whose subtasks carry no requirement
    set: the migration-gate closure must happen at the terminal gate, on the
    same final candidate the terminal migration check just judged."""
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus
    from kriya.workflow.obligations import ObligationAuthority, ObligationRecord
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    goal = "Migrate the JSON layer from gson to jackson-databind.\n- Keep the run() entry point in app.py\n"
    (tmp_path / "app.py").write_text("original\n")
    monkeypatch.setattr(wc, "create_git_worktree", lambda workspace: workspace)
    monkeypatch.setattr(wc, "resolve_migration_resolution", lambda goal, workspace: MigrationResolution(
        MigrationResolutionStatus.RESOLVED, obligation=MagicMock()))

    def terminal_migration_check(obligation, root, *, obligation_ledger, revision, source, **kwargs):
        obligation_ledger.record(ObligationRecord(
            id="migration.gson", kind=ObligationKind.MIGRATION_COMPLETION, status=ObligationStatus.SATISFIED,
            authority=ObligationAuthority.DETERMINISTIC, description="gson -> jackson-databind", source=source,
            revision=revision, evidence={"source_identity": "gson", "target_identity": "jackson-databind"}))
        return None

    monkeypatch.setattr(wc, "find_migration_incomplete", terminal_migration_check)
    plan = EngineeringPlan(plan_id="prd020", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="migrate", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)], requirement_ids=["REQ-1"],
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
        return json.loads(_verdicts_json(prompt, unverifiable=("REQ-1",)))

    we.spec_compliance.check = check

    async def fake_run(**kwargs):
        (tmp_path / "app.py").write_text("def run():\n    pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    we.run_generation_workflow = fake_run
    we.planner.run = AsyncMock(return_value="fake plan text")
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await wc.WorkflowController(we).execute(goal, str(tmp_path), migration_mode="enforce")

    outcomes = result.legacy_result["requirements"]["outcomes"]
    assert outcomes == {"REQ-1": "closed_by_evidence", "REQ-2": "satisfied"}
    assert not result.legacy_result.get("global_requirement_gap")
