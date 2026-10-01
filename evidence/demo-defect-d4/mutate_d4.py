import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_d4_runtime_entrypoint_ownership.py", "tests/test_d3_runtime_prerequisite.py",
         "tests/test_runtime_maven_acquisition.py"]
A, V, R = "kriya/workflow/acceptance.py", "kriya/tools/validate.py", "kriya/workflow/retry_strategy.py"
T = "kriya/workflow/attempt.py"
M = [
 ("every-entrypoint-failure-infrastructure", T, "    if ownership == CANDIDATE_RUNTIME_ENTRYPOINT_INVALID:\n", "    if False:\n"),
 ("every-entrypoint-failure-candidate", A, "    if not diagnosis or not run_command_targets_missing_entrypoint(output):\n        return None",
  "    if not diagnosis or not run_command_targets_missing_entrypoint(output):\n        return None\n    return CANDIDATE_RUNTIME_ENTRYPOINT_INVALID"),
 ("ignore-provenance", A, "    return (CANDIDATE_RUNTIME_ENTRYPOINT_INVALID if provenance == \"CANDIDATE_BUILD_CONFIG\"\n            else RUNTIME_COMMAND_ENTRYPOINT_INVALID)",
  "    return CANDIDATE_RUNTIME_ENTRYPOINT_INVALID"),
 ("skip-source-check", A, "    if declared:\n        return RUNTIME_ENTRYPOINT_NOT_BUILT", "    if False:\n        return RUNTIME_ENTRYPOINT_NOT_BUILT"),
 ("skip-compiled-check", A, "    if built:\n        return RUNTIME_ENTRYPOINT_NOT_LOADABLE", "    if False:\n        return RUNTIME_ENTRYPOINT_NOT_LOADABLE"),
 ("validator-skips-source-scan", V, "        declared = bool(effective) and any(", "        declared = False and any("),
 ("validator-skips-compiled-scan", V, "        compiled = bool(effective) and self._compiled_class_exists(f\"{relative}.class\")", "        compiled = False"),
 ("output-not-cross-checked", A, "    if effective not in reported:\n        return None\n", ""),
 ("authorize-pom-globally", R, "            allowed_scope = set() if is_deny_all_scope else set(ctx.allowed_write_relpaths)",
  "            allowed_scope = (set() if is_deny_all_scope else set(ctx.allowed_write_relpaths)) | set(implicated)"),
 ("verification-only-gets-all-files", R, "            allowed_scope = set() if is_deny_all_scope else set(ctx.allowed_write_relpaths)",
  "            allowed_scope = set(implicated) if is_deny_all_scope else set(ctx.allowed_write_relpaths)"),
 ("developer-for-kriya-owned-defect", T, "    if ownership == CANDIDATE_RUNTIME_ENTRYPOINT_INVALID:\n", "    if ownership is not None:\n"),
 ("model-attribution-instead", R, "        elif fail_type == \"candidate_runtime_entrypoint_invalid\":", "        elif False:"),
 ("no-default-cli-precedence", V, "                for execution in (executions if executions is not None else []):", "                for execution in []:"),
 ("parent-guessed", V, "            if child(root, \"parent\") is not None:\n                return None, None, self.ENTRYPOINT_UNKNOWN\n", ""),
 ("property-not-resolved", V, "            if name in properties and \"$\" not in properties[name]:", "            if False:"),
 ("bypass-d3-preparation", V, "        not_ready, prerequisite = self._prepare_runtime(commands)\n", "        not_ready, prerequisite = None, None\n"),
 ("candidate-runs-during-acquisition", V, "            if tooling_only:\n                self._acquire_maven_tooling(goals, cache_dir, _bounded(timeout))\n                return\n", ""),
]
surv = 0
for label, rel, old, new in M:
    p = ROOT / rel
    t = p.read_text()
    if t.count(old) != 1:
        print(label, "ANCHOR", t.count(old))
        surv += 1
        continue
    p.write_text(t.replace(old, new))
    try:
        r = subprocess.run([str(ROOT / ".venv/bin/pytest"), "-q", "-x", "-p", "no:randomly", *TESTS], cwd=ROOT, capture_output=True, text=True, timeout=1800)
    finally:
        p.write_text(t)
    k = r.returncode != 0
    surv += not k
    fail = [line for line in r.stdout.splitlines() if line.startswith("FAILED")][:1]
    print(label, "KILLED" if k else "SURVIVED", fail[0][7:110] if fail else "", flush=True)
print(f"{len(M)-surv}/{len(M)} killed")
