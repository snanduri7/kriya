import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_runtime_maven_acquisition.py"]
M = [
 ("ACQUIRE-WITH-CANDIDATE-COMMAND", "kriya/tools/validate.py",
  "            if tooling_only:\n                self._acquire_maven_tooling(goals, cache_dir, _bounded(timeout))\n                return\n", ""),
 ("runtime-not-tooling-only", "kriya/tools/validate.py",
  "                                       stdin_payload=stdin_payload, deadline=self._run_deadline(),\n                                       tooling_only=True)",
  "                                       stdin_payload=stdin_payload, deadline=self._run_deadline())"),
 ("tooling-mounts-candidate", "kriya/tools/validate.py",
  "                    dependency_cache_path=cache_dir, dependency_cache_writable=True, workspace_path=empty,",
  "                    dependency_cache_path=cache_dir, dependency_cache_writable=True,"),
 ("tooling-broader-network", "kriya/tools/validate.py",
  "                    cwd=empty, timeout=timeout, network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, acquisition=True,",
  "                    cwd=empty, timeout=timeout, network=NetworkAuthority.UNRESTRICTED, acquisition=True,"),
 ("no-plugin-still-acquires", "kriya/tools/validate.py",
  "        if tooling_only and not self._maven_plugin_goals(goals):", "        if False:"),
 ("declared-version-ignored", "kriya/tools/validate.py",
  "            coordinate = (self._declared_maven_plugin(plugin) or plugin) if \":\" not in plugin else plugin",
  "            coordinate = plugin"),
 ("profile-ignores-mount", "kriya/tools/validate.py",
  "            workspace_path=workspace_path or self.workspace_path,", "            workspace_path=self.workspace_path,"),
 ("second-acquisition", "kriya/tools/validate.py",
  "        _acquire_for_this_goal()\n        if _deadline_exhausted():", "        _acquire_for_this_goal()\n        _acquire_for_this_goal()\n        if _deadline_exhausted():"),
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
        r = subprocess.run([str(ROOT / ".venv/bin/pytest"), "-q", "-x", "-p", "no:randomly", *TESTS], cwd=ROOT, capture_output=True, text=True, timeout=1500)
    finally:
        p.write_text(t)
    k = r.returncode != 0
    surv += not k
    fail = [line for line in r.stdout.splitlines() if line.startswith("FAILED")][:1]
    print(label, "KILLED" if k else "SURVIVED", fail[0][:110] if fail else "", flush=True)
print(f"{len(M)-surv}/{len(M)} killed")
