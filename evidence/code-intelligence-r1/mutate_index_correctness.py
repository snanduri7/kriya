"""Stage-4 mutations (E-01/E-17/E-13/E-14): each must make a test fail. Usage: python mutate_index_correctness.py <repo>"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_code_intel_index_correctness.py"]
A, V, S = "kriya/analyzer/analyzer.py", "kriya/memory/vector.py", "kriya/skills/skill.py"
G = "kriya/analyzer/graph.py"
M = [
    ("e01-deletions-from-changed-subset", A, "(store.indexed_paths() | graph.indexed_paths()) - walked_rel_paths",
     "(store.indexed_paths() | graph.indexed_paths()) - {os.path.relpath(f, self.root_path) for f in files_to_index}"),
    ("e17-deletions-from-cache-only", A, "(store.indexed_paths() | graph.indexed_paths()) - walked_rel_paths",
     "set(store.file_metadata.keys()) - walked_rel_paths"),
    ("e17-metadata-row-kept", V, "            self.conn.execute(\"DELETE FROM file_metadata WHERE filepath = ?\", (filepath,))\n\n    def indexed_paths",
     "            pass\n\n    def indexed_paths"),
    ("e17-remove-not-transactional", V, "            self.conn.execute(\"DELETE FROM vector_chunks WHERE filepath = ?\", (filepath,))\n            self.conn.execute(f\"DELETE FROM {lexical} WHERE filepath = ?\", (filepath,))\n            self.conn.execute(\"DELETE FROM file_metadata",
     "            self.conn.execute(\"DELETE FROM vector_chunks WHERE filepath = ?\", (filepath,))\n            self.conn.commit()\n            self.conn.execute(f\"DELETE FROM {lexical} WHERE filepath = ?\", (filepath,))\n            self.conn.execute(\"DELETE FROM file_metadata"),
    ("e17-module-range-count", A, "                    \"start\": module_span[0],\n                    \"end\": module_span[1]",
     "                    \"start\": 1,\n                    \"end\": len(module_decls)"),
    ("e13-no-exclusion", A, "    return os.path.realpath(dirpath) in owned_roots or os.path.isfile(os.path.join(dirpath, SKILL_PACKAGE_MARKER))",
     "    return False"),
    ("e13-owned-roots-ignored", A, "    return os.path.realpath(dirpath) in owned_roots or os.path.isfile",
     "    return os.path.isfile"),
    ("e13-hardcoded-name", A, "    return os.path.realpath(dirpath) in owned_roots or os.path.isfile(os.path.join(dirpath, SKILL_PACKAGE_MARKER))",
     "    return os.path.realpath(dirpath) in owned_roots or os.path.basename(dirpath) == 'skills'"),
    ("manifest-stale-structure-reused", G, "            stale = stored != json.dumps(identity, sort_keys=True) and self.has_indexed_files()",
     "            stale = False"),
    ("manifest-empty-identity-accepted", G, "            stale = stored != json.dumps(identity, sort_keys=True) and self.has_indexed_files()",
     "            stale = stored is not None and stored != json.dumps(identity, sort_keys=True) and self.has_indexed_files()"),
    ("manifest-restructure-reembeds", A, "                vectors_current = not force and cached_hash == file_hash\n",
     "                vectors_current = False\n"),
    ("e14-substring", S, "    return any(haystack[i:i + width] == needle for i in range(len(haystack) - width + 1))",
     "    return term.lower() in text.lower()"),
]
survivors = 0
for label, rel, old, new in M:
    path = ROOT / rel
    text = path.read_text()
    if text.count(old) != 1:
        print(label, "ANCHOR", text.count(old))
        survivors += 1
        continue
    path.write_text(text.replace(old, new))
    try:
        result = subprocess.run([str(ROOT / ".venv/bin/pytest"), "-q", "-x", "-p", "no:randomly", *TESTS], cwd=ROOT,
                                capture_output=True, text=True, timeout=300)
    finally:
        path.write_text(text)
    killed = result.returncode != 0
    survivors += not killed
    failed = [line for line in result.stdout.splitlines() if line.startswith("FAILED")][:1]
    print(label, "KILLED" if killed else "SURVIVED", failed[0][7:110] if failed else "", flush=True)
print(f"{len(M) - survivors}/{len(M)} killed")
