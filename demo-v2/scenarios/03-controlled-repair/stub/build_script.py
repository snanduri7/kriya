#!/usr/bin/env python3
"""Build the replay script from a `record` mode log made with --override-first-developer: every answer verbatim as recorded, with the overridden Developer answer labelled
SCRIPTED (the wrong candidate). The result is a fixed script; re-running this tool on the same log gives the same file (provenance is recorded inside it).
usage: build_script.py <record.jsonl> <out.json> [--recorded-model NAME] [--recorded-on DATE]"""
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("log")
parser.add_argument("out")
parser.add_argument("--recorded-model", default="unknown")
parser.add_argument("--recorded-on", default="unknown")
args = parser.parse_args()
rows = [json.loads(line) for line in open(args.log, encoding="utf-8") if line.strip()]
steps, seen = [], {}
for row in rows:
    if row.get("kind") != "chat":
        continue
    role = row["role"]
    index = seen.get(role, 0)
    seen[role] = index + 1
    content = row["response"]["content"]
    label = "SCRIPTED: plausible but wrong candidate (returns the discount amount, not the discounted price)" if row.get("overridden") else "recorded verbatim"
    steps.append({"role": role, "label": label, "content": content})
script = {"controlled": True, "notice": "CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO: these answers are scripted, not produced by a model now.",
          "recorded_from": {"model": args.recorded_model, "date": args.recorded_on, "note": "one real run on the controlled-repair fixture through the recording proxy; only the first Developer answer was scripted (the wrong candidate)"},
          "steps": steps}
with open(args.out, "w", encoding="utf-8") as handle:
    json.dump(script, handle, indent=1)
    handle.write("\n")
print(f"{len(steps)} steps -> {args.out}")
