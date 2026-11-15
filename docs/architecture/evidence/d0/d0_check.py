"""D0 exit checks over the architecture documents (read-only). Exit 1 on any failure.

1. Bypass dispositions: every BYP row defined in 05-INTEGRATION-BYPASS-MAP.md has exactly one row in the section
   "Dispositions of every row", with a disposition from the closed vocabulary; every `root-gap` names a root R-nn
   that 06 defines and a contract C-nn that 07 defines, and a slice S0..S8; the totals line matches the count.
2. Open statements: delegated to open_statements.py --register (run separately).
3. Slice specs: 12-D0-CLOSURE-HARDENING.md carries, for S5, S6 and S8, every required field of an
   implementation-ready slice.
4. Registry mapping: REGISTRY-ROOT-MAP.md still states 141 rows and its per-class counts sum to 141.

Usage: python3 d0_check.py <repo_root>
"""
import re
import sys
from collections import Counter
from pathlib import Path

VOCABULARY = {"closed/covered", "intentional", "future", "root-gap", "blocker"}
SPEC_FIELDS = ("Root owner", "Files and modules", "Allowed scope", "Forbidden scope", "Invariants",
               "Contract changes", "Acceptance tests", "Negative controls", "Mutants", "Architecture checks",
               "Prerequisites", "Stop conditions")


def main() -> int:
    root = Path(sys.argv[1]) / "docs/architecture"
    failures = []
    bypass = (root / "05-INTEGRATION-BYPASS-MAP.md").read_text(encoding="utf-8")
    head, _, tail = bypass.partition("## Dispositions of every row")
    defined = re.findall(r"^\|\s*(BYP-A\d-\d{2})\s*\|", head, re.M)
    rows = re.findall(r"^\|\s*(BYP-A\d-\d{2})\s*\|\s*`([^`]+)`\s*\|\s*([^|]*)\|", tail, re.M)
    seen = Counter(i for i, _, _ in rows)
    roots = set(re.findall(r"^### (R-\d{2}) ", (root / "06-GAP-AUTHORITY-AUDIT.md").read_text(encoding="utf-8"), re.M))
    contracts = set(re.findall(r"\b(C-\d{2})\b", (root / "07-REQUIRED-CONTRACTS.md").read_text(encoding="utf-8")))
    if len(defined) != len(set(defined)):
        failures.append("a BYP row is defined twice in the slice tables")
    for row in defined:
        if seen[row] != 1:
            failures.append(f"{row}: {seen[row]} disposition rows (expected 1)")
    for row, disposition, basis in rows:
        if row not in defined:
            failures.append(f"{row}: disposition for a row the map does not define")
        if disposition not in VOCABULARY:
            failures.append(f"{row}: disposition {disposition!r} outside the vocabulary")
        if disposition == "root-gap":
            named_roots, named_contracts = re.findall(r"R-\d{2}", basis), re.findall(r"C-\d{2}", basis)
            if not named_roots or not named_contracts or not re.search(r"\bS[0-8]\b", basis):
                failures.append(f"{row}: root-gap without a root, a contract and a slice")
            failures += [f"{row}: unknown root {r}" for r in named_roots if r not in roots]
            failures += [f"{row}: unknown contract {c}" for c in named_contracts if c not in contracts]
    counts = Counter(d for _, d, _ in rows)
    totals = re.search(r"Totals: (.*?)\. The one", tail, re.S)
    stated = dict((name, int(n)) for name, n in re.findall(r"`([^`]+)` (\d+)", totals.group(1))) if totals else {}
    if stated != {name: counts.get(name, 0) for name in VOCABULARY}:
        failures.append(f"totals line {stated} differs from the rows {dict(counts)}")
    print(f"bypass rows defined: {len(defined)}; dispositions: {dict(counts)}")

    spec = (root / "12-D0-CLOSURE-HARDENING.md").read_text(encoding="utf-8")
    for slice_id in ("S5", "S6", "S8"):
        section = re.search(rf"^### {slice_id} .*?(?=^### |^## |\Z)", spec, re.M | re.S)
        if not section:
            failures.append(f"{slice_id}: no implementation specification")
            continue
        missing = [f for f in SPEC_FIELDS if not re.search(rf"^\|\s*{re.escape(f)}\s*\|\s*\S", section.group(0), re.M)]
        if missing:
            failures.append(f"{slice_id}: specification lacks {missing}")
    print("slice specifications checked: S5, S6, S8")

    registry = (root / "REGISTRY-ROOT-MAP.md").read_text(encoding="utf-8")
    line = re.search(r"Rows: \*\*(\d+)\*\*\.(.*)", registry)
    classes = [int(n) for n in re.findall(r"[A-Z_]+ (\d+)", line.group(2))] if line else []
    data_rows = len(re.findall(r"^\|\s*\d+\s*\|\s*`", registry, re.M))
    if not line or int(line.group(1)) != 141 or sum(classes) != 141 or data_rows != 141:
        failures.append(f"registry mapping: header {line.group(1) if line else None}, classes sum {sum(classes)}, rows {data_rows}")
    print(f"registry rows mapped: {data_rows}; class counts sum: {sum(classes)}")

    for failure in failures:
        print("FAIL", failure)
    print(f"failures: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
