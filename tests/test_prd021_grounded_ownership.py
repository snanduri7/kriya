"""PRD-021: plan-time grounded existing-responsibility ownership findings.

First the current deterministic owner resolution is pinned (it must keep its
precedence and behaviour); then the case it cannot see - a differently named
new owner of an existing responsibility - is surfaced as a GROUNDED finding
that informs planning and generation but never denies a plan, and that no
model justification alone can mark satisfied.
"""
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.workflow.file_resolution import prefer_existing_artifact_owners
from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationStatus
from kriya.workflow.ownership_findings import (
    ACKNOWLEDGED,
    SATISFIED,
    UNRESOLVED,
    find_ownership_findings,
    grounded_owner_candidates,
    parse_ownership_justifications,
    record_findings,
    settle_findings,
)

GOAL = "Reject orders whose quantity exceeds the stock level during checkout validation"


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "order_validator.py").write_text(
        "class OrderValidator:\n    def validate(self, order):\n        return order.quantity > 0\n")
    (src / "checkout.py").write_text(
        "from src.order_validator import OrderValidator\n\n"
        "def checkout(order):\n    return OrderValidator().validate(order)\n")
    (src / "order_repository.py").write_text("class OrderRepository:\n    def save(self, order):\n        pass\n")
    (src / "order.py").write_text("class Order:\n    pass\n")
    return tmp_path


# ------------------------------------------------ current behaviour, pinned

def test_exact_name_containment_and_scored_owner_resolution_are_unchanged(repo):
    # Exact basename (two tokens) resolves; token containment resolves.
    assert prefer_existing_artifact_owners(["lib/order_validator.py"], GOAL, str(repo)) == ["src/order_validator.py"]
    assert prefer_existing_artifact_owners(["src/order_validator_impl.py"], GOAL, str(repo)) == [
        "src/order_validator.py"]
    # An explicit request for a new artifact is honoured.
    assert prefer_existing_artifact_owners(
        ["src/order_validator_v2.py"], "create a new order validator module", str(repo)) == [
        "src/order_validator_v2.py"]


def test_before_this_change_differently_named_owners_pass_as_new(repo):
    """The pre-fix case: nothing deterministic connects these to
    order_validator.py, so they are accepted as new owners."""
    planned = ["src/order_rules.py", "src/order_validation.py", "src/stock_checker.py"]
    assert prefer_existing_artifact_owners(planned, GOAL, str(repo)) == planned


# ------------------------------------------------ grounded candidates

def test_a_candidate_needs_the_goal_to_name_its_responsibility(repo):
    candidates = grounded_owner_candidates(str(repo), GOAL)
    assert [c.path for c in candidates] == ["src/order_validator.py"]
    owner = candidates[0]
    assert owner.role == "valid" and owner.members == ("validate",)
    assert owner.referenced_by == ("src/checkout.py",)  # from the planning graph edges


def test_a_shared_domain_noun_alone_is_never_a_candidate(repo):
    # "order" appears in order_repository.py/order.py and in the goal; neither
    # is responsible for what the goal asks.
    assert grounded_owner_candidates(str(repo), "Persist orders with an audit trail") == []
    # Two goal nouns in a name are still not a responsibility the goal names.
    (repo / "src" / "order_stock_repository.py").write_text("class OrderStockRepository:\n    def load(self):\n        pass\n")
    assert grounded_owner_candidates(str(repo), "Report order stock levels nightly") == []


def test_findings_for_differently_named_new_owners_and_none_for_unrelated_files(repo):
    candidates = grounded_owner_candidates(str(repo), GOAL)
    findings = find_ownership_findings([
        ("src/order_rules.py", "s1", "reject orders over the stock level"),
        ("src/order_validation.py", "s1", "validation of orders"),
        ("src/stock_checker.py", "s2", "validate stock for each order"),
        ("src/audit_log.py", "s3", "write an audit log line"),
    ], candidates, touched_paths=["src/checkout.py"])
    by_path = {f.planned_path: f for f in findings}
    assert set(by_path) == {"src/order_rules.py", "src/order_validation.py", "src/stock_checker.py"}
    assert all(f.candidate_owner == "src/order_validator.py" and f.status == UNRESOLVED
               and f.classification == "GROUNDED" for f in findings)
    assert "role:valid" in by_path["src/stock_checker.py"].match_basis
    assert "graph:referenced_by_planned_file" in by_path["src/order_rules.py"].match_basis
    assert by_path["src/order_rules.py"].relationships == ("src/checkout.py references src/order_validator.py",)


# ------------------------------------------------ status rules

def _one_finding(repo, planned="src/order_rules.py"):
    return find_ownership_findings([(planned, "s1", "reject orders")], grounded_owner_candidates(str(repo), GOAL))


def test_a_justification_alone_acknowledges_but_never_satisfies(repo):
    [finding] = settle_findings(_one_finding(repo), goal=GOAL,
                                justifications={"src/order_rules.py": "rules differ from validation"})
    assert finding.status == ACKNOWLEDGED and finding.justification == "rules differ from validation"


