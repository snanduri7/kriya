"""P3 anchor analysis: for every anchored-edit failure in the LR-R1 live stores,
check each SEARCH block against (a) the file the edit targeted (the attempt's
base bytes, from the gate outcome's failed_content) and (b) the exact Developer
request the model was shown (model.request messages blob). Read-only.

Per SEARCH block:
- in_file:        verbatim in the target file
- in_prompt:      verbatim in the request text
- lines_in_file / lines_in_prompt: share of the block's non-blank lines found
- elision:        the block is in the prompt but not the file -> Kriya showed
                  text that is not the file's bytes (skeleton/elided view)
- invented:       in neither
Also: whether the request carried the whole target file verbatim.

usage: python p3_anchor_analysis.py > p3_anchor_analysis.json
"""
import gzip
import json
import os
import sys

from p3_census import RUNS, STORES

ANCHOR_CODES = {"ANCHOR_NOT_IN_FILE", "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT", "ANCHOR_AMBIGUOUS",
                "ANCHOR_NOT_FOUND"}


def load(store):
    records = [json.loads(line) for line in open(os.path.join(store, "records.jsonl"))]

    def blob(digest):
        hexd = digest.split(":", 1)[1]
        path = os.path.join(store, "blobs", hexd[:2], hexd + ".gz")
        return gzip.decompress(open(path, "rb").read()).decode("utf-8", "replace")
    return records, blob


def lines_share(block, text):
    lines = [line for line in block.split("\n") if line.strip()]
    if not lines:
        return 1.0
    return round(sum(1 for line in lines if line in text) / len(lines), 2)


def analyse(label, run_id):
    store = os.path.join(STORES, run_id)
    records, blob = load(store)
    out = []
    by_attempt = {}
    for record in records:
        key = (record.get("unit_id"), record.get("attempt_number"))
        by_attempt.setdefault(key, []).append(record)
    for (unit, attempt), recs in by_attempt.items():
        diagnosis = next((r for r in recs if r["kind"] == "diagnosis"), None)
        if diagnosis is None or diagnosis["payload"].get("reason_code") not in ANCHOR_CODES:
            continue
        requests = [r for r in recs if r["kind"] == "model.request" and r.get("role") == "developer"]
        outcome_rec = next((r for r in recs if r["kind"] == "mirror.gate_outcome"
                            and (r.get("blobs") or {}).get("outcome")), None)
        outcome = json.loads(blob(outcome_rec["blobs"]["outcome"])) if outcome_rec else {}
        prompt = ""
        if requests:
            messages = json.loads(blob(requests[-1]["blobs"]["messages"]))
            prompt = "\n".join(m["content"] for m in messages)
        failed = outcome.get("failed_content") or {}
        target = next(iter(failed), None)
        source = failed.get(target, "") if target else ""
        blocks = []
        for edit in outcome.get("attempted_edits") or []:
            search = edit.get("search") or ""
            in_file, in_prompt = search in source, search in prompt
            blocks.append({"in_file": in_file, "in_prompt": in_prompt,
                           "lines_in_file": lines_share(search, source),
                           "lines_in_prompt": lines_share(search, prompt),
                           "class": ("ok" if in_file else "elision" if in_prompt else "invented"),
                           "search_head": search[:160]})
        out.append({"run": label, "unit": unit, "attempt": attempt,
                    "reason_code": diagnosis["payload"].get("reason_code"),
                    "model": requests[-1]["payload"].get("model") if requests else None,
                    "target": target, "target_bytes": len(source.encode()),
                    "prompt_chars": len(prompt), "whole_file_in_prompt": bool(source) and source in prompt,
                    "blocks": blocks})
    return out


def main():
    rows = []
    for label, run_id in RUNS.items():
        rows.extend(analyse(label, run_id))
    json.dump(rows, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
