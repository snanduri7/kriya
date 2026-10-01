import subprocess, sys
from pathlib import Path
ROOT = Path(sys.argv[1])
TESTS = ["tests/test_d3_runtime_prerequisite.py"]
M = [
 ("reuse-prior-target", "kriya/tools/validate.py",
  "        if not any(self._runtime_needs_compiled_classes(c) for c in commands):\n            return None, None",
  "        if not any(self._runtime_needs_compiled_classes(c) for c in commands) or os.path.isdir(os.path.join(self.workspace_path, \"target\", \"classes\")):\n            return None, None"),
 ("no-prerequisite", "kriya/tools/validate.py",
  "        not_ready, prerequisite = self._prepare_runtime(commands)\n", "        not_ready, prerequisite = None, None\n"),
 ("prereq-failure-ignored", "kriya/tools/validate.py",
  "        if prepared[\"success\"]:\n            return None, \"mvn clean compile (current source): PASSED\"",
  "        if True:\n            return None, \"mvn clean compile (current source): PASSED\""),
 ("prereq-not-deadline-bound", "kriya/tools/validate.py",
  "        prepared = self.run_compile_check(self._java_sources() or [\"pom.xml\"], deadline=self._run_deadline())",
  "        prepared = self.run_compile_check(self._java_sources() or [\"pom.xml\"])"),
 ("stale-stack-skips-rebuild", "kriya/tools/validate.py",
  "        if self.stack != \"java\":\n            # A pom.xml", "        if False:\n            # A pom.xml"),
 ("prereq-failure-unclassified", "kriya/workflow/acceptance.py",
  "    if result.get(\"prerequisite_failed\") or output.startswith(\"RUNTIME_PREREQUISITE_FAILED:\"):", "    if False:"),
]
surv = 0
for label, rel, old, new in M:
    p = ROOT / rel; t = p.read_text()
    if t.count(old) != 1:
        print(label, "ANCHOR", t.count(old)); surv += 1; continue
    p.write_text(t.replace(old, new))
    try:
        r = subprocess.run([str(ROOT / ".venv/bin/pytest"), "-q", "-x", "-p", "no:randomly", *TESTS], cwd=ROOT, capture_output=True, text=True, timeout=900)
    finally:
        p.write_text(t)
    k = r.returncode != 0; surv += not k
    print(label, "KILLED" if k else "SURVIVED", flush=True)
print(f"{len(M)-surv}/{len(M)} killed")
