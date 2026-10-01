"""SEC-004 test fixture: one configurable, disposable, real MCP stdio server
covering every required adversarial behavior, selected via the
`ADVERSARIAL_MODE` environment variable (never a CLI arg - keeps the
command/args shape identical to a normal server, so tests exercise the same
spawn path). Modes:

  normal              - well-behaved handshake + tools/list + a `report_env`
                         and `echo` tool (see env_report_mcp_server.py for
                         the report_env shape, duplicated here so this file
                         has zero cross-imports and stays disposable).
  never_initialize    - accepts connections/reads stdin but never writes a
                         response to ANY request (startup-hang fixture).
  hang_after_init     - completes the handshake normally, then never
                         responds to any subsequent request (request-hang
                         fixture).
  ignore_sigterm      - completes the handshake, ignores SIGTERM entirely
                         (forced-kill fixture); also spawns a child which
                         spawns a grandchild, both ALSO ignoring SIGTERM and
                         writing their PIDs (plus its own) to the file named
                         by PID_REPORT_FILE as JSON, for descendant-cleanup
                         evidence.
  flood_stderr        - completes the handshake, then writes stderr lines
                         continuously (stderr-flood fixture).
  oversized_stdout    - completes the handshake, then writes ONE oversized
                         stdout line with no valid JSON shape (stdout-bound
                         fixture).
  malformed_response  - completes the handshake, then responds to the next
                         request with a non-JSON line followed by a valid
                         one (protocol-tolerance fixture).
  exit_mid_request    - completes the handshake, then exits (closes stdout)
                         immediately after receiving the next request,
                         without ever responding to it (EOF-during-request
                         fixture).
  spin_cpu            - completes the handshake, then spins the CPU in a
                         tight loop (CPU-limit fixture).
  allocate_memory     - completes the handshake, then allocates memory in a
                         tight loop (memory-limit fixture).
  delayed_response    - completes the handshake, then responds to every
                         later request only after DELAY_SECONDS (env var,
                         default 1.5) - for proving a late response arriving
                         after the CLIENT's own request timeout already
                         fired is discarded harmlessly, not resurrected.
"""
import json
import os
import signal
import subprocess
import sys
import time


def _write(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _read_line():
    return sys.stdin.readline()


def _handle_normal_request(req):
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "adversarial-server", "version": "1.0.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [
            {"name": "echo", "description": "Echoes back the input message.",
             "inputSchema": {"type": "object", "properties": {
                 "message": {"type": "string", "description": "Message to echo."}},
                 "required": ["message"]}},
            {"name": "report_env", "description": "Reports named env vars.",
             "inputSchema": {"type": "object", "properties": {
                 "names": {"type": "string", "description": "comma-separated names"}},
                 "required": ["names"]}},
        ]}}
    if method == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        tool_name = req.get("params", {}).get("name")
        if tool_name == "echo":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [
                {"type": "text", "text": f"Echo: {args.get('message', '')}"}]}}
        if tool_name == "report_env":
            names = [n.strip() for n in args.get("names", "").split(",") if n.strip()]
            report = {n: os.environ.get(n) for n in names}
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [
                {"type": "text", "text": json.dumps(report)}]}}
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    return None


def _run_normal():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        res = _handle_normal_request(req)
        if res is not None:
            _write(res)


def _run_never_initialize():
    # Read forever, respond to nothing - simulates a server that accepts
    # the connection but never completes (or even starts) the handshake.
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        time.sleep(0.05)


def _run_hang_after_init():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            continue
        else:
            # Never respond to anything past the handshake.
            while True:
                time.sleep(1)


def _run_ignore_sigterm():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pid_report_file = os.environ.get("PID_REPORT_FILE")
    if pid_report_file:
        child = subprocess.Popen([
            sys.executable, "-c",
            "import signal,subprocess,sys,os,json,time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "gc = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)']);"
            "path = os.environ['PID_REPORT_FILE'];"
            "data = json.load(open(path));"
            "data['grandchild'] = gc.pid;"
            "json.dump(data, open(path, 'w'));"
            "time.sleep(120)",
        ])
        with open(pid_report_file, "w") as f:
            json.dump({"parent": os.getpid(), "child": child.pid}, f)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        res = _handle_normal_request(req)
        if res is not None:
            _write(res)


def _run_flood_stderr():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            for i in range(200000):
                sys.stderr.write(f"flood line {i} " + ("x" * 200) + "\n")
            sys.stderr.flush()


def _run_oversized_stdout():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            sys.stdout.write("x" * 50_000_000 + "\n")
            sys.stdout.flush()


def _run_malformed_response():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            continue
        else:
            sys.stdout.write("not-json-at-all-{{{\n")
            sys.stdout.flush()
            res = _handle_normal_request(req)
            if res is not None:
                _write(res)


def _run_delayed_response():
    # Delays only the FIRST post-handshake request by DELAY_SECONDS (env
    # var) - for proving a late response (arriving after the CLIENT's own
    # request timeout already fired and discarded the pending Future) is
    # handled harmlessly: no resurrection, no crash, no corrupted
    # correlation for the NEXT, unrelated request, which responds
    # immediately like a normal server.
    delay = float(os.environ.get("DELAY_SECONDS", "1.5"))
    delayed_once = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            continue
        else:
            if not delayed_once:
                delayed_once = True
                time.sleep(delay)
            res = _handle_normal_request(req)
            if res is not None:
                _write(res)


def _run_exit_mid_request():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            continue
        else:
            sys.exit(0)


def _run_spin_cpu():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            x = 0
            while True:
                x += 1


def _run_allocate_memory():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if method == "initialize":
            _write(_handle_normal_request(req))
        elif method == "notifications/initialized":
            chunks = []
            while True:
                chunks.append(bytearray(10_000_000))


_MODES = {
    "normal": _run_normal,
    "never_initialize": _run_never_initialize,
    "hang_after_init": _run_hang_after_init,
    "ignore_sigterm": _run_ignore_sigterm,
    "flood_stderr": _run_flood_stderr,
    "oversized_stdout": _run_oversized_stdout,
    "malformed_response": _run_malformed_response,
    "exit_mid_request": _run_exit_mid_request,
    "delayed_response": _run_delayed_response,
    "spin_cpu": _run_spin_cpu,
    "allocate_memory": _run_allocate_memory,
}


if __name__ == "__main__":
    mode = os.environ.get("ADVERSARIAL_MODE", "normal")
    _MODES.get(mode, _run_normal)()
