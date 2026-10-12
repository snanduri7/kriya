#!/usr/bin/env python3
"""JVM-MODULE-OWNERSHIP-001 mandatory replay-before-enforce (owner decision 2026-10-12, B5).

Runs the PRODUCTION path - ``derive_java_example_artifact`` (module-aware placement) -> ``run_java_acceptance`` (module-qualified
task, trust surface, FS-1A report binding) -> ``judge_acceptance`` - against recorded subjects of the frozen task whose goal
examples the matcher grammar compiles, with the matcher form enabled FOR THIS PROCESS ONLY (``goal_example_forms.ENFORCED_FORMS``
is patched here; the product constant is changed only after this replay is clean). No model call; the hidden oracle is never read
(the examples come from the goal text). A subject is a control arm (``gate_controls``) or the staged files of a sealed run.

Pass criteria (owner B5): ZERO false rejection of correct subjects (every enforced example must PASS), every recorded wrong
subject whose defect a goal example expresses must be VIOLATED, and every subject must give the same verdict on every repeat.

    PYTHONPATH=$PWD .venv/bin/python handover/BACKEND-READINESS-V2/gate-certification/module_replay.py [--repeat 2] [--subjects a,b]

Output: ``~/kriya-m1-live/backend-readiness-v2/gate-cert/module_replay.json`` (resumable per (subject, run)).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_controls as g  # noqa: E402 - sibling module, path added above
import shadow_replay as sr  # noqa: E402

from kriya.config.config import AppConfig  # noqa: E402
from kriya.tools.validate import PolymorphicValidator  # noqa: E402
from kriya.workflow import acceptance_jvm as jvm  # noqa: E402
from kriya.workflow import acceptance_oracle as ao  # noqa: E402
from kriya.workflow import example_oracle_java as ej  # noqa: E402
from kriya.workflow import goal_example_forms as gef  # noqa: E402
from kriya.workflow.requirements import derive_requirements  # noqa: E402

TASK = "T3"
RESULT = g.OUT / "module_replay.json"
JAVA_HOME = "/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home"
# (subject, role, source): role "correct" must PASS, "wrong" must be VIOLATED, "inert" is recorded only (no defect a goal example expresses)
ARMS = [("base", "wrong"), ("good", "correct"), ("unrelated", "correct"), ("near_miss_unicode_only", "wrong"),
        ("near_miss_trim_only", "wrong")]
RECORDED = [(label, role, run) for task, label, run, role in sr.RECORDED if task == TASK]
RECORDED.append(("recorded-v6f-isWhitespace", "wrong", "20261012T151146-d6a90f7c"))


def subjects(selected: set[str]):
    for arm, role in ARMS:
        if not selected or arm in selected:
            yield arm, role, None, arm
    for label, role, run in RECORDED:
        if not selected or label in selected:
            yield label, role, sr.recorded_files(run), None


WORK = g.OUT / "mreplay-work"    # one fixed tree path: the contained runs' dependency cache is keyed by it
STATE = g.OUT / "mreplay-state"  # persistent across subjects (dependency cache, sanitized git metadata export)


def production_autonomy():
    """The production profile's containment: OCI, required, network denied outside the bounded acquisition phase, the sanitized
    Git metadata export on (OD-1) - a replay on the host would measure the harness (a build that runs ``git describe`` at
    configuration fails without that export), not the product."""
    return AppConfig().autonomy.model_copy(update={
        "containment_backend": "oci", "contained_execution_required": True, "shell_network": "denied",
        "git_metadata_export": True, "generation_time_budget_seconds": 3600})


def replay(subject: str, role: str, files, arm, repeat: int) -> dict:
    gef.ENFORCED_FORMS = frozenset(gef.ENFORCED_FORMS | {gef.FORM_MATCHER_PASS})  # replay-only: the product constant is unchanged
    os.environ["JAVA_HOME"] = JAVA_HOME
    goal = (g.TASK_DIR[TASK] / "goal.txt").read_text()
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    if True:
        tmp_path = WORK
        STATE.mkdir(parents=True, exist_ok=True)
        os.environ["KRIYA_STATE_DIR"] = str(STATE)
        tree = tmp_path / "tree"
        base_revision = g.build_tree(TASK, arm or "base", tree)
        for rel, data in (files or {}).items():
            (tree / rel).write_bytes(data)
        changed = [line[3:] for line in subprocess.run(["git", "status", "--porcelain"], cwd=tree, capture_output=True,
                                                       text=True, check=True).stdout.splitlines()]
        started = time.time()
        artifact, report = ej.derive_java_example_artifact(
            goal, derive_requirements(goal), state_root=str(STATE), candidate_root=str(tree))
        row = {"subject": subject, "role": role, "run": repeat, "changed": changed,
               "graduated": [e["text"] for e in report.get("graduated_examples", [])],
               "shadow_statuses": sorted({str(x.get("status")) for x in report.get("shadow_examples", [])}),
               "module": report.get("module"), "artifact": bool(artifact)}
        if artifact is None:
            return {**row, "verdict": "NO_ARTIFACT", "detail": report.get("refusal")}
        row["injection_path"] = artifact.java["injection_path"]
        autonomy = production_autonomy()

        def factory(root: str) -> PolymorphicValidator:
            return PolymorphicValidator(root, original_workspace_path=str(tree), autonomy_cfg=autonomy)

        run = jvm.run_java_acceptance(artifact, str(tree), candidate_paths=changed, base_revision=base_revision,
                                      validator_factory=factory)
        judgments = ao.judge_acceptance(artifact, run)
        row["judgments"] = {rid: {"code": j.code, "passed": j.passed, "violated": j.violated, "reason": j.reason[:300]}
                            for rid, j in sorted(judgments.items())}
        row["runner_refusal"] = getattr(run.refusal, "reason_code", None)
        row["seconds"] = round(time.time() - started, 1)
        if all(j.passed for j in judgments.values()):
            row["verdict"] = "PASS"
        elif any(j.violated for j in judgments.values()):
            row["verdict"] = "VIOLATED"
        else:
            row["verdict"] = "INDETERMINATE"
        return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--subjects", default="")
    args = parser.parse_args()
    g.OUT.mkdir(parents=True, exist_ok=True)
    done = json.loads(RESULT.read_text()) if RESULT.exists() else []
    seen = {(r["subject"], r["run"]) for r in done}
    selected = {s for s in args.subjects.split(",") if s}
    for subject, role, files, arm in subjects(selected):
        for repeat in range(1, args.repeat + 1):
            if (subject, repeat) in seen:
                continue
            row = replay(subject, role, files, arm, repeat)
            done.append(row)
            RESULT.write_text(json.dumps(done, indent=1))
            print(f"{subject:34} run{repeat} role={role:8} verdict={row['verdict']:13} module={row.get('module')} "
                  f"{row.get('seconds')}s", flush=True)
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
