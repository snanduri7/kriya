#!/usr/bin/env python3
"""Slot 3 offline prerequisites A/B/C (no model call): run Kriya's real JVM acceptance machinery from a REAL nested subtask
worktree - the same topology enforce creates (real workspace R -> plan sandbox P -> unit worktree U) - and read the structured
failure of the generated example class.

A  the example class executes against the UNIT's bytes (not P, not R): fix only in U -> PASSED; fix only in P -> VIOLATED;
   cleanup leaves U and P byte-identical;
B  the class source/placement is deterministic, carries the repository license header, is visible to the build in the nested
   layout (the run in A is the proof) and uses no task-specific path;
C  the failure of a case carries parseable EXPECTED / OBSERVED / TARGET / SOURCE LOCATION / KIND.

    PYTHONPATH=$PWD .venv/bin/python handover/BACKEND-READINESS-V2/gate-certification/slot3_prereq.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_controls as g  # noqa: E402 - sibling module, path added above
import yaml  # noqa: E402

from kriya.config.config import AutonomyConfig  # noqa: E402
from kriya.control.run_coordinator import authorize_candidate_workspace, begin_mutating_run  # noqa: E402
from kriya.tools.validate import PolymorphicValidator  # noqa: E402
from kriya.workflow import acceptance_jvm as jvm  # noqa: E402
from kriya.workflow import acceptance_oracle as ao  # noqa: E402
from kriya.workflow import example_oracle_java as ej  # noqa: E402
from kriya.workflow.requirements import derive_requirements  # noqa: E402
from kriya.workflow.worktree import create_git_worktree  # noqa: E402

TASK = "S3_A"


def sh(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def tree_digest(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith((".git/", ".git")) and "/.git/" not in rel and not rel.startswith("target"):
            out[rel] = subprocess.run(["shasum", "-a", "256", str(p)], capture_output=True, text=True).stdout.split()[0]
    return out


def fixed_files(arm: str, scratch: Path) -> dict[str, bytes]:
    """The files an arm changes vs the import commit (from a throwaway clone), as bytes."""
    tree = scratch / f"arm-{arm}"
    g.build_tree(TASK, arm, tree)
    names = sh("git", "diff", "--name-only", cwd=tree).split()
    return {n: (tree / n).read_bytes() for n in names}


def judge(art, unit: Path, plan: Path, cfg, changed: list[str]):
    os.environ["KRIYA_STATE_DIR"] = str(unit.parent / "state")
    base = sh("git", "rev-parse", "HEAD", cwd=unit)
    run = jvm.run_java_acceptance(
        art, str(unit), candidate_paths=changed, base_revision=base,
        validator_factory=lambda root: PolymorphicValidator(root, original_workspace_path=str(plan), autonomy_cfg=cfg))
    return run, ao.judge_acceptance(art, run)


def main() -> int:
    raw = yaml.safe_load((g.TASK_DIR[TASK] / "config.yaml").read_text())
    cfg = AutonomyConfig(**raw["autonomy"])
    goal = (g.TASK_DIR[TASK] / "goal.txt").read_text()
    results = {}
    with tempfile.TemporaryDirectory(prefix="slot3-", dir=g.OUT) as tmp:
        scratch = Path(tmp)
        good, _base_files = fixed_files("good", scratch), {}
        # R: the real workspace (a git repo at the import commit)
        real = scratch / "R"
        g.build_tree(TASK, "base", real)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "noop", "--allow-empty", cwd=real)
        reqs = derive_requirements(goal)
        art, report = ej.derive_java_example_artifact(goal, reqs, state_root=str(scratch / "state"), candidate_root=str(real))
        results["B_class"] = {"cases": len(art.cases), "injection_path": art.java["injection_path"],
                              "license_header_sha256": report.get("license_header_sha256"),
                              "graduated": len(report["graduated_examples"])}
        source = Path(art.stored_path).read_text()
        results["B_class"]["starts_with_license"] = source.lstrip().startswith("/*") and "Licensed" in source.split("package")[0]
        results["B_class"]["task_specific_tokens"] = [t for t in ("S3_A", "TextStringBuilder", "StrBuilder", "commons") if t.lower() in
                                                      ej.__dict__.get("__doc__", "").lower()]  # production module docstring only
        with begin_mutating_run(str(real)):
            plan = Path(create_git_worktree(str(real)))
            authorize_candidate_workspace(str(plan))
            unit = Path(create_git_worktree(str(plan)))
            results["topology"] = {"plan": str(plan.relative_to(real)), "unit": str(unit.relative_to(real)),
                                   "distinct": len({str(real), str(plan), str(unit)}) == 3}

            def reset(path: Path):
                sh("git", "checkout", "-q", "--", ".", cwd=path)
                sh("git", "clean", "-fdq", cwd=path)

            def put(path: Path, files: dict[str, bytes]):
                for rel, data in files.items():
                    (path / rel).write_bytes(data)

            scenarios = {
                "good_in_unit": (good, {}), "base_everywhere": ({}, {}),
                "fix_only_in_plan_sandbox": ({}, good), "fix_in_both": (good, good),
            }
            for name, (in_unit, in_plan) in scenarios.items():
                reset(unit)
                reset(plan)
                put(unit, in_unit)
                put(plan, in_plan)
                before_u, before_p = tree_digest(unit), tree_digest(plan)
                run, verdicts = judge(art, unit, plan, cfg.autonomy if hasattr(cfg, "autonomy") else cfg, list(in_unit))
                after_u, after_p = tree_digest(unit), tree_digest(plan)
                results[name] = {"refusal": str(run.refusal) if getattr(run, "refusal", None) else None,
                                 "verdicts": {rid: j.code for rid, j in verdicts.items()},
                                 "unit_unchanged_by_run": before_u == after_u, "plan_unchanged_by_run": before_p == after_p,
                                 "export_dirs_left": [p.name for p in (scratch / "state").glob("**/workspace")][:3]}
                if name == "base_everywhere":
                    details = run.case_details or {}
                    c = {}
                    for identity, entries in sorted(details.items()):
                        bad = [e for e in entries if e["status"] != "passed"]
                        if bad:
                            first = bad[0]["trace"].splitlines()[0] if bad[0]["trace"] else ""
                            m = re.search(r"expected: <(.*?)> but was: <(.*?)>", first)
                            c[identity] = {"kind": bad[0]["status"], "type": bad[0]["type"], "first_line": first[:160],
                                           "expected": m.group(1) if m else None, "observed": m.group(2) if m else None}
                    results["C_failures"] = c
    print(json.dumps(results, indent=1))
    Path(g.OUT / "slot3_prereq.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
