"""OPEN-ITEMS.md is the one ledger of every known issue, bug, gap and deferral of the full-modularization workstream (owner directive
2026-10-16). Each row names the slice that closes it; an OPEN row whose slice is already DONE fails here, so nothing is forgotten.
The planted self-check proves the rule is not vacuous."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, FrozenSet, List

from tests.architecture.test_import_direction import DONE_SLICES, SLICES

LEDGER = Path(__file__).resolve().parents[2] / "handover" / "MODULARIZATION" / "OPEN-ITEMS.md"
REGISTRY = Path(__file__).resolve().parents[2] / "handover" / "BACKLOG_REGISTRY.csv"
# Owner decision 2026-10-16: "needs owner decision" is not a final-gate exemption. An item is OPEN, CLOSED with evidence, or an
# owner-approved deferral to the backlog registry. NEEDS_OWNER_DECISION may exist while the workstream runs but never at Q1.
STATUSES = frozenset({"OPEN", "CLOSED", "NEEDS_OWNER_DECISION", "DEFERRED_BY_OWNER"})
_DEFERRAL = re.compile(r"backlog=(?P<id>[A-Z0-9-]+); reason=(?P<reason>.+); not-blocking=(?P<why>.+)")
_ROW = re.compile(r"^\| (OI-\d+) \|")


def parse(text: str) -> List[Dict[str, str]]:
    rows = []
    for line in text.splitlines():
        if _ROW.match(line):
            cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
            keys = ("id", "kind", "item", "found_in", "closes_in", "status", "evidence")
            rows.append(dict(zip(keys, cells, strict=True)) if len(cells) == len(keys) else {"id": cells[0], "malformed": line})
    return rows


def registry_ids() -> Dict[str, str]:
    with REGISTRY.open(newline="") as handle:
        return {row["id"]: row["status"] for row in csv.DictReader(handle)}


def problems(rows: List[Dict[str, str]], done: FrozenSet[str], slices: FrozenSet[str],
             registry: Dict[str, str] | None = None) -> List[str]:
    found, seen = [], set()
    for row in rows:
        ident = row["id"]
        if "malformed" in row:
            found.append(f"{ident}: not a 7-cell row")
            continue
        if ident in seen:
            found.append(f"{ident}: duplicate id")
        seen.add(ident)
        if row["closes_in"] not in slices:
            found.append(f"{ident}: closing slice {row['closes_in']!r} is not a plan slice")
        if row["status"] not in STATUSES:
            found.append(f"{ident}: status {row['status']!r}")
        if row["status"] == "CLOSED" and row["evidence"] in ("", "-"):
            found.append(f"{ident}: CLOSED without closure evidence")
        if row["status"] in ("OPEN", "NEEDS_OWNER_DECISION") and row["closes_in"] in done:
            found.append(f"{ident}: DUE - its closing slice {row['closes_in']} is DONE and the item is still {row['status']}")
        if row["status"] == "DEFERRED_BY_OWNER":
            match = _DEFERRAL.fullmatch(row["evidence"])
            if match is None:
                found.append(f"{ident}: DEFERRED_BY_OWNER needs 'backlog=<id>; reason=...; not-blocking=...'")
            elif registry is not None and match["id"] not in registry:
                found.append(f"{ident}: deferred to backlog id {match['id']} that is not in BACKLOG_REGISTRY.csv")
    return found


def final_gate_problems(rows: List[Dict[str, str]]) -> List[str]:
    """Q1 cannot pass with OPEN > 0, NEEDS_OWNER_DECISION > 0 or a closure without evidence (owner, 2026-10-16)."""
    found = []
    for row in rows:
        if "malformed" in row:
            continue
        if row["status"] in ("OPEN", "NEEDS_OWNER_DECISION"):
            found.append(f"{row['id']}: {row['status']} at the final gate")
        if row["status"] in ("CLOSED", "DEFERRED_BY_OWNER") and row["evidence"] in ("", "-"):
            found.append(f"{row['id']}: {row['status']} without evidence at the final gate")
    return found


def test_every_known_item_names_a_closing_slice_and_none_is_due_and_open():
    rows = parse(LEDGER.read_text())
    assert len(rows) >= 30, "the ledger lost rows"
    assert problems(rows, DONE_SLICES, SLICES, registry_ids()) == []


def test_q1_cannot_be_accepted_while_any_item_is_open_undecided_or_unevidenced():
    if "Q1" in DONE_SLICES:
        assert final_gate_problems(parse(LEDGER.read_text())) == []


def test_a_ledger_item_with_a_registry_id_is_not_also_open_in_the_registry():
    """Ledger = issues resolved during the workstream; registry = debt that survives it. Never both OPEN: cross-reference instead."""
    registry = registry_ids()
    cited = set(re.findall(r"Registry: ([A-Z0-9-]+)", LEDGER.read_text()))
    assert cited and cited <= set(registry)
    assert [i for i in cited if registry[i] == "OPEN"] == []


def test_the_rule_bites_on_planted_rows():
    text = "\n".join([
        "| OI-90 | k | i | f | C3 | OPEN | - |",
        "| OI-91 | k | i | f | ZZ9 | OPEN | - |",
        "| OI-92 | k | i | f | Q1 | CLOSED | - |",
        "| OI-92 | k | i | f | Q1 | OPEN | - |",
        "| OI-93 | k | i | f | OWNER | OPEN | - |",
        "| OI-96 | k | i | f | Q1 | DEFERRED_BY_OWNER | no reason |",
        "| OI-97 | k | i | f | Q1 | DEFERRED_BY_OWNER | backlog=NOPE-001; reason=r; not-blocking=w |",
        "| OI-98 | k | i | f | Q1 | DEFERRED_BY_OWNER | backlog=REAL-001; reason=r; not-blocking=w |",
        "| OI-99 | k | i | f | Q1 | NEEDS_OWNER_DECISION | - |",
        "| OI-94 | k | i | f | Q1 | WONTFIX | - |",
        "| OI-95 | too | few |",
    ])
    messages = problems(parse(text), DONE_SLICES, SLICES, {"REAL-001": "OPEN"})
    assert any("OI-90" in m and "DUE" in m for m in messages)
    assert any("OI-91" in m and "not a plan slice" in m for m in messages)
    assert any("OI-93" in m and "not a plan slice" in m for m in messages), "OWNER is no longer a closing slice"
    assert any("OI-96" in m and "DEFERRED_BY_OWNER needs" in m for m in messages)
    assert any("OI-97" in m and "not in BACKLOG_REGISTRY" in m for m in messages)
    assert not any("OI-98" in m for m in messages)
    assert not any("OI-99" in m for m in messages), "NEEDS_OWNER_DECISION is allowed while the workstream runs"
    assert any("OI-92" in m and "without closure evidence" in m for m in messages)
    assert any("OI-92" in m and "duplicate" in m for m in messages)
    assert any("OI-94" in m and "status" in m for m in messages)
    assert any("OI-95" in m and "7-cell" in m for m in messages)
    gate = final_gate_problems(parse(text))
    assert any("OI-99" in m and "final gate" in m for m in gate)
    assert any("OI-90" in m and "final gate" in m for m in gate)
    assert any("OI-92" in m and "without evidence" in m for m in gate)
    assert not any("OI-98" in m for m in gate)
