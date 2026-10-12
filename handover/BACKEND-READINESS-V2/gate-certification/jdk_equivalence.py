#!/usr/bin/env python3
"""JDK 8 vs JDK 17 accept/reject equivalence for the S3_A and S4_A acceptance controls (owner decision 2026-10-12 item 4).

The frozen oracles run on the host JDK 17; Kriya's own gates for these two tasks run in JDK 8 containers. This script
re-runs the *test-running part* of each frozen ACCEPT script (hidden tests, then the upstream suite) for every control
arm inside the JDK 8 images Kriya itself uses, and compares the resulting accept/reject classification with the JDK 17
result already recorded in `gate-cert/results.jsonl`. No frozen oracle or task file is modified; the oracle scripts are
read, not run, and what is NOT re-executed under JDK 8 is stated per component in the output.

    python3 handover/BACKEND-READINESS-V2/gate-certification/jdk_equivalence.py [--tasks S3_A,S4_A]

Output: `gate-cert/jdk_equivalence.json`, `gate-cert/logs/jdk8-*.txt`. Resumable (an arm already in the JSON is skipped).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_controls as g  # noqa: E402 - sibling module, path added above

PREP = g.ROOT / "backend-reliability-closure-002" / "cohort-002" / "prep"
SCRIPTS = g.S_ROOT / "scripts"
MAVEN_IMAGE = "maven:3.9-eclipse-temurin-8"
GRADLE_IMAGE = "gradle:8-jdk8"
SKIP = ["-Drat.skip=true", "-Dcheckstyle.skip=true", "-Dspotbugs.skip=true", "-Dpmd.skip=true", "-Djacoco.skip=true",
        "-Dcyclonedx.skip=true", "-Djapicmp.skip=true", "-Danimal.sniffer.skip=true"]
RESULT = g.OUT / "jdk_equivalence.json"
UID_GID = f"{os.getuid()}:{os.getgid()}"


def docker(image: str, workdir: Path, command: list[str], log: Path, mounts: dict[str, str], env: dict[str, str]) -> int:
    args = ["docker", "run", "--rm", "--user", UID_GID, "-w", "/work", "-v", f"{workdir}:/work"]
    for host, guest in mounts.items():
        args += ["-v", f"{host}:{guest}"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    with log.open("a") as fh:
        fh.write(f"$ {' '.join(command)}\n")
        fh.flush()
        return subprocess.run(args + [image, *command], stdout=fh, stderr=subprocess.STDOUT, check=False, timeout=g.TIMEOUT_S).returncode


def s3a_commands(tree: Path, log: Path) -> dict:
    hidden = PREP / "hidden" / "S3_A"
    test_dir = tree / "src" / "test" / "java" / "org" / "apache" / "commons" / "text"
    for name in ("StrBuilderTest.java", "TextStringBuilderTest.java", "LastIndexOfEmptyGoalTest.java"):
        shutil.copy(hidden / name, test_dir / name)
    shutil.rmtree(tree / "target", ignore_errors=True)
    mounts = {str(Path.home() / ".m2"): "/m2"}
    env = {"HOME": "/tmp/home", "MAVEN_CONFIG": "/m2"}
    base = ["mvn", "-q", "-B", "-Dmaven.repo.local=/m2/repository"]
    hidden_rc = docker(MAVEN_IMAGE, tree, base + [
        "test", "-Dtest=StrBuilderTest#testLastIndexOf_String,TextStringBuilderTest#testLastIndexOf_String,LastIndexOfEmptyGoalTest",
        "-Dsurefire.failIfNoSpecifiedTests=true", *SKIP], log, mounts, env)
    # COMPAT: the frozen script dumps public signatures of target/classes with the host javap; the classes built just above
    # (by JDK 8) are what the dump reads, so the class files really come from JDK 8.
    dump = subprocess.run([str(SCRIPTS / "compat_check.sh"), "dump", str(tree / "target/classes"), str(tree / "candidate_signatures.txt")],
                          capture_output=True, check=False).returncode
    compat = subprocess.run([str(SCRIPTS / "compat_check.sh"), "diff", str(g.S_ROOT / "prep/S3_A.base_signatures.host.txt"),
                             str(tree / "candidate_signatures.txt")], capture_output=True, check=False).returncode
    shutil.rmtree(tree / "target", ignore_errors=True)
    upstream_rc = docker(MAVEN_IMAGE, tree, base + ["test", *SKIP], log, mounts, env)
    return {"HIDDEN_EXIT": hidden_rc, "COMPAT_EXIT": compat if dump == 0 else 99, "UPSTREAM_SUITE_EXIT": upstream_rc}


def s4a_commands(tree: Path, log: Path) -> dict:
    hidden = PREP / "hidden" / "S4_A"
    test_dir = tree / "json-path" / "src" / "test" / "java" / "com" / "jayway" / "jsonpath"
    for name in ("Issue_970.java", "Issue_973.java", "Issue_970_973_GoalExamples.java"):
        shutil.copy(hidden / name, test_dir / name)
    gradle_home = Path.home() / ".gradle"
    mounts = {str(gradle_home): "/gradle-home"}
    env = {"HOME": "/tmp/home", "GRADLE_USER_HOME": "/gradle-home"}
    common = ["./gradlew", "--no-daemon", "--console=plain"]
    hidden_rc = docker(GRADLE_IMAGE, tree, common + [
        ":json-path:cleanTest", ":json-path:test", "--tests", "com.jayway.jsonpath.Issue_970", "--tests", "com.jayway.jsonpath.Issue_973",
        "--tests", "com.jayway.jsonpath.Issue_970_973_GoalExamples"], log, mounts, env)
    upstream_rc = docker(GRADLE_IMAGE, tree, common + [":json-path:cleanTest", ":json-path:test"], log, mounts, env)
    return {"HIDDEN_EXIT": hidden_rc, "UPSTREAM_SUITE_EXIT": upstream_rc}


RUNNERS = {"S3_A": s3a_commands, "S4_A": s4a_commands}
MEANINGFUL = {"S3_A": ("HIDDEN_EXIT", "UPSTREAM_SUITE_EXIT", "COMPAT_EXIT"), "S4_A": ("HIDDEN_EXIT", "UPSTREAM_SUITE_EXIT")}


def jdk17_components(task: str, arm: str) -> dict | None:
    """The components of the recorded JDK 17 ACCEPT run (first repetition) from results.jsonl."""
    rows = [json.loads(line) for line in (g.OUT / "results.jsonl").read_text().splitlines()]
    for row in rows:
        if row["task"] == task and row["arm"] == arm and row["kind"] == "accept" and row["rep"] == 1:
            found = {}
            for line in row["key"]:
                match = re.match(r"^(HIDDEN_EXIT|UPSTREAM_SUITE_EXIT|COMPAT_EXIT|ACCEPT_EXIT)=(\d+)", line)
                if match:
                    found[match.group(1)] = int(match.group(2))
            found.setdefault("ACCEPT_EXIT", row["exit"])
            return found
    return None


def classify(components: dict, task: str) -> str:
    return "ACCEPT" if all(components.get(name) == 0 for name in MEANINGFUL[task]) else "REJECT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="S3_A,S4_A")
    args = parser.parse_args()
    done = json.loads(RESULT.read_text()) if RESULT.exists() else []
    seen = {(row["task"], row["arm"]) for row in done}
    (g.OUT / "logs").mkdir(parents=True, exist_ok=True)
    for task in args.tasks.split(","):
        for arm in g.arms_for(task):
            if (task, arm) in seen:
                continue
            log = g.OUT / "logs" / f"jdk8-{task}-{arm}.txt"
            log.write_text("")
            with tempfile.TemporaryDirectory(prefix=f"jdk8-{task}-{arm}-", dir=g.OUT) as tmp:
                tree = Path(tmp) / "tree"
                g.build_tree(task, arm, tree)
                jdk8 = RUNNERS[task](tree, log)
            jdk17 = jdk17_components(task, arm) or {}
            row = {"task": task, "arm": arm, "jdk8": jdk8, "jdk17": jdk17, "jdk8_class": classify(jdk8, task),
                   "jdk17_class": classify(jdk17, task), "not_reexecuted_under_jdk8": ["tamper_gate (host file-hash comparison)"]}
            row["equivalent"] = row["jdk8_class"] == row["jdk17_class"]
            done.append(row)
            RESULT.write_text(json.dumps(done, indent=1))
            print(f"{task:5} {arm:34} jdk8={row['jdk8_class']} {jdk8} jdk17={row['jdk17_class']} {jdk17} equivalent={row['equivalent']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
