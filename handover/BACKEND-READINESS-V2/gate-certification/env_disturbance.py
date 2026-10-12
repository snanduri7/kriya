#!/usr/bin/env python3
"""Environment-disturbance control (G): run a task's frozen ACCEPT oracle on a KNOWN-GOOD tree with one dependency
cache or index deliberately unavailable, and record whether the gate reports an environment problem (exit 2) or a
plain product rejection (exit 1). Writes `env_disturbance.json` under gate_controls.OUT. No model call.

    env_disturbance.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_controls as g  # noqa: E402 - sibling module, path added above

MARKERS = ("INSTALL_FAILED", "ORACLE_ENVIRONMENT", "TAMPERED=[]", "BUILD FAILED", "Could not resolve", "Cannot resolve")
CASES = (
    ("T1", "pip_index_unreachable", {"PIP_INDEX_URL": "http://127.0.0.1:9/simple", "PIP_DEFAULT_TIMEOUT": "2", "PIP_RETRIES": "0"}),
    ("T2", "empty_maven_repo", "maven"),
    ("S3_A", "empty_maven_repo", "maven"),
    ("T3", "empty_GRADLE_USER_HOME", "gradle"),
    ("S4_A", "empty_GRADLE_USER_HOME", "gradle"),
)


def run_case(task: str, label: str, spec: object) -> dict:
    with tempfile.TemporaryDirectory(dir=g.OUT) as work, tempfile.TemporaryDirectory() as cache:
        work_dir = Path(work)
        tree, scr, log = work_dir / "tree", work_dir / "scr", work_dir / "log.txt"
        g.build_tree(task, "good", tree)
        subprocess.run(["rsync", "-a", "--exclude", ".kriya", f"{tree}/", f"{scr}/"], check=True)
        env = dict(os.environ)
        if isinstance(spec, dict):
            env.update(spec)
        elif spec == "maven":
            env["MAVEN_OPTS"] = f"-Dmaven.repo.local={cache}"
        else:
            env["GRADLE_USER_HOME"] = cache
        started = time.time()
        with log.open("w") as fh:
            code = subprocess.run([str(g.TASK_DIR[task] / "oracle" / "accept.sh"), str(tree), str(scr)], stdout=fh,
                                  stderr=subprocess.STDOUT, env=env, timeout=g.TIMEOUT_S, check=False).returncode
        text = log.read_text(errors="replace")
    return {"task": task, "disturbance": label, "exit": code, "seconds": round(time.time() - started, 1),
            "markers": [m for m in MARKERS if m in text], "tail": text.strip().splitlines()[-4:]}


def main() -> int:
    g.OUT.mkdir(parents=True, exist_ok=True)
    results = [run_case(*case) for case in CASES]
    for r in results:
        print(f"{r['task']:5}{r['disturbance']:26} accept_exit={r['exit']} {r['seconds']}s markers={r['markers']}")
    (g.OUT / "env_disturbance.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
