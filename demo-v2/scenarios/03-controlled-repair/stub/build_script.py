#!/usr/bin/env python3
"""Build the replay script from a `record` mode log made with --override: every answer verbatim as recorded, with each overridden answer labelled SCRIPTED (its label comes from the log). The result is a fixed script; re-running this tool on the same log gives the same file (provenance is recorded inside it).
usage: build_script.py <record.jsonl> <out.json> [--recorded-model NAME] [--recorded-on DATE] [--drop-role-prefix PREFIX]...
  --drop-role-prefix   drop every step whose role starts with PREFIX (the steps that belonged to a unit the replacement plan no longer has)"""
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("log")
parser.add_argument("out")
parser.add_argument("--recorded-model", default="unknown")
parser.add_argument("--recorded-on", default="unknown")
parser.add_argument("--drop-role-prefix", action="append", default=[])
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
    if any(role.startswith(prefix) for prefix in args.drop_role_prefix):
        continue
    label = row.get("override_label", "SCRIPTED") if row.get("overridden") else "recorded verbatim"
    steps.append({"role": role, "label": label, "content": content})
script = {"controlled": True, "notice": "CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO: these answers are scripted, not produced by a model now.",
          "recorded_from": {"model": args.recorded_model, "date": args.recorded_on, "note": "real answers recorded through the recording proxy on the controlled-repair fixture. Two answers are scripted: the Planner answer (a single-unit plan recorded earlier from a real run of the same goal) and the first Developer answer (the wrong candidate). Every other answer is the real model's, produced for the path those two answers cause"},
          "steps": steps}
with open(args.out, "w", encoding="utf-8") as handle:
    json.dump(script, handle, indent=1)
    handle.write("\n")
print(f"{len(steps)} steps -> {args.out}")
