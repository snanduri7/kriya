#!/usr/bin/env python3
"""Replay-driver mechanics proof on HISTORICAL V2 evidence (MODEL ROUTING V1, Stage 2). No model is called; no quality is compared.

Proves, for one sealed attempt-evidence store, that the experiment tooling can
  1. load the exact Planner request, the exact Developer attempt-1 request and the exact Developer retry request, and identify the
     CI-6 localization request separately (role=localization, response_format=json_schema) with its link to the next Developer attempt;
  2. preserve the messages byte-for-byte and the wire semantics (the production runtime rebuilds the wire body from the recorded
     messages; the request is captured and aborted BEFORE any network I/O);
  3. route the RECORDED response through the production parser for the role (planner / developer structured / CI-6 decision);
  4. materialize the recorded Developer edits through the production edit engine in a scratch copy, compare the result with the
     recorded candidate bytes, and run the existing task oracle on that scratch tree;
  5. leave the historical store and workspace untouched (sha256 before == after).

Usage: replay_mechanics.py --store DIR --config YAML --workspace DIR --task-dir DIR --out FILE
"""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_tree(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and ".git/" not in str(p) and "/.kriya" not in str(p):
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def load_blob(store, digest):
    name = digest.split(":", 1)[1]
    for path in Path(store, "blobs").rglob("*"):
        if path.is_file() and name in path.name:
            raw = path.read_bytes()
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
            return raw
    raise FileNotFoundError(digest)


class _Captured(BaseException):
    """Raised from the wire-capture hook so the request is abandoned before any network I/O."""

    def __init__(self, payload):
        super().__init__("captured")
        self.payload = payload


def classify(rec):
    role, phase, fmt = rec.get("role"), rec.get("phase"), rec["payload"].get("response_format")
    if role == "localization" and fmt == "json_schema":
        return "CI6_LOCALIZATION"
    if role == "planner":
        return "PLANNER"
    if role == "developer":
        n = rec.get("attempt_number")
        return "DEVELOPER_ATTEMPT_1" if n == 1 else f"DEVELOPER_RETRY_{n}"
    return f"OTHER:{role}"


def index(records):
    """Per model.request: class, ids, and (CI-6) the next Developer attempt in the same unit."""
    requests = [r for r in records if r["kind"] == "model.request"]
    out = []
    for r in requests:
        row = {"seq": r["seq"], "call_seq": r.get("call_seq"), "class": classify(r), "role": r.get("role"),
               "unit_id": r.get("unit_id"), "attempt_number": r.get("attempt_number"), "model": r["payload"]["model"],
               "response_format": r["payload"].get("response_format")}
        if row["class"] == "CI6_LOCALIZATION":
            nxt = next((x for x in requests if x["seq"] > r["seq"] and x.get("role") == "developer" and x.get("unit_id") == r.get("unit_id")), None)
            row["next_developer_seq"] = nxt["seq"] if nxt else None
            row["next_developer_attempt"] = nxt.get("attempt_number") if nxt else None
        out.append(row)
    return out


def response_for(records, request):
    return next((r for r in records if r["kind"] == "model.response" and r.get("call_seq") == request.get("call_seq")
                 and r.get("wire_seq") == request.get("wire_seq") and r["seq"] > request["seq"]), None)


async def rebuild_wire(config_path, workspace, messages, request, streamed=False):
    """Production LLMClient builds the wire body from the recorded messages; capture + abort before the network."""
    from kriya.config.config import load_config
    from kriya.core import llm as llm_module
    from kriya.core.attempt_evidence import scope as evidence_scope

    os.chdir(workspace)
    client = llm_module.LLMClient(load_config(config_path))

    def capture(runtime, wire_request):
        raise _Captured(runtime.wire_payload(wire_request, stream=wire_request.stream_callback is not None))

    evidence_scope.record_wire_request = capture
    kwargs = {"stream_callback": (lambda _chunk: None) if streamed else None,
              "json_mode": request["payload"].get("response_format") == "json_object",
              "model_override": request["payload"]["model"], "max_tokens_override": request["payload"].get("max_tokens")}
    if request["payload"].get("response_format") == "json_schema":
        from kriya.workflow import localization_decision as loc
        ids = re.findall(r"\bid=(\S+) \[", messages[1]["content"])
        kwargs["response_schema"] = loc.decision_schema(ids)
    try:
        await client.complete_result(messages[0]["content"], messages[1]["content"], **kwargs)
    except _Captured as cap:
        return cap.payload
    return None


def diff_keys(a, b, prefix=""):
    out = []
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        if isinstance(va, dict) and isinstance(vb, dict):
            out += diff_keys(va, vb, f"{prefix}{key}.")
        elif va != vb:
            out.append(f"{prefix}{key}")
    return out


def parse_recorded(store, records, request, content):
    from kriya.agents import contracts, response_protocol
    cls = classify(request)
    if cls == "PLANNER":
        parsed, error = contracts.parse_planner_structured_output(content)
        return {"parser": "parse_planner_structured_output", "ok": parsed is not None, "error": error}
    if cls == "CI6_LOCALIZATION":
        from kriya.workflow import localization_decision as loc
        messages = json.loads(load_blob(store, request["blobs"]["messages"]))
        ids = re.findall(r"\bid=(\S+) \[", messages[1]["content"])
        try:
            decision = loc.parse_decision(content, ids, lambda _i: True)
            return {"parser": "parse_decision", "ok": True, "candidate_set_size": len(ids), "targets": list(decision.target_symbol_ids)}
        except loc.DecisionRejected as rej:
            return {"parser": "parse_decision", "ok": False, "reason_code": rej.reason_code, "candidate_set_size": len(ids)}
    parse_rec = next((r for r in records if r["kind"] == "developer.parse" and r["payload"].get("call_seq_parsed") == request.get("call_seq")), None)
    if parse_rec is None:
        return {"parser": "parse_structured", "skipped": "no developer.parse record for this call"}
    path = parse_rec["payload"]["path"]
    resp = response_protocol.parse_structured(content, path)
    return {"parser": "parse_structured", "path": path, "kind": resp.kind, "recorded_kind": parse_rec["payload"].get("kind"),
            "kind_matches_recorded": resp.kind == parse_rec["payload"].get("kind"),
            "edit_count": len(resp.edits), "recorded_edit_count": parse_rec["payload"].get("edit_count"), "_resp": resp}


def materialize(store, records, request, resp, task_dir, report):
    """Recorded Developer edits -> production edit engine -> compare with recorded candidate bytes -> run the existing oracle."""
    from kriya.workflow.file_integrity import AnchoredReplace, apply_line_block_edits
    change = next((r for r in records if r["kind"] == "candidate.change" and r.get("attempt_number") == request.get("attempt_number")
                   and r.get("unit_id") == request.get("unit_id") and r["seq"] > request["seq"]), None)
    if change is None or not resp.edits:
        report["skipped"] = "no candidate.change / no edits for this call"
        return
    before = load_blob(store, change["blobs"]["before"]).decode("utf-8")
    after = load_blob(store, change["blobs"]["after"]).decode("utf-8")
    edits = [AnchoredReplace(operation_id=f"#{i}", search=s, replace=r) for i, (s, r) in enumerate(resp.edits)]
    result = apply_line_block_edits(before, edits)
    text = getattr(result, "text", None) or getattr(result, "content", None)
    report["engine_output_equals_recorded_after"] = text == after
    report["candidate_path"] = change["payload"].get("path")
    ws = Path(task_dir) / "ws"
    base_rev = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=ws, capture_output=True, text=True).stdout.split()[0]
    scr = Path(tempfile.mkdtemp(prefix="replay_scratch_"))
    try:
        tar = subprocess.run(["git", "archive", base_rev], cwd=ws, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(scr)], input=tar, check=True)
        target = scr / report["candidate_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text if text is not None else after, encoding="utf-8")
        oscr = Path(tempfile.mkdtemp(prefix="replay_oracle_"))
        shutil.rmtree(oscr)
        shutil.copytree(scr, oscr)
        accept = subprocess.run([str(Path(task_dir) / "oracle/accept.sh"), str(scr), str(oscr)], capture_output=True, text=True, timeout=900)
        report["oracle_accept_exit_on_scratch"] = accept.returncode
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "BACKEND-READINESS-V2/gate-certification"))
        from oracle_preflight import classify_run
        report["oracle_verdict_on_scratch"] = classify_run(accept.returncode, accept.stdout + accept.stderr)[0]
        shutil.rmtree(oscr, ignore_errors=True)
    finally:
        shutil.rmtree(scr, ignore_errors=True)


