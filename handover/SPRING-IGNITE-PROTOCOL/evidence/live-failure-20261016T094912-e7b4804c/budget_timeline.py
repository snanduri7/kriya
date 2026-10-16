"""Reconstructs where the 900 s generation budget of live run 20261016T094912-e7b4804c went, from the preserved Kriya log slice (no model, no network).

Usage: python budget_timeline.py <kriya-log-slice file>
"""
from __future__ import annotations

import re
import sys
from datetime import datetime

LOG = open(sys.argv[1], encoding="utf-8").read().splitlines()


def at(pattern: str, nth: int = 0) -> datetime:
    hits = [line for line in LOG if re.search(pattern, line)]
    return datetime.strptime(hits[nth][:23], "%Y-%m-%d %H:%M:%S,%f")


def seconds(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds(), 1)


clock_start = at(r"Sending completion request to local LLM \[Model: qwen3-coder:30b, Stream: False, JSON Mode: True")       # the run's first model call = the budget clock
planning_end = at(r"Plan validation: PASSED")
gen1_start = at(r"Developer: generating content for 'pom.xml'")
gen1_end = at(r"Committed generated/edited candidate to sandbox: src/main/resources/applicationContext.xml")
human_start = at(r"Escalating changes to human approval gate")
human_end = at(r"Quality Gates: Running polymorphic compiler")
gates1_end = at(r"Targeted retry 1/3")
retry1_end = at(r"Targeted retry 2/3")
retry2_end = at(r"Targeted retry 3/3")
retry3_end = at(r"Trying ONE targeted fix on fallback model")
decision = retry3_end  # the fallback pass is refused immediately after the third retry ends
rows = [
    ("planning (2 planner calls, repair of a schema-invalid first plan)", seconds(clock_start, planning_end)),
    ("attempt 1 Developer: 4 files, one model call each", seconds(gen1_start, gen1_end)),
    ("attempt 1 reviewer + static checks (before the human gate)", seconds(gen1_end, human_start)),
    ("HUMAN source review wait (owner reading and approving)", seconds(human_start, human_end)),
    ("attempt 1 gates + attribution", seconds(human_end, gates1_end)),
    ("attempt 2 (retry 1/3: ProtocolParser only)", seconds(gates1_end, retry1_end)),
    ("attempt 3 (retry 2/3: Protocol + ProtocolParser)", seconds(retry1_end, retry2_end)),
    ("attempt 4 (retry 3/3: Protocol + ProtocolParser)", seconds(retry2_end, retry3_end)),
]
total = seconds(clock_start, decision)
print(f"budget clock starts at the first model call ({clock_start.time()}); the fallback pass was refused at {decision.time()}: {total} s consumed of 900 s")
for label, value in rows:
    print(f"  {value:7.1f} s  {label}")
print(f"  sum of rows {round(sum(v for _l, v in rows), 1)} s; unattributed gaps {round(total - sum(v for _l, v in rows), 1)} s")
remaining = 900 - total
print(f"remaining {remaining:.1f} s vs required 300.0 s (2 files x 90 s configured estimate for the never-used fallback model + 120 s gate reserve)")
print(f"if the human wait were not counted: remaining would have been {remaining + rows[3][1]:.1f} s -> the fallback pass would have started")
