"""LR-R1-M1 live validation: per-run measurements (harness, not Kriya code).

usage: venv/bin/python scripts/analyze_run.py <workspace name>
Reads evidence/<ws>/{wall,timing,verify,explain}.json and the run's store
(read-only, through Kriya's own reader) and writes evidence/<ws>/summary.json.
"""
import json
import os
import subprocess
import sys
from collections import Counter

from kriya.core.attempt_evidence import reader

D = os.path.expanduser("~/kriya-m1-live")
STATE = os.path.join(D, "state")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def main(ws):
    out = os.path.join(D, "evidence", ws)
    wall = load(os.path.join(out, "wall.json"), {})
    timing = load(os.path.join(out, "timing.json"), {})
    run_id = open(os.path.join(out, "run_id.txt"), encoding="utf-8").read().strip()
    summary = {"workspace": ws, "goal": open(os.path.join(out, "goal.txt"), encoding="utf-8").read().strip(),
               "run_id": run_id or None, "generate_exit": wall.get("generate_exit"),
               "wall_seconds": wall.get("wall_seconds"), "process_wall_seconds": timing.get("process_wall_seconds")}
    functions = timing.get("functions") or {}
    recorder = timing.get("recorder_seconds")
    summary["recorder_seconds"] = recorder
    summary["recorder_function_seconds"] = {k.rsplit(".", 1)[-1]: round(v["seconds"], 4) for k, v in functions.items()
                                            if v["calls"]}
    summary["recorder_function_calls"] = {k.rsplit(".", 1)[-1]: v["calls"] for k, v in functions.items() if v["calls"]}
    summary["missing_targets"] = [k for k in functions if k.startswith("MISSING:")]
    pick = lambda name: next((v for k, v in functions.items() if k.endswith("." + name)), {"calls": 0, "seconds": 0.0})
    summary["record_candidate_change"] = pick("_record_candidate_change")
    summary["close_run"] = pick("close_run")
    summary["prune_after_run"] = pick("prune_after_run")
    if not run_id:
        summary["store"] = "NO STORE (capture off or no run)"
        _finish(out, summary, wall, recorder, model_seconds=None)
        return
    run = reader.open_run(STATE, run_id)
    records = list(run.records())
    verification = run.verify()
    summary["verify"] = {"status": verification.status, "sealed": verification.sealed}
    summary["records"] = len(records)
    summary["records_by_kind"] = dict(Counter(r["kind"] for r in records).most_common())
    size = subprocess.run(["du", "-sk", run.directory], capture_output=True, text=True).stdout.split()[0]
    summary["store_kib"] = int(size)
    manifest = run.manifest()
    summary["capture"] = manifest.get("capture")
    results = [r["payload"] for r in records if r["kind"] == "model.result"]
    model_seconds = sum(float(p.get("elapsed_seconds") or 0.0) for p in results)
    summary["model_calls"] = len(results)
    summary["model_seconds"] = round(model_seconds, 2)
    identities = Counter((r.get("role") or "?", p.get("model"), (p.get("runtime_fingerprint") or "")[:16],
                          p.get("runtime_fingerprint_exact"), (p.get("inference_settings_digest") or "")[:16])
                         for r, p in ((r, r["payload"]) for r in records if r["kind"] == "model.result"))
    summary["model_identities"] = [{"role": k[0], "model": k[1], "runtime": k[2], "exact": k[3], "settings": k[4],
                                    "calls": n} for k, n in identities.items()]
    explained = load(os.path.join(out, "explain.json"), {})
    completeness = Counter()
    absent = []
    for attempt in explained.get("attempts", []):
        for question, answer in attempt["answers"].items():
            status = answer.get("status") if isinstance(answer, dict) else None
            completeness[(question, status)] += 1
            if status in ("NOT_RECORDED", "NOT_APPLICABLE"):
                absent.append({"unit": attempt["unit_id"], "attempt": attempt["attempt"], "question": question,
                               "status": status, "reason": answer.get("reason")})
    q9 = explained.get("Q9") or {}
    summary["attempts"] = len(explained.get("attempts", []))
    summary["calls_outside_attempts"] = explained.get("calls_outside_attempts")
    summary["completeness"] = {f"{q}:{s}": n for (q, s), n in sorted(completeness.items(), key=str)}
    summary["absent_answers"] = absent
    summary["blank_answers"] = [a for a in absent if not (a["reason"] or "").strip()]
    summary["Q9"] = q9
    _finish(out, summary, wall, recorder, model_seconds)


def _finish(out, summary, wall, recorder, model_seconds):
    w = wall.get("wall_seconds")
    if w and recorder is not None:
        summary["recorder_pct_wall"] = round(100.0 * recorder / w, 3)
        if model_seconds is not None:
            summary["non_model_seconds"] = round(w - model_seconds, 2)
            summary["recorder_pct_non_model"] = round(100.0 * recorder / (w - model_seconds), 3)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1, default=str)
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