async def main_async(args):
    store, task_dir = Path(args.store), Path(args.task_dir)
    before = {"records": sha_file(store / "records.jsonl"), "workspace": sha_tree(task_dir / "ws")}
    records = [json.loads(line) for line in (store / "records.jsonl").open()]
    idx = index(records)
    report = {"store": store.name, "request_classes": {}, "ci6_links": [r for r in idx if r["class"] == "CI6_LOCALIZATION"][:5], "samples": {}}
    for row in idx:
        report["request_classes"][row["class"]] = report["request_classes"].get(row["class"], 0) + 1
    picks = {}
    for row in idx:
        c = row["class"]
        if c in ("PLANNER", "CI6_LOCALIZATION", "DEVELOPER_ATTEMPT_1") or c.startswith("DEVELOPER_RETRY_"):
            picks.setdefault("DEVELOPER_RETRY" if c.startswith("DEVELOPER_RETRY_") else c, row)
    for cls, row in picks.items():
        request = next(r for r in records if r["kind"] == "model.request" and r["seq"] == row["seq"])
        messages_raw = load_blob(store, request["blobs"]["messages"])
        digest_ok = "sha256:" + hashlib.sha256(messages_raw).hexdigest() == request["blobs"]["messages"]
        messages = json.loads(messages_raw)
        sample = {"seq": row["seq"], "model": row["model"], "unit_id": row["unit_id"], "attempt_number": row["attempt_number"],
                  "messages_digest_matches_record": digest_ok, "message_roles": [m["role"] for m in messages]}
        recorded_wire = json.loads(load_blob(store, request["blobs"]["wire_body"]))
        try:
            wire = await rebuild_wire(args.config, args.workspace, messages, request, streamed=bool(recorded_wire.get("stream")))
        except Exception as exc:  # report the failure honestly
            wire, sample["wire_rebuild_error"] = None, f"{type(exc).__name__}: {str(exc)[:200]}"
        if wire is not None:
            sample["wire_messages_identical_to_recorded"] = wire.get("messages") == recorded_wire.get("messages")
            sample["wire_other_fields_differing"] = diff_keys({k: v for k, v in wire.items() if k != "messages"},
                                                              {k: v for k, v in recorded_wire.items() if k != "messages"})
        resp_rec = response_for(records, request)
        if resp_rec is not None:
            content = load_blob(store, next(iter(resp_rec["blobs"].values()))).decode("utf-8")
            parsed = parse_recorded(store, records, request, content)
            resp = parsed.pop("_resp", None)
            sample["parse"] = parsed
            if cls == "DEVELOPER_ATTEMPT_1" and resp is not None:
                mat = {}
                materialize(store, records, request, resp, task_dir, mat)
                sample["materialize"] = mat
        report["samples"][cls] = sample
    after = {"records": sha_file(store / "records.jsonl"), "workspace": sha_tree(task_dir / "ws")}
    report["historical_evidence_unchanged"] = before == after
    return report


def main():
    ap = argparse.ArgumentParser()
    for name in ("store", "config", "workspace", "task_dir", "out"):
        ap.add_argument("--" + name.replace("_", "-"), dest=name, required=True)
    args = ap.parse_args()
    report = asyncio.run(main_async(args))
    Path(args.out).write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1)[:6000])
    return 0 if report["historical_evidence_unchanged"] else 3


if __name__ == "__main__":
    sys.exit(main())
