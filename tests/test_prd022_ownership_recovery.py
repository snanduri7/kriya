"""PRD-022: first-occurrence ownership recovery and reviewer evidence.

Proof, through the real WorkflowEngine (model calls stubbed): the very next
retry after a first deterministic ownership violation already carries the
grounded owner's exact current source, patch-only authority over it, and
nothing unrelated - so no new injection machinery was added. A repeated
violation still invalidates the architectural choice (threshold unchanged).
Grounded near-duplicate findings reach the Reviewer and a human approver as
advisory evidence only; they never fail a gate and no reviewer verdict
changes them.
"""
import json
import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.architectural_choice import INVALIDATION_REPEAT_THRESHOLD
from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationStatus

OWNER = "def price(quantity, unit):\n    return quantity * unit\n"
TEST = "from src.pricing import price\n\n\ndef test_price():\n    assert price(2, 3) == 6\n"
PARALLEL = [
    {"filepath": "src/discount_pricing.py", "content": (
        "def discounted_price(quantity, unit):\n    total = quantity * unit\n"
        "    return total * 0.9 if quantity > 10 else total\n")},
    {"filepath": "tests/test_pricing.py", "content": (
        "from src.discount_pricing import discounted_price\n\n\n"
        "def test_price():\n    assert discounted_price(2, 3) == 6\n")},
]
FIXED = [{"filepath": "src/pricing.py", "content": (
    "def price(quantity, unit):\n    total = quantity * unit\n    return total * 0.9 if quantity > 10 else total\n")}]


def _git_repo(root, files):
    for path, content in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
        with open(os.path.join(root, path), "w") as fh:
            fh.write(content)
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)


def _engine(tmp_path, architect_files, developer_outputs, *, reviewer=None, **autonomy):
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    for key, value in autonomy.items():
        setattr(cfg.autonomy, key, value)
    cfg.llm_chain = []
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)
    review_prompts = []

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system = system_prompt or ""
        if "Kriya Planner Agent" in system:
            return "Step 1: implement the change"
        if "Kriya Architect Agent" in system:
            return "Design\n```json\n" + json.dumps({"files": architect_files}) + "\n```"
        if "Kriya Reviewer Agent" in system:
            review_prompts.append(user_prompt)
            return reviewer or "Review: Approved"
        return "Review: Approved"

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    calls = []

    async def generate(*args, **kwargs):
        calls.append(kwargs)
        return developer_outputs[min(len(calls), len(developer_outputs)) - 1]

    engine.developer.run_generation = AsyncMock(side_effect=generate)
    return engine, calls, review_prompts


def _gates_pass():
    return (patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
                  return_value={"success": True, "output": ""}),
            patch("kriya.tools.validate.PolymorphicValidator.run_tests",
                  return_value={"success": True, "output": ""}))


@pytest.fixture
def pricing_repo(tmp_path):
    ws = tmp_path / "ws"
    _git_repo(str(ws), {"src/__init__.py": "", "src/pricing.py": OWNER, "src/shipping.py": "def ship():\n    return 1\n",
                        "tests/test_pricing.py": TEST})
    return ws


@pytest.mark.asyncio
async def test_the_first_corrective_retry_carries_the_exact_owner_source_and_nothing_unrelated(tmp_path, pricing_repo):
    engine, calls, _ = _engine(tmp_path, ["src/discount_pricing.py", "tests/test_pricing.py"], [PARALLEL, FIXED])
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(
            goal="Apply a 10% discount to price() for quantities over 10", workspace_path=str(pricing_repo))

    retry = calls[1]  # the very next Developer request after the first violation
    assert retry["known_target_files"] == ["src/pricing.py"]
    assert retry["implicated_files"] == ["src/pricing.py"]
    assert {path: op.value for path, op in retry["operation_by_file"].items()} == {
        "src/pricing.py": "repair_with_patch"}  # patch authority over the owner only
    assert "src/pricing.py" in retry["files_with_current_content"]
    assert "owner=src/pricing.py, candidate=src/discount_pricing.py" in retry["prior_error_context"]
    blob = json.dumps(retry, default=str)
    assert json.dumps(OWNER)[1:-1] in blob  # the owner's exact current source
    assert "def ship" not in blob  # nothing unrelated
    # That one corrective retry is enough: the redirected test is restored to
    # its baseline and the abandoned parallel file removed deterministically
    # (the retry had no authority over either), so no redundant retry follows.
    assert res["quality_gates_passed"] is True and len(calls) == 2
    assert (pricing_repo / "tests" / "test_pricing.py").read_text() == TEST
    assert not (pricing_repo / "src" / "discount_pricing.py").exists()
    assert "0.9" in (pricing_repo / "src" / "pricing.py").read_text()


