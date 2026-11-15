"""D0: for each root R-01..R-13, the map rows its audit section and its contract cite, by map, and the stage
documents, integration contracts and diagrams that cite the same rows (read-only). Output is the correlation set the
D0 sweep compared; a root with no row of a map prints 0 for it, which is itself read (is the absence a fact?).

Usage: python3 root_rows.py <repo_root> [--ids]
"""
import re
import sys
from pathlib import Path

ID = re.compile(r"\b(AUTH|SM|MUT|FR|BYP|GAP)-(A\d)-(\d{2})(?:\.\.(\d{2}))?")
CONTRACT_OF = {"R-12": "C-12", "R-13": "C-12"}  # C-12 and C-13 share one table in 07


def ids(text):
    found = set()
    for kind, sl, first, last in ID.findall(text):
        for n in range(int(first), int(last or first) + 1):
            found.add(f"{kind}-{sl}-{n:02d}")
    return found


def main() -> int:
    root = Path(sys.argv[1]) / "docs/architecture"
    audit = (root / "06-GAP-AUTHORITY-AUDIT.md").read_text(encoding="utf-8")
    contracts = (root / "07-REQUIRED-CONTRACTS.md").read_text(encoding="utf-8")
    ledger = (root / "GAP-LEDGER.md").read_text(encoding="utf-8")
    views = {name: (root / name).read_text(encoding="utf-8") for name in
             ("08-STAGE-DOCUMENTS.md", "09-INTEGRATION-CONTRACTS.md", "10-DIAGRAMS.md")}
    table = audit.split("## 1. The root issues")[1].split("## 2. Each root issue")[0]
    boundaries = audit.split("## 10. Integration surfaces")[1].split("## 11.")[0]
    for number in range(1, 14):
        rid = f"R-{number:02d}"
        section = re.search(rf"^### {rid} .*?(?=^### |^## )", audit, re.M | re.S)
        cid = CONTRACT_OF.get(rid, f"C-{number:02d}")
        contract = re.search(rf"^### {cid} .*?(?=^### |^## )", contracts, re.M | re.S)
        row = next((line for line in table.splitlines() if line.startswith(f"| {rid} ")), "")
        cited = ids(section.group(0) if section else "") | ids(contract.group(0) if contract else "") | ids(row)
        cited |= {i for line in boundaries.splitlines() if re.search(rf"\b{rid}\b", line) for i in ids(line)}
        # one level of expansion: the rows each child gap's ledger entry cites
        for gap in sorted(i for i in cited if i.startswith("GAP-")):
            entry = re.search(rf"^### {gap} .*?(?=^### |^## )", ledger, re.M | re.S)
            cited |= ids(entry.group(0)) if entry else set()
        by_kind = {k: sorted(i for i in cited if i.startswith(k + "-")) for k in ("AUTH", "MUT", "SM", "FR", "BYP", "GAP")}
        citing = {name[:2]: sum(1 for i in cited if i in text) for name, text in views.items()}
        print(f"{rid}: " + ", ".join(f"{k} {len(v)}" for k, v in by_kind.items())
              + f"; of these cited in stage docs {citing['08']}, integration contracts {citing['09']}, diagrams {citing['10']}")
        if "--ids" in sys.argv:
            for kind, values in by_kind.items():
                print(f"   {kind}: {' '.join(values)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
