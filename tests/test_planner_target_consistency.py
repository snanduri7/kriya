"""Planner Reliability R1, PR-3: a plan is consistent with the Code
Intelligence targets it states it will change.

Live (spring-framework-petclinic, pet types cache): the Planner was shown
the relevant tools-config.xml configuration candidates, planned a change to
the cache configuration, but owned only Java files - the plan contradicted
itself and the repair never converged. A subtask's stated mutation_targets
are now checked against the CURRENT Code Intelligence view: unknown ids are
PLAN_TARGET_UNKNOWN, a target in a file the subtask does not own is
PLAN_TARGET_INCONSISTENT, and the bounded repair names the exact correction.
Code Intelligence stays evidence: stating a target is optional.
"""
import asyncio
import json
import os
import subprocess
import textwrap

import pytest

from kriya.code_intel.service import CodeIntelligenceService
from kriya.core import role_metrics as rm
from kriya.workflow import planner_repair as pr
from kriya.workflow.plan_normalization import coalesce_same_file_owners
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.plan_targets import (
    PLAN_TARGET_INCONSISTENT,
    PLAN_TARGET_UNKNOWN,
    check_plan_targets,
    validate_mutation_targets,
)

TOOLS_XML = "src/main/resources/spring/tools-config.xml"
SERVICE_JAVA = "src/main/java/shop/PetService.java"
TOOLS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <beans xmlns="http://www.springframework.org/schema/beans">
        <bean id="cacheManager" class="org.springframework.cache.jcache.JCacheCacheManager">
            <property name="cacheNames" value="vets"/>
        </bean>
    </beans>
    """)


@pytest.fixture
def indexed(tmp_path):
    repo = tmp_path / "repo"
    for rel, text in ((TOOLS_XML, TOOLS),
                      (SERVICE_JAVA, "package shop;\npublic class PetService {\n"
                                     "    public Object findPetTypes() { return null; }\n}\n")):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    db = str(tmp_path / "dependency_graph.db")
    service = CodeIntelligenceService(str(repo), db)
    service.refresh()
    ids = {key: service.find_symbol(key)[0].symbol_id for key in ("cacheManager", "findPetTypes")}
    service.close()
    return str(repo), db, ids


def _plan(*subtasks):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [{"id": "ac1", "description": "x", "method": "judgment"}],
        "subtasks": [{"execution_method": "model", "execution_role": "implementation", "depends_on": [],
                      "acceptance_criteria_ids": ["ac1"], "relevant_global_invariant_ids": ["gi1"],
                      "verification": [{"type": "tool", "description": "compiles", "tool_name": "compile"}], **st} for st in subtasks]})


def _owning(sid, files, targets):
    return {"id": sid, "description": f"{sid} change",
            "planned_files": [{"path": f, "action": "modify"} for f in files], "mutation_targets": targets}


def test_the_live_spring_xml_shape_is_inconsistent_and_the_repair_names_the_xml(indexed):
    """The plan targets the cacheManager bean but owns only the Java service."""
    repo, db, ids = indexed
    plan = _plan(_owning("s1", [SERVICE_JAVA], [{"target_id": ids["cacheManager"], "file": TOOLS_XML}]))
    errors, codes, evidence = check_plan_targets(plan, repo, lambda: db)
    assert codes == [PLAN_TARGET_INCONSISTENT] and len(errors) == 1
    assert evidence[0]["resolved_path"] == TOOLS_XML and evidence[0]["verdict"] == PLAN_TARGET_INCONSISTENT
    prompt = pr.build_structured_plan_repair_prompt("goal", "{}", errors, codes, 1, validation_evidence=evidence)
    assert f"target_id={ids['cacheManager']} lives in {TOOLS_XML}" in prompt
    assert "list that file in this subtask's planned_files" in prompt


def test_a_consistent_target_passes_and_is_recorded(indexed):
    repo, db, ids = indexed
    plan = _plan(_owning("s1", [TOOLS_XML, SERVICE_JAVA],
                         [{"target_id": ids["cacheManager"], "file": TOOLS_XML, "requirement_ids": ["REQ-1"]},
                          {"target_id": ids["findPetTypes"], "file": SERVICE_JAVA}]))
    errors, codes, evidence = check_plan_targets(plan, repo, lambda: db)
    assert (errors, codes) == ([], [])
    assert [e["verdict"] for e in evidence] == ["consistent", "consistent"]
    assert evidence[0]["requirement_ids"] == ["REQ-1"]


def test_an_unknown_or_stale_id_is_never_accepted(indexed):
    repo, db, ids = indexed
    plan = _plan(_owning("s1", [TOOLS_XML], [{"target_id": ids["cacheManager"] + "#nope", "file": TOOLS_XML}]))
    errors, codes, evidence = check_plan_targets(plan, repo, lambda: db)
    assert codes == [PLAN_TARGET_UNKNOWN] and evidence[0]["resolved_path"] is None
    prompt = pr.build_structured_plan_repair_prompt("goal", "{}", errors, codes, 1, validation_evidence=evidence)
    assert "is not a listed Code Intelligence id" in prompt
    # A target removed from the CURRENT bytes is unknown too (index != authority).
    with open(os.path.join(repo, TOOLS_XML), "w") as handle:
        handle.write(TOOLS.replace('id="cacheManager"', 'id="other"'))
    plan = _plan(_owning("s1", [TOOLS_XML], [{"target_id": ids["cacheManager"], "file": TOOLS_XML}]))
    assert check_plan_targets(plan, repo, lambda: db)[1] == [PLAN_TARGET_UNKNOWN]


def test_a_target_whose_stated_file_disagrees_with_the_symbol_is_inconsistent(indexed):
    repo, db, ids = indexed
    plan = _plan(_owning("s1", [SERVICE_JAVA], [{"target_id": ids["cacheManager"], "file": SERVICE_JAVA}]))
    errors, codes, _ = check_plan_targets(plan, repo, lambda: db)
    assert codes == [PLAN_TARGET_INCONSISTENT] and f"lives in {TOOLS_XML!r}" in errors[0]


def test_stating_no_target_is_valid_and_opens_no_index(tmp_path):
    plan = _plan(_owning("s1", [SERVICE_JAVA], []))
    def unreachable():
        raise AssertionError("no target stated: the index must not be consulted")
    assert check_plan_targets(plan, str(tmp_path), unreachable) == ([], [], [])
    # Without an index, a stated target cannot be checked: unknown, never trusted.
    plan = _plan(_owning("s1", [SERVICE_JAVA], [{"target_id": "java:x", "file": SERVICE_JAVA}]))
    assert check_plan_targets(plan, str(tmp_path), lambda: str(tmp_path / "absent.db"))[1] == [PLAN_TARGET_UNKNOWN]
    assert validate_mutation_targets(plan, None)[1] == [PLAN_TARGET_UNKNOWN]


def test_codes_are_classified_and_empty_targets_keep_the_plan_identity():
    assert pr.classify_planner_outcome([PLAN_TARGET_INCONSISTENT], valid=False) == rm.STRUCTURED_VALIDATION_FAILURE
    assert pr.classify_planner_outcome([PLAN_TARGET_UNKNOWN], valid=False) == rm.STRUCTURED_POLICY_REJECTED
    plan = _plan(_owning("s1", [SERVICE_JAVA], []))
    assert "mutation_targets" not in json.dumps(plan.model_dump(mode="json"))
    targeted = _plan(_owning("s1", [SERVICE_JAVA], [{"target_id": "java:x", "file": SERVICE_JAVA}]))
    again = EngineeringPlan.model_validate(targeted.model_dump(mode="json"))
    assert again.subtasks[0].mutation_targets == targeted.subtasks[0].mutation_targets


def test_coalescing_keeps_every_target_once():
    plan = _plan(_owning("s1", [SERVICE_JAVA], [{"target_id": "a", "file": SERVICE_JAVA}]),
                 _owning("s2", [SERVICE_JAVA], [{"target_id": "a", "file": SERVICE_JAVA},
                                                {"target_id": "b", "file": SERVICE_JAVA}]))
    merged, _ = coalesce_same_file_owners(plan)
    assert [t.target_id for t in merged.subtasks[0].mutation_targets] == ["a", "b"]


def test_the_enforce_controller_rejects_an_inconsistent_plan_and_accepts_the_correction(indexed, tmp_path):
    """Through the real WorkflowController enforce loop: the first plan
    contradicts its stated target, the repair request names the exact file,
    the corrected plan validates and runs."""
    from unittest.mock import AsyncMock, patch

    from test_workflow_controller import _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

    repo, db, ids = indexed
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                       capture_output=True)
    engine = _workflow_engine()
    engine.kernel.config.paths.memory = os.path.dirname(db)
    engine.planner.run = AsyncMock(return_value="structured plan")
    engine.engineering_triage.recompute_from_files = AsyncMock(side_effect=lambda route, **_: route)
    calls = []

    async def generation(*args, **kwargs):
        calls.append(sorted(kwargs.get("allowed_write_relpaths") or []))
        return {"status": "success", "quality_gates_passed": True, "files": [],
                "verification_results": [{"type": "tool", "tool_name": "compile", "description": "compiles",
                                          "passed": True}]}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    target = {"target_id": ids["cacheManager"], "file": TOOLS_XML}
    plans = [_plan(_owning("s1", [SERVICE_JAVA], [target])), _plan(_owning("s1", [SERVICE_JAVA, TOOLS_XML], [target]))]
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", side_effect=plans):
        asyncio.run(WorkflowController(engine).execute(
            "Cache the pet types in the cacheManager", repo, migration_mode="enforce"))
    assert engine.planner.run.await_count == 2
    repair_request = " ".join(str(a) for a in engine.planner.run.await_args_list[1].args)
    assert f"lives in {TOOLS_XML}" in repair_request
    decisions = [json.loads(line) for line in open(os.path.join(repo, ".kriya/control/decisions.jsonl"))]
    recorded = [[t["verdict"] for t in d["targets"]] for d in decisions if d.get("type") == "structured_plan_targets"]
    assert recorded == [[PLAN_TARGET_INCONSISTENT], ["consistent"]]
    assert [d["valid"] for d in decisions if d.get("type") == "structured_plan_validation"] == [False, True]
    assert sorted([SERVICE_JAVA, TOOLS_XML]) in calls
