"""D0 cross-map concept sweep over the architecture documents (read-only).

The two errors this closure made (generation-clock scope, store classification) were both "one concept, stated
differently in two maps". This script retrieves, for a named concept, every place any document states it, with the
row ID of the table row or section it sits in, so the statements can be compared side by side against the code.
It decides nothing: it prints content for reading.

Usage: python3 concept_sweep.py <repo_root> <concept> [--width N] [--count]
       python3 concept_sweep.py <repo_root> --list
"""
import re
import sys
from pathlib import Path

CONCEPTS = {
    # the two classes already caught late
    "clock": r"per[- ]unit[- ]invocation|each unit invocation|bounds each unit|per unit invocation|"
             r"no (?:layer|owner|run)[^.|]{0,40}bound|nothing bounds|new (?:generation )?clock|fresh (?:clock|time)|"
             r"generation clock|one clock|run clock|time budget (?:is|bounds|per)|per process run",
    "store": r"outcome of record|no contract (?:states|names|says)|STORE_CLASSIFICATION|independent observability|"
             r"authoritative record|which record",
    # analogous same-concept classes
    "durable": r"memory only|not persisted|nothing persisted|lost on resume|survives? (?:a|an|the) (?:resume|"
               r"interruption|restart|sandbox reset)|not restored|is persisted|are persisted|not checkpointed",
    "review": r"final review[^.|]{0,80}(?:advisory|decid|fails|refus)|advisory[^.|]{0,40}(?:enforce|direct|milestone)",
    "scope": r"installation-wide|installation directory|per[- ]project|project[- ]keyed|no project (?:key|identity)|"
             r"one namespace|global (?:store|namespace|by nature)",
    "qual": r"qualif[^.|]{0,80}(?:primary|every role|fallback|retry identity|never|only)",
    "approval": r"approval (?:subject|record)[^.|]{0,80}|per unit[^.|]{0,40}approv|candidate digest",
    "resume": r"resum[^.|]{0,60}(?:new run|run id|new id|fresh|full|reuses?|discard|skips?)",
    "budgetfresh": r"fresh (?:full )?(?:budget|ceiling|allowance)|full (?:fresh )?ceiling|budgets? (?:start|zero)|"
                   r"starts? (?:from|at) zero",
    "planhash": r"plan hash|current_plan_hash|plan identity|content_hash",
    "reqtruth": r"requirement[_. ]truth|requirements_verified|quality_gates_passed|ALL_VERIFIED",
    "trust": r"outside the (?:untrusted[- ]reference )?fence|unfenced|inside the fence|trusted (?:`?Rules|header)|"
             r"system prompt",
    "mcp": r"MCP[^.|]{0,80}(?:operator-only|write grant|workspace_write|process directory|getcwd|authority root)",
    "placeholder": r"\bTODO\b|\bTBD\b|\bPENDING\b|NEEDS_CHARACTERIZATION|`UNKNOWN`|not established|was not read|"
                   r"were not read|not read in|not traced|not re-read|not exercised|not measured|not executed|"
                   r"not enumerated",
}
ROW = re.compile(r"\b((?:AUTH|SM|MUT|FR|BYP|GAP|DOC)-A\d-\d{2}|R-\d{2}|C-\d{2}|X-\d{2}|INT-\d{2}|STAGE-[A-Z-]+|IC-\d{2}|D-\d)\b")
HEAD = re.compile(r"^#{2,4}\s+(.*)")


def main() -> int:
    root = Path(sys.argv[1]) / "docs/architecture"
    if sys.argv[2] == "--list":
        print("\n".join(CONCEPTS))
        return 0
    pattern = re.compile(CONCEPTS[sys.argv[2]], re.I)
    width = int(sys.argv[sys.argv.index("--width") + 1]) if "--width" in sys.argv else 110
    count_only = "--count" in sys.argv
    total = 0
    for path in sorted(root.glob("*.md")):
        if path.name.startswith(("CORR016", "SEC001")):
            continue
        section, hits = "", 0
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            head = HEAD.match(line)
            if head:
                section = head.group(1)[:40]
            for match in pattern.finditer(line):
                hits += 1
                if count_only:
                    continue
                first = ROW.search(line)
                # a table row's own ID is its first cell; otherwise the nearest ID before the match, else the section
                before = ROW.findall(line[:match.start()])
                row = (first.group(1) if line.lstrip().startswith("|") and first else (before[-1] if before else section))
                start, end = max(0, match.start() - width), min(len(line), match.end() + width)
                print(f"{path.name[:14]}:{number} [{row}] ...{line[start:end].strip()}...")
        total += hits
        if count_only and hits:
            print(f"{path.name}: {hits}")
    print(f"total hits: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
