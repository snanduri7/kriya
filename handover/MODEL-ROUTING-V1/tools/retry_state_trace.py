"""Experiment-only, read-only: does a Developer retry prompt carry the previous attempt's model-written text?

For one sealed attempt-evidence store: for every attempt k, the fraction of the previous attempt's response 5-grams found in
attempt k's request text (system+user), plus a control (attempt k-1 request vs attempt k request). Usage: retry_state_trace.py STORE_DIR
"""
import glob
import gzip
import json
import os
import re
import sys
from collections import defaultdict


def blob(store, digest):
    name = digest.split(":", 1)[1]
    for path in glob.glob(store + "/blobs/**/*", recursive=True):
        if os.path.isfile(path) and name in path:
            raw = open(path, "rb").read()
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
            return raw.decode("utf-8", "replace")
    return ""


def grams(text):
    words = re.findall(r"[A-Za-z_]{3,}", text)
    return {" ".join(words[i:i + 5]) for i in range(len(words) - 4)}


def main(store):
    records = [json.loads(line) for line in open(store + "/records.jsonl")]
    requests = {r["call_seq"]: r for r in records if r["kind"] == "model.request"}
    responses, prompts = defaultdict(list), defaultdict(list)
    for req in requests.values():
        if req.get("role") != "developer":
            continue
        attempt = req.get("attempt_number")
        prompts[attempt].append(blob(store, req["blobs"]["messages"]))
    for r in records:
        if r["kind"] == "model.response" and requests.get(r["call_seq"], {}).get("role") == "developer":
            responses[requests[r["call_seq"]].get("attempt_number")].append(blob(store, next(iter(r["blobs"].values()))))
    for attempt in sorted(a for a in prompts if a):
        prev = grams(" ".join(responses.get(attempt - 1, [])))
        now = " ".join(prompts[attempt])
        hits = [g for g in prev if g in now]
        control_base = grams(" ".join(prompts.get(attempt - 1, [])))
        control = sum(1 for g in control_base if g in now)
        print(json.dumps({"attempt": attempt, "prev_response_5grams": len(prev), "found_in_this_prompt": len(hits),
                          "sample": hits[:3], "control_prev_prompt_5grams": len(control_base), "control_found": control}))


if __name__ == "__main__":
    main(sys.argv[1])
