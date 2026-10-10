"""``kriya kup-stdio``: ONE long-lived local process that answers the same allowlisted KUP read operations as the one-shot commands
(GUI M5, resolving GUI-KRIYA-PROCESS-STARTUP-LATENCY-001). A transport, not a service: no listener, no socket, no network, no workflow, no model.

Every operation is dispatched to ``kriya.kup.cli_ops.run_kup_operation`` - the function ``kriya traces --json`` and ``kriya evidence ...``
call - so the two transports cannot differ in behaviour. Nothing is cached between requests: the configuration (and with it SECURITY_AUTHORITY,
``content_visibility`` included) is loaded again for every request, and every evidence operation recomputes its own verification.

Protocol: newline-delimited JSON, UTF-8, one request line in, exactly one response line out, in order.

  request   {"id": "<1..128 of A-Za-z0-9._:->", "schema_version": 1, "capability_set": "1.1" (optional),
             "operation": "<allowlisted KUP operation>", "arguments": {<that operation's typed arguments>}}
  response  {"id": "<same>", "ok": true,  "envelope": <the KUP envelope, byte-for-byte what the one-shot command prints>}
            {"id": "<same or null>", "ok": false, "error": {"code": "<closed KUP vocabulary or HOST_ERROR>", "message": "..."}}   (a refusal of the
            REQUEST: malformed, over-long, unknown operation or argument, unsupported version, or an internal fault; never a Kriya answer)

stdout carries protocol lines only: the real stdout descriptor is moved aside and fd 1 is pointed at stderr, so a library that prints cannot
corrupt the stream. A malformed or refused request leaves the process healthy; EOF on stdin ends it cleanly. The first line is
``{"event": "ready", "protocol": ..., "pid": ...}``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from typing import Any, BinaryIO, Dict, Optional

from kriya.kup import policy

PROTOCOL = "kriya-kup-stdio/1"
MAX_REQUEST_BYTES = 64 * 1024
HOST_ERROR = "HOST_ERROR"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
REQUEST_KEYS = frozenset({"id", "schema_version", "capability_set", "operation", "arguments"})

# The typed arguments each operation accepts - the same names the one-shot grammar maps its flags to (kriya/cli.py::_traces_kup, _evidence_kup,
# kriya/kup/cli_ops.py). An argument outside this table refuses the request; the operation's own handler then validates the values.
_EVIDENCE_RECORDS = frozenset({"run_id", "unit", "phase", "role", "attempt", "from_seq", "kind", "limit", "cursor"})
ARGUMENTS: Dict[str, frozenset] = {
    "capabilities": frozenset(),
    "snapshot.acquire": frozenset({"workspace"}),
    "snapshot.list": frozenset({"verify"}),
    "snapshot.prune": frozenset({"keep"}),
    "snapshot.verify": frozenset({"snapshot_id"}),
    "history.list": frozenset({"snapshot_id", "limit", "cursor", "workspace"}),
    "history.detail": frozenset({"snapshot_id", "run_id"}),
    "history.prompt": frozenset({"snapshot_id", "run_id"}),
    "workspace.status": frozenset({"workspace"}),
    "workspace.describe": frozenset({"workspace"}),
    "execution.preflight": frozenset({"workspace"}),
    "launch.status": frozenset({"workspace", "launch_id"}),
    "evidence.manifest": frozenset({"run_id", "snapshot_id", "from_trace"}),
    "evidence.verify": frozenset({"run_id"}),
    "evidence.records": _EVIDENCE_RECORDS,
    "evidence.change_content": frozenset({"run_id", "seq", "part"}),
    "evidence.call_content": frozenset({"run_id", "seq", "part"}),
}
assert set(ARGUMENTS) == set(policy.OPERATIONS), "every KUP operation has an argument table, and nothing else is dispatchable"


def _refusal(request_id: Optional[str], code: str, message: str) -> Dict[str, Any]:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


def _scalar_or_strings(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool)) or (isinstance(value, list) and all(isinstance(v, str) for v in value))


def parse_request(line: bytes) -> Dict[str, Any]:
    """The validated request, or a ready-made refusal under the key ``refusal``."""
    try:
        request = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"refusal": _refusal(None, policy.INVALID_REQUEST, "the request line is not UTF-8 JSON")}
    if not isinstance(request, dict):
        return {"refusal": _refusal(None, policy.INVALID_REQUEST, "the request must be a JSON object")}
    request_id = request.get("id")
    if not isinstance(request_id, str) or not ID_RE.match(request_id):
        return {"refusal": _refusal(None, policy.INVALID_REQUEST, "id must be a string of 1..128 characters from A-Za-z0-9._:-")}
    extra = sorted(set(request) - REQUEST_KEYS)
    if extra:
        return {"refusal": _refusal(request_id, policy.INVALID_REQUEST, f"unknown request field(s): {', '.join(extra)}")}
    if request.get("schema_version") != policy.KUP_SCHEMA_VERSION or isinstance(request.get("schema_version"), bool):
        return {"refusal": _refusal(request_id, policy.UNSUPPORTED_SCHEMA_VERSION,
                                    f"KUP schema version {request.get('schema_version')!r} is not supported (supported: {policy.KUP_SCHEMA_VERSION})")}
    capability_set = request.get("capability_set", policy.CAPABILITY_SET)
    if capability_set != policy.CAPABILITY_SET:
        return {"refusal": _refusal(request_id, policy.UNSUPPORTED_SCHEMA_VERSION,
                                    f"capability set {capability_set!r} is not served (served: {policy.CAPABILITY_SET})")}
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in ARGUMENTS:
        return {"refusal": _refusal(request_id, policy.INVALID_REQUEST, f"operation {operation!r} is not an allowlisted KUP operation")}
    arguments = request.get("arguments", {})
    if not isinstance(arguments, dict):
        return {"refusal": _refusal(request_id, policy.INVALID_REQUEST, "arguments must be an object")}
    unknown = sorted(set(arguments) - ARGUMENTS[operation])
    if unknown:
        return {"refusal": _refusal(request_id, policy.INVALID_REQUEST, f"{operation} does not accept: {', '.join(unknown)}")}
    if not all(_scalar_or_strings(v) for v in arguments.values()):
        return {"refusal": _refusal(request_id, policy.INVALID_REQUEST, "argument values must be strings, numbers, booleans, null or lists of strings")}
    return {"id": request_id, "operation": operation, "arguments": arguments}


def _config_failure_envelope(operation: str, request_id: str, error: Exception) -> Dict[str, Any]:
    """D-7, exactly as ``_traces_kup_config_error``: a SEC-009 refusal is CONFIG_AUTHORITY_REFUSED, anything else CONFIG_LOAD_FAILED."""
    from kriya.config.authority import ConfigAuthorityError
    from kriya.kup.inspect import error_envelope

    code = policy.CONFIG_AUTHORITY_REFUSED if isinstance(error, ConfigAuthorityError) else policy.CONFIG_LOAD_FAILED
    return error_envelope(operation, request_id, code, str(error))


def answer(request: Dict[str, Any], config_path: Optional[str], trust_file: Optional[str]) -> str:
    """The response line (no newline) for one validated request."""
    from kriya.config import load_config
    from kriya.kup.cli_ops import run_kup_operation
    from kriya.kup.inspect import encode_response

    operation, request_id = request["operation"], request["id"]
    cfg = None
    if operation != "workspace.status":  # the one operation that, like `kriya runs status`, loads no configuration
        try:
            cfg = load_config(config_path, trust_file=trust_file)  # per request: SECURITY_AUTHORITY is never read once and kept
        except Exception as error:  # noqa: BLE001 - any load failure is the typed D-7 envelope, as the one-shot command does
            return '{"id":%s,"ok":true,"envelope":%s}' % (json.dumps(request_id), encode_response(_config_failure_envelope(operation, request_id, error)))
    envelope_text = encode_response(run_kup_operation(cfg, {"operation": operation, **request["arguments"]}, request_id))
    return '{"id":%s,"ok":true,"envelope":%s}' % (json.dumps(request_id), envelope_text)


def _read_line(stream: BinaryIO) -> Optional[bytes]:
    """One request line without its newline; None at EOF. An over-long line is drained to its newline (the stream stays in step) and returned as b''+marker."""
    line = stream.readline(MAX_REQUEST_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_REQUEST_BYTES and not line.endswith(b"\n"):
        while True:
            rest = stream.readline(MAX_REQUEST_BYTES)
            if not rest or rest.endswith(b"\n"):
                break
        return b"\x00too-long"
    return line.rstrip(b"\r\n")


def serve(stdin: BinaryIO, stdout: BinaryIO, config_path: Optional[str] = None, trust_file: Optional[str] = None) -> int:
    def emit(text: str) -> None:
        stdout.write(text.encode("utf-8") + b"\n")
        stdout.flush()

    emit(json.dumps({"event": "ready", "protocol": PROTOCOL, "pid": os.getpid(), "schema_version": policy.KUP_SCHEMA_VERSION, "capability_set": policy.CAPABILITY_SET}))
    while True:
        line = _read_line(stdin)
        if line is None:
            return 0
        if line == b"\x00too-long":
            emit(json.dumps(_refusal(None, policy.INVALID_REQUEST, f"the request line exceeds {MAX_REQUEST_BYTES} bytes")))
            continue
        if not line.strip():
            continue
        parsed = parse_request(line)
        if "refusal" in parsed:
            emit(json.dumps(parsed["refusal"]))
            continue
        try:
            emit(answer(parsed, config_path, trust_file))
        except Exception as error:  # noqa: BLE001 - an internal fault answers THIS request with a typed refusal and keeps the process serving
            traceback.print_exc(file=sys.stderr)
            emit(json.dumps(_refusal(parsed["id"], HOST_ERROR, f"internal fault in {parsed['operation']}: {type(error).__name__}: {error}")))


def run(config_path: Optional[str], trust_file: Optional[str]) -> int:
    """Entry point for the CLI command: protocol lines go to the ORIGINAL stdout, everything else that prints goes to stderr."""
    sys.dont_write_bytecode = True
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return serve(sys.stdin.buffer, out, config_path, trust_file)
