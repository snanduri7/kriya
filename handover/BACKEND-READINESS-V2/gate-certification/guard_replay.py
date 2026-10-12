#!/usr/bin/env python3
"""Run the exception-masking guard over every Java control tree (each arm's changed .java files vs the import commit)
and persist the result to `guard_replay.json` under gate_controls.OUT. Needs the repo on PYTHONPATH.

    PYTHONPATH=$PWD .venv/bin/python handover/BACKEND-READINESS-V2/gate-certification/guard_replay.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_controls as g  # noqa: E402 - sibling module, path added above

from kriya.workflow.exception_masking import find_unsafe_exception_masking  # noqa: E402

JAVA_TASKS = ("T2", "T3", "T6", "S3_A", "S4_A", "S4_B")


def replay_arm(task: str, arm: str) -> dict:
    with tempfile.TemporaryDirectory(dir=g.OUT) as work:
        tree = Path(work) / "tree"
        g.build_tree(task, arm, tree)
        changed = [n for n in g.git(tree, "diff", "--name-only").stdout.split() if n.endswith(".java")]
        findings = []
        for name in changed:
            baseline = g.git(tree, "show", f"HEAD:{name}").stdout
            found = find_unsafe_exception_masking(name, (tree / name).read_text(), baseline)
            if found:
                findings.append(found)
    return {"task": task, "arm": arm, "java_files_changed": len(changed), "findings": findings}


def main() -> int:
    g.OUT.mkdir(parents=True, exist_ok=True)
    rows = [replay_arm(t, a) for t in JAVA_TASKS for a in g.arms_for(t)]
    fired = [r for r in rows if r["findings"]]
    print(f"arms={len(rows)} with_java_changes={sum(bool(r['java_files_changed']) for r in rows)} fired={[(r['task'], r['arm']) for r in fired]}")
    (g.OUT / "guard_replay.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
