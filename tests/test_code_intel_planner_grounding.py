"""Code Intelligence R1 slice 2, item 8: Planner grounding.

Both Planner paths receive the ranked Code Intelligence candidates the goal
points at - symbol id, kind, path, compact signature, channels, configuration
entries included - and never a source body: the direct Planner/Architect
through the retrieval stage's graph context, the enforce Planner through its
authoritative request. Grounding is localization only; the plan's write
scope is decided exactly as before.
"""
import asyncio
import subprocess
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _fake_embedding import StaticEmbedder

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig

GOAL = "PricingService.applyDiscount must honour the pricing.maxDiscount limit"
PRICING = textwrap.dedent("""\
    package shop;

    public class PricingService {
        @Value("${pricing.maxDiscount}")
        private int maxDiscount;

        public int applyDiscount(int price, int discount) {
            return price - discount;
        }
    }
    """)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


@pytest.fixture
def indexed(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/main/java/shop").mkdir(parents=True)
    (repo / "src/main/resources").mkdir(parents=True)
    (repo / "src/main/java/shop/PricingService.java").write_text(PRICING)
    (repo / "src/main/resources/application.properties").write_text("pricing.maxDiscount=30\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    embedder = StaticEmbedder([0.3, 0.4, 0.5])
    asyncio.run(RepositoryAnalyzer(str(repo)).index_repository(
        cfg, generate_conventions_skill=False, embedding_client=embedder))
    return repo, cfg, embedder


def _assert_grounding(text):
    start = text.index("=== CODE INTELLIGENCE LOCALIZATION CANDIDATES")
    block = text[start:].split("\n\n", 1)[0]  # the map section alone
    assert "1. [method] shop.PricingService.applyDiscount - src/main/java/shop/PricingService.java" in block
    assert "public int applyDiscount(int price, int discount)" in block
    assert "id=java:src/main/java/shop/PricingService.java#shop.PricingService.applyDiscount(int,int)" in block
    assert "[config_key] pricing.maxDiscount - src/main/resources/application.properties" in block
    assert "(via qualified_symbol" in block
    assert "return price - discount;" not in block  # never a body


@pytest.mark.asyncio
async def test_enforce_planner_receives_code_intelligence_candidates(indexed, monkeypatch):
    repo, cfg, embedder = indexed
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_schema import (
        ChangeKind,
        EngineeringPlan,
        ExecutionMethod,
        FileAction,
        PlannedFile,
        Subtask,
    )
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    monkeypatch.setattr(wc, "create_git_worktree", lambda workspace: workspace)
    plan = EngineeringPlan(plan_id="ci-ground", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="cap the discount", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="src/main/java/shop/PricingService.java", action=FileAction.MODIFY)])])
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT))
    we.kernel = SimpleNamespace(config=cfg)
    requests = []

    async def planner(request, **kwargs):
        requests.append(request)
        return "plan"

    async def fake_run(**kwargs):
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.planner.run = planner
    we.run_generation_workflow = fake_run
    with patch("kriya.memory.embedding.configured_client", return_value=embedder), \
         patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        await wc.WorkflowController(we).execute(GOAL, str(repo), migration_mode="enforce")
    _assert_grounding(requests[0])
    assert requests[0].startswith("Original product request:\n" + GOAL)  # the goal itself is untouched


@pytest.mark.asyncio
async def test_direct_planner_and_architect_receive_code_intelligence_candidates(indexed):
    repo, cfg, embedder = indexed
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = []
    llm = LLMClient(cfg)
    prompts = {"planner": [], "architect": []}

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        if "Kriya Planner Agent" in (system_prompt or ""):
            prompts["planner"].append(user_prompt)
            return "Step 1: cap the discount in PricingService.applyDiscount"
        if "Kriya Architect Agent" in (system_prompt or ""):
            prompts["architect"].append(user_prompt)
            return 'Design.\n```json\n{"files": ["src/main/java/shop/PricingService.java"]}\n```'
        return "Review: Approved"

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.developer.run_generation = AsyncMock(return_value=[{
        "filepath": "src/main/java/shop/PricingService.java", "content": PRICING}])
    with patch("kriya.memory.embedding.configured_client", return_value=embedder), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True}):
        await engine.run_generation_workflow(goal=GOAL, workspace_path=str(repo))
    _assert_grounding(prompts["planner"][0])
    _assert_grounding(prompts["architect"][0])
