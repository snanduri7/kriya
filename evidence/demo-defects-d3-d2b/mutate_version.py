"""KRIYA-VERSION-001 mutation check (each must make tests/test_kriya_version_001.py fail)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
M = [
 ("no-embed-in-wheel", "setup.py", "            _write(self.build_lib, _build_info())", "            pass"),
 ("sdist-drops-identity", "setup.py", "        _write(base_dir, _build_info())", "        pass"),
 ("no-toplevel-check", "setup.py", "        if os.path.realpath(_git(\"rev-parse\", \"--show-toplevel\")) != os.path.realpath(ROOT):\n            return None\n", ""),
 ("untracked-not-dirty", "setup.py", " or any(line.startswith(\"??\") for line in untracked.splitlines())", ""),
 ("require-clean-ignored", "setup.py", "    if os.environ.get(\"KRIYA_BUILD_REQUIRE_CLEAN\") == \"1\" and", "    if False and"),
 ("invent-on-missing", "kriya/build_info.py", "        return {**unknown, \"build_provenance\": UNAVAILABLE}", "        return {\"commit\": \"0\"*40, \"tree\": \"0\"*40, \"dirty\": False, \"build_provenance\": EMBEDDED}"),
 ("no-schema-validation", "kriya/build_info.py", "    if (not isinstance(data, dict) or data.get(\"schema\") != BUILD_INFO_SCHEMA", "    if (not isinstance(data, dict)"),
 ("dirty-hidden-in-line", "kriya/build_info.py", "    dirty = \", dirty\" if report[\"dirty\"] else \"\"", "    dirty = \"\""),
 ("version-loads-config", "kriya/cli.py", "    if ctx.invoked_subcommand in ('authority', 'runs', 'version'):", "    if ctx.invoked_subcommand in ('authority', 'runs'):"),
 ("version-hardcoded", "kriya/build_info.py", "        return metadata.version(PRODUCT)", "        return \"0.0.0\""),
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
        r = subprocess.run([str(ROOT / ".venv/bin/pytest"), "-q", "-x", "-p", "no:randomly", "tests/test_kriya_version_001.py"],
                           cwd=ROOT, capture_output=True, text=True, timeout=900)
    finally:
        p.write_text(t)
    k = r.returncode != 0
    surv += not k
    print(label, "KILLED" if k else "SURVIVED", flush=True)
print(f"{len(M)-surv}/{len(M)} killed")