def test_goal_intent_removal_or_human_approval_satisfy(repo):
    [by_goal] = settle_findings(_one_finding(repo), goal="Create a new order rules module for checkout")
    assert by_goal.status == SATISFIED and by_goal.satisfied_by == "goal_intent"
    [by_migration] = settle_findings(_one_finding(repo), goal=GOAL, removed_paths=["src/order_validator.py"])
    assert by_migration.satisfied_by == "plan_removes_owner"
    finding = _one_finding(repo)[0]
    [by_human] = settle_findings([finding], goal=GOAL, human_approved=[finding.id])
    assert by_human.satisfied_by == "human_approval"


def test_findings_are_grounded_non_terminal_obligations(repo):
    ledger = ObligationLedger()
    record_findings(ledger, settle_findings(_one_finding(repo), goal=GOAL), revision=0, source="test")
    [record] = [ledger.current(i) for i in ledger.ids_by_kind(ObligationKind.GROUNDED_OWNERSHIP)]
    assert record.status is ObligationStatus.PENDING and record.authority.value == "grounded"
    assert not record.terminal_required and ledger.unresolved_terminal_obligations() == []
    assert record.evidence["id"] == record.id == "ownership.src/order_rules.py::src/order_validator.py"


def test_the_justification_is_read_from_the_structured_file_list_block():
    design = ('Design...\n```json\n{"files": ["src/order_rules.py"], '
              '"ownership_justification": {"src/order_rules.py": "separate pricing rules"}}\n```')
    assert parse_ownership_justifications(design) == {"src/order_rules.py": "separate pricing rules"}
    assert parse_ownership_justifications("src/order_rules.py: ownership_justification: prose") == {}


# ------------------------------------------------ direct path, end to end

@pytest.mark.asyncio
async def test_the_direct_run_surfaces_the_owner_before_planning_and_the_finding_before_writing(repo):
    """Planner and Architect see order_validator.py as the likely owner; the
    Architect still plans order_rules.py; the finding is recorded and shown to
    the Developer, and the run is not denied."""
    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.triage import (
        ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass,
    )
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.engineering_triage.enabled = True
    cfg.llm_chain = []
    cfg.paths.skills = str(repo / "skills")
    llm = LLMClient(cfg)
    prompts = {"planner": [], "architect": []}

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        if "Kriya Planner Agent" in (system_prompt or ""):
            prompts["planner"].append(user_prompt)
            return "Step 1: add src/order_rules.py with the stock rule"
        if "Kriya Architect Agent" in (system_prompt or ""):
            prompts["architect"].append(user_prompt)
            return 'Design: src/order_rules.py holds the stock rule.\n```json\n{"files": ["src/order_rules.py"]}\n```'
        return "Review: Approved"

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    ))
    engine.developer.run_generation = AsyncMock(return_value=[{
        "filepath": "src/order_rules.py", "content": "def within_stock(order, stock):\n    return order.quantity <= stock\n"}])
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}):
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(repo))

    assert "src/order_validator.py (responsibility: valid" in prompts["planner"][0]
    assert "src/order_validator.py (responsibility: valid" in prompts["architect"][0]
    assert res["quality_gates_passed"] is True  # a finding never denies
    [finding] = res["ownership_findings"]
    assert (finding["planned_path"], finding["candidate_owner"], finding["status"]) == (
        "src/order_rules.py", "src/order_validator.py", UNRESOLVED)
    dev_kwargs = json.dumps(engine.developer.run_generation.await_args_list[0].kwargs, default=str)
    assert "src/order_rules.py may duplicate src/order_validator.py" in dev_kwargs


# ------------------------------------------------ enforce path, end to end

@pytest.mark.asyncio
async def test_enforce_shows_candidates_to_the_planner_and_records_an_acknowledged_finding(repo, monkeypatch):
    from kriya.config import AppConfig
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_schema import (
        ChangeKind, EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask,
    )
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    monkeypatch.setattr(wc, "create_git_worktree", lambda workspace: workspace)
    plan = EngineeringPlan(plan_id="prd021", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="reject orders over the stock level", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="src/order_rules.py", action=FileAction.CREATE),
                       PlannedFile(path="src/checkout.py", action=FileAction.MODIFY)],
        ownership_justification={"src/order_rules.py": "stock rules are pricing policy, not validation"},
    )])
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    ))
    we.kernel = SimpleNamespace(config=AppConfig())
    requests, contexts, ledgers = [], [], []

    async def planner(request, **kwargs):
        requests.append(request)
        return "fake plan text"

    async def fake_run(**kwargs):
        contexts.append(kwargs.get("supplementary_context", ""))
        ledgers.append(kwargs.get("obligation_ledger"))
        (repo / "src" / "order_rules.py").write_text("RULE = 1\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/order_rules.py"]}

    we.planner.run = planner
    we.run_generation_workflow = fake_run
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await wc.WorkflowController(we).execute(GOAL, str(repo), migration_mode="enforce")

    assert "src/order_validator.py (responsibility: valid" in requests[0]
    assert "src/order_rules.py may duplicate src/order_validator.py" in contexts[0]
    assert "acknowledged: stock rules are pricing policy" in contexts[0]
    record = ledgers[0].current("ownership.src/order_rules.py::src/order_validator.py")
    assert record.status is ObligationStatus.INDETERMINATE and record.evidence["status"] == ACKNOWLEDGED
    assert result.legacy_result["quality_gates_passed"] is True  # acknowledged, never denied
