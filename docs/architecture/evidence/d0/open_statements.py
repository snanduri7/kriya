"""D0: list every statement of an open status in the architecture documents (read-only).

An "open status" is a placeholder or an unresolved question: TODO, TBD, PENDING, NEEDS_CHARACTERIZATION, a literal
`UNKNOWN` label, "not established", "open question". Statements of an evidence limit ("not executed", "not measured",
"was not read") are evidence labels, not open statuses, and are listed only with --limits.

With --register <file> the script checks the D0 resolution register: every open statement in a map or derivative
document must be covered by a register row naming its document and row ID, or be on a line the register exempts
(vocabulary definitions, history of a correction). Exit 1 when a statement is uncovered.

Usage: python3 open_statements.py <repo_root> [--limits] [--register docs/architecture/12-...md]
"""
import re
import sys
from pathlib import Path

OPEN = re.compile(r"\bTODO\b|\bTBD\b|\bPENDING\b|NEEDS_CHARACTERIZATION|`UNKNOWN`|not established|open question", re.I)
LIMITS = re.compile(r"was not read|were not read|not read in|not traced|not re-read|not exercised|not measured|"
                    r"not executed|not enumerated|not run\b", re.I)
ROW = re.compile(r"\b((?:AUTH|SM|MUT|FR|BYP|GAP|DOC)-A\d-\d{2})\b")
HEAD = re.compile(r"^#{2,4}\s+(.*)")
SCOPE = ("00-", "01-", "02-", "03-", "04-", "05-", "08-", "09-", "10-", "GAP-LEDGER")


def statements(root: Path, pattern):
    for path in sorted(root.glob("*.md")):
        if not path.name.startswith(SCOPE):
            continue
        section = ""
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            head = HEAD.match(line)
            if head:
                section = head.group(1)
                found = ROW.search(section)
                section = found.group(1) if found else section[:36]
            for match in pattern.finditer(line):
                first = ROW.search(line)
                row = first.group(1) if line.lstrip().startswith("|") and first else section
                start, end = max(0, match.start() - 130), min(len(line), match.end() + 90)
                yield path.name, number, row, line[start:end].strip()


def main() -> int:
    root = Path(sys.argv[1]) / "docs/architecture"
    pattern = LIMITS if "--limits" in sys.argv else OPEN
    found = list(statements(root, pattern))
    if "--register" not in sys.argv:
        for name, number, row, text in found:
            print(f"{name[:12]}:{number} [{row}] ...{text}...")
        print(f"total: {len(found)}")
        return 0
    register = Path(sys.argv[sys.argv.index("--register") + 1]).read_text(encoding="utf-8")
    covered = set(re.findall(r"^\|\s*`([^`]+)`\s*\|\s*([0-9, ]+)\s*\|", register, re.M))
    lines = {(name, int(n)) for name, numbers in covered for n in numbers.replace(" ", "").split(",") if n}
    missing = [(name, number, row, text) for name, number, row, text in found if (name, number) not in lines]
    for name, number, row, text in missing:
        print(f"UNCOVERED {name}:{number} [{row}] ...{text[:160]}...")
    print(f"open statements: {len(found)}; covered by the register: {len(found) - len(missing)}; uncovered: {len(missing)}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
