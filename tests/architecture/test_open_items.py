"""OPEN-ITEMS.md is the one ledger of every known issue, bug, gap and deferral of the full-modularization workstream (owner directive
2026-10-16). Each row names the slice that closes it; an OPEN row whose slice is already DONE fails here, so nothing is forgotten.
The planted self-check proves the rule is not vacuous."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, FrozenSet, List

from tests.architecture.test_import_direction import DONE_SLICES, SLICES

LEDGER = Path(__file__).resolve().parents[2] / "handover" / "MODULARIZATION" / "OPEN-ITEMS.md"
OWNER = "OWNER"
STATUSES = frozenset({"OPEN", "CLOSED"})
_ROW = re.compile(r"^\| (OI-\d+) \|")


def parse(text: str) -> List[Dict[str, str]]:
    rows = []
    for line in text.splitlines():
        if _ROW.match(line):
            cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
            keys = ("id", "kind", "item", "found_in", "closes_in", "status", "evidence")
            rows.append(dict(zip(keys, cells, strict=True)) if len(cells) == len(keys) else {"id": cells[0], "malformed": line})
    return rows


def problems(rows: List[Dict[str, str]], done: FrozenSet[str], slices: FrozenSet[str]) -> List[str]:
    found, seen = [], set()
    for row in rows:
        ident = row["id"]
        if "malformed" in row:
            found.append(f"{ident}: not a 7-cell row")
            continue
        if ident in seen:
            found.append(f"{ident}: duplicate id")
        seen.add(ident)
        if row["closes_in"] != OWNER and row["closes_in"] not in slices:
            found.append(f"{ident}: closing slice {row['closes_in']!r} is neither a plan slice nor OWNER")
        if row["status"] not in STATUSES:
            found.append(f"{ident}: status {row['status']!r}")
        if row["status"] == "CLOSED" and row["evidence"] in ("", "-"):
            found.append(f"{ident}: CLOSED without closure evidence")
        if row["status"] == "OPEN" and row["closes_in"] in done:
            found.append(f"{ident}: DUE - its closing slice {row['closes_in']} is DONE and the item is still OPEN")
    return found


def test_every_known_item_names_a_closing_slice_and_none_is_due_and_open():
    rows = parse(LEDGER.read_text())
    assert len(rows) >= 30, "the ledger lost rows"
    assert problems(rows, DONE_SLICES, SLICES) == []


def test_the_rule_bites_on_planted_rows():
    text = "\n".join([
        "| OI-90 | k | i | f | C3 | OPEN | - |",
        "| OI-91 | k | i | f | ZZ9 | OPEN | - |",
        "| OI-92 | k | i | f | Q1 | CLOSED | - |",
        "| OI-92 | k | i | f | Q1 | OPEN | - |",
        "| OI-93 | k | i | f | OWNER | OPEN | - |",
        "| OI-94 | k | i | f | Q1 | WONTFIX | - |",
        "| OI-95 | too | few |",
    ])
    messages = problems(parse(text), DONE_SLICES, SLICES)
    assert any("OI-90" in m and "DUE" in m for m in messages)
    assert any("OI-91" in m and "neither a plan slice" in m for m in messages)
    assert any("OI-92" in m and "without closure evidence" in m for m in messages)
    assert any("OI-92" in m and "duplicate" in m for m in messages)
    assert any("OI-94" in m and "status" in m for m in messages)
    assert any("OI-95" in m and "7-cell" in m for m in messages)
    assert not any("OI-93" in m for m in messages)
