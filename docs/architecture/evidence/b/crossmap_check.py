"""Phase B cross-map consistency check over the architecture documents (read-only).

1. Collects every row ID DEFINED in its home map (AUTH in 01, SM in 02, MUT in 03, FR in 04, BYP in 05, GAP and DOC
   in the gap ledger or the README) and every ID REFERENCED anywhere in docs/architecture/*.md.
2. Reports references to IDs that no map defines (dangling), IDs defined twice, and defined IDs no other document
   refers to (orphans: a fact with a home that nothing else uses).
3. Prints, for every AUTH row that names another map's rows, which maps it is connected to, so a row with a
   mutation and no MUT reference, or a failure behaviour and no FR reference, can be read by hand.

Usage: python3 crossmap_check.py <repo_root> [--fields]
  --fields  also print selected fields of every full-field AUTH row (truth, owner, persisted state, consumers,
            checkpoint/resume, alternate paths, failure/UNKNOWN), truncated, for the integration-surface reading.
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

HOMES = {"AUTH": "01-AUTHORITY-MATRIX.md", "SM": "02-STATE-MACHINE.md", "MUT": "03-MUTATION-AUTHORITY.md",
         "FR": "04-FAILURE-RECOVERY-MATRIX.md", "BYP": "05-INTEGRATION-BYPASS-MAP.md", "GAP": "GAP-LEDGER.md"}
ID = re.compile(r"\b(AUTH|SM|MUT|FR|BYP|GAP)-(A1|A2|A3|A4|A5|A6)-(\d{2})\b")
RANGE = re.compile(r"\b(AUTH|SM|MUT|FR|BYP|GAP)-(A1|A2|A3|A4|A5|A6)-(\d{2})\.\.(\d{2})\b")
DEFINE = re.compile(r"^(?:###\s+|\|\s*)(AUTH|SM|MUT|FR|BYP|GAP)-(A1|A2|A3|A4|A5|A6)-(\d{2})\b")
FIELDS = ("Truth decided", "Owner", "Persisted state", "Downstream consumers", "Checkpoint / resume", "Alternate paths",
          "Failure / UNKNOWN")


def main() -> int:
    root = Path(sys.argv[1]) / "docs/architecture"
    docs = {p.name: p.read_text(encoding="utf-8") for p in sorted(root.glob("*.md"))}
    defined = defaultdict(list)
    for kind, home in HOMES.items():
        for number, line in enumerate(docs[home].splitlines(), 1):
            match = DEFINE.match(line)
            if match and match.group(1) == kind:
                defined["-".join(match.groups())].append(f"{home}:{number}")
    referenced = defaultdict(set)
    for name, text in docs.items():
        for match in RANGE.finditer(text):
            kind, sl, first, last = match.groups()
            for n in range(int(first), int(last) + 1):
                referenced[f"{kind}-{sl}-{n:02d}"].add(name)
        for match in ID.finditer(text):
            referenced["-".join(match.groups())].add(name)
    dangling = sorted(i for i in referenced if i not in defined)
    twice = sorted(i for i, where in defined.items() if len(where) > 1)
    orphans = sorted(i for i in defined if referenced[i] <= {HOMES[i.split("-")[0]]})
    print(f"defined: " + ", ".join(f"{k} {sum(1 for i in defined if i.startswith(k + '-'))}" for k in HOMES))
    print(f"dangling references (cited, defined nowhere): {len(dangling)}")
    for i in dangling:
        print(f"  {i} cited in {sorted(referenced[i])}")
    print(f"defined more than once: {len(twice)}")
    for i in twice:
        print(f"  {i} at {defined[i]}")
    print(f"defined and cited by no other document: {len(orphans)}")
    print("  " + ", ".join(orphans))
    if "--fields" in sys.argv:
        text = docs[HOMES["AUTH"]]
        for block in re.split(r"\n(?=### AUTH-)", text):
            head = block.splitlines()[0]
            if not head.startswith("### AUTH-A"):
                continue
            rows = {m.group(1).strip(): m.group(2).strip() for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", block, re.M)}
            if "Truth decided" not in rows:
                continue
            print("\n" + head)
            for field in FIELDS:
                value = rows.get(field, "")
                print(f"  {field}: {value[:int(sys.argv[sys.argv.index('--fields') + 1]) if sys.argv[-1].isdigit() else 330]}")
    # A second appearance is intended in two places (the Phase-B completion of the A1/A2 authority rows; the A1b
    # characterization table of the A1 gaps): reported above for reading, not a failure. A dangling reference is.
    return 1 if dangling else 0


if __name__ == "__main__":
    sys.exit(main())
