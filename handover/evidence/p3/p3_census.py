"""P3 census: one row per Developer attempt of every LR-R1 live run (R1 v5-t1..t5,
R2 r2c-*), read-only from the sealed M1 attempt-evidence stores.

Per attempt: unit, attempt, mode/operation, Developer model(s) called and how
many calls, parse outcome, diagnosis reason code / failure type, candidate
decision, retry.delta information gain, recovery action.

usage: python p3_census.py > p3_census.json
"""
import gzip
import json
import os
import sys

STORES = os.path.expanduser("~/kriya-m1-live/state/attempt-evidence")
RUNS = {
    "R1-T1 httpx": "20261005T174403-633924ce", "R1-T2 moreit": "20261005T174757-165198e5",
    "R1-T3 lang-ascii": "20261005T180101-bf592824", "R1-T4 lang-countTrue": "20261005T183121-9434e9d6",
    "R1-T5 petclinic": "20261005T184002-494fa242",
    "R2-T1 inflect": "20261005T192055-bd4ff7df", "R2-T2 cssselect2": "20261005T192540-2f2806e9",
    "R2-T3 lang-a": "20261005T193554-02bd0660", "R2-T4 lang-b": "20261005T195809-6a6add6d",
    "R2-T5 spring-xml": "20261005T201054-868fc770",
}


def blob(store, digest):
    if not digest:
        return None
    hexd = digest.split(":", 1)[1]
    path = os.path.join(store, "blobs", hexd[:2], hexd + ".gz")
    if not os.path.exists(path):
        path = path[:-3]
        if not os.path.exists(path):
            return None
        return open(path, "rb").read().decode("utf-8", "replace")
    return gzip.decompress(open(path, "rb").read()).decode("utf-8", "replace")


def census(label, run_id):
    store = os.path.join(STORES, run_id)
    records = [json.loads(line) for line in open(os.path.join(store, "records.jsonl"))]
    attempts = {}
    order = []

    def row(record):
        key = (record.get("unit_id"), record.get("attempt_number"))
        if key not in attempts:
            attempts[key] = {"run": label, "unit": key[0], "attempt": key[1], "developer_calls": [],
                             "parse": [], "diagnosis": None, "candidate": [], "retry_delta": None,
                             "recovery": None, "mode": None, "operation": None, "fallback": []}
            order.append(key)
        return attempts[key]

    for record in records:
        kind, payload = record["kind"], record["payload"]
        if record.get("attempt_number") is None or record.get("unit_id") is None:
            continue
        r = row(record)
        if kind == "attempt.opened":
            r["mode"], r["operation"] = payload.get("mode"), payload.get("operation")
        elif kind == "model.request" and record.get("role") in ("developer", "developer_file_list", "file_list"):
            r["developer_calls"].append({"role": record.get("role"), "model": payload.get("model"),
                                         "dispatched": payload.get("dispatched")})
        elif kind == "developer.parse":
            r["parse"].append({k: payload.get(k) for k in ("kind", "path", "reason_code", "edit_count", "detail")})
        elif kind == "diagnosis":
            message = blob(store, (record.get("blobs") or {}).get("message"))
            r["diagnosis"] = {"type": payload.get("type"), "reason_code": payload.get("reason_code"),
                              "likely_files": payload.get("likely_files"),
                              "message": (message or "")[:600]}
        elif kind == "candidate.change":
            r["candidate"].append({k: payload.get(k) for k in ("path", "decision", "reason_code")})
        elif kind == "retry.delta":
            r["retry_delta"] = {k: payload.get(k) for k in ("information_gain", "changed", "reason")}
        elif kind == "recovery.decision":
            r["recovery"] = {k: payload.get(k) for k in ("action", "progress_classification",
                                                         "no_progress_reason", "stop_reason_code")}
        elif kind == "fallback.decision":
            r["fallback"].append({k: payload.get(k) for k in ("phase", "fallback", "selected", "requested_rejection")})
    return [attempts[k] for k in order]


def main():
    rows = []
    for label, run_id in RUNS.items():
        rows.extend(census(label, run_id))
    json.dump(rows, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
