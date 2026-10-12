#!/usr/bin/env python3
"""Mandatory SHADOW REPLAY of the goal-example grammar forms (owner decision 2026-10-12, Stage C change 1).

For each control arm and recorded candidate of a task whose goal contains compiled example forms, build the tree, compile the
goal's examples with `kriya.workflow.goal_example_forms` (no model, no oracle file read), render them into a JUnit 5 probe
class, inject it into the tree's test source set, run it with the project's own build tool and record MATCH / MISMATCH per
example. Hidden oracle files are never read or copied; the examples come only from the goal text.

Graduation of a form needs, over this replay: ZERO MISMATCH on correct candidates (known-good, unrelated change) for every
example of the form, at least one MISMATCH on a wrong candidate where one exists, and identical results on a second run.

    PYTHONPATH=$PWD .venv/bin/python handover/BACKEND-READINESS-V2/gate-certification/shadow_replay.py [--tasks S3_A,T3,S4_A] [--repeat 2]

Output: `gate-cert/shadow_replay.json` (resumable per (task, subject, run)), `gate-cert/logs/shadow-*.txt`.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_controls as g  # noqa: E402 - sibling module, path added above

from kriya.workflow.goal_example_forms import classify_goal_examples, render_probe_class  # noqa: E402

JAVA_HOME = "/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home"
ATTEMPT_EVIDENCE = g.T_ROOT / "state" / "attempt-evidence"
PROBE_PACKAGE, PROBE_CLASS = "kriya.examples", "KriyaShadowExamplesTest"
RESULT = g.OUT / "shadow_replay.json"
SKIP = ["-Drat.skip=true", "-Dcheckstyle.skip=true", "-Dspotbugs.skip=true", "-Dpmd.skip=true", "-Djacoco.skip=true",
        "-Dcyclonedx.skip=true", "-Djapicmp.skip=true", "-Danimal.sniffer.skip=true"]
# role of an arm for graduation: correct candidates must never be rejected; wrong ones should be.
ROLE = {"good": "correct", "unrelated": "correct", "alt_good": "correct", "base": "wrong", "near_miss_strbuilder_only": "wrong",
        "near_miss_textstringbuilder_only": "wrong", "near_miss_unicode_only": "wrong", "near_miss_trim_only": "wrong",
        "masking_throwable_final12": "oracle_blind"}
# recorded Kriya candidates (the staged file bytes of a sealed run): (task, label, run id, role)
RECORDED = [
    ("T3", "recorded-v2-regex-GENUINE", "20261011T231355-0b874675", "correct"),  # Kriya SUCCESS, external oracle accept 0
    ("T3", "recorded-v2f-isWhitespace", "20261012T003315-e4b13153", "wrong"),
    ("S3_A", "recorded-v3a-strbuilder-incomplete", "20261012T071018-527b5fc6", "wrong"),
]


def read_blob(run: str, digest: str) -> bytes:
    h = digest.split(":")[-1]
    folder = ATTEMPT_EVIDENCE / run / "blobs" / h[:2]
    exact = folder / h
    path = exact if exact.exists() else next(iter(sorted(folder.glob(h[:24] + "*"))))  # blob names may be digest prefixes
    raw = path.read_bytes()
    for fn in (gzip.decompress, zlib.decompress):
        try:
            return fn(raw)
        except Exception:  # not that codec
            continue
    return raw


def recorded_files(run: str) -> dict[str, bytes]:
    """path -> the last STAGED candidate bytes of the sealed run."""
    files: dict[str, bytes] = {}
    for line in (ATTEMPT_EVIDENCE / run / "records.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["kind"] == "candidate.change" and record["payload"].get("decision") == "STAGED":
            files[record["payload"]["path"]] = read_blob(run, record["blobs"]["after"])
    return files


def goal_text(task: str) -> str:
    return (g.TASK_DIR[task] / "goal.txt").read_text()


def test_dir_for(tree: Path, qualified_type_path: str) -> Path:
    """<module>/src/test/java next to the module whose src/main/java holds the type's unit."""
    return tree / qualified_type_path.split("src/main/java")[0] / "src" / "test" / "java"