@pytest.mark.asyncio
async def test_a_repeated_violation_still_invalidates_the_architectural_choice(tmp_path, pricing_repo):
    """The Developer writes the parallel file again on the retry (it is not
    removed when written again), so the second occurrence is the confirmed
    architectural choice - the threshold and the mechanism are unchanged."""
    from kriya.workflow import workflow as workflow_module
    from kriya.workflow.architectural_choice import classify_ownership_violations

    assert INVALIDATION_REPEAT_THRESHOLD == 2
    diagnostics = []

    def spy(*args, **kwargs):
        changes, diag = classify_ownership_violations(*args, **kwargs)
        diagnostics.append(diag)
        return changes, diag

    engine, calls, _ = _engine(tmp_path, ["src/discount_pricing.py", "tests/test_pricing.py"], [PARALLEL])
    p1, p2 = _gates_pass()
    with p1, p2, patch.object(workflow_module, "classify_ownership_violations", side_effect=spy):
        res = await engine.run_generation_workflow(
            goal="Apply a 10% discount to price() for quantities over 10", workspace_path=str(pricing_repo))

    assert res["quality_gates_passed"] is False
    assert diagnostics[0] is None  # first occurrence: an ordinary redirect
    assert diagnostics[1]["reason_code"] == "ARCHITECTURE_CHOICE_INVALIDATED"
    assert diagnostics[1]["invalidated_candidate"] == "src/discount_pricing.py"
    assert diagnostics[1]["grounded_owner"] == "src/pricing.py"


@pytest.fixture
def order_repo(tmp_path):
    ws = tmp_path / "ws"
    _git_repo(str(ws), {
        "src/__init__.py": "",
        "src/order_validator.py": "class OrderValidator:\n    def validate(self, order):\n        return order.quantity > 0\n",
        "src/checkout.py": "from src.order_validator import OrderValidator\n\n\ndef checkout(order):\n"
                           "    return OrderValidator().validate(order)\n",
    })
    return ws


GOAL = "Reject orders whose quantity exceeds the stock level during checkout validation"
RULES = [{"filepath": "src/order_rules.py",
          "content": "def validate_stock(order, stock):\n    return order.quantity <= stock\n"}]


def _triage_task(engine):
    from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    engine.kernel.config.engineering_triage.enabled = True
    engine.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    ))


@pytest.mark.asyncio
async def test_a_near_duplicate_reaches_the_reviewer_as_advisory_evidence_and_fails_no_gate(tmp_path, order_repo):
    engine, _, review_prompts = _engine(tmp_path, ["src/order_rules.py"], [RULES],
                                        reviewer="REJECTED - critical: duplicate of OrderValidator")
    _triage_task(engine)
    ledger = ObligationLedger()
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(order_repo), obligation_ledger=ledger)

    assert res["quality_gates_passed"] is True  # advisory: never a gate
    assert any("src/order_rules.py may duplicate existing src/order_validator.py" in p
               and "not a verified defect" in p for p in review_prompts)
    # The reviewer's own verdict does not upgrade the suspicion.
    record = ledger.current("ownership.src/order_rules.py::src/order_validator.py")
    assert record.kind is ObligationKind.GROUNDED_OWNERSHIP
    assert record.status is ObligationStatus.PENDING and record.authority.value == "grounded"
    assert all(r.authority.value == "grounded" for r in ledger.history(record.id))


@pytest.mark.asyncio
async def test_a_duplicate_created_without_a_planned_finding_is_found_after_generation(tmp_path, order_repo):
    """The Architect never names the new file; the Developer creates it
    anyway. The post-generation check records it for review."""
    engine, _, review_prompts = _engine(tmp_path, ["src/checkout.py"], [RULES + [
        {"filepath": "src/checkout.py", "content": "from src.order_rules import validate_stock\n\n\n"
                                                   "def checkout(order):\n    return validate_stock(order, 5)\n"}]])
    _triage_task(engine)
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(order_repo))

    findings = {f["planned_path"]: f for f in res.get("ownership_findings", [])}
    assert "src/order_rules.py" in findings and findings["src/order_rules.py"]["status"] == "unresolved"
    assert any("src/order_rules.py may duplicate" in p for p in review_prompts)


@pytest.mark.asyncio
async def test_a_human_approver_sees_the_finding_as_evidence_not_a_denial(tmp_path, order_repo):
    engine, _, _ = _engine(tmp_path, ["src/order_rules.py"], [RULES], mode="human-in-the-loop")
    _triage_task(engine)
    reasons = []

    def approve(diffs, reason):
        reasons.append(reason)
        return True

    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(order_repo),
                                                   approval_callback=approve)

    assert res["quality_gates_passed"] is True
    assert reasons and "src/order_rules.py may duplicate existing src/order_validator.py" in reasons[0]
