#!/usr/bin/env python3
"""A localhost OpenAI-compatible model server for the controlled-repair scenario. CONTROLLED / SCRIPTED: it is not a model.

  record  --upstream URL  : forward every request unchanged to a real model server and log each chat exchange (used once, to capture the formats Kriya's roles expect);
                            with --override ROLE_PREFIX:INDEX=FILE|LABEL the INDEXth answer of a role (matched by role prefix) is the file's content instead, logged as
                            overridden with its label, so the recording holds the real answers of every other role on the path the overridden answers cause
  replay  --script FILE   : answer chat requests from a fixed script, by role and by the order of that role's calls; never contacts a model

Standard library only. It listens on 127.0.0.1 and nothing else. In replay mode it makes no outbound connection of any kind.
Kriya reaches it through its ordinary configuration (`llm.base_url`), which needs the operator's one-time `kriya authority approve` like any non-default endpoint.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE_RE = re.compile(r"You are the Kriya ([A-Za-z ]+?) Agent", re.I)
MODEL_NAME = "scripted-stub"


def role_of(messages) -> str:
    for message in messages:
        if message.get("role") == "system":
            content = message.get("content")
            text = content if isinstance(content, str) else json.dumps(content)
            found = ROLE_RE.search(text)
            if found:
                return found.group(1).strip().lower().replace(" ", "_")
            return "system:" + re.sub(r"\s+", " ", text)[:60]
    return "no_system"


def prompt_tokens_of(request: dict) -> int:
    """An honest-looking prompt size (about one token per four bytes). Kriya refuses a provider that reports far fewer prompt tokens than the prompt can hold."""
    return max(1, len(json.dumps(request.get("messages") or [])) // 4)


def sse(content: str, finish: str = "stop", model: str = MODEL_NAME, prompt_tokens: int = 1) -> bytes:
    """One assistant message as a server-sent-event stream: the content in a few deltas, a finish chunk, a usage chunk, [DONE]."""
    chunks = [content[i:i + 400] for i in range(0, len(content), 400)] or [""]
    out = []
    for n, piece in enumerate(chunks):
        delta = {"role": "assistant", "content": piece} if n == 0 else {"content": piece}
        out.append({"id": "stub-1", "object": "chat.completion.chunk", "created": 0, "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
    out.append({"id": "stub-1", "object": "chat.completion.chunk", "created": 0, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    out.append({"id": "stub-1", "object": "chat.completion.chunk", "created": 0, "model": model, "choices": [],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": max(1, len(content) // 4), "total_tokens": prompt_tokens + max(1, len(content) // 4)}})
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in out) + "data: [DONE]\n\n").encode()


def completion(content: str, finish: str = "stop", model: str = MODEL_NAME, prompt_tokens: int = 1) -> dict:
    return {"id": "stub-1", "object": "chat.completion", "created": 0, "model": model, "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": max(1, len(content) // 4), "total_tokens": prompt_tokens + max(1, len(content) // 4)}}


def accumulate(raw: bytes) -> dict:
    """The assistant text, tool calls and finish reason of a (possibly streamed) chat response."""
    text = raw.decode("utf-8", "replace")
    content, tool_calls, finish = [], {}, None
    if text.lstrip().startswith("{"):
        body = json.loads(text)
        choice = (body.get("choices") or [{}])[0]
        return {"content": (choice.get("message") or {}).get("content") or "", "tool_calls": (choice.get("message") or {}).get("tool_calls"), "finish_reason": choice.get("finish_reason")}
    for line in text.splitlines():
        if not line.startswith("data:") or line.strip() == "data: [DONE]":
            continue
        try:
            chunk = json.loads(line[5:])
        except ValueError:
            continue
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
            for call in delta.get("tool_calls") or []:
                slot = tool_calls.setdefault(call.get("index", 0), {"function": {"name": "", "arguments": ""}})
                slot["id"] = call.get("id", slot.get("id"))
                slot["function"]["name"] += (call.get("function") or {}).get("name") or ""
                slot["function"]["arguments"] += (call.get("function") or {}).get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    return {"content": "".join(content), "tool_calls": [tool_calls[k] for k in sorted(tool_calls)] or None, "finish_reason": finish}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, mode, upstream, script, log_path, override=None):
        super().__init__(address, Handler)
        self.mode, self.upstream, self.script, self.log_path, self.overrides = mode, upstream, script, log_path, override or []
        self.lock = threading.Lock()
        self.counts: dict = {}
        self.seq = 0

    def log(self, record: dict) -> None:
        with self.lock:
            self.seq += 1
            record = {"seq": self.seq, "at": round(time.time(), 3), **record}
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    def next_for(self, role: str):
        with self.lock:
            index = self.counts.get(role, 0)
            self.counts[role] = index + 1
        steps = [s for s in self.script["steps"] if s["role"] == role]
        return index, (steps[index] if index < len(steps) else None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _forward(self, body: bytes) -> bytes:
        request = urllib.request.Request(self.server.upstream.rstrip("/") + self.path.replace("/v1/v1", "/v1"), data=body or None, method=self.command,
                                         headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection")})
        try:
            with urllib.request.urlopen(request, timeout=1800) as response:
                data = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return data
        except urllib.error.HTTPError as error:
            data = error.read()
            self.send_response(error.code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return data

    def do_GET(self):
        path = self.path.split("?")[0]
        if self.server.mode == "record":
            self._forward(b"")
        elif path.endswith("/models"):
            self._json({"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "demo-stub"}]})
        elif path.endswith("/api/version"):
            self._json({"version": "stub"})
        elif path.endswith("/api/ps"):
            self._json({"models": []})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = self._read()
        path = self.path.split("?")[0]
        request = json.loads(body or b"{}") if body[:1] == b"{" else {}
        if path.endswith("/embeddings"):
            if self.server.mode == "record":
                self._forward(body)
                return
            inputs = request.get("input")
            inputs = [inputs] if isinstance(inputs, str) else (inputs or [])
            self._json({"object": "list", "model": "stub-embedding", "data": [{"object": "embedding", "index": i, "embedding": [0.01] * 8} for i, _ in enumerate(inputs)]})
            return
        if not path.endswith("/chat/completions"):
            if self.server.mode == "record":
                self._forward(body)
            else:
                self._json({"error": "not found"}, 404)
            return
        role = role_of(request.get("messages") or [])
        if self.server.mode == "record":
            with self.server.lock:
                position = self.server.counts.get(role, 0)
                self.server.counts[role] = position + 1
            hit = next((o for o in self.server.overrides if role.startswith(o["role_prefix"]) and o["index"] == position), None)
            if hit is not None:
                content = hit["content"]
                payload = sse(content, prompt_tokens=prompt_tokens_of(request)) if request.get("stream") else json.dumps(completion(content, prompt_tokens=prompt_tokens_of(request))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if request.get("stream") else "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                self.server.log({"kind": "chat", "role": role, "overridden": True, "override_label": hit["label"], "request": request,
                                 "response": {"content": content, "tool_calls": None, "finish_reason": "stop"}})
                return
            data = self._forward(body)
            self.server.log({"kind": "chat", "role": role, "request": request, "response": accumulate(data)})
            return
        index, step = self.server.next_for(role)
        if step is None:
            self.server.log({"kind": "chat", "role": role, "index": index, "unscripted": True})
            self._json({"error": {"message": f"the script has no step for role {role!r} call #{index}", "type": "stub_script_exhausted"}}, 500)
            return
        self.server.log({"kind": "chat", "role": role, "index": index, "step": step.get("label")})
        content = step["content"]
        if request.get("stream"):
            payload = sse(content, prompt_tokens=prompt_tokens_of(request))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self._json(completion(content, prompt_tokens=prompt_tokens_of(request)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("record", "replay"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", required=True, help="JSON-lines log of every chat exchange")
    parser.add_argument("--upstream", help="record mode: the real model server's base URL (no /v1 suffix), e.g. http://localhost:11434")
    parser.add_argument("--script", help="replay mode: the script file")
    parser.add_argument("--override", action="append", default=[], metavar="ROLE_PREFIX:INDEX=FILE|LABEL",
                        help="record mode: serve FILE's content as the INDEXth answer of the role whose key starts with ROLE_PREFIX (repeatable)")
    args = parser.parse_args()
    script = None
    if args.mode == "record":
        if not args.upstream:
            parser.error("record mode needs --upstream")
    else:
        if not args.script:
            parser.error("replay mode needs --script")
        with open(args.script, encoding="utf-8") as handle:
            script = json.load(handle)
    overrides = []
    for spec in args.override:
        left, _, label = spec.partition("|")
        target, _, path = left.partition("=")
        prefix, _, index = target.rpartition(":")
        with open(path, encoding="utf-8") as handle:
            overrides.append({"role_prefix": prefix, "index": int(index), "content": handle.read(), "label": label or "SCRIPTED"})
    server = Server(("127.0.0.1", args.port), args.mode, args.upstream, script, args.log, overrides)
    print(f"stub_model_server {args.mode} on 127.0.0.1:{args.port} (CONTROLLED / SCRIPTED: not a model)", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
