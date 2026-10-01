"""Slice 3/4 mutations (overlay, current-bytes binding, fusion, packing, seam): each must make a test fail.
Usage: python mutate_service.py <repo>"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_code_intel_service.py", "tests/test_code_intel_integration.py"]
SV, PK, LC, CS = ("kriya/code_intel/service.py", "kriya/code_intel/packing.py", "kriya/code_intel/locate.py",
                  "kriya/workflow/context_source.py")
M = [
    ("overlay-does-not-shadow", SV, "        return self.overlay.shadowed if self.overlay else frozenset()",
     "        return frozenset()"),
    ("stale-index-span-used", SV, "        if digest != indexed.source_digest:\n            structure = parse_file",
     "        if False:\n            structure = parse_file"),
    ("member-at-trusts-stale-index", SV, "and self.store.file_digest(path) == source_digest(data):",
     "and self.store.file_digest(path) is not None:"),
    ("lexical-outranks-exact", LC, "FTS_MAX = 10.0", "FTS_MAX = 500.0"),
    ("type-wins-over-members", SV, "                    self._credit(evidence, meta, symbol, \"simple_symbol\", loc.OWNER_NAMED * weight)",
     "                    self._credit(evidence, meta, symbol, \"simple_symbol\", loc.SIMPLE_SYMBOL * 5)"),
    ("t0-dropped-over-budget", PK, "        package.over_budget = True  # T0 stays whole; nothing optional is added\n        return package",
     "        package.over_budget = True\n        package.member_text = ''\n        return package"),
    ("optional-tier-ignores-budget", PK, "    if budget is not None and count_tokens(package.render()) > budget:",
     "    if False:"),
    ("t0-not-last", PK, "        if self.tests:\n            parts.append(\"#### Linked tests (read-only)\\n\" + \"\\n\".join(sig for _, sig in self.tests))\n        parts.append(",
     "        if self.tests:\n            parts.append(\"#### Linked tests (read-only)\\n\" + \"\\n\".join(sig for _, sig in self.tests))\n        parts.insert(1, "),
    ("boundaries-primary-type-only", CS, "        if symbol.kind not in (\"method\", \"constructor\"):\n            continue\n        start = symbol.signature.start_line",
     "        if symbol.kind not in (\"method\", \"constructor\") or symbol.lookup_key.count('.') > prefix.count('.') + 1:\n            continue\n        start = symbol.signature.start_line"),
    ("abs-path-not-resolved", SV, "        for start in range(len(parts)):", "        for start in range(1):"),
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
