"""Mechanical score of planning-only runs (no prose graded)."""
import glob
import json
import os

from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.plan_schema import EngineeringPlan

M = os.path.expanduser("~/kriya-bench-live/matrix")
REQ = {"java": ({"src/main/java/org/apache/commons/lang3/StringUtils.java"}, None),
       "springboot": ({"src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java",
                       "src/main/resources/application.properties"}, "src/main/resources/application.properties"),
       "python": ({"more_itertools/more.py"}, None),
       "sxml": ({"src/main/resources/spring/tools-config.xml",
                 "src/main/java/org/springframework/samples/petclinic/service/ClinicServiceImpl.java"},
                "src/main/resources/spring/tools-config.xml")}
GOALS = {t.split("\t")[0]: t.split("\t")[1].strip() for t in open(f"{M}/tasks.tsv")}
GOALTASK = {"java": "java-symbol-chop", "springboot": "spring-boot-pagesize", "python": "python-symbol-valuechain",
            "sxml": "spring-xml-pettypes-cache"}


def is_test(p):
    b = os.path.basename(p)
    return p.startswith(("src/test/", "tests/", "test/")) or b.startswith("test_") or b.endswith(("Test.java", "Tests.java"))


rows = []
for d in sorted(glob.glob("plan_runs/*_*")):
    label = os.path.basename(d)
    model, task = label.split("_", 1)
    if not os.path.exists(f"{d}/calls.json"):
        continue
    calls = json.load(open(f"{d}/calls.json"))
    plans = [f for f in glob.glob(f"{d}/2*.json")]
    diags = [f for f in glob.glob(f"{d}/2*.jsonl")]
    required, config = REQ[task]
    attempts = [json.loads(line) for f in diags for line in open(f)]
    row = {"run": label, "outcome": calls["outcome"],
           "admitted": all(c.get("ok") or "CONTEXT_BUDGET" not in c.get("error", "") for c in calls["planner_calls"])
           and bool(calls["planner_calls"]),
           "planner_calls": len(calls["planner_calls"]),
           "planner_seconds": round(sum(c["seconds"] for c in calls["planner_calls"]), 1),
           "prompt_tokens": [c.get("prompt_tokens") for c in calls["planner_calls"]],
           "first_response_valid": bool(attempts) and not attempts[0].get("reason_codes"),
           "attempt_codes": [a.get("reason_codes") for a in attempts]}
    if plans:
        doc = json.load(open(plans[0]))
        plan = EngineeringPlan.model_validate(doc["plan"])
        impl = [s for s in plan.subtasks if s.execution_role.value != "verification"]
        owned = {pf.path for s in impl for pf in s.planned_files}
        main = {p for p in owned if not is_test(p)}
        runtime_needed = goal_requires_runtime_behavior(GOALS[GOALTASK[task]])
        row.update(
            executable=True, repairs=doc.get("repair_attempts"),
            correct_targets=required <= main, config_retained=(config in main) if config else None,
            irrelevant_files=sorted(main - required),
            irrelevant_units=sum(1 for s in impl if {pf.path for pf in s.planned_files if not is_test(pf.path)} - required),
            unjustified_runtime_units=0 if runtime_needed else sum(
                1 for s in plan.subtasks if any(v.requires_application_runtime for v in s.verification)),
            deterministic_verifier=all(any(v.has_evidence_producer for v in s.verification) for s in impl),
            units=[(s.id, s.execution_role.value, sorted(pf.path.split("/")[-1] for pf in s.planned_files),
                    [v.tool_name or v.verifier_kind.value for v in s.verification]) for s in plan.subtasks])
    else:
        row.update(executable=False)
    for line in open(f"{d}/decisions.jsonl") if os.path.exists(f"{d}/decisions.jsonl") else []:
        e = json.loads(line)
        if e.get("run_id") and plans and e["run_id"] not in plans[0]:
            continue
        if e.get("type") in ("context.request_fit", "structured_plan_normalized"):
            row.setdefault("decisions", []).append({k: e.get(k) for k in ("type", "structural_evidence",
                                                                          "verification_scope", "model") if e.get(k)})
    rows.append(row)
for r in rows:
    print(json.dumps(r))
