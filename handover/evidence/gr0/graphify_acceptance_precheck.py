"""GR-R0 Part 4 precheck (harness; no model): binds the PROSPECTIVE Graphify acceptance suite to the frozen
goal's requirements and runs it once through Kriya's own B2-a runner on a candidate tree (default: the frozen
base, where cases 1, 2 and 5 must FAIL and controls 3 and 4 PASS - non-vacuous), then prints the judgment.

usage: PYTHONPATH=<checkout>:<checkout>/tests python graphify_acceptance_precheck.py <goal.txt> <suite.py>
       <candidate_root> <state_dir> <out.json> [<requirements contract.json>]

GR-R1: with a requirement contract the suite binds to the contract's closed set, as `generate --requirements`."""
import json
import sys
from pathlib import Path

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.requirements import behavior_strength, derive_requirements


def main(goal_file, suite, root, state, out, contract_file=None):
    goal = Path(goal_file).read_text(encoding="utf-8")
    if contract_file:
        from kriya.workflow.requirement_contract import load_requirement_contract

        requirements = load_requirement_contract(contract_file, goal, state_root=state, workspace=root).requirement_set
    else:
        requirements = derive_requirements(goal)
    artifact = ao.load_acceptance(suite, requirements, state)
    run = ao.run_acceptance(artifact, root, candidate_paths=[],
                            validator_factory=lambda: PolymorphicValidator(root, autonomy_cfg=AutonomyConfig()))
    judged = ao.judge_acceptance(artifact, run)
    report = {
        "requirements_derived": len(requirements.requirements),
        "requirement_set_digest": requirements.digest, "contract_digest": requirements.contract_digest,
        "covered": list(artifact.requirement_ids),
        "strength": {rid: behavior_strength(requirements.get(rid).text, regression_covered=False)[0]
                     for rid in artifact.requirement_ids},
        "acceptance_digest": artifact.digest, "cases": [c.identity for c in artifact.cases],
        "refusal": str(run.refusal) if run.refusal else None,
        "integrity_problem": run.integrity_problem,
        "case_results": (run.report.to_dict() if run.report is not None else None),
        "judgment": {rid: (j if isinstance(j, (dict, str)) else repr(j)) for rid, j in judged.items()},
        "output_tail": (run.result or {}).get("output", "")[-3000:],
    }
    Path(out).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "output_tail"}, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main(*sys.argv[1:7])
