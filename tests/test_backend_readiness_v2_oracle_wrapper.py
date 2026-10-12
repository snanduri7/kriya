"""BACKEND-READINESS-V2 Stage D2: the evaluation wrapper classifies every oracle exit before it can be read as a verdict.
The wrapper runs against FAKE oracles (ORACLE_DIR) in a temp tree: a candidate rejection stays REJECT, broken infrastructure becomes
ENVIRONMENT_UNAVAILABLE, unclassifiable output becomes INDETERMINATE, success stays PASS - and the last two make the run INVALID."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "handover" / "BACKEND-READINESS-V2" / "gate-certification"
WRAPPER = ROOT / "wrappers" / "evaluate_v3.sh"
PREFLIGHT = ROOT / "oracle_preflight.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "oracle_preflight"

CASES = {
    "pass": ("echo ACCEPT_EXIT=0; exit 0", "PASS", 0),
    "reject_tests": ("echo '2 failed in 0.02s'; echo ACCEPT_EXIT=1; exit 1", "REJECT", 0),
    "reject_compile": ("echo 'COMPILATION ERROR :'; echo 'Foo.java:[3,1] error: cannot find symbol'; exit 1", "REJECT", 0),
    "environment": ("cat " + str(FIXTURES / "env-T2-empty_maven_repo.txt") + "; exit 1", "ENVIRONMENT_UNAVAILABLE", 1),
    "environment_pip": ("echo INSTALL_FAILED; exit 2", "ENVIRONMENT_UNAVAILABLE", 1),
    "indeterminate": ("echo 'something exploded'; exit 1", "INDETERMINATE", 1),
}


def _run(tmp_path, body, kind="accept", preflight=None):
    oracles = tmp_path / "oracle"
    oracles.mkdir()
    for name in ("accept", "regress"):
        (oracles / f"{name}.sh").write_text(f"#!/bin/sh\n{body if name == kind else 'echo ACCEPT_EXIT=0; exit 0'}\n")
        (oracles / f"{name}.sh").chmod(0o755)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("x\n")
    out = tmp_path / "out"
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(tmp_path), "ORACLE_DIR": str(oracles),
           "OUT_DIR": str(out), "SCRATCH_DIR": str(tmp_path / "scratch")}
    if preflight is not None:
        env["PREFLIGHT_SCRIPT"] = str(preflight)
    done = subprocess.run(["/bin/zsh", str(WRAPPER), "c2", "TX", str(tree), "tag", "-lbl"], env=env, capture_output=True, text=True,
                          check=False)
    return done, json.loads((out / f"oracle-{kind}-tag.json").read_text())


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_wrapper_classifies_the_oracle_exit_and_marks_unjudgeable_runs_invalid(tmp_path, name):
    body, verdict, invalid = CASES[name]
    done, record = _run(tmp_path, body)
    assert record["verdict"] == verdict
    assert record["counts_as_candidate_verdict"] == (verdict in ("PASS", "REJECT"))
    assert done.returncode == invalid
    assert ("QUALIFICATION INVALID FOR THIS RUN" in done.stdout) == bool(invalid)
    assert {"tree", "scratch", "exit"} <= set(record)  # the old keys stay


def test_the_regress_oracle_is_classified_the_same_way(tmp_path):
    done, record = _run(tmp_path, "echo INSTALL_FAILED; exit 2", kind="regress")
    assert record["verdict"] == "ENVIRONMENT_UNAVAILABLE" and done.returncode == 1


def test_the_classify_command_uses_distinct_exit_codes(tmp_path):
    log = tmp_path / "log.txt"
    seen = {}
    for text, code in (("ok", 0), ("2 failed in 1s", 1), ("INSTALL_FAILED", 3), ("nothing known", 4)):
        log.write_text(text)
        exit_arg = "0" if code == 0 else "1"
        seen[code] = subprocess.run(["python3", str(PREFLIGHT), "classify", exit_arg, str(log)], capture_output=True, text=True,
                                    check=False).returncode
    assert seen == {0: 0, 1: 1, 3: 3, 4: 4}


@pytest.mark.parametrize("script,label", [("raise RuntimeError('boom')", "traceback"), ("import sys; sys.exit(2)", "usage exit"),
                                          ("print('REJECT\\tx'); import sys; sys.exit(0)", "verdict/exit disagree")])
def test_a_crashed_or_inconsistent_classifier_makes_the_run_invalid_never_a_rejection(tmp_path, script, label):
    crashing = tmp_path / "pre.py"
    crashing.write_text(script + "\n")
    done, record = _run(tmp_path, "echo '2 failed in 0.02s'; exit 1", preflight=crashing)
    assert record["verdict"] == "INDETERMINATE" and not record["counts_as_candidate_verdict"], label
    assert done.returncode == 1 and "QUALIFICATION INVALID FOR THIS RUN" in done.stdout
