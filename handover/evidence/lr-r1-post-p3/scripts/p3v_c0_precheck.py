"""LR-R1 post-P3 validation: C0 preconditions for each Cohort A task (harness; no model is called).

For the named oracle of REQ-1, with Kriya's own FS-1C0 code (kriya/workflow/named_test_oracle.py) at the exact base:
- the named test exists at the base revision;
- run on an export of the base under the task's production config, it is a COMPLETE structured report whose every
  case PASSED (C0 is only for a known-good oracle), and its exact case inventory is captured;
- the goal's change target (the mutation-scope authorized path) is not on the oracle's trust surface, so the
  candidate need not change it.

usage: venv-p3c/bin/python scripts/p3v_c0_precheck.py <workspace>...
"""
import json
import os
import subprocess
import sys
import tempfile

D = os.path.expanduser("~/kriya-m1-live")


def check(name: str) -> dict:
    from kriya.config.config import load_config
    from kriya.tools import test_execution
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.file_resolution import is_runnable_test_file
    from kriya.workflow.named_test_oracle import BaseTree, OracleSurface, baseline_inventory, export_base
    from kriya.workflow.requirements import derive_requirements, mutation_path_roles, named_existing_tests

    workspace = os.path.join(D, "ws", name)
    base = open(os.path.join(D, "ws", f"{name}.base")).read().strip()
    os.chdir(workspace)  # the workspace is the config's run context (as in p3v_admission.py)
    cfg = load_config(os.path.join(D, "config", f"{name}.yaml"))
    goal = open(os.path.join(D, "goals", f"{name}.txt")).read()
    tracked = subprocess.run(["git", "ls-tree", "-r", "--name-only", base], cwd=workspace, capture_output=True,
                             text=True, check=True).stdout.split()
    requirements = derive_requirements(goal)
    named = named_existing_tests(requirements.requirements[0].text, [p for p in tracked if is_runnable_test_file(p)])
    targets = mutation_path_roles(requirements, tracked)["authorized"]
    tree = BaseTree(workspace, base)
    surface = OracleSurface(tree, workspace, named)
    with tempfile.TemporaryDirectory(prefix="p3v-c0-pre-") as export:
        export_base(tree, export)
        validator = PolymorphicValidator(export, original_workspace_path=workspace, autonomy_cfg=cfg.autonomy)
        runner = validator._test_runner()
        result = validator.run_tests(target_test=list(named))
    report = test_execution.report_from_result(result)
    inventory = baseline_inventory(result, runner)
    statuses = sorted({case.status for case in report.cases}) if report else []
    record = {
        "workspace": name, "base": base, "named_oracle": named, "named_at_base": all(p in tree.modes for p in named),
        "runner": runner, "report_complete": bool(report and report.complete), "runner_success": bool(result.get("success")),
        "case_count": len(inventory or []), "case_statuses": statuses, "inventory": inventory,
        "change_targets": targets, "targets_on_surface": sorted(set(targets) & set(surface.entries)),
        "surface_entries": len(surface.entries),
    }
    record["c0_precondition"] = (record["named_at_base"] and len(named) == 1 and record["report_complete"]
                                 and record["runner_success"] and statuses == ["passed"]
                                 and bool(targets) and not record["targets_on_surface"])
    os.makedirs(os.path.join(D, "evidence", "p3v", "c0-precheck"), exist_ok=True)
    with open(os.path.join(D, "evidence", "p3v", "c0-precheck", f"{name}.json"), "w") as handle:
        json.dump(record, handle, indent=1)
    subprocess.run(["git", "status", "--short"], cwd=workspace, check=True)
    return {k: v for k, v in record.items() if k != "inventory"}


if __name__ == "__main__":
    for workspace_name in sys.argv[1:]:
        print(json.dumps(check(workspace_name)), flush=True)
