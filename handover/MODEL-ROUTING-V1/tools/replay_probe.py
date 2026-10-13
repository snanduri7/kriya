"""Experiment-only replay-mechanics probe (MODEL ROUTING V1, Stage 1). Not product code; reads sealed stores, never writes them.

Loads ONE recorded ``model.request`` from an attempt-evidence store, sends its exact system/user messages through the
production ``LLMClient`` (so the target model gets its OWN binding from ``--config``: adapter, sampling, window, qualification
identity) and parses the answer with the production parser for the role:
  planner   -> kriya.agents.contracts.parse_planner_structured_output
  developer -> kriya.agents.response_protocol.parse_structured (path taken from the recorded ``developer.parse`` record)
It records, outside the store: the wire body actually posted vs the recorded one, the parse verdict, and the separated timings
(load / prefill+inference / total). It reports protocol outcomes only; it never ranks models.

Usage: replay_probe.py --store DIR --seq N --config YAML --model TAG --out FILE [--workspace DIR]
"""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_blob(store, digest):
    name = digest.split(":", 1)[1]
    for path in Path(store, "blobs").rglob("*"):
        if path.is_file() and name in path.name:
            raw = path.read_bytes()
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
            return raw.decode("utf-8")
    raise FileNotFoundError(digest)


def diff_keys(a, b, prefix=""):
    out = []
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        if isinstance(va, dict) and isinstance(vb, dict):
            out += diff_keys(va, vb, f"{prefix}{key}.")
        elif va != vb:
            out.append(f"{prefix}{key}")
    return out


async def run(args):
    from kriya.agents import contracts, response_protocol
    from kriya.config.config import load_config
    from kriya.core import llm as llm_module
    from kriya.core.attempt_evidence import scope as evidence_scope

    store = Path(args.store)
    records_file = store / "records.jsonl"
    before = sha(records_file)
    records = [json.loads(line) for line in records_file.open()]
    request = next(r for r in records if r["kind"] == "model.request" and r["seq"] == args.seq)
    messages = json.loads(load_blob(store, request["blobs"]["messages"]))
    recorded_wire = json.loads(load_blob(store, request["blobs"]["wire_body"]))
    verdict = {"store": store.name, "seq": args.seq, "role": request.get("role"), "recorded_model": request["payload"]["model"],
               "target_model": args.model, "messages": [m["role"] for m in messages]}
    if [m["role"] for m in messages] != ["system", "user"] or request["payload"].get("tools") or request["payload"].get("response_format") == "json_schema":
        verdict["replay_class"] = "NOT_REPLAYABLE_BY_THIS_DRIVER"
        return verdict, before, sha(records_file)
    os.chdir(args.workspace)
    cfg = load_config(args.config)
    client = llm_module.LLMClient(cfg)
    posted = []

    def capture(runtime, wire_request):
        posted.append(runtime.wire_payload(wire_request, stream=wire_request.stream_callback is not None))

    evidence_scope.record_wire_request = capture
    stream = (lambda _chunk: None) if recorded_wire.get("stream") else None
    started = time.monotonic()
    result = await client.complete_result(messages[0]["content"], messages[1]["content"], stream_callback=stream,
                                          json_mode=request["payload"].get("response_format") == "json_object",
                                          model_override=args.model,
                                          max_tokens_override=request["payload"].get("max_tokens"))
    wall = time.monotonic() - started
    meta = result.provider_metadata or {}
    verdict.update({
        "status": result.status.value, "finish_reason": result.finish_reason, "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens, "reasoning_present": result.reasoning_present,
        "timing_seconds": {"total_wall": round(wall, 2), "load": round(meta.get("load_ms", 0) / 1000, 2),
                           "prefill": round(meta.get("prompt_eval_ms", 0) / 1000, 2)},
    })
    if posted:
        wire = posted[0]
        verdict["wire_messages_identical_to_recorded"] = wire.get("messages") == recorded_wire.get("messages")
        verdict["wire_other_fields_differing_from_recorded"] = diff_keys({k: v for k, v in wire.items() if k != "messages"},
                                                                         {k: v for k, v in recorded_wire.items() if k != "messages"})
    if result.ok:
        if request.get("role") == "planner":
            parsed, error = contracts.parse_planner_structured_output(result.content)
            verdict["parse"] = {"parser": "parse_planner_structured_output", "ok": parsed is not None, "error": error}
        elif request.get("role") == "developer":
            call = request["call_seq"]
            path = next((r["payload"]["path"] for r in records if r["kind"] == "developer.parse" and r["payload"].get("call_seq_parsed") == call), None)
            if path:
                response = response_protocol.parse_structured(result.content, path)
                verdict["parse"] = {"parser": "parse_structured", "path": path, "kind": response.kind,
                                    "reason_code": getattr(response, "reason_code", None)}
            else:
                verdict["parse"] = {"parser": "parse_structured", "skipped": "no developer.parse record names the path"}
    verdict["replay_class"] = "EXECUTED"
    return verdict, before, sha(records_file)


def main():
    parser = argparse.ArgumentParser()
    for name in ("store", "seq", "config", "model", "out", "workspace"):
        parser.add_argument("--" + name, required=name != "workspace", type=int if name == "seq" else str)
    args = parser.parse_args()
    args.workspace = args.workspace or os.getcwd()
    verdict, before, after = asyncio.run(run(args))
    verdict["store_records_sha256_unchanged"] = before == after
    Path(args.out).write_text(json.dumps(verdict, indent=1))
    print(json.dumps(verdict, indent=1))
    return 0 if before == after else 3


if __name__ == "__main__":
    sys.exit(main())
