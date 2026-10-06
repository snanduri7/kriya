"""LR-R1 post-P3 validation: baseline admission gate (copy of r2_admission.py, output path only changed) (harness; no model is called).

For each candidate workspace, at its exact base: the repository's own full regression suite, run exactly the way
Kriya's own brownfield baseline capture runs it (workflow.py: PolymorphicValidator(workspace,
original_workspace_path=workspace, autonomy_cfg=cfg.autonomy).run_tests(target_test=None)) under the candidate's
approved production config - the environment the task will later be judged in. The exact argv of every process the
validator launches is recorded (its _run_cmd_with_timeout is wrapped, observation only). Then the workspace is
restored to its pristine base (git clean -fdx, reset --hard).

usage: venv-p5/bin/python scripts/r2_admission.py <workspace>...
"""
import json
import os
import re
import subprocess
import sys
import time

D = os.path.expanduser("~/kriya-m1-live")


def counts(output: str) -> dict:
    """Totals from the runner's own summary: pytest's final line or Maven Surefire's aggregate 'Tests run:' line."""
    py = re.findall(r"=+ (.*?) in [\d.]+s(?: \([^)]*\))? =+", output)
    if py:
        summary = py[-1]
        found = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)", summary)}
        return {"runner": "pytest", "summary": summary, "passed": found.get("passed", 0),
                "failed": found.get("failed", 0), "errors": found.get("errors", found.get("error", 0)),
                "skipped": found.get("skipped", 0)}
    mvn = re.findall(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)\s*$", output, re.M)
    if mvn:
        run, failures, errors, skipped = (int(x) for x in mvn[-1])
        return {"runner": "maven-surefire", "summary": f"Tests run: {run}, Failures: {failures}, Errors: {errors}, "
                f"Skipped: {skipped}", "passed": run - failures - errors - skipped, "failed": failures,
                "errors": errors, "skipped": skipped}
    return {"runner": "unknown", "summary": None, "passed": None, "failed": None, "errors": None, "skipped": None}


def admit(name: str) -> dict:
    from kriya.config.config import load_config
    from kriya.tools import validate

    workspace = os.path.join(D, "ws", name)
    base = open(os.path.join(D, "ws", f"{name}.base")).read().strip()
    os.chdir(workspace)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == base, (head, base)
    cfg = load_config(os.path.join(D, "config", f"{name}.yaml"))
    commands = []
    real = validate.PolymorphicValidator._run_cmd_with_timeout

    def recording(self, cmd, *args, **kwargs):
        commands.append({"argv": list(cmd) if isinstance(cmd, (list, tuple)) else cmd,
                         "cwd": kwargs.get("cwd") or (args[0] if args else None)})
        return real(self, cmd, *args, **kwargs)

    validate.PolymorphicValidator._run_cmd_with_timeout = recording
    started = time.monotonic()
    try:
        result = validate.PolymorphicValidator(workspace, original_workspace_path=workspace,
                                               autonomy_cfg=cfg.autonomy).run_tests(target_test=None)
    finally:
        validate.PolymorphicValidator._run_cmd_with_timeout = real
    seconds = round(time.monotonic() - started, 1)
    output = result.get("output") or ""
    found = counts(output)
    green = bool(result.get("success")) and found["failed"] == 0 and found["errors"] == 0 and found["passed"]
    record = {"workspace": name, "commit": head, "stack": validate.PolymorphicValidator(workspace).stack,
              "commands": commands, "success": bool(result.get("success")), **found, "duration_seconds": seconds,
              "execution_evidence": validate.execution_evidence(result), "gate": "GREEN" if green else "RED",
              "output_tail": output[-1500:]}
    subprocess.run(["git", "reset", "-q", "--hard", base], check=True)
    subprocess.run(["git", "clean", "-qfdx"], check=True)
    record["restored_clean"] = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                              text=True).stdout == ""
    os.makedirs(os.path.join(D, "evidence", "p3v", "admission"), exist_ok=True)
    with open(os.path.join(D, "evidence", "p3v", "admission", f"{name}.json"), "w") as handle:
        json.dump(record, handle, indent=1)
    print(json.dumps({k: record[k] for k in ("workspace", "commit", "gate", "summary", "passed", "failed", "errors",
                                             "skipped", "duration_seconds", "restored_clean")}), flush=True)
    return record


if __name__ == "__main__":
    for workspace_name in sys.argv[1:]:
        admit(workspace_name)
