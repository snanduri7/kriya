"""PRD-023: derived contract change classification, then escalation.

Every public contract change the brownfield API detector reports is
classified from evidence first (AUTHORIZED_DIRECT, UNAUTHORIZED,
POTENTIALLY_DERIVED, INDETERMINATE). A clear unauthorized change stays
blocking and is never offered to anyone; only evidence-backed potentially-
derived or indeterminate changes may go to a human, under an explicit policy,
and only a real approval creates a revision-bound authorization.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import _classify_and_escalate_contract_changes, run_attempt
from kriya.workflow.contract_authority import (
    AuthorizationAuthority,
    AuthorizationProvenance,
    derive_direct_contract_authorizations,
)
from kriya.workflow.contract_classification import (
    CONTRACT_ESCALATION_DECLINED,
    CONTRACT_ESCALATION_UNAVAILABLE,
    ContractChangeStatus,
    classify_api_violations,
)
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.file_resolution import find_brownfield_public_api_changes
from kriya.workflow.obligations import ObligationKind, ObligationLedger
from kriya.workflow.plan_schema import (
    ChangeKind, EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask,
)
from kriya.workflow.state import GenerationState

GOAL = (
    "Extend the existing `CustomerRecord` contract with a new required field named `region`.\n\n"
    "Requirements:\n- Update all affected consumers.\n"
)
RECORD = "m1/src/main/java/com/example/m1/CustomerRecord.java"
SUMMARY = "m2/src/main/java/com/example/m2/CustomerSummary.java"
SERVICE = "m2/src/main/java/com/example/m2/SummaryService.java"
LABEL = "m2/src/main/java/com/example/m2/Label.java"
LABELER = "m2/src/main/java/com/example/m2/Labeler.java"
ORIGINAL = {
    RECORD: "package com.example.m1; public record CustomerRecord(long customerId,String firstName) {}\n",
    SUMMARY: "package com.example.m2; public record CustomerSummary(long customerId,String displayName) {}\n",
    SERVICE: ("package com.example.m2; import com.example.m1.CustomerRecord;\n"
              "public class SummaryService { public CustomerSummary summarize(CustomerRecord r)"
              "{return new CustomerSummary(r.customerId(),r.firstName());} }\n"),
    LABEL: "package com.example.m2; public record Label(String text) {}\n",
    LABELER: "package com.example.m2; public class Labeler { Label make(){return new Label(\"x\");} }\n",
}
CANDIDATE = {
    SUMMARY: ("package com.example.m2; public record CustomerSummary(long customerId,String displayName,"
              "String region) {}\n"),
    SERVICE: ("package com.example.m2; import com.example.m1.CustomerRecord;\n"
              "public class SummaryService { public CustomerSummary summarize(CustomerRecord r)"
              "{return new CustomerSummary(r.customerId(),r.firstName(),r.region());} }\n"),
}
PLAN = EngineeringPlan(plan_id="prd023-plan", kind=ChangeKind.TASK, subtasks=[
    Subtask(id="s1", description="extend CustomerRecord", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path=RECORD, action=FileAction.MODIFY)]),
    Subtask(id="s2", description="propagate", execution_method=ExecutionMethod.MODEL, depends_on=["s1"],
            planned_files=[PlannedFile(path=SUMMARY, action=FileAction.MODIFY),
                           PlannedFile(path=SERVICE, action=FileAction.MODIFY),
                           PlannedFile(path=LABEL, action=FileAction.MODIFY),
                           PlannedFile(path=LABELER, action=FileAction.MODIFY)]),
])


@pytest.fixture
def repo(tmp_path):
    for path, content in ORIGINAL.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)
    return tmp_path


def _classify(repo, candidate, *, goal=GOAL, plan=PLAN):
    originals = {path: ORIGINAL[path] for path in candidate}
    authorizations = derive_direct_contract_authorizations(goal, plan)
    violations = find_brownfield_public_api_changes(str(repo), originals, candidate, "", [])
    return classify_api_violations(violations, original_contents=originals, final_contents=candidate,
                                   run_authorizations=authorizations)


# ------------------------------------------------ one case per status

def test_a_removed_signature_with_an_active_caller_is_unauthorized(tmp_path):
    (tmp_path / "calc.py").write_text("def compute(x):\n    return x\n")
    (tmp_path / "use.py").write_text("from calc import compute\nprint(compute(1))\n")
    original = {"calc.py": "def compute(x):\n    return x\n",
                "use.py": "from calc import compute\nprint(compute(1))\n"}
    # The caller was edited too, but still calls the removed function.
    final = {"calc.py": "def calculate(x):\n    return x\n",
             "use.py": "from calc import compute\nprint(compute(2))\n"}
    violations = find_brownfield_public_api_changes(str(tmp_path), original, final, "", [])
    [c] = classify_api_violations(violations, original_contents=original, final_contents=final,
                                  run_authorizations=derive_direct_contract_authorizations(GOAL, PLAN))
    assert c.status is ContractChangeStatus.UNAUTHORIZED and c.change_category == "remove"
    assert c.evidence["callers"] == ["use.py"]


def test_a_directly_authorized_change_is_classified_authorized_direct(repo):
    candidate = {RECORD: ("package com.example.m1; public record CustomerRecord(long customerId,"
                          "String firstName,String region) {}\n")}
    (repo / "m2" / "Use.java").write_text("new CustomerRecord(1,\"a\");\n")
    authorizations = derive_direct_contract_authorizations(GOAL, PLAN)
    originals = {RECORD: ORIGINAL[RECORD]}
    assert find_brownfield_public_api_changes(str(repo), originals, candidate, "", authorizations) == []
    all_changes = find_brownfield_public_api_changes(str(repo), originals, candidate, "", [])
    [c] = classify_api_violations([], original_contents=originals, final_contents=candidate,
                                  run_authorizations=authorizations, authorized_changes=all_changes)
    assert c.status is ContractChangeStatus.AUTHORIZED_DIRECT and c.evidence["authorization_ids"]


def test_an_ambiguous_downstream_migration_is_potentially_derived_never_authorized(repo):
    """The real PRV-08 shape: the goal authorizes CustomerRecord; the
    candidate also reshapes CustomerSummary, which SummaryService maps from
    CustomerRecord. Evidence for a derived change - still a violation."""
    [c] = _classify(repo, CANDIDATE)
    assert c.owner == SUMMARY and c.status is ContractChangeStatus.POTENTIALLY_DERIVED
    assert c.evidence["relationship"] == [f"{SERVICE} maps CustomerRecord ({RECORD}) to {SUMMARY}"]


def test_an_unsupported_relationship_is_indeterminate(repo):
    candidate = {LABEL: "package com.example.m2; public record Label(String text,int size) {}\n",
                 LABELER: "package com.example.m2; public class Labeler { Label make(){return new Label(\"x\",1);} }\n"}
    [c] = _classify(repo, candidate)
    assert c.owner == LABEL and c.status is ContractChangeStatus.INDETERMINATE


def test_callers_left_on_the_old_shape_or_no_authorized_change_at_all_are_unauthorized(repo):
    [left] = _classify(repo, {SUMMARY: CANDIDATE[SUMMARY]})  # SummaryService not co-updated
    assert left.status is ContractChangeStatus.UNAUTHORIZED and left.evidence["stale_callers"] == [SERVICE]
    [none] = _classify(repo, CANDIDATE, goal="Improve the summary output")  # nothing authorized
    assert none.status is ContractChangeStatus.UNAUTHORIZED


# ------------------------------------------------ escalation

def _ctx(repo, *, escalation="deny", mode="guardrails", callback=None):
    cfg = AppConfig()
    cfg.autonomy.contract_change_escalation = escalation
    cfg.autonomy.mode = mode
    return SimpleNamespace(kernel=Kernel(config=cfg), approval_callback=callback, grounding_goal=GOAL, goal=GOAL,
                           structured_plan=PLAN, current_subtask_id="s2", obligation_ledger=ObligationLedger(),
                           workspace_path=str(repo))


async def _escalate(repo, ctx, candidate=CANDIDATE):
    originals = {path: ORIGINAL[path] for path in candidate}
    violations = find_brownfield_public_api_changes(str(repo), originals, candidate, "", [])
    state = GenerationState()
    remaining, classes, reason = await _classify_and_escalate_contract_changes(
        state, ctx, violations, originals, candidate, derive_direct_contract_authorizations(GOAL, PLAN))
    return state, remaining, classes, reason


@pytest.mark.asyncio
async def test_the_default_policy_never_asks_and_stays_blocked(repo):
    callback = AsyncMock(return_value=True)
    ctx = _ctx(repo, mode="human-in-the-loop", callback=callback)
    _, remaining, classes, reason = await _escalate(repo, ctx)
    assert len(remaining) == 1 and reason is None
    callback.assert_not_called()
    [record] = ctx.obligation_ledger.current_by_kind(ObligationKind.CONTRACT_CHANGE_CLASSIFICATION)
    assert record.evidence["status"] == "potentially_derived"


@pytest.mark.asyncio
async def test_autonomous_mode_fails_closed_when_a_human_is_required_but_unavailable(repo):
    for ctx in (_ctx(repo, escalation="human", mode="guardrails", callback=AsyncMock(return_value=True)),
                _ctx(repo, escalation="human", mode="human-in-the-loop", callback=None)):
        state, remaining, _, reason = await _escalate(repo, ctx)
        assert len(remaining) == 1 and reason == CONTRACT_ESCALATION_UNAVAILABLE
        assert state.human_contract_authorizations == []


@pytest.mark.asyncio
async def test_a_declined_escalation_stays_blocked(repo):
    ctx = _ctx(repo, escalation="human", mode="human-in-the-loop", callback=lambda diffs, reason: False)
    _, remaining, _, reason = await _escalate(repo, ctx)
    assert len(remaining) == 1 and reason == CONTRACT_ESCALATION_DECLINED


@pytest.mark.asyncio
async def test_an_approval_creates_a_revision_bound_record_for_only_the_offered_changes(repo, tmp_path):
    # The candidate also removes an unrelated signature a caller still uses:
    # clearly unauthorized, never offered, still blocking.
    (repo / "m2" / "src" / "main" / "java" / "com" / "example" / "m2" / "Old.java").write_text(
        "package com.example.m2; public class Old { public int legacy(){return 1;} }\n")
    (repo / "m2" / "Caller.java").write_text("class Caller { int x = new Old().legacy(); }\n")
    old = "m2/src/main/java/com/example/m2/Old.java"
    ORIGINAL[old] = (repo / old).read_text()
    candidate = {**CANDIDATE, old: "package com.example.m2; public class Old { }\n"}
    prompts = []

    def approve(diffs, reason):
        prompts.append(reason)
        return True

    ctx = _ctx(repo, escalation="human", mode="human-in-the-loop", callback=approve)
    try:
        state, remaining, classes, reason = await _escalate(repo, ctx, candidate)
    finally:
        del ORIGINAL[old]  # module-level fixture data: never leak into other tests

    assert reason is None
    assert [v["owner"] for v in remaining] == [old]  # the clear violation still blocks
    assert SUMMARY in prompts[0] and old not in prompts[0]
    [grant] = state.human_contract_authorizations
    assert grant.provenance is AuthorizationProvenance.HUMAN
    assert grant.authority is AuthorizationAuthority.HUMAN_APPROVED
    assert (grant.affected_owner, grant.affected_symbol, grant.allowed_change_category.value) == (
        SUMMARY, "CustomerSummary", "modify")
    assert grant.legal_scope == {"owner": SUMMARY, "subtask_id": "s2"}
    assert grant.plan_revision == "prd023-plan" and grant.authorization_id.startswith("human::")
    assert grant.derivation_evidence["classification"]["status"] == "potentially_derived"


# ------------------------------------------------ end to end through run_attempt

@pytest.mark.asyncio
async def test_the_pre_write_gate_escalates_and_the_approval_authorizes_the_candidate(repo):
    from test_workflow import _minimal_attempt_ctx

    def run_ctx(escalation, callback):
        developer = AsyncMock()
        developer.run_generation = AsyncMock(return_value=[
            {"filepath": SUMMARY, "content": CANDIDATE[SUMMARY]},
            {"filepath": SERVICE, "content": CANDIDATE[SERVICE]},
        ])
        cfg = AppConfig()
        cfg.autonomy.mode = "human-in-the-loop"
        cfg.autonomy.contract_change_escalation = escalation
        return _minimal_attempt_ctx(
            repo, developer=developer, goal=GOAL, grounding_goal=GOAL, kernel=Kernel(config=cfg),
            architect_files=[SUMMARY, SERVICE], expected_files_upfront=[SUMMARY, SERVICE],
            architect_basename_to_path={"CustomerSummary.java": SUMMARY, "SummaryService.java": SERVICE},
            structured_plan=PLAN, current_subtask_id="s2", completed_subtask_ids=frozenset({"s1"}),
            approval_callback=callback,
        )

    gates = (patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
                   return_value={"success": True, "output": ""}),
             patch("kriya.tools.validate.PolymorphicValidator.run_tests",
                   return_value={"success": True, "output": ""}))
    blocked = GenerationState()
    with gates[0], gates[1], pytest.raises(QualityGateFailure) as denied:
        await run_attempt(blocked, run_ctx("deny", None))
    diagnostics = denied.value.failure.diagnostics
    assert denied.value.failure.type == "brownfield_public_api_changed"
    assert diagnostics["contract_classifications"][0]["status"] == "potentially_derived"

    approved = GenerationState()
    with gates[0], gates[1]:
        await run_attempt(approved, run_ctx("human", lambda diffs, reason: True))  # no QualityGateFailure
    [grant] = approved.human_contract_authorizations
    # The terminal re-check uses the same authority: nothing left to reject.
    assert find_brownfield_public_api_changes(
        str(repo), {SUMMARY: ORIGINAL[SUMMARY], SERVICE: ORIGINAL[SERVICE]}, CANDIDATE, "", [grant]) == []
