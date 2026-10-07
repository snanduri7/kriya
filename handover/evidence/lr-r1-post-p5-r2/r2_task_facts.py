"""LR-R1 R2: per-task facts from the evidence store and summary (harness, read-only)."""
import json
import sys

from kriya.core.attempt_evidence import reader

ws = sys.argv[1]
s = json.load(open(f"evidence/{ws}/summary.json"))
r = reader.open_run("state", s["run_id"])
rec = list(r.records())
cur = {}
for x in rec:
    if x.get("attempt_number") is None:
        continue
    k, p = x["kind"], x["payload"]
    d = cur.setdefault((x.get("unit_id"), x.get("attempt_number")), {})
    if k == "attempt.opened": d["mode"] = p.get("mode")  # noqa: E701 - evidence script kept as run
    if k == "fallback.decision" and p.get("phase") == "call": d["model"] = (p.get("selected") or "")[:12]  # noqa: E701 - evidence script kept as run
    if k == "candidate.change": d.setdefault("cand", []).append((p.get("decision"), (p.get("path") or "")[-30:], p.get("reason_code")))  # noqa: E701 - evidence script kept as run
    if k == "gate.result": d.setdefault("gates", []).append((p.get("stage"), p.get("gate"), p.get("success")))  # noqa: E701 - evidence script kept as run
    if k == "diagnosis": d["diag"] = (p.get("type"), p.get("reason_code"))  # noqa: E701 - evidence script kept as run
    if k == "recovery.decision": d["next"] = (p.get("action"), p.get("stop_reason_code"), p.get("no_progress_reason"))  # noqa: E701 - evidence script kept as run
term_gates = [(x["payload"].get("gate"), x["payload"].get("success")) for x in rec
              if x["kind"] == "gate.result" and x["payload"].get("stage") == "terminal"]
ev = [x["payload"].get("kind") for x in rec if x["kind"] == "mirror.event"]
print(json.dumps({"run_id": s["run_id"], "exit": s["generate_exit"], "terminal": s["Q9"].get("terminal_cause"),
                  "items": s["Q9"].get("items"), "attempts": s["attempts"], "model_calls": s["model_calls"],
                  "ids": [(m["role"], m["model"][:12], m["calls"]) for m in s["model_identities"]],
                  "wall": round(s["wall_seconds"], 1), "model_s": s["model_seconds"], "recorder_s": round(s["recorder_seconds"], 3),
                  "verify": s["verify"]["status"], "terminal_gates": term_gates,
                  "admissions": ev.count("retry.verification_admission"), "integration": ev.count("integration.obligation"),
                  "routing": sum(1 for x in rec if x["kind"] == "fallback.decision" and x["payload"].get("phase") == "routing"),
                  "blank": s.get("blank_answers")}, default=str))
for key, d in cur.items():
    print(key, d)