def run_build(task: str, tree: Path, module_prefix: str, log: Path) -> int:
    env = dict(os.environ, JAVA_HOME=JAVA_HOME)
    with log.open("w") as fh:
        if task == "S3_A":
            cmd = ["mvn", "-q", "-o", "-B", "test", f"-Dtest={PROBE_CLASS}", "-Dsurefire.failIfNoSpecifiedTests=true", *SKIP]
        else:
            project = ":" + module_prefix.strip("/").split("/")[0]
            cmd = ["./gradlew", "-q", "--offline", f"{project}:test", "--tests", f"{PROBE_PACKAGE}.*"]
        return subprocess.run(cmd, cwd=tree, env=env, stdout=fh, stderr=subprocess.STDOUT, check=False, timeout=g.TIMEOUT_S).returncode


def read_results(tree: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    for xml in tree.rglob(f"TEST-{PROBE_PACKAGE}.{PROBE_CLASS}.xml"):
        for case in ET.parse(xml).getroot().iter("testcase"):
            number = int(re.sub(r"\D", "", case.get("name", "0")) or 0)
            bad = case.find("failure") is not None or case.find("error") is not None
            out[number] = "MISMATCH" if bad else "MATCH"
    return out


def replay(task: str, subject: str, role: str, files: dict[str, bytes] | None, arm: str | None) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"shadow-{task}-", dir=g.OUT) as tmp:
        tree = Path(tmp) / "tree"
        g.build_tree(task, arm or "base", tree)
        for rel, data in (files or {}).items():
            (tree / rel).write_bytes(data)
        classification = classify_goal_examples(goal_text(task), str(tree))
        compiled = classification.compiled()
        record = {"task": task, "subject": subject, "role": role, "counts": classification.counts(),
                  "examples": [{"n": i, "form": e.form, "line": e.line, "text": e.text} for i, e in enumerate(compiled, 1)]}
        source = render_probe_class(compiled, PROBE_PACKAGE, PROBE_CLASS)
        if source is None:
            return {**record, "results": {}, "build_exit": None}
        prefix = ""
        for current, dirs, names in os.walk(tree):
            dirs[:] = [d for d in dirs if d not in (".git", ".kriya", "build", "target")]
            if f"{compiled[0].qualified_type.rsplit('.', 1)[-1]}.java" in names and "src/main/java" in current:
                prefix = os.path.relpath(current, tree).split("src/main/java")[0]
                break
        probe = tree / prefix / "src" / "test" / "java" / "kriya" / "examples" / f"{PROBE_CLASS}.java"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(source)
        log = g.OUT / "logs" / f"shadow-{task}-{subject}.txt"
        rc = run_build(task, tree, prefix, log)
        return {**record, "build_exit": rc, "results": {str(k): v for k, v in sorted(read_results(tree).items())}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="S3_A,T3,S4_A")
    parser.add_argument("--repeat", type=int, default=2)
    args = parser.parse_args()
    (g.OUT / "logs").mkdir(parents=True, exist_ok=True)
    done = json.loads(RESULT.read_text()) if RESULT.exists() else []
    seen = {(r["task"], r["subject"], r["run"]) for r in done}
    for task in args.tasks.split(","):
        subjects = [(arm, ROLE.get(arm, "wrong"), None, arm) for arm in g.arms_for(task)]
        subjects += [(label, role, recorded_files(run), None) for t, label, run, role in RECORDED if t == task]
        for subject, role, files, arm in subjects:
            for run in range(1, args.repeat + 1):
                if (task, subject, run) in seen:
                    continue
                row = {**replay(task, subject, role, files, arm), "run": run}
                done.append(row)
                RESULT.write_text(json.dumps(done, indent=1))
                tally = {v: list(row["results"].values()).count(v) for v in ("MATCH", "MISMATCH")}
                print(f"{task:5} {subject:36} run{run} {role:12} build_exit={row['build_exit']} {tally}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
